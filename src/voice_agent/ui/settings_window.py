"""The settings window: microphone, wake word, speech model, Groq on/off, and the API key.

Choices go to config/user.yaml (validated before writing); the key goes only to the secret
store and is never displayed again — the dialog shows whether one is saved, nothing more.
A new key is used at once; audio and model changes apply after a restart.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from voice_agent.core.exceptions import ConfigError
from voice_agent.core.models import AudioDevice
from voice_agent.settings.schema import Settings
from voice_agent.settings.secrets import (
    GROQ_KEY_TARGET,
    SecretStore,
    remove_groq_api_key,
    save_groq_api_key,
)

SYSTEM_DEFAULT = "System default"
WAKE_MODELS = ("hey_jarvis", "alexa", "hey_mycroft", "hey_rhasspy")
#: The 'wake word' choice meaning: use the wake and sleep phrases below (engine: phrase).
PHRASES = "Wake/sleep phrases"
#: English-only (.en) models hear English commands better at the same size and speed.
STT_MODELS = ("tiny.en", "base.en", "small.en", "tiny", "base", "small")
KEY_SAVED, NO_KEY = "Key saved in Windows Credential Manager", "No key saved"


class SettingsWindow(QDialog):
    def __init__(
        self,
        settings: Settings,
        devices: list[AudioDevice],
        store: SecretStore,
        save: Callable[[Mapping[str, Any]], object],
    ) -> None:
        super().__init__()
        self.setWindowTitle("Voice PC Agent — Settings")
        self._store = store
        self._save = save

        self.device = QComboBox()
        self.device.addItems([SYSTEM_DEFAULT, *(d.name for d in devices)])
        current = settings.audio.input_device
        self.device.setCurrentText(current if isinstance(current, str) else SYSTEM_DEFAULT)

        self.wake_enabled = QCheckBox("Listen for a wake word")
        self.wake_enabled.setChecked(settings.wakeword.enabled)
        wake = settings.wakeword
        self.wake_model = _combo(
            (PHRASES, *WAKE_MODELS), PHRASES if wake.engine == "phrase" else wake.model
        )
        self.wake_phrase = QLineEdit(wake.wake_phrase)
        self.sleep_phrase = QLineEdit(wake.sleep_phrase)
        self.stt_model = _combo(STT_MODELS, settings.speech.model)
        self.groq_enabled = QCheckBox("Ask Groq when a phrase is not a known command")
        self.groq_enabled.setChecked(settings.llm.enabled)

        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key.setPlaceholderText("Paste a new Groq API key")
        self.key_status = QLabel()
        remove = QPushButton("Remove key")
        remove.clicked.connect(self.remove_key)
        key_row = QHBoxLayout()
        key_row.addWidget(self.key_status)
        key_row.addWidget(remove)

        form = QFormLayout()
        form.addRow("Microphone", self.device)
        form.addRow("", self.wake_enabled)
        form.addRow("Wake word", self.wake_model)
        form.addRow("Wake phrase", self.wake_phrase)
        form.addRow("Sleep phrase", self.sleep_phrase)
        form.addRow("Speech model", self.stt_model)
        form.addRow("", self.groq_enabled)
        form.addRow("API key", self.api_key)
        form.addRow("", key_row)

        self.feedback = QLabel("Microphone and model changes apply after a restart.")
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Close
        )
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.feedback)
        layout.addWidget(buttons)
        self._show_key_status()

    def updates(self) -> dict[str, Any]:
        """The form as config overrides. The API key is deliberately not part of this."""
        device = self.device.currentText()
        return {
            "audio": {"input_device": None if device == SYSTEM_DEFAULT else device},
            "wakeword": self._wake_updates(),
            "speech": {"model": self.stt_model.currentText()},
            "llm": {"enabled": self.groq_enabled.isChecked()},
        }

    def _wake_updates(self) -> dict[str, Any]:
        choice = self.wake_model.currentText()
        updates: dict[str, Any] = {
            "enabled": self.wake_enabled.isChecked(),
            "engine": "phrase" if choice == PHRASES else "openwakeword",
            "wake_phrase": self.wake_phrase.text().strip(),
            "sleep_phrase": self.sleep_phrase.text().strip(),
        }
        if choice != PHRASES:
            updates["model"] = choice
        return updates

    def save(self) -> None:
        try:
            if self.api_key.text():
                save_groq_api_key(self._store, self.api_key.text())
            self._save(self.updates())
        except ConfigError as exc:
            self.feedback.setText(f"Not saved: {exc}")
            return
        finally:
            self.api_key.clear()  # a key never lingers in the widget, saved or not
        self._show_key_status()
        self.feedback.setText(
            "Saved. A new key works right away; microphone and model changes apply after a restart."
        )

    def done(self, result: int) -> None:
        """Every way out — Save, Close, Esc, the X — wipes a typed key from the widget."""
        self.api_key.clear()
        super().done(result)

    def remove_key(self) -> None:
        remove_groq_api_key(self._store)
        self._show_key_status()

    def _show_key_status(self) -> None:
        self.key_status.setText(KEY_SAVED if self._store.get(GROQ_KEY_TARGET) else NO_KEY)


def _combo(options: tuple[str, ...], current: str) -> QComboBox:
    box = QComboBox()
    box.addItems(list(dict.fromkeys((*options, current))))  # keep a custom value from config
    box.setCurrentText(current)
    return box
