from __future__ import annotations

from voice_agent.core.models import Decision, IntentProposal, IntentSource
from voice_agent.engine.command_registry import CommandRegistry
from voice_agent.safety.policy_engine import PolicyEngine, PolicySettings


def test_known_low_risk_command_is_allowed(registry: CommandRegistry) -> None:
    verdict = PolicyEngine(registry).evaluate(IntentProposal(intent="browser.next_tab"))
    assert verdict.decision is Decision.ALLOW


def test_intent_outside_the_registry_is_denied(registry: CommandRegistry) -> None:
    proposal = IntentProposal(intent="system.format_disk", source=IntentSource.LLM)
    verdict = PolicyEngine(registry).evaluate(proposal)
    assert verdict.decision is Decision.DENY
    assert verdict.reason == "unknown_intent"


def test_no_action_is_denied(registry: CommandRegistry) -> None:
    verdict = PolicyEngine(registry).evaluate(IntentProposal(intent="system.no_action"))
    assert verdict.decision is Decision.DENY


def test_high_risk_requires_confirmation(registry: CommandRegistry) -> None:
    verdict = PolicyEngine(registry).evaluate(IntentProposal(intent="window.close"))
    assert verdict.decision is Decision.CONFIRM


def test_out_of_range_argument_is_denied(registry: CommandRegistry) -> None:
    proposal = IntentProposal(intent="media.seek_forward", argument_seconds=99999)
    verdict = PolicyEngine(registry).evaluate(proposal)
    assert verdict.decision is Decision.DENY
    assert verdict.reason == "argument_out_of_range"


def test_default_argument_is_applied(registry: CommandRegistry) -> None:
    verdict = PolicyEngine(registry).evaluate(IntentProposal(intent="media.seek_forward"))
    assert verdict.argument == 10


def test_low_confidence_proposal_is_denied(registry: CommandRegistry) -> None:
    proposal = IntentProposal(intent="browser.next_tab", confidence=0.2, source=IntentSource.LLM)
    assert PolicyEngine(registry).evaluate(proposal).decision is Decision.DENY


def test_ungranted_permission_is_denied(registry: CommandRegistry) -> None:
    settings = PolicySettings(granted_permissions=frozenset({"media.control"}))
    verdict = PolicyEngine(registry, settings).evaluate(IntentProposal(intent="browser.next_tab"))
    assert verdict.decision is Decision.DENY
    assert verdict.reason.startswith("permission_denied")
