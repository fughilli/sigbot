"""Static infra config (config.yaml). Everything the bot can change at runtime
lives in the SQLite store instead — see store.py."""

from __future__ import annotations

import dataclasses
import os
import pathlib

import yaml


@dataclasses.dataclass(frozen=True)
class SigbotConfig:
    api_url: str    # sigbot service API, e.g. http://localhost:8100
    api_key: str    # minted in the sigbot dashboard for the finder's service
    # Hard allowlist: the only sender the bot listens to, matched against the
    # message log's sender field (ACI uuid, or E.164 for number-sharing
    # accounts — uuid is the reliable form).
    user_id: str


@dataclasses.dataclass(frozen=True)
class Config:
    sigbot: SigbotConfig
    db_path: str
    anthropic_api_key_env: str = "ANTHROPIC_API_KEY"
    agent_model: str = "claude-sonnet-4-6"     # conversational bot (chat)
    judge_model: str = "claude-haiku-4-5"      # per-listing aesthetic verdict
    sources: dict = dataclasses.field(default_factory=dict)
    dashboard: dict = dataclasses.field(
        default_factory=lambda: {"enabled": True, "host": "127.0.0.1", "port": 8099})

    @property
    def anthropic_api_key(self) -> str:
        key = os.environ.get(self.anthropic_api_key_env, "")
        if not key:
            raise RuntimeError(f"missing ${self.anthropic_api_key_env}")
        return key


def load(path: str | pathlib.Path = "config.yaml") -> Config:
    raw = yaml.safe_load(pathlib.Path(path).read_text())
    sb = raw["sigbot"]
    api_key = sb.get("api_key") or os.environ.get(sb.get("api_key_env", "FINDER_SIGBOT_API_KEY"), "")
    if not api_key:
        raise RuntimeError("sigbot.api_key missing (mint one in the sigbot dashboard)")
    return Config(
        sigbot=SigbotConfig(
            api_url=sb["api_url"].rstrip("/"),
            api_key=api_key,
            user_id=sb["user_id"],
        ),
        db_path=raw.get("db_path", "data/bot.db"),
        anthropic_api_key_env=raw.get("anthropic_api_key_env", "ANTHROPIC_API_KEY"),
        agent_model=raw.get("agent_model", "claude-sonnet-4-6"),
        judge_model=raw.get("judge_model", "claude-haiku-4-5"),
        sources=raw.get("sources", {"craigslist": {"enabled": True}, "facebook": {"enabled": False}}),
        dashboard={"enabled": True, "host": "127.0.0.1", "port": 8099, **raw.get("dashboard", {})},
    )
