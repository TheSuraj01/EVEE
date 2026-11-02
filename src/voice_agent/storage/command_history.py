"""What was heard and what happened — kept only if the user opted in.

Off by default: it follows `privacy.store_transcripts` (invariant 5), and while off it
does not even create the database. `purge()` deletes and VACUUMs, so the text does not
survive in SQLite's free pages either.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from voice_agent.core.models import ExecutionResult, HistoryEntry

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS history ("
    "id INTEGER PRIMARY KEY, ts TEXT NOT NULL, transcript TEXT NOT NULL, "
    "intent TEXT NOT NULL, status TEXT NOT NULL, message TEXT NOT NULL)"
)


class CommandHistory:
    def __init__(self, db_path: Path, enabled: bool) -> None:
        self._path = db_path
        self.enabled = enabled

    def record(self, transcript: str, result: ExecutionResult) -> None:
        if not self.enabled:
            return
        with closing(self._connect()) as db, db:
            db.execute(
                "INSERT INTO history (ts, transcript, intent, status, message) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    datetime.now(UTC).isoformat(timespec="seconds"),
                    transcript,
                    result.intent,
                    result.status.value,
                    result.message,
                ),
            )

    def recent(self, limit: int = 20) -> list[HistoryEntry]:
        if not self._path.exists():
            return []
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT ts, transcript, intent, status, message FROM history "
                "ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        fields = ("ts", "transcript", "intent", "status", "message")
        return [HistoryEntry(**dict(zip(fields, row, strict=True))) for row in rows]

    def purge(self) -> None:
        """One click, everything gone — including from the file's free pages."""
        if not self._path.exists():
            return
        with closing(self._connect()) as db:
            with db:
                db.execute("DELETE FROM history")
            db.execute("VACUUM")

    def _connect(self) -> sqlite3.Connection:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self._path)
        db.execute(SCHEMA)
        return db
