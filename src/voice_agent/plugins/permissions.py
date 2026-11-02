"""Host-side enforcement for plugin capabilities.

A plugin never receives a raw capability. Its keys and windows are wrappers that, on every
call, check that:

1. one of the plugin's *own* commands is executing, on this thread (so nothing happens
   while loading, from a background thread, or between commands);
2. the user has not stopped (mute / emergency stop);
3. the plugin declared the permission the call needs, and the host still grants it;
4. a destructive call (closing a window, Alt+F4, Delete...) comes from a command whose
   declared risk is high enough — so it went through confirmation.

What this is not: a sandbox. A plugin is Python running inside the agent; these wrappers
govern what the host hands it, and they keep honest plugins honest. A malicious plugin can
reach anything the process can, including the API key. The real boundary is that only the
plugins listed in config `plugins.enabled` are ever loaded.
"""

from __future__ import annotations

from voice_agent.core.constants import KNOWN_PERMISSIONS
from voice_agent.core.exceptions import Interrupted, PermissionDenied
from voice_agent.core.interfaces import KeySender, WindowBackend
from voice_agent.core.models import Risk
from voice_agent.engine.execution_context import ExecutionContext
from voice_agent.plugins.api import PluginHost
from voice_agent.plugins.manifest import PluginManifest

MEDIA_KEYS = frozenset(
    {"playpause", "volumeup", "volumedown", "volumemute", "nexttrack", "prevtrack", "stop"}
)
#: Without one of these, a chord is typing: "x", "shift+a", "enter".
_COMMAND_MODIFIERS = ("ctrl", "alt", "win")
_TYPING_KEYS = frozenset({"space", "enter", "return", "tab", "backspace"})
#: pyautogui spells some keys several ways; fold them so a variant can't dodge a rule.
_ALIASES = {
    "ctrlleft": "ctrl",
    "ctrlright": "ctrl",
    "altleft": "alt",
    "altright": "alt",
    "shiftleft": "shift",
    "shiftright": "shift",
    "winleft": "win",
    "winright": "win",
    "del": "delete",
}
#: Chords that close or destroy: the command sending them must be declared this risky.
_CHORD_RISK = {
    frozenset({"alt", "f4"}): Risk.HIGH,
    frozenset({"ctrl", "q"}): Risk.HIGH,
    frozenset({"ctrl", "shift", "w"}): Risk.HIGH,
    frozenset({"delete"}): Risk.HIGH,
    frozenset({"shift", "delete"}): Risk.HIGH,
    frozenset({"ctrl", "w"}): Risk.MEDIUM,
}


def _fold(keys: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(_ALIASES.get(key.lower(), key.lower()) for key in keys)


class _Guard:
    def __init__(self, granted: frozenset[str], plugin_id: str, context: ExecutionContext) -> None:
        self._granted = granted
        self._plugin_id = plugin_id
        self._context = context

    def _require(self, permission: str, needs: Risk = Risk.LOW) -> None:
        who = f"plugin {self._plugin_id!r}"
        spec = self._context.current()
        if spec is None or spec.plugin_id != self._plugin_id:
            msg = f"{who} acted outside its own command"
            raise PermissionDenied(msg)
        if self._context.interrupted():
            msg = f"{who}: stopped by the user"
            raise Interrupted(msg)
        if permission not in self._granted:
            msg = f"{who} used {permission} without declaring it"
            raise PermissionDenied(msg)
        if spec.risk.rank < needs.rank:
            msg = f"{who}: {spec.intent} is declared {spec.risk} risk; this action needs {needs}"
            raise PermissionDenied(msg)


class GuardedKeySender(_Guard):
    def __init__(
        self,
        keys: KeySender,
        granted: frozenset[str],
        plugin_id: str,
        context: ExecutionContext,
    ) -> None:
        super().__init__(granted, plugin_id, context)
        self._keys = keys

    def hotkey(self, *keys: str) -> None:
        """Media keys need media.control; a chord without ctrl/alt/win that types something
        needs keyboard.type; any other chord, keyboard.shortcut."""
        folded = _fold(keys)
        if folded and all(key in MEDIA_KEYS for key in folded):
            permission = "media.control"
        elif not any(key.startswith(_COMMAND_MODIFIERS) for key in folded) and any(
            len(key) == 1 or key in _TYPING_KEYS for key in folded
        ):
            permission = "keyboard.type"
        else:
            permission = "keyboard.shortcut"
        self._require(permission, _CHORD_RISK.get(frozenset(folded), Risk.LOW))
        self._keys.hotkey(*keys)

    def press(self, *keys: str) -> None:
        """A sequence of keys is typing; a Delete in it is still destructive."""
        needs = max(
            (_CHORD_RISK.get(frozenset({key}), Risk.LOW) for key in _fold(keys)),
            key=lambda risk: risk.rank,
            default=Risk.LOW,
        )
        self._require("keyboard.type", needs)
        self._keys.press(*keys)


class GuardedWindowBackend(_Guard):
    def __init__(
        self,
        windows: WindowBackend,
        granted: frozenset[str],
        plugin_id: str,
        context: ExecutionContext,
    ) -> None:
        super().__init__(granted, plugin_id, context)
        self._windows = windows

    def foreground(self) -> int:
        self._require("window.manage")
        return self._windows.foreground()

    def activate(self, handle: int) -> bool:
        self._require("window.manage")
        return self._windows.activate(handle)

    def minimize(self) -> None:
        self._require("window.manage")
        self._windows.minimize()

    def maximize(self) -> None:
        self._require("window.manage")
        self._windows.maximize()

    def close(self) -> None:
        self._require("window.manage", Risk.HIGH)
        self._windows.close()


def plugin_host(
    manifest: PluginManifest,
    keys: KeySender,
    windows: WindowBackend,
    context: ExecutionContext,
    granted: frozenset[str] = KNOWN_PERMISSIONS,
) -> PluginHost:
    """The capabilities one plugin may use: declared ∩ granted, checked on every call."""
    allowed = frozenset(manifest.permissions) & granted
    return PluginHost(
        keys=GuardedKeySender(keys, allowed, manifest.id, context),
        windows=GuardedWindowBackend(windows, allowed, manifest.id, context),
        interrupted=context.interrupted,
    )
