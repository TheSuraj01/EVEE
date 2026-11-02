"""Protocols that invert every dependency on hardware, the OS, the clock and the network.

If a module needs one of those, it depends on a Protocol declared here and receives an
implementation by injection. That is what keeps the test suite free of microphones,
Windows APIs and HTTP.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable

import numpy as np

from voice_agent.core.models import (
    AudioDevice,
    CommandSpec,
    ExecutionResult,
    IntentProposal,
    ResolvedCommand,
)


@runtime_checkable
class Clock(Protocol):
    """Monotonic time source. Injected so timing logic is testable without sleeping."""

    def monotonic_ms(self) -> float: ...


@runtime_checkable
class AudioDeviceBackend(Protocol):
    """The seam over sounddevice's device enumeration."""

    def inputs(self) -> list[AudioDevice]: ...

    def default_input(self) -> int | None: ...

    def refresh(self) -> None:
        """Re-scan hardware so plugged/unplugged devices show up. Only with no open stream."""
        ...


@runtime_checkable
class AudioStream(Protocol):
    """An open input stream (sounddevice.InputStream in production)."""

    @property
    def active(self) -> bool: ...

    def start(self) -> None: ...

    def stop(self) -> None: ...

    def close(self) -> None: ...


#: sounddevice's callback signature: (indata[frames, channels], frames, time, status).
AudioCallback = Callable[[np.ndarray, int, Any, Any], None]


class StreamOpener(Protocol):
    def __call__(
        self, device: AudioDevice, sample_rate: int, block_samples: int, callback: AudioCallback
    ) -> AudioStream: ...


@runtime_checkable
class WakeWordDetector(Protocol):
    def process(self, frame: np.ndarray) -> bool:
        """Return True on the frame where the wake word completes."""
        ...

    def reset(self) -> None: ...


@runtime_checkable
class SpeechProvider(Protocol):
    def transcribe(self, audio: np.ndarray, sample_rate: int) -> str:
        """Return a raw transcript, or an empty string when nothing was said."""
        ...

    def warm_up(self) -> None: ...


@runtime_checkable
class IntentProvider(Protocol):
    """Anything that turns a transcript into a *proposal*. Never into an action."""

    def propose(self, transcript: str, context: dict[str, str]) -> IntentProposal | None: ...


@runtime_checkable
class ActionHandler(Protocol):
    """Executes one resolved command. Only the Dispatcher is allowed to call this."""

    def execute(self, command: ResolvedCommand) -> ExecutionResult: ...


@runtime_checkable
class KeySender(Protocol):
    """The narrow seam over pyautogui / pywin32 keyboard input."""

    def press(self, *keys: str) -> None: ...

    def hotkey(self, *keys: str) -> None: ...


@runtime_checkable
class WindowBackend(Protocol):
    """The narrow seam over pywin32. Every operation acts on the foreground window."""

    def foreground(self) -> int:
        """Opaque handle of the focused window; 0 when there is none."""
        ...

    def activate(self, handle: int) -> bool:
        """Give focus back to `handle`; False if that window is gone or refused focus."""
        ...

    def minimize(self) -> None: ...

    def maximize(self) -> None: ...

    def close(self) -> None: ...


@runtime_checkable
class ConfirmationPrompt(Protocol):
    def confirm(self, spec: CommandSpec, timeout_s: float) -> bool:
        """Ask the user. Must return False on timeout — silence is never consent."""
        ...


class SystemClock:
    """Default Clock implementation."""

    def monotonic_ms(self) -> float:
        import time

        return time.monotonic() * 1000.0
