"""No exception reaches the UI untranslated, and no exception text reaches the user."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from tests.conftest import AlwaysConfirm, FakeClock, RecordingHandler

from voice_agent.core import exceptions
from voice_agent.core.exceptions import ActionExecutionError, PolicyViolation, VoiceAgentError
from voice_agent.core.models import CommandSpec, ExecutionStatus, IntentProposal
from voice_agent.engine.command_registry import CommandRegistry
from voice_agent.engine.deduplicator import Deduplicator
from voice_agent.engine.dispatcher import Dispatcher
from voice_agent.engine.result_handler import to_result
from voice_agent.safety.policy_engine import PolicyEngine

LEAKY = "transcript=open my bank gsk_Fake0nlyForTests1234 C:\\Users\\me"


def _subclasses(cls: type[VoiceAgentError]) -> Iterator[type[VoiceAgentError]]:
    for sub in cls.__subclasses__():
        yield sub
        yield from _subclasses(sub)


ALL_ERRORS = [VoiceAgentError, *_subclasses(VoiceAgentError)]


def test_hierarchy_is_discovered() -> None:
    assert exceptions.LLMUnavailableError in ALL_ERRORS
    assert len(ALL_ERRORS) >= 10


@pytest.mark.parametrize("cls", ALL_ERRORS, ids=lambda c: c.__name__)
def test_every_error_declares_its_own_user_message(cls: type[VoiceAgentError]) -> None:
    assert cls.__dict__.get("user_message"), f"{cls.__name__} must declare user_message"


@pytest.mark.parametrize("cls", ALL_ERRORS, ids=lambda c: c.__name__)
def test_every_error_translates_to_its_safe_message(cls: type[VoiceAgentError]) -> None:
    result = to_result(cls(LEAKY), "browser.next_tab")
    assert result.message == cls.user_message
    assert result.status is not ExecutionStatus.SUCCESS
    assert result.intent == "browser.next_tab"
    assert result.detail == {"reason": cls.__name__}


def test_policy_violations_are_denials_not_errors() -> None:
    assert to_result(exceptions.PermissionDenied()).status is ExecutionStatus.DENIED
    assert to_result(exceptions.ConfigError()).status is ExecutionStatus.ERROR


def test_unexpected_exception_gets_a_generic_message_and_is_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    result = to_result(RuntimeError(LEAKY))
    assert result.status is ExecutionStatus.ERROR
    assert result.message == VoiceAgentError.user_message
    assert "bank" not in result.model_dump_json()
    assert any(r.exc_info for r in caplog.records)


def test_fallback_sets_the_message_for_unexpected_exceptions() -> None:
    result = to_result(ValueError(LEAKY), fallback=ActionExecutionError)
    assert result.message == ActionExecutionError.user_message


class _ExplodingPrompt:
    def confirm(self, spec: CommandSpec, timeout_s: float) -> bool:
        raise RuntimeError(LEAKY)


class _ExplodingPolicy(PolicyEngine):
    def evaluate(self, proposal: IntentProposal):  # type: ignore[override]
        raise PolicyViolation(LEAKY)


def test_dispatcher_translates_a_failing_confirmation_prompt(registry: CommandRegistry) -> None:
    dispatcher = Dispatcher(
        registry,
        PolicyEngine(registry),
        Deduplicator(clock=FakeClock()),
        confirmation=_ExplodingPrompt(),
        clock=FakeClock(),
    )
    dispatcher.register_handler("window.close", RecordingHandler())
    result = dispatcher.dispatch(IntentProposal(intent="window.close"))
    assert result.status is ExecutionStatus.ERROR
    assert "bank" not in result.model_dump_json()


def test_dispatcher_translates_a_failing_policy_and_still_audits(registry: CommandRegistry) -> None:
    audited: list[str] = []
    dispatcher = Dispatcher(
        registry,
        _ExplodingPolicy(registry),
        clock=FakeClock(),
        audit=lambda proposal, _result: audited.append(proposal.intent),
    )
    result = dispatcher.dispatch(IntentProposal(intent="browser.next_tab"))
    assert result.status is ExecutionStatus.DENIED
    assert result.latency_ms is not None
    assert audited == ["browser.next_tab"]


def test_dispatcher_handler_crash_says_the_action_failed(registry: CommandRegistry) -> None:
    dispatcher = Dispatcher(registry, PolicyEngine(registry), Deduplicator(clock=FakeClock()))
    dispatcher.register_handler("browser.next_tab", RecordingHandler(raises=ValueError(LEAKY)))
    result = dispatcher.dispatch(IntentProposal(intent="browser.next_tab"))
    assert result.status is ExecutionStatus.ERROR
    assert result.message == ActionExecutionError.user_message
    assert result.detail == {"reason": "ValueError"}


def test_a_failing_audit_sink_cannot_escape_dispatch(registry: CommandRegistry) -> None:
    def broken_sink(_proposal: IntentProposal, _result: object) -> None:
        raise RuntimeError(LEAKY)

    dispatcher = Dispatcher(
        registry, PolicyEngine(registry), Deduplicator(clock=FakeClock()), audit=broken_sink
    )
    dispatcher.register_handler("browser.next_tab", RecordingHandler())
    assert dispatcher.dispatch(IntentProposal(intent="browser.next_tab")).ok


def test_dispatch_rechecks_still_wanted_after_confirmation(registry: CommandRegistry) -> None:
    """A mute that lands while the confirmation prompt is up must stop the action."""
    wanted = [True]

    class MutesWhileAsking:
        def confirm(self, spec: CommandSpec, timeout_s: float) -> bool:
            wanted[0] = False
            return True

    handler = RecordingHandler()
    dispatcher = Dispatcher(
        registry,
        PolicyEngine(registry),
        Deduplicator(clock=FakeClock()),
        confirmation=MutesWhileAsking(),
        clock=FakeClock(),
    )
    dispatcher.register_handler("window.close", handler)
    result = dispatcher.dispatch(
        IntentProposal(intent="window.close"), still_wanted=lambda: wanted[0]
    )
    assert (result.status, result.detail["reason"]) == (ExecutionStatus.DENIED, "muted")
    assert handler.calls == []


def test_dispatch_does_not_even_prompt_when_already_unwanted(registry: CommandRegistry) -> None:
    prompt = AlwaysConfirm(True)
    dispatcher = Dispatcher(
        registry, PolicyEngine(registry), Deduplicator(clock=FakeClock()), confirmation=prompt
    )
    dispatcher.register_handler("window.close", RecordingHandler())
    result = dispatcher.dispatch(IntentProposal(intent="window.close"), still_wanted=lambda: False)
    assert result.detail["reason"] == "muted"
    assert prompt.asked == []


def test_an_executor_returning_garbage_is_an_error_not_a_crash(registry: CommandRegistry) -> None:
    """Found re-checking Phase 5: a plugin's execute() returned None and dispatch() raised."""

    class Sloppy:
        def execute(self, command: object) -> None:
            return None

    dispatcher = Dispatcher(registry, PolicyEngine(registry), Deduplicator(clock=FakeClock()))
    dispatcher.register_handler("browser.next_tab", Sloppy())  # type: ignore[arg-type]
    result = dispatcher.dispatch(IntentProposal(intent="browser.next_tab"))
    assert result.status is ExecutionStatus.ERROR
    assert result.message == ActionExecutionError.user_message
