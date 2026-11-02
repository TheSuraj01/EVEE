"""Window management.

Minimise, maximise and close go through the host's window capability (pywin32 in
production). Switching and snapping use documented Windows shortcuts instead, because
SetForegroundWindow is restricted for background processes and alt+tab is not.
"""

from __future__ import annotations

from voice_agent.plugins.api import ActionHandler, Plugin, PluginHost, Shortcut


class WindowManagementPlugin(Plugin):
    def handlers(self, host: PluginHost) -> dict[str, ActionHandler]:
        return {
            **host.window_actions(),
            **host.shortcuts(
                {
                    "window.focus_next": Shortcut(("alt", "tab")),
                    "window.snap_left": Shortcut(("win", "left")),
                    "window.snap_right": Shortcut(("win", "right")),
                }
            ),
        }
