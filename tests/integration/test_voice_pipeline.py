"""Milestone T-027: voice controls the PC with zero cloud calls.

Real ring buffer, worker thread, utterance gate, state machine, router, policy and
dispatcher. Faked only at the edges: the microphone stream, the wake word model, Whisper,
and the keyboard/window backends.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from tests.conftest import (
    FakeClock,
    FakeDeviceBackend,
    FakeOpener,
    FakeSpeechProvider,
    FakeTransport,
    RecordingKeySender,
    RecordingWindowBackend,
)

from voice_agent.audio.capture import AudioCapture
from voice_agent.audio.device_manager import DeviceManager
from voice_agent.audio.ring_buffer import RingBuffer
from voice_agent.audio.vad import UtteranceGate, energy_detector
from voice_agent.bootstrap import Agent, build_agent
from voice_agent.core.exceptions import TranscriptionError
from voice_agent.core.models import AudioDevice, ExecutionResult, ExecutionStatus
from voice_agent.settings.loader import load_settings
from voice_agent.speech.transcription_service import TranscriptionService
from voice_agent.voice_loop import AlwaysAwake, VoiceLoop, listen
from voice_agent.wakeword.activation_controller import ActivationController, State

RATE, FRAME_MS = 16000, 30
FRAME = RATE * FRAME_MS // 1000
MIC = AudioDevice(index=1, name="USB Mic", channels=1, default_samplerate=48000)


def silence(ms: int) -> np.ndarray:
    return np.zeros(RATE * ms // 1000, dtype=np.float32)


def tone(ms: int, amplitude: float = 0.3) -> np.ndarray:
    t = np.arange(RATE * ms // 1000) / RATE
    return (amplitude * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


#: "Hey Jarvis" (a loud marker the fake wake model keys on), "next tab", then silence.
COMMAND = np.concatenate((tone(30, amplitude=0.95), tone(600), silence(1200)))


class MarkerWake:
    """Fake wake word model: fires on any frame louder than 0.9."""

    def __init__(self) -> None:
        self.resets = 0

    def process(self, frame: np.ndarray) -> bool:
        return float(np.max(np.abs(frame))) > 0.9

    def reset(self) -> None:
        self.resets += 1


class Harness:
    def __init__(
        self, tmp_path: Path, transcripts: list[str], llm: FakeTransport | None = None
    ) -> None:
        self.keys = RecordingKeySender()
        self.agent: Agent = build_agent(
            load_settings(env={}),
            keys=self.keys,
            windows=RecordingWindowBackend(),
            audit_path=tmp_path / "audit.jsonl",
            llm=llm,
        )
        self.clock = FakeClock()
        self.controller = ActivationController(cooldown_ms=1500, clock=self.clock)
        self.wake = MarkerWake()
        self.stt = FakeSpeechProvider(transcripts)
        self.results: list[tuple[str, ExecutionResult]] = []
        self.done = threading.Event()
        self.loop = VoiceLoop(
            self.controller,
            self.wake,
            UtteranceGate(energy_detector(0.01), FRAME_MS, 900, 8000),
            TranscriptionService(self.stt, RATE, self.clock),
            self.agent.handle,
            on_result=self._record,
        )

    def _record(self, transcript: str, result: ExecutionResult) -> None:
        self.results.append((transcript, result))
        self.done.set()

    def feed(self, signal: np.ndarray) -> None:
        for i in range(signal.size // FRAME):
            self.loop.on_frame(signal[i * FRAME : (i + 1) * FRAME])


@pytest.fixture
def harness(tmp_path: Path) -> Harness:
    h = Harness(tmp_path, ["Hey Jarvis, next tab."])
    h.controller.start()
    return h


def test_spoken_command_presses_the_keys(harness: Harness) -> None:
    harness.feed(COMMAND)
    assert harness.keys.calls == [("ctrl", "tab")]
    [(heard, result)] = harness.results
    assert heard == "next tab"
    assert result.status is ExecutionStatus.SUCCESS
    assert harness.controller.state is State.COOLDOWN
    assert harness.wake.resets == 1


def test_speech_without_the_wake_word_does_nothing(harness: Harness) -> None:
    harness.feed(np.concatenate((tone(600), silence(1200))))
    assert harness.keys.calls == []
    assert harness.stt.calls == []


def test_wake_word_then_silence_never_reaches_whisper(harness: Harness) -> None:
    harness.feed(np.concatenate((tone(30, amplitude=0.95), silence(8100))))
    assert harness.stt.calls == []
    [(_, result)] = harness.results
    assert result.status is ExecutionStatus.UNRESOLVED


def test_mute_during_transcription_cancels_the_command(tmp_path: Path) -> None:
    h = Harness(tmp_path, [])

    class MutingProvider(FakeSpeechProvider):
        def transcribe(self, audio: np.ndarray, sample_rate: int) -> str:
            h.controller.mute()  # the kill switch lands while Whisper is busy
            return "next tab"

    h.loop._stt = TranscriptionService(MutingProvider(), RATE, h.clock)
    h.controller.start()
    h.feed(COMMAND)
    assert h.keys.calls == []
    assert h.controller.state is State.IDLE


def test_transcription_failure_is_reported_and_the_loop_recovers(harness: Harness) -> None:
    class Failing(FakeSpeechProvider):
        def transcribe(self, audio: np.ndarray, sample_rate: int) -> str:
            raise TranscriptionError("model file missing")

    harness.loop._stt = TranscriptionService(Failing(), RATE, harness.clock)
    harness.feed(COMMAND)
    [(_, result)] = harness.results
    assert result.status is ExecutionStatus.ERROR
    assert result.message == TranscriptionError.user_message
    harness.clock.advance(1500)
    assert harness.controller.state is State.LISTENING


def test_wake_word_disabled_means_every_utterance_is_a_command(tmp_path: Path) -> None:
    h = Harness(tmp_path, ["next tab"])
    h.loop._wake = AlwaysAwake()
    h.controller.start()
    h.feed(np.concatenate((silence(30), tone(600), silence(1200))))
    assert h.keys.calls == [("ctrl", "tab")]


def _run_listen(h: Harness, backend: FakeDeviceBackend, opener: FakeOpener):
    ring = RingBuffer(RATE * 10)
    capture = AudioCapture(ring, opener, RATE, FRAME, clock=h.clock)
    stop, notes = threading.Event(), []
    listening = threading.Semaphore(0)

    def notify(message: str) -> None:
        notes.append(message)
        if message.startswith("Listening"):
            listening.release()

    thread = threading.Thread(
        target=listen,
        kwargs={
            "devices": DeviceManager(backend),
            "preference": None,
            "capture": capture,
            "ring": ring,
            "frame_samples": FRAME,
            "on_frame": h.loop.on_frame,
            "controller": h.controller,
            "stop": stop,
            "notify": notify,
            "retry_s": 0.01,
        },
        daemon=True,
    )
    thread.start()
    return thread, stop, notes, listening


def test_live_threads_turn_microphone_audio_into_a_key_press(tmp_path: Path) -> None:
    h = Harness(tmp_path, ["next tab"])
    opener = FakeOpener()
    thread, stop, notes, listening = _run_listen(h, FakeDeviceBackend([MIC], default=1), opener)
    assert listening.acquire(timeout=2)

    for i in range(COMMAND.size // FRAME):  # PortAudio delivering 30 ms blocks
        opener.feed(COMMAND[i * FRAME : (i + 1) * FRAME])
    assert h.done.wait(2)
    stop.set()
    thread.join(2)

    assert h.keys.calls == [("ctrl", "tab")]
    assert not thread.is_alive()
    assert opener.stream.closed
    assert h.controller.state is State.IDLE
    assert notes[0] == "Listening on 'USB Mic'."


def test_unplugged_microphone_is_reported_and_reacquired(tmp_path: Path) -> None:
    h = Harness(tmp_path, [])
    backend, opener = FakeDeviceBackend([MIC], default=1), FakeOpener()
    thread, stop, notes, listening = _run_listen(h, backend, opener)
    assert listening.acquire(timeout=2)

    opener.stream.active = False  # PortAudio drops the device
    assert listening.acquire(timeout=2)  # ...and we are listening again
    stop.set()
    thread.join(2)

    assert any("unplugged" in n for n in notes)
    assert backend.refreshed == 1


def test_no_microphone_at_start_waits_for_one(tmp_path: Path) -> None:
    h = Harness(tmp_path, [])
    backend = FakeDeviceBackend([], default=None)
    thread, stop, notes, listening = _run_listen(h, backend, FakeOpener())
    assert not listening.acquire(timeout=0.05)
    backend.devices, backend.default = [MIC], 1  # user plugs a mic in
    assert listening.acquire(timeout=2)
    stop.set()
    thread.join(2)
    assert notes[0].startswith("Microphone unavailable")


def test_stopping_cancels_a_pending_confirmation_before_waiting_for_the_worker(
    tmp_path: Path,
) -> None:
    """Phase 2 audit: Ctrl+C used to leave a confirmation answerable for ~2 s."""
    released, entered = threading.Event(), threading.Event()
    h = Harness(tmp_path, [])
    h.controller = ActivationController(1500, clock=h.clock, on_mute=released.set)

    def pending_confirmation(frame: np.ndarray) -> None:
        entered.set()
        released.wait(5)  # like ConfirmationBroker.confirm blocked on the user

    h.loop.on_frame = pending_confirmation  # type: ignore[method-assign]
    opener = FakeOpener()
    thread, stop, _, listening = _run_listen(h, FakeDeviceBackend([MIC], default=1), opener)
    assert listening.acquire(timeout=2)
    opener.feed(silence(30))
    assert entered.wait(2)

    stop.set()
    thread.join(1.0)  # worker.stop() alone would wait 2 s
    assert not thread.is_alive()
    assert released.is_set()


def test_mute_during_a_slow_cloud_call_cancels_the_command(tmp_path: Path) -> None:
    """Cloud-path audit P1: the Groq call can take seconds; a mute in that window must win."""
    reply = (
        '{"intent":"browser.next_tab","argument_seconds":null,'
        '"requires_confirmation":false,"confidence":0.9}'
    )
    holder: list[Harness] = []

    class MuteMidCall(FakeTransport):
        def complete(self, **kwargs: object) -> str:
            holder[0].controller.mute()  # the user hits mute while Groq is thinking
            return super().complete(**kwargs)

    h = Harness(tmp_path, ["move one tab to the right"], llm=MuteMidCall([reply]))
    holder.append(h)
    h.controller.start()
    h.feed(COMMAND)

    assert h.keys.calls == []
    assert h.controller.state is State.IDLE


# --- wakeword.engine: phrase -----------------------------------------------------------
#: One sentence, no wake marker: in phrase mode there is no wake model.
SENTENCE = np.concatenate((silence(60), tone(600), silence(1200)))


class UnsureSpeech(FakeSpeechProvider):
    """The fast model: scripted words, and how sure it was of each (Transcript.confidence)."""

    def __init__(self, heard: list[tuple[str, float]]) -> None:
        super().__init__([text for text, _ in heard])
        self._scores = [score for _, score in heard]
        self.last_confidence: float | None = None

    def transcribe(self, audio: np.ndarray, sample_rate: int) -> str:
        self.last_confidence = self._scores.pop(0) if self._scores else None
        return super().transcribe(audio, sample_rate)


def phrase_harness(
    tmp_path: Path,
    transcripts: list[str],
    knows: Any = None,
    asleep_max_samples: int | None = None,
    stt: FakeSpeechProvider | None = None,
    careful: FakeSpeechProvider | None = None,
) -> Harness:
    from voice_agent.wakeword.phrases import WakePhrases

    h = Harness(tmp_path, transcripts)
    h.stt = stt or h.stt
    h.controller = ActivationController(cooldown_ms=0, clock=h.clock)  # no wake model to echo
    h.loop = VoiceLoop(
        h.controller,
        AlwaysAwake(),
        UtteranceGate(energy_detector(0.01), FRAME_MS, 900, 8000),
        TranscriptionService(h.stt, RATE, h.clock, careful=careful),
        h.agent.handle,
        on_result=h._record,
        phrases=WakePhrases("wake up ev", "sleep ev"),
        knows=knows or h.agent.knows,
        asleep_max_samples=asleep_max_samples,
    )
    h.controller.start()
    return h


def test_asleep_only_the_wake_phrase_counts_then_every_sentence_is_a_command(
    tmp_path: Path,
) -> None:
    h = phrase_harness(
        tmp_path,
        ["next tab", "Wake up, EV.", "next tab", "previous tab", "Sleep, Eve.", "next tab"],
    )

    for _ in range(6):
        h.feed(SENTENCE)

    assert h.keys.calls == [("ctrl", "tab"), ("ctrl", "shift", "tab")]  # only while awake
    assert len(h.stt.calls) == 6  # asleep speech is transcribed locally to find the phrase...
    assert h.results[0][1].message.startswith("Awake")  # ...but never reported
    assert len(h.results) == 4  # awake, two commands, asleep
    assert not h.loop.awake


def test_while_listening_continuously_silence_costs_nothing(tmp_path: Path) -> None:
    h = phrase_harness(tmp_path, ["wake up ev"])
    h.feed(SENTENCE)

    h.feed(silence(20_010))

    assert len(h.stt.calls) == 1  # Whisper never runs on silence
    assert len(h.results) == 1  # and no "I didn't hear a command" every 8 seconds


def test_asleep_speech_never_reaches_the_agent(tmp_path: Path) -> None:
    h = phrase_harness(tmp_path, ["close this window", "delete all my files"])
    handled: list[str] = []
    h.loop._handle = lambda text, _still: handled.append(text)  # type: ignore[assignment,return-value]

    h.feed(SENTENCE)
    h.feed(SENTENCE)

    assert handled == []
    assert h.results == []


def test_a_click_wakes_and_sleeps_it(tmp_path: Path) -> None:
    h = phrase_harness(tmp_path, ["next tab"])

    h.loop.set_awake(True)
    h.feed(SENTENCE)
    assert h.keys.calls == [("ctrl", "tab")]

    h.loop.set_awake(False)
    assert not h.loop.awake


def test_the_wake_phrase_and_a_command_in_one_breath_wakes_and_runs_it(tmp_path: Path) -> None:
    h = phrase_harness(tmp_path, ["Wake up EV, next tab.", "previous tab"])

    h.feed(SENTENCE)
    h.feed(SENTENCE)

    assert h.loop.awake
    assert h.keys.calls == [("ctrl", "tab"), ("ctrl", "shift", "tab")]
    assert h.results[0][1].message.startswith("Awake")


def test_a_loose_hearing_wakes_only_for_a_known_command(tmp_path: Path) -> None:
    """'Wake up F' is Whisper's 'wake up ev' - and also how 'wake up at...' starts."""
    h = phrase_harness(tmp_path, ["Wake up at seven tomorrow.", "Wake up F, next tab."])
    handled: list[str] = []
    handle = h.loop._handle

    def spy(text: str, still: Any) -> Any:
        handled.append(text)
        return handle(text, still)

    h.loop._handle = spy  # type: ignore[method-assign]

    h.feed(SENTENCE)
    assert not h.loop.awake and handled == []  # asleep speech goes nowhere, cloud included

    h.feed(SENTENCE)
    assert h.loop.awake and handled == ["next tab"]


