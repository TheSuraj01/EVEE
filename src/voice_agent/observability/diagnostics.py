"""'Copy diagnostics': a plain-text report to paste into a bug report.

Versions, a config summary, input devices, latency percentiles and the last decisions.
No transcripts, no transcript hashes, no key — only whether one is configured — and the
whole text passes through the key redactor before it leaves the app.
"""

from __future__ import annotations

import json
import platform
import statistics
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from voice_agent import __version__
from voice_agent.core.models import AudioDevice, PluginLoadReport
from voice_agent.observability.logging import redact
from voice_agent.settings.schema import Settings

PACKAGES = (
    "faster-whisper",
    "openwakeword",
    "sounddevice",
    "webrtcvad-wheels",
    "groq",
    "PySide6",
    "pywin32",
    "pyautogui",
    "pydantic",
    "numpy",
)
DECISIONS = 20


def build_report(
    settings: Settings,
    *,
    audit_path: Path,
    log_path: Path,
    devices: list[AudioDevice],
    key_configured: bool,
    plugins: PluginLoadReport | None = None,
) -> str:
    """Everything a maintainer needs to reproduce a problem, nothing the user said."""
    wake, speech, llm, privacy = settings.wakeword, settings.speech, settings.llm, settings.privacy
    audit = _json_lines(audit_path)
    lines = [
        f"Voice PC Agent {__version__} diagnostics, {datetime.now(UTC):%Y-%m-%d %H:%M} UTC",
        "Review before sharing: device names can contain personal names.",
        f"Python {platform.python_version()} on {platform.platform()}",
        "Packages: " + ", ".join(f"{name} {_version(name)}" for name in PACKAGES),
        "",
        "Config:",
        f"  wake word: {_on(wake.enabled)} "
        + (
            f"(phrases: '{wake.wake_phrase}' / '{wake.sleep_phrase}')"
            if wake.engine == "phrase"
            else f"({wake.model}, threshold {wake.threshold})"
        ),
        f"  speech: {speech.model} on {speech.device} ({speech.compute_type})",
        f"  cloud: {_on(llm.enabled)} ({llm.model}, timeout {llm.timeout_seconds:g} s)",
        f"  Groq API key: {'configured' if key_configured else 'not configured'}",
        f"  store transcripts: {_on(privacy.store_transcripts)}, "
        f"audit log: {_on(privacy.audit_log_enabled)}",
        f"  input device setting: {settings.audio.input_device or 'system default'}",
        "",
        "Input devices:",
        *([f"  {d.index}: {d.name}" for d in devices] or ["  no input devices"]),
        "",
        "Plugins:",
        *_plugins(plugins),
        "",
        "Latency:",
        *_latencies(_json_lines(log_path), audit),
        "",
        f"Last {DECISIONS} decisions:",
        *([_decision(entry) for entry in audit[-DECISIONS:]] or ["  no decisions recorded"]),
    ]
    return redact("\n".join(lines))


def _plugins(report: PluginLoadReport | None) -> list[str]:
    if report is None:
        return ["  not loaded in this process"]
    return [
        f"  loaded: {', '.join(report.loaded) or 'none'}",
        f"  not enabled: {', '.join(report.skipped) or 'none'}",
        *(f"  rejected: {name} ({reason})" for name, reason in report.rejected.items()),
    ]


def _decision(entry: dict[str, Any]) -> str:
    # Deliberately field by field: a transcript (or its hash) can never slip in.
    latency = entry.get("latency_ms")
    took = f" {latency:.0f} ms" if isinstance(latency, int | float) else ""
    return (
        f"  {entry.get('ts', '?')} {entry.get('intent', '?')} [{entry.get('source', '?')}] "
        f"{entry.get('decision', '?')} ({entry.get('reason', '?')}){took}"
    )


def _latencies(log: list[dict[str, Any]], audit: list[dict[str, Any]]) -> list[str]:
    stages = {
        "stt": [e["latency_ms"] for e in log if e.get("stage") == "stt" and _number(e)],
        "dispatch": [e["latency_ms"] for e in audit if _number(e)],
    }
    lines = []
    for stage, values in stages.items():
        if not values:
            continue
        p50 = statistics.median(values)
        p95 = statistics.quantiles(values, n=20)[18] if len(values) > 1 else values[0]
        lines.append(f"  {stage}: p50 {p50:.0f} ms, p95 {p95:.0f} ms (n={len(values)})")
    return lines or ["  no latency samples yet"]


def _number(entry: dict[str, Any]) -> bool:
    return isinstance(entry.get("latency_ms"), int | float)


def _json_lines(path: Path) -> list[dict[str, Any]]:
    """Parse what can be parsed: a half-written or foreign line must not sink the report."""
    if not path.is_file():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict):
            entries.append(entry)
    return entries


def _version(package: str) -> str:
    try:
        return version(package)
    except PackageNotFoundError:
        return "not installed"


def _on(flag: bool) -> str:
    return "on" if flag else "off"
