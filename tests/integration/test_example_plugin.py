"""The example plugin from docs/plugin-development.md, tested the way the guide recommends:
through the real loader, the permission-guarded host, and the agent."""

from __future__ import annotations

from typing import Any

import pytest
from tests.conftest import RecordingKeySender, RecordingWindowBackend

from voice_agent.core.models import ExecutionStatus, ResolvedCommand
from voice_agent.engine.command_registry import CommandRegistry
from voice_agent.engine.execution_context import ExecutionContext
from voice_agent.plugins.loader import PLUGINS_DIR, load_plugins
from voice_agent.plugins.permissions import plugin_host


def test_zoom_loads_on_its_own_with_recording_backends() -> None:
    """The guide's minimal test: no agent, just the loader and a fake keyboard."""
    keys, registry, context = RecordingKeySender(), CommandRegistry(), ExecutionContext()
    handlers, report = load_plugins(
        PLUGINS_DIR,
        ["zoom"],
        registry,
        host_for=lambda manifest: plugin_host(manifest, keys, RecordingWindowBackend(), context),
    )
    assert report.loaded == ("zoom",)
    assert registry.resolve_alias("enlarge text") == "zoom.in"

    spec = registry.require("zoom.in")
    with context.running(spec):  # what the dispatcher does around every executor
        handlers["zoom.in"].execute(ResolvedCommand(spec=spec))
    assert keys.calls == [("ctrl", "add")]


def test_zoom_is_on_by_default_and_can_be_turned_off(shipped_with: Any) -> None:
    assert "zoom.in" in shipped_with().agent.registry

    without = shipped_with(["browser", "media", "window-management"])
    assert "zoom" in without.agent.plugins.skipped
    assert "zoom.in" not in without.agent.registry


@pytest.mark.parametrize(
    ("phrase", "chord"),
    [
        ("zoom in", ("ctrl", "add")),
        ("shrink text", ("ctrl", "subtract")),
        ("actual size", ("ctrl", "0")),
    ],
)
def test_zoom_end_to_end_once_enabled(
    shipped_with: Any, phrase: str, chord: tuple[str, ...]
) -> None:
    rig = shipped_with(enabled=["browser", "media", "window-management", "zoom"])
    assert rig.agent.handle(phrase).status is ExecutionStatus.SUCCESS
    assert rig.keys.calls == [chord]
