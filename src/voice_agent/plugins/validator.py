"""Everything checkable about a plugin before, and right after, its code runs.

The manifest is untrusted input: every refusal is a PluginError that names the plugin
folder and the problem, so the user can tell which plugin was turned away and why.
"""

from __future__ import annotations

from collections.abc import Collection
from pathlib import Path

import yaml
from pydantic import ValidationError

from voice_agent.core.exceptions import PluginError
from voice_agent.plugins.manifest import PluginManifest

#: First-party namespaces. A third-party plugin claiming one could load first and take a
#: built-in or shipped command — e.g. window.close at low risk, skipping confirmation.
RESERVED_NAMESPACES = {
    "ui": "builtin",
    "system": "builtin",
    "app": "builtin",
    "web": "builtin",
    "browser": "browser",
    "media": "media",
    "window": "window-management",
    "edit": "editing",
    "page": "editing",
    "desktop": "desktop",
    "zoom": "zoom",
}


def read_manifest(path: Path) -> PluginManifest:
    """plugin.yaml -> manifest, or PluginError."""
    folder = path.parent.name
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        msg = f"{folder}: plugin.yaml is unreadable or not valid YAML"
        raise PluginError(msg) from exc
    try:
        manifest = PluginManifest.model_validate(data)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in exc.errors()
        )
        msg = f"{folder}: invalid manifest ({problems})"
        raise PluginError(msg) from None
    if manifest.id != folder:
        msg = f"{folder}: manifest id {manifest.id!r} must match its folder name"
        raise PluginError(msg)
    for command in manifest.commands:
        namespace = command.intent.split(".")[0]
        owner = RESERVED_NAMESPACES.get(namespace)
        if owner is not None and owner != manifest.id:
            msg = f"{folder}: {command.intent} is in the reserved '{namespace}' namespace"
            raise PluginError(msg)
    if not (path.parent / manifest.entrypoint).is_file():
        msg = f"{folder}: entrypoint {manifest.entrypoint} not found"
        raise PluginError(msg)
    return manifest


def check_handlers(manifest: PluginManifest, intents: Collection[str]) -> None:
    """A plugin executes exactly what it declared: nothing outside its manifest, and no
    declared command left without an executor."""
    declared = {command.intent for command in manifest.commands}
    extra = sorted(set(intents) - declared)
    if extra:
        msg = f"{manifest.id}: executors for undeclared intents {extra}"
        raise PluginError(msg)
    missing = sorted(declared - set(intents))
    if missing:
        msg = f"{manifest.id}: no executor for declared intents {missing}"
        raise PluginError(msg)
