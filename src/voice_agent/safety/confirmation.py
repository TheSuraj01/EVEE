"""Confirmation for commands the policy engine marks CONFIRM (risk >= HIGH).

Silence is never consent: a timeout, a crashed prompt, a cancel, or any answer other than
a literal True is a denial.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from collections.abc import Callable
from typing import TextIO

from voice_agent.core.interfaces import ConfirmationPrompt, WindowBackend
from voice_agent.core.models import CommandSpec

log = logging.getLogger(__name__)


class ConfirmationBroker:
    """The prompt the Dispatcher holds. Wraps a console or dialog prompt, enforces the
    deadline even if that prompt hangs, and can be cancelled by the emergency stop.

    Answering a prompt moves focus to the prompt. With `windows` given, the broker records
    the focused window first and hands focus back after a yes, so the confirmed action
    hits the window the user meant — or is denied if that window can't be refocused."""

    def __init__(
        self,
        prompt: ConfirmationPrompt,
        windows: WindowBackend | None = None,
        grace_s: float = 0.5,
    ) -> None:
        self._prompt = prompt
        self._windows = windows
        #: A well-behaved prompt times out on its own; the grace lets it finish cleanly
        #: (e.g. print its newline) before the hard deadline cuts off a hung one.
        self._grace_s = grace_s
        self._lock = threading.Lock()
        self._pending: threading.Event | None = None
        self._cancelled = False

    def confirm(self, spec: CommandSpec, timeout_s: float) -> bool:
        """Ask on a helper thread; deny on timeout, error or cancel."""
        # Per-call event and answer: a prompt abandoned by an earlier timeout can never
        # answer, or wake, a later confirmation.
        target = self._windows.foreground() if self._windows is not None else 0
        done = threading.Event()
        answer: list[bool] = []
        with self._lock:
            self._pending, self._cancelled = done, False

        def ask() -> None:
            try:
                answer.append(self._prompt.confirm(spec, timeout_s) is True)
            except Exception:
                log.exception("confirmation prompt failed for %s", spec.intent)
            finally:
                done.set()

        threading.Thread(target=ask, name="confirmation", daemon=True).start()
        answered = done.wait(timeout_s + self._grace_s)
        with self._lock:
            cancelled, self._pending = self._cancelled, None
        if not (answered and not cancelled and answer == [True]):
            return False
        if self._windows is not None and not self._windows.activate(target):
            log.warning("confirmed %s, but its target window could not be refocused", spec.intent)
            return False
        return True

    def cancel(self) -> None:
        """Emergency stop (invariant 8): resolve any pending confirmation as a denial."""
        with self._lock:
            self._cancelled = True
            if self._pending is not None:
                self._pending.set()
        # A prompt that can take its question off screen (the tray dialog) is told to.
        dismiss = getattr(self._prompt, "dismiss", None)
        if callable(dismiss):
            dismiss()


def _read_key(
    timeout_s: float, stop: threading.Event | None = None
) -> str | None:  # pragma: no cover - needs a real console
    """One keypress, or None at the deadline or once `stop` is set (the question was
    cancelled). Never leaves a blocked read behind."""
    if stop is not None and stop.is_set():
        return None
    deadline = time.monotonic() + timeout_s
    if sys.platform == "win32":
        import msvcrt

        while time.monotonic() < deadline and not (stop is not None and stop.is_set()):
            if msvcrt.kbhit():
                return msvcrt.getwch()
            time.sleep(0.05)
        return None

    import select

    ready, _, _ = select.select([sys.stdin], [], [], timeout_s)
    return sys.stdin.readline() if ready else None


class ConsolePrompt:
    """CLI-mode prompt: 'y' confirms; any other key, or nothing, declines."""

    def __init__(
        self,
        read_key: Callable[[float], str | None] | None = None,
        out: TextIO | None = None,
    ) -> None:
        self._dismissed = threading.Event()
        self._read_key = read_key or (lambda timeout_s: _read_key(timeout_s, self._dismissed))
        self._out = out

    def confirm(self, spec: CommandSpec, timeout_s: float) -> bool:
        self._dismissed.clear()
        out = self._out or sys.stdout
        out.write(f"  Confirm: {spec.description or spec.intent}? [y/N] ({timeout_s:.0f}s) ")
        out.flush()
        key = self._read_key(timeout_s)
        out.write(f"{key.strip()}\n" if key else "(timed out)\n")
        return key is not None and key.strip().lower() == "y"

    def dismiss(self) -> None:
        """The question was cancelled (mute, emergency stop): stop reading keys at once, so
        the next key press goes to whatever asks next, not to a dead question."""
        self._dismissed.set()
