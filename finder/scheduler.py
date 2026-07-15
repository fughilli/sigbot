"""Scrape-pass scheduling inside the daemon (not host cron), so cadence
changes made in conversation take effect immediately."""

from __future__ import annotations

import datetime
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from finder.bot import tools as tools_mod
from finder.store import Store

log = logging.getLogger(__name__)

_JITTER_S = 20 * 60
_JOB_ID = "scrape_pass"


class ScrapeScheduler:
    """Wraps APScheduler; `pass_fn` is an async callable running one pass."""

    def __init__(self, store: Store, pass_fn):
        self.store = store
        self.pass_fn = pass_fn
        self.scheduler = AsyncIOScheduler()

    def start(self) -> None:
        minutes = self.store.get_setting(
            "cadence_minutes", tools_mod.DEFAULT_CADENCE_MINUTES
        )
        self.scheduler.add_job(
            self._run, IntervalTrigger(minutes=minutes, jitter=_JITTER_S),
            id=_JOB_ID, max_instances=1, coalesce=True,
        )
        self.scheduler.start()
        log.info("scheduled scrape pass every %s min (±20 min jitter)", minutes)

    def apply_cadence(self) -> None:
        """Re-read cadence from the store (called after set_cadence)."""
        minutes = self.store.get_setting(
            "cadence_minutes", tools_mod.DEFAULT_CADENCE_MINUTES
        )
        self.scheduler.reschedule_job(
            _JOB_ID, trigger=IntervalTrigger(minutes=minutes, jitter=_JITTER_S)
        )
        log.info("rescheduled scrape pass to every %s min", minutes)

    def trigger_now(self) -> str:
        """Sync-callable (from the tool layer): pull the next fire time to now."""
        self.scheduler.modify_job(
            _JOB_ID, next_run_time=datetime.datetime.now(datetime.timezone.utc)
        )
        return "scrape pass starting"

    def status(self) -> dict:
        job = self.scheduler.get_job(_JOB_ID)
        return {
            "next_run": job.next_run_time.isoformat(timespec="seconds") if job and job.next_run_time else None,
        }

    async def _run(self) -> None:
        try:
            summary = await self.pass_fn()
            self.store.set_setting("last_run", {
                "at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
                **(summary or {}),
            })
        except Exception:
            log.exception("scrape pass failed")
