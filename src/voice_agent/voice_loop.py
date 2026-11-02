"""The live voice loop.

`VoiceLoop.on_frame` runs on the audio worker thread, one frame at a time:

    LISTENING  -> wake word detector       -> CAPTURING
    CAPTURING  -> utterance gate           -> PROCESSING
    PROCESSING -> transcript -> agent.handle (router -> policy -> dispatcher) -> COOLDOWN

With wake/sleep phrases (`wakeword.engine: phrase`) there is no wake model: the gate runs
all the time and any speech moves to CAPTURING. Asleep, a transcript is only checked for the
wake phrase - alone, or opening a command in the same breath - and otherwise dropped; awake,
every sentence is a command until the sleep phrase.

`listen` owns the microphone: it starts capture, waits, and on a lost device reports it,
re-scans hardware and starts again — until told to stop.
"""

from __future__ import annotations

import contextlib
import queue
import threading
from collections.abc import Callable

import numpy as np

from voice_agent.audio.audio_worker import AudioWorker
from voice_agent.audio.capture import AudioCapture
from voice_agent.audio.device_manager import DeviceManager
from voice_agent.audio.ring_buffer import RingBuffer
from voice_agent.audio.vad import UtteranceGate
from voice_agent.core.exceptions import AudioDeviceError, VoiceAgentError
from voice_agent.core.interfaces import WakeWordDetector
from voice_agent.core.models import ExecutionResult, ExecutionStatus, Transcript
from voice_agent.engine.result_handler import to_result
from voice_agent.nlu.router import MAX_COMMAND_WORDS
from voice_agent.speech.transcription_service import TranscriptionService
from voice_agent.wakeword.activation_controller import ActivationController, State
from voice_agent.wakeword.phrases import Phrase, WakePhrases

#: `detail["phase"]` of the results that report waking and falling asleep (for the chime).
AWAKE, ASLEEP = "awake", "asleep"

#: Below this, Whisper was guessing (Transcript.confidence): an unknown sentence heard this
#: unsurely is listened to again by the larger model before anything acts on it. Measured on
#: 618 quiet, noisy clips: heard right, the median was -0.26; misheard, mostly below -0.4.
#: Here 18 of 22 mishearings get the second listen, and under 4% of rewordings the cloud
#: would have understood anyway pay for one.
UNSURE = -0.35

NOTHING_HEARD = ExecutionResult(
    status=ExecutionStatus.UNRESOLVED, message="I didn't hear a command."
)


class AlwaysAwake:
    """WakeWordDetector for `wakeword.enabled: false`: every utterance is a command."""

    def process(self, frame: np.ndarray) -> bool:  # noqa: ARG002 - protocol signature
        return True

    def reset(self) -> None:
        return None


