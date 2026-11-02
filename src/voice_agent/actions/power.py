"""Putting the PC to sleep: system.sleep (high risk, so it is always confirmed first).

PCs with classic sleep (S3) are suspended with SetSuspendState. "Modern Standby" PCs, most
recent laptops, refuse that call, so there the agent uses Windows' own Sleep command, the
way you would by keyboard: Win+X, then "Shut down or sign out" (U), then "Sleep" (S). Those
letters are the English menu's: on another display language the agent refuses rather than
risk picking Shut down or Restart.
"""

from __future__ import annotations

import sys
from collections.abc import Callable

from voice_agent.core.interfaces import KeySender
from voice_agent.core.models import ExecutionResult, ExecutionStatus, ResolvedCommand

SYSTEM_SLEEP = "system.sleep"
#: How long the Win+X menu and its submenu take to open before the next key is pressed.
MENU_OPEN_S = 0.6
SUBMENU_OPEN_S = 0.4


def english_ui() -> bool:  # pragma: no cover - Win32
    """Is Windows' display language English, so the Win+X menu letters are U and S?"""
    if sys.platform != "win32":
        return False
    import ctypes

    return bool(ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF == 0x09)


def suspend() -> bool:  # pragma: no cover - would put this PC to sleep
    """Classic sleep, where the hardware has it. False when it doesn't (Modern Standby) or
    the call fails: the caller then uses the Sleep menu."""
    if sys.platform != "win32":
        return False
    import ctypes

    powrprof = ctypes.windll.powrprof
    if not powrprof.IsPwrSuspendAllowed():
        return False
    return bool(powrprof.SetSuspendState(False, False, False))


class PowerActions:
    """ActionHandler for system.sleep."""

    def __init__(
        self,
        keys: KeySender,
        suspend: Callable[[], bool],
        pause: Callable[[float], None],
        interrupted: Callable[[], bool] = lambda: False,
        english: Callable[[], bool] = lambda: True,
    ) -> None:
        self._keys = keys
        self._suspend = suspend
        self._pause = pause
        self._english = english
        #: The emergency stop can still cancel between the menu steps.
        self._interrupted = interrupted

    def execute(self, command: ResolvedCommand) -> ExecutionResult:  # noqa: ARG002
        if not self._suspend():
            if not self._english():
                return ExecutionResult(
                    status=ExecutionStatus.ERROR,
                    intent=SYSTEM_SLEEP,
                    message="Sleep by voice needs English Windows on this PC; use Start → Power.",
                    detail={"reason": "unsupported"},
                )
            steps = ((("win", "x"), MENU_OPEN_S), (("u",), SUBMENU_OPEN_S), (("s",), 0.0))
            for n, (step, wait) in enumerate(steps):
                if self._interrupted():
                    for _ in range(n):  # close the menu (and submenu) already open
                        self._keys.press("esc")
                    return ExecutionResult(
                        status=ExecutionStatus.DENIED,
                        intent=SYSTEM_SLEEP,
                        message="Cancelled — listening was stopped.",
                        detail={"reason": "muted"},
                    )
                if len(step) > 1:
                    self._keys.hotkey(*step)
                else:
                    self._keys.press(*step)
                self._pause(wait)
        return ExecutionResult(
            status=ExecutionStatus.SUCCESS, intent=SYSTEM_SLEEP, message="Going to sleep."
        )
