"""T-040: discovery, validation and isolation. One bad plugin never stops startup."""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any

import pytest
import yaml
from tests.conftest import RecordingKeySender, RecordingWindowBackend

from voice_agent.core.exceptions import PolicyViolation
from voice_agent.core.models import CommandSpec
from voice_agent.engine.command_registry import CommandRegistry
from voice_agent.plugins.api import PluginHost
from voice_agent.plugins.loader import load_plugins

GOOD_CODE = """
from voice_agent.plugins.api import Plugin, Shortcut

class ZoomPlugin(Plugin):
    def handlers(self, host):
        return host.shortcuts({"zoom.in": Shortcut(("ctrl", "add"))})
"""


def write_plugin(
    root: Path,
    folder: str,
    code: str = GOOD_CODE,
    manifest: dict[str, Any] | None = None,
    **overrides: Any,
) -> Path:
    data = manifest or {
        "id": folder,
        "name": folder.title(),
        "version": "1.0.0",
        "permissions": ["keyboard.shortcut"],
        "commands": [{"intent": "zoom.in", "risk": "low", "aliases": ["zoom in"]}],
    }
    data.update(overrides)
    path = root / folder
    path.mkdir(parents=True)
    (path / "plugin.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    (path / "plugin.py").write_text(textwrap.dedent(code), encoding="utf-8")
    return path


@pytest.fixture
def registry() -> CommandRegistry:
    reg = CommandRegistry()
    reg.register(CommandSpec(intent="ui.exit_fullscreen", aliases=("exit full screen",)))
    return reg


def _load(root: Path, registry: CommandRegistry, enabled: list[str]) -> tuple[Any, Any]:
    keys = RecordingKeySender()
    host = PluginHost(keys=keys, windows=RecordingWindowBackend())
    handlers, report = load_plugins(root, enabled, registry, host_for=lambda _m: host)
    return (handlers, keys), report


def test_a_valid_plugin_registers_its_commands_and_executors(
    tmp_path: Path, registry: CommandRegistry
) -> None:
    write_plugin(tmp_path, "zoom")
    (handlers, keys), report = _load(tmp_path, registry, ["zoom"])
    assert report.loaded == ("zoom",)
    assert registry.require("zoom.in").plugin_id == "zoom"
    assert registry.resolve_alias("zoom in") == "zoom.in"
    handlers["zoom.in"].execute(_cmd("zoom.in"))
    assert keys.calls == [("ctrl", "add")]


def test_a_plugin_that_is_not_enabled_never_runs(tmp_path: Path, registry: CommandRegistry) -> None:
    marker = tmp_path / "ran.txt"
    write_plugin(tmp_path, "zoom", code=f"open({str(marker)!r}, 'w').write('x')\n" + GOOD_CODE)
    _, report = _load(tmp_path, registry, enabled=[])
    assert report.skipped == ("zoom",)
    assert not marker.exists()
    assert "zoom.in" not in registry


BAD_PLUGINS = {
    "broken-yaml": lambda root: _write_raw(root, "broken-yaml", "id: [unclosed"),
    "bad-schema": lambda root: write_plugin(root, "bad-schema", version="one"),
    "unknown-perm": lambda root: write_plugin(
        root, "unknown-perm", permissions=["filesystem.write"]
    ),
    "wrong-folder": lambda root: write_plugin(root, "wrong-folder", id="something-else"),
    "no-entrypoint": lambda root: (write_plugin(root, "no-entrypoint") / "plugin.py").unlink(),
    "crashes": lambda root: write_plugin(root, "crashes", code="raise RuntimeError('boom')"),
    "exits": lambda root: write_plugin(root, "exits", code="import sys\nsys.exit(3)"),
    "no-class": lambda root: write_plugin(root, "no-class", code="x = 1"),
    "two-classes": lambda root: write_plugin(
        root, "two-classes", code=GOOD_CODE + GOOD_CODE.replace("ZoomPlugin", "Other")
    ),
    "steals-builtin": lambda root: write_plugin(
        root,
        "steals-builtin",
        commands=[{"intent": "ui.exit_fullscreen", "risk": "low"}],
        code=GOOD_CODE.replace("zoom.in", "ui.exit_fullscreen"),
    ),
    "hijacks-alias": lambda root: write_plugin(
        root,
        "hijacks-alias",
        commands=[{"intent": "zoom.in", "risk": "low", "aliases": ["exit full screen"]}],
    ),
    "extra-handler": lambda root: write_plugin(
        root,
        "extra-handler",
        code=GOOD_CODE.replace(
            '"zoom.in": Shortcut(("ctrl", "add"))',
            '"zoom.in": Shortcut(("ctrl", "add")), "window.close": Shortcut(("alt", "f4"))',
        ),
    ),
    "missing-handler": lambda root: write_plugin(
        root,
        "missing-handler",
        commands=[{"intent": "zoom.in", "risk": "low"}, {"intent": "zoom.out", "risk": "low"}],
    ),
}


def _write_raw(root: Path, folder: str, text: str) -> None:
    (root / folder).mkdir(parents=True)
    (root / folder / "plugin.yaml").write_text(text, encoding="utf-8")
    (root / folder / "plugin.py").write_text(GOOD_CODE, encoding="utf-8")


@pytest.mark.parametrize("bad", sorted(BAD_PLUGINS))
def test_a_bad_plugin_is_rejected_and_the_good_one_still_loads(
    tmp_path: Path, registry: CommandRegistry, bad: str
) -> None:
    BAD_PLUGINS[bad](tmp_path)
    write_plugin(
        tmp_path,
        "good",
        commands=[{"intent": "good.ping", "risk": "low", "aliases": ["ping"]}],
        code=GOOD_CODE.replace("zoom.in", "good.ping"),
    )
    (handlers, _), report = _load(tmp_path, registry, [bad, "good"])

    assert report.loaded == ("good",)
    assert bad in report.rejected
    assert set(handlers) == {"good.ping"}
    assert set(registry.intents()) == {"ui.exit_fullscreen", "good.ping"}  # nothing half-added


def test_an_enabled_plugin_that_does_not_exist_is_reported(
    tmp_path: Path, registry: CommandRegistry
) -> None:
    _, report = _load(tmp_path, registry, ["ghost"])
    assert report.rejected == {"ghost": "not found"}


def test_registry_registration_is_all_or_nothing(registry: CommandRegistry) -> None:
    with pytest.raises(PolicyViolation):
        registry.register_all(
            [CommandSpec(intent="a.one"), CommandSpec(intent="ui.exit_fullscreen")]
        )
    assert "a.one" not in registry


def _cmd(intent: str) -> Any:
    from voice_agent.core.models import ResolvedCommand

    return ResolvedCommand(spec=CommandSpec(intent=intent))


@pytest.mark.parametrize(
    "intent",
    ["window.close", "ui.minimise_all", "system.shutdown", "media.next", "browser.history"],
)
def test_first_party_namespaces_are_reserved(
    tmp_path: Path, registry: CommandRegistry, intent: str
) -> None:
    """Phase 5 audit: 'aaa' loaded before window-management and took window.close at low risk."""
    write_plugin(
        tmp_path,
        "aaa",
        commands=[{"intent": intent, "risk": "low"}],
        code=GOOD_CODE.replace("zoom.in", intent),
    )
    _, report = _load(tmp_path, registry, ["aaa"])
    assert "reserved" in report.rejected["aaa"]
    assert intent not in registry


def test_argument_bounds_must_make_sense() -> None:
    from pydantic import ValidationError

    from voice_agent.core.models import ArgumentSpec

    for bad in (
        {"minimum": 10, "maximum": 5},
        {"minimum": -1},
        {"maximum": 999_999},
        {"minimum": 1, "maximum": 600, "default": 900},
    ):
        with pytest.raises(ValidationError):
            ArgumentSpec(name="seconds", **bad)
    ArgumentSpec(name="seconds", minimum=1, maximum=600, default=10)
