"""Suppresses repeated executions of the same intent inside a short window.

Double wake-word triggers and repeated partial transcripts are normal, not exceptional
(invariant 9). The clock is injected so this is testable without sleeping.
"""

from __future__ import annotations

from voice_agent.core.constants import DEFAULT_DEDUPE_WINDOW_MS
from voice_agent.core.interfaces import Clock, SystemClock


class Deduplicator:
    def __init__(
        self, window_ms: int = DEFAULT_DEDUPE_WINDOW_MS, clock: Clock | None = None
    ) -> None:
        self._window = window_ms
        self._clock: Clock = clock or SystemClock()
        self._last: dict[str, float] = {}

    def is_duplicate(self, key: str) -> bool:
        """Check *and* record. Callers must not call this twice for one utterance."""
        now = self._clock.monotonic_ms()
        previous = self._last.get(key)
        self._last[key] = now
        return previous is not None and (now - previous) < self._window

    def reset(self) -> None:
        self._last.clear()
