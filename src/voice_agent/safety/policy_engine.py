"""The policy engine: the only component allowed to authorise an action.

Nothing downstream of here re-checks anything, and nothing upstream of here is trusted —
not the transcript, not the cloud model, not a plugin's manifest. A proposal becomes a
ResolvedCommand only by passing through `evaluate`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from voice_agent.core.constants import BUILTIN_PERMISSIONS, KNOWN_PERMISSIONS
from voice_agent.core.models import (
    NO_ACTION_INTENT,
    CommandSpec,
    Decision,
    IntentProposal,
    PolicyVerdict,
    Risk,
)
from voice_agent.engine.command_registry import CommandRegistry
from voice_agent.safety.validation import web_address

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PolicySettings:
    reject_unknown_intents: bool = True
    require_confirmation_for_destructive_actions: bool = True
    confirmation_threshold: Risk = Risk.HIGH
    minimum_confidence: float = 0.55
    #: Permissions the host is currently willing to grant at all.
    granted_permissions: frozenset[str] = field(default=KNOWN_PERMISSIONS | BUILTIN_PERMISSIONS)


class PolicyEngine:
    def __init__(self, registry: CommandRegistry, settings: PolicySettings | None = None) -> None:
        self._registry = registry
        self._settings = settings or PolicySettings()

    def evaluate(self, proposal: IntentProposal) -> PolicyVerdict:
        """Decide, and log every refusal: intent, source and reason — never the transcript."""
        verdict = self.decide(proposal)
        if verdict.decision is Decision.DENY and verdict.reason != "no_action":
            log.info(
                "refused %r from %s: %s",  # %r: model output cannot forge log lines
                proposal.intent,
                proposal.source.value,
                verdict.reason,
                extra={"stage": "policy"},
            )
        return verdict

    def decide(self, proposal: IntentProposal) -> PolicyVerdict:
        """The verdict alone, with no side effects — for display. The gate is evaluate()."""
        s = self._settings

        if proposal.intent == NO_ACTION_INTENT:
            return PolicyVerdict(decision=Decision.DENY, reason="no_action")

        spec = self._registry.get(proposal.intent)
        if spec is None:
            # An intent outside the registry is the main thing this engine exists to stop.
            return PolicyVerdict(decision=Decision.DENY, reason="unknown_intent")

        if proposal.confidence < s.minimum_confidence:
            return PolicyVerdict(decision=Decision.DENY, reason="low_confidence", spec=spec)

        missing = [p for p in spec.permissions if p not in s.granted_permissions]
        if missing:
            return PolicyVerdict(
                decision=Decision.DENY,
                reason=f"permission_denied:{missing[0]}",
                spec=spec,
            )

        argument, arg_reason = self._resolve_argument(spec, proposal)
        if arg_reason is not None:
            return PolicyVerdict(decision=Decision.DENY, reason=arg_reason, spec=spec)
        text, text_reason = _resolve_text(spec, proposal)
        if text_reason is not None:
            return PolicyVerdict(decision=Decision.DENY, reason=text_reason, spec=spec)

        needs_confirmation = proposal.requires_confirmation or (
            s.require_confirmation_for_destructive_actions
            and spec.risk.rank >= s.confirmation_threshold.rank
        )
        if needs_confirmation:
            return PolicyVerdict(
                decision=Decision.CONFIRM,
                reason="confirmation_required",
                spec=spec,
                argument=argument,
                text=text,
            )

        return PolicyVerdict(
            decision=Decision.ALLOW, reason="ok", spec=spec, argument=argument, text=text
        )

    def _resolve_argument(
        self, spec: object, proposal: IntentProposal
    ) -> tuple[int | None, str | None]:
        argspec = getattr(spec, "argument", None)
        value = proposal.argument_seconds

        if argspec is None:
            # An argument supplied for a command that takes none is a sign of a confused
            # resolver; drop it rather than passing it to an executor that ignores it.
            return None, None

        if value is None:
            if argspec.required and argspec.default is None:
                return None, "missing_argument"
            return argspec.default, None

        if not isinstance(value, int) or isinstance(value, bool):
            return None, "invalid_argument_type"
        if value < argspec.minimum or value > argspec.maximum:
            return None, "argument_out_of_range"
        return value, None


def _resolve_text(spec: CommandSpec, proposal: IntentProposal) -> tuple[str | None, str | None]:
    """The text argument, checked: one of the spec's choices (matched without regard to
    case, returned as listed), or a public https address. Anything else is refused."""
    wanted = spec.text
    if wanted is None:
        return None, None  # dropped, like a number for a command that takes none
    value = (proposal.argument_text or "").strip()
    if not value:
        return None, "missing_argument"
    if wanted.kind == "choice":
        folded = value.casefold()
        match = next((c for c in wanted.choices if c.casefold() == folded), None)
        return (match, None) if match is not None else (None, "argument_not_allowed")
    address = web_address(value)
    return (address, None) if address is not None else (None, "argument_not_allowed")
