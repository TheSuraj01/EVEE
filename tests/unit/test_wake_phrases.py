"""Wake and sleep phrases, matched on what Whisper heard."""

from __future__ import annotations

import pytest

from voice_agent.wakeword.phrases import Phrase, WakePhrases

PHRASES = WakePhrases("wake up ev", "sleep ev")


@pytest.mark.parametrize(
    "heard",
    ["wake up ev", "Wake up, EV.", "Wake up, Eve.", "wake up eve", "Wake up.", "Um, wake up ev"],
)
def test_the_wake_phrase_as_whisper_writes_it(heard: str) -> None:
    assert PHRASES.classify(heard) is Phrase.WAKE


@pytest.mark.parametrize(
    "heard",
    # The last three are what Whisper actually wrote for "sleep E V" said by Windows voices.
    ["sleep ev", "Sleep, Eve.", "Sleep EV.", "sleep eve", "Sleepy V.", "Sleepy-Bee.", "sleeper"],
)
def test_the_sleep_phrase_as_whisper_writes_it(heard: str) -> None:
    assert PHRASES.classify(heard) is Phrase.SLEEP


@pytest.mark.parametrize(
    "heard",
    [
        "I had to wake up early today",
        "wake up early",
        "wake up everyone",
        "next tab",
        "",
        "Thank you.",
        "did you sleep",
    ],
)
def test_sentences_that_merely_share_words_are_not_phrases(heard: str) -> None:
    """The whole utterance must be the phrase, so conversation can't wake or sleep it."""
    assert PHRASES.classify(heard) is None


def test_phrases_are_configurable() -> None:
    phrases = WakePhrases("hello agent", "goodbye agent")

    assert phrases.classify("Hello, agent.") is Phrase.WAKE
    assert phrases.classify("Goodbye agent") is Phrase.SLEEP
    assert phrases.classify("wake up ev") is None


def test_wake_and_sleep_phrases_must_differ() -> None:
    with pytest.raises(ValueError, match="differ"):
        WakePhrases("wake up ev", "Wake up, EV.")


def test_sleeping_is_lenient_waking_is_strict() -> None:
    """Going to sleep by mistake is harmless; waking by mistake isn't."""
    assert PHRASES.classify("sleep well") is Phrase.SLEEP
    assert PHRASES.classify("wake up early") is None


@pytest.mark.parametrize(
    ("heard", "rest", "sure"),
    [
        # What Whisper wrote for "wake up E V, ..." said straight on by Windows voices.
        ("Wake up Evie, open chrome.", "open chrome", True),
        ("Wake up EV Next tab", "next tab", True),
        ("Wake up EV, open YouTube in a new tab.", "open youtube in a new tab", True),
        ("wake up E V open chrome", "open chrome", True),
        ("Wake up F, volume up.", "volume up", False),  # "F": also how "wake up at" begins
    ],
)
def test_the_wake_phrase_can_open_a_command(heard: str, rest: str, sure: bool) -> None:
    """Said in one breath, the phrase and the command are one utterance: it used to stay
    asleep (0 of 24 in a recorded test). The rest is the command."""
    assert PHRASES.classify(heard) is None
    assert PHRASES.wake_prefix(heard) == (rest, sure)


@pytest.mark.parametrize(
    "heard",
    [
        "Wake up everyone, let's go.",
        "wake up early tomorrow",
        "I need to wake up at 7.",
        "wake up ev",
    ],
)
def test_a_sentence_that_only_starts_alike_does_not_open_a_command(heard: str) -> None:
    assert PHRASES.wake_prefix(heard) is None
