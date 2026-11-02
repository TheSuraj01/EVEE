"""Local-first intent routing.

The alias matcher runs first and the cloud provider is consulted only on a miss, which is
invariant 3: pressing a key must never require the internet. When the cloud provider fails
the router returns an explicit `system.no_action` (so the attempt is audited as cloud-
sourced) and sets `last_error` to a reason that is safe to show the user (invariant 4).
"""

from __future__ import annotations

import logging

from voice_agent.core.exceptions import LLMUnavailableError
from voice_agent.core.interfaces import IntentProvider
from voice_agent.core.models import NO_ACTION_INTENT, IntentProposal, IntentSource
from voice_agent.nlu.alias_matcher import AliasMatcher
from voice_agent.speech.text_normalizer import normalize

log = logging.getLogger(__name__)

#: Longer than this is conversation, not a command: not worth a cloud request (each one
#: spends the per-minute quota that real commands need).
MAX_COMMAND_WORDS = 14
#: What Whisper writes for noise, breathing and silence when nobody asked for anything.
_NOT_SPEECH = frozenset(
    {
        "you",
        "thank you",
        "thanks",
        "thank you very much",
        "thanks for watching",
        "thank you for watching",
        "bye",
        "bye bye",
        "hmm",
        "mm",
        "oh",
        "ah",
        "huh",
        "so",
        "yeah",
        "yes",
        "no",
    }
)


def worth_asking(transcript: str) -> bool:
    """Could this be a command the phrase table missed? Not if it is a stock hallucination
    or a long stretch of conversation."""
    words = normalize(transcript)
    return bool(words) and words not in _NOT_SPEECH and len(words.split()) <= MAX_COMMAND_WORDS


class IntentRouter:
    def __init__(
        self,
        alias_matcher: AliasMatcher,
        llm_provider: IntentProvider | None = None,
        llm_enabled: bool = True,
    ) -> None:
        self._alias = alias_matcher
        self._llm = llm_provider
        self._llm_enabled = llm_enabled
        self.last_error: str | None = None

    def local(self, transcript: str) -> IntentProposal | None:
        """The local matcher alone: never the cloud."""
        return self._alias.match(transcript)

    def resolve(
        self, transcript: str, context: dict[str, str] | None = None
    ) -> IntentProposal | None:
        self.last_error = None

        local = self._alias.match(transcript)
        if local is not None:
            return local

        if not self._llm_enabled or self._llm is None or not worth_asking(transcript):
            return None

        try:
            proposal = self._llm.propose(transcript, context or {})
        except LLMUnavailableError as exc:
            log.warning("cloud intent resolution failed: %s", exc)  # detail: redacted log only
            self.last_error = exc.user_message
            return IntentProposal(intent=NO_ACTION_INTENT, source=IntentSource.LLM, raw=transcript)
        if proposal is not None and proposal.argument_text is not None:
            # The model names an app as it knows it; the policy accepts only the Start menu's
            # own name. Map one onto the other here, by the rules speech uses. No match:
            # left as it is, and the policy refuses it.
            listed = self._alias.choice_for(proposal.intent, proposal.argument_text)
            if listed is not None:
                proposal = proposal.model_copy(update={"argument_text": listed})
        return proposal
