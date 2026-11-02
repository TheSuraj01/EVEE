from __future__ import annotations

import pytest

from voice_agent.speech.text_normalizer import command_key, extract_seconds, normalize


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Next tab.", "next tab"),
        ("  NEXT   TAB  ", "next tab"),
        ("Hey Jarvis, next tab", "next tab"),
        ("um, next tab please", "next tab"),
        ("Café", "cafe"),
        ("", ""),
        ("jarvis", ""),
    ],
)
def test_normalize(raw: str, expected: str) -> None:
    assert normalize(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("seek forward 30 seconds", 30),
        ("skip forward thirty seconds", 30),
        ("seek forward", None),
        ("rewind ten", 10),
        ("seek forward 2 minutes", 120),
        ("skip forward one minute", 60),
        ("rewind 90 secs", 90),
        ("seek forward 0", 0),
    ],
)
def test_extract_seconds(raw: str, expected: int | None) -> None:
    assert extract_seconds(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Close the tab.", "close tab"),
        ("close this tab", "close tab"),
        ("Can you go ahead and turn it up, please?", "turn up"),
        ("I'd like to open a new tab", "open new tab"),
        ("Let's go back", "go back"),
        ("the", ""),
    ],
)
def test_command_key_drops_only_words_that_never_pick_the_command(raw: str, expected: str) -> None:
    assert command_key(raw) == expected


def test_the_transcript_itself_keeps_the_little_words() -> None:
    """The cloud model reads normalize()'s output: it needs 'the tab on the right'."""
    assert normalize("Go to the tab on the right.") == "go to the tab on the right"


def test_a_command_key_is_its_own_key() -> None:
    """Registered once, looked up again: both must land on the same string."""
    for raw in ("the computer settings", "Hey Jarvis, can you just close that tab", "the"):
        key = command_key(raw)
        assert command_key(key) == key
    assert command_key("the computer settings") == "settings"
