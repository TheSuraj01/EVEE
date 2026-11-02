"""faster-whisper behind the SpeechProvider protocol: CPU, int8, loaded lazily.

Empty or silent audio returns "" at once and never reaches the model: Whisper invents text
on silence ("Thank you."), and each call costs a second or two on a CPU.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import numpy as np

from voice_agent.core.exceptions import TranscriptionError

log = logging.getLogger(__name__)

WHISPER_RATE = 16000

#: Peak amplitude below this is treated as silence (float32 audio in [-1, 1]).
SILENCE_PEAK = 1e-3


def load_whisper(name: str, device: str, compute_type: str) -> Any:  # pragma: no cover
    """Production loader: local files only; the network is touched once, when they're missing.
    (Without local_files_only, faster-whisper asks huggingface.co on every start.)"""
    from faster_whisper import WhisperModel
    from huggingface_hub.errors import LocalEntryNotFoundError

    try:
        return WhisperModel(name, device=device, compute_type=compute_type, local_files_only=True)
    except LocalEntryNotFoundError:
        log.info("Whisper model %r not found locally; downloading it once", name)
        return WhisperModel(name, device=device, compute_type=compute_type)


class FasterWhisperProvider:
    def __init__(
        self,
        model: str,
        device: str = "cpu",
        compute_type: str = "int8",
        language: str = "en",
        beam_size: int = 1,
        load: Callable[[str, str, str], Any] | None = None,
    ) -> None:
        self._name = model
        self._device = device
        self._compute_type = compute_type
        self._language = language
        self._beam_size = beam_size
        self._load = load or load_whisper
        self._model: Any = None
        #: Of the last transcription: see Transcript.confidence.
        self.last_confidence: float | None = None

    def warm_up(self) -> None:
        """Load and run once at startup, so the first command doesn't pay for it."""
        self._run(np.zeros(WHISPER_RATE, dtype=np.float32))

    def transcribe(self, audio: np.ndarray, sample_rate: int) -> str:
        if sample_rate != WHISPER_RATE:
            msg = f"Whisper needs {WHISPER_RATE} Hz audio, got {sample_rate}"
            raise TranscriptionError(msg)
        if audio.size == 0 or float(np.max(np.abs(audio))) < SILENCE_PEAK:
            return ""
        return self._run(audio)

    def _run(self, audio: np.ndarray) -> str:
        try:
            if self._model is None:
                self._model = self._load(self._name, self._device, self._compute_type)
            segments, _info = self._model.transcribe(
                audio.astype(np.float32, copy=False),
                language=self._language,
                beam_size=self._beam_size,
                vad_filter=False,  # our own gate already cut the utterance
                condition_on_previous_text=False,
                without_timestamps=True,
            )
            texts, confidence = [], None
            for segment in segments:
                texts.append(segment.text.strip())
                score = getattr(segment, "avg_logprob", None)
                if score is not None:
                    confidence = score if confidence is None else min(confidence, score)
            self.last_confidence = confidence
            return " ".join(texts).strip()
        except Exception as exc:  # library boundary: model load, ctranslate2, decoding
            msg = f"speech model failed: {type(exc).__name__}"
            raise TranscriptionError(msg) from exc
