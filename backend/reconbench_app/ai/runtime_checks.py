"""Checks repeated at enqueue, before sending and before applying an AI result.

Shared by the API (status, enqueue) and the AI worker. Nothing here can open a
sealed key; it only decides whether a stored key is usable in principle.

Providers are separate (LLM plan §0.6): text hooks use the Gemini settings on
``ai_setting``; the vision hook uses the workspace's one vision provider
(``primary_reader``), which may be the company vLLM server (``ai_provider_config``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from reconbench_app.ai.connection_state import ROTATE_PREFIX
from reconbench_app.ai.consent import consent_fingerprint, hooks_for_mode
from reconbench_app.db.metadata import metadata

SETTINGS = metadata.tables["ai_setting"]
PROVIDER_CONFIGS = metadata.tables["ai_provider_config"]
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
BLOCK_PROVIDER_DISABLED = "PROVIDER_DISABLED"
VISION_HOOK = "h6_vision"


@dataclass(frozen=True, slots=True)
class ProviderConfig:
    """A workspace's ``ai_provider_config`` row (only vLLM is read at run time)."""

    provider: str
    enabled: bool
    model: str | None
    base_url: str | None
    key_secret_id: UUID | None
    version: int
    pending_key_secret_id: UUID | None = None
    key_tier: str = "free"


def fingerprint_for(
    *,
    provider: str,
    model: str,
    mode: str,
    reader_strategy: str,
    primary_reader: str | None,
    apply_mode: str = "suggest",
    auto_verify_field_types: tuple[str, ...] = (),
    vllm_model: str | None = None,
    vllm_base_url: str | None = None,
) -> str:
    """The consent fingerprint of saved or proposed settings (legacy-identical mapping)."""

    local = primary_reader == "vllm"
    return consent_fingerprint(
        provider=provider,
        model=model,
        mode=mode,
        vision=reader_strategy == "single",
        auto_verify_field_types=auto_verify_field_types,
        reader_strategy=reader_strategy,
        vision_provider=primary_reader,
        vision_model=vllm_model if local else model,
        vision_base_url=vllm_base_url if local else None,
        apply_mode=apply_mode,
    )


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
    auto_verify_field_types: tuple[str, ...] = ()
    reader_strategy: str = "off"
    primary_reader: str | None = None
    apply_mode: str = "suggest"
    vllm: ProviderConfig | None = None
    # The Gemini card; its key (when set) is the Gemini key, else ``ai_setting.key_secret_id``.
    gemini: ProviderConfig | None = None

    @property
    def strategy(self) -> str:
        """``reader_strategy``; a row written only with the old ``vision_enabled`` is ``single``."""

        if self.reader_strategy == "off" and self.vision_enabled:
            return "single"
        return self.reader_strategy

    @property
    def active_provider(self) -> str | None:
        """The one active AI provider.

        rules_only/shadow -> None, single -> primary_reader, off -> provider.
        """

        if self.mode == "rules_only":
            return None
        if self.strategy == "shadow":
            return None
        if self.strategy == "single":
            return self.primary_reader or self.provider or "gemini"
        return self.provider

    @property
    def active_model(self) -> str | None:
        """The model of the active provider (vllm -> vllm card model, gemini -> model)."""

        if self.active_provider == "vllm":
            return self.vllm.model if self.vllm else None
        if self.active_provider == "gemini":
            return self.model
        return None

    @property
    def vision_provider(self) -> str | None:
        return self.active_provider

    @property
    def vision_target(self) -> tuple[str | None, str | None]:
        return self.target(VISION_HOOK)

    def target(self, hook: str | None = None) -> tuple[str | None, str | None]:
        return (self.active_provider, self.active_model)

    @property
    def hooks(self) -> tuple[str, ...]:
        # Shadow never replaces H1: its answers go to ``reader_comparison`` only (WI-BE6).
        return hooks_for_mode(self.mode, vision=self.strategy == "single")

    @property
    def expected_fingerprint(self) -> str | None:
        if self.active_provider is None or self.active_model is None:
            return None
        return fingerprint_for(
            provider=self.provider or "gemini",
            model=self.model or "",
            mode=self.mode,
            reader_strategy=self.strategy,
            primary_reader=self.primary_reader,
            apply_mode=self.apply_mode,
            auto_verify_field_types=self.auto_verify_field_types,
            vllm_model=self.vllm.model if self.vllm else None,
            vllm_base_url=self.vllm.base_url if self.vllm else None,
        )

    @property
    def consent_current(self) -> bool:
        if not self.consent_given:
            return False
        expected = self.expected_fingerprint
        return expected is not None and self.consent_fingerprint == expected


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


