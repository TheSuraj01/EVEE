"""Shared fakes. No test in this suite may touch a microphone, a model or the network."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import numpy as np
import pytest

from voice_agent.core.interfaces import AudioCallback, AudioStream
from voice_agent.core.models import (
    ArgumentSpec,
    AudioDevice,
    CommandSpec,
    ExecutionResult,
    ExecutionStatus,
    IntentProposal,
    ResolvedCommand,
    Risk,
)
from voice_agent.engine.command_registry import CommandRegistry


class FakeClock:
    def __init__(self, start_ms: float = 0.0) -> None:
        self.now = start_ms

    def monotonic_ms(self) -> float:
        return self.now

    def advance(self, ms: float) -> None:
        self.now += ms


class RecordingHandler:
    def __init__(self, raises: Exception | None = None) -> None:
        self.calls: list[ResolvedCommand] = []
        self._raises = raises

    def execute(self, command: ResolvedCommand) -> ExecutionResult:
        self.calls.append(command)
        if self._raises is not None:
            raise self._raises
        return ExecutionResult(status=ExecutionStatus.SUCCESS, intent=command.spec.intent)


class AlwaysConfirm:
    def __init__(self, answer: bool = True) -> None:
        self.answer = answer
        self.asked: list[CommandSpec] = []

    def confirm(self, spec: CommandSpec, timeout_s: float) -> bool:
        self.asked.append(spec)
        return self.answer


class RecordingKeySender:
    """Stands in for pyautogui. Records every chord instead of typing into the desktop."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def press(self, *keys: str) -> None:
        self.calls.append(keys)

    def hotkey(self, *keys: str) -> None:
        self.calls.append(keys)


class RecordingWindowBackend:
    """Stands in for pywin32 so window tests run on any OS without touching real windows."""

    def __init__(
        self, raises: Exception | None = None, foreground: int = 42, activates: bool = True
    ) -> None:
        self.calls: list[str] = []
        #: The foreground handle at the moment each operation ran.
        self.targets: list[int] = []
        self.activated: list[int] = []
        self.current = foreground
        self._raises = raises
        self._activates = activates

    def _record(self, name: str) -> None:
        self.calls.append(name)
        self.targets.append(self.current)
        if self._raises is not None:
            raise self._raises

    def foreground(self) -> int:
        return self.current

    def activate(self, handle: int) -> bool:
        self.activated.append(handle)
        if self._activates:
            self.current = handle
        return self._activates

    def minimize(self) -> None:
        self._record("minimize")

    def maximize(self) -> None:
        self._record("maximize")

    def close(self) -> None:
        self._record("close")


class FakeSpeechProvider:
    """Stands in for Whisper everywhere in tests: returns scripted transcripts, in order."""

    def __init__(self, transcripts: list[str] | None = None) -> None:
        self.transcripts = list(transcripts or [])
        self.calls: list[tuple[int, int]] = []
        self.warmed = False

    def transcribe(self, audio: np.ndarray, sample_rate: int) -> str:
        self.calls.append((audio.size, sample_rate))
        return self.transcripts.pop(0) if self.transcripts else ""

    def warm_up(self) -> None:
        self.warmed = True


class FakeDeviceBackend:
    def __init__(self, devices: list[AudioDevice], default: int | None = None) -> None:
        self.devices = devices
        self.default = default
        self.refreshed = 0

    def inputs(self) -> list[AudioDevice]:
        return list(self.devices)

    def default_input(self) -> int | None:
        return self.default

    def refresh(self) -> None:
        self.refreshed += 1


class FakeStream:
    def __init__(self) -> None:
        self.active = False
        self.closed = False

    def start(self) -> None:
        self.active = True

    def stop(self) -> None:
        self.active = False

    def close(self) -> None:
        self.closed = True


class FakeOpener:
    def __init__(self, error: Exception | None = None) -> None:
        self.stream = FakeStream()
        self.callback: AudioCallback | None = None
        self.args: tuple[Any, ...] = ()
        self._error = error

    def __call__(
        self, device: AudioDevice, sample_rate: int, block_samples: int, callback: AudioCallback
    ) -> AudioStream:
        if self._error is not None:
            raise self._error
        self.args = (device, sample_rate, block_samples)
        self.callback = callback
        return self.stream

    def feed(self, samples: np.ndarray) -> None:
        """Play the PortAudio thread: deliver one block of mono audio."""
        assert self.callback is not None
        self.callback(samples.reshape(-1, 1), len(samples), None, None)


class FakeTransport:
    """Stands in for Groq. Returns canned JSON strings, or raises."""

    def __init__(self, payloads: list[str] | None = None, error: Exception | None = None) -> None:
        self.payloads = payloads or []
        self.error = error
        self.calls: list[dict] = []

    def complete(self, **kwargs: object) -> str:
        self.calls.append(dict(kwargs))
        if self.error is not None:
            raise self.error
        return self.payloads.pop(0) if self.payloads else "{}"


@dataclass
class Shipped:
    """The real agent — built-ins plus the enabled plugins — over recording backends."""

    agent: Any
    keys: RecordingKeySender
    windows: RecordingWindowBackend

    def dispatch(self, intent: str, seconds: int | None = None) -> ExecutionResult:
        return self.agent.dispatcher.dispatch(
            IntentProposal(intent=intent, argument_seconds=seconds)
        )


@pytest.fixture
def shipped_with(tmp_path: Any) -> Any:
    from voice_agent.bootstrap import build_agent
    from voice_agent.settings.loader import load_settings

    def build(enabled: list[str] | None = None) -> Shipped:
        env = {} if enabled is None else {"VOICE_AGENT__PLUGINS__ENABLED": str(enabled)}
        keys, windows = RecordingKeySender(), RecordingWindowBackend()
        agent = build_agent(
            load_settings(env=env), keys=keys, windows=windows, audit_path=tmp_path / "a.jsonl"
        )
        return Shipped(agent, keys, windows)

    return build


@pytest.fixture
def shipped(shipped_with: Any) -> Shipped:
    return shipped_with()  # type: ignore[no-any-return]


@pytest.fixture(autouse=True)
def _private_instance_mutex(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests never hold the real single-instance mutex: a running agent would make them see
    'already running', and a test run would stop the real app from starting."""
    import uuid

    monkeypatch.setattr("voice_agent.main.INSTANCE_MUTEX", f"Local\\VoicePCAgent-{uuid.uuid4()}")


@pytest.fixture(scope="session")
def qapp() -> Any:
    """A headless QApplication; UI tests skip where PySide6 is not installed (CI)."""
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def registry() -> CommandRegistry:
    reg = CommandRegistry()
    reg.register_all(
        [
            CommandSpec(
                intent="browser.next_tab",
                description="Next browser tab",
                aliases=("next tab", "switch to next tab"),
                risk=Risk.LOW,
                permissions=("keyboard.shortcut",),
            ),
            CommandSpec(
                intent="media.seek_forward",
                description="Skip forward",
                aliases=("seek forward", "skip forward"),
                risk=Risk.LOW,
                permissions=("media.control",),
                argument=ArgumentSpec(name="seconds", minimum=1, maximum=600, default=10),
            ),
            CommandSpec(
                intent="window.close",
                description="Close the focused window",
                aliases=("close this window",),
                risk=Risk.HIGH,
                permissions=("window.manage",),
            ),
            CommandSpec(intent="system.no_action", description="Do nothing"),
        ]
    )
    return reg


@pytest.fixture
def proposal() -> IntentProposal:
    return IntentProposal(intent="browser.next_tab")
