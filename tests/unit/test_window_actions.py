"""Window actions run against a recording backend: no real window is ever touched."""

from __future__ import annotations

import sys
from typing import Any

import pytest
from tests.conftest import AlwaysConfirm, FakeClock, RecordingWindowBackend

from voice_agent.actions.windows import WindowActions, is_shell_window
from voice_agent.core.exceptions import ActionExecutionError
from voice_agent.core.interfaces import WindowBackend
from voice_agent.core.models import (
    CommandSpec,
    ExecutionStatus,
    IntentProposal,
    ResolvedCommand,
    Risk,
)
from voice_agent.engine.command_registry import CommandRegistry
from voice_agent.engine.deduplicator import Deduplicator
from voice_agent.engine.dispatcher import Dispatcher
from voice_agent.safety.policy_engine import PolicyEngine


@pytest.fixture
def shipped(shipped_with: Any) -> CommandRegistry:
    """The full registry: window.* now comes from the window-management plugin."""
    return shipped_with().agent.registry  # type: ignore[no-any-return]


def _cmd(intent: str) -> ResolvedCommand:
    return ResolvedCommand(spec=CommandSpec(intent=intent))


def _dispatcher(
    registry: CommandRegistry, backend: RecordingWindowBackend, confirm: bool
) -> Dispatcher:
    dispatcher = Dispatcher(
        registry,
        PolicyEngine(registry),
        Deduplicator(clock=FakeClock()),
        confirmation=AlwaysConfirm(confirm),
        clock=FakeClock(),
    )
    dispatcher.register_handler("window.close", WindowActions(backend))
    return dispatcher


def test_fake_backend_satisfies_the_protocol() -> None:
    assert isinstance(RecordingWindowBackend(), WindowBackend)


@pytest.mark.parametrize("op", ["minimize", "maximize", "close"])
def test_window_intent_calls_the_backend(op: str) -> None:
    backend = RecordingWindowBackend()
    assert WindowActions(backend).execute(_cmd(f"window.{op}")).ok
    assert backend.calls == [op]


def test_unknown_window_intent_raises() -> None:
    backend = RecordingWindowBackend()
    with pytest.raises(ActionExecutionError):
        WindowActions(backend).execute(_cmd("window.destroy_everything"))
    assert backend.calls == []


def test_close_is_high_risk_and_needs_confirmation(shipped: CommandRegistry) -> None:
    assert shipped.require("window.close").risk is Risk.HIGH

    declined = RecordingWindowBackend()
    result = _dispatcher(shipped, declined, confirm=False).dispatch(
        IntentProposal(intent="window.close")
    )
    assert result.status is ExecutionStatus.DENIED
    assert declined.calls == []

    accepted = RecordingWindowBackend()
    result = _dispatcher(shipped, accepted, confirm=True).dispatch(
        IntentProposal(intent="window.close")
    )
    assert result.ok
    assert accepted.calls == ["close"]


def test_backend_failure_becomes_a_safe_error(shipped: CommandRegistry) -> None:
    backend = RecordingWindowBackend(raises=ActionExecutionError("no foreground hwnd 0x0"))
    result = _dispatcher(shipped, backend, confirm=True).dispatch(
        IntentProposal(intent="window.close")
    )
    assert result.status is ExecutionStatus.ERROR
    assert "hwnd" not in result.message


@pytest.mark.parametrize("cls", ["Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"])
def test_desktop_and_taskbar_are_never_targets(cls: str) -> None:
    assert is_shell_window(cls)


def test_application_windows_are_targets() -> None:
    assert not is_shell_window("Chrome_WidgetWin_1")


def test_suite_never_imports_pywin32() -> None:
    assert "win32gui" not in sys.modules
