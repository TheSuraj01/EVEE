"""Soak: 1 000 spoken commands through the whole VoiceService — real ring buffer, worker
thread, wake/VAD state machine, router, policy, dispatcher, plugins and audit log; fakes only
at the microphone, Whisper and the OS. Every tenth command is a repeat inside the dedupe
window (a double trigger) and must not run twice.
"""

from __future__ import annotations

import threading
import time
import tracemalloc
from collections.abc import Callable
from pathlib import Path

import pytest
from tests.conftest import (
    FakeClock,
    FakeDeviceBackend,
    FakeOpener,
    FakeSpeechProvider,
    RecordingKeySender,
    RecordingWindowBackend,
)
from tests.integration.test_voice_pipeline import COMMAND, FRAME, MIC, MarkerWake

from voice_agent.audio.device_manager import DeviceManager
from voice_agent.audio.vad import energy_detector
from voice_agent.core.models import ExecutionResult, ExecutionStatus
from voice_agent.service import VoiceService
from voice_agent.settings.loader import load_settings
from voice_agent.wakeword.activation_controller import State

COMMANDS = 1000
LEAK_LIMIT = 50 * 1024 * 1024
#: What each phrase sends (volume steps five presses at a time).
PHRASES = {
    "next tab": [("ctrl", "tab")],
    "previous tab": [("ctrl", "shift", "tab")],
    "volume up": [("volumeup",)] * 5,
    "volume down": [("volumedown",)] * 5,
    "play": [("playpause",)],
    "full screen": [("f11",)],
}
COOLDOWN_MS, DEDUPE_MS = 200, 1200


def wait_until(condition: Callable[[], bool], timeout_s: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while not condition():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.001)
    return True


@pytest.mark.slow  # ~15 s, mostly the audio worker's real 10 ms poll
def test_a_thousand_commands_leak_nothing_run_once_and_leave_no_threads(tmp_path: Path) -> None:
    phrases = list(PHRASES)
    script: list[str] = []
    for i in range(COMMANDS):
        repeat = i % 10 == 9
        script.append(script[-1] if repeat else phrases[i % len(phrases)])

    clock, keys, opener = FakeClock(), RecordingKeySender(), FakeOpener()
    heard = threading.Semaphore(0)
    results: list[ExecutionResult] = []

    def on_result(_: str, result: ExecutionResult) -> None:
        results.append(result)
        heard.release()

    baseline = set(threading.enumerate())
    listening = threading.Event()
    service = VoiceService(
        load_settings(
            env={
                "VOICE_AGENT__WAKEWORD__COOLDOWN_MS": str(COOLDOWN_MS),
                "VOICE_AGENT__NLU__DEDUPE_WINDOW_MS": str(DEDUPE_MS),
                "VOICE_AGENT__WAKEWORD__ENGINE": "openwakeword",
            }
        ),
        keys=keys,
        windows=RecordingWindowBackend(),
        prompt=None,
        llm=None,
        stt=FakeSpeechProvider(script),
        wake=MarkerWake(),
        detector=energy_detector(0.01),
        devices=DeviceManager(FakeDeviceBackend([MIC], default=1)),
        open_stream=opener,
        notify=lambda note: listening.set() if note.startswith("Listening") else None,
        on_result=on_result,
        audit_path=tmp_path / "audit.jsonl",
        clock=clock,
    )
    service.start()
    assert listening.wait(2)

    try:
        for i in range(COMMANDS):
            if i == 100:
                tracemalloc.start()  # after warm-up: caches, compiled regexes, first allocations
            for start in range(0, COMMAND.size, FRAME):
                opener.feed(COMMAND[start : start + FRAME])
            assert heard.acquire(timeout=2), f"command {i} never finished"
            assert wait_until(lambda: service.state is not State.PROCESSING)
            # Past the cooldown either way; still inside the dedupe window before a repeat.
            next_is_repeat = i + 1 < COMMANDS and (i + 1) % 10 == 9
            clock.advance(COOLDOWN_MS + 100 if next_is_repeat else DEDUPE_MS + 100)
        growth, _peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
        service.stop()

    assert growth < LEAK_LIMIT, f"{growth / 2**20:.1f} MB retained over {COMMANDS - 100} commands"

    statuses = [r.status for r in results]
    assert len(results) == COMMANDS
    assert statuses.count(ExecutionStatus.DUPLICATE) == COMMANDS // 10
    assert statuses.count(ExecutionStatus.SUCCESS) == COMMANDS - COMMANDS // 10
    expected = [keys for i, p in enumerate(script) if i % 10 != 9 for keys in PHRASES[p]]
    assert keys.calls == expected  # each command exactly once, in order

    assert wait_until(lambda: set(threading.enumerate()) <= baseline)
    assert not service.running
    assert opener.stream.closed
