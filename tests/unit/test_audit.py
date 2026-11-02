"""Audit log: one JSON line per decision, append-only, raw transcript only when allowed."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

import pytest
from tests.conftest import FakeClock, RecordingHandler

from voice_agent.core.models import ExecutionResult, IntentProposal, IntentSource
from voice_agent.engine.command_registry import CommandRegistry
from voice_agent.engine.deduplicator import Deduplicator
from voice_agent.engine.dispatcher import Dispatcher
from voice_agent.safety.audit import AuditLog
from voice_agent.safety.policy_engine import PolicyEngine

SPOKEN = "next tab please, my bank is open"


def _dispatch(registry: CommandRegistry, audit: AuditLog, *proposals: IntentProposal) -> None:
    dispatcher = Dispatcher(
        registry, PolicyEngine(registry), Deduplicator(clock=FakeClock()), audit=audit
    )
    dispatcher.register_handler("browser.next_tab", RecordingHandler())
    for proposal in proposals:
        dispatcher.dispatch(proposal)


def _lines(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_decision_is_recorded_without_the_raw_transcript(
    registry: CommandRegistry, tmp_path: Path
) -> None:
    path = tmp_path / "audit" / "decisions.jsonl"
    _dispatch(registry, AuditLog(path), IntentProposal(intent="browser.next_tab", raw=SPOKEN))

    [entry] = _lines(path)
    assert entry["intent"] == "browser.next_tab"
    assert entry["source"] == "local"
    assert entry["decision"] == "success"
    assert entry["reason"] == "ok"
    assert len(str(entry["transcript_hash"])) == 64
    assert datetime.fromisoformat(str(entry["ts"])).tzinfo is not None
    assert "transcript" not in entry
    assert "bank" not in path.read_text(encoding="utf-8")


def test_raw_transcript_only_when_explicitly_allowed(
    registry: CommandRegistry, tmp_path: Path
) -> None:
    path = tmp_path / "decisions.jsonl"
    audit = AuditLog(path, include_transcript=True)
    _dispatch(registry, audit, IntentProposal(intent="browser.next_tab", raw=SPOKEN))
    assert _lines(path)[0]["transcript"] == SPOKEN


def test_llm_denial_records_source_and_reason(registry: CommandRegistry, tmp_path: Path) -> None:
    path = tmp_path / "decisions.jsonl"
    rogue = IntentProposal(intent="system.format_disk", source=IntentSource.LLM, raw="wipe it")
    _dispatch(registry, AuditLog(path), rogue)
    [entry] = _lines(path)
    assert (entry["source"], entry["decision"], entry["reason"]) == (
        "llm",
        "denied",
        "unknown_intent",
    )


def test_log_is_append_only(registry: CommandRegistry, tmp_path: Path) -> None:
    path = tmp_path / "decisions.jsonl"
    _dispatch(registry, AuditLog(path), IntentProposal(intent="browser.next_tab"))
    first = path.read_text(encoding="utf-8")
    _dispatch(registry, AuditLog(path), IntentProposal(intent="system.no_action"))
    assert path.read_text(encoding="utf-8").startswith(first)
    assert [e["decision"] for e in _lines(path)] == ["success", "denied"]


def test_hash_correlates_repeats_but_cannot_be_reversed_by_guessing(
    registry: CommandRegistry, tmp_path: Path
) -> None:
    """Invariant 5: an unsalted sha256 of a public alias is the transcript in disguise."""
    path = tmp_path / "decisions.jsonl"
    repeat = IntentProposal(intent="browser.next_tab", raw="next tab")
    _dispatch(registry, AuditLog(path), repeat, repeat)
    first, second = (e["transcript_hash"] for e in _lines(path))
    assert first == second
    assert first != hashlib.sha256(b"next tab").hexdigest()

    other = tmp_path / "other.jsonl"
    _dispatch(registry, AuditLog(other), repeat)
    assert _lines(other)[0]["transcript_hash"] != first  # key is per process, never stored


def test_missing_transcript_is_recorded_as_null(tmp_path: Path) -> None:
    path = tmp_path / "decisions.jsonl"
    AuditLog(path)(IntentProposal(intent="system.no_action"), ExecutionResult.unresolved(""))
    assert _lines(path)[0]["transcript_hash"] is None


def test_malformed_transcript_text_is_still_audited(tmp_path: Path) -> None:
    path = tmp_path / "decisions.jsonl"
    broken = IntentProposal(intent="system.no_action", raw="next " + chr(0xD800) + " tab")
    AuditLog(path, include_transcript=True)(broken, ExecutionResult.unresolved(""))
    assert _lines(path)[0]["intent"] == "system.no_action"


def test_unwritable_audit_never_breaks_the_voice_loop(
    registry: CommandRegistry, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _dispatch(registry, AuditLog(tmp_path), IntentProposal(intent="browser.next_tab"))
    assert any("audit" in r.getMessage() for r in caplog.records)


def test_dispatch_latency_is_recorded_for_diagnostics(
    registry: CommandRegistry, tmp_path: Path
) -> None:
    path = tmp_path / "decisions.jsonl"
    _dispatch(registry, AuditLog(path), IntentProposal(intent="browser.next_tab"))
    assert isinstance(_lines(path)[0]["latency_ms"], float)
