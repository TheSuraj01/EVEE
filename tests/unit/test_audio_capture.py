"""Capture and worker over a fake stream: the PortAudio callback is called by hand."""

from __future__ import annotations

import threading
from typing import Any

import numpy as np
import pytest
from tests.conftest import FakeClock, FakeOpener

from voice_agent.audio.audio_worker import AudioWorker
from voice_agent.audio.capture import AudioCapture
from voice_agent.audio.ring_buffer import RingBuffer
from voice_agent.core.exceptions import AudioDeviceError, VoiceAgentError
from voice_agent.core.models import AudioDevice

MIC = AudioDevice(index=1, name="USB Mic", channels=1, default_samplerate=48000)


def _capture(opener: FakeOpener, clock: FakeClock, ring: RingBuffer | None = None) -> AudioCapture:
    return AudioCapture(ring or RingBuffer(4096), opener, 16000, 480, clock=clock, stall_ms=2000)


def _counter(start: int, n: int) -> np.ndarray:
    return np.arange(start, start + n, dtype=np.float32)


def test_start_opens_the_chosen_device_at_the_configured_rate() -> None:
    opener = FakeOpener()
    _capture(opener, FakeClock()).start(MIC)
    assert opener.args == (MIC, 16000, 480)
    assert opener.stream.active


def test_callback_copies_audio_into_the_ring() -> None:
    opener, ring = FakeOpener(), RingBuffer(4096)
    _capture(opener, FakeClock(), ring).start(MIC)
    opener.feed(_counter(0, 480))
    assert ring.read_since(0)[0].tolist() == list(range(480))


def test_muted_audio_never_enters_the_buffer_but_the_mic_stays_healthy() -> None:
    opener, ring, clock = FakeOpener(), RingBuffer(4096), FakeClock()
    capture = _capture(opener, clock, ring)
    capture.start(MIC)
    capture.mute()
    clock.advance(1500)
    opener.feed(_counter(0, 480))
    assert ring.written == 0
    clock.advance(1500)
    capture.check()  # callback still arriving: not an unplug
    capture.unmute()
    opener.feed(_counter(0, 480))
    assert ring.written == 480


def test_a_stopped_stream_is_reported_as_an_unplug() -> None:
    opener = FakeOpener()
    capture = _capture(opener, FakeClock())
    capture.start(MIC)
    opener.stream.active = False  # PortAudio gave up on the device
    with pytest.raises(AudioDeviceError, match="unplugged"):
        capture.check()


def test_silence_from_the_driver_is_reported_as_an_unplug() -> None:
    opener, clock = FakeOpener(), FakeClock()
    capture = _capture(opener, clock)
    capture.start(MIC)
    clock.advance(1900)
    capture.check()
    clock.advance(200)
    with pytest.raises(AudioDeviceError, match="unplugged"):
        capture.check()


def test_open_failure_names_the_device() -> None:
    capture = _capture(FakeOpener(error=OSError("Error opening InputStream")), FakeClock())
    with pytest.raises(AudioDeviceError, match="USB Mic"):
        capture.start(MIC)


def test_stop_closes_the_stream_and_silences_health_checks() -> None:
    opener, clock = FakeOpener(), FakeClock()
    capture = _capture(opener, clock)
    capture.start(MIC)
    capture.stop()
    assert opener.stream.closed
    clock.advance(10_000)
    capture.check()


def _worker(ring: RingBuffer, frames: list[np.ndarray], **kwargs: Any) -> AudioWorker:
    kwargs.setdefault("health", lambda: None)
    kwargs.setdefault("on_error", lambda exc: None)
    return AudioWorker(ring, 480, frames.append, **kwargs)


def test_worker_delivers_whole_frames_in_order_and_keeps_the_remainder() -> None:
    ring, frames = RingBuffer(4096), []
    worker = _worker(ring, frames)
    ring.write(_counter(0, 1000))
    assert worker.poll() == 2
    ring.write(_counter(1000, 440))
    assert worker.poll() == 1
    assert np.concatenate(frames).tolist() == list(range(1440))


def test_worker_ignores_audio_from_before_it_started() -> None:
    ring, frames = RingBuffer(4096), []
    ring.write(_counter(0, 480))
    worker = _worker(ring, frames)
    worker.start()
    worker.stop()
    assert worker.poll() == 0


def test_a_failing_frame_handler_does_not_stop_the_stream(
    caplog: pytest.LogCaptureFixture,
) -> None:
    ring, seen = RingBuffer(4096), []

    def flaky(frame: np.ndarray) -> None:
        seen.append(frame)
        if len(seen) == 1:
            raise RuntimeError("wake word model hiccup")

    worker = AudioWorker(ring, 480, flaky, health=lambda: None, on_error=lambda exc: None)
    ring.write(_counter(0, 960))
    assert worker.poll() == 2
    assert any("frame handler failed" in r.getMessage() for r in caplog.records)


def test_worker_thread_delivers_frames_and_stops_cleanly() -> None:
    ring, got = RingBuffer(4096), threading.Event()
    worker = AudioWorker(
        ring, 480, lambda frame: got.set(), health=lambda: None, on_error=lambda exc: None
    )
    worker.start()
    ring.write(_counter(0, 480))
    assert got.wait(1)
    worker.stop()
    assert not worker.running


def test_device_error_reaches_on_error_and_ends_the_thread() -> None:
    errors: list[VoiceAgentError] = []
    reported = threading.Event()

    def dead_mic() -> None:
        raise AudioDeviceError("unplugged")

    def on_error(exc: VoiceAgentError) -> None:
        errors.append(exc)
        reported.set()

    worker = AudioWorker(RingBuffer(64), 480, lambda f: None, health=dead_mic, on_error=on_error)
    worker.start()
    assert reported.wait(1)
    worker.stop()
    assert not worker.running
    assert isinstance(errors[0], AudioDeviceError)


def test_a_stream_that_fails_to_start_is_closed() -> None:
    opener = FakeOpener()

    def broken_start() -> None:
        raise OSError("device busy")

    opener.stream.start = broken_start  # type: ignore[method-assign]
    with pytest.raises(AudioDeviceError, match="USB Mic"):
        _capture(opener, FakeClock()).start(MIC)
    assert opener.stream.closed
