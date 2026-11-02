"""T-042: browser, media and window-management are real plugins now.

Every command is driven through the shipped agent — registry, policy, dispatcher and the
plugin's permission-guarded host — exactly as a spoken command would be.
"""

from __future__ import annotations

import pytest
from tests.conftest import Shipped

from voice_agent.core.models import ExecutionStatus, IntentProposal, Risk


def test_every_enabled_plugin_loads(shipped: Shipped) -> None:
    report = shipped.agent.plugins
    assert set(report.loaded) == {
        "browser",
        "media",
        "window-management",
        "editing",
        "desktop",
        "zoom",
    }
    assert report.rejected == {}


@pytest.mark.parametrize(
    ("intent", "chord"),
    [
        ("browser.next_tab", ("ctrl", "tab")),
        ("browser.previous_tab", ("ctrl", "shift", "tab")),
        ("browser.close_tab", ("ctrl", "w")),
        ("browser.reopen_tab", ("ctrl", "shift", "t")),
        ("media.play_pause", ("playpause",)),
        ("media.mute", ("volumemute",)),
        ("window.focus_next", ("alt", "tab")),
        ("window.snap_left", ("win", "left")),
        ("window.snap_right", ("win", "right")),
        ("browser.new_tab", ("ctrl", "t")),
        ("browser.new_window", ("ctrl", "n")),
        ("browser.refresh", ("f5",)),
        ("browser.back", ("alt", "left")),
        ("browser.forward", ("alt", "right")),
        ("browser.find", ("ctrl", "f")),
        ("browser.bookmark", ("ctrl", "d")),
        ("media.next_track", ("nexttrack",)),
        ("media.previous_track", ("prevtrack",)),
        ("media.stop", ("stop",)),
        ("edit.copy", ("ctrl", "c")),
        ("edit.paste", ("ctrl", "v")),
        ("edit.cut", ("ctrl", "x")),
        ("edit.undo", ("ctrl", "z")),
        ("edit.redo", ("ctrl", "y")),
        ("edit.select_all", ("ctrl", "a")),
        ("edit.save", ("ctrl", "s")),
        ("edit.print", ("ctrl", "p")),
        ("page.down", ("pagedown",)),
        ("page.up", ("pageup",)),
        ("page.top", ("ctrl", "home")),
        ("page.bottom", ("ctrl", "end")),
        ("desktop.show", ("win", "d")),
        ("desktop.minimize_all", ("win", "m")),
        ("desktop.start_menu", ("win",)),
        ("desktop.file_explorer", ("win", "e")),
        ("desktop.settings", ("win", "i")),
        ("desktop.screenshot", ("win", "shift", "s")),
        ("desktop.task_manager", ("ctrl", "shift", "esc")),
        ("desktop.task_view", ("win", "tab")),
        ("desktop.next_desktop", ("ctrl", "win", "right")),
        ("desktop.previous_desktop", ("ctrl", "win", "left")),
        ("desktop.notifications", ("win", "n")),
        ("desktop.clipboard", ("win", "v")),
        ("zoom.in", ("ctrl", "add")),
    ],
)
def test_single_chord_commands(shipped: Shipped, intent: str, chord: tuple[str, ...]) -> None:
    assert shipped.dispatch(intent).ok
    assert shipped.keys.calls == [chord]


def test_volume_moves_by_a_noticeable_step(shipped: Shipped) -> None:
    shipped.dispatch("media.volume_up")
    assert shipped.keys.calls == [("volumeup",)] * 5


@pytest.mark.parametrize(
    ("phrase", "presses"),
    [("skip forward 45 seconds", [("right",)] * 9), ("rewind 10", [("left",)] * 2)],
)
def test_spoken_duration_reaches_the_plugin(
    shipped: Shipped, phrase: str, presses: list[tuple[str, ...]]
) -> None:
    assert shipped.agent.handle(phrase).ok
    assert shipped.keys.calls == presses


@pytest.mark.parametrize("seconds", [0, 601])
def test_seek_outside_one_to_six_hundred_is_refused(shipped: Shipped, seconds: int) -> None:
    result = shipped.dispatch("media.seek_forward", seconds)
    assert result.status is ExecutionStatus.DENIED
    assert shipped.keys.calls == []


@pytest.mark.parametrize("op", ["minimize", "maximize"])
def test_window_backend_commands(shipped: Shipped, op: str) -> None:
    assert shipped.dispatch(f"window.{op}").ok
    assert shipped.windows.calls == [op]


def test_closing_a_window_is_high_risk_and_needs_a_yes(shipped: Shipped) -> None:
    assert shipped.agent.registry.require("window.close").risk is Risk.HIGH
    result = shipped.dispatch("window.close")  # no prompt wired: silence is never consent
    assert result.status is ExecutionStatus.DENIED
    assert shipped.windows.calls == []


def test_closing_a_tab_is_medium_risk_and_runs_without_a_prompt(shipped: Shipped) -> None:
    assert shipped.agent.registry.require("browser.close_tab").risk is Risk.MEDIUM
    assert shipped.dispatch("browser.close_tab").ok


@pytest.mark.parametrize(
    ("phrase", "intent"),
    [
        ("close this tab", "browser.close_tab"),
        ("bring that tab back", "browser.reopen_tab"),
        ("seek backward", "media.seek_backward"),
        ("hide this window", "window.minimize"),
    ],
)
def test_plugin_aliases_resolve_locally(shipped: Shipped, phrase: str, intent: str) -> None:
    proposal = shipped.agent.router.resolve(phrase)
    assert proposal is not None
    assert proposal.intent == intent


def test_plugin_commands_carry_their_plugin_and_permissions(shipped: Shipped) -> None:
    registry = shipped.agent.registry
    assert registry.require("browser.next_tab").plugin_id == "browser"
    assert set(registry.require("media.seek_forward").permissions) == {
        "media.control",
        "keyboard.shortcut",
    }
    assert registry.require("ui.exit_fullscreen").plugin_id == "builtin"


def test_disabling_a_plugin_removes_its_commands_everywhere(shipped_with: object) -> None:
    agent = shipped_with(enabled=["browser"]).agent  # type: ignore[operator]
    assert "media.play_pause" not in agent.registry
    assert "media.play_pause" not in agent.dispatcher.wired_intents
    result = agent.dispatcher.dispatch(IntentProposal(intent="media.play_pause"))
    assert result.detail["reason"] == "unknown_intent"
