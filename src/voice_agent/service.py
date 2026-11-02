"""The live agent as one object that the console and the tray both drive.

Building it is cheap. `start()` returns at once and runs everything slow — loading (and on
first run downloading) the speech models, then listening — on its own thread. `mute`,
`unmute` and `emergency_stop` are safe from any thread (invariant 8).
"""

from __future__ import annotations

import functools
import logging
import sys
import threading
from collections.abc import Callable
from pathlib import Path

from dotenv import load_dotenv

from voice_agent.audio.capture import AudioCapture
from voice_agent.audio.device_manager import DeviceManager
from voice_agent.audio.ring_buffer import RingBuffer
from voice_agent.audio.vad import PRE_ROLL_MS, SpeechDetector, UtteranceGate
from voice_agent.bootstrap import AUDIT_FILE, Agent, SystemSeams, build_agent
from voice_agent.core.interfaces import (
    Clock,
    ConfirmationPrompt,
    KeySender,
    SpeechProvider,
    StreamOpener,
    SystemClock,
    WakeWordDetector,
    WindowBackend,
)
from voice_agent.core.models import ExecutionResult
from voice_agent.llm.groq_qwen_provider import ChatTransport, KeyedTransport
from voice_agent.settings.loader import DEFAULT_CONFIG_DIR
from voice_agent.settings.paths import DB_NAME, data_dir
from voice_agent.settings.schema import Settings
from voice_agent.settings.secrets import SecretStore
from voice_agent.speech.transcription_service import TranscriptionService
from voice_agent.storage.command_history import CommandHistory
from voice_agent.storage.preferences import Preferences
from voice_agent.voice_loop import AlwaysAwake, VoiceLoop, listen
from voice_agent.wakeword.activation_controller import ActivationController, State
from voice_agent.wakeword.phrases import WakePhrases

log = logging.getLogger(__name__)

#: Seconds of audio the ring buffer holds while the worker is busy transcribing.
RING_SECONDS = 10
#: Two clicks closer than this are one double-click: it counts once.
DOUBLE_CLICK_MS = 500
#: Asleep, utterances longer than this (with the gate's lead-in and tail) are not
#: transcribed: room for "wake up EV, open Visual Studio Code", not for a TV programme.
ASLEEP_MAX_SECONDS = 6
#: Less sound than this is a click, a tap or a cough: even a quick "Play." is longer.
BLIP_MS = 200


