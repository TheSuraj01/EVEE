"""Window capability: the executor for minimise/maximise/close and its pywin32 backend.

The window-management plugin uses these through its (permission-guarded) PluginHost; its
switch/snap shortcuts live in the plugin itself.
"""

from __future__ import annotations

import os
from collections.abc import Callable

from voice_agent.core.exceptions import ActionExecutionError
from voice_agent.core.interfaces import WindowBackend
from voice_agent.core.models import ExecutionResult, ExecutionStatus, ResolvedCommand

#: Desktop and taskbar. WM_CLOSE to Progman opens the shutdown dialog — never a target.
_SHELL_CLASSES = frozenset({"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"})


def is_shell_window(class_name: str) -> bool:
    """True for windows that belong to the Windows shell rather than an application."""
    return class_name in _SHELL_CLASSES


class WindowActions:
    """ActionHandler for the backend-driven window intents."""

    def __init__(self, backend: WindowBackend) -> None:
        self._ops: dict[str, Callable[[], None]] = {
            "window.minimize": backend.minimize,
            "window.maximize": backend.maximize,
            "window.close": backend.close,
        }

    @property
    def intents(self) -> tuple[str, ...]:
        return tuple(self._ops)

    def execute(self, command: ResolvedCommand) -> ExecutionResult:
        """Run the backend op; an intent missing from the table is a wiring bug."""
        intent = command.spec.intent
        op = self._ops.get(intent)
        if op is None:
            msg = f"no window operation bound to {intent}"
            raise ActionExecutionError(msg)
        op()
        return ExecutionResult(
            status=ExecutionStatus.SUCCESS,
            intent=intent,
            message=command.spec.description or intent,
        )


class Win32WindowBackend:  # pragma: no cover - needs a real Windows desktop
    """Production backend. pywin32 is imported here, never at module import."""

    def __init__(self) -> None:
        import pywintypes
        import win32con
        import win32console
        import win32gui
        import win32process

        self._con = win32con
        self._gui = win32gui
        self._error = pywintypes.error
        self._console = win32console
        self._process = win32process

    def foreground(self) -> int:
        return int(self._gui.GetForegroundWindow())

    def activate(self, handle: int) -> bool:
        # Allowed here because the prompt that just took focus belongs to this process.
        if not handle or not self._gui.IsWindow(handle):
            return False
        try:
            self._gui.SetForegroundWindow(handle)
        except self._error:
            return False
        return self.foreground() == handle

    def _target(self) -> int:
        """The foreground window, unless it is the shell or this agent's own window."""
        hwnd = self.foreground()
        if (
            not hwnd
            or is_shell_window(self._gui.GetClassName(hwnd))
            or hwnd == self._console.GetConsoleWindow()
            or self._process.GetWindowThreadProcessId(hwnd)[1] == os.getpid()
        ):
            msg = "no application window is focused"
            raise ActionExecutionError(msg)
        return hwnd

    def minimize(self) -> None:
        self._gui.ShowWindow(self._target(), self._con.SW_MINIMIZE)

    def maximize(self) -> None:
        self._gui.ShowWindow(self._target(), self._con.SW_MAXIMIZE)

    def close(self) -> None:
        # WM_CLOSE asks politely: the app may prompt to save. Never TerminateProcess.
        self._gui.PostMessage(self._target(), self._con.WM_CLOSE, 0, 0)
