"""Settings: secrets go to the credential store, everything else to a validated user.yaml."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from voice_agent.core.exceptions import ConfigError
from voice_agent.core.models import AudioDevice
from voice_agent.settings.loader import DEFAULT_CONFIG_DIR, load_settings, save_user_settings
from voice_agent.settings.secrets import (
    GROQ_KEY_TARGET,
    groq_api_key,
    remove_groq_api_key,
    save_groq_api_key,
)

KEY = "gsk_" + "Fake0nlyForTests1234567890"


class FakeStore:
    def __init__(self) -> None:
        self.items: dict[str, str] = {}

    def get(self, target: str) -> str | None:
        return self.items.get(target)

    def set(self, target: str, value: str) -> None:
        self.items[target] = value

    def delete(self, target: str) -> None:
        self.items.pop(target, None)


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    shutil.copy(DEFAULT_CONFIG_DIR / "default.yaml", tmp_path / "default.yaml")
    return tmp_path


# --- secrets -------------------------------------------------------------------------------


def test_environment_key_wins_over_the_store() -> None:
    store = FakeStore()
    store.set(GROQ_KEY_TARGET, "gsk_stored")
    assert groq_api_key(store, env={"GROQ_API_KEY": "gsk_env"}) == "gsk_env"
    assert groq_api_key(store, env={}) == "gsk_stored"
    assert groq_api_key(FakeStore(), env={}) is None


def test_saved_key_is_trimmed_and_removable() -> None:
    store = FakeStore()
    save_groq_api_key(store, f"  {KEY}\n")
    assert store.items == {GROQ_KEY_TARGET: KEY}
    remove_groq_api_key(store)
    assert store.items == {}


@pytest.mark.parametrize("bad", ["", "   ", "gsk_two words"])
def test_obviously_broken_keys_are_refused_without_echoing_them(bad: str) -> None:
    with pytest.raises(ConfigError) as info:
        save_groq_api_key(FakeStore(), bad)
    assert "two words" not in str(info.value)


# --- user.yaml -----------------------------------------------------------------------------


def test_changes_are_merged_into_user_yaml_and_take_effect(config_dir: Path) -> None:
    save_user_settings({"speech": {"model": "small"}}, config_dir)
    save_user_settings({"llm": {"enabled": False}}, config_dir)
    written = yaml.safe_load((config_dir / "user.yaml").read_text(encoding="utf-8"))
    assert written == {"llm": {"enabled": False}, "speech": {"model": "small"}}
    settings = load_settings(config_dir, env={})
    assert (settings.speech.model, settings.llm.enabled) == ("small", False)


def test_an_invalid_change_is_refused_and_nothing_is_written(config_dir: Path) -> None:
    save_user_settings({"speech": {"model": "small"}}, config_dir)
    before = (config_dir / "user.yaml").read_text(encoding="utf-8")
    with pytest.raises(ConfigError, match=r"wakeword\.threshold"):
        save_user_settings({"wakeword": {"threshold": 7}}, config_dir)
    with pytest.raises(ConfigError, match=r"llm\.api_key"):
        save_user_settings({"llm": {"api_key": KEY}}, config_dir)  # secrets never in YAML
    assert (config_dir / "user.yaml").read_text(encoding="utf-8") == before
    assert not list(config_dir.glob("*.tmp"))


# --- the dialog ----------------------------------------------------------------------------

MICS = [
    AudioDevice(index=1, name="USB Mic", channels=1, default_samplerate=48000),
    AudioDevice(index=4, name="Mic Array (Realtek)", channels=2, default_samplerate=44100),
]


@pytest.fixture
def dialog(qapp: Any, config_dir: Path) -> tuple[Any, FakeStore, list[dict[str, Any]]]:
    from voice_agent.ui.settings_window import SettingsWindow

    saved: list[dict[str, Any]] = []
    store = FakeStore()

    def save(updates: dict[str, Any]) -> object:
        saved.append(updates)
        return save_user_settings(updates, config_dir)

    window = SettingsWindow(load_settings(config_dir, env={}), MICS, store, save=save)
    return window, store, saved


def test_dialog_shows_current_settings(dialog: tuple[Any, FakeStore, list[Any]]) -> None:
    window, _, _ = dialog
    assert window.device.currentText() == "System default"
    assert window.wake_enabled.isChecked()
    assert window.wake_model.currentText() == "Wake/sleep phrases"
    assert (window.wake_phrase.text(), window.sleep_phrase.text()) == ("wake up ev", "sleep ev")
    assert window.stt_model.currentText() == "base.en"
    assert window.groq_enabled.isChecked()
    assert window.key_status.text() == "No key saved"


def test_save_writes_choices_and_sends_the_key_only_to_the_store(
    dialog: tuple[Any, FakeStore, list[dict[str, Any]]],
) -> None:
    window, store, saved = dialog
    window.device.setCurrentText("Mic Array (Realtek)")
    window.wake_enabled.setChecked(False)
    window.stt_model.setCurrentText("small")
    window.groq_enabled.setChecked(False)
    window.api_key.setText(KEY)
    window.save()

    [updates] = saved
    assert updates == {
        "audio": {"input_device": "Mic Array (Realtek)"},
        "wakeword": {
            "enabled": False,
            "engine": "phrase",
            "wake_phrase": "wake up ev",
            "sleep_phrase": "sleep ev",
        },
        "speech": {"model": "small"},
        "llm": {"enabled": False},
    }
    assert KEY not in repr(updates)
    assert store.items == {GROQ_KEY_TARGET: KEY}
    assert window.api_key.text() == ""  # never left sitting in the widget
    assert window.key_status.text() == "Key saved in Windows Credential Manager"


def test_a_stored_key_is_never_displayed(qapp: Any, config_dir: Path) -> None:
    from voice_agent.ui.settings_window import SettingsWindow

    store = FakeStore()
    store.set(GROQ_KEY_TARGET, KEY)
    window = SettingsWindow(load_settings(config_dir, env={}), MICS, store, save=lambda u: u)
    assert window.api_key.text() == ""
    assert KEY not in window.key_status.text()
    window.remove_key()
    assert store.items == {}
    assert window.key_status.text() == "No key saved"


def test_an_invalid_save_is_reported_not_raised(
    dialog: tuple[Any, FakeStore, list[dict[str, Any]]],
) -> None:
    window, store, _ = dialog
    window.api_key.setText("gsk_two words")
    window.save()
    assert "Not saved" in window.feedback.text()
    assert store.items == {}


def test_a_typed_key_is_wiped_however_the_dialog_closes(
    dialog: tuple[Any, FakeStore, list[dict[str, Any]]],
) -> None:
    window, store, _ = dialog
    window.api_key.setText(KEY)
    window.reject()  # Close / Esc / the window's X
    assert window.api_key.text() == ""
    assert store.items == {}


def test_picking_a_wake_word_model_switches_engine(
    dialog: tuple[Any, FakeStore, list[dict[str, Any]]],
) -> None:
    window, _, saved = dialog
    window.wake_model.setCurrentText("hey_jarvis")
    window.save()

    assert saved[0]["wakeword"]["engine"] == "openwakeword"
    assert saved[0]["wakeword"]["model"] == "hey_jarvis"


def test_custom_phrases_are_saved_and_identical_ones_refused(
    dialog: tuple[Any, FakeStore, list[dict[str, Any]]],
) -> None:
    window, _, saved = dialog
    window.wake_phrase.setText("  hello agent ")
    window.sleep_phrase.setText("goodbye agent")
    window.save()
    assert saved[-1]["wakeword"]["wake_phrase"] == "hello agent"

    window.sleep_phrase.setText("Hello, agent")
    window.save()
    assert window.feedback.text().startswith("Not saved")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Credential Manager")
def test_the_vault_loads_the_module_reading_a_saved_key_needs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """pywin32 imports win32timezone behind the scenes once a key is saved; a frozen build
    left it out, so Settings and the cloud broke the moment a key existed. Imported up
    front, the build bundles it, and a build without it fails at once."""
    from voice_agent.settings.secrets import CredentialManagerStore

    monkeypatch.setitem(sys.modules, "win32timezone", None)
    with pytest.raises(ImportError):
        CredentialManagerStore()