class VoiceService:
    def __init__(
        self,
        settings: Settings,
        *,
        keys: KeySender,
        windows: WindowBackend,
        prompt: ConfirmationPrompt | None,
        llm: ChatTransport | None,
        stt: SpeechProvider,
        wake: WakeWordDetector | None,
        careful_stt: SpeechProvider | None = None,
        detector: SpeechDetector,
        devices: DeviceManager,
        open_stream: StreamOpener,
        notify: Callable[[str], None],
        on_result: Callable[[str, ExecutionResult], None],
        on_wake: Callable[[], None] | None = None,
        audit_path: Path = AUDIT_FILE,
        clock: Clock | None = None,
        preferences: Preferences | None = None,
        history: CommandHistory | None = None,
        seams: SystemSeams | None = None,
    ) -> None:
        audio = settings.audio
        self._preferences = preferences
        self._history = history
        self._frame_samples = audio.sample_rate * audio.frame_ms // 1000
        self._preference = audio.input_device
        self._devices = devices
        self._notify = notify
        self._warm_ups: list[Callable[[], None]] = [stt.warm_up]
        if careful_stt is not None:  # loaded up front: a first slow listen must not stall
            self._warm_ups.append(careful_stt.warm_up)
        if wake is not None and hasattr(wake, "warm_up"):
            self._warm_ups.insert(0, wake.warm_up)

        wake_cfg = settings.wakeword
        self._phrases = (
            WakePhrases(wake_cfg.wake_phrase, wake_cfg.sleep_phrase)
            if wake_cfg.enabled and wake_cfg.engine == "phrase"
            else None
        )
        # The cooldown swallows a wake model's echo triggers; phrase mode has no wake model,
        # and a cooldown there would only make it deaf between consecutive commands.
        cooldown_ms = 0 if self._phrases is not None else wake_cfg.cooldown_ms
        self.controller = ActivationController(cooldown_ms, clock=clock)
        self._clock: Clock = clock or SystemClock()
        self._last_tap_ms = float("-inf")
        self.agent: Agent = build_agent(
            settings,
            keys=keys,
            windows=windows,
            prompt=prompt,
            audit_path=audit_path,
            llm=llm,
            # Key loops stop the moment this command is no longer being processed: a mute,
            # an emergency stop, or a mute+unmute in between (same test as still_wanted).
            interrupted=lambda: self.controller.state is not State.PROCESSING,
            clock=clock,
            seams=seams,
        )
        if self.agent.confirmation is not None:
            # Mute, stop and the emergency stop all deny a confirmation that is already up.
            self.controller.on_mute = self.agent.confirmation.cancel

        def report(transcript: str, result: ExecutionResult) -> None:
            if history is not None:
                history.record(transcript, result)
            on_result(transcript, result)

        self._loop = VoiceLoop(
            self.controller,
            wake or AlwaysAwake(),
            UtteranceGate(
                detector,
                audio.frame_ms,
                audio.silence_timeout_ms,
                int(audio.command_timeout_seconds * 1000),
            ),
            TranscriptionService(stt, audio.sample_rate, clock, careful=careful_stt),
            self.agent.handle,
            on_wake=on_wake,
            on_result=report,
            phrases=self._phrases,
            knows=self.agent.knows,
            asleep_max_samples=audio.sample_rate * ASLEEP_MAX_SECONDS,
            # The gate's lead-in and closing silence, plus under BLIP_MS of sound.
            blip_samples=audio.sample_rate
            * (PRE_ROLL_MS + audio.silence_timeout_ms + BLIP_MS)
            // 1000,
        )
        self._ring = RingBuffer(audio.sample_rate * RING_SECONDS)
        self._capture = AudioCapture(
            self._ring, open_stream, audio.sample_rate, self._frame_samples, clock=clock
        )
        self._stop = threading.Event()
        self._loading = threading.Event()
        self._thread: threading.Thread | None = None
        #: Mute (hotkey thread) and unmute (Qt thread) each make two writes; without this
        #: an interleaving could leave the UI muted with the microphone flowing again.
        self._mute_lock = threading.Lock()
        if preferences is not None and preferences.get("muted", False):
            self._silence()  # muted (or stopped) last time: privacy-safe restart

    @property
    def state(self) -> State:
        return self.controller.state

    @property
    def muted(self) -> bool:
        return self.controller.muted

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def loading(self) -> bool:
        """Models are loading (minutes on a first run, which downloads them); the microphone
        isn't open yet, which is not the same as it being lost."""
        return self._loading.is_set()

    def start(self) -> None:
        """Load models and listen, on a background thread. Returns immediately."""
        if self.running:
            return
        self._stop.clear()
        self._loading.set()
        self._thread = threading.Thread(target=self._run, name="voice-service", daemon=True)
        self._thread.start()

    def stop(self, timeout_s: float = 5.0) -> None:
        self._stop.set()
        self.controller.stop()  # cancels a pending confirmation before we wait on anything
        self.join(timeout_s)

    def join(self, timeout_s: float) -> None:
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout_s)

    def mute(self) -> None:
        self._silence()
        self._remember_muted(True)

    def unmute(self) -> None:
        with self._mute_lock:
            self._capture.unmute()
            self.controller.unmute()
        self._remember_muted(False)

    @property
    def asleep(self) -> bool:
        """Phrase mode, between the sleep phrase and the wake phrase: speech is only checked
        for the wake phrase. Never True in wake-word mode."""
        return self._phrases is not None and not self._loop.awake

    def tap(self) -> None:
        """A click on the indicator or the tray dot. Phrase mode: wake up, or go to sleep
        (muted: listen again, awake). Wake-word mode: listen again if muted, else take a
        command now without the wake word. A second click within DOUBLE_CLICK_MS is the same
        click, so a double-click can't undo itself."""
        now = self._clock.monotonic_ms()
        if now - self._last_tap_ms < DOUBLE_CLICK_MS:
            return
        self._last_tap_ms = now
        if self._phrases is not None:
            awake = self.muted or not self._loop.awake
            if self.muted:
                self.unmute()
            self._loop.set_awake(awake)
        elif self.muted:
            self.unmute()
        elif self.state in (State.LISTENING, State.COOLDOWN):
            self._loop.talk_now()

    def emergency_stop(self) -> None:
        """Kill switch: stop listening, deny any pending confirmation, stay muted until the
        user unmutes. Callable from any thread — the hotkey thread, the tray, Ctrl+C."""
        self._silence()
        self._remember_muted(True)
        self._notify("Emergency stop — muted. Unmute to listen again.")

    def clear_history(self) -> None:
        """One-click purge of the opted-in command history."""
        if self._history is not None:
            self._history.purge()

    def _silence(self) -> None:
        """Drop microphone samples in the audio callback itself (nothing new reaches memory),
        wipe what was already buffered, then stop the state machine, cancelling a pending
        command or confirmation."""
        with self._mute_lock:
            self._capture.mute()
            self._ring.clear()
            self._loop.cancel_talk_now()  # a click not yet taken up must not fire after unmute
            self.controller.mute()
            self._loop.set_awake(False)  # after the mute: a wake racing it can't outlive it

    def _remember_muted(self, muted: bool) -> None:
        if self._preferences is not None:
            self._preferences.set("muted", muted)

    def _run(self) -> None:
        for name, reason in self.agent.plugins.rejected.items():
            self._notify(f"Plugin '{name}' was not loaded: {reason}")
        self._notify("Loading speech models...")
        try:
            for warm_up in self._warm_ups:
                warm_up()
        except Exception as exc:  # startup boundary: report it; nothing is listening yet
            self._notify(f"Could not load the speech models: {exc}")
            return
        finally:
            self._loading.clear()
        if self._stop.is_set():
            return
        self._notify("Ready.")
        listen(
            devices=self._devices,
            preference=self._preference,
            capture=self._capture,
            ring=self._ring,
            frame_samples=self._frame_samples,
            on_frame=self._loop.on_frame,
            controller=self.controller,
            stop=self._stop,
            notify=self._notify,
        )


