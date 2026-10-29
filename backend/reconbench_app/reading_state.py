"""Waiting for reading (accuracy plan PD-2f, D103 Q3): which documents may be used.

A scanned invoice read with the switch on AI waits until its AI version is current
(``waiting``). When the AI reading fails it shows the stated reason (``failed``) until a
person picks Retry or "Read with rules". Mapping, vendor resolution and reconciliation
use ``ready`` documents only, so a run never blocks on a reading and nothing read by
rules counts for such an invoice before a person chooses it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Final
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from reconbench_app.db.metadata import metadata

READY: Final = "ready"
WAITING: Final = "waiting"
FAILED: Final = "failed"

# Stated reasons, shown in plain words on the document page.
NOT_QUEUED: Final = "AI_READING_NOT_QUEUED"
"""The reading could not be queued (call cap for one upload, or a queue error)."""
NOT_ELIGIBLE: Final = "AI_READING_NOT_ELIGIBLE"
"""The AI reader can't take this file (too many pages or too large)."""
PAGES_FAILED: Final = "AI_READING_PAGES_FAILED"
"""The page images for the reader could not be prepared."""
ANSWER_INVALID: Final = "AI_READING_ANSWER_INVALID"
"""The provider's answer did not pass the checks."""
MERGE_FAILED: Final = "AI_READING_MERGE_FAILED"
"""The checked answer could not be turned into a version."""

_DOCUMENTS = metadata.tables["document"]


def ready_clause(document: sa.Table = _DOCUMENTS) -> sa.ColumnElement[bool]:
    """The filter every mapping, vendor and reconciliation load applies."""

    return document.c.reading_state == READY


def _set(
    connection: Connection,
    workspace_id: UUID,
    document_id: UUID,
    *,
    state: str,
    failure_code: str | None,
    only_from: tuple[str, ...],
    bump_version: bool = False,
) -> bool:
    # ``only_from`` is the guard against a race; a worker's change never makes a
    # person's open screen stale, a person's action does (``bump_version``).
    values: dict[str, object] = {
        "reading_state": state,
        "reading_failure_code": failure_code,
        "updated_at": sa.func.now(),
    }
    if bump_version:
        values["version"] = _DOCUMENTS.c.version + 1
    result = connection.execute(
        sa.update(_DOCUMENTS)
        .where(
            _DOCUMENTS.c.workspace_id == workspace_id,
            _DOCUMENTS.c.id == document_id,
            _DOCUMENTS.c.reading_state.in_(only_from),
        )
        .values(**values)
    )
    return bool(result.rowcount)


def mark_waiting(
    connection: Connection, workspace_id: UUID, document_id: UUID, *, retry: bool = False
) -> bool:
    """A new reading starts: from a fresh parse (any state) or a person's Retry (``failed``)."""

    return _set(
        connection,
        workspace_id,
        document_id,
        state=WAITING,
        failure_code=None,
        only_from=(FAILED,) if retry else (READY, WAITING, FAILED),
        bump_version=retry,
    )


def mark_failed(
    connection: Connection, workspace_id: UUID, document_id: UUID, failure_code: str
) -> bool:
    """The AI reading of a waiting document failed; a person picks what happens next."""

    return _set(
        connection,
        workspace_id,
        document_id,
        state=FAILED,
        failure_code=failure_code[:80],
        only_from=(WAITING,),
    )


def mark_ready(
    connection: Connection,
    workspace_id: UUID,
    document_id: UUID,
    *,
    by_person: bool = False,
    new_version: bool = False,
) -> bool:
    """The AI version is current (from ``waiting``), a person chose "Read with rules", or
    a fresh version read by rules only replaced the one that waited (``new_version``)."""

    return _set(
        connection,
        workspace_id,
        document_id,
        state=READY,
        failure_code=None,
        only_from=(WAITING, FAILED) if by_person or new_version else (WAITING,),
        bump_version=by_person,
    )


