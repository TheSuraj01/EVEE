"""The plugin manifest (plugin.yaml) as a typed model.

specs/plugin-manifest.schema.json is the published contract and a test holds the two in
step. A manifest DECLARES; the host grants and enforces. One deliberate tightening over the
JSON schema: `entrypoint` must be a bare `name.py`, so it cannot point outside the plugin.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from voice_agent.core.models import ArgumentSpec, CommandSpec, Risk

Permission = Literal[
    "keyboard.shortcut",
    "keyboard.type",
    "window.manage",
    "media.control",
    "clipboard.read",
    "clipboard.write",
]


class _Strict(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ManifestCommand(_Strict):
    intent: str = Field(pattern=r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")
    description: str = Field(default="", max_length=160)
    risk: Risk
    aliases: tuple[str, ...] = ()
    argument: ArgumentSpec | None = None

    @field_validator("aliases")
    @classmethod
    def _unique_aliases(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            msg = "aliases must be unique"
            raise ValueError(msg)
        return value


class PluginManifest(_Strict):
    id: str = Field(pattern=r"^[a-z][a-z0-9-]{1,31}$")
    name: str = Field(max_length=64)
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    description: str = Field(default="", max_length=256)
    author: str = ""
    entrypoint: str = Field(default="plugin.py", pattern=r"^[A-Za-z0-9_\-]+\.py$")
    permissions: tuple[Permission, ...]
    commands: tuple[ManifestCommand, ...] = Field(min_length=1)

    @field_validator("permissions")
    @classmethod
    def _unique_permissions(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            msg = "permissions must be unique"
            raise ValueError(msg)
        return value

    @field_validator("commands")
    @classmethod
    def _unique_intents(cls, value: tuple[ManifestCommand, ...]) -> tuple[ManifestCommand, ...]:
        intents = [c.intent for c in value]
        dupes = sorted({i for i in intents if intents.count(i) > 1})
        if dupes:
            msg = f"duplicate intent in manifest: {dupes[0]}"
            raise ValueError(msg)
        return value

    def specs(self) -> tuple[CommandSpec, ...]:
        """Registry entries. Every command carries the plugin's declared permissions, so the
        policy engine refuses all of them if the host stops granting one."""
        return tuple(
            CommandSpec(
                intent=c.intent,
                description=c.description,
                aliases=c.aliases,
                risk=c.risk,
                permissions=self.permissions,
                argument=c.argument,
                plugin_id=self.id,
            )
            for c in self.commands
        )
