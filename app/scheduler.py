"""Periodic updates from the persisted source, while the app is running."""
from __future__ import annotations

import asyncio
import logging

from app import saved_source, workflows
from app.settings import Settings


SYNC_INTERVAL_SECONDS = 12 * 60 * 60
logger = logging.getLogger(__name__)


def sync_saved_source(settings: Settings) -> None:
    # Read the current record on every tick, including URLs saved after startup.
    if not settings.allow_url_fetch or not saved_source.load_url(settings):
        return

    result = workflows.update_saved(settings)
    logger.info("Scheduled saved-source sync completed.")
    for warning in result.warnings:
        logger.warning("Scheduled saved-source sync: %s", warning)


async def run_periodic_sync(settings: Settings, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=SYNC_INTERVAL_SECONDS)
            return
        except TimeoutError:
            pass

        if stop.is_set():
            return
        try:
            # Fetching, file operations, and custom commands must not block HTTP.
            await asyncio.to_thread(sync_saved_source, settings)
        except Exception:
            logger.exception("Scheduled saved-source sync failed; will retry in 12 hours.")