class VoiceLoop:
    def __init__(
        self,
        controller: ActivationController,
        wake: WakeWordDetector,
        gate: UtteranceGate,
        stt: TranscriptionService,
        handle: Callable[[str, Callable[[], bool]], ExecutionResult],
        on_wake: Callable[[], None] | None = None,
        on_result: Callable[[str, ExecutionResult], None] | None = None,
        phrases: WakePhrases | None = None,
        knows: Callable[[str], bool] = lambda _text: False,
        asleep_max_samples: int | None = None,
        blip_samples: int = 0,
    ) -> None:
        self._controller = controller
        self._phrases = phrases
        #: Local only: is this a command the agent recognises without the cloud?
        self._knows = knows
        #: Asleep, a longer stretch of speech can't be the wake phrase (TV, a conversation):
        #: skipping Whisper on it keeps the loop in real time, so the phrase isn't queued.
        self._asleep_max_samples = asleep_max_samples
        #: An utterance this short holds a click or a cough, not a word: a fifth of what the
        #: microphone sent. Each would cost Whisper a second and queue ahead of real speech.
        self._blip_samples = blip_samples
        #: Phrase mode: set between the wake phrase (or a click) and the sleep phrase.
        self._awake = threading.Event()
        self._wake = wake
        self._gate = gate
        self._stt = stt
        self._handle = handle
        self._on_wake = on_wake or (lambda: None)
        self._on_result = on_result or (lambda *_: None)
        self._talk_now = threading.Event()

    def talk_now(self) -> None:
        """Take the next command without the wake word (a click on the indicator or the tray
        dot). Picked up by the audio thread on its next frame: only that thread touches the
        utterance gate."""
        self._talk_now.set()

    def cancel_talk_now(self) -> None:
        self._talk_now.clear()

    @property
    def awake(self) -> bool:
        """Phrase mode: acting on every sentence. Always False in wake-word mode."""
        return self._awake.is_set()

    def set_awake(self, awake: bool) -> None:
        """A click, or a mute (which always sends it to sleep). Thread-safe."""
        if awake:
            self._awake.set()
        else:
            self._awake.clear()

    def on_frame(self, frame: np.ndarray) -> None:
        state = self._controller.state
        if state is State.LISTENING and self._phrases is not None:
            self._gate.process(frame)  # None, or empty after a quiet spell: nothing to do
            if self._gate.heard and self._controller.wake():  # speech: the pill shows it
                self._on_wake()
        elif state is State.LISTENING:
            clicked = self._talk_now.is_set()
            if clicked:
                self._talk_now.clear()
            if (clicked or self._wake.process(frame)) and self._controller.wake():
                self._gate.reset()
                self._on_wake()
        elif state is State.CAPTURING:
            utterance = self._gate.process(frame)
            if utterance is not None and self._controller.begin_processing():
                try:
                    self._process(utterance)
                finally:
                    self._controller.finish()
                    self._wake.reset()
        elif state is State.IDLE:
            self._gate.reset()  # muted: a half-captured command is discarded

    def _process(self, utterance: np.ndarray) -> None:
        quiet = self._phrases is not None  # continuous: silence and noise aren't news
        if utterance.size == 0:
            if not quiet:
                self._on_result("", NOTHING_HEARD)
            return
        cap = self._asleep_max_samples
        if quiet and not self.awake and cap is not None and utterance.size > cap:
            return
        if quiet and utterance.size <= self._blip_samples:
            return
        try:
            transcript = self._stt.transcribe(utterance)
        except VoiceAgentError as exc:
            self._on_result("", to_result(exc))
            return
        if self._worth_a_second_listen(transcript):
            with contextlib.suppress(VoiceAgentError):  # failing, the first transcript stands
                transcript = self._stt.transcribe_carefully(utterance) or transcript
        if not transcript.text:
            if not quiet:
                self._on_result("", NOTHING_HEARD)
            return
        if self._controller.state is not State.PROCESSING:
            return  # muted while transcribing: the kill switch wins (invariant 8)
        text: str | None = transcript.text
        if self._phrases is not None:
            text = self._command(self._phrases, transcript.text)
        if text is None:
            return

        # Re-checked inside dispatch too: the cloud call may take seconds (invariant 8).
        def still_wanted() -> bool:
            return self._controller.state is State.PROCESSING

        self._on_result(text, self._handle(text, still_wanted))

    def _worth_a_second_listen(self, transcript: Transcript) -> bool:
        """Whether the fast model's words are unclear enough to ask the larger one. Asleep:
        only when they nearly are the wake phrase (never for the room's chatter). Otherwise:
        when they are no command the agent knows and Whisper itself was unsure."""
        text = transcript.text
        if not self._stt.can_listen_carefully or not text:
            return False
        if len(text.split()) > MAX_COMMAND_WORDS or self._controller.state is not State.PROCESSING:
            return False
        if self._phrases is not None:
            if self._phrases.classify(text) is not None or self._phrases.wake_prefix(text):
                return False
            if not self.awake:
                return self._phrases.resembles_wake(text)
        if self._knows(text):
            return False
        return transcript.confidence is None or transcript.confidence < UNSURE

    def _command(self, phrases: WakePhrases, text: str) -> str | None:
        """Phrase mode: what to run, if anything. Asleep, only the wake phrase counts, alone
        or opening a command ("wake up EV, open chrome"); anything else goes nowhere - not to
        the agent, the cloud or the history. Awake, every sentence is a command, except the
        sleep phrase - unless it is a command itself ("sleep PC")."""
        said = phrases.classify(text)
        if said is Phrase.WAKE:
            self._wake_up(phrases)  # already awake: nothing to run, nothing to send
            return None
        prefix = phrases.wake_prefix(text)
        if prefix is not None:
            rest, sure = prefix
            if sure or self._knows(rest):  # a loose hearing wakes only for a real command
                self._wake_up(phrases)
                return rest
        if not self.awake:
            return None
        if said is Phrase.SLEEP and not self._knows(text):
            self._awake.clear()
            self._on_result("", _asleep_result(phrases))
            return None
        return text

    def _wake_up(self, phrases: WakePhrases) -> None:
        if not self.awake:
            self._awake.set()
            if self._controller.state is not State.PROCESSING:
                self._awake.clear()  # muted this very moment: a mute always sends it to sleep
                return
            self._on_result("", _awake_result(phrases))


