"""Loads `Settings`: default.yaml <- user.yaml (optional) <- VOICE_AGENT__SECTION__KEY env.

Secrets are never read here; they come from the environment or Credential Manager at the
point of use. Every failure surfaces as a ConfigError naming the offending key path.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from voice_agent.core.exceptions import ConfigError
from voice_agent.settings.paths import APP_ROOT, data_dir
from voice_agent.settings.schema import Settings

DEFAULT_CONFIG_DIR = APP_ROOT / "config"
ENV_PREFIX = "VOICE_AGENT__"


def load_settings(
    config_dir: Path = DEFAULT_CONFIG_DIR, env: Mapping[str, str] | None = None
) -> Settings:
    """Build validated settings; `env` is injectable so tests never read the real one."""
    default = config_dir / "default.yaml"
    if not default.is_file():
        msg = f"default config not found: {default}"
        raise ConfigError(msg)
    data = _read_yaml(default)

    user = user_settings_file(config_dir)
    if user.is_file():
        data = _merge(data, _read_yaml(user))

    _apply_env(data, os.environ if env is None else env)
    return _validate(data)


def save_user_settings(
    updates: Mapping[str, Any], config_dir: Path = DEFAULT_CONFIG_DIR
) -> Settings:
    """Merge `updates` into user.yaml — only if default + user.yaml + updates validates, and
    atomically, so a bad edit or a crash mid-write can never break the next start."""
    user = user_settings_file(config_dir)
    merged_user = _merge(_read_yaml(user) if user.is_file() else {}, updates)
    settings = _validate(_merge(_read_yaml(config_dir / "default.yaml"), merged_user))
    user.parent.mkdir(parents=True, exist_ok=True)
    staging = user.with_name("user.yaml.tmp")
    staging.write_text(yaml.safe_dump(merged_user, sort_keys=True), encoding="utf-8")
    staging.replace(user)
    return settings


def user_settings_file(config_dir: Path = DEFAULT_CONFIG_DIR) -> Path:
    """config/user.yaml when run from source. The packaged app keeps it in the per-user data
    folder, so unzipping a new version over the old app folder keeps the user's settings."""
    if getattr(sys, "frozen", False):
        return data_dir() / "user.yaml"
    return config_dir / "user.yaml"


def _validate(data: Mapping[str, Any]) -> Settings:
    try:
        return Settings.model_validate(data)
    except ValidationError as exc:
        problems = "; ".join(
            f"'{'.'.join(str(p) for p in err['loc'])}': {err['msg']}" for err in exc.errors()
        )
        msg = f"invalid config at {problems}"
        raise ConfigError(msg) from exc


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        # Report the position only: the YAML error text can quote the offending line.
        mark = getattr(exc, "problem_mark", None)
        where = f" (line {mark.line + 1})" if mark is not None else ""
        msg = f"{path.name} is not valid YAML{where}"
        raise ConfigError(msg) from None
    if data is None:
        return {}
    if not isinstance(data, dict):
        msg = f"{path.name} must contain a mapping at the top level"
        raise ConfigError(msg)
    return data


def _merge(base: dict[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    """Deep merge so a user.yaml can override one key without restating its section."""
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def _apply_env(data: dict[str, Any], env: Mapping[str, str]) -> None:
    for name, raw in env.items():
        if not name.upper().startswith(ENV_PREFIX):
            continue
        *parents, leaf = name[len(ENV_PREFIX) :].lower().split("__")
        node: Any = data
        for part in parents:
            node = node.setdefault(part, {}) if isinstance(node, dict) else None
        if not isinstance(node, dict):
            msg = f"{name} does not address a config key"
            raise ConfigError(msg)
        node[leaf] = _parse_scalar(raw)


def _parse_scalar(raw: str) -> Any:
    """Read env values as YAML scalars so 'false', '3' and 'null' arrive typed."""
    try:
        return yaml.safe_load(raw)
    except yaml.YAMLError:
        return raw
