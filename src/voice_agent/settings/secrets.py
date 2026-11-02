"""Where the Groq API key lives: the environment (or .env) first, then Windows Credential
Manager. Never config files, never logs, never shown back to the user (invariant 6)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Protocol

from voice_agent.core.constants import APP_SLUG
from voice_agent.core.exceptions import ConfigError

GROQ_KEY_TARGET = f"{APP_SLUG}/GROQ_API_KEY"


class SecretStore(Protocol):
    def get(self, target: str) -> str | None: ...

    def set(self, target: str, value: str) -> None: ...

    def delete(self, target: str) -> None: ...


def groq_api_key(store: SecretStore | None, env: Mapping[str, str] | None = None) -> str | None:
    """The environment wins, so a developer's .env overrides what the UI stored."""
    found = (os.environ if env is None else env).get("GROQ_API_KEY")
    if found:
        return found
    return store.get(GROQ_KEY_TARGET) if store is not None else None


def save_groq_api_key(store: SecretStore, key: str) -> None:
    """Store a pasted key. Only obviously broken input is refused, and never echoed."""
    cleaned = key.strip()
    if not cleaned or any(ch.isspace() for ch in cleaned):
        msg = "that does not look like an API key (empty or contains spaces)"
        raise ConfigError(msg)
    store.set(GROQ_KEY_TARGET, cleaned)


def remove_groq_api_key(store: SecretStore) -> None:
    store.delete(GROQ_KEY_TARGET)


class CredentialManagerStore:  # pragma: no cover - the real Windows credential vault
    """Generic credentials in Windows Credential Manager, scoped to this user."""

    _NOT_FOUND = 1168  # ERROR_NOT_FOUND

    def __init__(self) -> None:
        import pywintypes
        import win32cred

        # CredRead builds the saved credential's date with win32timezone, which pywin32
        # imports behind the scenes: a frozen build leaves it out unless it is imported
        # here. Without it, reading a *saved* key failed, so Settings stopped opening and
        # the cloud never found the key, from the moment one was saved.
        import win32timezone  # noqa: F401

        self._cred = win32cred
        self._error = pywintypes.error

    def get(self, target: str) -> str | None:
        try:
            credential = self._cred.CredRead(target, self._cred.CRED_TYPE_GENERIC, 0)
        except self._error as exc:
            if exc.winerror == self._NOT_FOUND:
                return None
            raise
        blob = credential["CredentialBlob"]
        return blob.decode("utf-16-le") if isinstance(blob, bytes) else str(blob)

    def set(self, target: str, value: str) -> None:
        self._cred.CredWrite(
            {
                "Type": self._cred.CRED_TYPE_GENERIC,
                "TargetName": target,
                "UserName": APP_SLUG,
                "CredentialBlob": value,
                "Persist": self._cred.CRED_PERSIST_LOCAL_MACHINE,
            },
            0,
        )

    def delete(self, target: str) -> None:
        try:
            self._cred.CredDelete(target, self._cred.CRED_TYPE_GENERIC, 0)
        except self._error as exc:
            if exc.winerror != self._NOT_FOUND:
                raise
