"""Windows desktop shortcuts. Each is a key chord Windows itself defines; nothing here starts
a program by name, so a transcript can never choose what runs."""

from __future__ import annotations

from voice_agent.plugins.api import ActionHandler, Plugin, PluginHost, Shortcut


class DesktopPlugin(Plugin):
    def handlers(self, host: PluginHost) -> dict[str, ActionHandler]:
        return host.shortcuts(
            {
                "desktop.show": Shortcut(("win", "d")),
                "desktop.minimize_all": Shortcut(("win", "m")),
                "desktop.start_menu": Shortcut(("win",)),
                "desktop.file_explorer": Shortcut(("win", "e")),
                "desktop.settings": Shortcut(("win", "i")),
                "desktop.screenshot": Shortcut(("win", "shift", "s")),
                "desktop.task_manager": Shortcut(("ctrl", "shift", "esc")),
                "desktop.task_view": Shortcut(("win", "tab")),
                "desktop.next_desktop": Shortcut(("ctrl", "win", "right")),
                "desktop.previous_desktop": Shortcut(("ctrl", "win", "left")),
                "desktop.notifications": Shortcut(("win", "n")),
                "desktop.clipboard": Shortcut(("win", "v")),
            }
        )
