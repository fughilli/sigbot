"""Per-function Signal groups (PLAN.md §4.6): the bot creates one named group
per function, containing just the user, and remembers its ids in settings."""

from __future__ import annotations

import logging

from finder.notify.signal import SignalClient
from finder.store import Store

log = logging.getLogger(__name__)

FINDER_GROUP_NAME = "\U0001f6cb Furniture Finder"
_SETTING_KEY = "finder_group"


async def ensure_finder_group(client: SignalClient, store: Store, user_id: str) -> dict:
    """Returns {'send_id': 'group.<b64>', 'internal_id': '<b64>'} — creating the
    group on first run, recovering it by name if settings were lost."""
    cached = store.get_setting(_SETTING_KEY)
    groups = {g.get("internal_id"): g for g in await client.list_groups()}

    if cached and cached.get("internal_id") in groups:
        return cached

    for g in groups.values():  # settings lost but group exists — re-adopt
        if g.get("name") == FINDER_GROUP_NAME:
            ids = {"send_id": g["id"], "internal_id": g["internal_id"]}
            store.set_setting(_SETTING_KEY, ids)
            return ids

    log.info("creating group %r", FINDER_GROUP_NAME)
    created = await client.create_group(FINDER_GROUP_NAME, members=[user_id])
    created_id = created.get("id", "")
    # The create response's id is the prefixed send-id ("group.<b64>"), not the
    # internal id — resolve both canonically from the group list instead of
    # deriving one from the other.
    for g in await client.list_groups():
        if created_id in (g["id"], g.get("internal_id")) or g.get("name") == FINDER_GROUP_NAME:
            ids = {"send_id": g["id"], "internal_id": g["internal_id"]}
            store.set_setting(_SETTING_KEY, ids)
            return ids
    raise RuntimeError(f"created group {created_id!r} not found in group list")