def test_the_wake_phrase_while_awake_is_not_sent_anywhere(tmp_path: Path) -> None:
    h = phrase_harness(tmp_path, ["wake up ev", "Wake up, EV."])
    handled: list[str] = []
    h.loop._handle = lambda text, _still: handled.append(text)  # type: ignore[assignment,return-value]

    h.feed(SENTENCE)
    h.feed(SENTENCE)

    assert h.loop.awake and handled == []
    assert len(h.results) == 1  # "Awake" once


def test_a_command_that_sounds_like_the_sleep_phrase_stays_a_command(tmp_path: Path) -> None:
    """'Sleep PC' is 0.75 like 'sleep ev': it must reach the agent, not send it to sleep."""
    h = phrase_harness(tmp_path, ["Sleep PC.", "Sleep, Eve."], knows=lambda t: t == "sleep pc")
    handled: list[str] = []
    h.loop._handle = lambda text, _still: handled.append(text)  # type: ignore[assignment,return-value]
    h.loop.set_awake(True)

    h.feed(SENTENCE)
    assert handled == ["sleep pc"] and h.loop.awake
    h.feed(SENTENCE)
    assert not h.loop.awake


def test_asleep_a_long_stretch_of_speech_skips_whisper(tmp_path: Path) -> None:
    """TV or a conversation can't be the wake phrase; transcribing it would put the loop
    behind, so the phrase said next would wait in a queue - or fall out of the buffer."""
    h = phrase_harness(tmp_path, ["wake up ev"], asleep_max_samples=RATE)

    h.feed(SENTENCE)  # longer than a second

    assert h.stt.calls == [] and not h.loop.awake


