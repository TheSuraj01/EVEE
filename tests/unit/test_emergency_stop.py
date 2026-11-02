"""Invariant 8: the hotkey parser, the interruptible key loop, and the always-on indicator."""

from __future__ import annotations

from typing import Any

import pytest
from tests.conftest import RecordingKeySender

from voice_agent.actions.keyboard import KeyboardActions, Shortcut
from voice_agent.core.exceptions import ConfigError
from voice_agent.core.models import CommandSpec, ExecutionStatus, ResolvedCommand
from voice_agent.settings.loader import load_settings
from voice_agent.ui.hotkey import parse_hotkey
from voice_agent.wakeword.activation_controller import State

CTRL, ALT, SHIFT, WIN = 0x2, 0x1, 0x4, 0x8


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("ctrl+alt+space", (CTRL | ALT, 0x20)),
        ("Ctrl + Alt + Space", (CTRL | ALT, 0x20)),
        ("win+shift+s", (WIN | SHIFT, ord("S"))),
        ("ctrl+f12", (CTRL, 0x7B)),
        ("alt+pause", (ALT, 0x13)),
        ("ctrl+shift+9", (CTRL | SHIFT, ord("9"))),
    ],
)
def test_hotkey_parses(spec: str, expected: tuple[int, int]) -> None:
    assert parse_hotkey(spec) == expected


@pytest.mark.parametrize(
    "spec",
    ["space", "shift+a", "ctrl+", "ctrl+alt+spacebar", "hyper+space", "", "ctrl++space"],
)
def test_hotkeys_that_would_hijack_typing_or_make_no_sense_are_refused(spec: str) -> None:
    with pytest.raises(ConfigError):
        parse_hotkey(spec)


def test_shipped_default_hotkey_is_valid() -> None:
    assert parse_hotkey(load_settings(env={}).app.emergency_stop_hotkey) == (CTRL | ALT, 0x20)


def test_key_loop_stops_between_presses_when_interrupted() -> None:
    keys, stop = RecordingKeySender(), [False]

    def press(*chord: str) -> None:
        keys.calls.append(chord)
        stop[0] = len(keys.calls) == 2  # emergency stop lands after the 2nd press

    keys.hotkey = press  # type: ignore[method-assign]
    actions = KeyboardActions(
        keys,
        {"media.seek_forward": Shortcut(("right",), seconds_per_press=5)},
        interrupted=lambda: stop[0],
    )
    result = actions.execute(
        ResolvedCommand(spec=CommandSpec(intent="media.seek_forward"), argument=600)
    )
    assert keys.calls == [("right",), ("right",)]  # not 120
    assert (result.status, result.detail["reason"]) == (ExecutionStatus.DENIED, "interrupted")


class FakeService:
    def __init__(self) -> None:
        self.state, self.muted, self.running = State.LISTENING, False, True
        self.loading = False
        self.asleep = False

    def mute(self) -> None: ...

    def unmute(self) -> None: ...

    def emergency_stop(self) -> None: ...


def test_indicator_never_takes_focus_and_stays_on_top(qapp: Any) -> None:
    from PySide6.QtCore import Qt

    from voice_agent.ui.status_indicator import StatusIndicator

    flags = StatusIndicator(FakeService()).windowFlags()
    for flag in (
        Qt.WindowType.WindowStaysOnTopHint,
        Qt.WindowType.WindowDoesNotAcceptFocus,  # a click must leave focus on the user's app
        Qt.WindowType.FramelessWindowHint,
    ):
        assert flags & flag, flag


def test_indicator_follows_the_service(qapp: Any) -> None:
    from voice_agent.ui.status_indicator import StatusIndicator

    service = FakeService()
    indicator = StatusIndicator(service)
    assert "Listening" in indicator.label.text()
    service.state, service.muted = State.IDLE, True
    indicator.refresh()
    assert "Muted" in indicator.label.text()


