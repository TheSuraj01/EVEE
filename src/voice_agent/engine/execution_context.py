"""Which command is executing right now, and on which thread.

The dispatcher opens the context around every executor call. Plugin capabilities check it
on every use, so they are live only inside one of their own commands, on the dispatching
thread, and never once the user has stopped — not while loading, not from a background
thread, not after an emergency stop.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from voice_agent.core.models import CommandSpec


def _never() -> bool:
    return False


class ExecutionContext:
    def __init__(self, interrupted: Callable[[], bool] = _never) -> None:
        #: True once the command being executed should stop (mute, emergency stop).
        self.interrupted = interrupted
        self._lock = threading.Lock()
        self._spec: CommandSpec | None = None
        self._thread: int | None = None

    @contextmanager
    def running(self, spec: CommandSpec) -> Iterator[None]:
        with self._lock:
            self._spec, self._thread = spec, threading.get_ident()
        try:
            yield
        finally:
            with self._lock:
                self._spec, self._thread = None, None

    def current(self) -> CommandSpec | None:
        """The executing command — visible only to the thread executing it."""
        with self._lock:
            return self._spec if self._thread == threading.get_ident() else None
