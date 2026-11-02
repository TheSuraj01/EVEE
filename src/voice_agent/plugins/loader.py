"""Discovers plugins/*/plugin.yaml and loads the ones enabled in config.

This is the only module that loads code dynamically (a contract test holds it to that).
Its inputs are folder names from config and files on disk — never a transcript or a model
reply. Each plugin loads in isolation: a bad manifest, any Exception or a sys.exit() from
its code, a hijacked intent or alias — that plugin is rejected and reported, its commands
are not registered, and startup carries on. (KeyboardInterrupt is deliberately not caught:
Ctrl+C during startup should still quit.)
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from collections.abc import Callable, Collection
from pathlib import Path
from types import ModuleType

from voice_agent.core.exceptions import PluginError
from voice_agent.core.interfaces import ActionHandler
from voice_agent.core.models import PluginLoadReport
from voice_agent.engine.command_registry import CommandRegistry
from voice_agent.plugins.api import Plugin, PluginHost
from voice_agent.plugins.manifest import PluginManifest
from voice_agent.plugins.validator import check_handlers, read_manifest
from voice_agent.settings.loader import DEFAULT_CONFIG_DIR

log = logging.getLogger(__name__)

PLUGINS_DIR = DEFAULT_CONFIG_DIR.parent / "plugins"


def load_plugins(
    directory: Path,
    enabled: Collection[str],
    registry: CommandRegistry,
    host_for: Callable[[PluginManifest], PluginHost],
) -> tuple[dict[str, ActionHandler], PluginLoadReport]:
    """Register each enabled plugin's commands and return its executors."""
    handlers: dict[str, ActionHandler] = {}
    loaded: list[str] = []
    skipped: list[str] = []
    rejected: dict[str, str] = {}
    found = sorted(path.parent.name for path in directory.glob("*/plugin.yaml"))

    for folder in found:
        if folder not in enabled:
            skipped.append(folder)  # discovered, but its code is never run
            continue
        try:
            manifest = read_manifest(directory / folder / "plugin.yaml")
            plugin = _instantiate(directory / folder / manifest.entrypoint, manifest.id)
            executors = dict(plugin.handlers(host_for(manifest)))
            check_handlers(manifest, executors)
            registry.register_all(manifest.specs())  # all or nothing
        except (Exception, SystemExit) as exc:  # isolation is the point of this loop
            reason = f"{type(exc).__name__}: {exc}"[:300]
            log.warning("plugin %r rejected: %s", folder, reason)
            rejected[folder] = reason
            continue
        handlers.update(executors)
        loaded.append(manifest.id)

    for missing in sorted(set(enabled) - set(found)):
        rejected[missing] = "not found"
    return handlers, PluginLoadReport(
        loaded=tuple(loaded), skipped=tuple(skipped), rejected=rejected
    )


def _instantiate(path: Path, plugin_id: str) -> Plugin:
    module = _import(path, f"voice_agent_plugin_{plugin_id.replace('-', '_')}")
    classes = [
        obj
        for obj in vars(module).values()
        if isinstance(obj, type)
        and issubclass(obj, Plugin)
        and obj is not Plugin
        and obj.__module__ == module.__name__
    ]
    if len(classes) != 1:
        msg = f"{plugin_id}: expected exactly one Plugin subclass, found {len(classes)}"
        raise PluginError(msg)
    return classes[0]()


def _import(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        msg = f"cannot load {path.name}"
        raise PluginError(msg)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses and typing resolve names through sys.modules
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module
