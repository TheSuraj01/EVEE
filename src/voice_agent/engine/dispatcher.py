"""The dispatcher: the one path from a transcript to an executed action.

Order is fixed and deliberate:

    transcript -> router (local first, cloud only on miss)
               -> policy engine (allow / confirm / deny)
               -> confirmation broker, if required
               -> deduplicator
               -> action handler

No other module may call an `ActionHandler`. If you find yourself wanting to, you want a
new handler registration instead.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from voice_agent.core.exceptions import ActionExecutionError
from voice_agent.core.interfaces import ActionHandler, Clock, ConfirmationPrompt, SystemClock
from voice_agent.core.models import (
    CommandSpec,
    Decision,
    ExecutionResult,
    ExecutionStatus,
    IntentProposal,
    ResolvedCommand,
)
from voice_agent.engine.command_registry import CommandRegistry
from voice_agent.engine.deduplicator import Deduplicator
from voice_agent.engine.execution_context import ExecutionContext
from voice_agent.engine.result_handler import to_result
from voice_agent.safety.policy_engine import PolicyEngine

AuditSink = Callable[[IntentProposal, ExecutionResult], None]

log = logging.getLogger(__name__)


class Dispatcher:
    def __init__(
        self,
        registry: CommandRegistry,
        policy: PolicyEngine,
        deduplicator: Deduplicator | None = None,
        confirmation: ConfirmationPrompt | None = None,
        clock: Clock | None = None,
        audit: AuditSink | None = None,
        confirmation_timeout_s: float = 10.0,
        context: ExecutionContext | None = None,
    ) -> None:
        self._registry = registry
        self._policy = policy
        self._dedupe = deduplicator or Deduplicator()
        self._confirmation = confirmation
        self._clock: Clock = clock or SystemClock()
        self._audit = audit
        self._confirmation_timeout_s = confirmation_timeout_s
        #: Opened around every executor call: plugin capabilities only work inside it.
        self._context = context or ExecutionContext()
        self._handlers: dict[str, ActionHandler] = {}

    @property
    def wired_intents(self) -> frozenset[str]:
        """Intents that have an executor. A contract test holds this to the registry."""
        return frozenset(self._handlers)

    def register_handler(self, intent: str, handler: ActionHandler) -> None:
        """Bind an executor to an intent that already exists in the registry."""
        self._registry.require(intent)
        self._handlers[intent] = handler

    def dispatch(
        self, proposal: IntentProposal, still_wanted: Callable[[], bool] | None = None
    ) -> ExecutionResult:
        """`still_wanted` is the kill switch (invariant 8): it is checked before any prompt and
        again right before execution, because resolution and confirmation can take seconds."""
        started = self._clock.monotonic_ms()
        try:
            result = self._dispatch_inner(proposal, still_wanted or (lambda: True))
        except Exception as exc:
            # The UI boundary: whatever failed (policy, prompt, dedupe), the caller gets a
            # result, never an exception.
            result = to_result(exc, proposal.intent)
        result = result.model_copy(update={"latency_ms": self._clock.monotonic_ms() - started})
        if self._audit is not None:
            try:
                self._audit(proposal, result)
            except Exception:
                # The action has already happened; a broken sink must not undo the result.
                log.exception("audit sink failed for %s", proposal.intent)
        return result

    def _ask(self, spec: CommandSpec) -> bool:
        """Silence is never consent: with no prompt available, a confirmation fails."""
        if self._confirmation is None:
            return False
        return self._confirmation.confirm(spec, timeout_s=self._confirmation_timeout_s)

    def _dispatch_inner(
        self, proposal: IntentProposal, still_wanted: Callable[[], bool]
    ) -> ExecutionResult:
        if not still_wanted():
            return _cancelled(proposal.intent)
        verdict = self._policy.evaluate(proposal)

        if verdict.decision is Decision.DENY:
            return ExecutionResult(
                status=ExecutionStatus.DENIED,
                intent=proposal.intent,
                message=_deny_message(verdict.reason),
                detail={"reason": verdict.reason, "source": proposal.source.value},
            )

        spec = verdict.spec
        assert spec is not None  # guaranteed by PolicyEngine for ALLOW/CONFIRM

        needs_confirm = verdict.decision is Decision.CONFIRM
        # The question names what would open: "Open a website in a new browser tab: https://...".
        question = (
            spec
            if verdict.text is None
            else spec.model_copy(update={"description": f"{spec.description}: {verdict.text}"})
        )
        if needs_confirm and not self._ask(question):
            return ExecutionResult(
                status=ExecutionStatus.DENIED,
                intent=spec.intent,
                message="Cancelled — not confirmed.",
                detail={"reason": "confirmation_declined"},
            )
        if not still_wanted():
            return _cancelled(spec.intent)

        dedupe_key = f"{spec.intent}:{verdict.argument}:{verdict.text}"
        if self._dedupe.is_duplicate(dedupe_key):
            return ExecutionResult(
                status=ExecutionStatus.DUPLICATE,
                intent=spec.intent,
                message="Ignored a repeated command.",
                detail={"reason": "duplicate"},
            )

        handler = self._handlers.get(spec.intent)
        if handler is None:
            return ExecutionResult(
                status=ExecutionStatus.ERROR,
                intent=spec.intent,
                message="That command is registered but not wired up yet.",
                detail={"reason": "no_handler"},
            )

        command = ResolvedCommand(
            spec=spec,
            argument=verdict.argument,
            text=verdict.text,
            source=proposal.source,
        )
        try:
            with self._context.running(spec):
                result = handler.execute(command)
            if not isinstance(result, ExecutionResult):
                # Executors include third-party plugin code: hold them to the typed boundary.
                msg = f"{spec.intent} executor returned {type(result).__name__}, not a result"
                raise ActionExecutionError(msg)
            return result
        except Exception as exc:
            # The voice loop must survive a failing action; to_result logs, never swallows.
            return to_result(exc, spec.intent, fallback=ActionExecutionError)


def _cancelled(intent: str) -> ExecutionResult:
    return ExecutionResult(
        status=ExecutionStatus.DENIED,
        intent=intent,
        message="Cancelled — listening was stopped.",
        detail={"reason": "muted"},
    )


def _deny_message(reason: str) -> str:
    if reason == "no_action":
        return "No action taken."
    if reason == "unknown_intent":
        return "I don't have a command for that."
    if reason == "low_confidence":
        return "I wasn't confident enough to act on that."
    if reason.startswith("permission_denied"):
        return "That action isn't permitted."
    if reason in {"argument_out_of_range", "invalid_argument_type", "missing_argument"}:
        return "That value is outside the allowed range."
    if reason == "argument_not_allowed":
        return "I can't open that: not an app on this PC, or not a plain https web address."
    return "That action was refused."
