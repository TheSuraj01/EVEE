"""The latency budget, enforced in CI: the local command path must stay under 400 ms p95.

STT is timed by scripts/benchmark_stt.py on a machine with the models; CI has none, so here
the speech stages run against fakes to prove the harness measures what it claims.
"""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np
import pytest
from scripts.benchmark_intents import BUDGET_MS, benchmark_agent, measure, percentiles
from scripts.benchmark_stt import measure_speech, read_wav, synthetic_clip
from scripts.score_corpus import load_corpus
from tests.conftest import FakeSpeechProvider

from voice_agent.speech.faster_whisper_provider import SILENCE_PEAK, WHISPER_RATE


def test_local_command_path_p95_is_within_budget(tmp_path: Path) -> None:
    agent = benchmark_agent(tmp_path / "audit.jsonl")
    transcripts = [case.say for case in load_corpus()]

    samples = measure(agent, transcripts, repeat=2)

    assert {stage: len(values) for stage, values in samples.items()} == dict.fromkeys(
        ("resolve", "policy", "dispatch", "total"), 2 * len(transcripts)
    )
    assert percentiles(samples["total"])["p95"] <= BUDGET_MS


def test_the_benchmark_really_executes_every_repeat(tmp_path: Path) -> None:
    """Dedupe is off: a repeated command must run again, or the timings would be of a no-op."""
    agent = benchmark_agent(tmp_path / "audit.jsonl")

    measure(agent, ["next tab"], repeat=3)

    lines = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    assert all('"decision": "success"' in line for line in lines)


def test_percentiles() -> None:
    stats = percentiles([float(n) for n in range(1, 101)])

    assert stats["p50"] == pytest.approx(50.5)
    assert stats["p95"] == pytest.approx(95.05)
    assert stats["max"] == 100.0
    assert percentiles([7.0]) == dict.fromkeys(("p50", "p95", "p99", "max"), 7.0)


def test_speech_stages_are_timed_per_frame_and_per_utterance(tmp_path: Path) -> None:
    agent = benchmark_agent(tmp_path / "audit.jsonl")
    stt = FakeSpeechProvider(["next tab", "next tab"])
    clip = synthetic_clip()
    frames = clip.size // 480

    samples = measure_speech(stt, clip, agent, repeat=2, detect=lambda frame: True)

    assert len(samples["vad"]) == 2 * frames
    assert len(samples["transcribe"]) == len(samples["local path"]) == 2
    assert "wake word" not in samples  # no detector given: the stage is skipped, not faked
    assert all(p >= t for p, t in zip(samples["local path"], samples["transcribe"], strict=True))
    assert stt.calls == [(clip.size, WHISPER_RATE)] * 2


def test_the_synthetic_clip_is_loud_enough_to_reach_the_model() -> None:
    clip = synthetic_clip()

    assert clip.dtype == np.float32
    assert SILENCE_PEAK < float(np.max(np.abs(clip))) <= 1.0


def test_read_wav_accepts_16k_mono_and_refuses_anything_else(tmp_path: Path) -> None:
    def write(path: Path, rate: int, channels: int) -> Path:
        with wave.open(str(path), "wb") as wav:
            wav.setnchannels(channels)
            wav.setsampwidth(2)
            wav.setframerate(rate)
            wav.writeframes(np.full(160 * channels, 16384, dtype=np.int16).tobytes())
        return path

    audio = read_wav(write(tmp_path / "ok.wav", WHISPER_RATE, 1))
    assert audio.size == 160
    assert audio[0] == pytest.approx(0.5)

    for rate, channels in ((44100, 1), (WHISPER_RATE, 2)):
        with pytest.raises(SystemExit, match="16 kHz mono"):
            read_wav(write(tmp_path / f"bad{rate}{channels}.wav", rate, channels))
