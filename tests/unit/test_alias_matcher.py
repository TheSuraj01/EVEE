from __future__ import annotations

from voice_agent.core.models import CommandSpec, Risk
from voice_agent.engine.command_registry import CommandRegistry
from voice_agent.nlu.alias_matcher import FUZZY_THRESHOLD, AliasMatcher


def test_exact_match_is_full_confidence(registry: CommandRegistry) -> None:
    result = AliasMatcher(registry).match("Next tab")
    assert result is not None
    assert result.intent == "browser.next_tab"
    assert result.confidence == 1.0


def test_argument_is_extracted_for_commands_that_take_one(registry: CommandRegistry) -> None:
    result = AliasMatcher(registry).match("seek forward 45 seconds")
    assert result is not None
    assert result.intent == "media.seek_forward"
    assert result.argument_seconds == 45


def test_default_argument_applies_when_none_spoken(registry: CommandRegistry) -> None:
    result = AliasMatcher(registry).match("seek forward")
    assert result is not None
    assert result.argument_seconds == 10


def test_unrelated_phrase_is_handed_on_rather_than_guessed(registry: CommandRegistry) -> None:
    assert AliasMatcher(registry).match("what is the weather in Delhi") is None


def test_spoken_zero_reaches_policy_instead_of_becoming_the_default(
    registry: CommandRegistry,
) -> None:
    """'seek forward 0' must be refused by the bounds check, not silently seek 10 s."""
    result = AliasMatcher(registry).match("seek forward 0")
    assert result is not None
    assert result.argument_seconds == 0


def test_a_duration_only_rides_on_a_command_that_takes_one(registry: CommandRegistry) -> None:
    """'next tab thirty seconds' is not 'next tab'; with a browser 'go back' and a seek, 'go
    back ten seconds' must not become browser back. It goes on to the next resolver."""
    matcher = AliasMatcher(registry)

    assert matcher.match("next tab thirty seconds") is None
    seek = matcher.match("seek forward 30 seconds")
    assert seek is not None
    assert seek.argument_seconds == 30


def test_little_words_and_polite_openings_do_not_decide_the_command(
    registry: CommandRegistry,
) -> None:
    """'close the window' is the registered 'close this window'; 'go ahead and', 'I want
    to' and 'just' carry no meaning. Real phrasing must not need the cloud."""
    matcher = AliasMatcher(registry)

    for said in ("Close the window.", "I want to close the window", "just close that window"):
        result = matcher.match(said)
        assert result is not None, said
        assert result.intent == "window.close"
        assert result.confidence == 1.0
    assert matcher.match("go ahead and switch to the next tab") is not None
    assert matcher.match("the") is None  # only little words: nothing to match


def test_a_close_mishearing_of_one_low_risk_command_still_matches(
    registry: CommandRegistry,
) -> None:
    """'next tap' is below the strict threshold, but nothing else is anywhere near it."""
    result = AliasMatcher(registry).match("Next tap.")
    assert result is not None
    assert result.intent == "browser.next_tab"
    assert result.confidence < FUZZY_THRESHOLD


def test_a_loose_guess_never_picks_a_command_that_closes_something(
    registry: CommandRegistry,
) -> None:
    """'clothes window' is as near to 'close this window' as 'next tap' is to 'next tab',
    but window.close is high risk: only a strict match may run it."""
    assert AliasMatcher(registry).match("clothes window") is None


def test_a_mishearing_between_two_commands_picks_neither() -> None:
    """'zoom on' is 0.86 like 'zoom in' and 0.8 like 'zoom out': too close to call."""
    registry = CommandRegistry()
    registry.register_all(
        [
            CommandSpec(intent="zoom.in", description="in", aliases=("zoom in",), risk=Risk.LOW),
            CommandSpec(intent="zoom.out", description="out", aliases=("zoom out",), risk=Risk.LOW),
        ]
    )
    matcher = AliasMatcher(registry)

    assert matcher.match("zoom on") is None
    assert matcher.match("zoom in") is not None


def test_a_negated_command_is_never_guessed(registry: CommandRegistry) -> None:
    """'not switch to next tab' is a few letters from an alias but means the opposite."""
    matcher = AliasMatcher(registry)

    for said in ("not switch to next tab", "don't close this window", "won't skip forward"):
        assert matcher.match(said) is None, said
