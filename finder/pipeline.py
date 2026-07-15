"""One scrape pass: fetch -> normalize -> dedup -> filter -> score -> notify.

M1 stub: no fetchers are wired yet (M2 adds Craigslist, M4 Facebook), so a
pass just reports that. The interface is stable: run_pass returns a summary
dict stored under the 'last_run' setting and readable via the status tool.
"""

from __future__ import annotations

import logging

from finder.config import Config
from finder.notify.signal import SignalClient
from finder.store import Store

log = logging.getLogger(__name__)


async def run_pass(config: Config, store: Store, client: SignalClient, group: dict) -> dict:
    queries = store.list_queries(include_paused=False)
    location = store.get_setting("location")
    if not queries or not location:
        log.info("pass skipped: no active queries or no location set")
        return {"skipped": "no active queries or location not set"}

    # M2: craigslist fetcher -> store.seen() dedup -> hard filters -> notify.
    log.info("pass ran (no fetchers enabled yet): %d queries", len(queries))
    return {"fetched": 0, "new": 0, "matched": 0, "note": "no fetchers enabled yet (M2)"}