def _awake_result(phrases: WakePhrases) -> ExecutionResult:
    message = f"Awake: every sentence is a command until you say '{phrases.sleep}'."
    return ExecutionResult(status=ExecutionStatus.SUCCESS, message=message, detail={"phase": AWAKE})


def _asleep_result(phrases: WakePhrases) -> ExecutionResult:
    message = f"Asleep: say '{phrases.wake}' to start again."
    return ExecutionResult(
        status=ExecutionStatus.SUCCESS, message=message, detail={"phase": ASLEEP}
    )


def listen(
    *,
    devices: DeviceManager,
    preference: int | str | None,
    capture: AudioCapture,
    ring: RingBuffer,
    frame_samples: int,
    on_frame: Callable[[np.ndarray], None],
    controller: ActivationController,
    stop: threading.Event,
    notify: Callable[[str], None],
    retry_s: float = 2.0,
) -> None:
    """Run until `stop` is set. A lost or missing microphone is reported, then retried."""
    errors: queue.Queue[VoiceAgentError] = queue.Queue()
    worker = AudioWorker(ring, frame_samples, on_frame, health=capture.check, on_error=errors.put)
    rescan = False
    try:
        while not stop.is_set():
            try:
                device = devices.reselect(preference) if rescan else devices.select(preference)
                capture.start(device)
            except AudioDeviceError as exc:
                notify(f"Microphone unavailable: {exc}. Retrying in {retry_s:g} s.")
                rescan = True
                stop.wait(retry_s)
                continue

            controller.start()
            worker.start()
            if controller.muted:  # e.g. restarted muted: say so, don't claim to listen
                notify(f"Muted: '{device.name}' is open, but nothing is heard.")
            else:
                notify(f"Listening on '{device.name}'.")
            error = _wait_for_error(errors, stop)
            controller.stop()  # first: cancels a pending confirmation the worker may sit in
            worker.stop()
            capture.stop()  # must be closed before the next re-scan
            if error is not None:
                notify(f"Microphone lost: {error}. Retrying in {retry_s:g} s.")
                rescan = True
                stop.wait(retry_s)
    finally:
        controller.stop()
        worker.stop()
        capture.stop()


def _wait_for_error(
    errors: queue.Queue[VoiceAgentError], stop: threading.Event
) -> VoiceAgentError | None:
    # Short timeouts keep the main thread responsive to Ctrl+C on Windows.
    while not stop.is_set():
        try:
            return errors.get(timeout=0.2)
        except queue.Empty:
            continue
    return None
