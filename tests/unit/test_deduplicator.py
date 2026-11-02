from __future__ import annotations

from tests.conftest import FakeClock

from voice_agent.engine.deduplicator import Deduplicator


def test_repeat_inside_window_is_a_duplicate() -> None:
    clock = FakeClock()
    dedupe = Deduplicator(window_ms=1000, clock=clock)
    assert dedupe.is_duplicate("a") is False
    clock.advance(300)
    assert dedupe.is_duplicate("a") is True


def test_repeat_outside_window_is_allowed() -> None:
    clock = FakeClock()
    dedupe = Deduplicator(window_ms=1000, clock=clock)
    dedupe.is_duplicate("a")
    clock.advance(1500)
    assert dedupe.is_duplicate("a") is False


def test_different_keys_do_not_interfere() -> None:
    dedupe = Deduplicator(window_ms=1000, clock=FakeClock())
    assert dedupe.is_duplicate("a") is False
    assert dedupe.is_duplicate("b") is False
