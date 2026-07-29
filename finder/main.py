"""Entry point: starts the sigbot-API poller and the scrape scheduler.

The finder is an API client of sigbot (which owns the Signal account): its
minted API key confines it to the furniture-finder group's service, whose
persona is set to respond_to='none' so the finder alone drives the chat.
"""

from __future__ import annotations

import argparse
import asyncio
import functools
import logging
import os

from finder import config as config_mod
from finder import pipeline
from finder.bot.agent import Agent
from finder.events import EventBus
from finder.bot.listener import run_listener
from finder.bot.tools import Tools
from finder.notify.sigbot_api import SigbotService
from finder.scheduler import ScrapeScheduler
from finder.store import Store
from finder.web.server import start_dashboard

log = logging.getLogger(__name__)

_ONBOARDING = (
    "\U0001f44b I'm your furniture finder. Tell me where to search "
    "(zip code + radius) and what you're hunting for — e.g. "
    "\"couches under $500 near 94103, mid-century modern\". "
    "You can also send photos of pieces you love and I'll use them as "
    "aesthetic references."
)


async def run(config_path: str) -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    config = config_mod.load(config_path)
    store = Store(config.db_path)
    svc = SigbotService(config.sigbot.api_url, config.sigbot.api_key)

    bus = EventBus()
    pass_fn = functools.partial(pipeline.run_pass, config, store, svc, bus)
    scheduler = ScrapeScheduler(store, pass_fn)
    tools = Tools(
        store,
        trigger_run=scheduler.trigger_now,
        get_status=scheduler.status,
        on_cadence_change=scheduler.apply_cadence,
    )
    agent = Agent(config, store, svc, tools)

    scheduler.start()
    if config.dashboard.get("enabled"):
        await start_dashboard(store, bus, scheduler,
                              host=config.dashboard["host"],
                              port=config.dashboard["port"])
    if not store.get_setting("onboarded"):
        try:
            await svc.send(_ONBOARDING)
            store.set_setting("onboarded", True)
        except Exception as e:
            log.warning("onboarding send failed (%s); will retry next start", e)

    await run_listener(svc, store, config.sigbot.user_id, agent.handle)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Furniture finder Signal bot (sigbot API client)",
        epilog="All persistent artifacts (config.yaml, data/, references/, "
               "cache/) live under --workdir.",
    )
    parser.add_argument(
        "--workdir",
        default=os.environ.get("BUILD_WORKING_DIRECTORY") or ".",
        help="directory holding persistent state; must survive container "
             "restarts (default: bazel invocation dir, else cwd)",
    )
    parser.add_argument(
        "--config",
        default=os.environ.get("FINDER_CONFIG", "config.yaml"),
        help="static config file, resolved relative to --workdir",
    )
    args = parser.parse_args()
    os.chdir(args.workdir)
    asyncio.run(run(args.config))


if __name__ == "__main__":
    main()
