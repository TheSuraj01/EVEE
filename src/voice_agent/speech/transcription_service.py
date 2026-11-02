"""Utterance -> normalised transcript, timed.

Normalising here (not only in the matcher) also strips a wake word that bled into the
captured audio, so "hey jarvis next tab" arrives as "next tab".
"""

from __future__ import annotations

import logging

import numpy as np

from voice_agent.core.exceptions import TranscriptionError, VoiceAgentError
from voice_agent.core.interfaces import Clock, SpeechProvider, SystemClock
from voice_agent.core.models import Transcript
from voice_agent.speech.text_normalizer import normalize

log = logging.getLogger(__name__)


class TranscriptionService:
    def __init__(
        self,
        provider: SpeechProvider,
        sample_rate: int,
        clock: Clock | None = None,
        careful: SpeechProvider | None = None,
    ) -> None:
        self._provider = provider
        self._rate = sample_rate
        self._clock: Clock = clock or SystemClock()
        #: A larger, slower model for a second listen when the first one was unclear.
        self._careful = careful

    @property
    def can_listen_carefully(self) -> bool:
        return self._careful is not None

    def transcribe(self, audio: np.ndarray) -> Transcript:
        """Typed errors only: anything unexpected from a provider is a TranscriptionError."""
        return self._run(self._provider, audio, "stt")

    def transcribe_carefully(self, audio: np.ndarray) -> Transcript | None:
        """The same audio through the larger model; None when there isn't one."""
        if self._careful is None:
            return None
        return self._run(self._careful, audio, "stt_careful")

    def _run(self, provider: SpeechProvider, audio: np.ndarray, stage: str) -> Transcript:
        started = self._clock.monotonic_ms()
        try:
            raw = provider.transcribe(audio, self._rate)
        except VoiceAgentError:
            raise
        except Exception as exc:
            msg = f"speech provider failed: {type(exc).__name__}"
            raise TranscriptionError(msg) from exc
        latency_ms = self._clock.monotonic_ms() - started

        text = normalize(raw)
        confidence = getattr(provider, "last_confidence", None)
        log.info(
            "transcribed%s in %.0f ms",
            " carefully" if stage == "stt_careful" else "",
            latency_ms,
            extra={"stage": stage, "latency_ms": latency_ms, "transcript": text},
        )
        return Transcript(
            text=text,
            latency_ms=latency_ms,
            confidence=confidence if isinstance(confidence, float) else None,
        )
