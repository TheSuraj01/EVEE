"""Tray menu and the UI-mode confirmation dialog, headless. The status text is Qt-free."""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest

from voice_agent.core.models import CommandSpec
from voice_agent.ui.status import describe
from voice_agent.wakeword.activation_controller import State


@pytest.mark.parametrize(
    ("state", "muted", "running", "label"),
    [
        (State.LISTENING, False, True, "Listening"),
        (State.COOLDOWN, False, True, "Listening"),
        (State.CAPTURING, False, True, "Hearing you"),
        (State.PROCESSING, False, True, "Working"),
        (State.IDLE, True, True, "Muted"),
        (State.IDLE, False, True, "Microphone offline"),
        (State.IDLE, False, False, "Stopped"),
    ],
)
def test_status_text(state: State, muted: bool, running: bool, label: str) -> None:
    assert describe(state, muted, running)[0] == label


def test_muted_and_live_are_never_the_same_colour() -> None:
    assert describe(State.IDLE, True, True)[1] != describe(State.LISTENING, False, True)[1]


class FakeService:
    def __init__(self) -> None:
        self.state, self.muted, self.running = State.LISTENING, False, True
        self.loading = False
        self.asleep = False
        self.calls: list[str] = []

    def mute(self) -> None:
        self.calls.append("mute")
        self.state, self.muted = State.IDLE, True

    def unmute(self) -> None:
        self.calls.append("unmute")
        self.state, self.muted = State.LISTENING, False

    def emergency_stop(self) -> None:
        self.calls.append("stop")
        self.mute()

    def tap(self) -> None:
        self.calls.append("tap")


@pytest.fixture
def tray(qapp: Any) -> tuple[Any, FakeService, list[str]]:
    from voice_agent.ui.tray import Tray

    service, opened = FakeService(), []
    tray = Tray(
        service,
        on_settings=lambda: opened.append("settings"),
        on_logs=lambda: opened.append("logs"),
        on_quit=lambda: opened.append("quit"),
        on_clear_history=lambda: opened.append("purge"),
        on_copy_diagnostics=lambda: opened.append("diagnostics"),
    )
    return tray, service, opened


def _action(tray: Any, text: str) -> Any:
    [action] = [a for a in tray.menu.actions() if a.text() == text]
    return action


def test_toggle_mutes_then_listens(tray: tuple[Any, FakeService, list[str]]) -> None:
    view, service, _ = tray
    _action(view, "Mute").trigger()
    assert service.calls == ["mute"]
    assert "Muted" in view.icon.toolTip()
    _action(view, "Listen").trigger()
    assert service.calls == ["mute", "unmute"]
    assert "Listening" in view.icon.toolTip()


def test_emergency_stop_is_one_click(tray: tuple[Any, FakeService, list[str]]) -> None:
    view, service, _ = tray
    _action(view, "Emergency stop").trigger()
    assert service.calls[0] == "stop"
    assert _action(view, "Listen")  # the toggle now offers to resume


def test_settings_logs_and_quit(tray: tuple[Any, FakeService, list[str]]) -> None:
    view, _, opened = tray
    for text in (
        "Settings...",
        "Open logs",
        "Clear command history",
        "Copy diagnostics",
        "Quit",
    ):
        _action(view, text).trigger()
    assert opened == ["settings", "logs", "purge", "diagnostics", "quit"]


def test_refresh_follows_state_changes_made_elsewhere(
    tray: tuple[Any, FakeService, list[str]],
) -> None:
    view, service, _ = tray
    service.state = State.CAPTURING  # e.g. the wake word just fired on the voice thread
    view.refresh()
    assert "Hearing you" in view.icon.toolTip()


def test_status_line_shows_the_latest_service_message(
    tray: tuple[Any, FakeService, list[str]],
) -> None:
    view, _, _ = tray
    view.set_status("Microphone lost: unplugged?")
    assert "Microphone lost" in view.icon.toolTip()


def test_dialog_prompt_times_out_to_no(qapp: Any) -> None:
    """Silence is never consent, in UI mode too: the dialog closes itself and answers no."""
    from voice_agent.ui.application import QtConfirmPrompt

    prompt = QtConfirmPrompt()
    answers: list[bool] = []
    spec = CommandSpec(intent="window.close", description="Close the focused window")
    worker = threading.Thread(target=lambda: answers.append(prompt.confirm(spec, 0.05)))
    worker.start()
    deadline = time.monotonic() + 3
    while worker.is_alive() and time.monotonic() < deadline:
        qapp.processEvents()
        worker.join(0.01)
    assert answers == [False]


def _pump_until(qapp: Any, condition: Any, timeout_s: float = 3.0) -> None:
    deadline = time.monotonic() + timeout_s
    while not condition() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.005)


def _confirm_in_thread(prompt: Any, timeout_s: float) -> tuple[threading.Thread, list[bool]]:
    answers: list[bool] = []
    spec = CommandSpec(intent="window.close", description="Close the focused window")
    worker = threading.Thread(target=lambda: answers.append(prompt.confirm(spec, timeout_s)))
    worker.start()
    return worker, answers


def test_a_stale_timer_from_an_approved_box_cannot_approve_the_next_one(qapp: Any) -> None:
    """Phase 4 audit P0 (a): box #1's self-close timer fired during #2 and said Yes for it."""
    from PySide6.QtWidgets import QMessageBox

    from voice_agent.ui.application import QtConfirmPrompt

    prompt = QtConfirmPrompt()
    first, first_answer = _confirm_in_thread(prompt, 0.3)
    _pump_until(qapp, lambda: prompt._box is not None)
    prompt._box.button(QMessageBox.StandardButton.Yes).click()  # the user approves #1
    _pump_until(qapp, lambda: not first.is_alive())
    assert first_answer == [True]

    second, second_answer = _confirm_in_thread(prompt, 0.6)  # nobody touches #2
    _pump_until(qapp, lambda: not second.is_alive())  # #1's timer fires meanwhile
    assert second_answer == [False]


