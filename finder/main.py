"""Entry point: starts the Signal listener and the scrape scheduler."""

from __future__ import annotations

import asyncio
import functools
import logging
import os

from finder import config as config_mod
from finder import pipeline
from finder.bot.agent import Agent
from finder.bot.listener import run_listener
from finder.bot.tools import Tools
from finder.notify.groups import ensure_finder_group
from finder.notify.signal import SignalClient
from finder.scheduler import ScrapeScheduler
from finder.store import Store

log = logging.getLogger(__name__)

_ONBOARDING = (
    "\U0001f44b I'm your furniture finder. Tell me where to search "
    "(zip code + radius) and what you're hunting for — e.g. "
    "\"couches under $500 near 94103, mid-century modern\". "
    "You can also send photos of pieces you love and I'll use them as "
    "aesthetic references."
)


async def run() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    # `bazel run` starts us in the runfiles tree; config.yaml, data/ and
    # references/ are all resolved relative to the invocation directory.
    if os.environ.get("BUILD_WORKING_DIRECTORY"):
        os.chdir(os.environ["BUILD_WORKING_DIRECTORY"])
    config = config_mod.load(os.environ.get("FINDER_CONFIG", "config.yaml"))
    store = Store(config.db_path)
    client = SignalClient(config.signal.api_url, config.signal.bot_number)

    group = await ensure_finder_group(client, store, config.signal.user_id)
    log.info("finder group ready: %s", group["send_id"])

    pass_fn = functools.partial(pipeline.run_pass, config, store, client, group)
    scheduler = ScrapeScheduler(store, pass_fn)
    tools = Tools(
        store,
        trigger_run=scheduler.trigger_now,
        get_status=scheduler.status,
        on_cadence_change=scheduler.apply_cadence,
    )
    agent = Agent(config, store, client, group, tools)

    scheduler.start()
    if not store.get_setting("onboarded"):
        await client.send(group["send_id"], _ONBOARDING)
        store.set_setting("onboarded", True)

    await run_listener(client, config.signal.user_id, group, agent.handle)


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
