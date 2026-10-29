"""Connection state of a provider card (WI-PV2): derived from stored facts, never a label.

A card is **Not connected**, **Connected** or **Error**:

* Connected only after a provider test passed (``connected_at``) and no later test or health
  check failed (``last_error_code`` empty). Any change to the credentials, model or server
  clears both, so the card is Not connected until it is tested again.
* Error carries a generic reason code and the time; never provider text.
* A failed health check turns a Connected card into Error; a passing one turns it back
  (within one health cycle). A health check never makes a card Connected by itself.
* Rotation = test then swap: the candidate key waits in ``pending_key_secret_id`` and the
  old key stays active until the candidate passes a test; a failed test removes the
  candidate and changes nothing else.

This module holds the database transitions used by the AI worker (test results, health) and
the API (derived view), so both agree. It never opens a sealed key.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Literal
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.engine import Connection, Engine

from reconbench_app.db.metadata import metadata

CONFIGS = metadata.tables["ai_provider_config"]
SETTINGS = metadata.tables["ai_setting"]
SECRETS = metadata.tables["secret"]

ROTATE_PREFIX = "provider_test_rotate:"
HEALTH_PREFIX = "provider_health:"
IMAGE_PREFIX = "provider_test_image:"
# A candidate key that no test settled within this time is dropped (worker restart, skip).
STALE_ROTATION_AFTER = timedelta(hours=1)

# Generic reason codes shown to the admin; anything else is reported as ``error``.
REASON_CODES = frozenset(
    {
        "bad_key",
        "PERMISSION_DENIED",
        "PREPAY_DEPLETED",
        "BILLING_OR_PREREQUISITE",
        "MODEL_UNAVAILABLE",
        "NOT_FOUND",
        "QUOTA_EXHAUSTED",
        "INVALID_REQUEST",
        "rate_limited",
        "timeout",
        "PROVIDER_UNAVAILABLE",
        "invalid_output",
        "endpoint_not_allowed",
    }
)
# The server answered and could not do what the test asked: the capability is missing.
# Any other failure (timeout, server down, bad key) leaves the capability unknown.
CAPABILITY_REFUSALS = frozenset({"INVALID_REQUEST", "invalid_output"})

State = Literal["not_connected", "connected", "error"]
Kind = Literal["test", "image", "rotate", "health"]


def reason_code(code: str | None) -> str:
    return code if code in REASON_CODES else "error"


def request_kind(request: Any) -> Kind:
    """What a ``provider_test`` ledger row is for, from its operation key."""

    key = str(request["operation_key"])
    if key.startswith(ROTATE_PREFIX):
        return "rotate"
    if key.startswith(HEALTH_PREFIX):
        return "health"
    if key.startswith(IMAGE_PREFIX):
        return "image"
    return "test"


def capability(ok: bool, error_code: str | None) -> bool | None:
    """What one capability test proved: yes, no, or unknown (it did not get an answer)."""

    if ok:
        return True
    return False if error_code in CAPABILITY_REFUSALS else None


def capabilities(card: Any) -> dict[str, Any]:
    """The public capability block of one card (PD-1b); ``None`` means not checked."""

    return {
        "vision": card["supports_vision"] if card is not None else None,
        "json_schema": card["supports_json_schema"] if card is not None else None,
        "context_window": card["context_window"] if card is not None else None,
    }


def can_read_pages(card: Any) -> bool:
    """A company-server card that AI reading may use: both capabilities proven by a test."""

    return bool(
        card is not None
        and card["supports_vision"] is True
        and card["supports_json_schema"] is True
    )


def is_rotation_test(request: Any) -> bool:
    return request["call_kind"] == "provider_test" and request_kind(request) == "rotate"


def rotation_operation_key(provider: str, pending_secret_id: UUID) -> str:
    return f"{ROTATE_PREFIX}{provider}:{pending_secret_id}"


def derive(card: Any, *, usable: bool) -> dict[str, Any]:
    """The public connection block of one card (no secret, no provider text).

    ``usable``: the card has what a call needs (Gemini: a key; the company server: enabled
    with a model and a URL). A card that is not usable is Not connected whatever it records.
    """

    if card is None or not usable:
        return {
            "state": "not_connected",
            "reason_code": None,
            "checked_at": None,
            "connected_at": None,
            "error_at": None,
            "key_rotated_at": card["key_rotated_at"] if card is not None else None,
            "rotation_pending": bool(card is not None and card["pending_key_secret_id"]),
        }
    state: State
    if card["last_error_code"]:
        state = "error"
    elif card["connected_at"] is not None:
        state = "connected"
    else:
        state = "not_connected"
    checks = [value for value in (card["last_test_at"], card["last_health_at"]) if value]
    return {
        "state": state,
        "reason_code": card["last_error_code"] if state == "error" else None,
        "checked_at": max(checks) if checks else None,
        "connected_at": card["connected_at"],
        "error_at": card["last_error_at"],
        "key_rotated_at": card["key_rotated_at"],
        "rotation_pending": bool(card["pending_key_secret_id"]),
    }


def reset_values() -> dict[str, Any]:
    """Column values that put a card back to Not connected (any credential/server change)."""

    return {
        "connected_at": None,
        "last_error_code": None,
        "last_error_at": None,
        "last_test_at": None,
        "last_test_ok": None,
        "last_health_at": None,
        "last_health_ok": None,
        "supports_vision": None,
        "supports_json_schema": None,
        "context_window": None,
    }


def health_values(now: datetime, *, ok: bool, code: str | None) -> dict[str, Any]:
    """``UPDATE`` values for a health result:
    Connected <-> Error, never Not connected -> Connected.
    """

    connected = CONFIGS.c.connected_at.is_not(None)
    return {
        "last_health_at": now,
        "last_health_ok": ok,
        "last_error_code": sa.case(
            (connected, sa.null() if ok else sa.literal(reason_code(code), sa.Text())),
            else_=CONFIGS.c.last_error_code,
        ),
        "last_error_at": sa.case(
            (connected, sa.null() if ok else sa.literal(now, sa.TIMESTAMP(timezone=True))),
            else_=CONFIGS.c.last_error_at,
        ),
    }


def record_result(
    connection: Connection,
    request: Any,
    now: datetime,
    *,
    ok: bool,
    error_code: str | None = None,
    context_window: int | None = None,
) -> int:
    """Apply a finished ``provider_test`` request to its card; returns rows changed.

    Only for the settings version the request ran under: a stale result changes nothing
    (so a disconnect or any credential change mid-run discards the answer).

    The text test proves the strict JSON schema and records the server's context window;
    the image test sets only ``supports_vision`` and never changes the connection state.
    """

    provider = str(request["provider"])
    card = (
        connection.execute(
            sa.select(CONFIGS)
            .where(
                CONFIGS.c.workspace_id == request["workspace_id"], CONFIGS.c.provider == provider
            )
            .with_for_update()
        )
        .mappings()
        .first()
    )
    if card is None:
        return 0
    current = connection.scalar(
        sa.select(SETTINGS.c.id).where(
            SETTINGS.c.workspace_id == request["workspace_id"],
            SETTINGS.c.version == request["settings_version"],
        )
    )
    if current is None:
        return 0
    kind = request_kind(request)
    if kind == "rotate":
        return _finish_rotation(connection, card, request, now, ok=ok)
    if kind == "health":
        return int(
            connection.execute(
                sa.update(CONFIGS)
                .where(CONFIGS.c.id == card["id"])
                .values(**health_values(now, ok=ok, code=error_code))
            ).rowcount
        )
    if kind == "image":
        return int(
            connection.execute(
                sa.update(CONFIGS)
                .where(CONFIGS.c.id == card["id"])
                .values(supports_vision=capability(ok, error_code))
            ).rowcount
        )
    values: dict[str, Any] = {
        "last_test_at": now,
        "last_test_ok": ok,
        "supports_json_schema": capability(ok, error_code),
    }
    if context_window is not None:
        values["context_window"] = context_window
    if ok:
        values |= {"connected_at": now, "last_error_code": None, "last_error_at": None}
    else:
        values |= {
            "connected_at": None,
            "last_error_code": reason_code(error_code),
            "last_error_at": now,
        }
    return int(
        connection.execute(
            sa.update(CONFIGS).where(CONFIGS.c.id == card["id"]).values(**values)
        ).rowcount
    )


def _finish_rotation(
    connection: Connection, card: Any, request: Any, now: datetime, *, ok: bool
) -> int:
    pending = card["pending_key_secret_id"]
    suffix = str(request["operation_key"]).rsplit(":", 1)[-1]
    if pending is None or str(pending) != suffix:
        return 0  # a newer rotation replaced this candidate, or the card was disconnected
    if not ok:
        connection.execute(
            sa.update(CONFIGS)
            .where(CONFIGS.c.id == card["id"])
            .values(pending_key_secret_id=None, version=CONFIGS.c.version + 1, updated_at=now)
        )
        connection.execute(sa.delete(SECRETS).where(SECRETS.c.id == pending))
        return 1
    old = [card["key_secret_id"]]
    if card["provider"] == "gemini":
        # The Gemini key may still sit on ai_setting for a workspace not yet moved (dual read).
        legacy = connection.scalar(
            sa.select(SETTINGS.c.key_secret_id).where(
                SETTINGS.c.workspace_id == card["workspace_id"]
            )
        )
        if legacy is not None:
            old.append(legacy)
            connection.execute(
                sa.update(SETTINGS)
                .where(SETTINGS.c.workspace_id == card["workspace_id"])
                .values(key_secret_id=None)
            )
    connection.execute(
        sa.update(CONFIGS)
        .where(CONFIGS.c.id == card["id"])
        .values(
            key_secret_id=pending,
            pending_key_secret_id=None,
            key_rotated_at=now,
            connected_at=card["connected_at"] or now,
            last_error_code=None,
            last_error_at=None,
            version=CONFIGS.c.version + 1,
            updated_at=now,
        )
    )
    stale = [item for item in old if item is not None and item != pending]
    if stale:
        connection.execute(sa.delete(SECRETS).where(SECRETS.c.id.in_(stale)))
    return 1


def clear_stale_rotations(engine: Engine, *, now: datetime) -> int:
    """Drop candidate keys that no test settled (worker restart, skipped test)."""

    with engine.begin() as connection:
        stale = list(
            connection.scalars(
                sa.select(CONFIGS.c.pending_key_secret_id)
                .select_from(CONFIGS.join(SECRETS, SECRETS.c.id == CONFIGS.c.pending_key_secret_id))
                .where(SECRETS.c.created_at <= now - STALE_ROTATION_AFTER)
                .with_for_update(of=CONFIGS)
            )
        )
        if not stale:
            return 0
        connection.execute(
            sa.update(CONFIGS)
            .where(CONFIGS.c.pending_key_secret_id.in_(stale))
            .values(pending_key_secret_id=None, version=CONFIGS.c.version + 1, updated_at=now)
        )
        connection.execute(sa.delete(SECRETS).where(SECRETS.c.id.in_(stale)))
        return len(stale)


__all__ = [
    "CAPABILITY_REFUSALS",
    "HEALTH_PREFIX",
    "IMAGE_PREFIX",
    "REASON_CODES",
    "ROTATE_PREFIX",
    "can_read_pages",
    "capabilities",
    "capability",
    "clear_stale_rotations",
    "derive",
    "health_values",
    "is_rotation_test",
    "reason_code",
    "record_result",
    "request_kind",
    "reset_values",
    "rotation_operation_key",
]
