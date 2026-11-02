"""Append-only JSONL audit of every dispatch decision.

The raw transcript is written only when the caller allows it (store_transcripts on and
redaction off). Otherwise only a keyed hash: it correlates repeats of a phrase within one
run, but its key lives only in memory, so the file alone cannot be reversed by guessing
common phrases (invariant 5).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import threading
from datetime import UTC, datetime
from pathlib import Path

from voice_agent.core.models import ExecutionResult, IntentProposal, IntentSource

log = logging.getLogger(__name__)


class AuditLog:
    """A Dispatcher audit sink: call it with (proposal, result) after every decision."""

    def __init__(self, path: Path, include_transcript: bool = False) -> None:
        self._path = path
        self._include_transcript = include_transcript
        self._lock = threading.Lock()
        self._key = secrets.token_bytes(32)

    def _hash(self, text: str) -> str:
        # surrogatepass: a malformed transcript must still hash, not crash the audit.
        data = text.encode("utf-8", "surrogatepass")
        return hmac.new(self._key, data, hashlib.sha256).hexdigest()

    def __call__(self, proposal: IntentProposal, result: ExecutionResult) -> None:
        raw = proposal.raw
        entry: dict[str, object] = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "transcript_hash": self._hash(raw) if raw else None,
            "intent": proposal.intent,
            "source": "local" if proposal.source is IntentSource.ALIAS else proposal.source.value,
            "decision": result.status.value,
            "reason": result.detail.get("reason", "ok"),
            # For the diagnostics report's dispatch percentiles.
            "latency_ms": result.latency_ms,
        }
        if self._include_transcript:
            entry["transcript"] = raw
        # ASCII-escaped JSON: any transcript text, even unpaired surrogates, is writable.
        line = json.dumps(entry) + "\n"

        try:
            with self._lock:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                with self._path.open("a", encoding="utf-8") as handle:
                    handle.write(line)
        except OSError as exc:
            # The action already happened; losing an audit line must not stop the agent.
            log.error("could not write audit entry to %s: %s", self._path, exc)
