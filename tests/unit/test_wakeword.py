"""Wake word adapter over a fake model, and the activation state machine with no audio."""

from __future__ import annotations

import numpy as np
import pytest
from tests.conftest import FakeClock

from voice_agent.core.interfaces import WakeWordDetector
from voice_agent.wakeword.activation_controller import ActivationController, State
from voice_agent.wakeword.openwakeword_adapter import CHUNK_SAMPLES, OpenWakeWordDetector

FRAME = 480


class FakeModel:
    def __init__(self, scores: list[float]) -> None:
        self.scores = scores
        self.chunks: list[np.ndarray] = []
        self.resets = 0

    def predict(self, chunk: np.ndarray) -> dict[str, float]:
        self.chunks.append(chunk)
        return {"hey_jarvis_v0.1": self.scores.pop(0) if self.scores else 0.0}

    def reset(self) -> None:
        self.resets += 1


def _detector(model: FakeModel, loads: list[str] | None = None) -> OpenWakeWordDetector:
    def load(name: str) -> FakeModel:
        if loads is not None:
            loads.append(name)
        return model

    return OpenWakeWordDetector("hey_jarvis", threshold=0.6, load=load)


def test_adapter_satisfies_the_protocol() -> None:
    assert isinstance(_detector(FakeModel([])), WakeWordDetector)


def test_model_loads_lazily_once() -> None:
    loads: list[str] = []
    detector = _detector(FakeModel([]), loads)
    assert loads == []
    detector.warm_up()
    detector.process(np.zeros(FRAME, dtype=np.float32))
    assert loads == ["hey_jarvis"]


def test_frames_are_regrouped_into_80_ms_int16_chunks() -> None:
    model = FakeModel([])
    detector = _detector(model)
    for _ in range(8):  # 8 x 480 = 3840 = 3 x 1280
        detector.process(np.full(FRAME, 0.5, dtype=np.float32))
    assert [c.size for c in model.chunks] == [CHUNK_SAMPLES] * 3
    assert model.chunks[0].dtype == np.int16
    assert model.chunks[0][0] == 16383


def test_fires_only_at_or_above_threshold() -> None:
    detector = _detector(FakeModel([0.59, 0.6]))
    frame = np.zeros(CHUNK_SAMPLES, dtype=np.float32)
    assert detector.process(frame) is False
    assert detector.process(frame) is True


def test_reset_clears_buffered_audio_and_model_state() -> None:
    model = FakeModel([])
    detector = _detector(model)
    detector.warm_up()
    detector.process(np.zeros(FRAME, dtype=np.float32))
    detector.reset()
    detector.process(np.zeros(1000, dtype=np.float32))
    assert model.chunks == []  # the 480 buffered samples were dropped
    assert model.resets == 1


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def controller(clock: FakeClock) -> ActivationController:
    ctl = ActivationController(cooldown_ms=1500, clock=clock)
    ctl.start()
    return ctl


def _complete_command(controller: ActivationController) -> None:
    assert controller.wake()
    assert controller.begin_processing()
    controller.finish()


def test_full_cycle_follows_the_documented_states(controller: ActivationController) -> None:
    assert controller.state is State.LISTENING
    assert controller.wake()
    assert controller.state is State.CAPTURING
    assert controller.begin_processing()
    assert controller.state is State.PROCESSING
    controller.finish()
    assert controller.state is State.COOLDOWN


def test_cooldown_blocks_a_second_activation(
    controller: ActivationController, clock: FakeClock
) -> None:
    _complete_command(controller)
    clock.advance(1499)
    assert controller.wake() is False
    assert controller.state is State.COOLDOWN
    clock.advance(1)
    assert controller.state is State.LISTENING
    assert controller.wake() is True


def test_double_wake_trigger_is_one_activation(controller: ActivationController) -> None:
    assert controller.wake()
    assert controller.wake() is False
    assert controller.state is State.CAPTURING


def test_nothing_happens_before_start(clock: FakeClock) -> None:
    ctl = ActivationController(cooldown_ms=1500, clock=clock)
    assert ctl.state is State.IDLE
    assert ctl.wake() is False


def test_mute_wins_from_every_state(controller: ActivationController) -> None:
    """Invariant 8: mute works even while a command is being processed."""
    assert controller.wake()
    assert controller.begin_processing()
    controller.mute()
    controller.finish()  # the in-flight command completes, but must not unmute
    assert controller.state is State.IDLE
    assert controller.wake() is False
    controller.unmute()
    assert controller.state is State.LISTENING


def test_out_of_order_calls_are_refused(controller: ActivationController) -> None:
    assert controller.begin_processing() is False
    controller.finish()
    assert controller.state is State.LISTENING


def test_device_reconnect_does_not_undo_a_user_mute(controller: ActivationController) -> None:
    controller.mute()
    controller.stop()  # microphone lost...
    controller.start()  # ...and re-acquired
    assert controller.state is State.IDLE
    controller.unmute()
    assert controller.state is State.LISTENING


def test_unmute_while_stopped_does_not_start_listening(clock: FakeClock) -> None:
    ctl = ActivationController(cooldown_ms=1500, clock=clock)
    ctl.mute()
    ctl.unmute()
    assert ctl.state is State.IDLE


def test_mute_and_stop_both_fire_the_cancel_hook(clock: FakeClock) -> None:
    """Invariant 8: a pending confirmation must be cancelled the moment we stop listening."""
    cancelled: list[str] = []
    ctl = ActivationController(1500, clock=clock, on_mute=lambda: cancelled.append("x"))
    ctl.start()
    ctl.mute()
    ctl.stop()
    assert cancelled == ["x", "x"]
