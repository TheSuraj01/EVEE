"""Fixed-capacity float32 ring buffer between the audio callback and the audio worker.

One writer (the PortAudio callback, which must never block) and one reader. No lock:
positions are absolute sample counts, and int assignment is atomic under the GIL. The
writer announces the range it is about to overwrite (`_reserved`) before copying and
publishes it (`_written`) after; the reader drops anything the writer may have touched
while it was copying. Overflow drops the oldest samples.
"""

from __future__ import annotations

import numpy as np


class RingBuffer:
    def __init__(self, capacity: int) -> None:
        if capacity <= 0:
            msg = "capacity must be positive"
            raise ValueError(msg)
        self._buf = np.zeros(capacity, dtype=np.float32)
        self._capacity = capacity
        self._reserved = 0
        self._written = 0

    @property
    def written(self) -> int:
        """Total samples ever written: the absolute position of the next sample."""
        return self._written

    def write(self, samples: np.ndarray) -> None:
        """Copy mono samples in. Called from the audio callback: no allocation beyond a cast."""
        data = np.asarray(samples, dtype=np.float32)
        if data.ndim != 1:
            msg = f"expected mono samples, got shape {data.shape}"
            raise ValueError(msg)
        total = data.size
        if total == 0:
            return
        kept = data[-self._capacity :]
        start = (self._written + total - kept.size) % self._capacity
        first = min(kept.size, self._capacity - start)

        self._reserved = self._written + total
        self._buf[start : start + first] = kept[:first]
        self._buf[: kept.size - first] = kept[first:]
        self._written = self._reserved

    def clear(self) -> None:
        """Zero every sample (privacy, on mute). Positions are kept, so a reader stays
        consistent; at worst it reads silence."""
        self._buf.fill(0.0)

    def read_since(self, position: int) -> tuple[np.ndarray, int]:
        """Samples after absolute `position`, oldest first, and the position to read from
        next. Samples older than one capacity are gone; the result skips them."""
        end = self._written
        start = max(position, end - self._capacity)
        if start >= end:
            return np.empty(0, dtype=np.float32), end

        begin = start % self._capacity
        count = end - start
        first = min(count, self._capacity - begin)
        data = np.concatenate((self._buf[begin : begin + first], self._buf[: count - first]))

        # Anything at an absolute index below reserved - capacity may have been overwritten
        # while we copied; drop it rather than return a torn sample.
        overrun = self._reserved - self._capacity - start
        if overrun > 0:
            data = data[overrun:]
        return data, end
