"""Device selection over a fake backend: no audio hardware is ever enumerated."""

from __future__ import annotations

import pytest
from tests.conftest import FakeDeviceBackend

from voice_agent.audio.device_manager import DeviceManager
from voice_agent.core.exceptions import AudioDeviceError
from voice_agent.core.interfaces import AudioDeviceBackend
from voice_agent.core.models import AudioDevice

USB = AudioDevice(index=1, name="Microphone (USB Audio)", channels=1, default_samplerate=48000)
ARRAY = AudioDevice(index=4, name="Mic Array (Realtek)", channels=2, default_samplerate=44100)


def test_fake_satisfies_the_protocol() -> None:
    assert isinstance(FakeDeviceBackend([]), AudioDeviceBackend)


def test_no_preference_picks_the_system_default() -> None:
    assert DeviceManager(FakeDeviceBackend([USB, ARRAY], default=4)).select(None) == ARRAY


def test_no_default_falls_back_to_the_first_input() -> None:
    assert DeviceManager(FakeDeviceBackend([USB, ARRAY], default=None)).select(None) == USB


def test_preference_by_index_or_case_insensitive_name() -> None:
    manager = DeviceManager(FakeDeviceBackend([USB, ARRAY], default=1))
    assert manager.select(4) == ARRAY
    assert manager.select("realtek") == ARRAY


def test_missing_preference_names_what_is_available() -> None:
    manager = DeviceManager(FakeDeviceBackend([USB, ARRAY]))
    with pytest.raises(AudioDeviceError, match=r"Blue Yeti.*Microphone \(USB Audio\)"):
        manager.select("Blue Yeti")
    with pytest.raises(AudioDeviceError, match="9"):
        manager.select(9)


def test_no_microphone_at_all_is_a_clear_error() -> None:
    with pytest.raises(AudioDeviceError, match="no microphone"):
        DeviceManager(FakeDeviceBackend([])).select(None)


def test_reselect_rescans_after_a_hot_unplug() -> None:
    backend = FakeDeviceBackend([USB, ARRAY], default=1)
    manager = DeviceManager(backend)
    assert manager.select(None) == USB

    backend.devices, backend.default = [ARRAY], 4  # USB mic pulled out
    assert manager.reselect(None) == ARRAY
    assert backend.refreshed == 1

    backend.devices = []
    with pytest.raises(AudioDeviceError):
        manager.reselect(None)
