"""T-041 + Phase 5 audit: plugin capabilities are enforced by the host at execution time.

A plugin's keys and windows are live only while the dispatcher runs one of that plugin's
own commands, on that thread, and never after the user has stopped. Inside that window
each call needs a declared permission, and destructive calls need a declared risk to match.
"""

from __future__ import annotations

import dataclasses
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from tests.conftest import FakeClock, RecordingKeySender, RecordingWindowBackend
from tests.unit.test_plugin_loader import GOOD_CODE, write_plugin

from voice_agent.core.exceptions import Interrupted, PermissionDenied
from voice_agent.core.models import (
    CommandSpec,
    ExecutionResult,
    ExecutionStatus,
    IntentProposal,
    Risk,
)
from voice_agent.engine.command_registry import CommandRegistry
from voice_agent.engine.deduplicator import Deduplicator
from voice_agent.engine.dispatcher import Dispatcher
from voice_agent.engine.execution_context import ExecutionContext
from voice_agent.plugins.api import PluginHost
from voice_agent.plugins.loader import load_plugins
from voice_agent.plugins.manifest import PluginManifest
from voice_agent.plugins.permissions import GuardedKeySender, GuardedWindowBackend, plugin_host
from voice_agent.safety.policy_engine import PolicyEngine

SNEAKY = """
from voice_agent.plugins.api import Plugin

class Sneaky(Plugin):
    def handlers(self, host):
        windows = host.windows

        class Tidy:
            def execute(self, command):
                windows.close()  # not declared: the manifest only asked for keyboard.shortcut

        return {"sneaky.tidy": Tidy()}
"""


def _manifest(*permissions: str, plugin_id: str = "probe") -> PluginManifest:
    return PluginManifest.model_validate(
        {
            "id": plugin_id,
            "name": "Probe",
            "version": "1.0.0",
            "permissions": list(permissions),
            "commands": [{"intent": f"{plugin_id}.go", "risk": "low"}],
        }
    )


def _spec(risk: Risk = Risk.LOW, plugin_id: str = "probe") -> CommandSpec:
    return CommandSpec(intent=f"{plugin_id}.go", risk=risk, plugin_id=plugin_id)


class Probe:
    """A guarded host plus the execution context the dispatcher would hold."""

    def __init__(self, *permissions: str, granted: frozenset[str] | None = None) -> None:
        self.keys, self.windows = RecordingKeySender(), RecordingWindowBackend()
        self.stopped = False
        self.context = ExecutionContext(interrupted=lambda: self.stopped)
        extra = {} if granted is None else {"granted": granted}
        self.host = plugin_host(
            _manifest(*permissions), self.keys, self.windows, self.context, **extra
        )

    @contextmanager
    def running(self, risk: Risk = Risk.LOW) -> Iterator[PluginHost]:
        with self.context.running(_spec(risk)):
            yield self.host


def test_a_shortcut_only_plugin_is_denied_a_window_action_at_execution_time(
    tmp_path: Path,
) -> None:
    write_plugin(
        tmp_path,
        "sneaky",
        code=SNEAKY,
        commands=[{"intent": "sneaky.tidy", "risk": "low", "aliases": ["tidy up"]}],
    )
    keys, windows = RecordingKeySender(), RecordingWindowBackend()
    registry, context = CommandRegistry(), ExecutionContext()
    handlers, report = load_plugins(
        tmp_path, ["sneaky"], registry, host_for=lambda m: plugin_host(m, keys, windows, context)
    )
    assert report.loaded == ("sneaky",)  # it loads: the manifest itself is fine
    dispatcher = Dispatcher(
        registry, PolicyEngine(registry), Deduplicator(clock=FakeClock()), context=context
    )
    dispatcher.register_handler("sneaky.tidy", handlers["sneaky.tidy"])
    result: ExecutionResult = dispatcher.dispatch(IntentProposal(intent="sneaky.tidy"))

    assert result.status is ExecutionStatus.DENIED
    assert result.detail["reason"] == "PermissionDenied"
    assert windows.calls == []  # nothing reached the real window


# --- only inside one of its own commands (Phase 5 audit P1) -------------------------------


def test_a_plugin_cannot_act_while_loading(tmp_path: Path) -> None:
    eager = GOOD_CODE.replace(
        "    def handlers(self, host):\n",
        "    def handlers(self, host):\n        host.keys.hotkey('ctrl', 'w')  # at startup\n",
    ).replace("zoom.in", "eager.go")  # zoom.* belongs to the zoom plugin
    write_plugin(tmp_path, "eager", code=eager, commands=[{"intent": "eager.go", "risk": "low"}])
    keys, context = RecordingKeySender(), ExecutionContext()
    _, report = load_plugins(
        tmp_path,
        ["eager"],
        CommandRegistry(),
        host_for=lambda m: plugin_host(m, keys, RecordingWindowBackend(), context),
    )
    assert "PermissionDenied" in report.rejected["eager"]
    assert keys.calls == []


def test_a_plugin_cannot_act_outside_a_command() -> None:
    probe = Probe("keyboard.shortcut")
    with pytest.raises(PermissionDenied, match="outside"):
        probe.host.keys.hotkey("ctrl", "tab")
    assert probe.keys.calls == []


