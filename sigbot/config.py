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
    anthropic_api_key_env: str = "ANTHROPIC_API_KEY"
    default_model: str = "claude-sonnet-4-6"

    @property
    def anthropic_api_key(self) -> str:
        key = os.environ.get(self.anthropic_api_key_env, "")
        if not key:
            raise RuntimeError(f"missing ${self.anthropic_api_key_env}")
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
        anthropic_api_key_env=raw.get("anthropic_api_key_env", "ANTHROPIC_API_KEY"),
        default_model=raw.get("default_model", "claude-sonnet-4-6"),
    )
