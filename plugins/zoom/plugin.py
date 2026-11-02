"""Zoom: the example plugin in docs/plugin-development.md.

Ctrl with the numeric-keypad plus and minus zooms in browsers and most document apps, and
Ctrl+0 resets — the same keys on every keyboard layout, unlike Ctrl+= .
"""

from __future__ import annotations

from voice_agent.plugins.api import ActionHandler, Plugin, PluginHost, Shortcut


class ZoomPlugin(Plugin):
    def handlers(self, host: PluginHost) -> dict[str, ActionHandler]:
        return host.shortcuts(
            {
                "zoom.in": Shortcut(("ctrl", "add")),
                "zoom.out": Shortcut(("ctrl", "subtract")),
                "zoom.reset": Shortcut(("ctrl", "0")),
            }
        )
