"""Project-wide constants. Values that policy depends on live here, not in call sites."""

from __future__ import annotations

APP_NAME = "Voice PC Agent"
APP_SLUG = "voice-pc-agent"

#: Identical intents inside this window are treated as one physical utterance.
DEFAULT_DEDUPE_WINDOW_MS = 1200

#: Hard ceiling on what we will ever send to a cloud model, in characters.
MAX_TRANSCRIPT_CHARS = 500

#: Risk at or above this level requires explicit user confirmation.
CONFIRMATION_RISK_THRESHOLD = "high"

#: Permission strings the host knows how to enforce.
#: Capabilities only the agent's own commands have: a plugin manifest cannot declare them.
BUILTIN_PERMISSIONS = frozenset({"app.launch", "web.open", "power.sleep"})

KNOWN_PERMISSIONS = frozenset(
    {
        "keyboard.shortcut",
        "keyboard.type",
        "window.manage",
        "media.control",
        "clipboard.read",
        "clipboard.write",
    }
)
