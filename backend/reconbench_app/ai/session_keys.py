"""Delete session-only provider keys in the same transaction that ends their session."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from reconbench_app.ai.ledger import clear_provider_config_keys


def delete_session_keys(
    connection: Connection, session_ids: Sequence[UUID], *, now: datetime
) -> int:
    """Remove keys bound to these sessions; saved keys (no session) are untouched."""

    if not session_ids:
        return 0
    secret_ids = list(
        connection.scalars(
            sa.text("SELECT id FROM secret WHERE session_id = ANY(:sessions) FOR UPDATE"),
            {"sessions": list(session_ids)},
        )
    )
    if not secret_ids:
        return 0
    connection.execute(
        sa.text(
            "UPDATE ai_setting SET key_secret_id = NULL, version = version + 1, "
            "last_test_at = NULL, last_test_ok = NULL, updated_at = :now "
            "WHERE key_secret_id = ANY(:secrets)"
        ),
        {"now": now, "secrets": secret_ids},
    )
    clear_provider_config_keys(connection, secret_ids, now=now)
    connection.execute(
        sa.text("DELETE FROM secret WHERE id = ANY(:secrets)"), {"secrets": secret_ids}
    )
    return len(secret_ids)


__all__ = ["delete_session_keys"]
