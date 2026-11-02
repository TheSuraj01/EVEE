"""The tray application: Qt on the main thread, the voice pipeline on its own thread.

The voice thread never touches a widget. It reports through queued Qt signals, and the
GUI polls the service for state. Confirmations are asked on the GUI thread while the voice
thread waits, with the same deadline as everywhere else: silence is never consent.
"""

from __future__ import annotations

import logging
import sys
import threading
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

from voice_agent.bootstrap import AUDIT_FILE
from voice_agent.core.models import (
    AudioDevice,
    CommandSpec,
    ExecutionResult,
    PluginLoadReport,
)
from voice_agent.observability.diagnostics import build_report
from voice_agent.observability.logging import LOG_DIR, LOG_FILE
from voice_agent.service import build_live_service, groq_key
from voice_agent.settings.loader import load_settings, save_user_settings
from voice_agent.settings.paths import DB_NAME, data_dir
from voice_agent.settings.schema import Settings
from voice_agent.storage.preferences import Preferences
from voice_agent.ui.hotkey import start_emergency_hotkey
from voice_agent.ui.status_indicator import StatusIndicator
from voice_agent.ui.tray import Tray
from voice_agent.voice_loop import ASLEEP, AWAKE

log = logging.getLogger(__name__)

#: Windows' own "Asterisk" and "Default Beep" sounds (MessageBeep types), as set in Sound.
WAKE_SOUND, SLEEP_SOUND = 0x40, 0x00


class UiBridge(QObject):
    """Carries events from the voice thread to the GUI thread."""

    message = Signal(str)
    result = Signal(str, object)


class _Reply:
    """One question's answer. Per call, so no other box or timer can ever answer it."""

    def __init__(self) -> None:
        self.done = threading.Event()
        self.yes = False


class QtConfirmPrompt(QObject):
    """ConfirmationPrompt for UI mode: the dialog runs on the GUI thread, the caller waits.

    Each confirm() gets its own reply, its box answers only that reply and only once, and a
    new question closes any box still open — a stale box or timer can never say yes for a
    later, different command.
    """

    _ask = Signal(str, float, object)
    _dismiss = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._box: QMessageBox | None = None
        self._ask.connect(self._show, Qt.ConnectionType.QueuedConnection)
        self._dismiss.connect(self._close_box, Qt.ConnectionType.QueuedConnection)

    def confirm(self, spec: CommandSpec, timeout_s: float) -> bool:
        reply = _Reply()
        self._ask.emit(spec.description or spec.intent, timeout_s, reply)
        answered = reply.done.wait(timeout_s)
        return answered and reply.yes

    def dismiss(self) -> None:
        """Take the open question off screen, as a "no". Safe from any thread."""
        self._dismiss.emit()

    @Slot()
    def _close_box(self) -> None:
        if self._box is not None:
            self._box.reject()  # its question is over; its own reply records a "no"

    @Slot(str, float, object)
    def _show(self, text: str, timeout_s: float, reply: _Reply) -> None:
        self._close_box()
        box = QMessageBox(
            QMessageBox.Icon.Question,
            "Voice PC Agent",
            f"{text}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        box.setDefaultButton(QMessageBox.StandardButton.No)
        box.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)

        def answered(_code: int) -> None:
            if not reply.done.is_set():  # first close wins; later rejects change nothing
                reply.yes = box.clickedButton() is box.button(QMessageBox.StandardButton.Yes)
                reply.done.set()

        box.finished.connect(answered)
        QTimer.singleShot(int(timeout_s * 1000), box, box.reject)  # no answer = no
        self._box = box  # keep it alive while open
        box.open()  # non-modal: no nested event loop inside a slot


def chime(result: ExecutionResult, beep: Callable[[int], object] | None = None) -> None:
    """Heard: a short sound when the agent wakes up and another when it falls asleep, so the
    wake and sleep phrases can be used without looking at the indicator."""
    sound = {AWAKE: WAKE_SOUND, ASLEEP: SLEEP_SOUND}.get(str(result.detail.get("phase")))
    if sound is None:
        return
    if beep is None:
        if sys.platform != "win32":
            return
        import winsound

        beep = winsound.MessageBeep  # asynchronous: never holds up the GUI thread
    beep(sound)


def describe_result(transcript: str, result: ExecutionResult, include_transcript: bool) -> str:
    """Toast text. Windows keeps notifications on disk, so the transcript is shown only when
    the user opted into storing transcripts (invariant 5)."""
    heard = f"“{transcript}” — " if transcript and include_transcript else ""
    return f"{heard}{result.message}"


