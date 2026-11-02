"""Per-user locations. A desktop app cannot rely on its working directory."""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path

from voice_agent.core.constants import APP_SLUG

DB_NAME = "voice-agent.sqlite3"
LOG_NAME = "voice-agent.log"
AUDIT_NAME = "decisions.jsonl"

#: Where config/ and plugins/ live: the checkout when run from source, the bundle's data
#: folder (sys._MEIPASS) in a PyInstaller build.
APP_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[3]))


def data_dir(env: Mapping[str, str] | None = None) -> Path:
    """%LOCALAPPDATA%/voice-pc-agent on Windows (not roaming: nothing here should follow the
    user to other machines); ~/.local/share/voice-pc-agent elsewhere."""
    base = (os.environ if env is None else env).get("LOCALAPPDATA")
    return (Path(base) if base else Path.home() / ".local" / "share") / APP_SLUG
