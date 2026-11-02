"""Preferences and opt-in command history in SQLite; purge must really remove the text."""

from __future__ import annotations

from pathlib import Path

from voice_agent.core.models import ExecutionResult, ExecutionStatus
from voice_agent.settings.paths import data_dir
from voice_agent.storage.command_history import CommandHistory
from voice_agent.storage.preferences import Preferences

SECRET_PHRASE = "open my bank statement from tuesday"
OK = ExecutionResult(status=ExecutionStatus.SUCCESS, intent="browser.next_tab", message="Done")


def test_data_dir_is_per_user_not_the_working_directory(tmp_path: Path) -> None:
    assert data_dir({"LOCALAPPDATA": str(tmp_path)}) == tmp_path / "voice-pc-agent"
    assert data_dir({}).name == "voice-pc-agent"


def test_preferences_round_trip_and_survive_a_restart(tmp_path: Path) -> None:
    db = tmp_path / "state" / "agent.sqlite3"
    prefs = Preferences(db)
    assert prefs.get("muted", False) is False
    prefs.set("muted", True)
    prefs.set("muted", True)  # upsert, not a duplicate row
    assert Preferences(db).get("muted", False) is True


def test_history_is_off_by_default_and_then_writes_nothing(tmp_path: Path) -> None:
    db = tmp_path / "agent.sqlite3"
    history = CommandHistory(db, enabled=False)
    history.record(SECRET_PHRASE, OK)
    assert history.recent() == []
    assert not db.exists()  # nothing on disk at all


def test_history_records_newest_first_when_opted_in(tmp_path: Path) -> None:
    history = CommandHistory(tmp_path / "agent.sqlite3", enabled=True)
    history.record("next tab", OK)
    history.record("previous tab", OK.model_copy(update={"intent": "browser.previous_tab"}))
    newest, oldest = history.recent()
    assert (newest.transcript, newest.intent) == ("previous tab", "browser.previous_tab")
    assert (oldest.transcript, oldest.status) == ("next tab", "success")
    assert len(history.recent(limit=1)) == 1


def test_purge_removes_the_text_from_the_file_itself(tmp_path: Path) -> None:
    db = tmp_path / "agent.sqlite3"
    history = CommandHistory(db, enabled=True)
    for _ in range(50):
        history.record(SECRET_PHRASE, OK)
    assert SECRET_PHRASE.encode() in db.read_bytes()
    history.purge()
    assert history.recent() == []
    assert SECRET_PHRASE.encode() not in db.read_bytes()  # VACUUM, not just DELETE


def test_purge_works_even_when_history_is_now_off(tmp_path: Path) -> None:
    db = tmp_path / "agent.sqlite3"
    CommandHistory(db, enabled=True).record(SECRET_PHRASE, OK)
    CommandHistory(db, enabled=False).purge()  # the user turned it off, then clicked purge
    assert SECRET_PHRASE.encode() not in db.read_bytes()


def test_history_and_preferences_share_one_file_safely(tmp_path: Path) -> None:
    db = tmp_path / "agent.sqlite3"
    Preferences(db).set("muted", True)
    history = CommandHistory(db, enabled=True)
    history.record("next tab", OK)
    history.purge()
    assert Preferences(db).get("muted", False) is True  # purge clears history only
