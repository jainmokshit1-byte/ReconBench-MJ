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
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.engine import Connection, Engine

from reconbench_app.db.ids import new_id
from reconbench_app.db.metadata import metadata

CACHE = metadata.tables["ai_cache"]


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
        )
        .on_conflict_do_nothing()
    )


def purge_expired(engine: Engine, *, now: datetime, ttl_days: int) -> int:
    with engine.begin() as connection:
        result = connection.execute(
            sa.delete(CACHE).where(CACHE.c.created_at <= now - timedelta(days=ttl_days))
        )
    return int(result.rowcount or 0)


__all__ = ["cache_key", "lookup", "purge_expired", "store"]
