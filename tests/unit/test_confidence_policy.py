"""T-032: low-confidence and out-of-bounds proposals are rejected — and the rejection logged.

The checks live in PolicyEngine (the single gate, invariant 2), with bounds read from the
registry; these tests pin the boundaries for model-sourced proposals and the config wiring.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from tests.conftest import RecordingKeySender, RecordingWindowBackend

from voice_agent.bootstrap import build_agent
from voice_agent.core.models import Decision, IntentProposal, IntentSource
from voice_agent.engine.command_registry import CommandRegistry
from voice_agent.safety.policy_engine import PolicyEngine
from voice_agent.settings.loader import load_settings


def _llm(intent: str, confidence: float = 0.9, seconds: int | None = None) -> IntentProposal:
    return IntentProposal(
        intent=intent,
        confidence=confidence,
        argument_seconds=seconds,
        source=IntentSource.LLM,
        raw="please skip ahead a bit in my bank video",
    )


@pytest.mark.parametrize(
    ("confidence", "decision"), [(0.54, Decision.DENY), (0.55, Decision.ALLOW)]
)
def test_confidence_threshold_is_inclusive(
    registry: CommandRegistry, confidence: float, decision: Decision
) -> None:
    assert (
        PolicyEngine(registry).evaluate(_llm("browser.next_tab", confidence)).decision is decision
    )


@pytest.mark.parametrize(
    ("seconds", "decision"),
    [(0, Decision.DENY), (1, Decision.ALLOW), (600, Decision.ALLOW), (601, Decision.DENY)],
)
def test_argument_bounds_come_from_the_registry(
    registry: CommandRegistry, seconds: int, decision: Decision
) -> None:
    proposal = _llm("media.seek_forward", seconds=seconds)
    assert PolicyEngine(registry).evaluate(proposal).decision is decision


def test_rejection_is_logged_with_intent_and_reason_but_not_the_transcript(
    registry: CommandRegistry, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="voice_agent.safety"):
        PolicyEngine(registry).evaluate(_llm("media.seek_forward", seconds=9999))
    [record] = [r for r in caplog.records if r.name == "voice_agent.safety.policy_engine"]
    message = record.getMessage()
    assert "media.seek_forward" in message
    assert "argument_out_of_range" in message
    assert "llm" in message
    assert "bank" not in message


def test_allowed_proposals_are_not_logged_as_rejections(
    registry: CommandRegistry, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="voice_agent.safety"):
        PolicyEngine(registry).evaluate(_llm("browser.next_tab"))
    assert [r for r in caplog.records if r.name == "voice_agent.safety.policy_engine"] == []


def test_threshold_is_wired_from_config(tmp_path: Path) -> None:
    settings = load_settings(env={"VOICE_AGENT__SAFETY__MINIMUM_CONFIDENCE": "0.8"})
    agent = build_agent(
        settings,
        keys=RecordingKeySender(),
        windows=RecordingWindowBackend(),
        audit_path=tmp_path / "audit.jsonl",
    )
    verdict = agent.policy.evaluate(_llm("browser.next_tab", confidence=0.75))
    assert (verdict.decision, verdict.reason) == (Decision.DENY, "low_confidence")


def test_decide_is_the_same_verdict_without_logging(
    registry: CommandRegistry, caplog: pytest.LogCaptureFixture
) -> None:
    """For display (the dry run): showing a refusal must not log it a second time."""
    proposal = _llm("media.seek_forward", seconds=9999)
    with caplog.at_level(logging.INFO, logger="voice_agent.safety"):
        shown = PolicyEngine(registry).decide(proposal)
    assert shown == PolicyEngine(registry).evaluate(proposal)
    assert [r for r in caplog.records if r.getMessage().startswith("refused")] == []
