"""Rate limit + circuit breaker around the cloud provider, on a fake clock."""

from __future__ import annotations

import pytest
from tests.conftest import FakeClock

from voice_agent.core.exceptions import LLMUnavailableError
from voice_agent.core.models import IntentProposal
from voice_agent.llm.circuit_breaker import GuardedIntentProvider

OK = IntentProposal(intent="browser.next_tab")


class ScriptedProvider:
    """Fails while `failing` is true; counts every call that actually reached it."""

    def __init__(self) -> None:
        self.failing = False
        self.calls = 0
        self.during_call: object = None

    def propose(self, transcript: str, context: dict[str, str]) -> IntentProposal | None:
        self.calls += 1
        if callable(self.during_call):
            self.during_call()
        if self.failing:
            raise LLMUnavailableError("Groq intent resolution failed: TimeoutError")
        return OK


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def provider() -> ScriptedProvider:
    return ScriptedProvider()


@pytest.fixture
def guard(provider: ScriptedProvider, clock: FakeClock) -> GuardedIntentProvider:
    return GuardedIntentProvider(
        provider, max_per_minute=100, failures_to_open=3, open_ms=30_000, clock=clock
    )


def _fail(guard: GuardedIntentProvider, times: int) -> None:
    for _ in range(times):
        with pytest.raises(LLMUnavailableError):
            guard.propose("x", {})


def test_healthy_calls_pass_straight_through(guard: GuardedIntentProvider) -> None:
    assert guard.propose("x", {}) is OK


def test_three_consecutive_failures_open_the_circuit(
    guard: GuardedIntentProvider, provider: ScriptedProvider
) -> None:
    provider.failing = True
    _fail(guard, 3)
    with pytest.raises(LLMUnavailableError, match="circuit open"):
        guard.propose("x", {})
    assert provider.calls == 3  # the 4th never reached the network


def test_a_success_resets_the_count(
    guard: GuardedIntentProvider, provider: ScriptedProvider
) -> None:
    provider.failing = True
    _fail(guard, 2)
    provider.failing = False
    guard.propose("x", {})
    provider.failing = True
    _fail(guard, 2)
    provider.failing = False
    assert guard.propose("x", {}) is OK  # never three in a row: still closed


def test_half_open_probe_success_closes_the_circuit(
    guard: GuardedIntentProvider, provider: ScriptedProvider, clock: FakeClock
) -> None:
    provider.failing = True
    _fail(guard, 3)
    clock.advance(29_999)
    _fail(guard, 1)
    assert provider.calls == 3
    clock.advance(1)
    provider.failing = False
    assert guard.propose("x", {}) is OK  # the probe
    assert guard.propose("x", {}) is OK
    assert provider.calls == 5


def test_half_open_probe_failure_reopens_at_once(
    guard: GuardedIntentProvider, provider: ScriptedProvider, clock: FakeClock
) -> None:
    provider.failing = True
    _fail(guard, 3)
    clock.advance(30_000)
    _fail(guard, 1)  # the probe fails: one failure is enough now
    assert provider.calls == 4
    _fail(guard, 1)
    assert provider.calls == 4
    clock.advance(30_000)
    provider.failing = False
    assert guard.propose("x", {}) is OK


def test_only_one_probe_while_half_open(
    guard: GuardedIntentProvider, provider: ScriptedProvider, clock: FakeClock
) -> None:
    provider.failing = True
    _fail(guard, 3)
    clock.advance(30_000)
    provider.failing = False
    blocked: list[Exception] = []

    def second_caller_during_probe() -> None:
        provider.during_call = None
        try:
            guard.propose("y", {})
        except LLMUnavailableError as exc:
            blocked.append(exc)

    provider.during_call = second_caller_during_probe
    assert guard.propose("x", {}) is OK
    assert len(blocked) == 1
    assert provider.calls == 4


def test_rate_limit_fails_fast_without_counting_as_a_failure(
    provider: ScriptedProvider, clock: FakeClock
) -> None:
    guard = GuardedIntentProvider(provider, max_per_minute=2, failures_to_open=1, clock=clock)
    guard.propose("x", {})
    clock.advance(1_000)
    guard.propose("x", {})
    with pytest.raises(LLMUnavailableError, match="rate limit"):
        guard.propose("x", {})
    assert provider.calls == 2
    clock.advance(59_000)  # the first call leaves the 60 s window
    assert guard.propose("x", {}) is OK  # and the circuit never opened


def test_a_crashing_probe_does_not_wedge_the_circuit_half_open(
    guard: GuardedIntentProvider, provider: ScriptedProvider, clock: FakeClock
) -> None:
    provider.failing = True
    _fail(guard, 3)
    clock.advance(30_000)

    def crash() -> None:
        raise RuntimeError("bug in a provider")

    provider.during_call = crash
    with pytest.raises(RuntimeError):
        guard.propose("x", {})
    provider.during_call, provider.failing = None, False
    clock.advance(30_000)
    assert guard.propose("x", {}) is OK
