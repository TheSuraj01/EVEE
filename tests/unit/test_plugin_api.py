"""T-039: the manifest model (in step with the published JSON schema) and the plugin API."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError
from tests.conftest import RecordingKeySender, RecordingWindowBackend

from voice_agent.core.constants import KNOWN_PERMISSIONS
from voice_agent.core.models import CommandSpec, ResolvedCommand, Risk
from voice_agent.plugins.api import Plugin, PluginHost, Shortcut
from voice_agent.plugins.manifest import PluginManifest

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = json.loads((ROOT / "specs" / "plugin-manifest.schema.json").read_text(encoding="utf-8"))


def _manifest(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": "zoom",
        "name": "Zoom",
        "version": "1.0.0",
        "permissions": ["keyboard.shortcut"],
        "commands": [{"intent": "zoom.in", "risk": "low", "aliases": ["zoom in"]}],
    }
    data.update(overrides)
    return data


def test_model_matches_the_published_schema() -> None:
    """specs/plugin-manifest.schema.json is the contract third parties write against."""
    props, fields = SCHEMA["properties"], PluginManifest.model_fields
    assert set(SCHEMA["required"]) == {n for n, f in fields.items() if f.is_required()}
    assert set(props) == set(fields)
    assert set(props["permissions"]["items"]["enum"]) == set(KNOWN_PERMISSIONS)
    command = props["commands"]["items"]
    assert set(command["properties"]["risk"]["enum"]) == {r.value for r in Risk}
    for name, pattern in (("id", props["id"]["pattern"]), ("version", props["version"]["pattern"])):
        assert any(getattr(m, "pattern", None) == pattern for m in fields[name].metadata), name


@pytest.mark.parametrize("path", sorted((ROOT / "plugins").glob("*/plugin.yaml")))
def test_shipped_manifests_are_valid(path: Path) -> None:
    PluginManifest.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


@pytest.mark.parametrize(
    ("override", "field"),
    [
        ({"id": "Zoom!"}, "id"),
        ({"version": "1.0"}, "version"),
        ({"permissions": ["filesystem.write"]}, "permissions"),
        ({"permissions": ["keyboard.shortcut", "keyboard.shortcut"]}, "permissions"),
        ({"commands": []}, "commands"),
        ({"commands": [{"intent": "NotDotted", "risk": "low"}]}, "commands"),
        ({"commands": [{"intent": "zoom.in", "risk": "catastrophic"}]}, "commands"),
        ({"entrypoint": "../../src/voice_agent/main.py"}, "entrypoint"),
        ({"entrypoint": "C:\\evil.py"}, "entrypoint"),
        ({"grants": ["everything"]}, "grants"),
    ],
)
def test_bad_manifests_are_refused(override: dict[str, Any], field: str) -> None:
    with pytest.raises(ValidationError, match=field):
        PluginManifest.model_validate(_manifest(**override))


def test_duplicate_intents_in_one_manifest_are_refused() -> None:
    twice = [{"intent": "zoom.in", "risk": "low"}, {"intent": "zoom.in", "risk": "high"}]
    with pytest.raises(ValidationError, match="duplicate intent"):
        PluginManifest.model_validate(_manifest(commands=twice))


def test_manifest_becomes_registry_specs_owned_by_the_plugin() -> None:
    manifest = PluginManifest.model_validate(_manifest())
    [spec] = manifest.specs()
    assert spec == CommandSpec(
        intent="zoom.in",
        aliases=("zoom in",),
        risk=Risk.LOW,
        permissions=("keyboard.shortcut",),
        plugin_id="zoom",
    )


def test_a_plugin_must_implement_handlers() -> None:
    class Empty(Plugin):
        pass

    with pytest.raises(TypeError):
        Empty()  # type: ignore[abstract]


def test_host_builds_shortcut_and_window_executors_for_a_plugin() -> None:
    keys, windows = RecordingKeySender(), RecordingWindowBackend()
    host = PluginHost(keys=keys, windows=windows)

    shortcuts = host.shortcuts({"zoom.in": Shortcut(("ctrl", "add"))})
    shortcuts["zoom.in"].execute(ResolvedCommand(spec=CommandSpec(intent="zoom.in")))
    assert keys.calls == [("ctrl", "add")]

    window = host.window_actions()
    assert set(window) == {"window.minimize", "window.maximize", "window.close"}
    window["window.close"].execute(ResolvedCommand(spec=CommandSpec(intent="window.close")))
    assert windows.calls == ["close"]