def open_settings(current: QWidget | None, make: Callable[[], QWidget] | None = None) -> QWidget:
    """Show the settings window and return it, for the caller to keep. Already open: it comes
    to the front. Otherwise a fresh one, so it shows what was last saved.

    Non-modal and on top, never exec(): opened from the indicator, which never takes focus,
    Windows may put a new window behind the app you are in. A modal one hidden there blocked
    every other window of the agent — the indicator, its menu, and Settings again."""
    if current is None or not current.isVisible():
        current = (make or _settings_dialog)()
        current.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
    current.show()
    current.raise_()
    current.activateWindow()
    return current


def _settings_dialog() -> QWidget:  # pragma: no cover - real devices and credential vault
    from voice_agent.settings.secrets import CredentialManagerStore
    from voice_agent.ui.settings_window import SettingsWindow

    return SettingsWindow(
        load_settings(), _input_devices(), CredentialManagerStore(), save_user_settings
    )


def _input_devices() -> list[AudioDevice]:  # pragma: no cover - real audio stack
    from voice_agent.audio.device_manager import DeviceManager, SoundDeviceBackend

    try:
        return DeviceManager(SoundDeviceBackend()).inputs()
    except Exception:  # a broken audio stack must not block settings or diagnostics
        log.exception("could not list input devices")
        return []


def diagnostics_report(
    plugins: PluginLoadReport | None = None,
) -> str:  # pragma: no cover - real devices and credential vault
    return build_report(
        load_settings(),
        audit_path=AUDIT_FILE,
        log_path=LOG_FILE,
        devices=_input_devices(),
        key_configured=groq_key() is not None,
        plugins=plugins,
    )


def _open_folder(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve())))


def run_tray(settings: Settings, use_llm: bool) -> int:  # pragma: no cover - a desktop session
    existing = QApplication.instance()
    app = existing if isinstance(existing, QApplication) else QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)  # the tray outlives any dialog

    bridge = UiBridge()
    # Building the service can already report something (no cloud key), before anything is
    # connected to show it: a signal nobody listens to would drop it without a trace.
    held: list[str] = []

    def hold(message: str) -> None:
        held.append(message)

    bridge.message.connect(hold)
    service = build_live_service(
        settings,
        use_llm=use_llm,
        prompt=QtConfirmPrompt(),
        notify=bridge.message.emit,
        on_result=bridge.result.emit,
    )
    bridge.message.disconnect(hold)

    def clear_history() -> None:
        service.clear_history()
        tray.toast("Command history cleared. The audit log and log files are kept.")

    settings_window: list[QWidget] = []  # kept alive while open; at most one

    def show_settings() -> None:
        try:
            settings_window[:] = [open_settings(settings_window[0] if settings_window else None)]
        except Exception:  # a Qt slot: an error here would otherwise vanish without a trace
            log.exception("could not open settings")
            tray.toast("Could not open Settings. The log has the details.")

    def copy_diagnostics() -> None:
        QGuiApplication.clipboard().setText(diagnostics_report(service.agent.plugins))
        tray.toast("Diagnostics copied. Review them before sharing.")

    tray = Tray(
        service,
        on_settings=show_settings,
        on_logs=lambda: _open_folder(LOG_DIR),
        on_quit=app.quit,
        on_clear_history=clear_history,
        on_copy_diagnostics=copy_diagnostics,
    )
    bridge.message.connect(tray.set_status)
    bridge.message.connect(lambda message: log.info("%s", message))
    if settings.ui.sounds:
        # Wake and sleep are reported with no transcript; a command's result never is.
        bridge.result.connect(lambda transcript, result: None if transcript else chime(result))
    if settings.ui.show_toast_on_action:
        bridge.result.connect(
            lambda transcript, result: tray.toast(
                describe_result(transcript, result, settings.privacy.store_transcripts)
            )
        )

    indicator = None
    if settings.ui.show_microphone_indicator:
        layout = Preferences(data_dir() / DB_NAME)
        indicator = StatusIndicator(
            service,
            menu=tray.menu,
            on_moved=lambda pos: layout.set("indicator_position", list(pos)),
        )
        indicator.show()
        saved = layout.get("indicator_position", None)
        indicator.place(tuple(saved) if isinstance(saved, list) and len(saved) == 2 else None)
    hotkey, notice = start_emergency_hotkey(
        settings.app.emergency_stop_hotkey, service.emergency_stop
    )
    tray.show_stop_key(hotkey.spec if hotkey is not None else None)
    if hotkey is None and notice is not None:
        # Only when there is no hotkey at all; a fallback key is named in the menu instead
        # of a popup at every start.
        tray.toast(f"{notice} Use the menu's Emergency stop.")

    for name, reason in service.agent.plugins.rejected.items():
        # The tooltip only keeps the latest status line; this must not scroll away.
        tray.toast(f"Plugin '{name}' was not loaded: {reason}")
    for note in held:
        log.warning("%s", note)
        tray.toast(note)

    service.start()
    try:
        return int(app.exec())
    finally:
        if hotkey is not None:
            hotkey.stop()
        service.stop()
