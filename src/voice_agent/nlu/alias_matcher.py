"""Local, deterministic intent resolution.

This runs before any network call. If it resolves the transcript, the cloud model is never
contacted — invariant 3 in CLAUDE.md. Exact normalised match first, then a command said
with its value ("open chrome" -> app.open, Google Chrome), then a conservative fuzzy pass
that only fires well above the noise floor, and only when the phrase is clearly closer to
one command than to any other.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from voice_agent.core.models import CommandSpec, IntentProposal, IntentSource, Risk
from voice_agent.engine.command_registry import CommandRegistry
from voice_agent.speech.text_normalizer import command_key, extract_seconds

#: Below this similarity we would rather hand the phrase to the LLM than guess.
FUZZY_THRESHOLD = 0.88
#: A looser mishearing ("play paws", "reset too") still counts, but only for a low-risk
#: command, and only when it is at least NEAR_MARGIN closer to that command than to every
#: other: between two commands, guess neither. Never a guess that closes something.
NEAR_THRESHOLD = 0.8
NEAR_MARGIN = 0.15
#: Even a strict match must beat every other command by this much ("unclose tab" sits
#: between close tab and reopen tab: neither).
STRICT_MARGIN = 0.05
#: A negated sentence is never guessed at: "let's not close the tab" is one letter-run away
#: from "close tab". No alias contains these, so exact phrases are unaffected. "t" is what
#: every n't leaves behind ("won't" -> "won t").
_NEGATIONS = frozenset({"not", "no", "never", "don", "dont", "t"})

_DURATION_TAIL = re.compile(
    r"\s*(?:by\s+)?(?:\d{1,3}|zero|one|two|three|four|five|six|seven|eight|nine|ten|"
    r"fifteen|twenty|thirty|forty|fifty|sixty|ninety)"
    r"(?:\s*(?:seconds?|secs?|minutes?|mins?))?$"
)


#: A first word that is only the maker: "Google Chrome" is also just "chrome".
_VENDORS = frozenset({"google", "microsoft", "mozilla", "adobe", "windows"})
#: Said after an app's name without being part of it: "open the chrome app".
_APP_SUFFIXES = (" application", " app")


def _strip_duration(phrase: str) -> str:
    """Remove a trailing duration so the remaining stem can match a plain alias."""
    return _DURATION_TAIL.sub("", phrase).strip()


def _ways_to_say(name: str) -> list[str]:
    """'Google Chrome' -> 'google chrome', 'chrome'; 'Visual Studio Code' -> also 'vs code'
    and 'vscode'; 'Notepad++' -> 'notepad plus plus'."""
    key = command_key(name.replace("+", " plus "))
    words = key.split()
    ways = [key]
    if len(words) > 1 and words[0] in _VENDORS:
        ways.append(" ".join(words[1:]))
    if len(words) >= 3:
        short = "".join(w[0] for w in words[:-1]) + " " + words[-1]
        ways += [short, short.replace(" ", "")]
    return [way for way in ways if way]


def choice_keys(choices: tuple[str, ...]) -> dict[str, str]:
    """Every way to say each choice -> that choice. A way two choices share is dropped, and
    a choice's own name always beats another's nickname."""
    owners: dict[str, set[str]] = {}
    for name in choices:
        for way in _ways_to_say(name):
            owners.setdefault(way, set()).add(name)
    keys = {way: next(iter(names)) for way, names in owners.items() if len(names) == 1}
    for name in reversed(choices):  # the first-listed choice wins a tie of full names
        keys[_ways_to_say(name)[0]] = name
    return keys


class AliasMatcher:
    def __init__(self, registry: CommandRegistry, fuzzy_threshold: float = FUZZY_THRESHOLD) -> None:
        self._registry = registry
        self._threshold = fuzzy_threshold
        self._choice_keys: dict[str, dict[str, str]] = {}

    def match(self, transcript: str) -> IntentProposal | None:
        """Return a proposal, or None when the phrase should go to the next resolver."""
        phrase = command_key(transcript)
        if not phrase:
            return None

        intent = self._registry.resolve_alias(phrase)
        if intent is not None:
            return self._build(intent, transcript, confidence=1.0)

        said = self._said_with_value(phrase, transcript)
        if said is not None:
            return said

        # "seek forward 45 seconds" is the same command as "seek forward"; the duration is
        # an argument, not part of the alias, so strip it and look again. Only for commands
        # that take a duration: "go back ten seconds" must not become browser 'go back'.
        stem = _strip_duration(phrase)
        if stem != phrase:
            intent = self._registry.resolve_alias(stem)
            if intent is not None and self._registry.require(intent).argument is not None:
                return self._build(intent, transcript, confidence=1.0)

        if _NEGATIONS.intersection(phrase.split()):
            return None  # for the cloud model, which can say no_action
        ranked = self._closest(phrase)
        if not ranked:
            return None
        best_score, best_intent = ranked[0]
        lead = best_score - (ranked[1][0] if len(ranked) > 1 else 0.0)
        strict = best_score >= self._threshold and lead >= STRICT_MARGIN
        near = (
            best_score >= NEAR_THRESHOLD
            and lead >= NEAR_MARGIN
            and self._registry.require(best_intent).risk is Risk.LOW
        )
        if strict or near:
            return self._build(best_intent, transcript, confidence=best_score)
        return None

    def _said_with_value(self, phrase: str, transcript: str) -> IntentProposal | None:
        """A command whose value is one of a fixed set, said in one of its phrasings:
        "open chrome", "launch vs code". The value must be one of the choices (a close
        mishearing of one counts only if no other choice comes near)."""
        for spec in self._registry:
            if spec.text is None or spec.text.kind != "choice":
                continue
            for pattern in spec.text.say:
                head, _, tail = (command_key(part) for part in pattern.partition("{}"))
                if not phrase.startswith(f"{head} " if head else ""):
                    continue
                slot = phrase[len(head) :].strip()
                if tail:
                    if not slot.endswith(f" {tail}"):
                        continue
                    slot = slot[: -len(tail)].strip()
                choice, score = self._choose(spec, slot)
                if choice is not None:
                    return IntentProposal(
                        intent=spec.intent,
                        argument_text=choice,
                        confidence=score,
                        source=IntentSource.ALIAS,
                        raw=transcript,
                    )
        return None

    def choice_for(self, intent: str, name: str) -> str | None:
        """The listed choice a name from the cloud model means ("vs code" -> "Visual Studio
        Code"): its exact name, a nickname, or without the maker ("Microsoft Word" -> "Word").
        Never a near spelling: the model's words were not misheard, and an app it names that
        isn't installed ("Steam") must not become one that is ("Microsoft Stream")."""
        spec = self._registry.get(intent)
        if spec is None or spec.text is None or spec.text.kind != "choice":
            return None
        wanted = name.strip().casefold()
        exact = next((c for c in spec.text.choices if c.casefold() == wanted), None)
        if exact is not None:
            return exact
        key = command_key(name)
        words = key.split()
        found = self._choose(spec, key, fuzzy=False)[0]
        if found is None and len(words) > 1 and words[0] in _VENDORS:
            found = self._choose(spec, " ".join(words[1:]), fuzzy=False)[0]
        return found

    def _choose(self, spec: CommandSpec, slot: str, fuzzy: bool = True) -> tuple[str | None, float]:
        assert spec.text is not None
        keys = self._choice_keys.get(spec.intent)
        if keys is None:
            keys = self._choice_keys[spec.intent] = choice_keys(spec.text.choices)
        for candidate in (slot, *(slot[: -len(s)] for s in _APP_SUFFIXES if slot.endswith(s))):
            if candidate in keys:
                return keys[candidate], 1.0
        if not fuzzy or _NEGATIONS.intersection(slot.split()):
            return None, 0.0  # "open chrome, no": not a mishearing of an app's name
        best: dict[str, float] = {}
        for way, name in keys.items():
            best[name] = max(SequenceMatcher(None, slot, way).ratio(), best.get(name, 0.0))
        ranked = sorted(((score, name) for name, score in best.items()), reverse=True)
        if not ranked:
            return None, 0.0
        score, name = ranked[0]
        lead = score - (ranked[1][0] if len(ranked) > 1 else 0.0)
        if score >= self._threshold and lead >= STRICT_MARGIN:
            return name, score
        return None, 0.0

    def _closest(self, phrase: str) -> list[tuple[float, str]]:
        """(similarity, intent) for each command's closest alias, most similar first."""
        best: dict[str, float] = {}
        for alias in self._registry.aliases():
            intent = self._registry.resolve_alias(alias)
            if intent is not None:
                score = SequenceMatcher(None, phrase, alias).ratio()
                best[intent] = max(score, best.get(intent, 0.0))
        return sorted(((score, intent) for intent, score in best.items()), reverse=True)

    def _build(self, intent: str, transcript: str, confidence: float) -> IntentProposal:
        spec = self._registry.require(intent)
        argument: int | None = None
        if spec.argument is not None:
            # `is None`, not `or`: a spoken 0 must reach the policy and be refused there.
            spoken = extract_seconds(transcript)
            argument = spec.argument.default if spoken is None else spoken
        return IntentProposal(
            intent=intent,
            argument_seconds=argument,
            confidence=confidence,
            source=IntentSource.ALIAS,
            raw=transcript,
        )
