"""End-to-end through the real router, policy engine and dispatcher, with fake edges.

These tests encode the project's safety invariants. If one of them starts failing, the
safety model has regressed — do not weaken the assertion.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.conftest import (
    AlwaysConfirm,
    FakeClock,
    FakeTransport,
    RecordingHandler,
    RecordingKeySender,
    RecordingWindowBackend,
)

from voice_agent.bootstrap import build_agent
from voice_agent.core.exceptions import ActionExecutionError
from voice_agent.core.models import ExecutionStatus, IntentSource
from voice_agent.engine.command_registry import CommandRegistry
from voice_agent.engine.deduplicator import Deduplicator
from voice_agent.engine.dispatcher import Dispatcher
from voice_agent.llm.groq_qwen_provider import GroqQwenIntentProvider
from voice_agent.nlu.alias_matcher import AliasMatcher
from voice_agent.nlu.router import IntentRouter
from voice_agent.safety.policy_engine import PolicyEngine
from voice_agent.settings.loader import load_settings

VALID = (
    '{"intent":"browser.next_tab","argument_seconds":null,'
    '"requires_confirmation":false,"confidence":0.9}'
)


@pytest.fixture
def pipeline(registry: CommandRegistry):
    clock = FakeClock()
    handler = RecordingHandler()
    dispatcher = Dispatcher(
        registry,
        PolicyEngine(registry),
        Deduplicator(window_ms=1000, clock=clock),
        confirmation=AlwaysConfirm(True),
        clock=clock,
    )
    for spec in registry:
        dispatcher.register_handler(spec.intent, handler)
    return dispatcher, handler, clock


def test_known_phrase_executes(pipeline, registry: CommandRegistry) -> None:
    dispatcher, handler, _ = pipeline
    router = IntentRouter(AliasMatcher(registry))
    proposal = router.resolve("next tab")
    assert proposal is not None
    assert dispatcher.dispatch(proposal).status is ExecutionStatus.SUCCESS
    assert handler.calls[0].spec.intent == "browser.next_tab"


def test_known_phrase_never_reaches_the_cloud(registry: CommandRegistry) -> None:
    """Invariant 3: pressing a key must not require the internet."""
    transport = FakeTransport([VALID])
    router = IntentRouter(
        AliasMatcher(registry),
        GroqQwenIntentProvider(registry, transport, model="test-model"),
        llm_enabled=True,
    )
    assert router.resolve("next tab") is not None
    assert transport.calls == []


def test_unknown_phrase_falls_through_to_the_cloud(registry: CommandRegistry) -> None:
    transport = FakeTransport([VALID])
    router = IntentRouter(
        AliasMatcher(registry),
        GroqQwenIntentProvider(registry, transport, model="test-model"),
        llm_enabled=True,
    )
    proposal = router.resolve("move me along to the tab on the right")
    assert proposal is not None
    assert proposal.source is IntentSource.LLM
    assert len(transport.calls) == 1


def test_cloud_failure_degrades_instead_of_crashing(registry: CommandRegistry) -> None:
    """Invariant 4: Groq down must not take the agent down."""
    transport = FakeTransport(error=TimeoutError("timeout"))
    router = IntentRouter(
        AliasMatcher(registry),
        GroqQwenIntentProvider(registry, transport, model="test-model"),
        llm_enabled=True,
    )
    fallback = router.resolve("something unusual")  # T-031: explicit no_action, not None
    assert fallback is not None
    assert (fallback.intent, fallback.source) == ("system.no_action", IntentSource.LLM)
    assert router.last_error is not None
    assert router.resolve("next tab") is not None  # local path still works


def test_duplicate_utterance_executes_once(pipeline, registry: CommandRegistry) -> None:
    """Invariant 9: a double wake-word trigger must not double-press."""
    dispatcher, handler, clock = pipeline
    router = IntentRouter(AliasMatcher(registry))
    proposal = router.resolve("next tab")
    assert proposal is not None

    assert dispatcher.dispatch(proposal).status is ExecutionStatus.SUCCESS
    clock.advance(200)
    assert dispatcher.dispatch(proposal).status is ExecutionStatus.DUPLICATE
    assert len(handler.calls) == 1


def test_high_risk_command_is_not_executed_without_confirmation(registry: CommandRegistry) -> None:
    handler = RecordingHandler()
    dispatcher = Dispatcher(
        registry,
        PolicyEngine(registry),
        Deduplicator(clock=FakeClock()),
        confirmation=AlwaysConfirm(False),
        clock=FakeClock(),
    )
    dispatcher.register_handler("window.close", handler)
    proposal = IntentRouter(AliasMatcher(registry)).resolve("close this window")
    assert proposal is not None
    assert dispatcher.dispatch(proposal).status is ExecutionStatus.DENIED
    assert handler.calls == []


def test_handler_exception_is_contained(registry: CommandRegistry) -> None:
    """The voice loop must survive a failing action."""
    handler = RecordingHandler(raises=ActionExecutionError("boom"))
    dispatcher = Dispatcher(registry, PolicyEngine(registry), Deduplicator(clock=FakeClock()))
    dispatcher.register_handler("browser.next_tab", handler)
    proposal = IntentRouter(AliasMatcher(registry)).resolve("next tab")
    assert proposal is not None
    result = dispatcher.dispatch(proposal)
    assert result.status is ExecutionStatus.ERROR
    assert "API" not in result.message


def test_the_injected_clock_also_governs_dedupe(tmp_path: Path) -> None:
    """VoiceService injects one clock for cooldown, capture and dedupe; on the system clock
    the dedupe window would never pass in a test, and every repeat would be dropped."""
    clock = FakeClock()
    settings = load_settings(env={})
    agent = build_agent(
        settings,
        keys=RecordingKeySender(),
        windows=RecordingWindowBackend(),
        audit_path=tmp_path / "a.jsonl",
        clock=clock,
    )

    assert agent.handle("next tab").status is ExecutionStatus.SUCCESS
    assert agent.handle("next tab").status is ExecutionStatus.DUPLICATE
    clock.advance(settings.nlu.dedupe_window_ms)
    assert agent.handle("next tab").status is ExecutionStatus.SUCCESS
