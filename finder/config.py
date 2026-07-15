"""Static infra config (config.yaml). Everything the bot can change at runtime
lives in the SQLite store instead — see store.py."""

from __future__ import annotations

import dataclasses
import os
import pathlib

import yaml


@dataclasses.dataclass(frozen=True)
class SignalConfig:
    api_url: str
    bot_number: str
    user_number: str  # hard allowlist: the only sender the bot listens to


@dataclasses.dataclass(frozen=True)
class Config:
    signal: SignalConfig
    db_path: str
    anthropic_api_key_env: str = "ANTHROPIC_API_KEY"
    agent_model: str = "claude-sonnet-4-6"
    sources: dict = dataclasses.field(default_factory=dict)

    @property
    def anthropic_api_key(self) -> str:
        key = os.environ.get(self.anthropic_api_key_env, "")
        if not key:
            raise RuntimeError(f"missing ${self.anthropic_api_key_env}")
        return key


def load(path: str | pathlib.Path = "config.yaml") -> Config:
    raw = yaml.safe_load(pathlib.Path(path).read_text())
    sig = raw["signal"]
    return Config(
        signal=SignalConfig(
            api_url=sig["api_url"].rstrip("/"),
            bot_number=sig["bot_number"],
            user_number=sig["user_number"],
        ),
        db_path=raw.get("db_path", "data/bot.db"),
        anthropic_api_key_env=raw.get("anthropic_api_key_env", "ANTHROPIC_API_KEY"),
        agent_model=raw.get("agent_model", "claude-sonnet-4-6"),
        sources=raw.get("sources", {"craigslist": {"enabled": True}, "facebook": {"enabled": False}}),
    )
