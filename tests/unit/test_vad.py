"""Utterance gate over a synthetic waveform: deterministic, no microphone, no webrtcvad."""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pytest

from voice_agent.audio.vad import UtteranceGate, energy_detector

RATE, FRAME_MS = 16000, 30
FRAME = RATE * FRAME_MS // 1000


def silence(ms: int) -> np.ndarray:
    return np.zeros(RATE * ms // 1000, dtype=np.float32)


def noise(ms: int, rms: float = 0.002) -> np.ndarray:
    rng = np.random.default_rng(7)
    return (rng.standard_normal(RATE * ms // 1000) * rms).astype(np.float32)


def tone(ms: int, amplitude: float = 0.3) -> np.ndarray:
    t = np.arange(RATE * ms // 1000) / RATE
    return (amplitude * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


Waveform = Callable[..., np.ndarray]


@pytest.fixture
def waveform() -> Waveform:
    """Concatenate segments into one signal whose length is a whole number of frames."""

    def build(*segments: np.ndarray) -> np.ndarray:
        signal = np.concatenate(segments)
        assert signal.size % FRAME == 0
        return signal

    return build


def _gate() -> UtteranceGate:
    return UtteranceGate(energy_detector(0.01), FRAME_MS, silence_timeout_ms=900, max_ms=8000)


def _run(gate: UtteranceGate, signal: np.ndarray) -> list[tuple[int, np.ndarray]]:
    """Feed frame by frame; return (frame index, utterance) for each emission."""
    emitted = []
    for i in range(signal.size // FRAME):
        out = gate.process(signal[i * FRAME : (i + 1) * FRAME])
        if out is not None:
            emitted.append((i, out))
    return emitted


def test_utterance_ends_after_the_silence_timeout(waveform: Waveform) -> None:
    spoken = tone(600)
    emitted = _run(_gate(), waveform(silence(600), spoken, silence(1500)))
    [(index, audio)] = emitted
    # 20 silent frames, 20 tone frames, then the 30th silent frame reaches 900 ms.
    assert index == 20 + 20 + 30 - 1
    assert np.array_equal(audio[10 * FRAME : 30 * FRAME], spoken)  # after a 300 ms pre-roll
    assert audio.size == (10 + 20 + 30) * FRAME


def test_a_pause_shorter_than_the_timeout_does_not_split_the_command(
    waveform: Waveform,
) -> None:
    emitted = _run(_gate(), waveform(tone(300), silence(600), tone(300), silence(900)))
    assert len(emitted) == 1


def test_endless_speech_is_cut_at_the_command_timeout(waveform: Waveform) -> None:
    [(index, audio)] = _run(_gate(), waveform(tone(9000)))
    assert index * FRAME_MS < 8000 <= (index + 1) * FRAME_MS  # first frame at the cap
    assert audio.size == (index + 1) * FRAME


def test_no_speech_returns_empty_instead_of_silence_for_whisper(waveform: Waveform) -> None:
    [(index, audio)] = _run(_gate(), waveform(noise(8100)))
    assert index * FRAME_MS < 8000 <= (index + 1) * FRAME_MS
    assert audio.size == 0


def test_gate_resets_between_utterances(waveform: Waveform) -> None:
    gate = _gate()
    once = waveform(tone(300), silence(900))
    assert len(_run(gate, np.concatenate((once, once)))) == 2


def test_reset_discards_a_half_captured_utterance(waveform: Waveform) -> None:
    gate = _gate()
    _run(gate, waveform(tone(300)))
    gate.reset()
    [(_, audio)] = _run(gate, waveform(silence(300), tone(300), silence(900)))
    assert audio.size == (10 + 10 + 30) * FRAME


def test_energy_detector_threshold() -> None:
    detect = energy_detector(0.01)
    assert detect(tone(30))
    assert not detect(noise(30))
    assert not detect(np.empty(0, dtype=np.float32))


def test_webrtc_detector_accepts_pipeline_frames() -> None:
    webrtcvad = pytest.importorskip("webrtcvad")
    assert webrtcvad is not None
    from voice_agent.audio.vad import webrtc_detector

    detect = webrtc_detector(RATE, aggressiveness=2, energy_floor=0.01)
    assert not detect(silence(30))
    detect(tone(30))  # must not raise on a 30 ms float32 frame


def test_speech_that_starts_late_is_not_cut_short(waveform: Waveform) -> None:
    """Continuous listening: the cap counts from the first word, not from when the gate
    started listening."""
    spoken = tone(1200)
    gate = _gate()

    [(_, audio)] = _run(gate, waveform(silence(7500), spoken, silence(900)))

    assert audio.size >= spoken.size


def test_the_gate_says_when_speech_has_started(waveform: Waveform) -> None:
    gate = _gate()
    _run(gate, waveform(silence(300)))
    assert not gate.heard
    _run(gate, waveform(tone(60)))
    assert gate.heard
