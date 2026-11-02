"""The audio worker thread: moves whole frames from the ring buffer to the voice loop.

It owns the only blocking work on the audio path, so neither the PortAudio callback nor
the main loop ever waits on wake-word, VAD or speech-to-text processing.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

import numpy as np

from voice_agent.audio.ring_buffer import RingBuffer
from voice_agent.core.exceptions import VoiceAgentError

log = logging.getLogger(__name__)


class AudioWorker:
    def __init__(
        self,
        buffer: RingBuffer,
        frame_samples: int,
        on_frame: Callable[[np.ndarray], None],
        health: Callable[[], None],
        on_error: Callable[[VoiceAgentError], None],
        poll_s: float = 0.01,
    ) -> None:
        self._buffer = buffer
        self._frame = frame_samples
        self._on_frame = on_frame
        self._health = health
        self._on_error = on_error
        self._poll_s = poll_s
        self._pos = buffer.written
        self._pending = np.empty(0, dtype=np.float32)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def poll(self) -> int:
        """One pass: check the mic, then deliver every whole frame available. Returns the
        number of frames delivered."""
        self._health()
        data, self._pos = self._buffer.read_since(self._pos)
        if data.size:
            self._pending = np.concatenate((self._pending, data))
        count = self._pending.size // self._frame
        for i in range(count):
            try:
                self._on_frame(self._pending[i * self._frame : (i + 1) * self._frame])
            except Exception:
                # Thread boundary: one bad frame must not silence the agent. Logged, not hidden.
                log.exception("frame handler failed")
        self._pending = self._pending[count * self._frame :]
        return count

    def start(self) -> None:
        """Start from 'now': audio buffered before start is stale and is skipped."""
        if self.running:
            return
        self._pos = self._buffer.written
        self._pending = np.empty(0, dtype=np.float32)
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="audio-worker", daemon=True)
        self._thread.start()

    def stop(self, timeout_s: float = 2.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout_s)

    def join(self, timeout_s: float) -> None:
        if self._thread is not None:
            self._thread.join(timeout_s)

    def _run(self) -> None:
        while not self._stop.wait(self._poll_s):
            try:
                self.poll()
            except VoiceAgentError as exc:
                self._on_error(exc)
                return