def _contrast(foreground: str, background: str) -> float:
    def luminance(color: str) -> float:
        channels = [int(color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
        r, g, b = (c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels)
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    hi, lo = sorted((luminance(foreground), luminance(background)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


@pytest.mark.parametrize(
    ("state", "muted", "running"),
    [
        (State.LISTENING, False, True),
        (State.CAPTURING, False, True),
        (State.PROCESSING, False, True),
        (State.IDLE, True, True),
        (State.IDLE, False, True),
    ],
)
def test_indicator_text_is_readable_and_the_state_colour_is_on_the_dot(
    qapp: Any, state: State, muted: bool, running: bool
) -> None:
    """WCAG AA: 4.5:1 for the words, 3:1 for the coloured dot, against the pill."""
    from voice_agent.ui.status import describe
    from voice_agent.ui.status_indicator import PILL, TEXT, StatusIndicator

    service = FakeService()
    service.state, service.muted, service.running = state, muted, running
    indicator = StatusIndicator(service)
    _, colour = describe(state, muted, running)

    assert _contrast(TEXT, PILL) >= 4.5
    assert _contrast(colour, PILL) >= 3.0
    assert f"color: {colour}" in indicator.label.text()  # the dot
    assert f"color: {TEXT}" in indicator.label.styleSheet()  # the words


def _fake_hotkeys(taken: set[str]) -> tuple[Any, list[str]]:
    tried: list[str] = []

    class FakeHotkey:
        def __init__(self, spec: str, on_press: Any) -> None:
            parse_hotkey(spec)  # an invalid spec fails here, as GlobalHotkey's does
            self.spec = spec

        def start(self) -> bool:
            tried.append(self.spec)
            return self.spec not in taken

    return FakeHotkey, tried


def test_the_configured_emergency_hotkey_is_used_when_free() -> None:
    from voice_agent.ui.hotkey import start_emergency_hotkey

    make, tried = _fake_hotkeys(taken=set())
    hotkey, notice = start_emergency_hotkey("ctrl+alt+space", lambda: None, make=make)

    assert (hotkey.spec, notice, tried) == ("ctrl+alt+space", None, ["ctrl+alt+space"])


@pytest.mark.parametrize("configured", ["ctrl+alt+space", "nonsense"])
def test_a_taken_or_invalid_emergency_hotkey_falls_back_and_says_which_key_works(
    configured: str,
) -> None:
    from voice_agent.ui.hotkey import start_emergency_hotkey

    make, _ = _fake_hotkeys(taken={"ctrl+alt+space"})
    hotkey, notice = start_emergency_hotkey(configured, lambda: None, make=make)

    assert hotkey.spec == "ctrl+alt+backspace"
    assert notice is not None
    assert "ctrl+alt+backspace" in notice
    assert configured in notice


def test_no_free_emergency_hotkey_is_reported_not_raised() -> None:
    from voice_agent.ui.hotkey import FALLBACK_HOTKEYS, start_emergency_hotkey

    make, tried = _fake_hotkeys(taken={"ctrl+alt+space", *FALLBACK_HOTKEYS})
    hotkey, notice = start_emergency_hotkey("ctrl+alt+space", lambda: None, make=make)

    assert hotkey is None
    assert notice is not None
    assert "No emergency-stop hotkey is free" in notice
    assert tried == ["ctrl+alt+space", *FALLBACK_HOTKEYS]


class TappableService(FakeService):
    def __init__(self) -> None:
        super().__init__()
        self.taps = 0

    def tap(self) -> None:
        self.taps += 1


def _mouse(widget: Any, kind: Any, button: Any, buttons: Any, at: Any) -> None:
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication

    local = QPointF(widget.mapFromGlobal(at))
    event = QMouseEvent(kind, local, QPointF(at), button, buttons, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(widget, event)


def test_clicking_the_indicator_talks_to_the_agent(qapp: Any) -> None:
    from PySide6.QtCore import QEvent, Qt

    from voice_agent.ui.status_indicator import StatusIndicator

    service = TappableService()
    indicator = StatusIndicator(service)
    at = indicator.mapToGlobal(indicator.rect().center())
    left, none = Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton

    _mouse(indicator, QEvent.Type.MouseButtonPress, left, left, at)
    _mouse(indicator, QEvent.Type.MouseButtonRelease, left, none, at)

    assert service.taps == 1


def test_right_click_opens_the_menu(qapp: Any) -> None:
    from PySide6.QtCore import QEvent, Qt

    from voice_agent.ui.status_indicator import StatusIndicator

    class Menu:
        shown_at: Any = None

        def popup(self, at: Any) -> None:
            self.shown_at = at

    service, menu = TappableService(), Menu()
    indicator = StatusIndicator(service, menu=menu)  # type: ignore[arg-type]
    at = indicator.mapToGlobal(indicator.rect().center())
    right = Qt.MouseButton.RightButton

    _mouse(indicator, QEvent.Type.MouseButtonPress, right, right, at)

    assert menu.shown_at == at
    assert service.taps == 0


def test_dragging_moves_the_indicator_remembers_it_and_does_not_click(qapp: Any) -> None:
    from PySide6.QtCore import QEvent, QPoint, Qt

    from voice_agent.ui.status_indicator import StatusIndicator

    moved: list[tuple[int, int]] = []
    service = TappableService()
    indicator = StatusIndicator(service, on_moved=moved.append)
    start = indicator.pos()
    grab = indicator.mapToGlobal(indicator.rect().center())
    left, none = Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton

    _mouse(indicator, QEvent.Type.MouseButtonPress, left, left, grab)
    _mouse(indicator, QEvent.Type.MouseMove, none, left, grab + QPoint(40, 30))
    _mouse(indicator, QEvent.Type.MouseButtonRelease, left, none, grab + QPoint(40, 30))

    assert indicator.pos() == start + QPoint(40, 30)
    assert moved == [(indicator.x(), indicator.y())]
    assert service.taps == 0


def test_the_indicator_returns_where_it_was_left_if_that_is_on_screen(qapp: Any) -> None:
    from PySide6.QtWidgets import QApplication

    from voice_agent.ui.status_indicator import StatusIndicator

    indicator = StatusIndicator(TappableService())
    area = QApplication.primaryScreen().availableGeometry()

    indicator.place((area.left() + 50, area.top() + 60))
    assert (indicator.x(), indicator.y()) == (area.left() + 50, area.top() + 60)

    indicator.place((area.right() + 5000, 0))  # a monitor that's gone: back to the default
    assert area.contains(indicator.geometry())
    assert indicator.y() > area.center().y()  # bottom right, clear of title-bar buttons


def test_every_state_colour_is_visible_on_the_pill() -> None:
    from voice_agent.ui import status
    from voice_agent.ui.status_indicator import PILL

    for colour in (status.GREEN, status.AMBER, status.BLUE, status.GREY, status.RED, status.INDIGO):
        assert _contrast(colour, PILL) >= 3.0, colour
