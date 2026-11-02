"""The emergency-stop hotkey: system-wide, whatever window has focus (invariant 8).

Win32 RegisterHotKey via stdlib ctypes, on a dedicated thread: WM_HOTKEY is posted to the
thread that registered, so that thread runs a message loop. Works in tray and console mode.
"""

from __future__ import annotations

import ctypes
import logging
import sys
import threading
from collections.abc import Callable

from voice_agent.core.exceptions import ConfigError

log = logging.getLogger(__name__)

_MODIFIERS = {"alt": 0x1, "ctrl": 0x2, "control": 0x2, "shift": 0x4, "win": 0x8}
#: A hotkey without one of these would swallow ordinary typing (e.g. shift+a = "A").
_REQUIRED = 0x1 | 0x2 | 0x8
_NAMED_KEYS = {
    "space": 0x20,
    "esc": 0x1B,
    "escape": 0x1B,
    "pause": 0x13,
    "enter": 0x0D,
    "tab": 0x09,
    "backspace": 0x08,
    "insert": 0x2D,
    "delete": 0x2E,
    "home": 0x24,
    "end": 0x23,
    **{f"f{n}": 0x6F + n for n in range(1, 25)},
}
MOD_NOREPEAT = 0x4000
WM_HOTKEY, WM_QUIT = 0x0312, 0x0012
HOTKEY_ID = 0xE57


def parse_hotkey(spec: str) -> tuple[int, int]:
    """'ctrl+alt+space' -> (Win32 modifier flags, virtual-key code)."""
    parts = [part.strip().lower() for part in spec.split("+")]
    if len(parts) < 2 or not all(parts):
        msg = f"hotkey {spec!r}: expected modifiers + key, e.g. 'ctrl+alt+space'"
        raise ConfigError(msg)
    *mods, key = parts
    unknown = [m for m in mods if m not in _MODIFIERS]
    if unknown:
        msg = f"hotkey {spec!r}: unknown modifier {unknown[0]!r}"
        raise ConfigError(msg)
    modifiers = 0
    for mod in mods:
        modifiers |= _MODIFIERS[mod]
    if not modifiers & _REQUIRED:
        msg = f"hotkey {spec!r}: needs ctrl, alt or win, or it would capture normal typing"
        raise ConfigError(msg)
    if key in _NAMED_KEYS:
        return modifiers, _NAMED_KEYS[key]
    if len(key) == 1 and key.isascii() and key.isalnum():
        return modifiers, ord(key.upper())
    msg = f"hotkey {spec!r}: unknown key {key!r}"
    raise ConfigError(msg)


class GlobalHotkey:
    def __init__(self, spec: str, on_press: Callable[[], None]) -> None:
        self._modifiers, self._vk = parse_hotkey(spec)  # bad config fails before any thread
        self.spec = spec
        self._on_press = on_press
        self._ready = threading.Event()
        self._registered = False
        self._thread_id = 0
        self._thread: threading.Thread | None = None

    def start(self) -> bool:  # pragma: no cover - registers with the real desktop
        """Register. False when another program already owns the combination."""
        self._thread = threading.Thread(target=self._run, name="hotkey", daemon=True)
        self._thread.start()
        self._ready.wait(2)
        return self._registered

    def stop(self) -> None:  # pragma: no cover - real desktop
        if sys.platform == "win32" and self._thread_id:
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        if self._thread is not None:
            self._thread.join(2)

    def _run(self) -> None:  # pragma: no cover - real desktop
        if sys.platform != "win32":
            self._ready.set()
            return
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        self._thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
        mods = self._modifiers | MOD_NOREPEAT
        self._registered = bool(user32.RegisterHotKey(None, HOTKEY_ID, mods, self._vk))
        self._ready.set()
        if not self._registered:
            return
        message = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                if message.message == WM_HOTKEY:
                    try:
                        self._on_press()
                    except Exception:  # thread boundary: the hotkey must keep working
                        log.exception("emergency stop handler failed")
        finally:
            user32.UnregisterHotKey(None, HOTKEY_ID)


#: Tried in order when the configured key is taken or invalid: on every keyboard, and not
#: used by Windows itself.
FALLBACK_HOTKEYS = ("ctrl+alt+backspace", "ctrl+shift+alt+space")


def start_emergency_hotkey(
    spec: str,
    on_press: Callable[[], None],
    make: Callable[[str, Callable[[], None]], GlobalHotkey] = GlobalHotkey,
) -> tuple[GlobalHotkey | None, str | None]:
    """The configured key, else the first free fallback. Returns the running hotkey (None if
    every candidate is taken) and a notice for the user when it isn't the configured key.
    The kill switch never takes the app down with it: the tray's Emergency stop remains."""
    for candidate in (spec, *(key for key in FALLBACK_HOTKEYS if key != spec)):
        try:
            hotkey = make(candidate, on_press)
        except ConfigError:
            log.error("emergency stop hotkey %r is invalid", candidate)
            continue
        if hotkey.start():
            if candidate == spec:
                return hotkey, None
            return hotkey, f"Emergency stop is {candidate}: {spec} is taken or invalid."
        log.warning("emergency stop hotkey %r is taken by another program", candidate)
    taken = ", ".join((spec, *FALLBACK_HOTKEYS))
    return None, f"No emergency-stop hotkey is free ({taken} are taken)."