def mark_version_failed(
    connection: Connection, workspace_id: UUID, document_version_id: UUID, failure_code: str
) -> bool:
    """``mark_failed`` for the document whose *current* version is this one.

    A reading of an older version never changes the state of the document.
    """

    document_id = connection.scalar(
        sa.select(_DOCUMENTS.c.id).where(
            _DOCUMENTS.c.workspace_id == workspace_id,
            _DOCUMENTS.c.current_version_id == document_version_id,
            _DOCUMENTS.c.withdrawn_at.is_(None),
        )
    )
    if document_id is None:
        return False
    return mark_failed(connection, workspace_id, document_id, failure_code)


_FAIL_FINISHED = """UPDATE document AS d
SET reading_state = 'failed',
    reading_failure_code = left(
      CASE
        WHEN r.request_state = 'succeeded' THEN 'AI_READING_ANSWER_INVALID'
        ELSE coalesce(r.skip_code, r.error_code, 'AI_READING_FAILED')
      END,
      80
    ),
    updated_at = now()
FROM ai_request AS r
WHERE d.workspace_id = r.workspace_id
  AND d.current_version_id = r.subject_id
  AND d.withdrawn_at IS NULL
  AND d.reading_state = 'waiting'
  AND r.hook = 'h6_vision'
  AND NOT r.shadow
  AND (
    r.request_state IN ('skipped', 'released', 'failed', 'uncertain')
    -- An answer that did not pass the checks, or arrived after consent was revoked.
    OR (
      r.request_state = 'succeeded'
      AND r.apply_outcome IN ('discarded_invalid', 'discarded_revoked', 'not_applicable')
    )
  )
  AND NOT EXISTS (
    SELECT 1 FROM ai_request AS n
    WHERE n.workspace_id = r.workspace_id
      AND n.subject_id = r.subject_id
      AND n.hook = 'h6_vision'
      AND NOT n.shadow
      AND (n.created_at, n.id) > (r.created_at, r.id)
  )"""


def fail_finished_readings(connection: Connection, *, request_id: UUID | None = None) -> int:
    """Waiting documents whose latest AI reading ended without an answer become ``failed``.

    Called by every writer of a final request state (skip, release, finish, the lease
    sweep), in its transaction; the provider's own retries are over by then. An older
    request never fails a document a person retried, and a stale one (the version is no
    longer current) never touches it. Idempotent.
    """

    statement = _FAIL_FINISHED + (" AND r.id = :request_id" if request_id is not None else "")
    params = {"request_id": request_id} if request_id is not None else {}
    return int(connection.execute(sa.text(statement), params).rowcount or 0)


STALLED: Final = "AI_READING_STALLED"
"""Nothing is reading the document any more (a job gave up); a person picks next."""

_FAIL_STALLED = """UPDATE document AS d
SET reading_state = 'failed', reading_failure_code = :code, updated_at = now()
WHERE d.reading_state = 'waiting'
  AND d.withdrawn_at IS NULL
  AND d.updated_at < :cutoff
  AND NOT EXISTS (
    SELECT 1 FROM ai_request AS r
    WHERE r.workspace_id = d.workspace_id
      AND r.subject_id = d.current_version_id
      AND r.hook = 'h6_vision'
      AND NOT r.shadow
      AND r.request_state IN ('queued', 'reserved', 'sending')
  )
  AND NOT EXISTS (
    SELECT 1 FROM consensus_run AS c
    WHERE c.workspace_id = d.workspace_id
      AND c.document_version_id = d.current_version_id
      AND c.status = 'queued'
      AND c.created_at >= :cutoff
  )"""


def fail_stalled_readings(connection: Connection, *, cutoff: datetime) -> int:
    """Safety net for the AI sweep: a document still waiting since before ``cutoff`` with
    no live request and no recent merge run fails as ``AI_READING_STALLED``."""

    return int(
        connection.execute(sa.text(_FAIL_STALLED), {"code": STALLED, "cutoff": cutoff}).rowcount
        or 0
    )


__all__ = [
    "ANSWER_INVALID",
    "FAILED",
    "MERGE_FAILED",
    "NOT_ELIGIBLE",
    "NOT_QUEUED",
    "PAGES_FAILED",
    "READY",
    "STALLED",
    "WAITING",
    "fail_finished_readings",
    "fail_stalled_readings",
    "mark_failed",
    "mark_ready",
    "mark_version_failed",
    "mark_waiting",
    "ready_clause",
]
