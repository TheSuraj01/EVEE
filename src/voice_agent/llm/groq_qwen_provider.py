"""Groq-hosted Qwen intent provider.

Two things make this safe rather than merely functional:

1. The `enum` in the JSON schema is built from the live `CommandRegistry`, so the model
   cannot be offered an intent the host does not implement, and the two can never drift.
2. The return value is an `IntentProposal` — a *suggestion*. It is worthless until the
   policy engine approves it.

The `groq` package is imported lazily so that the rest of the application, and the whole
test suite, run without it installed and without network access.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any, Protocol

from voice_agent.core.constants import MAX_TRANSCRIPT_CHARS
from voice_agent.core.exceptions import LLMUnavailableError
from voice_agent.core.models import IntentProposal, IntentSource
from voice_agent.engine.command_registry import CommandRegistry
from voice_agent.llm.prompts import render_system_prompt

#: Worth one retry: request timeout, conflict (plus every 5xx). Not 429: an instant retry
#: would be rate-limited too; it counts as a failure for the circuit breaker instead.
_RETRYABLE_STATUS = frozenset({408, 409})


class ChatTransport(Protocol):
    """The seam that keeps HTTP out of the tests."""

    def complete(self, **kwargs: Any) -> str:
        """Return the assistant message content as a JSON string."""
        ...


class GroqTransport:
    """Thin wrapper over the official SDK. Created only when the app really calls Groq.

    Classifies failures: a client error (bad key, bad request, unknown model, 429) becomes a
    permanent LLMUnavailableError; timeouts, connection errors, 408/409 and 5xx are
    re-raised as-is so the provider may retry them.
    """

    def __init__(
        self, api_key: str | None = None, timeout_s: float = 4.0, client: Any = None
    ) -> None:
        if client is None:
            key = api_key or os.environ.get("GROQ_API_KEY")
            if not key:
                msg = "no Groq API key: add one in Settings, or set GROQ_API_KEY"
                raise LLMUnavailableError(msg)
            try:
                from groq import Groq
            except ImportError as exc:  # pragma: no cover - environment dependent
                msg = "the 'groq' package is not installed"
                raise LLMUnavailableError(msg) from exc
            # max_retries=0: the provider owns retries. The SDK's default 2 would multiply
            # them — 1 configured retry became up to 6 calls.
            client = Groq(api_key=key, timeout=timeout_s, max_retries=0)
        self._client = client

    def complete(self, **kwargs: Any) -> str:
        try:
            response = self._client.chat.completions.create(**kwargs)
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            if status is None or status in _RETRYABLE_STATUS or status >= 500:
                raise
            # Type and status only: an error body could echo request content.
            msg = f"Groq refused the request: {type(exc).__name__} (HTTP {status})"
            raise LLMUnavailableError(msg) from None
        content = response.choices[0].message.content
        if not content:
            msg = "the model returned an empty response"
            raise LLMUnavailableError(msg)
        return str(content)


class KeyedTransport:
    """A GroqTransport for whatever key is configured at the time of each call: a key saved
    in Settings works at once, a replaced one is used instead, and a removed one stops the
    calls. With no key every call fails as "no key" (permanent: the provider does not retry
    it, and the router reports it)."""

    def __init__(
        self,
        key: Callable[[], str | None],
        timeout_s: float = 4.0,
        make: Callable[..., ChatTransport] = GroqTransport,
    ) -> None:
        self._key = key
        self._timeout_s = timeout_s
        self._make = make
        self._transport: ChatTransport | None = None
        self._made_with: str | None = None

    def complete(self, **kwargs: Any) -> str:
        key = self._key()
        if self._transport is None or key != self._made_with:
            self._transport = None  # a removed key must not leave the old client in use
            transport = self._make(api_key=key, timeout_s=self._timeout_s)
            self._transport, self._made_with = transport, key
        return self._transport.complete(**kwargs)


class GroqQwenIntentProvider:
    """Implements `core.interfaces.IntentProvider`."""

    def __init__(
        self,
        registry: CommandRegistry,
        transport: ChatTransport,
        model: str,
        max_retries: int = 1,
    ) -> None:
        self._registry = registry
        self._transport = transport
        self._model = model
        self._max_retries = max_retries

    def propose(self, transcript: str, context: dict[str, str]) -> IntentProposal | None:
        payload = transcript.strip()[:MAX_TRANSCRIPT_CHARS]
        if not payload:
            return None

        last_error: Exception | None = None
        for _ in range(self._max_retries + 1):
            try:
                raw = self._transport.complete(
                    model=self._model,
                    max_tokens=192,  # one small JSON object; room for a long web address
                    temperature=0.0,
                    messages=[
                        {
                            "role": "system",
                            "content": render_system_prompt(self._registry, context),
                        },
                        {"role": "user", "content": payload},
                    ],
                    response_format=self.response_format(),
                )
            except LLMUnavailableError:
                raise  # permanent (bad key, bad request): a retry would fail the same way
            except Exception as exc:  # transient: timeout, connection, 429, 5xx
                last_error = exc
                continue
            # Not retried on a bad reply: at temperature 0 the model would repeat it.
            return self._parse(raw, payload)

        msg = f"Groq intent resolution failed: {type(last_error).__name__}"
        raise LLMUnavailableError(msg) from last_error

    def response_format(self) -> dict[str, Any]:
        """Strict JSON schema whose intent enum comes from the registry at call time."""
        intents = list(self._registry.intents())
        return {
            "type": "json_schema",
            "json_schema": {
                "name": "intent_proposal",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "intent": {"type": "string", "enum": intents},
                        "argument_seconds": {"type": ["integer", "null"]},
                        "argument_text": {"type": ["string", "null"]},
                        "requires_confirmation": {"type": "boolean"},
                        "confidence": {"type": "number"},
                    },
                    "required": [
                        "intent",
                        "argument_seconds",
                        "argument_text",
                        "requires_confirmation",
                        "confidence",
                    ],
                    "additionalProperties": False,
                },
            },
        }

    def _parse(self, raw: str, transcript: str) -> IntentProposal:
        try:
            proposal = IntentProposal.model_validate_json(raw)
        except Exception as exc:
            msg = "the model returned a payload that did not validate"
            raise LLMUnavailableError(msg) from exc

        # Defence in depth: strict schemas are a contract, not a guarantee we rely on.
        if proposal.intent not in self._registry:
            # Model output: quoted and capped so it cannot forge lines in a plain-text log.
            msg = f"model proposed an unregistered intent: {proposal.intent[:64]!r}"
            raise LLMUnavailableError(msg)

        return proposal.model_copy(update={"source": IntentSource.LLM, "raw": transcript})
