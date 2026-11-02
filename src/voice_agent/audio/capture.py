"""Microphone capture.

The PortAudio callback only stamps "audio arrived" and copies samples into the ring
buffer, so wake-word, VAD and speech-to-text work can never block it. Audio stays in
memory; nothing here writes to disk (invariant 5).
"""

from __future__ import annotations

import contextlib
import logging
from typing import Any

import numpy as np

from voice_agent.audio.ring_buffer import RingBuffer
from voice_agent.core.exceptions import AudioDeviceError
from voice_agent.core.interfaces import AudioStream, Clock, StreamOpener, SystemClock
from voice_agent.core.models import AudioDevice

log = logging.getLogger(__name__)

UNPLUGGED = "the microphone stopped delivering audio — was it unplugged?"


class AudioCapture:
    def __init__(
        self,
        buffer: RingBuffer,
        open_stream: StreamOpener,
        sample_rate: int,
        block_samples: int,
        clock: Clock | None = None,
        stall_ms: float = 2000,
    ) -> None:
        self._buffer = buffer
        self._open = open_stream
        self._rate = sample_rate
        self._block = block_samples
        self._clock: Clock = clock or SystemClock()
        self._stall_ms = stall_ms
        self._stream: AudioStream | None = None
        self._muted = False
        self._last_audio_ms = 0.0

    @property
    def muted(self) -> bool:
        return self._muted

    def mute(self) -> None:
        """Stop audio at the source: muted samples never reach the buffer."""
        self._muted = True

    def unmute(self) -> None:
        self._muted = False

    def start(self, device: AudioDevice) -> None:
        """Open and start the stream; a busy or missing device is an AudioDeviceError."""
        self.stop()
        msg = f"could not open '{device.name}'"
        try:
            stream = self._open(device, self._rate, self._block, self._on_audio)
        except Exception as exc:  # PortAudio raises its own types; translate at the edge
            raise AudioDeviceError(f"{msg}: {exc}") from exc
        try:
            self._last_audio_ms = self._clock.monotonic_ms()
            stream.start()
        except Exception as exc:
            # Close it now: a re-scan (PortAudio re-init) with it still alive can crash.
            with contextlib.suppress(Exception):
                stream.close()
            raise AudioDeviceError(f"{msg}: {exc}") from exc
        self._stream = stream

    def stop(self) -> None:
        stream, self._stream = self._stream, None
        if stream is None:
            return
        try:
            stream.stop()
            stream.close()
        except Exception:  # an unplugged device can fail to close; nothing left to do
            log.warning("error while closing the input stream", exc_info=True)

    def check(self) -> None:
        """Raise AudioDeviceError if the stream died or the driver went silent."""
        stream = self._stream
        if stream is None:
            return
        silent_ms = self._clock.monotonic_ms() - self._last_audio_ms
        if not stream.active or silent_ms > self._stall_ms:
            raise AudioDeviceError(UNPLUGGED)

    def _on_audio(self, indata: np.ndarray, *_: Any) -> None:
        # PortAudio's thread (indata, frames, time, status): no logging, no locks, no
        # allocation beyond the copy.
        self._last_audio_ms = self._clock.monotonic_ms()
        if not self._muted:
            self._buffer.write(indata[:, 0])


def open_sounddevice_stream(  # pragma: no cover - needs real audio hardware
    device: AudioDevice, sample_rate: int, block_samples: int, callback: Any
) -> AudioStream:
    """Production StreamOpener: mono float32 at the pipeline's rate (MME resamples)."""
    import sounddevice

    stream: AudioStream = sounddevice.InputStream(
        device=device.index,
        samplerate=sample_rate,
        channels=1,
        dtype="float32",
        blocksize=block_samples,
        callback=callback,
    )
    return stream
