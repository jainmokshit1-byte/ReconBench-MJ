"""Application audit writes that defer sequence and hash-chain fields to PostgreSQL."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, cast
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from reconbench_app.authorization import ActorContext
from reconbench_app.db.ids import new_id
from reconbench_app.db.metadata import metadata
from reconbench_app.json_serialization import to_jsonable

AuditAction = Literal[
    "case.confirm_link",
    "case.change_link",
    "case.approve",
    "case.hold",
    "case.reject",
    "case.note",
    "case.release",
    "link.create",
    "link.confirm",
    "link.reject",
    "link.restore",
    "vendor.assign",
    "vendor.alias_add",
    "vendor.alias_delete",
    "vendor.merge",
    "vendor.suggestion_decide",
    "vendor.create",
    "vendor.rename",
    "vendor.cleanup",
    "file.remove",
    "file.duplicate_skipped",
    "file.page_split.resolve",
    "config.save",
    "ai.settings_save",
    "ai.key_remove",
    "ai.consent_change",
    "ai.test_requested",
    "ai.request_acknowledged",
    "ai.provider_config_save",
    "ai.provider_connect",
    "ai.provider_disconnect",
    "ai.provider_rotate",
    "ai.reader_strategy_change",
    "ai.apply_mode_change",
    "ai.scanned_invoice_reader_change",
    "ai.active_provider_change",
    "ai.manage_denied",
    "document.correct",
    "document.verify_ai_values",
    "document.reading_retry",
    "document.reading_use_rules",
    "document.duplicate_held",
    "document.duplicate_decide",
    "export.create",
    "export.download",
    "retention.purge",
]
AuditTargetType = Literal[
    "case",
    "link",
    "vendor",
    "file",
    "document",
    "config",
    "ai_setting",
    "ai_request",
    "export",
    "user",
]


@dataclass(frozen=True, slots=True)
class AuditLogRecord:
    id: UUID
    sequence: int
    occurred_at: datetime
    previous_hash: str | None
    row_hash: str


def write_audit_event(
    connection: Connection,
    actor: ActorContext,
    *,
    action: AuditAction,
    target_type: AuditTargetType,
    target_id: UUID | None = None,
    before_state: Mapping[str, Any] | None = None,
    after_state: Mapping[str, Any] | None = None,
    reason: str | None = None,
) -> AuditLogRecord:
    """Insert an audit event in the caller's transaction.

    The insert deliberately omits `seq`, `occurred_at`, `prev_hash`, and `row_hash`;
    Phase 2's BEFORE INSERT trigger owns those values and serializes each workspace's
    chain. The caller's database role remains subject to the append-only grants/triggers.
    """

    if not connection.in_transaction():
        raise RuntimeError("audit writes must participate in the caller's transaction")

    audit_log = metadata.tables["audit_log"]
    row_id = new_id()
    connection.execute(
        sa.insert(audit_log).values(
            id=row_id,
            workspace_id=actor.workspace_id,
            actor_id=actor.actor_id,
            actor_role=actor.actor_role,
            action=action,
            target_type=target_type,
            target_id=target_id,
            before_state=to_jsonable(before_state) if before_state is not None else None,
            after_state=to_jsonable(after_state) if after_state is not None else None,
            reason=reason,
        )
    )
    generated = (
        connection.execute(
            sa.select(
                audit_log.c.seq,
                audit_log.c.occurred_at,
                audit_log.c.prev_hash,
                audit_log.c.row_hash,
            ).where(audit_log.c.id == row_id)
        )
        .mappings()
        .one()
    )
    return AuditLogRecord(
        id=row_id,
        sequence=int(generated["seq"]),
        occurred_at=cast(datetime, generated["occurred_at"]),
        previous_hash=cast(str | None, generated["prev_hash"]),
        row_hash=str(generated["row_hash"]),
    )


__all__ = [
    "AuditAction",
    "AuditLogRecord",
    "AuditTargetType",
    "write_audit_event",
]
