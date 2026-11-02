from __future__ import annotations

import io
import json
import logging

import numpy as np
import pytest
from tests.conftest import FakeClock, FakeSpeechProvider

from voice_agent.core.exceptions import TranscriptionError
from voice_agent.observability.logging import JsonFormatter, RedactionFilter
from voice_agent.speech.transcription_service import TranscriptionService

AUDIO = np.ones(8000, dtype=np.float32)


class SlowProvider(FakeSpeechProvider):
    """Advances the fake clock as if inference took `ms`."""

    def __init__(self, clock: FakeClock, ms: float, text: str) -> None:
        super().__init__([text])
        self._clock, self._ms = clock, ms

    def transcribe(self, audio: np.ndarray, sample_rate: int) -> str:
        self._clock.advance(self._ms)
        return super().transcribe(audio, sample_rate)


class BrokenProvider(FakeSpeechProvider):
    def __init__(self, error: Exception) -> None:
        super().__init__()
        self._error = error

    def transcribe(self, audio: np.ndarray, sample_rate: int) -> str:
        raise self._error


def test_transcript_is_normalised_and_timed() -> None:
    clock = FakeClock()
    service = TranscriptionService(SlowProvider(clock, 640, "Hey Jarvis, Next tab!"), 16000, clock)
    result = service.transcribe(AUDIO)
    assert result.text == "next tab"
    assert result.latency_ms == 640


def test_provider_receives_the_pipeline_rate() -> None:
    provider = FakeSpeechProvider(["x"])
    TranscriptionService(provider, 16000, FakeClock()).transcribe(AUDIO)
    assert provider.calls == [(8000, 16000)]


def test_latency_metric_is_emitted_as_structured_fields(
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock = FakeClock()
    with caplog.at_level(logging.INFO, logger="voice_agent.speech"):
        TranscriptionService(SlowProvider(clock, 812, "next tab"), 16000, clock).transcribe(AUDIO)
    [record] = [r for r in caplog.records if getattr(r, "stage", None) == "stt"]
    assert record.__dict__["latency_ms"] == 812

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(RedactionFilter())
    handler.setFormatter(JsonFormatter())
    handler.handle(record)
    line = json.loads(stream.getvalue())
    assert (line["stage"], line["latency_ms"]) == ("stt", 812)


def test_silence_gives_an_empty_transcript() -> None:
    assert TranscriptionService(FakeSpeechProvider([""]), 16000).transcribe(AUDIO).text == ""


def test_unexpected_provider_failure_becomes_a_transcription_error() -> None:
    with pytest.raises(TranscriptionError):
        TranscriptionService(BrokenProvider(RuntimeError("boom")), 16000).transcribe(AUDIO)


def test_transcription_error_passes_through_unchanged() -> None:
    original = TranscriptionError("model missing")
    with pytest.raises(TranscriptionError) as info:
        TranscriptionService(BrokenProvider(original), 16000).transcribe(AUDIO)
    assert info.value is original
