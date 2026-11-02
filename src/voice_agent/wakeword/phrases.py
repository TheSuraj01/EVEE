"""Wake and sleep phrases, matched on the transcript (`wakeword.engine: phrase`).

openWakeWord can only spot the phrases it was trained on. A phrase the user picks, such as
"wake up ev", is recognised by Whisper instead and matched here. The phrase must be the
whole utterance, allowing for how Whisper spells it ("Wake up, Eve.", "Sleepy V."), so a
sentence that merely contains the words ("I had to wake up early") wakes nothing.

Waking is strict and sleeping lenient: going to sleep by mistake is harmless, waking by
mistake isn't.
"""

from __future__ import annotations

from difflib import SequenceMatcher
from enum import StrEnum

from voice_agent.speech.text_normalizer import normalize

#: Whole-utterance similarity needed. Wake: 'wake up eve', 'wake up F' and 'wake up' pass;
#: 'wake up early' and 'wake up everyone' don't.
WAKE_SIMILARITY = 0.8
#: Sleep: 'Sleepy V', 'Sleepy-Bee' and 'sleeper' (Whisper's takes on "sleep E V") pass.
SLEEP_SIMILARITY = 0.65
#: Asleep, a transcript this close to the wake phrase (alone or opening the sentence) gets a
#: second, more careful listen: "wake up" said quickly or from across the room.
RESEMBLES = 0.55
#: The wake phrase opening a longer sentence ("Wake up EV, open chrome") counts from
#: WAKE_SIMILARITY, but only from here is it sure: 'wake up Eve', 'wake up Evie'. Below,
#: 'wake up F' (Whisper's "ev") is also how 'wake up at seven' starts.
PREFIX_SURE = 0.9


class Phrase(StrEnum):
    WAKE = "wake"
    SLEEP = "sleep"


class WakePhrases:
    def __init__(self, wake: str, sleep: str) -> None:
        self.wake, self.sleep = normalize(wake), normalize(sleep)
        if not self.wake or not self.sleep or self.wake == self.sleep:
            msg = f"wake and sleep phrases must differ and not be empty: {wake!r}, {sleep!r}"
            raise ValueError(msg)

    def classify(self, transcript: str) -> Phrase | None:
        """WAKE, SLEEP, or None for anything else."""
        said = normalize(transcript)
        if not said:
            return None
        wake, sleep = _similarity(said, self.wake), _similarity(said, self.sleep)
        if wake >= WAKE_SIMILARITY and wake >= sleep:
            return Phrase.WAKE
        if sleep >= SLEEP_SIMILARITY and sleep > wake:
            return Phrase.SLEEP
        return None

    def resembles_wake(self, transcript: str) -> bool:
        """Close to the wake phrase without being it: worth a second listen."""
        words = normalize(transcript).split()
        n = len(self.wake.split())
        candidates = [" ".join(words)] + [" ".join(words[:k]) for k in (n, n + 1) if len(words) > k]
        return any(_similarity(c, self.wake) >= RESEMBLES for c in candidates if c)

    def wake_prefix(self, transcript: str) -> tuple[str, bool] | None:
        """'Wake up EV, open chrome' -> ('open chrome', sure). The wake phrase must open the
        sentence as its first n or n+1 words (Whisper may write 'EV' as 'E V') and more
        words must follow. Not `sure`: a looser hearing, for the caller to accept only when
        the rest is a known command."""
        words = normalize(transcript).split()
        n = len(self.wake.split())
        best: tuple[float, int] | None = None
        for k in (n, n + 1):
            if len(words) > k:
                score = _similarity(" ".join(words[:k]), self.wake)
                if best is None or score > best[0]:
                    best = (score, k)
        if best is None or best[0] < WAKE_SIMILARITY:
            return None
        score, k = best
        return " ".join(words[k:]), score >= PREFIX_SURE


def _similarity(said: str, phrase: str) -> float:
    """The better of with and without spaces: Whisper splits and joins words freely
    ('sleepy bee', 'sleeper')."""
    return max(
        SequenceMatcher(None, said, phrase).ratio(),
        SequenceMatcher(None, said.replace(" ", ""), phrase.replace(" ", "")).ratio(),
    )
