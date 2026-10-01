"""Shared persistence conventions for workspace-scoped application writes."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Literal
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session
from sqlalchemy.sql import Select
from sqlalchemy.sql.schema import Table

from reconbench_app.db.ids import new_id
from reconbench_app.db.metadata import metadata

ScopeKind = Literal["vendor", "po"]
_APPEND_ONLY_TABLES = frozenset(
    {
        "document_version",
        "doc_line",
        "field_evidence",
        "link",
        "config_version",
        "recon_case",
        "recon_line_result",
        "run_manifest",
        "case_action",
        "audit_log",
    }
)


class VersionConflict(Exception):
    """A stale If-Match value, including the current scoped row representation."""

    def __init__(self, current: Mapping[str, Any] | None) -> None:
        super().__init__("resource version does not match If-Match")
        self.current = dict(current) if current is not None else None


class ScopeNotFound(Exception):
    """The workspace-scoped vendor or PO scope does not exist."""


def workspace_filter(query: Select[Any], table: Any, workspace_id: UUID) -> Select[Any]:
    """Add the mandatory workspace predicate to a Core or ORM select."""

    resolved_table: Any = getattr(table, "__table__", table)
    columns = getattr(resolved_table, "c", None)
    if columns is None or "workspace_id" not in columns:
        raise ValueError("workspace-scoped queries require a table with workspace_id")
    return query.where(columns.workspace_id == workspace_id)


def parse_if_match(value: str) -> int:
    """Parse the integer resource-version ETag used by If-Match."""

    candidate = value.strip()
    if candidate.startswith("W/"):
        raise ValueError("weak ETags are not valid for If-Match writes")
    if len(candidate) >= 2 and candidate[0] == candidate[-1] == '"':
        candidate = candidate[1:-1]
    if not candidate.isascii() or not candidate.isdecimal():
        raise ValueError("If-Match must contain a quoted positive version number")
    version = int(candidate)
    if version < 1:
        raise ValueError("If-Match version must be positive")
    return version


def version_etag(version: int) -> str:
    """Format a resource version as its strong HTTP ETag."""

    if version < 1:
        raise ValueError("version must be positive")
    return f'"{version}"'


def update_versioned(
    session: Session,
    table: Table,
    *,
    workspace_id: UUID,
    row_id: UUID,
    if_match: str,
    values: Mapping[str, Any],
) -> dict[str, Any]:
    """Atomically update a versioned row or raise with its current state."""

    if "workspace_id" not in table.c or "version" not in table.c:
        raise ValueError("versioned updates require workspace_id and version columns")
    if "id" not in table.c:
        raise ValueError("versioned updates require an id column")
    if "version" in values or "workspace_id" in values or "id" in values:
        raise ValueError("identity, scope, and version columns are managed by the helper")

    expected_version = parse_if_match(if_match)
    update_statement = (
        sa.update(table)
        .where(
            table.c.workspace_id == workspace_id,
            table.c.id == row_id,
            table.c.version == expected_version,
        )
        .values(**values, version=table.c.version + 1)
        .returning(*table.c)
    )
    current = session.execute(update_statement).mappings().first()
    if current is not None:
        return dict(current)

    current = (
        session.execute(
            sa.select(*table.c).where(table.c.workspace_id == workspace_id, table.c.id == row_id)
        )
        .mappings()
        .first()
    )
    raise VersionConflict(current)


def apply_dirty_scope_change[T](
    session: Session,
    *,
    workspace_id: UUID,
    scope_kind: ScopeKind,
    scope_id: UUID,
    reason: str,
    change: Callable[[Session], T],
) -> T:
    """Commit one change, its scope sequence bump, and a coalesced dirty mark together."""

    if not reason.strip():
        raise ValueError("dirty-scope reason must not be blank")

    # Require a top-level transaction so the change cannot commit independently.
    if session.in_transaction():
        raise RuntimeError("dirty-scope changes require a session with no active transaction")

    with session.begin():
        result = change(session)
        if scope_kind == "vendor":
            scope_table = metadata.tables["vendor"]
            predicate = sa.and_(
                scope_table.c.id == scope_id,
                scope_table.c.workspace_id == workspace_id,
            )
        elif scope_kind == "po":
            scope_table = metadata.tables["document"]
            predicate = sa.and_(
                scope_table.c.id == scope_id,
                scope_table.c.workspace_id == workspace_id,
                scope_table.c.doc_type == "po",
            )
        else:  # pragma: no cover - narrowed by the Literal annotation
            raise ValueError(f"unsupported dirty scope kind: {scope_kind}")

        bumped = session.execute(
            sa.update(scope_table)
            .where(predicate)
            .values(input_seq=scope_table.c.input_seq + 1)
            .returning(scope_table.c.id)
        ).first()
        if bumped is None:
            raise ScopeNotFound(f"{scope_kind} scope is not in workspace {workspace_id}")

        dirty_scope = metadata.tables["dirty_scope"]
        mark = pg_insert(dirty_scope).values(
            id=new_id(),
            workspace_id=workspace_id,
            scope_kind=scope_kind,
            scope_id=scope_id,
            reason=reason,
        )
        session.execute(
            mark.on_conflict_do_update(
                index_elements=[
                    dirty_scope.c.workspace_id,
                    dirty_scope.c.scope_kind,
                    dirty_scope.c.scope_id,
                ],
                index_where=dirty_scope.c.processed_at.is_(None),
                set_={
                    "mark_count": dirty_scope.c.mark_count + 1,
                    "last_marked_at": sa.func.now(),
                    "reason": mark.excluded.reason,
                },
            )
        )
    return result


__all__ = [
    "ScopeNotFound",
    "VersionConflict",
    "apply_dirty_scope_change",
    "parse_if_match",
    "version_etag",
    "workspace_filter",
    "update_versioned",
]
