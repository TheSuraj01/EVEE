#!/usr/bin/env python3
"""Speech-side latency, and the whole local path from end of speech to action.

Stages:
  load        Whisper model load and first run: reported, never budgeted (once, at startup)
  vad         per audio frame
  wake word   per audio frame (skipped when the wake word is off or its model is missing)
  transcribe  one utterance
  local path  transcribe + resolve + policy + dispatch: end of speech to action

The 400 ms p95 budget applies to the local path. The audio is a WAV you recorded (16 kHz,
mono, 16-bit) or, by default, a synthetic voice-like clip. Whisper pads every clip to 30 s,
so the synthetic clip times the encoder fairly, but only a real recording times the decoding
of real words. Nothing is recorded and nothing is written. Needs the voice extra; the first
run downloads the Whisper model, as the app does.

Usage:
    python scripts/benchmark_stt.py [--wav command.wav] [--repeat 10] [--budget-ms 400]
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import time
import wave
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

from scripts.benchmark_intents import (  # noqa: E402
    BUDGET_MS,
    benchmark_agent,
    table,
    time_path,
    verdict,
)

RATE = 16000


def synthetic_clip(seconds: float = 1.5) -> np.ndarray:
    """Voice-like audio: harmonics of a gliding pitch under a two-syllables-a-second envelope."""
    t = np.arange(int(seconds * RATE)) / RATE
    phase = 2 * np.pi * np.cumsum(140 + 30 * np.sin(2 * np.pi * 0.8 * t)) / RATE
    voice = sum(np.sin(k * phase) / k for k in range(1, 8))
    envelope = np.clip(np.sin(2 * np.pi * 2 * t), 0, None)
    return np.asarray(0.3 * voice * envelope / np.max(np.abs(voice)), dtype=np.float32)


def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as wav:
        if (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) != (RATE, 1, 2):
            msg = f"{path}: need 16 kHz mono 16-bit PCM"
            raise SystemExit(msg)
        pcm = np.frombuffer(wav.readframes(wav.getnframes()), dtype=np.int16)
    return pcm.astype(np.float32) / 32768


def measure_speech(
    stt: Any,
    clip: np.ndarray,
    agent: Any,
    repeat: int,
    *,
    detect: Callable[[np.ndarray], bool] | None = None,
    wake: Any = None,
    frame_samples: int = 480,
) -> dict[str, list[float]]:
    """ms per stage. A stage with no component given is left out, not faked."""
    samples: dict[str, list[float]] = {"vad": [], "wake word": [], "transcribe": []}
    samples["local path"] = []
    frames = [
        clip[i : i + frame_samples] for i in range(0, clip.size - frame_samples + 1, frame_samples)
    ]
    for _ in range(repeat):
        for frame in frames:
            for stage, step in (("vad", detect), ("wake word", wake and wake.process)):
                if step:
                    t0 = time.perf_counter()
                    step(frame)
                    samples[stage].append((time.perf_counter() - t0) * 1000)
        t0 = time.perf_counter()
        transcript = stt.transcribe(clip, RATE)
        transcribe = (time.perf_counter() - t0) * 1000
        samples["transcribe"].append(transcribe)
        samples["local path"].append(transcribe + time_path(agent, transcript)["total"])
    return {stage: values for stage, values in samples.items() if values}


def main(argv: Sequence[str] | None = None) -> int:  # pragma: no cover - real models
    parser = argparse.ArgumentParser(description="Time speech-to-text and the local path.")
    parser.add_argument("--wav", type=Path, help="a 16 kHz mono 16-bit recording of a command")
    parser.add_argument("--repeat", type=int, default=10)
    parser.add_argument("--budget-ms", type=float, default=BUDGET_MS, help="local path p95")
    args = parser.parse_args(argv)

    from voice_agent.audio.vad import webrtc_detector
    from voice_agent.settings.loader import load_settings
    from voice_agent.speech.faster_whisper_provider import FasterWhisperProvider
    from voice_agent.wakeword.openwakeword_adapter import (
        OpenWakeWordDetector,
        missing_model_files,
    )

    settings = load_settings()
    audio, speech, wake_cfg = settings.audio, settings.speech, settings.wakeword
    stt = FasterWhisperProvider(
        speech.model, speech.device, speech.compute_type, speech.language, speech.beam_size
    )
    t0 = time.perf_counter()
    stt.warm_up()
    print(f"load      Whisper {speech.model!r}: {(time.perf_counter() - t0) * 1000:.0f} ms")

    wake = None
    if wake_cfg.enabled and not missing_model_files(wake_cfg.model):
        wake = OpenWakeWordDetector(wake_cfg.model, wake_cfg.threshold)
        wake.warm_up()
    else:
        print("wake word: off or model files missing; not timed")

    clip = read_wav(args.wav) if args.wav else synthetic_clip()
    detect = webrtc_detector(audio.sample_rate, audio.vad_aggressiveness, audio.vad_energy_floor)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        samples = measure_speech(
            stt,
            clip,
            benchmark_agent(Path(tmp) / "audit.jsonl"),
            args.repeat,
            detect=detect,
            wake=wake,
            frame_samples=audio.sample_rate * audio.frame_ms // 1000,
        )
    source = "synthetic" if args.wav is None else args.wav.name
    print(f"audio     {source}, {clip.size / RATE:.1f} s\n")
    print(table(samples))
    return verdict(samples, "local path", args.budget_ms)


if __name__ == "__main__":
    sys.exit(main())
