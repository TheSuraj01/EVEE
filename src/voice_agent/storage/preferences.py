"""Small state that should survive a restart — e.g. "the user muted". Not configuration:
that lives in config/user.yaml. Values are JSON; one connection per call keeps it
thread-safe for the voice and GUI threads at this tiny write rate."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

SCHEMA = "CREATE TABLE IF NOT EXISTS preferences (key TEXT PRIMARY KEY, value TEXT NOT NULL)"


class Preferences:
    def __init__(self, db_path: Path) -> None:
        self._path = db_path

    def get(self, key: str, default: Any) -> Any:
        with closing(self._connect()) as db:
            row = db.execute("SELECT value FROM preferences WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key: str, value: Any) -> None:
        with closing(self._connect()) as db, db:
            db.execute(
                "INSERT INTO preferences (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, json.dumps(value)),
            )

    def _connect(self) -> sqlite3.Connection:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self._path)
        db.execute(SCHEMA)
        return db
