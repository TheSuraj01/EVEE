"""Opening a website in a new tab: web.open.

The address has already passed the policy engine (safety/validation.py: a public https
address, nothing else). It opens in the browser you are using when that is the window in
front, so "open YouTube in another tab" lands next to the tab you were on; otherwise in the
default browser.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from pathlib import PureWindowsPath

from voice_agent.core.exceptions import ActionExecutionError
from voice_agent.core.models import ExecutionResult, ExecutionStatus, ResolvedCommand

WEB_OPEN = "web.open"
#: Browsers that open an address given on their command line in a new tab.
BROWSERS = frozenset(
    {"chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe", "vivaldi.exe"}
)


def browser_in_front(exe_path: str | None) -> str | None:
    """The foreground program's path if it is a known browser, else None."""
    if exe_path and PureWindowsPath(exe_path).name.casefold() in BROWSERS:
        return exe_path
    return None


def foreground_program() -> str | None:  # pragma: no cover - Win32
    """Full path of the program that owns the foreground window."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), ctypes.byref(pid))
    handle = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    try:
        size = wintypes.DWORD(1024)
        path = ctypes.create_unicode_buffer(size.value)
        ok = kernel32.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(size))
        return path.value if ok else None
    finally:
        kernel32.CloseHandle(handle)


def open_in_browser(address: str) -> None:  # pragma: no cover - opens a real browser
    """A new tab in the browser in front, else the default browser. The address starts with
    https://, so it can never be read as a browser switch."""
    if sys.platform != "win32":
        return
    from voice_agent.actions.apps import with_com

    with_com()
    browser = browser_in_front(foreground_program())
    if browser is not None:
        os.startfile(browser, arguments=address)  # noqa: S606 - vetted https address
    else:
        os.startfile(address)  # noqa: S606 - vetted https address: the default browser


class WebActions:
    """ActionHandler for web.open."""

    def __init__(self, open_address: Callable[[str], None]) -> None:
        self._open = open_address

    def execute(self, command: ResolvedCommand) -> ExecutionResult:
        address = command.text or ""
        if not address.startswith("https://"):  # the policy engine guarantees this
            msg = "refusing to open an address the policy engine did not vet"
            raise ActionExecutionError(msg)
        try:
            self._open(address)
        except OSError as exc:
            msg = f"could not open the browser: {exc.strerror or type(exc).__name__}"
            raise ActionExecutionError(msg) from exc
        return ExecutionResult(
            status=ExecutionStatus.SUCCESS, intent=WEB_OPEN, message="Opening it in a new tab."
        )
