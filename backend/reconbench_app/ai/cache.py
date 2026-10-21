"""Workspace-scoped cache of validated, normalised provider output.

``cache_key`` identifies *content* (exact payload digest, model and versions), so
two subjects with identical sent text share an entry. ``request_key`` (ledger)
identifies a *subject attempt*. Lookups always filter by workspace, so entries
are never shared across workspaces, and entries older than the TTL are ignored
and purged.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.engine import Connection, Engine

from reconbench_app.ai.vision import HOOK as VISION_HOOK
from reconbench_app.db.ids import new_id
from reconbench_app.db.metadata import metadata

CACHE = metadata.tables["ai_cache"]
REQUESTS = metadata.tables["ai_request"]
CONSENSUS_RUNS = metadata.tables["consensus_run"]


def cache_key(
    *,
    hook: str,
    model: str,
    prompt_version: str,
    schema_version: str,
    redaction_version: str,
    payload_digest: str,
) -> str:
    canonical = json.dumps(
        {
            "hook": hook,
            "model": model,
            "payload_digest": payload_digest,
            "prompt_version": prompt_version,
            "redaction_version": redaction_version,
            "schema_version": schema_version,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _digest(document: Mapping[str, Any]) -> str:
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def reader_params_digest(reader_params: Mapping[str, Any]) -> str:
    return _digest(reader_params)


def cache_key_v2(
    *,
    provider: str,
    hook: str,
    model: str,
    prompt_version: str,
    schema_version: str,
    redaction_version: str,
    reader_params_digest: str,
    payload_digest: str,
) -> str:
    """Vision key (WI-BE5): adds the provider and the reader/image parameters to v1.

    Text hooks H1–H4 keep :func:`cache_key` so their cached answers stay valid.
    """

    return _digest(
        {
            "hook": hook,
            "key_version": 2,
            "model": model,
            "payload_digest": payload_digest,
            "prompt_version": prompt_version,
            "provider": provider,
            "reader_params_digest": reader_params_digest,
            "redaction_version": redaction_version,
            "schema_version": schema_version,
        }
    )


def request_cache_key(request: Mapping[str, Any], payload_digest: str) -> str:
    """The key for one ledger row: vision answers use v2, text hooks keep v1."""

    if request["hook"] == VISION_HOOK:
        return cache_key_v2(
            provider=str(request["provider"]),
            hook=str(request["hook"]),
            model=str(request["model"]),
            prompt_version=str(request["prompt_version"]),
            schema_version=str(request["schema_version"]),
            redaction_version=str(request["redaction_version"]),
            reader_params_digest=reader_params_digest(request["reader_params"] or {}),
            payload_digest=payload_digest,
        )
    return cache_key(
        hook=str(request["hook"]),
        model=str(request["model"]),
        prompt_version=str(request["prompt_version"]),
        schema_version=str(request["schema_version"]),
        redaction_version=str(request["redaction_version"]),
        payload_digest=payload_digest,
    )


def lookup(
    connection: Connection, *, workspace_id: UUID, key: str, now: datetime, ttl_days: int
) -> tuple[UUID, dict[str, Any]] | None:
    row = (
        connection.execute(
            sa.update(CACHE)
            .where(
                CACHE.c.workspace_id == workspace_id,
                CACHE.c.cache_key == key,
                CACHE.c.created_at > now - timedelta(days=ttl_days),
            )
            .values(hit_count=CACHE.c.hit_count + 1, last_hit_at=now)
            .returning(CACHE.c.id, CACHE.c.response)
        )
        .mappings()
        .first()
    )
    if row is None:
        return None
    return UUID(str(row["id"])), dict(row["response"])


def store(
    connection: Connection,
    *,
    workspace_id: UUID,
    key: str,
    hook: str,
    model: str,
    prompt_version: str,
    document_hash: str | None,
    normalized_output: dict[str, Any],
    provider: str | None = None,
    schema_version: str | None = None,
    reader_params: Mapping[str, Any] | None = None,
) -> None:
    connection.execute(
        postgres_insert(CACHE)
        .values(
            id=new_id(),
            workspace_id=workspace_id,
            cache_key=key,
            hook=hook,
            model=model,
            prompt_version=prompt_version,
            document_hash=document_hash,
            response=normalized_output,
            provider=provider,
            schema_version=schema_version,
            # sa.null(): a Python None would be stored as the JSON value ``null``.
            reader_params=dict(reader_params) if reader_params is not None else sa.null(),
        )
        .on_conflict_do_nothing()
    )


def purge_expired(engine: Engine, *, now: datetime, ttl_days: int) -> int:
    pending_consensus = (
        sa.select(CONSENSUS_RUNS.c.id)
        .select_from(
            CONSENSUS_RUNS.join(
                REQUESTS,
                sa.and_(
                    REQUESTS.c.workspace_id == CONSENSUS_RUNS.c.workspace_id,
                    REQUESTS.c.id == CONSENSUS_RUNS.c.ai_request_id,
                ),
            )
        )
        .where(
            CONSENSUS_RUNS.c.workspace_id == CACHE.c.workspace_id,
            CONSENSUS_RUNS.c.status == "queued",
            REQUESTS.c.cache_key == CACHE.c.cache_key,
        )
        .exists()
    )
    with engine.begin() as connection:
        result = connection.execute(
            sa.delete(CACHE).where(
                CACHE.c.created_at <= now - timedelta(days=ttl_days), ~pending_consensus
            )
        )
    return int(result.rowcount or 0)


__all__ = [
    "cache_key",
    "cache_key_v2",
    "lookup",
    "purge_expired",
    "reader_params_digest",
    "request_cache_key",
    "store",
]
