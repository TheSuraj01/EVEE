"""Ring buffer: overflow drops oldest; reads come back in order, with no torn samples.

Samples are their own absolute index, so ordering and loss are checkable exactly.
"""

from __future__ import annotations

import random
import threading

import numpy as np
import pytest

from voice_agent.audio.ring_buffer import RingBuffer


def _counter(start: int, n: int) -> np.ndarray:
    return np.arange(start, start + n, dtype=np.float32)


def test_reads_what_was_written_in_order() -> None:
    ring = RingBuffer(8)
    ring.write(_counter(0, 5))
    data, pos = ring.read_since(0)
    assert data.tolist() == [0, 1, 2, 3, 4]
    assert pos == 5
    data, pos = ring.read_since(pos)
    assert data.size == 0


def test_overflow_drops_the_oldest() -> None:
    ring = RingBuffer(4)
    ring.write(_counter(0, 3))
    ring.write(_counter(3, 3))
    data, pos = ring.read_since(0)
    assert data.tolist() == [2, 3, 4, 5]
    assert pos == 6


def test_a_single_write_larger_than_capacity_keeps_the_tail() -> None:
    ring = RingBuffer(4)
    ring.write(_counter(0, 10))
    assert ring.read_since(0)[0].tolist() == [6, 7, 8, 9]


def test_multichannel_input_is_rejected_by_shape_not_silently_mixed() -> None:
    with pytest.raises(ValueError, match="mono"):
        RingBuffer(4).write(np.zeros((2, 2), dtype=np.float32))


def test_ordering_property_over_random_write_and_read_sizes() -> None:
    rng = random.Random(1234)
    for _ in range(200):
        capacity = rng.randint(1, 64)
        ring, written, pos = RingBuffer(capacity), 0, 0
        for _ in range(rng.randint(1, 40)):
            n = rng.randint(0, capacity * 2)
            ring.write(_counter(written, n))
            written += n
            if rng.random() < 0.5:
                data, new_pos = ring.read_since(pos)
                oldest_kept = max(pos, written - capacity)
                assert data.tolist() == list(range(oldest_kept, written))
                assert new_pos == written
                pos = new_pos


def test_concurrent_reader_never_sees_torn_or_reordered_samples() -> None:
    ring = RingBuffer(256)
    done = threading.Event()

    def produce() -> None:
        written = 0
        for _ in range(4000):
            ring.write(_counter(written, 37))
            written += 37
        done.set()

    writer = threading.Thread(target=produce)
    writer.start()
    pos, last = 0, -1.0
    while not done.is_set() or pos < ring.written:
        data, pos = ring.read_since(pos)
        if data.size:
            assert data[0] > last
            assert np.all(np.diff(data) == 1), "torn or reordered read"
            last = float(data[-1])
    writer.join()
    assert last == 4000 * 37 - 1
