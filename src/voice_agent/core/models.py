"""Typed payloads that cross module boundaries.

Everything here is immutable on purpose: a command travelling through the pipeline must
not be mutated by a later stage, so an audit entry always describes what really happened.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

NO_ACTION_INTENT = "system.no_action"


class Risk(StrEnum):
    """How much damage a command can do if it fires when the user did not mean it."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

    @property
    def rank(self) -> int:
        return {"low": 0, "medium": 1, "high": 2}[self.value]


class IntentSource(StrEnum):
    """Where the intent came from. Used by the audit log and the confidence policy."""

    ALIAS = "alias"
    LLM = "llm"
    MANUAL = "manual"


class Decision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    CONFIRM = "confirm"


class ExecutionStatus(StrEnum):
    SUCCESS = "success"
    DENIED = "denied"
    DUPLICATE = "duplicate"
    UNRESOLVED = "unresolved"
    ERROR = "error"


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


#: An hour: no voice command needs a larger number, and a plugin cannot declare one.
MAX_ARGUMENT = 3600


class ArgumentSpec(_Frozen):
    """Bounds for the single optional numeric argument a command may take."""

    name: str
    required: bool = False
    minimum: int = Field(default=0, ge=0)
    maximum: int = Field(default=600, le=MAX_ARGUMENT)
    default: int | None = None

    @model_validator(mode="after")
    def _consistent(self) -> ArgumentSpec:
        """The policy engine hands `default` out unchecked, so it must be in range here."""
        if self.minimum > self.maximum:
            msg = f"minimum {self.minimum} is above maximum {self.maximum}"
            raise ValueError(msg)
        if self.default is not None and not self.minimum <= self.default <= self.maximum:
            msg = f"default {self.default} is outside {self.minimum}-{self.maximum}"
            raise ValueError(msg)
        return self


#: Longer text is refused outright, before any check looks at it.
MAX_TEXT_ARGUMENT = 300


class TextArgument(_Frozen):
    """A command's text argument: one name out of `choices` (an app), or a web address
    (`url`). The policy engine checks it; the executor only ever sees the checked value."""

    kind: Literal["choice", "url"]
    choices: tuple[str, ...] = ()
    #: How it is said locally, "{}" marking the value ("open {}"). `choice` only: a web
    #: address is not something a phrase table can recognise.
    say: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _consistent(self) -> TextArgument:
        if self.kind == "url" and (self.choices or self.say):
            msg = "a url argument takes no choices and no phrasings"
            raise ValueError(msg)
        if any(pattern.count("{}") != 1 for pattern in self.say):
            msg = "each phrasing must contain '{}' exactly once"
            raise ValueError(msg)
        return self


class CommandSpec(_Frozen):
    """The registry entry for one command. The allowlist is the set of these."""

    intent: str
    description: str = ""
    aliases: tuple[str, ...] = ()
    risk: Risk = Risk.LOW
    permissions: tuple[str, ...] = ()
    argument: ArgumentSpec | None = None
    text: TextArgument | None = None
    plugin_id: str = "builtin"

    @field_validator("intent")
    @classmethod
    def _dotted(cls, value: str) -> str:
        if "." not in value or value != value.strip().lower():
            msg = f"intent must be lowercase dotted, got {value!r}"
            raise ValueError(msg)
        return value


class IntentProposal(_Frozen):
    """What a resolver (alias matcher or LLM) *suggests*. Never authoritative."""

    intent: str
    argument_seconds: int | None = None
    argument_text: str | None = Field(default=None, max_length=MAX_TEXT_ARGUMENT)
    requires_confirmation: bool = False
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source: IntentSource = IntentSource.ALIAS
    raw: str | None = None


class PolicyVerdict(_Frozen):
    decision: Decision
    reason: str
    spec: CommandSpec | None = None
    argument: int | None = None
    text: str | None = None

    @property
    def allowed(self) -> bool:
        return self.decision is Decision.ALLOW


class ResolvedCommand(_Frozen):
    """A proposal that survived the policy engine and may be executed.

    Deliberately carries no transcript: executors — plugins included — act on the intent
    and its validated argument, and never see what was said (invariant 5)."""

    spec: CommandSpec
    argument: int | None = None
    #: The checked text argument: an app name from the spec's choices, or a vetted https
    #: address. Never the transcript.
    text: str | None = None
    source: IntentSource = IntentSource.ALIAS


class AudioDevice(_Frozen):
    """An input device as the OS reports it. `index` is only valid until devices change."""

    index: int
    name: str
    channels: int = Field(ge=1)
    default_samplerate: float


class Transcript(_Frozen):
    """Speech-to-text output for one utterance: normalised text plus how long it took."""

    text: str
    latency_ms: float
    #: How sure the speech model was: its lowest per-segment average log-probability (0 is
    #: certain; below about -0.5 it is often guessing). None when the provider can't say.
    confidence: float | None = None


class HistoryEntry(_Frozen):
    """One opted-in command-history row: what was heard and what happened."""

    ts: str
    transcript: str
    intent: str
    status: str
    message: str


class PluginLoadReport(_Frozen):
    """Which plugins run, which were only discovered, and which were refused (and why)."""

    loaded: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()
    rejected: dict[str, str] = Field(default_factory=dict)


class ExecutionResult(_Frozen):
    """The only thing the UI layer ever sees. Messages here are user-safe."""

    status: ExecutionStatus
    intent: str = NO_ACTION_INTENT
    message: str = ""
    detail: dict[str, Any] = Field(default_factory=dict)
    latency_ms: float | None = None

    @property
    def ok(self) -> bool:
        return self.status is ExecutionStatus.SUCCESS

    @classmethod
    def unresolved(cls, transcript: str) -> ExecutionResult:
        return cls(
            status=ExecutionStatus.UNRESOLVED,
            message="I didn't recognise that command.",
            detail={"transcript_length": len(transcript)},
        )
