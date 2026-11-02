"""Editing and page movement: the standard Windows shortcuts, sent to the focused window."""

from __future__ import annotations

from voice_agent.plugins.api import ActionHandler, Plugin, PluginHost, Shortcut


class EditingPlugin(Plugin):
    def handlers(self, host: PluginHost) -> dict[str, ActionHandler]:
        return host.shortcuts(
            {
                "edit.copy": Shortcut(("ctrl", "c")),
                "edit.paste": Shortcut(("ctrl", "v")),
                "edit.cut": Shortcut(("ctrl", "x")),
                "edit.undo": Shortcut(("ctrl", "z")),
                "edit.redo": Shortcut(("ctrl", "y")),
                "edit.select_all": Shortcut(("ctrl", "a")),
                "edit.save": Shortcut(("ctrl", "s")),
                "edit.print": Shortcut(("ctrl", "p")),
                "page.down": Shortcut(("pagedown",)),
                "page.up": Shortcut(("pageup",)),
                "page.top": Shortcut(("ctrl", "home")),
                "page.bottom": Shortcut(("ctrl", "end")),
            }
        )
