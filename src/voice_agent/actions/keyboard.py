"""Keyboard-driven actions.

Every key press goes through an injected `KeySender`, so tests record chords instead of
typing into the developer's desktop. Key names are pyautogui's.

Known failure mode: Windows (UIPI) silently drops synthetic input aimed at an elevated
window. That is expected and listed in docs/plan/MANUAL_CHECKS.md.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from voice_agent.core.exceptions import ActionExecutionError
from voice_agent.core.interfaces import KeySender
from voice_agent.core.models import ExecutionResult, ExecutionStatus, ResolvedCommand

#: Defence in depth: a runaway argument must never turn into minutes of key presses.
MAX_PRESSES = 120


@dataclass(frozen=True)
class Shortcut:
    keys: tuple[str, ...]
    repeat: int = 1
    #: Duration commands: one press covers this many seconds, so presses = ceil(arg / n).
    seconds_per_press: int | None = None

    def presses(self, argument: int | None) -> int:
        """How many times to send the chord, given the policy-approved argument."""
        if self.seconds_per_press is None or argument is None:
            return self.repeat
        return min(MAX_PRESSES, max(1, math.ceil(argument / self.seconds_per_press)))


UI_SHORTCUTS: Mapping[str, Shortcut] = {
    "ui.exit_fullscreen": Shortcut(("esc",)),
    "ui.enter_fullscreen": Shortcut(("f11",)),
}


class KeyboardActions:
    """ActionHandler serving every intent in its shortcut table."""

    def __init__(
        self,
        keys: KeySender,
        shortcuts: Mapping[str, Shortcut] = UI_SHORTCUTS,
        interrupted: Callable[[], bool] = lambda: False,
    ) -> None:
        self._keys = keys
        self._shortcuts = shortcuts
        #: Checked before every press: the emergency stop can cut a long seek short.
        self._interrupted = interrupted

    def execute(self, command: ResolvedCommand) -> ExecutionResult:
        """Send the chord; an intent missing from the table is a wiring bug, not a guess."""
        intent = command.spec.intent
        shortcut = self._shortcuts.get(intent)
        if shortcut is None:
            msg = f"no shortcut bound to {intent}"
            raise ActionExecutionError(msg)

        presses = shortcut.presses(command.argument)
        for done in range(presses):
            if self._interrupted():
                return ExecutionResult(
                    status=ExecutionStatus.DENIED,
                    intent=intent,
                    message="Stopped.",
                    detail={"reason": "interrupted", "presses": done},
                )
            self._keys.hotkey(*shortcut.keys)
        return ExecutionResult(
            status=ExecutionStatus.SUCCESS,
            intent=intent,
            message=command.spec.description or intent,
            detail={"keys": "+".join(shortcut.keys), "presses": presses},
        )


class PyAutoGuiKeySender:  # pragma: no cover - needs a real desktop session
    """The production KeySender. pyautogui is imported here, never at module import."""

    def __init__(self) -> None:
        import pyautogui

        # ponytail: global pause; per-call intervals if some app drops fast repeats.
        pyautogui.PAUSE = 0.02
        self._gui = pyautogui

    def press(self, *keys: str) -> None:
        for key in keys:
            self._gui.press(key)

    def hotkey(self, *keys: str) -> None:
        self._gui.hotkey(*keys)
