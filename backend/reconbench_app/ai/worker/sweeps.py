"""Heartbeat and periodic sweeps run alongside the AI worker's job loop."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.engine import Engine

from reconbench_app.ai import cache as ai_cache
from reconbench_app.ai import ledger, vision
from reconbench_app.ai.limits import AiLimits, LimitsState

_LOGGER = logging.getLogger(__name__)
SWEEP_EVERY_HEARTBEATS = 3


def beat(engine: Engine, instance: str, now: datetime) -> None:
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO ai_worker_heartbeat (worker_role, instance, started_at, last_seen_at) "
                "VALUES ('ai', :instance, :now, :now) "
                "ON CONFLICT (worker_role) DO UPDATE SET instance = EXCLUDED.instance, "
                "last_seen_at = EXCLUDED.last_seen_at"
            ),
            {"instance": instance[:200], "now": now},
        )


def sweep_once(engine: Engine, limits: AiLimits, now: datetime) -> dict[str, int]:
    requests = ledger.sweep_requests(
        engine, now=now, queue_timeout_seconds=limits.queue_timeout_seconds
    )
    return {
        "uncertain": requests.uncertain,
        "released": requests.released,
        "skipped": requests.skipped,
        "expired_keys": ledger.sweep_expired_secrets(engine, now=now),
        "cache_purged": ai_cache.purge_expired(engine, now=now, ttl_days=limits.cache_ttl_days),
        "page_images_purged": vision.purge_idle_pages(engine, now=now),
    }


async def heartbeat_loop(
    engine: Engine,
    instance: str,
    *,
    interval_seconds: float,
    limits: Callable[[], LimitsState],
) -> None:
    beats = 0
    while True:
        now = datetime.now(UTC)
        try:
            await asyncio.to_thread(beat, engine, instance, now)
            if beats % SWEEP_EVERY_HEARTBEATS == 0:
                state = limits()
                if state.limits is not None:
                    counts = await asyncio.to_thread(sweep_once, engine, state.limits, now)
                    if any(counts.values()):
                        _LOGGER.info("ai sweep", extra={"counts": counts})
        except sa.exc.DBAPIError:
            _LOGGER.warning("ai heartbeat or sweep failed; retrying next interval")
        beats += 1
        await asyncio.sleep(interval_seconds)


__all__ = ["beat", "heartbeat_loop", "sweep_once"]
