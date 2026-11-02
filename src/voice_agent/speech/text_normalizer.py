"""Transcript normalisation.

Whisper output varies in punctuation, casing and filler words between runs. Alias lookup
must be stable across that variation, so both sides of the comparison go through here.
"""

from __future__ import annotations

import re
import unicodedata

_PUNCTUATION = re.compile(r"[^\w\s]")
_WHITESPACE = re.compile(r"\s+")

_FILLERS = frozenset({"uh", "um", "er", "please", "now", "okay", "ok"})
_LEADING = ("hey jarvis", "jarvis", "computer", "can you", "could you", "would you")

_NUMBER_WORDS = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "fifteen": 15,
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "ninety": 90,
}


def normalize(text: str) -> str:
    """Lowercase, strip accents and punctuation, drop wake words and fillers."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().strip()
    text = _PUNCTUATION.sub(" ", text)
    text = _WHITESPACE.sub(" ", text).strip()
    for prefix in _LEADING:
        if text.startswith(prefix + " "):
            text = text[len(prefix) + 1 :]
        elif text == prefix:
            text = ""
    words = [w for w in text.split() if w not in _FILLERS]
    return " ".join(words)


#: Words that never decide *which* command was meant: "close the tab" is "close tab".
_CARRIER_WORDS = frozenset({"the", "a", "an", "this", "that", "it", "my", "me", "just"})
_CARRIER_LEADING = (
    "will you",
    "i want to",
    "i wanna",
    "i would like to",
    "i d like to",
    "let s",
    "go ahead and",
)


def command_key(text: str) -> str:
    """The form command aliases are compared in, on both sides: normalised, with carrier
    words and polite openings dropped. Only for alias lookup — the transcript itself, and
    what the cloud model reads, keep every word. Repeated until nothing changes, so a key
    is its own key ("the computer settings" -> "computer settings" -> "settings"): the
    registry's duplicate check and the lookup must see the same string."""
    key = normalize(text)
    while True:
        words = key
        for prefix in _CARRIER_LEADING:
            if words.startswith(prefix + " "):
                words = words[len(prefix) + 1 :]
        words = normalize(" ".join(w for w in words.split() if w not in _CARRIER_WORDS))
        if words == key:  # only ever shrinks, so this ends
            return key
        key = words


def extract_seconds(text: str) -> int | None:
    """Pull a duration out of a phrase such as 'seek forward thirty seconds'.

    Returns None when no duration is present; the caller decides on a default.
    """
    normalized = normalize(text)
    for number in (r"\d{1,3}", *_NUMBER_WORDS):
        found = re.search(rf"\b({number})\b(\s*(?:minutes?|mins?)\b)?", normalized)
        if found:
            raw = found.group(1)
            value = int(raw) if raw.isdigit() else _NUMBER_WORDS[raw]
            # "2 minutes" is 120 s, not 2 s; the policy bounds still apply afterwards.
            return value * 60 if found.group(2) else value
    return None