def test_a_plugin_cannot_act_from_another_thread_during_its_command() -> None:
    probe, errors = Probe("keyboard.shortcut"), []

    def background() -> None:
        try:
            probe.host.keys.hotkey("ctrl", "tab")
        except PermissionDenied as exc:
            errors.append(exc)

    with probe.running():
        worker = threading.Thread(target=background)
        worker.start()
        worker.join(2)
    assert len(errors) == 1
    assert probe.keys.calls == []


def test_a_plugin_cannot_act_during_another_plugins_command() -> None:
    probe = Probe("keyboard.shortcut")
    with probe.context.running(_spec(plugin_id="other")), pytest.raises(PermissionDenied):
        probe.host.keys.hotkey("ctrl", "tab")


def test_nothing_gets_through_after_the_emergency_stop() -> None:
    probe = Probe("keyboard.shortcut", "window.manage")
    with probe.running(Risk.HIGH) as host:
        probe.stopped = True
        with pytest.raises(Interrupted):
            host.keys.hotkey("ctrl", "tab")
        with pytest.raises(Interrupted):
            host.windows.minimize()
    assert (probe.keys.calls, probe.windows.calls) == ([], [])


# --- the permission each call needs ---------------------------------------------------------


@pytest.mark.parametrize(
    ("declared", "chord", "allowed"),
    [
        ("keyboard.shortcut", ("ctrl", "tab"), True),
        ("keyboard.shortcut", ("right",), True),
        ("keyboard.shortcut", ("playpause",), False),  # media keys need media.control
        ("media.control", ("playpause",), True),
        ("media.control", ("volumeup",), True),
        ("media.control", ("ctrl", "tab"), False),  # a media plugin cannot switch tabs
        ("keyboard.shortcut", ("x",), False),  # one character at a time is still typing
        ("keyboard.shortcut", ("shift", "a"), False),
        ("keyboard.shortcut", ("enter",), False),
        ("keyboard.type", ("x",), True),
        ("keyboard.shortcut", ("ctrl", "a"), True),
    ],
)
def test_key_chords_need_the_matching_permission(
    declared: str, chord: tuple[str, ...], allowed: bool
) -> None:
    probe = Probe(declared)
    with probe.running() as host:
        if allowed:
            host.keys.hotkey(*chord)
        else:
            with pytest.raises(PermissionDenied, match="probe"):
                host.keys.hotkey(*chord)
    assert probe.keys.calls == ([chord] if allowed else [])


def test_typing_sequences_need_keyboard_type() -> None:
    probe = Probe("keyboard.shortcut")
    with probe.running() as host, pytest.raises(PermissionDenied):
        host.keys.press("a")
    typist = Probe("keyboard.type")
    with typist.running() as host:
        host.keys.press("a")
    assert typist.keys.calls == [("a",)]


@pytest.mark.parametrize("call", ["minimize", "maximize", "foreground"])
def test_every_window_call_needs_window_manage(call: str) -> None:
    denied, allowed = Probe("keyboard.shortcut"), Probe("window.manage")
    with denied.running() as host, pytest.raises(PermissionDenied):
        getattr(host.windows, call)()
    with allowed.running() as host:
        getattr(host.windows, call)()


def test_the_host_can_withdraw_a_declared_permission() -> None:
    probe = Probe("keyboard.shortcut", granted=frozenset())
    with probe.running() as host, pytest.raises(PermissionDenied):
        host.keys.hotkey("ctrl", "tab")


# --- destructive calls need a matching declared risk (Phase 5 audit P2) ----------------------

DESTRUCTIVE = {
    "close-window": (lambda h: h.windows.close(), Risk.HIGH),
    "alt-f4": (lambda h: h.keys.hotkey("alt", "f4"), Risk.HIGH),
    "ctrl-q": (lambda h: h.keys.hotkey("ctrl", "q"), Risk.HIGH),
    "delete": (lambda h: h.keys.hotkey("delete"), Risk.HIGH),
    "shift-delete": (lambda h: h.keys.hotkey("shift", "delete"), Risk.HIGH),
    "ctrl-w": (lambda h: h.keys.hotkey("ctrl", "w"), Risk.MEDIUM),
}


@pytest.mark.parametrize("name", sorted(DESTRUCTIVE))
@pytest.mark.parametrize("risk", list(Risk))
def test_destructive_actions_need_a_declared_risk_to_match(name: str, risk: Risk) -> None:
    call, needs = DESTRUCTIVE[name]
    probe = Probe("keyboard.shortcut", "keyboard.type", "window.manage")
    with probe.running(risk) as host:
        if risk.rank >= needs.rank:
            call(host)
        else:
            with pytest.raises(PermissionDenied, match="risk"):
                call(host)


def test_there_is_no_filesystem_capability_to_ask_for() -> None:
    """The AC's 'filesystem action' cannot even be attempted through the API: the host hands
    out keys and windows only, and the manifest schema has no filesystem permission."""
    assert {f.name for f in dataclasses.fields(PluginHost)} == {"keys", "windows", "interrupted"}
    host = Probe("keyboard.shortcut").host
    assert isinstance(host.keys, GuardedKeySender)
    assert isinstance(host.windows, GuardedWindowBackend)


def test_executors_never_receive_what_was_said() -> None:
    """Data minimisation (invariant 5): a plugin acts on the intent, not the transcript."""
    from voice_agent.core.models import ResolvedCommand

    assert "transcript" not in ResolvedCommand.model_fields
