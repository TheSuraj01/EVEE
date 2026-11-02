"""Redaction must hold for message args, exception text, and transcripts — on every handler."""

from __future__ import annotations

import io
import json
import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

from voice_agent.observability.logging import (
    TRANSCRIPT_LIMIT,
    JsonFormatter,
    RedactionFilter,
    configure_logging,
)

FAKE_KEY = "gsk_" + "Fake0nlyForTests1234567890abcdef"


def _emit(
    filter_: RedactionFilter,
    msg: str,
    *args: object,
    formatter: logging.Formatter | None = None,
    **kwargs: object,
) -> str:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(filter_)
    handler.setFormatter(formatter or logging.Formatter("%(message)s"))
    logger = logging.getLogger("tests.redaction")
    logger.propagate = False
    logger.addHandler(handler)
    try:
        logger.error(msg, *args, **kwargs)
    finally:
        logger.removeHandler(handler)
    return stream.getvalue()


def test_api_key_in_message_args_renders_masked() -> None:
    out = _emit(RedactionFilter(), "calling groq with %s", FAKE_KEY)
    assert "gsk_***" in out
    assert FAKE_KEY not in out


def test_api_key_in_exception_text_is_masked() -> None:
    try:
        raise RuntimeError(f"auth failed for {FAKE_KEY}")
    except RuntimeError:
        out = _emit(RedactionFilter(), "request failed", exc_info=True)
    assert "RuntimeError" in out
    assert FAKE_KEY not in out


def test_ordinary_words_are_not_mistaken_for_keys() -> None:
    assert _emit(RedactionFilter(), "task-12345678 and risk-medium-level").strip() == (
        "task-12345678 and risk-medium-level"
    )


def test_transcript_is_dropped_unless_storing_transcripts_is_on() -> None:
    """Invariant 5: a log file is persistence, and most commands are under 32 chars."""
    out = _emit(
        RedactionFilter(),
        "heard",
        formatter=JsonFormatter(),
        extra={"transcript": "close window"},
    )
    assert "transcript" not in json.loads(out)
    assert "close window" not in out


def test_transcript_truncated_when_stored_with_redaction_on() -> None:
    out = _emit(
        RedactionFilter(store_transcripts=True, redact_transcripts=True),
        "heard",
        formatter=JsonFormatter(),
        extra={"transcript": "please open my bank statement from last tuesday"},
    )
    transcript = json.loads(out)["transcript"]
    assert transcript.startswith("please open my bank statement fr")
    assert len(transcript) == TRANSCRIPT_LIMIT + 1  # plus the ellipsis


def test_transcript_kept_when_stored_with_redaction_off() -> None:
    text = "please open my bank statement from last tuesday"
    out = _emit(
        RedactionFilter(store_transcripts=True, redact_transcripts=False),
        "heard",
        formatter=JsonFormatter(),
        extra={"transcript": text},
    )
    assert json.loads(out)["transcript"] == text


def test_json_formatter_survives_quotes() -> None:
    out = _emit(RedactionFilter(), 'user said "stop"', formatter=JsonFormatter())
    assert json.loads(out)["msg"] == 'user said "stop"'


@pytest.fixture
def restore_root_logging() -> Iterator[None]:
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield
    for handler in root.handlers:
        if handler not in handlers:
            handler.close()
    root.handlers[:] = handlers
    root.setLevel(level)


@pytest.mark.usefixtures("restore_root_logging")
def test_shipped_config_writes_redacted_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    configure_logging(redact_transcripts=True, log_dir=tmp_path / "logs")
    logging.getLogger("voice_agent.llm").warning(
        "key %s", FAKE_KEY, extra={"transcript": "x" * 100}
    )
    for handler in logging.getLogger().handlers:
        handler.flush()

    line = (tmp_path / "logs" / "voice-agent.log").read_text(encoding="utf-8").splitlines()[-1]
    record = json.loads(line)
    assert record["msg"] == "key gsk_***"
    assert "transcript" not in record  # shipped defaults: store_transcripts is false
