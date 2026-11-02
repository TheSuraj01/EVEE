"""Silence is never consent: timeout, error, cancel or a non-True answer all mean no."""

from __future__ import annotations

import io
import threading
import time

import pytest
from tests.conftest import AlwaysConfirm, FakeClock, RecordingWindowBackend

from voice_agent.actions.windows import WindowActions
from voice_agent.core.models import CommandSpec, IntentProposal, Risk
from voice_agent.engine.command_registry import CommandRegistry
from voice_agent.engine.deduplicator import Deduplicator
from voice_agent.engine.dispatcher import Dispatcher
from voice_agent.safety.confirmation import ConfirmationBroker, ConsolePrompt
from voice_agent.safety.policy_engine import PolicyEngine

SPEC = CommandSpec(intent="window.close", description="Close the focused window", risk=Risk.HIGH)


class HangingPrompt:
    """A prompt that ignores its timeout — the broker must not wait for it."""

    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()

    def confirm(self, spec: CommandSpec, timeout_s: float) -> bool:
        self.started.set()
        self.release.wait(5)
        return True


class ExplodingPrompt:
    def confirm(self, spec: CommandSpec, timeout_s: float) -> bool:
        raise RuntimeError("dialog crashed")


class TruthyPrompt:
    def confirm(self, spec: CommandSpec, timeout_s: float) -> bool:
        return "yes"  # type: ignore[return-value]


@pytest.fixture
def hanging() -> HangingPrompt:
    prompt = HangingPrompt()
    yield prompt
    prompt.release.set()


@pytest.mark.parametrize("answer", [True, False])
def test_broker_passes_through_a_real_answer(answer: bool) -> None:
    assert ConfirmationBroker(AlwaysConfirm(answer)).confirm(SPEC, timeout_s=1) is answer


def test_only_literal_true_is_consent() -> None:
    assert ConfirmationBroker(TruthyPrompt()).confirm(SPEC, timeout_s=1) is False


def test_prompt_failure_is_a_denial(caplog: pytest.LogCaptureFixture) -> None:
    assert ConfirmationBroker(ExplodingPrompt()).confirm(SPEC, timeout_s=1) is False
    assert any("prompt failed" in r.getMessage() for r in caplog.records)


def test_timeout_is_a_denial_even_if_the_prompt_hangs(hanging: HangingPrompt) -> None:
    started = time.monotonic()
    assert ConfirmationBroker(hanging, grace_s=0).confirm(SPEC, timeout_s=0.03) is False
    assert time.monotonic() - started < 1


def test_cancel_denies_a_pending_confirmation(hanging: HangingPrompt) -> None:
    """Invariant 8: the emergency stop must work while an action is pending."""
    broker = ConfirmationBroker(hanging)
    result: list[bool] = []
    worker = threading.Thread(target=lambda: result.append(broker.confirm(SPEC, timeout_s=5)))
    worker.start()
    assert hanging.started.wait(1)
    broker.cancel()
    worker.join(1)
    assert result == [False]


@pytest.mark.parametrize(
    ("key", "expected"), [("y", True), ("Y", True), ("n", False), (None, False)]
)
def test_console_prompt_reads_one_key(key: str | None, expected: bool) -> None:
    out = io.StringIO()
    prompt = ConsolePrompt(read_key=lambda _timeout: key, out=out)
    assert prompt.confirm(SPEC, timeout_s=10) is expected
    assert "Close the focused window" in out.getvalue()
    if key is None:
        assert "timed out" in out.getvalue()


def test_dispatcher_hands_its_configured_timeout_to_the_prompt(
    registry: CommandRegistry,
) -> None:
    seen: list[float] = []

    class Recording:
        def confirm(self, spec: CommandSpec, timeout_s: float) -> bool:
            seen.append(timeout_s)
            return True

    backend = RecordingWindowBackend()
    dispatcher = Dispatcher(
        registry,
        PolicyEngine(registry),
        Deduplicator(clock=FakeClock()),
        confirmation=ConfirmationBroker(Recording()),
        clock=FakeClock(),
        confirmation_timeout_s=3,
    )
    dispatcher.register_handler("window.close", WindowActions(backend))
    assert dispatcher.dispatch(IntentProposal(intent="window.close")).ok
    assert seen == [3]
    assert backend.calls == ["close"]


class FocusStealingPrompt:
    """Like a real console or dialog prompt: answering it moves focus to the prompt."""

    def __init__(self, backend: RecordingWindowBackend, answer: bool) -> None:
        self._backend = backend
        self._answer = answer

    def confirm(self, spec: CommandSpec, timeout_s: float) -> bool:
        self._backend.current = 7  # the prompt's own window
        return self._answer


def test_confirmed_action_runs_on_the_window_focused_before_the_prompt(
    registry: CommandRegistry,
) -> None:
    """Phase 1 audit P1: WM_CLOSE must not hit the prompt that just stole focus."""
    backend = RecordingWindowBackend(foreground=42)
    dispatcher = Dispatcher(
        registry,
        PolicyEngine(registry),
        Deduplicator(clock=FakeClock()),
        confirmation=ConfirmationBroker(FocusStealingPrompt(backend, True), windows=backend),
        clock=FakeClock(),
    )
    dispatcher.register_handler("window.close", WindowActions(backend))
    assert dispatcher.dispatch(IntentProposal(intent="window.close")).ok
    assert backend.activated == [42]
    assert backend.targets == [42]


def test_unrestorable_focus_is_a_denial() -> None:
    backend = RecordingWindowBackend(foreground=42, activates=False)
    broker = ConfirmationBroker(FocusStealingPrompt(backend, True), windows=backend)
    assert broker.confirm(SPEC, timeout_s=1) is False


def test_declined_prompt_does_not_touch_focus() -> None:
    backend = RecordingWindowBackend(foreground=42)
    broker = ConfirmationBroker(FocusStealingPrompt(backend, False), windows=backend)
    assert broker.confirm(SPEC, timeout_s=1) is False
    assert backend.activated == []


def test_cancel_also_dismisses_a_prompt_that_can_be_dismissed() -> None:
    class Dismissable(HangingPrompt):
        def __init__(self) -> None:
            super().__init__()
            self.dismissed = 0

        def dismiss(self) -> None:
            self.dismissed += 1
            self.release.set()

    prompt = Dismissable()
    broker = ConfirmationBroker(prompt)
    broker.cancel()
    assert prompt.dismissed == 1
    ConfirmationBroker(AlwaysConfirm(True)).cancel()  # prompts without dismiss() are fine


def test_a_cancelled_console_prompt_stops_reading_keys() -> None:
    """After a mute, the next U belongs to the console's unmute, not a dead Yes/No prompt."""
    import threading

    from voice_agent.safety.confirmation import _read_key

    stop = threading.Event()
    stop.set()
    assert _read_key(30, stop) is None  # returns at once, reading nothing

    prompt = ConsolePrompt(read_key=lambda _timeout: None, out=io.StringIO())
    prompt.dismiss()  # what ConfirmationBroker.cancel() calls
