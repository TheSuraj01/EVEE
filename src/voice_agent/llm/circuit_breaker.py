"""Rate limit + circuit breaker around the cloud intent provider.

- At most `max_per_minute` calls in any rolling 60 s; beyond that, fail fast.
- After `failures_to_open` consecutive failures the circuit opens and calls fail fast for
  `open_ms`, so a dead network costs nothing per utterance instead of a timeout and a retry.
- Then exactly one probe call is let through (half-open): success closes the circuit,
  failure re-opens it at once.

Failing fast raises LLMUnavailableError, which the router turns into no_action (inv. 4).
"""

from __future__ import annotations

import threading
from collections import deque

from voice_agent.core.exceptions import LLMUnavailableError
from voice_agent.core.interfaces import Clock, IntentProvider, SystemClock
from voice_agent.core.models import IntentProposal

WINDOW_MS = 60_000


class GuardedIntentProvider:
    """An IntentProvider that wraps another one with a rate limit and a circuit breaker."""

    def __init__(
        self,
        provider: IntentProvider,
        max_per_minute: int,
        failures_to_open: int,
        open_ms: float = 30_000,
        clock: Clock | None = None,
    ) -> None:
        self._provider = provider
        self._max_per_minute = max_per_minute
        self._failures_to_open = failures_to_open
        self._open_ms = open_ms
        self._clock: Clock = clock or SystemClock()
        self._lock = threading.Lock()
        self._calls: deque[float] = deque()
        self._failures = 0
        self._opened_at: float | None = None
        self._probing = False

    def propose(self, transcript: str, context: dict[str, str]) -> IntentProposal | None:
        self._admit()
        succeeded = False
        try:
            proposal = self._provider.propose(transcript, context)
            succeeded = True
            return proposal
        finally:
            # Any exception counts, so a crashing probe can't leave the circuit half-open.
            self._record(succeeded)

    def _admit(self) -> None:
        with self._lock:
            now = self._clock.monotonic_ms()
            half_open = False
            if self._opened_at is not None:
                if self._probing or now - self._opened_at < self._open_ms:
                    msg = "cloud circuit open after repeated failures"
                    raise LLMUnavailableError(msg)
                half_open = True

            while self._calls and now - self._calls[0] >= WINDOW_MS:
                self._calls.popleft()
            if len(self._calls) >= self._max_per_minute:
                msg = f"cloud rate limit reached ({self._max_per_minute}/min)"
                raise LLMUnavailableError(msg)

            self._calls.append(now)
            self._probing = half_open

    def _record(self, succeeded: bool) -> None:
        with self._lock:
            if succeeded:
                self._failures, self._opened_at, self._probing = 0, None, False
                return
            self._failures += 1
            if self._probing or self._failures >= self._failures_to_open:
                self._opened_at = self._clock.monotonic_ms()
            self._probing = False
