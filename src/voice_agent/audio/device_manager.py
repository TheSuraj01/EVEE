"""Input device selection.

Every failure is an AudioDeviceError whose text says what was wanted and what exists, so
"the microphone is unavailable" is never the only clue the user gets.
"""

from __future__ import annotations

from voice_agent.core.exceptions import AudioDeviceError
from voice_agent.core.interfaces import AudioDeviceBackend
from voice_agent.core.models import AudioDevice


class DeviceManager:
    def __init__(self, backend: AudioDeviceBackend) -> None:
        self._backend = backend

    def inputs(self) -> list[AudioDevice]:
        return self._backend.inputs()

    def select(self, preference: int | str | None = None) -> AudioDevice:
        """Config value -> device: None = system default, int = index, str = name part."""
        devices = self._backend.inputs()
        if not devices:
            msg = "no microphone found — is one plugged in and enabled in Windows?"
            raise AudioDeviceError(msg)

        if preference is None:
            default = self._backend.default_input()
            return next((d for d in devices if d.index == default), devices[0])

        if isinstance(preference, int):
            matches = [d for d in devices if d.index == preference]
        else:
            needle = preference.casefold()
            matches = [d for d in devices if needle in d.name.casefold()]
        if not matches:
            available = ", ".join(f"{d.index}: {d.name}" for d in devices)
            msg = f"input device {preference!r} not found; available: {available}"
            raise AudioDeviceError(msg)
        return matches[0]

    def reselect(self, preference: int | str | None = None) -> AudioDevice:
        """After a hot-unplug: re-scan hardware, then select again. Call with no open stream."""
        self._backend.refresh()
        return self.select(preference)


class SoundDeviceBackend:  # pragma: no cover - needs real audio hardware
    """Production backend. sounddevice is imported here, never at module import."""

    def __init__(self) -> None:
        import sounddevice

        self._sd = sounddevice

    def default_input(self) -> int | None:
        index = self._sd.default.device[0]
        return int(index) if index is not None and index >= 0 else None

    def inputs(self) -> list[AudioDevice]:
        # Windows lists each mic once per host API (MME, DirectSound, WASAPI...). Keep the
        # default input's host API so one physical mic is one entry.
        default = self.default_input()
        hostapi = self._sd.query_devices(default)["hostapi"] if default is not None else None
        return [
            AudioDevice(
                index=i,
                name=d["name"],
                channels=d["max_input_channels"],
                default_samplerate=d["default_samplerate"],
            )
            for i, d in enumerate(self._sd.query_devices())
            if d["max_input_channels"] > 0 and hostapi in (None, d["hostapi"])
        ]

    def refresh(self) -> None:
        # ponytail: private API, but PortAudio only re-scans hardware on re-initialise.
        self._sd._terminate()
        self._sd._initialize()
