"""Keyboard actions run against a recording KeySender: nothing is ever typed for real."""

from __future__ import annotations

import sys

import pytest
from tests.conftest import FakeClock, RecordingKeySender

from voice_agent.actions.keyboard import MAX_PRESSES, UI_SHORTCUTS, KeyboardActions, Shortcut
from voice_agent.core.exceptions import ActionExecutionError
from voice_agent.core.models import CommandSpec, ExecutionStatus, ResolvedCommand
from voice_agent.engine.catalog import load_command_specs
from voice_agent.engine.command_registry import CommandRegistry
from voice_agent.engine.deduplicator import Deduplicator
from voice_agent.engine.dispatcher import Dispatcher
from voice_agent.nlu.alias_matcher import AliasMatcher
from voice_agent.safety.policy_engine import PolicyEngine


def _cmd(intent: str, argument: int | None = None) -> ResolvedCommand:
    return ResolvedCommand(spec=CommandSpec(intent=intent), argument=argument)


def test_exit_fullscreen_sends_escape() -> None:
    keys = RecordingKeySender()
    result = KeyboardActions(keys).execute(_cmd("ui.exit_fullscreen"))
    assert keys.calls == [("esc",)]
    assert result.ok


def test_enter_fullscreen_sends_f11() -> None:
    keys = RecordingKeySender()
    KeyboardActions(keys).execute(_cmd("ui.enter_fullscreen"))
    assert keys.calls == [("f11",)]


def test_spoken_phrase_presses_escape_end_to_end() -> None:
    registry = CommandRegistry()
    registry.register_all(load_command_specs())
    keys = RecordingKeySender()
    dispatcher = Dispatcher(registry, PolicyEngine(registry), Deduplicator(clock=FakeClock()))
    dispatcher.register_handler("ui.exit_fullscreen", KeyboardActions(keys))

    proposal = AliasMatcher(registry).match("get me out of full screen")
    assert proposal is not None
    assert dispatcher.dispatch(proposal).status is ExecutionStatus.SUCCESS
    assert keys.calls == [("esc",)]


def test_intent_outside_the_table_raises_instead_of_guessing() -> None:
    keys = RecordingKeySender()
    with pytest.raises(ActionExecutionError):
        KeyboardActions(keys).execute(_cmd("browser.next_tab"))
    assert keys.calls == []


def test_repeat_presses_the_chord_several_times() -> None:
    keys = RecordingKeySender()
    actions = KeyboardActions(keys, {"media.volume_up": Shortcut(("volumeup",), repeat=3)})
    actions.execute(_cmd("media.volume_up"))
    assert keys.calls == [("volumeup",)] * 3


@pytest.mark.parametrize(("argument", "presses"), [(None, 1), (1, 1), (5, 1), (6, 2), (30, 6)])
def test_duration_maps_to_one_press_per_step(argument: int | None, presses: int) -> None:
    assert Shortcut(("right",), seconds_per_press=5).presses(argument) == presses


def test_presses_are_capped_even_if_policy_regresses() -> None:
    assert Shortcut(("right",), seconds_per_press=1).presses(10_000) == MAX_PRESSES


def test_every_ui_shortcut_is_a_registered_intent() -> None:
    registry = CommandRegistry()
    registry.register_all(load_command_specs())
    assert set(UI_SHORTCUTS) <= set(registry.intents())


def test_suite_never_imports_pyautogui() -> None:
    assert "pyautogui" not in sys.modules
