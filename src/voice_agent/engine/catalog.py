"""Loads the built-in command table from YAML into CommandSpec objects.

Kept separate from the registry so that plugins and built-ins share one registration path
and one validation path.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from voice_agent.core.exceptions import ConfigError
from voice_agent.core.models import ArgumentSpec, CommandSpec, Risk, TextArgument
from voice_agent.settings.paths import APP_ROOT

DEFAULT_COMMANDS_FILE = APP_ROOT / "config" / "commands.yaml"


def load_command_specs(path: Path | None = None) -> tuple[CommandSpec, ...]:
    source = path or DEFAULT_COMMANDS_FILE
    try:
        data: Any = yaml.safe_load(source.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        msg = f"command table not found: {source}"
        raise ConfigError(msg) from exc
    except yaml.YAMLError as exc:
        msg = f"command table is not valid YAML: {source}"
        raise ConfigError(msg) from exc

    entries = (data or {}).get("commands")
    if not isinstance(entries, list):
        msg = "command table must contain a top-level 'commands' list"
        raise ConfigError(msg)

    specs: list[CommandSpec] = []
    for entry in entries:
        specs.append(_to_spec(entry))
    return tuple(specs)


def _to_spec(entry: dict[str, Any]) -> CommandSpec:
    try:
        argument = entry.get("argument")
        text = entry.get("text")
        return CommandSpec(
            intent=entry["intent"],
            description=entry.get("description", ""),
            aliases=tuple(entry.get("aliases") or ()),
            risk=Risk(entry.get("risk", "low")),
            permissions=tuple(entry.get("permissions") or ()),
            argument=ArgumentSpec(**argument) if argument else None,
            text=TextArgument(**text) if text else None,
            plugin_id=entry.get("plugin_id", "builtin"),
        )
    except Exception as exc:
        msg = f"invalid command entry: {entry!r}"
        raise ConfigError(msg) from exc
