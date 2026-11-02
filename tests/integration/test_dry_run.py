"""The first demoable milestone: type a phrase, watch it go through the real pipeline."""

from __future__ import annotations

import io
import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from voice_agent import main as cli
from voice_agent.core.exceptions import ConfigError
from voice_agent.safety.confirmation import ConsolePrompt
from voice_agent.settings.loader import load_settings

SESSION = [
    "next tab",
    "skip forward 30 seconds",
    "close this window",
    "delete all my files",
    "",
    "quit",
]


def _feed(monkeypatch: pytest.MonkeyPatch, lines: list[str]) -> None:
    it: Iterator[str] = iter(lines)

    def fake_input(_prompt: str = "") -> str:
        try:
            return next(it)
        except StopIteration:
            raise EOFError from None

    monkeypatch.setattr("builtins.input", fake_input)


@pytest.fixture
def session(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> tuple[str, list[dict[str, object]]]:
    _feed(monkeypatch, SESSION)
    audit = tmp_path / "decisions.jsonl"
    declining = ConsolePrompt(read_key=lambda _timeout: "n", out=io.StringIO())
    assert cli.run_dry_run(load_settings(env={}), prompt=declining, audit_path=audit) == 0
    entries = [json.loads(line) for line in audit.read_text(encoding="utf-8").splitlines()]
    return capsys.readouterr().out, entries


def test_known_phrase_shows_resolution_policy_and_simulated_keys(
    session: tuple[str, list[dict[str, object]]],
) -> None:
    out, _ = session
    assert "browser.next_tab (alias, confidence 1.00)" in out
    assert "policy:    allow (ok)" in out
    assert "simulated: ctrl+tab" in out


def test_duration_reaches_the_simulated_keyboard(
    session: tuple[str, list[dict[str, object]]],
) -> None:
    out, _ = session
    assert "media.seek_forward (alias, confidence 1.00, 30s)" in out
    assert "simulated: right x6" in out


def test_high_risk_is_refused_without_confirmation(
    session: tuple[str, list[dict[str, object]]],
) -> None:
    out, _ = session
    assert "policy:    confirm (confirmation_required)" in out
    assert "Cancelled — not confirmed." in out
    assert "close foreground window" not in out


def test_unknown_phrase_is_unrecognised_and_does_nothing(
    session: tuple[str, list[dict[str, object]]],
) -> None:
    out, _ = session
    assert "didn't recognise" in out
    assert "policy:    deny (no_action)" in out


def test_every_decision_is_audited(session: tuple[str, list[dict[str, object]]]) -> None:
    _, entries = session
    assert [e["decision"] for e in entries] == ["success", "success", "denied", "denied"]
    assert all("transcript" not in e for e in entries)


def test_end_of_input_exits_cleanly(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _feed(monkeypatch, [])
    assert cli.run_dry_run(load_settings(env={}), audit_path=tmp_path / "a.jsonl") == 0


def test_bad_config_exits_with_a_message(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def broken() -> None:
        raise ConfigError("invalid config at 'llm.timeout_seconds'")

    monkeypatch.setattr(cli, "load_settings", broken)
    assert cli.main(["--dry-run"]) == 2
    assert "llm.timeout_seconds" in capsys.readouterr().err


@pytest.fixture
def quiet_main(monkeypatch: pytest.MonkeyPatch) -> list[bool]:
    """main() without touching global logging, .env, the network or any audio hardware."""
    live: list[bool] = []
    monkeypatch.setattr(cli, "configure_logging", lambda **_: None)
    monkeypatch.setattr(cli, "run_live", lambda settings, use_llm: live.append(use_llm) or 0)
    return live


def test_live_mode_uses_the_cloud_by_default(quiet_main: list[bool]) -> None:
    assert cli.main([]) == 0
    assert quiet_main == [True]


def test_no_llm_keeps_the_live_pipeline_local(quiet_main: list[bool]) -> None:
    assert cli.main(["--no-llm"]) == 0
    assert quiet_main == [False]


def test_tray_flag_starts_the_tray_app(
    quiet_main: list[bool], monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("PySide6")
    import voice_agent.ui.application as ui

    trays: list[bool] = []
    monkeypatch.setattr(ui, "run_tray", lambda settings, use_llm: trays.append(use_llm) or 0)
    assert cli.main(["--tray", "--no-llm"]) == 0
    assert trays == [False]
    assert quiet_main == []  # the console pipeline did not start
