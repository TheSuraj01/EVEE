"""The command registry: the single source of truth for what this agent can do.

The allowlist used by the policy engine *and* the enum handed to the cloud model are both
derived from this object at runtime. They cannot drift apart, which is the point.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator

from voice_agent.core.exceptions import PolicyViolation
from voice_agent.core.models import CommandSpec
from voice_agent.speech.text_normalizer import command_key


class CommandRegistry:
    def __init__(self) -> None:
        self._by_intent: dict[str, CommandSpec] = {}
        self._by_alias: dict[str, str] = {}

    def register(self, spec: CommandSpec) -> None:
        """Register one command. Duplicate intents or aliases are a hard error: a silent
        override would let a plugin hijack a built-in command."""
        if spec.intent in self._by_intent:
            msg = f"duplicate intent: {spec.intent}"
            raise PolicyViolation(msg)
        for alias in spec.aliases:
            key = command_key(alias)
            if not key:
                msg = f"empty alias for {spec.intent}"
                raise PolicyViolation(msg)
            owner = self._by_alias.get(key)
            if owner is not None and owner != spec.intent:
                msg = f"alias {alias!r} already maps to {owner}"
                raise PolicyViolation(msg)
            self._by_alias[key] = spec.intent
        self._by_intent[spec.intent] = spec

    def register_all(self, specs: Iterable[CommandSpec]) -> None:
        """All or nothing: one bad entry (say, a plugin claiming a built-in alias) leaves the
        registry exactly as it was."""
        trial = CommandRegistry()
        trial._by_intent, trial._by_alias = dict(self._by_intent), dict(self._by_alias)
        for spec in specs:
            trial.register(spec)
        self._by_intent, self._by_alias = trial._by_intent, trial._by_alias

    def get(self, intent: str) -> CommandSpec | None:
        return self._by_intent.get(intent)

    def require(self, intent: str) -> CommandSpec:
        spec = self._by_intent.get(intent)
        if spec is None:
            msg = f"unknown intent: {intent}"
            raise PolicyViolation(msg)
        return spec

    def resolve_alias(self, phrase: str) -> str | None:
        return self._by_alias.get(command_key(phrase))

    def intents(self) -> tuple[str, ...]:
        """Sorted for determinism: this tuple becomes part of the LLM prompt and schema."""
        return tuple(sorted(self._by_intent))

    def aliases(self) -> tuple[str, ...]:
        return tuple(sorted(self._by_alias))

    def __contains__(self, intent: object) -> bool:
        return intent in self._by_intent

    def __iter__(self) -> Iterator[CommandSpec]:
        return iter(self._by_intent.values())

    def __len__(self) -> int:
        return len(self._by_intent)
