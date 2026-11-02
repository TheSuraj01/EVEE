"""VoiceService: the one object the console and the tray both drive. Faked at the edges only."""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

import numpy as np
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
from tests.integration.test_voice_pipeline import silence as silence_ms

from voice_agent.audio.device_manager import DeviceManager
from voice_agent.audio.vad import energy_detector
from voice_agent.core.models import CommandSpec, ExecutionResult
from voice_agent.service import VoiceService
from voice_agent.settings.loader import load_settings
from voice_agent.storage.command_history import CommandHistory
from voice_agent.storage.preferences import Preferences
from voice_agent.wakeword.activation_controller import State


class Events:
    def __init__(self) -> None:
        self.notes: list[str] = []
        self.results: list[tuple[str, ExecutionResult]] = []
        self.listening = threading.Semaphore(0)
        self.result = threading.Event()

    def notify(self, message: str) -> None:
        self.notes.append(message)
        if message.startswith(("Listening", "Muted:")):
            self.listening.release()

    def on_result(self, transcript: str, result: ExecutionResult) -> None:
        self.results.append((transcript, result))
        self.result.set()


class BrokenWarmUp(FakeSpeechProvider):
    def warm_up(self) -> None:
        raise RuntimeError("model file is corrupt")


class Rig:
    def __init__(
        self,
        tmp_path: Path,
        stt: FakeSpeechProvider,
        prompt: object = None,
        preferences: Preferences | None = None,
        engine: str = "openwakeword",
    ) -> None:
        self.events = Events()
        self.keys = RecordingKeySender()
        self.opener = FakeOpener()
        self.clock = FakeClock()
        self.stt = stt
        self.service = VoiceService(
            load_settings(env={"VOICE_AGENT__WAKEWORD__ENGINE": engine}),
            keys=self.keys,
            windows=RecordingWindowBackend(),
            prompt=prompt,  # type: ignore[arg-type]
            llm=None,
            stt=stt,
            wake=MarkerWake(),
            detector=energy_detector(0.01),
            devices=DeviceManager(FakeDeviceBackend([MIC], default=1)),
            open_stream=self.opener,
            notify=self.events.notify,
            on_result=self.events.on_result,
            audit_path=tmp_path / "audit.jsonl",
            clock=self.clock,
            preferences=preferences,
        )

    def speak(self, signal: np.ndarray) -> None:
        for i in range(signal.size // FRAME):
            self.opener.feed(signal[i * FRAME : (i + 1) * FRAME])


@pytest.fixture
def rig(tmp_path: Path):
    r = Rig(tmp_path, FakeSpeechProvider(["next tab"]))
    yield r
    r.service.stop()


def test_start_returns_at_once_then_loads_models_and_listens(rig: Rig) -> None:
    rig.service.start()
    assert rig.events.listening.acquire(timeout=2)
    assert rig.stt.warmed
    assert rig.events.notes[:2] == ["Loading speech models...", "Ready."]
    assert rig.service.state is State.LISTENING


def test_spoken_command_runs_through_the_service(rig: Rig) -> None:
    rig.service.start()
    assert rig.events.listening.acquire(timeout=2)
    rig.speak(COMMAND)
    assert rig.events.result.wait(2)
    assert rig.keys.calls == [("ctrl", "tab")]


def test_mute_and_unmute(rig: Rig) -> None:
    rig.service.start()
    assert rig.events.listening.acquire(timeout=2)
    rig.service.mute()
    assert (rig.service.muted, rig.service.state) == (True, State.IDLE)
    rig.speak(COMMAND)
    rig.service.unmute()
    assert (rig.service.muted, rig.service.state) == (False, State.LISTENING)
    assert rig.keys.calls == []


@pytest.mark.parametrize("silence", ["mute", "emergency_stop"])
def test_muted_audio_never_reaches_memory(rig: Rig, silence: str) -> None:
    """Privacy: while muted, microphone samples are dropped in the audio callback itself."""
    rig.service.start()
    assert rig.events.listening.acquire(timeout=2)
    ring = rig.service._ring

    rig.speak(np.concatenate((silence_ms(30), COMMAND[30 * 16 :])))  # speech, no wake word
    assert ring._buf.any()

    getattr(rig.service, silence)()
    assert not ring._buf.any()  # what was heard before the mute is wiped, not kept
    before = ring.written
    rig.speak(COMMAND)
    assert ring.written == before

    rig.service.unmute()
    rig.speak(COMMAND)
    assert ring.written > before
    assert rig.events.result.wait(2)


def test_a_restart_after_mute_starts_with_the_microphone_dropped(tmp_path: Path) -> None:
    prefs = Preferences(tmp_path / "prefs.sqlite3")
    prefs.set("muted", True)
    rig = Rig(tmp_path, FakeSpeechProvider(["next tab"]), preferences=prefs)
    try:
        rig.service.start()
        assert rig.events.listening.acquire(timeout=2)
        assert rig.events.notes[-1] == "Muted: 'USB Mic' is open, but nothing is heard."
        before = rig.service._ring.written
        rig.speak(COMMAND)
        assert rig.service._ring.written == before
    finally:
        rig.service.stop()


def test_stop_ends_the_thread_and_closes_the_microphone(rig: Rig) -> None:
    rig.service.start()
    assert rig.events.listening.acquire(timeout=2)
    rig.service.stop()
    assert not rig.service.running
    assert rig.opener.stream.closed


def test_model_failure_is_reported_and_nothing_listens(tmp_path: Path) -> None:
    rig = Rig(tmp_path, BrokenWarmUp())
    rig.service.start()
    rig.service.join(2)
    assert not rig.service.running
    assert any("Could not load the speech models" in n for n in rig.events.notes)
    assert rig.opener.callback is None  # the microphone was never opened


def test_emergency_stop_cancels_a_pending_confirmation(tmp_path: Path) -> None:
    """Invariant 8: the kill switch reaches a confirmation prompt that is already up."""
    asked, released = threading.Event(), threading.Event()

    class SlowPrompt:
        def confirm(self, spec: CommandSpec, timeout_s: float) -> bool:
            asked.set()
            released.wait(5)
            return True  # the user would say yes, but the stop came first

    rig = Rig(tmp_path, FakeSpeechProvider(["close this window"]), prompt=SlowPrompt())
    rig.service.start()
    assert rig.events.listening.acquire(timeout=2)
    worker = threading.Thread(target=rig.speak, args=(COMMAND,))
    worker.start()
    assert asked.wait(2)

    rig.service.emergency_stop()
    released.set()
    assert rig.events.result.wait(2)
    worker.join(2)
    rig.service.stop()

    [(_, result)] = rig.events.results
    assert not result.ok
    assert rig.service.muted
    assert any("Emergency stop" in n for n in rig.events.notes)


def test_emergency_stop_cuts_off_a_long_key_sequence(tmp_path: Path) -> None:
    """Invariant 8: 'skip forward 30 seconds' is six presses; the stop lands after two."""
    rig = Rig(tmp_path, FakeSpeechProvider(["skip forward 30 seconds"]))

    def press(*chord: str) -> None:
        rig.keys.calls.append(chord)
        if len(rig.keys.calls) == 2:
            rig.service.emergency_stop()

    rig.keys.hotkey = press  # type: ignore[method-assign]
    rig.service.start()
    assert rig.events.listening.acquire(timeout=2)
    rig.speak(COMMAND)
    assert rig.events.result.wait(2)

    assert rig.keys.calls == [("right",), ("right",)]
    [(_, result)] = rig.events.results
    assert result.detail["reason"] == "interrupted"


def _service_with_storage(
    tmp_path: Path, history_on: bool
) -> tuple[Rig, Preferences, CommandHistory]:
    db = tmp_path / "agent.sqlite3"
    rig = Rig(tmp_path, FakeSpeechProvider(["next tab"]))
    prefs, history = Preferences(db), CommandHistory(db, enabled=history_on)
    rig.service = VoiceService(
        load_settings(env={"VOICE_AGENT__WAKEWORD__ENGINE": "openwakeword"}),
        keys=rig.keys,
        windows=RecordingWindowBackend(),
        prompt=None,
        llm=None,
        stt=rig.stt,
        wake=MarkerWake(),
        detector=energy_detector(0.01),
        devices=DeviceManager(FakeDeviceBackend([MIC], default=1)),
        open_stream=rig.opener,
        notify=rig.events.notify,
        on_result=rig.events.on_result,
        audit_path=tmp_path / "audit.jsonl",
        clock=FakeClock(),
        preferences=prefs,
        history=history,
    )
    return rig, prefs, history


def test_mute_is_remembered_and_a_restart_starts_muted(tmp_path: Path) -> None:
    rig, prefs, _ = _service_with_storage(tmp_path, history_on=False)
    rig.service.emergency_stop()
    assert prefs.get("muted", False) is True

    restarted, _, _ = _service_with_storage(tmp_path, history_on=False)
    assert restarted.service.muted  # privacy-safe: a stop survives a restart
    restarted.service.unmute()
    assert prefs.get("muted", True) is False


def test_results_reach_history_only_when_opted_in(tmp_path: Path) -> None:
    for opted_in in (False, True):
        rig, _, history = _service_with_storage(tmp_path / str(opted_in), history_on=opted_in)
        rig.service.start()
        assert rig.events.listening.acquire(timeout=2)
        rig.speak(COMMAND)
        assert rig.events.result.wait(2)
        rig.service.stop()
        assert [e.transcript for e in history.recent()] == (["next tab"] if opted_in else [])


def test_clear_history_purges_through_the_service(tmp_path: Path) -> None:
    rig, _, history = _service_with_storage(tmp_path, history_on=True)
    history.record("next tab", ExecutionResult(status="success", intent="browser.next_tab"))
    rig.service.clear_history()
    assert history.recent() == []


def test_mute_then_unmute_mid_sequence_still_stops_the_key_loop(tmp_path: Path) -> None:
    """Phase 4 audit: 'interrupted' meant 'idle', so a quick unmute let the loop resume."""
    rig = Rig(tmp_path, FakeSpeechProvider(["skip forward 30 seconds"]))

    def press(*chord: str) -> None:
        rig.keys.calls.append(chord)
        if len(rig.keys.calls) == 2:
            rig.service.mute()
            rig.service.unmute()  # back to listening before the next press

    rig.keys.hotkey = press  # type: ignore[method-assign]
    rig.service.start()
    assert rig.events.listening.acquire(timeout=2)
    rig.speak(COMMAND)
    assert rig.events.result.wait(2)
    assert rig.keys.calls == [("right",), ("right",)]


def test_a_rejected_plugin_is_announced_at_startup(tmp_path: Path) -> None:
    events = Events()
    settings = load_settings(env={"VOICE_AGENT__PLUGINS__ENABLED": "[browser, ghost]"})
    service = VoiceService(
        settings,
        keys=RecordingKeySender(),
        windows=RecordingWindowBackend(),
        prompt=None,
        llm=None,
        stt=BrokenWarmUp(),  # stop right after the announcements
        wake=MarkerWake(),
        detector=energy_detector(0.01),
        devices=DeviceManager(FakeDeviceBackend([MIC], default=1)),
        open_stream=FakeOpener(),
        notify=events.notify,
        on_result=events.on_result,
        audit_path=tmp_path / "audit.jsonl",
        clock=FakeClock(),
    )
    service.start()
    service.join(2)
    assert "Plugin 'ghost' was not loaded: not found" in events.notes


def test_the_service_says_it_is_loading_until_the_models_are_ready(tmp_path: Path) -> None:
    release = threading.Event()

    class SlowWarmUp(FakeSpeechProvider):
        def warm_up(self) -> None:
            release.wait(2)  # a first-run download

    rig = Rig(tmp_path, SlowWarmUp(["next tab"]))
    try:
        assert not rig.service.loading
        rig.service.start()
        assert rig.service.loading
        release.set()
        assert rig.events.listening.acquire(timeout=2)
        assert not rig.service.loading
    finally:
        rig.service.stop()


def test_a_failed_model_load_is_not_left_reporting_loading(tmp_path: Path) -> None:
    rig = Rig(tmp_path, BrokenWarmUp())
    rig.service.start()
    rig.service.join(2)
    assert not rig.service.loading
    assert not rig.service.running


SPEECH_WITHOUT_WAKE_WORD = np.concatenate((silence_ms(30), COMMAND[FRAME:]))


def test_a_click_while_muted_unmutes(rig: Rig) -> None:
    rig.service.start()
    assert rig.events.listening.acquire(timeout=2)
    rig.service.emergency_stop()

    rig.service.tap()

    assert (rig.service.muted, rig.service.state) == (False, State.LISTENING)


def test_a_click_takes_a_command_without_the_wake_word(rig: Rig) -> None:
    rig.service.start()
    assert rig.events.listening.acquire(timeout=2)

    rig.service.tap()
    rig.speak(SPEECH_WITHOUT_WAKE_WORD)

    assert rig.events.result.wait(2)
    assert rig.keys.calls == [("ctrl", "tab")]


def test_without_a_click_speech_without_the_wake_word_does_nothing(rig: Rig) -> None:
    rig.service.start()
    assert rig.events.listening.acquire(timeout=2)

    rig.speak(SPEECH_WITHOUT_WAKE_WORD)

    assert not rig.events.result.wait(0.3)
    assert rig.keys.calls == []


def test_a_double_click_on_a_muted_agent_only_unmutes(rig: Rig) -> None:
    """The second click of a double-click must not also start a command."""
    rig.service.start()
    assert rig.events.listening.acquire(timeout=2)
    rig.service.mute()

    rig.service.tap()
    rig.service.tap()
    rig.speak(SPEECH_WITHOUT_WAKE_WORD)

    assert not rig.service.muted
    assert not rig.events.result.wait(0.3)
    rig.clock.advance(1000)  # a later, separate click does start one
    rig.service.tap()
    rig.speak(SPEECH_WITHOUT_WAKE_WORD)
    assert rig.events.result.wait(2)


def test_muting_cancels_a_click_that_was_not_taken_up_yet(rig: Rig) -> None:
    rig.service.start()
    assert rig.events.listening.acquire(timeout=2)

    rig.service.tap()  # no audio has arrived to take it up
    rig.service.mute()
    rig.clock.advance(1000)
    rig.service.unmute()
    rig.speak(SPEECH_WITHOUT_WAKE_WORD)

    assert not rig.events.result.wait(0.3)
    assert rig.keys.calls == []


def test_phrase_mode_starts_asleep_and_wakes_on_the_phrase(tmp_path: Path) -> None:
    rig = Rig(tmp_path, FakeSpeechProvider(["next tab", "wake up ev", "next tab"]), engine="phrase")
    try:
        rig.service.start()
        assert rig.events.listening.acquire(timeout=2)
        assert rig.service.asleep

        for _ in range(3):
            rig.speak(SPEECH_WITHOUT_WAKE_WORD)
            rig.events.result.clear()
        assert wait_until(lambda: rig.keys.calls == [("ctrl", "tab")])
        assert not rig.service.asleep
    finally:
        rig.service.stop()


def test_in_phrase_mode_a_click_toggles_awake_and_mute_sends_it_to_sleep(tmp_path: Path) -> None:
    rig = Rig(tmp_path, FakeSpeechProvider([]), engine="phrase")
    try:
        rig.service.start()
        assert rig.events.listening.acquire(timeout=2)

        rig.service.tap()
        assert not rig.service.asleep
        rig.clock.advance(1000)
        rig.service.tap()
        assert rig.service.asleep

        rig.clock.advance(1000)
        rig.service.tap()  # awake again...
        rig.service.emergency_stop()  # ...the kill switch mutes and sends it to sleep
        assert rig.service.muted
        assert rig.service.asleep

        rig.clock.advance(1000)
        rig.service.tap()  # a click on the muted pill: listening and awake
        assert (rig.service.muted, rig.service.asleep) == (False, False)
    finally:
        rig.service.stop()


def wait_until(condition: Callable[[], bool], timeout_s: float = 2.0) -> bool:
    import time

    deadline = time.monotonic() + timeout_s
    while not condition():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.005)
    return True
