"""'Copy diagnostics': useful for a bug report, and safe to paste into one."""

from __future__ import annotations

import json
from pathlib import Path

from voice_agent.core.models import AudioDevice
from voice_agent.observability.diagnostics import build_report
from voice_agent.settings.loader import load_settings

KEY = "gsk_" + "Fake0nlyForTests1234567890"
MICS = [AudioDevice(index=1, name="USB Mic", channels=1, default_samplerate=48000)]


def _audit(path: Path, count: int, transcript: str | None = None) -> None:
    lines = []
    for i in range(count):
        entry = {
            "ts": f"2026-10-20T10:00:{i:02d}.000+00:00",
            "transcript_hash": "ab" * 32,
            "intent": f"intent.{i}",
            "source": "local",
            "decision": "success",
            "reason": "ok",
            "latency_ms": float(10 + i),
        }
        if transcript is not None:
            entry["transcript"] = transcript
        lines.append(json.dumps(entry))
    path.write_text("\n".join(lines) + "\nnot json\n", encoding="utf-8")


def _log(path: Path, stt_ms: list[float]) -> None:
    lines = [json.dumps({"msg": "transcribed", "stage": "stt", "latency_ms": ms}) for ms in stt_ms]
    lines.append(json.dumps({"msg": f"auth header {KEY}"}))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _report(tmp_path: Path, **kwargs: object) -> str:
    defaults: dict[str, object] = {
        "audit_path": tmp_path / "decisions.jsonl",
        "log_path": tmp_path / "voice-agent.log",
        "devices": MICS,
        "key_configured": True,
    }
    defaults.update(kwargs)
    return build_report(load_settings(env={}), **defaults)  # type: ignore[arg-type]


def test_report_has_versions_config_and_devices(tmp_path: Path) -> None:
    report = _report(tmp_path)
    assert "Voice PC Agent 0.1.0" in report
    assert "Python 3.12" in report
    assert "qwen/qwen3.8-27b" in report
    assert "Groq API key: configured" in report
    assert "1: USB Mic" in report


def test_only_the_last_20_decisions_and_never_transcripts(tmp_path: Path) -> None:
    _audit(tmp_path / "decisions.jsonl", 25, transcript="open my bank statement")
    report = _report(tmp_path)
    assert "intent.24" in report
    assert "intent.5" in report
    assert "intent.4" not in report  # 25 entries, last 20 kept
    assert "bank" not in report
    assert "ab" * 32 not in report  # hashes add nothing to a bug report


def test_latency_percentiles_per_stage(tmp_path: Path) -> None:
    _audit(tmp_path / "decisions.jsonl", 20)
    _log(tmp_path / "voice-agent.log", [400.0] * 19 + [2000.0])
    report = _report(tmp_path)
    assert "stt: p50 400 ms, p95 1920 ms (n=20)" in report
    assert "dispatch: p50 20 ms" in report


def test_secrets_are_masked_wherever_they_come_from(tmp_path: Path) -> None:
    _log(tmp_path / "voice-agent.log", [400.0])
    report = _report(tmp_path, devices=[MICS[0].model_copy(update={"name": f"Mic {KEY}"})])
    assert KEY not in report
    assert "gsk_***" in report


def test_missing_files_are_reported_not_fatal(tmp_path: Path) -> None:
    report = _report(tmp_path, devices=[], key_configured=False)
    assert "Groq API key: not configured" in report
    assert "no decisions recorded" in report
    assert "no latency samples" in report
    assert "no input devices" in report


def test_plugins_section_says_why_a_plugin_was_refused(tmp_path: Path) -> None:
    from voice_agent.core.models import PluginLoadReport

    report = _report(
        tmp_path,
        plugins=PluginLoadReport(
            loaded=("browser",), skipped=("zoom",), rejected={"ghost": "not found"}
        ),
    )
    assert "loaded: browser" in report
    assert "not enabled: zoom" in report
    assert "rejected: ghost (not found)" in report
