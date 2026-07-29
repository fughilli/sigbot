"""Static infra config (sigbot.yaml). Everything registered at runtime —
services (group personas), API keys, admin users — lives in the SQLite store
and is managed through the dashboard or the admin CLI, not this file."""

from __future__ import annotations

import dataclasses
import os
import pathlib

import yaml


@dataclasses.dataclass(frozen=True)
class SignalConfig:
    api_url: str
    bot_number: str


@dataclasses.dataclass(frozen=True)
class ApiConfig:
    host: str = "0.0.0.0"
    port: int = 8100


@dataclasses.dataclass(frozen=True)
class Config:
    signal: SignalConfig
    bot_name: str
    db_path: str
    api: ApiConfig
    # Anthropic key sources, first match wins: inline string in the config,
    # a key file (path relative to the workdir), then the env var.
    anthropic_api_key_inline: str | None = None
    anthropic_api_key_file: str | None = None
    anthropic_api_key_env: str = "ANTHROPIC_API_KEY"
    default_model: str = "claude-sonnet-4-6"

    @property
    def anthropic_api_key(self) -> str:
        if self.anthropic_api_key_inline:
            return self.anthropic_api_key_inline
        if self.anthropic_api_key_file:
            path = pathlib.Path(self.anthropic_api_key_file)
            try:
                key = path.read_text().strip()
            except OSError as e:
                raise RuntimeError(f"could not read anthropic_api_key_file {path}: {e}")
            if not key:
                raise RuntimeError(f"anthropic_api_key_file {path} is empty")
            return key
        key = os.environ.get(self.anthropic_api_key_env, "")
        if not key:
            raise RuntimeError(
                f"no Anthropic API key: set anthropic_api_key or "
                f"anthropic_api_key_file in the config, or export "
                f"${self.anthropic_api_key_env}")
        return key


def load(path: str | pathlib.Path = "sigbot.yaml") -> Config:
    raw = yaml.safe_load(pathlib.Path(path).read_text())
    sig = raw["signal"]
    api = raw.get("api", {})
    return Config(
        signal=SignalConfig(
            api_url=sig["api_url"].rstrip("/"),
            bot_number=sig["bot_number"],
        ),
        bot_name=raw.get("bot_name", "Bot"),
        db_path=raw.get("db_path", "data/sigbot.db"),
        api=ApiConfig(host=api.get("host", "0.0.0.0"), port=int(api.get("port", 8100))),
        anthropic_api_key_inline=raw.get("anthropic_api_key"),
        anthropic_api_key_file=raw.get("anthropic_api_key_file"),
        anthropic_api_key_env=raw.get("anthropic_api_key_env", "ANTHROPIC_API_KEY"),
        default_model=raw.get("default_model", "claude-sonnet-4-6"),
    )
