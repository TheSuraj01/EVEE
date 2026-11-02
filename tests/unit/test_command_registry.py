from __future__ import annotations

import pytest

from voice_agent.core.exceptions import PolicyViolation
from voice_agent.core.models import CommandSpec
from voice_agent.engine.catalog import load_command_specs
from voice_agent.engine.command_registry import CommandRegistry


def test_resolves_alias_regardless_of_formatting(registry: CommandRegistry) -> None:
    assert registry.resolve_alias("Next Tab!") == "browser.next_tab"


def test_duplicate_intent_is_rejected(registry: CommandRegistry) -> None:
    with pytest.raises(PolicyViolation):
        registry.register(CommandSpec(intent="browser.next_tab"))


def test_alias_collision_between_plugins_is_rejected(registry: CommandRegistry) -> None:
    with pytest.raises(PolicyViolation, match="already maps to"):
        registry.register(CommandSpec(intent="evil.plugin", aliases=("next tab",)))


def test_intents_are_sorted_for_deterministic_prompts(registry: CommandRegistry) -> None:
    assert list(registry.intents()) == sorted(registry.intents())


def test_shipped_command_table_loads_and_registers() -> None:
    reg = CommandRegistry()
    reg.register_all(load_command_specs())
    assert set(reg.intents()) == {
        "ui.exit_fullscreen",
        "ui.enter_fullscreen",
        "system.no_action",
        "system.sleep",
        "web.open",
    }  # everything else comes from plugins (T-042), and app.open from the Start menu