# --- a second, careful listen ------------------------------------------------------------


def test_unclear_words_are_listened_to_again_by_the_larger_model(tmp_path: Path) -> None:
    """'Go back ahead', said unsurely, is no command: the larger model hears 'go back a tab'."""
    careful = FakeSpeechProvider(["Go back a tab."])
    h = phrase_harness(tmp_path, [], stt=UnsureSpeech([("Go back ahead.", -0.9)]), careful=careful)
    h.loop.set_awake(True)

    h.feed(SENTENCE)

    assert len(careful.calls) == 1
    assert h.keys.calls == [("ctrl", "shift", "tab")]


@pytest.mark.parametrize(
    ("heard", "score"),
    [("Next tab.", -0.9), ("Open YouTube in a new tab please.", -0.1)],
    ids=["a known command, however unsure", "an unknown sentence heard with confidence"],
)
def test_clear_words_get_no_second_listen(tmp_path: Path, heard: str, score: float) -> None:
    careful = FakeSpeechProvider(["never used"])
    h = phrase_harness(tmp_path, [], stt=UnsureSpeech([(heard, score)]), careful=careful)
    h.loop.set_awake(True)

    h.feed(SENTENCE)

    assert careful.calls == []


def test_asleep_a_near_miss_of_the_wake_phrase_gets_a_second_listen(tmp_path: Path) -> None:
    careful = FakeSpeechProvider(["Wake up, EV."])
    h = phrase_harness(tmp_path, [], stt=UnsureSpeech([("Wait cup, a V.", -0.3)]), careful=careful)

    h.feed(SENTENCE)

    assert h.loop.awake and len(careful.calls) == 1


def test_asleep_the_rooms_chatter_never_wakes_the_larger_model(tmp_path: Path) -> None:
    careful = FakeSpeechProvider(["never used"])
    h = phrase_harness(
        tmp_path, [], stt=UnsureSpeech([("We should get groceries later.", -1.2)]), careful=careful
    )

    h.feed(SENTENCE)

    assert careful.calls == [] and not h.loop.awake


def test_a_click_or_a_cough_never_reaches_whisper(tmp_path: Path) -> None:
    """A fifth of what the real microphone sent was under a quarter second of sound."""
    h = phrase_harness(tmp_path, ["Thank you."])
    h.loop._blip_samples = RATE * 1400 // 1000  # lead-in 300 + silence 900 + 200 ms of sound

    h.feed(np.concatenate((silence(60), tone(90), silence(1200))))  # a 90 ms click

    assert h.stt.calls == []
