"""Typed configuration schema.

Every field is required: `config/default.yaml` is the single source of defaults, and the
schema only describes and bounds it. Unknown keys are rejected, which is also how secrets
are kept out of YAML — there is no field for an API key to land in.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from voice_agent.speech.text_normalizer import normalize


class _Section(BaseModel):
    # hide_input_in_errors: a mistyped secret must not be echoed back in the error text.
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)


class AppSettings(_Section):
    name: str
    start_minimized: bool
    emergency_stop_hotkey: str


class AudioSettings(_Section):
    sample_rate: int = Field(gt=0)
    channels: int = Field(ge=1)
    frame_ms: int = Field(gt=0)
    command_timeout_seconds: float = Field(gt=0)
    silence_timeout_ms: int = Field(gt=0)
    vad_energy_floor: float = Field(ge=0, le=1)
    vad_aggressiveness: int = Field(ge=0, le=3)
    input_device: int | str | None


class WakeWordSettings(_Section):
    enabled: bool
    #: phrase: say `wake_phrase` to start, then every sentence is a command until
    #: `sleep_phrase` (any words: the speech model hears them). openwakeword: say the
    #: `model` wake word before each command.
    engine: Literal["phrase", "openwakeword"]
    wake_phrase: str = Field(min_length=2, max_length=60)
    sleep_phrase: str = Field(min_length=2, max_length=60)
    model: str
    threshold: float = Field(ge=0, le=1)
    cooldown_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def _phrases_differ(self) -> WakeWordSettings:
        wake, sleep = normalize(self.wake_phrase), normalize(self.sleep_phrase)
        if not wake or not sleep or wake == sleep:
            msg = "wake_phrase and sleep_phrase must differ and contain words"
            raise ValueError(msg)
        return self


class SpeechSettings(_Section):
    provider: str
    model: str
    device: str
    compute_type: str
    language: str
    beam_size: int = Field(ge=1)
    #: A larger model for a second listen when the first was unclear; None: never.
    careful_model: str | None = None


class NluSettings(_Section):
    fuzzy_threshold: float = Field(ge=0, le=1)
    dedupe_window_ms: int = Field(ge=0)


class LlmSettings(_Section):
    enabled: bool
    provider: str
    model: str
    reasoning_effort: str
    timeout_seconds: float = Field(gt=0)
    max_retries: int = Field(ge=0)
    max_calls_per_minute: int = Field(ge=1)
    circuit_breaker_failures: int = Field(ge=1)
    fallback_to_rules: bool


class SafetySettings(_Section):
    reject_unknown_intents: bool
    require_confirmation_for_destructive_actions: bool
    confirmation_timeout_seconds: float = Field(gt=0)
    minimum_confidence: float = Field(ge=0, le=1)
    # Invariant 1: these exist so the intent is explicit, and can only ever be false.
    allow_arbitrary_shell: Literal[False]
    allow_arbitrary_python: Literal[False]


class PrivacySettings(_Section):
    store_audio: Literal[False]  # invariant 5: audio is never written to disk
    store_transcripts: bool
    redact_sensitive_logs: bool
    audit_log_enabled: bool


class PluginSettings(_Section):
    #: Folder names under plugins/ that may load. Anything else is never executed.
    enabled: list[str]


class UiSettings(_Section):
    show_microphone_indicator: bool
    show_toast_on_action: bool
    #: A short chime when the agent wakes up and another when it goes to sleep.
    sounds: bool = True


class Settings(_Section):
    app: AppSettings
    audio: AudioSettings
    wakeword: WakeWordSettings
    speech: SpeechSettings
    nlu: NluSettings
    llm: LlmSettings
    safety: SafetySettings
    privacy: PrivacySettings
    plugins: PluginSettings
    ui: UiSettings
