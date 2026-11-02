#!/usr/bin/env python3
"""How well the agent hears *you*: your voice, your microphone, your room.

It shows a phrase; you say it. The agent's own speech gate cuts the utterance, each speech
model being compared transcribes it, and the report shows what each heard and whether the
agent would have done the right thing. Matching is local only: nothing goes to the cloud.
Audio stays in memory and is never written anywhere; what was heard is printed here only.

Close the agent first (tray -> Quit), so the microphone is free. Then, from the project
folder:
    .venv\\Scripts\\python scripts\\voice_check.py [--models base.en,small.en] [--rounds 2]
"""

from __future__ import annotations

import argparse
import queue
import statistics
import sys
import tempfile
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

#: (what to say, what should happen): an intent, "wake", "sleep", or "wake+<intent>".
PHRASES = (
    ("wake up ev", "wake"),
    ("next tab", "browser.next_tab"),
    ("previous tab", "browser.previous_tab"),
    ("close this tab", "browser.close_tab"),
    ("volume up", "media.volume_up"),
    ("pause", "media.play_pause"),
    ("mute", "media.mute"),
    ("scroll down", "page.down"),
    ("go back", "browser.back"),
    ("skip forward thirty seconds", "media.seek_forward"),
    ("open chrome", "app.open"),
    ("wake up ev, open chrome", "wake+app.open"),
    ("sleep ev", "sleep"),
)
#: Seconds to wait for you to start speaking before the phrase is skipped.
PATIENCE_S = 10


def judge(expect: str, heard: str, agent: Any, phrases: Any) -> bool:
    """Would the agent have done what `expect` names, given these words?"""
    from voice_agent.wakeword.phrases import Phrase

    if expect in ("wake", "sleep"):
        return phrases.classify(heard) is (Phrase.WAKE if expect == "wake" else Phrase.SLEEP)
    if expect.startswith("wake+"):
        prefix = phrases.wake_prefix(heard)
        if prefix is None:
            return False
        heard, expect = prefix[0], expect.removeprefix("wake+")
    proposal = agent.router.local(heard)
    return proposal is not None and proposal.intent == expect


def record(frames: queue.Queue[np.ndarray], gate: Any) -> np.ndarray | None:
    """One utterance, cut by the agent's own gate; None if nothing was said in time."""
    while not frames.empty():
        frames.get_nowait()  # what was heard before the prompt is not this phrase
    gate.reset()
    deadline = time.monotonic() + PATIENCE_S
    while True:
        try:
            utterance = gate.process(frames.get(timeout=1.0))
        except queue.Empty:
            utterance = None
        if utterance is not None and utterance.size:
            return np.asarray(utterance, dtype=np.float32)
        if not gate.heard and time.monotonic() > deadline:
            return None


def transcribe(model: Any, audio: np.ndarray) -> tuple[str, float | None, float]:
    """Words, the least sure segment's log-probability, milliseconds."""
    started = time.perf_counter()
    segments, _ = model.transcribe(
        audio,
        language="en",
        beam_size=1,
        vad_filter=False,
        condition_on_previous_text=False,
        without_timestamps=True,
    )
    segments = list(segments)
    words = " ".join(s.text.strip() for s in segments).strip()
    sure = min((s.avg_logprob for s in segments), default=None)
    return words, sure, (time.perf_counter() - started) * 1000


def dbfs(value: float) -> float:
    return 20 * float(np.log10(max(value, 1e-9)))


