"""Checks repeated at enqueue, before sending and before applying an AI result.

Shared by the API (status, enqueue) and the AI worker. Nothing here can open a
sealed key; it only decides whether a stored key is usable in principle.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from reconbench_app.ai.consent import consent_fingerprint, hooks_for_mode
from reconbench_app.db.metadata import metadata

SETTINGS = metadata.tables["ai_setting"]
SECRETS = metadata.tables["secret"]
SESSIONS = metadata.tables["session"]

BLOCK_SETTINGS_CHANGED = "SETTINGS_CHANGED"
BLOCK_AI_OFF = "AI_OFF"
BLOCK_CONSENT_NEEDED = "CONSENT_NEEDED"
BLOCK_CONSENT_OUTDATED = "CONSENT_OUTDATED"
BLOCK_KEY_NOT_SET = "KEY_NOT_SET"
BLOCK_KEY_EXPIRED = "KEY_EXPIRED"
BLOCK_PROVIDER_CHANGED = "PROVIDER_CHANGED"
BLOCK_KEY_UNREADABLE = "KEY_UNREADABLE"


@dataclass(frozen=True, slots=True)
class SettingsSnapshot:
    workspace_id: UUID
    mode: str
    provider: str | None
    model: str | None
    version: int
    consent_given: bool
    consent_fingerprint: str | None
    key_secret_id: UUID | None
    key_storage: str
    vision_enabled: bool = False

    @property
    def hooks(self) -> tuple[str, ...]:
        return hooks_for_mode(self.mode, vision=self.vision_enabled)

    @property
    def consent_current(self) -> bool:
        if not self.consent_given or self.provider is None or self.model is None:
            return False
        return self.consent_fingerprint == consent_fingerprint(
            provider=self.provider, model=self.model, mode=self.mode, vision=self.vision_enabled
        )


@dataclass(frozen=True, slots=True)
class KeyRecord:
    secret_id: UUID
    envelope: bytes
    nonce: bytes
    key_id: str
    key_hint: str | None
    expires_at: datetime | None
    session_id: UUID | None
    created_by: UUID
    created_at: datetime


def load_settings(
    connection: Connection, workspace_id: UUID, *, for_update: bool = False
) -> SettingsSnapshot | None:
    query = sa.select(SETTINGS).where(SETTINGS.c.workspace_id == workspace_id)
    if for_update:
        query = query.with_for_update()
    row = connection.execute(query).mappings().first()
    if row is None:
        return None
    return SettingsSnapshot(
        workspace_id=workspace_id,
        mode=str(row["mode"]),
        provider=row["provider"],
        model=row["model"],
        version=int(row["version"]),
        consent_given=bool(row["consent_given"]),
        consent_fingerprint=row["consent_fingerprint"],
        key_secret_id=row["key_secret_id"],
        key_storage=str(row["key_storage"]),
        vision_enabled=bool(row["vision_enabled"]),
    )


def load_key(connection: Connection, settings: SettingsSnapshot, now: datetime) -> KeyRecord | str:
    """Return the usable key record, or a blocker code. Session keys die with the session."""

    if settings.key_secret_id is None:
        return BLOCK_KEY_NOT_SET
    row = (
        connection.execute(
            sa.select(
                SECRETS,
                SESSIONS.c.revoked_at,
                SESSIONS.c.idle_expires_at,
                SESSIONS.c.abs_expires_at,
            )
            .select_from(SECRETS.outerjoin(SESSIONS, SESSIONS.c.id == SECRETS.c.session_id))
            .where(
                SECRETS.c.id == settings.key_secret_id,
                SECRETS.c.workspace_id == settings.workspace_id,
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        return BLOCK_KEY_NOT_SET
    if row["expires_at"] is not None:
        if row["expires_at"] <= now:
            return BLOCK_KEY_EXPIRED
        if (
            row["revoked_at"] is not None
            or row["idle_expires_at"] is None
            or row["idle_expires_at"] <= now
            or row["abs_expires_at"] <= now
        ):
            return BLOCK_KEY_EXPIRED
    return KeyRecord(
        secret_id=UUID(str(row["id"])),
        envelope=bytes(row["ciphertext"]),
        nonce=bytes(row["nonce"]),
        key_id=str(row["key_id"]),
        key_hint=row["key_hint"],
        expires_at=row["expires_at"],
        session_id=row["session_id"],
        created_by=UUID(str(row["created_by"])),
        created_at=row["created_at"],
    )


def request_blocker(
    connection: Connection, request: Any, now: datetime, *, provider_name: str
) -> str | None:
    """Re-check a ledger row against current settings, consent and key availability."""

    settings = load_settings(connection, request["workspace_id"])
    if settings is None or settings.version != int(request["settings_version"]):
        return BLOCK_SETTINGS_CHANGED
    if settings.provider != request["provider"] or settings.model != request["model"]:
        return BLOCK_PROVIDER_CHANGED
    if settings.provider != provider_name:
        return BLOCK_PROVIDER_CHANGED
    if request["call_kind"] == "hook":
        if settings.mode == "rules_only" or settings.mode != request["mode"]:
            return BLOCK_AI_OFF
        if not settings.consent_given:
            return BLOCK_CONSENT_NEEDED
        if (
            not settings.consent_current
            or settings.consent_fingerprint != request["consent_fingerprint"]
        ):
            return BLOCK_CONSENT_OUTDATED
    key = load_key(connection, settings, now)
    return key if isinstance(key, str) else None


__all__ = [
    "BLOCK_AI_OFF",
    "BLOCK_CONSENT_NEEDED",
    "BLOCK_CONSENT_OUTDATED",
    "BLOCK_KEY_EXPIRED",
    "BLOCK_KEY_NOT_SET",
    "BLOCK_KEY_UNREADABLE",
    "BLOCK_PROVIDER_CHANGED",
    "BLOCK_SETTINGS_CHANGED",
    "KeyRecord",
    "SettingsSnapshot",
    "load_key",
    "load_settings",
    "request_blocker",
]
