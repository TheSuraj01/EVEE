"""T-031: local first, cloud only on a miss, and a cloud failure degrades to no_action.

The whole agent is built from the shipped config and command table; only Groq is faked.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tests.conftest import FakeTransport, RecordingKeySender, RecordingWindowBackend

from voice_agent.bootstrap import Agent, build_agent
from voice_agent.core.exceptions import LLMUnavailableError
from voice_agent.core.models import ExecutionStatus
from voice_agent.settings.loader import load_settings


def reply(intent: str, confidence: float = 0.9, seconds: int | None = None) -> str:
    return json.dumps(
        {
            "intent": intent,
            "argument_seconds": seconds,
            "requires_confirmation": False,
            "confidence": confidence,
        }
    )


class Rig:
    def __init__(self, tmp_path: Path, transport: FakeTransport, **env: str) -> None:
        self.keys = RecordingKeySender()
        self.transport = transport
        self.audit = tmp_path / "audit.jsonl"
        self.agent: Agent = build_agent(
            load_settings(env={f"VOICE_AGENT__{k}": v for k, v in env.items()}),
            keys=self.keys,
            windows=RecordingWindowBackend(),
            audit_path=self.audit,
            llm=transport,
        )

    def audited(self) -> list[dict[str, object]]:
        return [json.loads(line) for line in self.audit.read_text(encoding="utf-8").splitlines()]


@pytest.mark.parametrize("phrase", ["next tab", "Next tab, please!", "next tabb"])
def test_known_or_near_known_phrases_never_reach_the_cloud(tmp_path: Path, phrase: str) -> None:
    """Invariant 3: pressing a key must never require the internet."""
    rig = Rig(tmp_path, FakeTransport([reply("browser.next_tab")]))
    assert rig.agent.handle(phrase).ok
    assert rig.keys.calls == [("ctrl", "tab")]
    assert rig.transport.calls == []


def test_unfamiliar_phrasing_is_resolved_by_the_cloud(tmp_path: Path) -> None:
    rig = Rig(tmp_path, FakeTransport([reply("media.seek_forward", seconds=45)]))
    assert rig.agent.handle("jump ahead forty five secs in this video").ok
    assert len(rig.transport.calls) == 1
    assert rig.keys.calls == [("right",)] * 9
    assert rig.audited()[0]["source"] == "llm"


def test_cloud_failure_degrades_to_no_action_with_a_visible_reason(tmp_path: Path) -> None:
    """Invariant 4: Groq down -> nothing fires, the user is told why, the agent stays up."""
    rig = Rig(tmp_path, FakeTransport(error=TimeoutError("read timeout")))
    result = rig.agent.handle("hop over the boring part")
    assert result.status is ExecutionStatus.DENIED
    assert result.message == LLMUnavailableError.user_message
    assert rig.keys.calls == []
    [entry] = rig.audited()
    assert (entry["intent"], entry["source"]) == ("system.no_action", "llm")

    assert rig.agent.handle("next tab").ok  # the local path is untouched
    assert rig.agent.router.last_error is None


def test_cloud_proposals_still_face_the_policy_engine(tmp_path: Path) -> None:
    rig = Rig(
        tmp_path,
        FakeTransport([reply("window.close", confidence=0.99), reply("browser.next_tab", 0.3)]),
    )
    closing = rig.agent.handle("get rid of this thing")  # high risk, no prompt wired
    unsure = rig.agent.handle("maybe the other one")  # below minimum_confidence
    assert (closing.status, unsure.status) == (ExecutionStatus.DENIED, ExecutionStatus.DENIED)
    assert unsure.detail["reason"] == "low_confidence"
    assert rig.keys.calls == []


def test_disabling_the_llm_in_config_keeps_everything_local(tmp_path: Path) -> None:
    rig = Rig(tmp_path, FakeTransport([reply("browser.next_tab")]), LLM__ENABLED="false")
    result = rig.agent.handle("move one tab to the right")
    assert result.status is ExecutionStatus.DENIED
    assert rig.transport.calls == []


def test_no_transport_means_local_only(tmp_path: Path) -> None:
    agent = build_agent(
        load_settings(env={}),
        keys=RecordingKeySender(),
        windows=RecordingWindowBackend(),
        audit_path=tmp_path / "audit.jsonl",
    )
    assert agent.handle("move one tab to the right").status is ExecutionStatus.DENIED


def test_dead_network_stops_costing_a_timeout_per_utterance(tmp_path: Path) -> None:
    """T-033: after llm.circuit_breaker_failures failed utterances the cloud is skipped."""
    rig = Rig(tmp_path, FakeTransport(error=TimeoutError("read timeout")))
    for _ in range(3):  # each: 1 call + 1 retry
        rig.agent.handle("hop over the boring part")
    assert len(rig.transport.calls) == 6

    result = rig.agent.handle("hop over the boring part")
    assert len(rig.transport.calls) == 6  # circuit open: no network at all
    assert result.message == LLMUnavailableError.user_message
    assert rig.agent.handle("next tab").ok  # local commands are unaffected


def test_cloud_rate_limit_comes_from_config(tmp_path: Path) -> None:
    rig = Rig(
        tmp_path,
        FakeTransport([reply("browser.next_tab")] * 5),
        LLM__MAX_CALLS_PER_MINUTE="2",
    )
    for _ in range(3):
        rig.agent.handle("move one tab to the right")
    assert len(rig.transport.calls) == 2