def main(argv: Sequence[str] | None = None) -> int:  # pragma: no cover - a microphone
    parser = argparse.ArgumentParser(description="How well the agent hears your voice.")
    parser.add_argument("--models", default="base.en,small.en", help="comma-separated")
    parser.add_argument("--rounds", type=int, default=1, help="times through the phrases")
    args = parser.parse_args(argv)

    import sounddevice
    from faster_whisper import WhisperModel

    from voice_agent.actions.apps import start_menu_apps
    from voice_agent.audio.device_manager import DeviceManager, SoundDeviceBackend
    from voice_agent.audio.vad import UtteranceGate, webrtc_detector
    from voice_agent.bootstrap import SystemSeams, build_agent
    from voice_agent.main import Simulator
    from voice_agent.settings.loader import load_settings
    from voice_agent.wakeword.phrases import WakePhrases

    settings = load_settings()
    audio_cfg, wake_cfg = settings.audio, settings.wakeword
    simulator = Simulator()
    agent = build_agent(
        settings,
        keys=simulator,
        windows=simulator,
        audit_path=Path(tempfile.mkdtemp()) / "audit.jsonl",  # nothing is dispatched
        seams=SystemSeams(apps=start_menu_apps()),
    )
    phrases = WakePhrases(wake_cfg.wake_phrase, wake_cfg.sleep_phrase)
    names = [m.strip() for m in args.models.split(",") if m.strip()]
    print("Loading", ", ".join(names), "...")
    models = {name: WhisperModel(name, device="cpu", compute_type="int8") for name in names}

    rate, frame = audio_cfg.sample_rate, audio_cfg.sample_rate * audio_cfg.frame_ms // 1000
    device = DeviceManager(SoundDeviceBackend()).select(audio_cfg.input_device)
    gate = UtteranceGate(
        webrtc_detector(rate, audio_cfg.vad_aggressiveness, audio_cfg.vad_energy_floor),
        audio_cfg.frame_ms,
        audio_cfg.silence_timeout_ms,
        int(audio_cfg.command_timeout_seconds * 1000),
    )
    frames: queue.Queue[np.ndarray] = queue.Queue()
    rows: list[dict[str, Any]] = []
    levels: list[float] = []
    print(f"Microphone: {device.name}. Speak normally, as you would to the agent.\n")
    with sounddevice.InputStream(
        device=device.index,
        samplerate=rate,
        channels=1,
        dtype="float32",
        blocksize=frame,
        callback=lambda data, *_: frames.put(data[:, 0].copy()),
    ):
        for _ in range(args.rounds):
            for say, expect in PHRASES:
                print(f'Say: "{say}"')
                audio = record(frames, gate)
                if audio is None:
                    print("  (nothing heard: skipped)\n")
                    continue
                lead, tail = int(0.3 * rate), int(0.9 * rate)  # the gate's pre-roll and silence
                voiced = audio[lead : max(lead + 1, audio.size - tail)]
                levels.append(float(np.sqrt(np.mean(np.square(voiced)))))
                row: dict[str, Any] = {"say": say}
                for name, model in models.items():
                    heard, sure, ms = transcribe(model, audio)
                    ok = judge(expect, heard, agent, phrases)
                    row[name] = (ok, sure, ms, heard)
                    mark = "ok  " if ok else "MISS"
                    sure_txt = "?" if sure is None else f"{sure:.2f}"
                    print(f"  {mark} {name:<9} {ms:5.0f} ms  sure {sure_txt:>5}  heard {heard!r}")
                rows.append(row)
                print()

    print("=" * 72)
    for name in names:
        results = [r[name] for r in rows]
        if results:
            right = sum(ok for ok, *_ in results)
            ms = statistics.median(ms for _, _, ms, _ in results)
            print(f"{name:<9} {right}/{len(results)} right, median {ms:.0f} ms")
    if levels:
        speech = statistics.median(levels)
        floor = audio_cfg.vad_energy_floor
        print(
            f"Your speech: {dbfs(speech):.0f} dBFS (RMS {speech:.4f}); the agent ignores "
            f"anything below {floor} (audio.vad_energy_floor)."
        )
        if speech < 3 * floor:
            print(f"  -> Quiet for this floor: set audio.vad_energy_floor to {speech / 3:.4f}.")
    print("\nPaste this report into the chat to tune the agent to your voice.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
