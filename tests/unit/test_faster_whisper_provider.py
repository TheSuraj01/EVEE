"""faster-whisper provider over a fake model: no model file is ever loaded."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pytest
from tests.conftest import FakeSpeechProvider

from voice_agent.core.exceptions import TranscriptionError
from voice_agent.core.interfaces import SpeechProvider
from voice_agent.speech.faster_whisper_provider import FasterWhisperProvider

SPEECH = (0.3 * np.sin(np.linspace(0, 400, 16000))).astype(np.float32)


@dataclass
class Segment:
    text: str
    avg_logprob: float = -0.2


class FakeWhisper:
    def __init__(self, texts: list[str] | None = None, error: Exception | None = None) -> None:
        self.texts = texts or [" Next tab."]
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def transcribe(self, audio: np.ndarray, **kwargs: Any) -> tuple[Any, object]:
        self.calls.append({"size": audio.size, **kwargs})
        if self.error is not None:
            raise self.error
        return (Segment(t) for t in self.texts), object()


def _provider(model: FakeWhisper, loads: list[tuple[str, str, str]] | None = None):
    def load(name: str, device: str, compute_type: str) -> FakeWhisper:
        if loads is not None:
            loads.append((name, device, compute_type))
        return model

    return FasterWhisperProvider("base", language="en", beam_size=1, load=load)


def test_real_and_fake_providers_satisfy_the_protocol() -> None:
    assert isinstance(_provider(FakeWhisper()), SpeechProvider)
    assert isinstance(FakeSpeechProvider(), SpeechProvider)


def test_model_loads_lazily_on_cpu_int8() -> None:
    loads: list[tuple[str, str, str]] = []
    provider = _provider(FakeWhisper(), loads)
    assert loads == []
    provider.transcribe(SPEECH, 16000)
    provider.transcribe(SPEECH, 16000)
    assert loads == [("base", "cpu", "int8")]


def test_warm_up_loads_and_runs_the_model_once() -> None:
    model, loads = FakeWhisper(), []
    _provider(model, loads).warm_up()
    assert len(loads) == 1
    assert len(model.calls) == 1


def test_segments_are_joined_and_trimmed_with_config_options() -> None:
    model = FakeWhisper([" Skip forward", " thirty seconds. "])
    assert _provider(model).transcribe(SPEECH, 16000) == "Skip forward thirty seconds."
    call = model.calls[0]
    assert (call["language"], call["beam_size"], call["vad_filter"]) == ("en", 1, False)


@pytest.mark.parametrize(
    "audio",
    [np.empty(0, dtype=np.float32), np.zeros(16000, dtype=np.float32)],
    ids=["empty", "silent"],
)
def test_empty_or_silent_audio_never_reaches_the_model(audio: np.ndarray) -> None:
    loads: list[tuple[str, str, str]] = []
    assert _provider(FakeWhisper(), loads).transcribe(audio, 16000) == ""
    assert loads == []


def test_wrong_sample_rate_is_refused() -> None:
    with pytest.raises(TranscriptionError, match="16000"):
        _provider(FakeWhisper()).transcribe(SPEECH, 44100)


def test_model_failure_becomes_a_transcription_error() -> None:
    with pytest.raises(TranscriptionError):
        _provider(FakeWhisper(error=RuntimeError("ctranslate2 OOM"))).transcribe(SPEECH, 16000)


def test_how_sure_the_model_was_is_kept_for_the_second_listen() -> None:
    model = FakeWhisper([" Next", " tab."])
    model.transcribe = lambda audio, **kw: (  # type: ignore[method-assign]
        iter([Segment(" Next", -0.2), Segment(" tab.", -0.7)]),
        object(),
    )
    provider = _provider(model)
    provider.transcribe(SPEECH, 16000)
    assert provider.last_confidence == -0.7  # the least sure segment
