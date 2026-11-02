"""The release pipeline, checked as data: GitHub runs it, but its shape is a contract.
Tag -> version check -> full gate -> build + smoke test -> zip + SHA-256 -> release whose
notes carry a privacy summary that matches the code."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from voice_agent.core.constants import MAX_TRANSCRIPT_CHARS

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str) -> dict[Any, Any]:
    return yaml.safe_load((ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8"))


def _job() -> dict[str, Any]:
    return _load("release.yml")["jobs"]["windows-build"]  # type: ignore[no-any-return]


def _index(steps: list[dict[str, Any]], needle: str) -> int:
    return next(i for i, step in enumerate(steps) if needle in str(step))


def test_a_version_tag_is_gated_built_checksummed_and_released_in_that_order() -> None:
    workflow = _load("release.yml")
    trigger = workflow.get("on", workflow.get(True))  # YAML 1.1 reads a bare `on` as True
    steps = _job()["steps"]

    assert trigger == {"push": {"tags": ["v*"]}}
    order = [
        _index(steps, "GITHUB_REF_NAME"),  # tag must equal v + __version__
        _index(steps, "pytest -q -m slow"),
        _index(steps, "build_windows.ps1"),
        _index(steps, "sha256sum"),
        _index(steps, "softprops/action-gh-release"),
    ]
    assert order == sorted(order)
    gate = steps[order[1]]["run"]
    for check in ("python -m pytest -q\n", "ruff check .", "ruff format --check .", "mypy src"):
        assert check in gate


def test_multi_command_steps_stop_at_the_first_failure() -> None:
    """pwsh only reports a step's last exit code; bash -e stops at the first failure."""
    for name, job in (("release.yml", "windows-build"), ("ci.yml", "gate")):
        assert _load(name)["jobs"][job]["defaults"]["run"]["shell"] == "bash", name


def test_release_notes_carry_a_privacy_summary_that_matches_the_code() -> None:
    release = next(s for s in _job()["steps"] if "action-gh-release" in s.get("uses", ""))
    body = release["with"]["body"]

    assert "SHA256SUMS.txt" in release["with"]["files"]
    assert "## Privacy" in body
    for fact in (
        "never written to disk",
        f"{MAX_TRANSCRIPT_CHARS} characters",
        "your own Groq API key",
        "privacy.redact_sensitive_logs: false",  # the one case transcripts reach the audit log
        "Credential Manager",
        "No telemetry",
        "docs/privacy.md",
    ):
        assert fact in body, fact
