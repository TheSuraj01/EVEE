"""Structured logging with redaction applied at the handler, so nothing reaches disk unscrubbed.

Convention: never interpolate a transcript into a log message. Pass it as
`extra={"transcript": text}` so the redaction filter can find it. A log file is
persistence, so the field is dropped unless `privacy.store_transcripts` is on
(invariant 5); when it is on and redaction is too, it is cut to 32 characters.
"""

from __future__ import annotations

import json
import logging
import logging.config
import re
from pathlib import Path
from typing import Any

import yaml

from voice_agent.settings.loader import DEFAULT_CONFIG_DIR
from voice_agent.settings.paths import LOG_NAME, data_dir

DEFAULT_LOGGING_FILE = DEFAULT_CONFIG_DIR / "logging.yaml"
#: Per-user, not the working directory: the tray app can be started from anywhere.
LOG_DIR = data_dir() / "logs"
LOG_FILE = LOG_DIR / LOG_NAME
TRANSCRIPT_LIMIT = 32

#: `extra=` fields copied into JSON lines. Metrics use stage + latency_ms (see T-038).
_STRUCTURED_EXTRAS = ("transcript", "stage", "latency_ms")

# Groq (gsk_) and OpenAI/Anthropic-style (sk-) keys; the prefix is kept for debuggability.
_API_KEY = re.compile(r"\b(gsk_|sk-)[A-Za-z0-9_-]{8,}")


def redact(text: str) -> str:
    """Mask anything shaped like an API key."""
    return _API_KEY.sub(r"\1***", text)


class RedactionFilter(logging.Filter):
    """Attach to handlers (not loggers) so records propagated from children are covered."""

    def __init__(self, store_transcripts: bool = False, redact_transcripts: bool = True) -> None:
        super().__init__()
        self.store_transcripts = store_transcripts
        self.redact_transcripts = redact_transcripts

    def filter(self, record: logging.LogRecord) -> bool:
        # Render first: the key is usually in the args, not the format string.
        record.msg = redact(record.getMessage())
        record.args = None
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = redact(record.exc_text)

        transcript = record.__dict__.get("transcript")
        if transcript is not None and not self.store_transcripts:
            del record.__dict__["transcript"]
        elif transcript is not None:
            text = redact(str(transcript))
            if self.redact_transcripts and len(text) > TRANSCRIPT_LIMIT:
                text = text[:TRANSCRIPT_LIMIT] + "…"
            record.__dict__["transcript"] = text
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line, so quotes in a message can't corrupt the log file."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key in _STRUCTURED_EXTRAS:
            if key in record.__dict__:
                payload[key] = record.__dict__[key]
        if record.exc_info and not record.exc_text:
            record.exc_text = self.formatException(record.exc_info)
        if record.exc_text:
            payload["exc"] = record.exc_text
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(
    redact_transcripts: bool,
    store_transcripts: bool = False,
    config_file: Path = DEFAULT_LOGGING_FILE,
    log_dir: Path = LOG_DIR,
) -> None:
    """Apply logging.yaml with the privacy settings injected, so redaction follows config."""
    config = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    config["filters"]["redaction"]["redact_transcripts"] = redact_transcripts
    config["filters"]["redaction"]["store_transcripts"] = store_transcripts
    for handler in config.get("handlers", {}).values():
        if "filename" in handler:
            log_dir.mkdir(parents=True, exist_ok=True)
            handler["filename"] = str(log_dir / Path(handler["filename"]).name)
    logging.config.dictConfig(config)
