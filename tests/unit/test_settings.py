"""Config loading: defaults <- user.yaml <- VOICE_AGENT__* env. Never touches the real env."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from voice_agent.core.exceptions import ConfigError
from voice_agent.settings.loader import DEFAULT_CONFIG_DIR, load_settings

DEFAULT_YAML = DEFAULT_CONFIG_DIR / "default.yaml"


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    shutil.copy(DEFAULT_YAML, tmp_path / "default.yaml")
    return tmp_path


def _user(config_dir: Path, text: str) -> None:
    (config_dir / "user.yaml").write_text(text, encoding="utf-8")


def test_shipped_default_round_trips_unchanged() -> None:
    settings = load_settings(env={})
    assert settings.model_dump() == yaml.safe_load(DEFAULT_YAML.read_text(encoding="utf-8"))


def test_user_yaml_overrides_default(config_dir: Path) -> None:
    _user(config_dir, "llm:\n  timeout_seconds: 6\n")
    settings = load_settings(config_dir, env={})
    assert settings.llm.timeout_seconds == 6
    assert settings.llm.max_retries == 1  # untouched siblings survive the merge


def test_env_beats_user_yaml_beats_default(config_dir: Path) -> None:
    _user(config_dir, "llm:\n  timeout_seconds: 6\n")
    env = {"VOICE_AGENT__LLM__TIMEOUT_SECONDS": "9", "VOICE_AGENT_ENV": "development"}
    assert load_settings(config_dir, env=env).llm.timeout_seconds == 9


def test_env_values_are_typed(config_dir: Path) -> None:
    env = {"VOICE_AGENT__LLM__ENABLED": "false", "VOICE_AGENT__AUDIO__INPUT_DEVICE": "3"}
    settings = load_settings(config_dir, env=env)
    assert settings.llm.enabled is False
    assert settings.audio.input_device == 3


def test_invalid_value_names_the_key_path(config_dir: Path) -> None:
    _user(config_dir, "llm:\n  timeout_seconds: soon\n")
    with pytest.raises(ConfigError, match=r"llm\.timeout_seconds"):
        load_settings(config_dir, env={})


def test_unknown_key_names_the_key_path(config_dir: Path) -> None:
    with pytest.raises(ConfigError, match=r"audio\.sample_rat"):
        load_settings(config_dir, env={"VOICE_AGENT__AUDIO__SAMPLE_RAT": "8000"})


def test_secret_in_yaml_is_rejected_and_not_echoed(config_dir: Path) -> None:
    _user(config_dir, "llm:\n  api_key: gsk_supersecretvalue123\n")
    with pytest.raises(ConfigError) as info:
        load_settings(config_dir, env={})
    assert "llm.api_key" in str(info.value)
    assert "supersecret" not in str(info.value)
    assert "supersecret" not in str(info.value.__cause__)


def test_arbitrary_shell_cannot_be_enabled(config_dir: Path) -> None:
    """Invariant 1 is not a preference: config cannot switch it off."""
    _user(config_dir, "safety:\n  allow_arbitrary_shell: true\n")
    with pytest.raises(ConfigError, match=r"safety\.allow_arbitrary_shell"):
        load_settings(config_dir, env={})


def test_malformed_yaml_raises_config_error(config_dir: Path) -> None:
    _user(config_dir, "llm: [unclosed\n")
    with pytest.raises(ConfigError, match=r"user\.yaml"):
        load_settings(config_dir, env={})


def test_env_override_below_a_scalar_is_rejected(config_dir: Path) -> None:
    with pytest.raises(ConfigError, match="VOICE_AGENT__LLM__MODEL__X"):
        load_settings(config_dir, env={"VOICE_AGENT__LLM__MODEL__X": "1"})


def test_missing_default_file_raises_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match=r"default\.yaml"):
        load_settings(tmp_path, env={})


def test_the_packaged_app_keeps_user_settings_outside_its_own_folder(
    monkeypatch: pytest.MonkeyPatch, config_dir: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """Unzipping a new version replaces the app folder; the user's settings must survive.
    (config_dir is a temp copy, so even a regression can't write into the repo.)"""
    import sys

    from voice_agent.settings.loader import save_user_settings

    appdata = tmp_path_factory.mktemp("appdata")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(appdata))

    save_user_settings({"wakeword": {"threshold": 0.45}}, config_dir)

    assert (appdata / "voice-pc-agent" / "user.yaml").is_file()
    assert not (config_dir / "user.yaml").exists()
    assert load_settings(config_dir, env={}).wakeword.threshold == 0.45


def test_wake_engine_and_phrases_are_validated(config_dir: Path) -> None:
    settings = load_settings(config_dir, env={})
    assert settings.wakeword.engine == "phrase"
    assert (settings.wakeword.wake_phrase, settings.wakeword.sleep_phrase) == (
        "wake up ev",
        "sleep ev",
    )
    with pytest.raises(ConfigError, match="engine"):
        load_settings(config_dir, env={"VOICE_AGENT__WAKEWORD__ENGINE": "porcupine"})
    with pytest.raises(ConfigError, match="differ"):
        load_settings(config_dir, env={"VOICE_AGENT__WAKEWORD__SLEEP_PHRASE": "Wake up, EV"})


def test_no_popup_after_every_command_by_default(config_dir: Path) -> None:
    assert load_settings(config_dir, env={}).ui.show_toast_on_action is False
