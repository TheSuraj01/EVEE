"""Browser shortcuts (Chrome, Edge and Firefox share them). A plugin supplies executors,
never policy."""

from __future__ import annotations

from voice_agent.plugins.api import ActionHandler, Plugin, PluginHost, Shortcut


class BrowserPlugin(Plugin):
    def handlers(self, host: PluginHost) -> dict[str, ActionHandler]:
        return host.shortcuts(
            {
                "browser.next_tab": Shortcut(("ctrl", "tab")),
                "browser.previous_tab": Shortcut(("ctrl", "shift", "tab")),
                "browser.close_tab": Shortcut(("ctrl", "w")),
                "browser.reopen_tab": Shortcut(("ctrl", "shift", "t")),
                "browser.new_tab": Shortcut(("ctrl", "t")),
                "browser.new_window": Shortcut(("ctrl", "n")),
                "browser.refresh": Shortcut(("f5",)),
                "browser.back": Shortcut(("alt", "left")),
                "browser.forward": Shortcut(("alt", "right")),
                "browser.find": Shortcut(("ctrl", "f")),
                "browser.bookmark": Shortcut(("ctrl", "d")),
            }
        )