def test_a_leftover_box_cannot_approve_a_different_command(qapp: Any) -> None:
    """Phase 4 audit P0 (b): after a cancel, Yes on the old box approved the next intent."""
    from PySide6.QtWidgets import QMessageBox

    from voice_agent.ui.application import QtConfirmPrompt

    prompt = QtConfirmPrompt()
    first, first_answer = _confirm_in_thread(prompt, 2.0)
    _pump_until(qapp, lambda: prompt._box is not None)
    stale = prompt._box

    second, second_answer = _confirm_in_thread(prompt, 0.4)
    _pump_until(qapp, lambda: prompt._box is not stale)
    assert not stale.isVisible()  # a new question closes the old box
    stale.button(QMessageBox.StandardButton.Yes).click()  # a late click on the old box
    _pump_until(qapp, lambda: not (first.is_alive() or second.is_alive()))
    assert second_answer == [False]
    assert first_answer == [False]


def test_toast_hides_the_transcript_unless_transcripts_are_stored() -> None:
    pytest.importorskip("PySide6")
    from voice_agent.core.models import ExecutionResult, ExecutionStatus
    from voice_agent.ui.application import describe_result

    result = ExecutionResult(status=ExecutionStatus.SUCCESS, message="Switched tab")
    assert describe_result("open my bank", result, include_transcript=False) == "Switched tab"
    assert "open my bank" in describe_result("open my bank", result, include_transcript=True)


def test_clear_history_is_labelled_for_what_it_actually_clears(
    tray: tuple[Any, FakeService, list[str]],
) -> None:
    view, _, _ = tray
    assert _action(view, "Clear command history")


def test_dismiss_closes_the_dialog_and_answers_no(qapp: Any) -> None:
    """After an emergency stop the question is void: the box must not linger on screen."""
    from voice_agent.ui.application import QtConfirmPrompt

    prompt = QtConfirmPrompt()
    worker, answers = _confirm_in_thread(prompt, 5.0)
    _pump_until(qapp, lambda: prompt._box is not None)
    box = prompt._box
    prompt.dismiss()  # called from whatever thread the kill switch runs on
    _pump_until(qapp, lambda: not worker.is_alive(), timeout_s=2.0)
    assert answers == [False]
    assert not box.isVisible()


def test_the_menu_names_the_emergency_key_that_actually_works(tray: Any) -> None:
    """Notifications can be hidden (Do Not Disturb); the menu can't."""
    view, _, _ = tray

    view.show_stop_key("ctrl+alt+backspace")
    assert "Emergency stop (ctrl+alt+backspace)" in [a.text() for a in view.menu.actions()]

    view.show_stop_key(None)
    assert "Emergency stop (no hotkey)" in [a.text() for a in view.menu.actions()]


def test_loading_models_is_not_reported_as_a_dead_microphone() -> None:
    """First run downloads the models for minutes; red 'Microphone offline' would look
    broken. A user's mute still wins over the loading notice."""
    assert describe(State.IDLE, False, True, loading=True)[0] == "Loading models"
    assert describe(State.IDLE, True, True, loading=True)[0] == "Muted"
    assert describe(State.IDLE, False, True)[0] == "Microphone offline"


def test_left_clicking_the_tray_dot_talks_to_the_agent(tray: Any) -> None:
    from PySide6.QtWidgets import QSystemTrayIcon

    view, service, _ = tray
    view.icon.activated.emit(QSystemTrayIcon.ActivationReason.Trigger)

    assert service.calls[-1] == "tap"


def test_asleep_is_its_own_state_and_hides_the_capture_flicker() -> None:
    """Asleep, it still transcribes speech locally to find the wake phrase; the pill shouldn't
    flash 'Hearing you' for every sentence in the room."""
    assert describe(State.LISTENING, False, True, asleep=True)[0] == "Asleep"
    assert describe(State.CAPTURING, False, True, asleep=True)[0] == "Asleep"
    assert describe(State.IDLE, True, True, asleep=True)[0] == "Muted"
    assert describe(State.LISTENING, False, True, asleep=False)[0] == "Listening"


def test_settings_opens_again_on_top_and_never_modal(qapp: Any) -> None:
    """Opened from the indicator, which never takes focus, a window can land behind the
    user's app; a modal one hidden there blocked the indicator and Settings itself."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QDialog

    from voice_agent.ui.application import open_settings

    made: list[QDialog] = []

    def make() -> QDialog:
        made.append(QDialog())
        return made[-1]

    first = open_settings(None, make)
    assert first.isVisible() and not first.isModal()
    assert first.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    assert open_settings(first, make) is first  # still open: brought forward, not doubled
    assert len(made) == 1

    first.close()
    second = open_settings(first, make)  # closed: a fresh one, showing what was last saved
    assert second is not first and second.isVisible()
    second.close()


def test_waking_and_sleeping_each_have_their_own_chime() -> None:
    from voice_agent.core.models import ExecutionResult, ExecutionStatus
    from voice_agent.ui.application import SLEEP_SOUND, WAKE_SOUND, chime
    from voice_agent.voice_loop import ASLEEP, AWAKE

    played: list[int] = []
    for phase in (AWAKE, ASLEEP, None):
        detail = {} if phase is None else {"phase": phase}
        chime(ExecutionResult(status=ExecutionStatus.SUCCESS, detail=detail), played.append)

    assert played == [WAKE_SOUND, SLEEP_SOUND]  # a command result plays nothing
    assert WAKE_SOUND != SLEEP_SOUND
