"""Media controls.

Play/pause and volume use the global media keys, so they work whatever has focus. Seeking
has no global key: it sends arrow keys to the focused player.
"""

from __future__ import annotations

from voice_agent.plugins.api import ActionHandler, Plugin, PluginHost, Shortcut

# ponytail: one arrow = 5 s holds for YouTube and most web players; VLC/Netflix differ.
# Per-application profiles are in the backlog.
SEEK_SECONDS_PER_PRESS = 5

#: One volume key press moves Windows volume by 2%; five makes the change audible.
VOLUME_STEP = 5


class MediaPlugin(Plugin):
    def handlers(self, host: PluginHost) -> dict[str, ActionHandler]:
        return host.shortcuts(
            {
                "media.play_pause": Shortcut(("playpause",)),
                "media.seek_forward": Shortcut(
                    ("right",), seconds_per_press=SEEK_SECONDS_PER_PRESS
                ),
                "media.seek_backward": Shortcut(
                    ("left",), seconds_per_press=SEEK_SECONDS_PER_PRESS
                ),
                "media.volume_up": Shortcut(("volumeup",), repeat=VOLUME_STEP),
                "media.volume_down": Shortcut(("volumedown",), repeat=VOLUME_STEP),
                "media.mute": Shortcut(("volumemute",)),
                "media.next_track": Shortcut(("nexttrack",)),
                "media.previous_track": Shortcut(("prevtrack",)),
                "media.stop": Shortcut(("stop",)),
            }
        )