def load_provider_config(
    connection: Connection, workspace_id: UUID, provider: str
) -> ProviderConfig | None:
    row = (
        connection.execute(
            sa.select(PROVIDER_CONFIGS).where(
                PROVIDER_CONFIGS.c.workspace_id == workspace_id,
                PROVIDER_CONFIGS.c.provider == provider,
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        return None
    return ProviderConfig(
        provider=provider,
        enabled=bool(row["enabled"]),
        model=row["model"],
        base_url=row["base_url"],
        key_secret_id=row["key_secret_id"],
        version=int(row["version"]),
        pending_key_secret_id=row["pending_key_secret_id"],
        key_tier=str(row["key_tier"]),
    )


def effective_gemini_secret(legacy: UUID | None, card: ProviderConfig | None) -> UUID | None:
    """The Gemini key reference: the card's, else the legacy ``ai_setting`` pointer.

    WI-PV2 moves the key to the card; the old location stays readable for one release
    (accuracy plan WI-PV1), so a workspace not yet moved keeps working.
    """

    if card is not None and card.key_secret_id is not None:
        return card.key_secret_id
    return legacy


def load_settings(
    connection: Connection, workspace_id: UUID, *, for_update: bool = False
) -> SettingsSnapshot | None:
    query = sa.select(SETTINGS).where(SETTINGS.c.workspace_id == workspace_id)
    if for_update:
        query = query.with_for_update()
    row = connection.execute(query).mappings().first()
    if row is None:
        return None
    gemini = load_provider_config(connection, workspace_id, "gemini")
    return SettingsSnapshot(
        workspace_id=workspace_id,
        mode=str(row["mode"]),
        provider=row["provider"],
        model=row["model"],
        version=int(row["version"]),
        consent_given=bool(row["consent_given"]),
        consent_fingerprint=row["consent_fingerprint"],
        key_secret_id=effective_gemini_secret(row["key_secret_id"], gemini),
        key_storage=str(row["key_storage"]),
        vision_enabled=bool(row["vision_enabled"]),
        auto_verify_field_types=tuple(row["auto_verify_field_types"] or ()),
        reader_strategy=str(row["reader_strategy"]),
        primary_reader=row["primary_reader"],
        apply_mode=str(row["apply_mode"]),
        vllm=load_provider_config(connection, workspace_id, "vllm"),
        gemini=gemini,
    )


def load_key(connection: Connection, settings: SettingsSnapshot, now: datetime) -> KeyRecord | str:
    """Return the usable Gemini key record, or a blocker code. Session keys die with the session."""

    return load_secret(connection, settings.workspace_id, settings.key_secret_id, now)


def load_provider_key(
    connection: Connection,
    settings: SettingsSnapshot,
    provider: str,
    now: datetime,
    *,
    pending: bool = False,
) -> KeyRecord | str | None:
    """The key a call to ``provider`` uses: Gemini's is required; vLLM's is optional.

    ``None`` means the provider is called without a key (the company vLLM server
    needs none, plan §0.5). A vLLM key that was stored and has expired still blocks.
    ``pending=True`` is a rotation test: it uses the candidate key, never the live one.
    """

    if pending:
        card = settings.vllm if provider == "vllm" else settings.gemini
        if card is None or card.pending_key_secret_id is None:
            return BLOCK_KEY_NOT_SET
        return load_secret(connection, settings.workspace_id, card.pending_key_secret_id, now)
    if provider != "vllm":
        return load_key(connection, settings, now)
    config = settings.vllm
    if config is None or not config.enabled or not config.model or not config.base_url:
        return BLOCK_PROVIDER_DISABLED
    if config.key_secret_id is None:
        return None
    return load_secret(connection, settings.workspace_id, config.key_secret_id, now)


def load_secret(
    connection: Connection, workspace_id: UUID, secret_id: UUID | None, now: datetime
) -> KeyRecord | str:
    if secret_id is None:
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
                SECRETS.c.id == secret_id,
                SECRETS.c.workspace_id == workspace_id,
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
    if request["call_kind"] == "provider_test":
        if request["provider"] == "vllm":
            target: tuple[str | None, str | None] = (
                "vllm",
                settings.vllm.model if settings.vllm else None,
            )
        elif request["provider"] == "gemini":
            gemini_model = (
                settings.gemini.model
                if settings.gemini and settings.gemini.model
                else settings.model
            )
            target = ("gemini", gemini_model)
        else:
            target = (None, None)
    else:
        target = settings.target(request["hook"])
    if target != (request["provider"], request["model"]) or target[0] != provider_name:
        return BLOCK_PROVIDER_CHANGED
    if request["call_kind"] == "hook":
        card = settings.vllm if provider_name == "vllm" else settings.gemini
        if card is not None and not card.enabled:
            return BLOCK_PROVIDER_DISABLED
        if settings.mode == "rules_only" or settings.mode != request["mode"]:
            return BLOCK_AI_OFF
        if request["hook"] == VISION_HOOK and settings.strategy != (
            "shadow" if request.get("shadow") else "single"
        ):
            # A shadow request never runs as the workspace's reader, nor the reverse.
            return BLOCK_AI_OFF
        if not settings.consent_given:
            return BLOCK_CONSENT_NEEDED
        if (
            not settings.consent_current
            or settings.consent_fingerprint != request["consent_fingerprint"]
        ):
            return BLOCK_CONSENT_OUTDATED
    key = load_provider_key(
        connection,
        settings,
        provider_name,
        now,
        pending=request["call_kind"] == "provider_test"
        and str(request["operation_key"]).startswith(ROTATE_PREFIX),
    )
    return key if isinstance(key, str) else None


__all__ = [
    "BLOCK_AI_OFF",
    "BLOCK_CONSENT_NEEDED",
    "BLOCK_CONSENT_OUTDATED",
    "BLOCK_KEY_EXPIRED",
    "BLOCK_KEY_NOT_SET",
    "BLOCK_KEY_UNREADABLE",
    "BLOCK_PROVIDER_CHANGED",
    "BLOCK_PROVIDER_DISABLED",
    "BLOCK_SETTINGS_CHANGED",
    "KeyRecord",
    "ProviderConfig",
    "effective_gemini_secret",
    "SettingsSnapshot",
    "fingerprint_for",
    "load_key",
    "load_provider_config",
    "load_provider_key",
    "load_secret",
    "load_settings",
    "request_blocker",
]
