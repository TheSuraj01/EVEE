"""scripts/voice_check.py: the judging of what was heard (the microphone part needs a person)."""

from __future__ import annotations

from typing import Any

import pytest
from scripts.voice_check import PHRASES, judge

from voice_agent.wakeword.phrases import WakePhrases

PHRASES_OF_AGENT = WakePhrases("wake up ev", "sleep ev")


@pytest.mark.parametrize(
    ("expect", "heard", "right"),
    [
        ("wake", "Wake up, Evie.", True),
        ("wake", "Wake up everyone.", False),
        ("sleep", "Sleep EV.", True),
        ("browser.next_tab", "Next tab.", True),
        ("media.play_pause", "Play Paws.", True),  # a mishearing the agent still gets
        ("browser.next_tab", "Text up.", False),
        ("wake+browser.next_tab", "Wake up EV, next tab.", True),
        ("wake+browser.next_tab", "Next tab.", False),  # the wake phrase is part of it
    ],
)
def test_a_phrase_counts_only_if_the_agent_would_have_done_it(
    shipped: Any, expect: str, heard: str, right: bool
) -> None:
    assert judge(expect, heard, shipped.agent, PHRASES_OF_AGENT) is right


def test_every_phrase_expects_something_the_agent_can_do(shipped: Any) -> None:
    intents = set(shipped.agent.registry.intents()) | {"app.open"}  # app.open needs the Start menu
    for _say, expect in PHRASES:
        assert expect.removeprefix("wake+") in intents | {"wake", "sleep"}
