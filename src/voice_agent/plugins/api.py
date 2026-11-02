"""The public plugin API.

A plugin is a class with one method: given the capabilities the host grants it, return an
executor for each intent in its manifest. Whether a command may run is never the plugin's
business — the policy engine decides that before any executor is called. The capabilities
in `PluginHost` are already limited to what the manifest declared (see permissions.py).

    from voice_agent.plugins.api import Plugin, PluginHost, Shortcut

    class ZoomPlugin(Plugin):
        def handlers(self, host: PluginHost):
            return host.shortcuts({"zoom.in": Shortcut(("ctrl", "add"))})
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from voice_agent.actions.keyboard import KeyboardActions, Shortcut
from voice_agent.actions.windows import WindowActions
from voice_agent.core.interfaces import ActionHandler, KeySender, WindowBackend
from voice_agent.core.models import ExecutionResult, ExecutionStatus, ResolvedCommand

__all__ = [
    "ActionHandler",
    "ExecutionResult",
    "ExecutionStatus",
    "Plugin",
    "PluginHost",
    "ResolvedCommand",
    "Shortcut",
]


def _never() -> bool:
    return False


@dataclass(frozen=True)
class PluginHost:
    """What a plugin is given — and all it is given."""

    keys: KeySender
    windows: WindowBackend
    #: True once the command being executed should stop (emergency stop, mute).
    interrupted: Callable[[], bool] = field(default=_never)

    def shortcuts(self, table: Mapping[str, Shortcut]) -> dict[str, ActionHandler]:
        """Executors that send key chords, interruptible between presses."""
        handler = KeyboardActions(self.keys, table, self.interrupted)
        return dict.fromkeys(table, handler)

    def window_actions(self) -> dict[str, ActionHandler]:
        """Executors for window.minimize / window.maximize / window.close."""
        handler = WindowActions(self.windows)
        return dict.fromkeys(handler.intents, handler)


class Plugin(ABC):
    @abstractmethod
    def handlers(self, host: PluginHost) -> Mapping[str, ActionHandler]:
        """intent -> executor, for exactly the intents in this plugin's manifest."""
