"""The user docs can't silently drift from the code: every command and phrase is in the
command reference, and every relative link (and #anchor) points somewhere real."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from voice_agent.core.models import NO_ACTION_INTENT
from voice_agent.plugins.loader import PLUGINS_DIR

ROOT = Path(__file__).resolve().parents[2]
DOCS = sorted([ROOT / "README.md", *(ROOT / "docs").rglob("*.md")])
LINK = re.compile(r"\]\(([^)\s]+)\)")


def _slug(heading: str) -> str:
    """GitHub's anchor for a heading."""
    return re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")


def test_command_reference_lists_every_command_and_phrase(shipped_with: Any) -> None:
    every_plugin = sorted(path.parent.name for path in PLUGINS_DIR.glob("*/plugin.yaml"))
    registry = shipped_with(every_plugin).agent.registry
    reference = (ROOT / "docs" / "command-reference.md").read_text(encoding="utf-8")

    missing = [
        text
        for spec in registry
        if spec.intent != NO_ACTION_INTENT
        for text in (f"`{spec.intent}`", *spec.aliases)
        if text not in reference
    ]
    assert missing == []


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: str(p.relative_to(ROOT)))
def test_relative_links_resolve(doc: Path) -> None:
    broken = []
    for target in LINK.findall(doc.read_text(encoding="utf-8")):
        if target.startswith(("http://", "https://", "mailto:")):
            continue
        file_part, _, anchor = target.partition("#")
        path = (doc.parent / file_part).resolve() if file_part else doc
        if not path.exists():
            broken.append(target)
        elif anchor and path.suffix == ".md":
            headings = re.findall(r"^#+ (.+)$", path.read_text(encoding="utf-8"), re.M)
            if anchor not in {_slug(h) for h in headings}:
                broken.append(target)
    assert broken == []
