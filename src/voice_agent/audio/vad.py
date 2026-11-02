"""Voice activity gate: turns the frames after a wake word into one utterance.

The utterance ends once `silence_timeout_ms` of non-speech follows speech, or at `max_ms`
no matter what. If nothing was said it is empty: Whisper hallucinates text ("Thank you.")
on silence, so silence must never reach it.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable

import numpy as np

SpeechDetector = Callable[[np.ndarray], bool]

#: Kept from before the first speech frame, so soft onsets ("skip", "f...") survive.
PRE_ROLL_MS = 300


def _rms(frame: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(frame)))) if frame.size else 0.0


def energy_detector(floor: float) -> SpeechDetector:
    """Speech = RMS at or above `floor`. Cheap, and the first stage of the real detector."""
    return lambda frame: _rms(frame) >= floor


def webrtc_detector(sample_rate: int, aggressiveness: int, energy_floor: float) -> SpeechDetector:
    """Energy floor, then WebRTC VAD: the floor stops WebRTC calling room hum speech.
    Frames must be 10, 20 or 30 ms at 8/16/32/48 kHz."""
    import webrtcvad

    vad = webrtcvad.Vad(aggressiveness)

    def detect(frame: np.ndarray) -> bool:
        if _rms(frame) < energy_floor:
            return False
        pcm = (np.clip(frame, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
        return bool(vad.is_speech(pcm, sample_rate))

    return detect


class UtteranceGate:
    def __init__(
        self,
        is_speech: SpeechDetector,
        frame_ms: int,
        silence_timeout_ms: int,
        max_ms: int,
    ) -> None:
        self._is_speech = is_speech
        self._frame_ms = frame_ms
        self._silence_timeout_ms = silence_timeout_ms
        self._max_ms = max_ms
        self._pre_roll: deque[np.ndarray] = deque(maxlen=max(1, PRE_ROLL_MS // frame_ms))
        self.reset()

    def reset(self) -> None:
        """Drop any half-captured utterance (new activation, mute, cancel)."""
        self._frames: list[np.ndarray] = []
        self._pre_roll.clear()
        self._waited_ms = 0  # before the first word
        self._speech_ms = 0  # since the first word
        self._silence_ms = 0
        self._heard = False

    @property
    def heard(self) -> bool:
        """Speech has started in the utterance being captured."""
        return self._heard

    def process(self, frame: np.ndarray) -> np.ndarray | None:
        """Feed one frame; returns the finished utterance (possibly empty), else None.
        The cap is `max_ms` of waiting for a first word, then `max_ms` of speech from it,
        so a command that starts late isn't cut short."""
        if self._is_speech(frame):
            if not self._heard:
                self._frames.extend(self._pre_roll)
                self._heard = True
            self._silence_ms = 0
        else:
            self._silence_ms += self._frame_ms

        if self._heard:
            self._frames.append(frame)
            self._speech_ms += self._frame_ms
        else:
            self._pre_roll.append(frame)
            self._waited_ms += self._frame_ms

        finished = self._heard and self._silence_ms >= self._silence_timeout_ms
        capped = (self._speech_ms if self._heard else self._waited_ms) >= self._max_ms
        if not finished and not capped:
            return None
        audio = np.concatenate(self._frames) if self._heard else np.empty(0, dtype=np.float32)
        self.reset()
        return audio
