"""The activation state machine: IDLE -> LISTENING -> CAPTURING -> PROCESSING -> COOLDOWN.

IDLE is "not listening": either the pipeline is stopped (no microphone) or the user muted.
The two are tracked separately, so re-acquiring a microphone never undoes a user's mute.
COOLDOWN swallows the repeat triggers that wake-word models produce for one utterance
(invariant 9) and expires back to LISTENING. Thread-safe: the audio worker drives it while
the UI or kill switch may mute it at any moment (invariant 8); `on_mute` runs on every
mute/stop so a pending confirmation is cancelled at once.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from enum import StrEnum

from voice_agent.core.interfaces import Clock, SystemClock


class State(StrEnum):
    IDLE = "idle"
    LISTENING = "listening"
    CAPTURING = "capturing"
    PROCESSING = "processing"
    COOLDOWN = "cooldown"


class ActivationController:
    def __init__(
        self,
        cooldown_ms: int,
        clock: Clock | None = None,
        on_mute: Callable[[], None] | None = None,
    ) -> None:
        self._cooldown_ms = cooldown_ms
        self._clock: Clock = clock or SystemClock()
        #: Called on every mute/stop. Public so it can be wired after the agent is built.
        self.on_mute: Callable[[], None] = on_mute or (lambda: None)
        self._lock = threading.Lock()
        self._state = State.IDLE
        self._cooldown_until = 0.0
        self._running = False
        self._user_muted = False

    @property
    def muted(self) -> bool:
        """The user's mute (not the pipeline being stopped)."""
        return self._user_muted

    @property
    def state(self) -> State:
        with self._lock:
            return self._current()

    def start(self) -> None:
        """The pipeline has a microphone: listen, unless the user has muted."""
        with self._lock:
            self._running = True
        if not self._user_muted:
            self._move({State.IDLE}, State.LISTENING)

    def stop(self) -> None:
        """The pipeline lost (or released) the microphone. Does not touch the user's mute."""
        with self._lock:
            self._running = False
            self._state = State.IDLE
        self.on_mute()

    def mute(self) -> None:
        """The user's mute / kill switch: stop listening from any state, mid-command too."""
        with self._lock:
            self._user_muted = True
            self._state = State.IDLE
        self.on_mute()

    def unmute(self) -> None:
        with self._lock:
            self._user_muted = False
            if self._running and self._state is State.IDLE:
                self._state = State.LISTENING

    def wake(self) -> bool:
        """Wake word heard. False when not listening — e.g. cooling down or already awake."""
        return self._move({State.LISTENING}, State.CAPTURING)

    def begin_processing(self) -> bool:
        return self._move({State.CAPTURING}, State.PROCESSING)

    def finish(self) -> None:
        """Command done: cool down. A mute that landed mid-command is left in place."""
        with self._lock:
            if self._state is State.PROCESSING:
                self._state = State.COOLDOWN
                self._cooldown_until = self._clock.monotonic_ms() + self._cooldown_ms

    def _current(self) -> State:
        if self._state is State.COOLDOWN and self._clock.monotonic_ms() >= self._cooldown_until:
            self._state = State.LISTENING
        return self._state

    def _move(self, allowed: set[State], target: State) -> bool:
        with self._lock:
            if self._current() not in allowed:
                return False
            self._state = target
            return True