def groq_key() -> str | None:  # pragma: no cover - reads .env and the credential vault
    """The one lookup the app and its diagnostics share: .env/environment, then the vault."""
    from voice_agent.settings.secrets import groq_api_key

    load_dotenv(DEFAULT_CONFIG_DIR.parent / ".env")
    return groq_api_key(_vault())


@functools.cache
def _vault() -> SecretStore | None:  # pragma: no cover - the credential vault
    """The credential vault, or None without pywin32 (environment / .env only). Looked up
    once: the key itself is read again on every call."""
    from voice_agent.settings.secrets import CredentialManagerStore

    try:
        return CredentialManagerStore()
    except ImportError:
        if sys.platform == "win32":  # on Windows it is a broken build: say so, once
            log.exception("Windows Credential Manager can't be read; a saved key is ignored")
        return None


def cloud_transport(
    settings: Settings, notify: Callable[[str], None]
) -> ChatTransport | None:  # pragma: no cover - reads .env, builds an HTTP client
    """Groq when enabled, else None. Without a key yet, a note — and the key is looked up
    again on each call, so one saved in Settings works at once. Local commands work either
    way (invariant 4). The key comes from the environment, .env or the vault, never config."""
    if not settings.llm.enabled:
        return None
    if groq_key() is None:
        notify(
            "No Groq API key yet: add one in Settings. Until then only the listed command "
            "phrases work, not other ways of saying them, and no apps or websites by name."
        )
    return KeyedTransport(groq_key, timeout_s=settings.llm.timeout_seconds)


def build_live_service(  # pragma: no cover - real microphone, models, desktop
    settings: Settings,
    *,
    use_llm: bool,
    prompt: ConfirmationPrompt,
    notify: Callable[[str], None],
    on_result: Callable[[str, ExecutionResult], None],
    on_wake: Callable[[], None] | None = None,
) -> VoiceService:
    """VoiceService over the real backends. Constructors are cheap; nothing loads yet."""
    import time

    from voice_agent.actions.apps import launch_app, start_menu_apps
    from voice_agent.actions.keyboard import PyAutoGuiKeySender
    from voice_agent.actions.power import english_ui, suspend
    from voice_agent.actions.web import open_in_browser
    from voice_agent.actions.windows import Win32WindowBackend
    from voice_agent.audio.capture import open_sounddevice_stream
    from voice_agent.audio.device_manager import SoundDeviceBackend
    from voice_agent.audio.vad import webrtc_detector
    from voice_agent.speech.faster_whisper_provider import FasterWhisperProvider
    from voice_agent.wakeword.openwakeword_adapter import OpenWakeWordDetector

    audio, wake_cfg, speech = settings.audio, settings.wakeword, settings.speech
    return VoiceService(
        settings,
        keys=PyAutoGuiKeySender(),
        windows=Win32WindowBackend(),
        prompt=prompt,
        llm=cloud_transport(settings, notify) if use_llm else None,
        stt=FasterWhisperProvider(
            speech.model, speech.device, speech.compute_type, speech.language, speech.beam_size
        ),
        careful_stt=(
            FasterWhisperProvider(
                speech.careful_model,
                speech.device,
                speech.compute_type,
                speech.language,
                speech.beam_size,
            )
            if speech.careful_model
            else None
        ),
        wake=(
            OpenWakeWordDetector(wake_cfg.model, wake_cfg.threshold)
            if wake_cfg.enabled and wake_cfg.engine == "openwakeword"
            else None  # phrase mode needs no wake model: nothing to download or run
        ),
        detector=webrtc_detector(
            audio.sample_rate, audio.vad_aggressiveness, audio.vad_energy_floor
        ),
        devices=DeviceManager(SoundDeviceBackend()),
        open_stream=open_sounddevice_stream,
        notify=notify,
        on_result=on_result,
        on_wake=on_wake,
        preferences=Preferences(data_dir() / DB_NAME),
        history=CommandHistory(data_dir() / DB_NAME, enabled=settings.privacy.store_transcripts),
        seams=SystemSeams(
            apps=_start_menu_apps(start_menu_apps),
            launch_app=launch_app,
            open_address=open_in_browser,
            suspend=suspend,
            pause=time.sleep,
            english_ui=english_ui,
        ),
    )


def _start_menu_apps(read: Callable[[], dict[str, str]]) -> dict[str, str]:
    """No Start menu (a broken shell, no pywin32) means no app.open, not no agent."""
    try:
        return read()
    except Exception:  # library boundary: COM and the Windows shell
        log.exception("could not read the Start menu; 'open <app>' is unavailable")
        return {}
