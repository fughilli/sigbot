"""Entry point: starts the HTTP server (service API + dashboard) and the
Signal listener that drives per-group personas."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import secrets

from sigbot import auth
from sigbot import config as config_mod
from sigbot.agent import PersonaAgent
from sigbot.api import start_server
from sigbot.listener import run_listener
from sigbot.signal_client import SignalClient
from sigbot.store import Store

log = logging.getLogger(__name__)


def bootstrap_admin(store: Store) -> None:
    """First run: create the dashboard admin. Password comes from
    $SIGBOT_ADMIN_PASSWORD, else is generated and printed ONCE — reset later
    with `bazel run //sigbot:admin -- set-password <user>`."""
    if store.count_admins():
        return
    username = os.environ.get("SIGBOT_ADMIN_USER", "admin")
    password = os.environ.get("SIGBOT_ADMIN_PASSWORD")
    generated = password is None
    if generated:
        password = secrets.token_urlsafe(12)
    store.upsert_admin(username, auth.hash_password(password))
    if generated:
        print(f"\n*** dashboard admin created: {username} / {password} "
              "(shown once — change it with //sigbot:admin) ***\n", flush=True)
    else:
        log.info("dashboard admin %r created from environment", username)


async def run(config_path: str) -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    config = config_mod.load(config_path)
    store = Store(config.db_path)
    client = SignalClient(config.signal.api_url, config.signal.bot_number)
    bootstrap_admin(store)

    try:
        await client.set_profile_name(config.bot_name)
        log.info("profile name set to %r", config.bot_name)
    except Exception as e:
        log.warning("could not set profile name (%s) — continuing", e)

    await start_server(store, client, host=config.api.host, port=config.api.port,
                       default_model=config.default_model)

    agent = PersonaAgent(config, store, client)
    await run_listener(client, config.bot_name, store.services_by_group, agent.handle)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generic multi-persona Signal bot",
        epilog="All persistent artifacts (sigbot.yaml, data/) live under --workdir.",
    )
    parser.add_argument(
        "--workdir",
        default=os.environ.get("BUILD_WORKING_DIRECTORY") or ".",
        help="directory holding persistent state; must survive container "
             "restarts (default: bazel invocation dir, else cwd)",
    )
    parser.add_argument(
        "--config",
        default=os.environ.get("SIGBOT_CONFIG", "sigbot.yaml"),
        help="static config file, resolved relative to --workdir",
    )
    args = parser.parse_args()
    os.chdir(args.workdir)
    asyncio.run(run(args.config))


if __name__ == "__main__":
    main()
