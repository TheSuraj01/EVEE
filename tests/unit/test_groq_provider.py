"""The Groq provider is exercised entirely through a fake transport: no network, ever."""

from __future__ import annotations

import json

import pytest
from tests.conftest import FakeTransport

from voice_agent.core.exceptions import LLMUnavailableError
from voice_agent.core.models import IntentSource
from voice_agent.engine.command_registry import CommandRegistry
from voice_agent.llm.groq_qwen_provider import (
    GroqQwenIntentProvider,
    GroqTransport,
    KeyedTransport,
)

VALID = (
    '{"intent":"browser.next_tab","argument_seconds":null,'
    '"requires_confirmation":false,"confidence":0.9}'
)


def _provider(registry: CommandRegistry, transport: FakeTransport) -> GroqQwenIntentProvider:
    return GroqQwenIntentProvider(registry, transport, model="test-model", max_retries=1)


def test_schema_enum_is_derived_from_the_registry(registry: CommandRegistry) -> None:
    fmt = _provider(registry, FakeTransport()).response_format()
    enum = fmt["json_schema"]["schema"]["properties"]["intent"]["enum"]
    assert set(enum) == set(registry.intents())
    assert fmt["json_schema"]["strict"] is True


def test_valid_payload_becomes_an_llm_sourced_proposal(registry: CommandRegistry) -> None:
    result = _provider(registry, FakeTransport([VALID])).propose("go to the tab after this", {})
    assert result is not None
    assert result.intent == "browser.next_tab"
    assert result.source is IntentSource.LLM


def test_unregistered_intent_is_rejected_even_if_well_formed(registry: CommandRegistry) -> None:
    rogue = (
        '{"intent":"system.run_shell","argument_seconds":null,'
        '"requires_confirmation":false,"confidence":1.0}'
    )
    with pytest.raises(LLMUnavailableError):
        _provider(registry, FakeTransport([rogue, rogue])).propose("do something", {})


def test_malformed_json_raises_rather_than_guessing(registry: CommandRegistry) -> None:
    with pytest.raises(LLMUnavailableError):
        _provider(registry, FakeTransport(["not json", "not json"])).propose("hello", {})


def test_transport_failure_is_retried_then_surfaced(registry: CommandRegistry) -> None:
    transport = FakeTransport(error=TimeoutError("timeout"))
    with pytest.raises(LLMUnavailableError):
        _provider(registry, transport).propose("hello", {})
    assert len(transport.calls) == 2


def test_transcript_is_truncated_before_leaving_the_machine(registry: CommandRegistry) -> None:
    transport = FakeTransport([VALID])
    _provider(registry, transport).propose("x" * 5000, {})
    sent = transport.calls[0]["messages"][-1]["content"]
    assert len(sent) == 500


class StatusError(Exception):
    """Shaped like the Groq SDK's APIStatusError: carries an HTTP status code."""

    def __init__(self, status_code: int, body: str = "") -> None:
        super().__init__(body)
        self.status_code = status_code


class FakeClient:
    """Stands in for groq.Groq: chat.completions.create returns or raises."""

    def __init__(self, content: str | None = VALID, error: Exception | None = None) -> None:
        self.chat = self
        self.completions = self
        self._content, self._error = content, error

    def create(self, **kwargs: object) -> object:
        if self._error is not None:
            raise self._error
        message = type("M", (), {"content": self._content})
        return type("R", (), {"choices": [type("C", (), {"message": message})]})


def test_permanent_failure_is_not_retried(registry: CommandRegistry) -> None:
    transport = FakeTransport(error=LLMUnavailableError("Groq refused the request (HTTP 401)"))
    with pytest.raises(LLMUnavailableError):
        _provider(registry, transport).propose("hello", {})
    assert len(transport.calls) == 1


def test_malformed_reply_is_not_retried_at_temperature_zero(registry: CommandRegistry) -> None:
    transport = FakeTransport(["not json", VALID])
    with pytest.raises(LLMUnavailableError):
        _provider(registry, transport).propose("hello", {})
    assert len(transport.calls) == 1


def test_transport_returns_the_message_content() -> None:
    assert GroqTransport(client=FakeClient()).complete(model="m") == VALID


@pytest.mark.parametrize("status", [400, 401, 403, 404, 413, 422, 429])
def test_client_errors_become_permanent_and_hide_the_body(status: int) -> None:
    transport = GroqTransport(client=FakeClient(error=StatusError(status, "body gsk_secret")))
    with pytest.raises(LLMUnavailableError, match=str(status)) as info:
        transport.complete(model="m")
    assert "gsk_secret" not in str(info.value)


@pytest.mark.parametrize("error", [StatusError(408), StatusError(503), TimeoutError("read")])
def test_transient_errors_are_left_for_the_provider_to_retry(error: Exception) -> None:
    with pytest.raises(type(error)):
        GroqTransport(client=FakeClient(error=error)).complete(model="m")


def test_empty_reply_is_a_permanent_failure() -> None:
    with pytest.raises(LLMUnavailableError, match="empty"):
        GroqTransport(client=FakeClient(content="")).complete(model="m")


def test_missing_api_key_is_a_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with pytest.raises(LLMUnavailableError, match="GROQ_API_KEY"):
        GroqTransport()


def test_sdk_retries_are_off_so_ours_do_not_multiply(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("groq")
    monkeypatch.setenv("GROQ_API_KEY", "gsk_" + "notarealkey000000")
    transport = GroqTransport(timeout_s=4.0)
    assert transport._client.max_retries == 0
    assert transport._client.timeout == 4.0


def test_unregistered_intent_is_quoted_and_capped_in_the_error(registry: CommandRegistry) -> None:
    """The intent is model output: it must not be able to forge lines in a plain-text log."""
    forged = "x\n2026-10-20 INFO forged line " + "y" * 200
    reply = (
        '{"intent":' + json.dumps(forged) + ',"argument_seconds":null,'
        '"requires_confirmation":false,"confidence":1.0}'
    )
    with pytest.raises(LLMUnavailableError) as info:
        _provider(registry, FakeTransport([reply])).propose("hello", {})
    assert "\n" not in str(info.value)
    assert len(str(info.value)) < 140


def test_the_key_in_settings_is_the_one_used_from_the_next_call() -> None:
    """Saved after startup: used at once. Replaced: the new one. Removed: no more calls."""
    current: list[str | None] = [None]
    made: list[str | None] = []

    def make(api_key: str | None, timeout_s: float) -> FakeTransport:
        made.append(api_key)
        if not api_key:
            raise LLMUnavailableError("no Groq API key")
        return FakeTransport()

    transport = KeyedTransport(lambda: current[0], timeout_s=2.0, make=make)

    with pytest.raises(LLMUnavailableError, match="no Groq API key"):
        transport.complete(model="m")
    current[0] = "gsk_first"
    transport.complete(model="m")
    transport.complete(model="m")  # same key: the same client
    current[0] = "gsk_second"
    transport.complete(model="m")
    current[0] = None  # removed in Settings
    with pytest.raises(LLMUnavailableError):
        transport.complete(model="m")
    with pytest.raises(LLMUnavailableError):
        transport.complete(model="m")  # and it stays removed: the old client is gone
    assert made == [None, "gsk_first", "gsk_second", None, None]
