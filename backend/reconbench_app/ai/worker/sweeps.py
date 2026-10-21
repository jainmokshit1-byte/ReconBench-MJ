"""Heartbeat and periodic sweeps run alongside the AI worker's job loop."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.engine import Engine

from reconbench_app.ai import cache as ai_cache
from reconbench_app.ai import connection_state, ledger, vision
from reconbench_app.ai.contracts import SCHEMA_VERSIONS
from reconbench_app.ai.endpoint_policy import EndpointNotAllowed
from reconbench_app.ai.fixtures import PROVIDER_TEST
from reconbench_app.ai.limits import AiLimits, LimitsState
from reconbench_app.ai.redaction import REDACTION_VERSION
from reconbench_app.db.metadata import metadata

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
        "stale_rotations": connection_state.clear_stale_rotations(engine, now=now),
    }


def provider_health(engine: Engine, provider: Any, now: datetime) -> int:
    """``GET /models`` once per enabled vLLM server URL; store the result on its cards.

    ``provider`` is the worker's vLLM provider (``for_base_url`` + ``health``). A card
    whose URL is no longer allowlisted is marked unhealthy without any I/O. Health is
    checked without a key: a server that requires one reports ``bad_key`` here and is
    proven by the provider test instead. Returns the number of cards updated.
    """

    configs = metadata.tables["ai_provider_config"]
    with engine.connect() as connection:
        urls = sorted(
            {
                str(url)
                for url in connection.scalars(
                    sa.select(configs.c.base_url).where(
                        configs.c.provider == "vllm",
                        configs.c.enabled.is_(True),
                        configs.c.base_url.is_not(None),
                    )
                )
            }
        )
    updated = 0
    for url in urls:
        code: str | None = None
        try:
            result = provider.for_base_url(url).health()
            ok = bool(result.ok)
            code = None if ok else getattr(result, "code", None)
        except EndpointNotAllowed:
            ok, code = False, "endpoint_not_allowed"
        with engine.begin() as connection:
            updated += int(
                connection.execute(
                    sa.update(configs)
                    .where(
                        configs.c.provider == "vllm",
                        configs.c.enabled.is_(True),
                        configs.c.base_url == url,
                    )
                    .values(**connection_state.health_values(now, ok=ok, code=code))
                ).rowcount
            )
    return updated


GEMINI_HEALTH_EVERY = timedelta(hours=1)


def gemini_health(
    engine: Engine,
    run_request: Callable[[UUID], Any],
    now: datetime,
    *,
    every: timedelta = GEMINI_HEALTH_EVERY,
) -> int:
    """At most one synthetic test request per Gemini card per ``every`` (default one hour).

    The pinned SDK offers no known call that is neither billed nor counted against the
    quota (**unverified**, plan WI-PV2), so the check is the same tiny synthetic request as
    a provider test, sent at most hourly and recorded in the ledger (so it is counted in the
    day's requests). Only cards that were tested before are checked; the result updates
    ``last_health_*`` and flips Connected <-> Error, never Not connected -> Connected.
    Returns the number of requests run.
    """

    configs = metadata.tables["ai_provider_config"]
    settings = metadata.tables["ai_setting"]
    with engine.connect() as connection:
        cards = (
            connection.execute(
                sa.select(
                    configs.c.workspace_id,
                    settings.c.mode,
                    settings.c.model,
                    settings.c.version,
                )
                .select_from(
                    configs.join(settings, settings.c.workspace_id == configs.c.workspace_id)
                )
                .where(
                    configs.c.provider == "gemini",
                    sa.or_(
                        configs.c.connected_at.is_not(None),
                        configs.c.last_error_code.is_not(None),
                    ),
                    sa.or_(
                        configs.c.key_secret_id.is_not(None),
                        settings.c.key_secret_id.is_not(None),
                    ),
                    settings.c.provider == "gemini",
                    settings.c.model.is_not(None),
                    sa.or_(
                        configs.c.last_health_at.is_(None),
                        configs.c.last_health_at <= now - every,
                    ),
                )
            )
            .mappings()
            .all()
        )
    ran = 0
    hour = now.astimezone(UTC).strftime("%Y%m%dT%H")
    for card in cards:
        with engine.begin() as connection:
            recent = connection.scalar(
                sa.select(sa.func.count())
                .select_from(ledger.REQUESTS)
                .where(
                    ledger.REQUESTS.c.workspace_id == card["workspace_id"],
                    ledger.REQUESTS.c.operation_key.like(
                        f"{connection_state.HEALTH_PREFIX}gemini:%"
                    ),
                    ledger.REQUESTS.c.created_at > now - every,
                )
            )
            if recent:
                continue
            request_id = ledger.create_request(
                connection,
                ledger.NewRequest(
                    workspace_id=card["workspace_id"],
                    call_kind="provider_test",
                    hook=None,
                    mode=str(card["mode"]),
                    subject_type="fixture",
                    subject_id=None,
                    subject_version=hour,
                    operation_key=f"{connection_state.HEALTH_PREFIX}gemini:{hour}",
                    settings_version=int(card["version"]),
                    consent_fingerprint=None,
                    provider="gemini",
                    model=str(card["model"]),
                    prompt_version=PROVIDER_TEST.fixture_id,
                    schema_version=SCHEMA_VERSIONS["provider_test"],
                    redaction_version=REDACTION_VERSION,
                    requested_by=None,
                ),
            )
        if request_id is None:
            continue
        run_request(request_id)
        ran += 1
    return ran


async def heartbeat_loop(
    engine: Engine,
    instance: str,
    *,
    interval_seconds: float,
    limits: Callable[[], LimitsState],
    vllm_provider: Any = None,
    gemini_health_check: Callable[[datetime], Any] | None = None,
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
                if vllm_provider is not None:
                    await asyncio.to_thread(provider_health, engine, vllm_provider, now)
                if gemini_health_check is not None:
                    await asyncio.to_thread(gemini_health_check, now)
        except sa.exc.DBAPIError:
            _LOGGER.warning("ai heartbeat or sweep failed; retrying next interval")
        beats += 1
        await asyncio.sleep(interval_seconds)


__all__ = ["beat", "gemini_health", "heartbeat_loop", "provider_health", "sweep_once"]
