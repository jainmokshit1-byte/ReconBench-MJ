"""AI settings, consent, key storage, provider tests and status (Phase 7).

Keys are write-only: they are sealed with the AI worker's public key on receipt
and never returned. Every settings change bumps ``version`` and clears the
previous provider-test result. The status label is derived from settings, the
deployment gate, the worker heartbeat and the request ledger, and is never
"Connected": the best state is "Last provider test passed at T" (decision D7).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, Protocol
from uuid import UUID

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from sqlalchemy.engine import Connection, Engine

from reconbench_app.ai import ledger
from reconbench_app.ai.consent import (
    CONSENT_TEXT,
    CONSENT_TEXT_VERSION,
    DATA_SENT,
    HOOK_LABELS,
    HOOKS_BY_MODE,
    MODE_LABELS,
    consent_fingerprint,
    hooks_for_mode,
)
from reconbench_app.ai.contracts import SCHEMA_VERSIONS
from reconbench_app.ai.fixtures import PROVIDER_TEST
from reconbench_app.ai.gate import DeploymentGate
from reconbench_app.ai.key_seal import SealingKeyError, SealingPublicKey, key_hint, seal
from reconbench_app.ai.limits import (
    TOKEN_BOUND_GUARANTEED,
    load_limits,
    load_prices,
    usd_cap_offered,
)
from reconbench_app.ai.redaction import REDACTION_VERSION
from reconbench_app.ai.runtime_checks import (
    BLOCK_KEY_EXPIRED,
    BLOCK_KEY_NOT_SET,
    load_key,
    load_settings,
)
from reconbench_app.audit import write_audit_event
from reconbench_app.authorization import ActorContext, requires
from reconbench_app.db.ids import new_id
from reconbench_app.db.metadata import metadata
from reconbench_app.extraction.vision import DEFAULT_VISION_READER, vision_reader

SETTINGS = metadata.tables["ai_setting"]
SECRETS = metadata.tables["secret"]
SESSIONS = metadata.tables["session"]
AUDIT = metadata.tables["audit_log"]
USERS = metadata.tables["app_user"]
HEARTBEAT = metadata.tables["ai_worker_heartbeat"]
HEARTBEAT_STALE_AFTER = timedelta(seconds=45)
# A workspace without a settings row has the rules-only defaults at version 1.
IMPLICIT_VERSION = 1
TEST_LIMIT = 5
TEST_WINDOW = timedelta(minutes=1)

_MODEL = re.compile(r"gemini-[a-z0-9][a-z0-9.\-]{0,55}\Z")
_KEY = re.compile(r"[A-Za-z0-9_.\-]{20,256}\Z")

PROVIDER_ERROR_LABELS = {
    "bad_key": "the key was rejected",
    "PERMISSION_DENIED": "the key lacks permission for this model",
    "PREPAY_DEPLETED": "prepaid credit is used up",
    "BILLING_OR_PREREQUISITE": "billing or a project prerequisite is not set up",
    "MODEL_UNAVAILABLE": "the model is not available",
    "NOT_FOUND": "the requested resource was not found",
    "QUOTA_EXHAUSTED": "the quota is used up",
    "INVALID_REQUEST": "the provider rejected the request",
    "rate_limited": "rate limited",
    "timeout": "the call timed out",
    "PROVIDER_UNAVAILABLE": "the provider is unavailable",
    "invalid_output": "the answer did not match the required format",
    "ungrounded": "the answer was not supported by the sent text",
    "error": "an unexpected error",
}
ATTENTION_CODES = frozenset(
    {
        "bad_key",
        "PERMISSION_DENIED",
        "PREPAY_DEPLETED",
        "BILLING_OR_PREREQUISITE",
        "MODEL_UNAVAILABLE",
        "NOT_FOUND",
        "QUOTA_EXHAUSTED",
        "INVALID_REQUEST",
    }
)

StatusCode = Literal[
    "OFF",
    "EXTERNAL_CALLS_DISABLED",
    "EXTERNAL_CALLS_SYNTHETIC_ONLY",
    "CONSENT_NEEDED",
    "CONSENT_OUTDATED",
    "KEY_NOT_SET",
    "KEY_REMOVED",
    "KEY_EXPIRED",
    "KEY_UNREADABLE",
    "AI_WORKER_UNAVAILABLE",
    "LIMITS_NOT_CONFIGURED",
    "PROVIDER_ERROR",
    "NOT_TESTED",
    "TEST_PASSED",
]


class AiSettingsError(Exception):
    def __init__(self, status: int, code: str, title: str, detail: str) -> None:
        self.status = status
        self.code = code
        self.title = title
        self.detail = detail
        super().__init__(f"{code}: {detail}")


def _vision_unavailable() -> AiSettingsError:
    return AiSettingsError(
        422,
        "vision_unavailable",
        "Page images are turned off",
        "This server does not read page images (RB_VISION_READER is off).",
    )


class ConsentAcceptance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accept: Literal[True]
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class AiSettingsUpdate(BaseModel):
    """Every field is optional; the key is write-only and never echoed."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["rules_only", "ai_assisted", "ai_first"] | None = None
    provider: Literal["gemini"] | None = None
    model: str | None = Field(default=None, max_length=64)
    api_key: SecretStr | None = Field(default=None)
    key_storage: Literal["session", "saved"] | None = None
    consent: ConsentAcceptance | None = None
    withdraw_consent: bool = False
    auto_accept_ai_links: bool | None = None
    budget_cap_usd: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=4)
    redaction_enabled: bool | None = None
    # Send masked page images to the vision reader instead of PDF text (AC-5).
    vision: bool | None = None

    @field_validator("model")
    @classmethod
    def _model_is_not_a_key(cls, value: str | None) -> str | None:
        if value is not None and not _MODEL.fullmatch(value):
            raise ValueError("model must be a Gemini model id such as gemini-3.8-flash")
        return value

    @field_validator("api_key")
    @classmethod
    def _key_shape(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None:
            secret = value.get_secret_value()
            if not _KEY.fullmatch(secret) or secret.startswith("gemini-"):
                raise ValueError("the API key format is not valid")
        return value


class JobQueue(Protocol):
    def enqueue_ai_request(self, *, ai_request_id: UUID, connection: Connection) -> int: ...


@dataclass(frozen=True, slots=True)
class AiServiceConfig:
    gate: DeploymentGate
    public_key_file: Path | None
    limits_file: Path | None
    price_file: Path | None
    vision_reader: str = DEFAULT_VISION_READER

    @property
    def vision_available(self) -> bool:
        return vision_reader(self.vision_reader) is not None


def _utcnow() -> datetime:
    return datetime.now(UTC)


class AiSettingsService:
    def __init__(
        self,
        engine: Engine,
        config: AiServiceConfig,
        queue: JobQueue,
        *,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self.engine = engine
        self.config = config
        self.queue = queue
        self.clock = clock

    # -- reads ---------------------------------------------------------------------------------

    def _public_key(self) -> SealingPublicKey | None:
        if self.config.public_key_file is None:
            return None
        try:
            return SealingPublicKey.from_file(self.config.public_key_file)
        except SealingKeyError:
            return None

    @requires("data.view")
    def view(self, actor: ActorContext) -> dict[str, Any]:
        now = self.clock()
        with self.engine.connect() as connection:
            return self._view(connection, actor.workspace_id, now)

    def _view(self, connection: Connection, workspace_id: UUID, now: datetime) -> dict[str, Any]:
        row = (
            connection.execute(sa.select(SETTINGS).where(SETTINGS.c.workspace_id == workspace_id))
            .mappings()
            .first()
        )
        mode = str(row["mode"]) if row else "rules_only"
        provider = row["provider"] if row else None
        model = row["model"] if row else None
        vision_on = bool(row and row["vision_enabled"])
        fingerprint = (
            consent_fingerprint(provider=provider, model=model, mode=mode, vision=vision_on)
            if provider and model and mode != "rules_only"
            else None
        )
        key = self._key_view(connection, row, now)
        limits_state = load_limits(self.config.limits_file)
        prices = load_prices(self.config.price_file)
        return {
            "version": int(row["version"]) if row else IMPLICIT_VERSION,
            "mode": mode,
            "mode_label": MODE_LABELS.get(mode, mode),
            "provider": provider,
            "model": model,
            "key": key,
            "consent": {
                "given": bool(row and row["consent_given"]),
                "current": bool(
                    row and row["consent_given"] and fingerprint == row["consent_fingerprint"]
                ),
                "at": row["consent_at"] if row else None,
                "text_version": CONSENT_TEXT_VERSION,
                "required_fingerprint": fingerprint,
                "text": CONSENT_TEXT.format(
                    provider="Google Gemini", model=model or "the selected model"
                ),
                "hooks": [
                    {"hook": hook, "label": HOOK_LABELS[hook], "data_sent": DATA_SENT[hook]}
                    for hook in hooks_for_mode(mode, vision=vision_on)
                ],
            },
            "vision": {
                "enabled": vision_on,
                "available": self.config.vision_available,
                "reader": self.config.vision_reader,
            },
            "auto_accept_ai_links": False,
            "redaction_enabled": True,
            "external_calls": self.config.gate.mode,
            "limits": (
                limits_state.limits.model_dump() if limits_state.limits is not None else None
            ),
            "budget": {
                "usd_cap_offered": usd_cap_offered(model or "", prices),
                "budget_cap_usd": row["budget_cap_usd"] if row else None,
                "token_cap_guaranteed": TOKEN_BOUND_GUARANTEED,
                "priced": bool(prices and model and model in prices.models),
            },
            "key_storage_available": self._public_key() is not None,
            "last_test": {
                "at": row["last_test_at"] if row else None,
                "ok": row["last_test_ok"] if row else None,
            },
            "status": self._status(connection, workspace_id, row, now),
        }

    def _key_view(self, connection: Connection, row: Any, now: datetime) -> dict[str, Any]:
        if row is None or row["key_secret_id"] is None:
            return {"set": False, "hint": None, "storage": None, "expires_at": None}
        secret = (
            connection.execute(
                sa.select(SECRETS.c.key_hint, SECRETS.c.expires_at, SECRETS.c.session_id).where(
                    SECRETS.c.id == row["key_secret_id"]
                )
            )
            .mappings()
            .first()
        )
        if secret is None:
            return {"set": False, "hint": None, "storage": None, "expires_at": None}
        return {
            "set": True,
            "hint": secret["key_hint"],
            "storage": "session" if secret["session_id"] else "saved",
            "expires_at": secret["expires_at"],
        }

    def _status(
        self, connection: Connection, workspace_id: UUID, row: Any, now: datetime
    ) -> dict[str, Any]:
        def status(code: StatusCode, label: str, **extra: Any) -> dict[str, Any]:
            return {"code": code, "label": label, **extra}

        if row is None or row["mode"] == "rules_only":
            return status("OFF", "Off: rules only")
        if self.config.gate.mode == "disabled":
            return status("EXTERNAL_CALLS_DISABLED", "External calls disabled")
        if self.config.gate.mode == "synthetic_only":
            return status(
                "EXTERNAL_CALLS_SYNTHETIC_ONLY", "External calls limited to synthetic fixtures"
            )
        settings = load_settings(connection, workspace_id)
        assert settings is not None
        if not settings.consent_given:
            return status("CONSENT_NEEDED", "Consent needed")
        if not settings.consent_current:
            return status("CONSENT_OUTDATED", "Consent outdated: review and consent again")
        key = load_key(connection, settings, now)
        if key == BLOCK_KEY_NOT_SET:
            event = self._last_key_event(connection, workspace_id)
            if event is not None and event["action"] == "ai.key_remove":
                return status(
                    "KEY_REMOVED",
                    f"Key removed by {event['display_name']}",
                    at=event["occurred_at"],
                    by=event["display_name"],
                )
            if event is not None:
                # A key was saved and later deleted without a removal: its session ended.
                return status(
                    "KEY_EXPIRED", "Key expired: the session ended or reached its time limit"
                )
            return status("KEY_NOT_SET", "Key not set")
        if key == BLOCK_KEY_EXPIRED:
            return status("KEY_EXPIRED", "Key expired: the session ended or reached its time limit")
        assert not isinstance(key, str)
        requests = ledger.REQUESTS
        recent = (
            connection.execute(
                sa.select(
                    requests.c.request_state,
                    requests.c.error_code,
                    requests.c.finished_at,
                    requests.c.call_kind,
                    requests.c.settings_version,
                    requests.c.apply_outcome,
                )
                .where(
                    requests.c.workspace_id == workspace_id,
                    requests.c.finished_at.is_not(None),
                    requests.c.created_at >= key.created_at,
                )
                .order_by(requests.c.finished_at.desc())
                .limit(20)
            )
            .mappings()
            .all()
        )
        if recent and recent[0]["error_code"] == "KEY_UNREADABLE":
            return status("KEY_UNREADABLE", "Key unreadable: re-enter the key")
        if not self._worker_alive(connection, now):
            return status("AI_WORKER_UNAVAILABLE", "AI worker unavailable")
        if load_limits(self.config.limits_file).limits is None:
            return status("LIMITS_NOT_CONFIGURED", "Limits not configured")
        for item in recent:
            if item["request_state"] == "succeeded" and item["error_code"] is None:
                break
            if item["request_state"] == "failed" and item["error_code"] in ATTENTION_CODES:
                label = PROVIDER_ERROR_LABELS.get(item["error_code"], "an error")
                return status(
                    "PROVIDER_ERROR",
                    f"Provider error: {label}",
                    at=item["finished_at"],
                    error_code=item["error_code"],
                )
        # Queried separately: hook traffic can push a passing test out of ``recent``.
        passed_at = connection.scalar(
            sa.select(sa.func.max(requests.c.finished_at)).where(
                requests.c.workspace_id == workspace_id,
                requests.c.call_kind == "provider_test",
                requests.c.settings_version == settings.version,
                requests.c.request_state == "succeeded",
                requests.c.apply_outcome == "applied",
                requests.c.created_at >= key.created_at,
            )
        )
        if passed_at is None:
            return status("NOT_TESTED", "Not tested since last change")
        return status("TEST_PASSED", "Last provider test passed", at=passed_at)

    def _last_key_event(self, connection: Connection, workspace_id: UUID) -> Any:
        """Latest audit event that set or removed a key, with the actor's name."""

        return (
            connection.execute(
                sa.select(AUDIT.c.action, AUDIT.c.occurred_at, USERS.c.display_name)
                .select_from(AUDIT.outerjoin(USERS, USERS.c.id == AUDIT.c.actor_id))
                .where(
                    AUDIT.c.workspace_id == workspace_id,
                    sa.or_(
                        AUDIT.c.action == "ai.key_remove",
                        sa.and_(
                            AUDIT.c.action == "ai.settings_save",
                            AUDIT.c.after_state["key_changed"].as_boolean().is_(True),
                        ),
                    ),
                )
                .order_by(AUDIT.c.seq.desc())
                .limit(1)
            )
            .mappings()
            .first()
        )

    def _worker_alive(self, connection: Connection, now: datetime) -> bool:
        last_seen = connection.scalar(
            sa.select(HEARTBEAT.c.last_seen_at).where(HEARTBEAT.c.worker_role == "ai")
        )
        return last_seen is not None and now - last_seen <= HEARTBEAT_STALE_AFTER

    @requires("ai.manage")
    def consent_preview(
        self, actor: ActorContext, *, mode: str, model: str, vision: bool = False
    ) -> dict[str, Any]:
        """What a person would consent to for unsaved settings; nothing is written.

        The browser cannot compute the fingerprint (it covers server-side prompt, schema
        and redaction versions), so the card fetches it here before the save that turns
        AI on.
        """

        if mode not in HOOKS_BY_MODE:
            raise AiSettingsError(
                422, "mode_not_ai", "Choose an AI mode", "Rules-only mode needs no consent."
            )
        if not _MODEL.fullmatch(model):
            raise AiSettingsError(
                422,
                "invalid_model",
                "Invalid model",
                "The model must be a Gemini model id such as gemini-3.8-flash.",
            )
        if vision and not self.config.vision_available:
            raise _vision_unavailable()
        return {
            "mode": mode,
            "mode_label": MODE_LABELS.get(mode, mode),
            "provider": "gemini",
            "model": model,
            "text_version": CONSENT_TEXT_VERSION,
            "text": CONSENT_TEXT.format(provider="Google Gemini", model=model),
            "fingerprint": consent_fingerprint(
                provider="gemini", model=model, mode=mode, vision=vision
            ),
            "vision": vision,
            "hooks": [
                {"hook": hook, "label": HOOK_LABELS[hook], "data_sent": DATA_SENT[hook]}
                for hook in hooks_for_mode(mode, vision=vision)
            ],
        }

    # -- writes --------------------------------------------------------------------------------

    def _locked_row(self, connection: Connection, actor: ActorContext) -> Any:
        connection.execute(
            sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"reconbench.ai-settings:{actor.workspace_id}"},
        )
        return (
            connection.execute(
                sa.select(SETTINGS)
                .where(SETTINGS.c.workspace_id == actor.workspace_id)
                .with_for_update()
            )
            .mappings()
            .first()
        )

    @staticmethod
    def _check_version(row: Any, expected_version: int) -> None:
        current = int(row["version"]) if row else IMPLICIT_VERSION
        if current != expected_version:
            raise AiSettingsError(
                412,
                "version_conflict",
                "Settings changed",
                "AI settings were changed by someone else. Reload and try again.",
            )

    @requires("ai.manage")
    def update(
        self,
        actor: ActorContext,
        *,
        session_id: UUID,
        expected_version: int,
        body: AiSettingsUpdate,
    ) -> dict[str, Any]:
        now = self.clock()
        if body.auto_accept_ai_links:
            raise AiSettingsError(
                422,
                "auto_accept_not_available",
                "Auto-accept is not available",
                "AI links always need a person to confirm them (decision D11).",
            )
        if body.redaction_enabled is False:
            raise AiSettingsError(
                422,
                "redaction_required",
                "Redaction cannot be turned off",
                "Personal and banking details are always removed before a call.",
            )
        with self.engine.begin() as connection:
            row = self._locked_row(connection, actor)
            self._check_version(row, expected_version)
            before = self._public_state(row)
            mode = body.mode or (str(row["mode"]) if row else "rules_only")
            provider = body.provider or (row["provider"] if row else None)
            model = body.model or (row["model"] if row else None)
            vision_on = (
                body.vision if body.vision is not None else bool(row and row["vision_enabled"])
            )
            if body.vision and not self.config.vision_available:
                raise _vision_unavailable()
            if body.budget_cap_usd is not None and not usd_cap_offered(
                model or "", load_prices(self.config.price_file)
            ):
                raise AiSettingsError(
                    422,
                    "usd_cap_not_available",
                    "A USD cap is not available",
                    "A USD cap needs a priced model and a guaranteed token bound; neither is "
                    "proven for this model, so ReconBench does not offer one.",
                )
            fingerprint = (
                consent_fingerprint(provider=provider, model=model, mode=mode, vision=vision_on)
                if provider and model and mode != "rules_only"
                else None
            )
            consent_values: dict[str, Any] = {}
            consent_changed = False
            if body.withdraw_consent:
                consent_values = {
                    "consent_given": False,
                    "consent_fingerprint": None,
                    "consent_by": None,
                    "consent_at": None,
                }
                consent_changed = bool(row and row["consent_given"])
                if mode != "rules_only":
                    mode = "rules_only"
            elif body.consent is not None:
                if fingerprint is None or body.consent.fingerprint != fingerprint:
                    raise AiSettingsError(
                        409,
                        "consent_mismatch",
                        "Consent is for different settings",
                        "The settings changed since the consent text was shown. Review it again.",
                    )
                consent_values = {
                    "consent_given": True,
                    "consent_fingerprint": fingerprint,
                    "consent_by": actor.actor_id,
                    "consent_at": now,
                    "consent_text_version": CONSENT_TEXT_VERSION,
                }
                consent_changed = True
            elif (
                row is not None
                and row["consent_given"]
                and row["consent_fingerprint"] != fingerprint
            ):
                consent_values = {"consent_given": False}
                consent_changed = True
            consent_given = consent_values.get("consent_given", bool(row and row["consent_given"]))
            if mode != "rules_only" and not consent_given:
                raise AiSettingsError(
                    422,
                    "consent_required",
                    "Consent required",
                    "Review what is sent for each hook and consent before turning AI on.",
                )
            if mode != "rules_only" and (provider is None or model is None):
                raise AiSettingsError(
                    422,
                    "provider_required",
                    "Provider and model required",
                    "Choose Gemini and a model before turning AI on.",
                )

            key_values: dict[str, Any] = {}
            old_secret = row["key_secret_id"] if row else None
            key_changed = False
            if body.api_key is not None:
                public_key = self._public_key()
                if public_key is None:
                    raise AiSettingsError(
                        503,
                        "key_storage_unavailable",
                        "Key storage unavailable",
                        "The AI sealing key is not configured on the server.",
                    )
                storage = body.key_storage or (str(row["key_storage"]) if row else "session")
                secret_id = new_id()
                session_values: dict[str, Any] = {"session_id": None, "expires_at": None}
                if storage == "session":
                    session_values = {
                        "session_id": session_id,
                        "expires_at": connection.scalar(
                            sa.select(SESSIONS.c.abs_expires_at).where(
                                SESSIONS.c.id == session_id,
                                SESSIONS.c.workspace_id == actor.workspace_id,
                                SESSIONS.c.user_id == actor.actor_id,
                            )
                        ),
                    }
                    if session_values["expires_at"] is None:
                        raise AiSettingsError(
                            401,
                            "session_required",
                            "Session required",
                            "Sign in again to store a session key.",
                        )
                plaintext = body.api_key.get_secret_value()
                sealed = seal(
                    plaintext.encode("utf-8"),
                    public_key=public_key,
                    workspace_id=actor.workspace_id,
                    secret_id=secret_id,
                )
                connection.execute(
                    sa.insert(SECRETS).values(
                        id=secret_id,
                        workspace_id=actor.workspace_id,
                        purpose="ai_api_key",
                        ciphertext=sealed.envelope,
                        nonce=sealed.nonce,
                        key_id=sealed.key_id,
                        key_hint=key_hint(plaintext),
                        created_by=actor.actor_id,
                        **session_values,
                    )
                )
                del plaintext
                key_values = {"key_secret_id": secret_id, "key_storage": storage}
                key_changed = True
            elif (
                body.key_storage is not None
                and old_secret is not None
                and (row is None or body.key_storage != row["key_storage"])
            ):
                raise AiSettingsError(
                    422,
                    "key_required",
                    "Re-enter the key",
                    "Changing where the key is kept requires entering the key again.",
                )
            elif body.key_storage is not None:
                key_values = {"key_storage": body.key_storage}

            values: dict[str, Any] = {
                "mode": mode,
                "provider": provider,
                "model": model,
                "vision_enabled": vision_on,
                "updated_by": actor.actor_id,
                "updated_at": now,
                "last_test_at": None,
                "last_test_ok": None,
                **consent_values,
                **key_values,
            }
            if body.budget_cap_usd is not None:
                values["budget_cap_usd"] = body.budget_cap_usd
            if row is None:
                connection.execute(
                    sa.insert(SETTINGS).values(
                        id=new_id(),
                        workspace_id=actor.workspace_id,
                        version=IMPLICIT_VERSION + 1,
                        **values,
                    )
                )
            else:
                connection.execute(
                    sa.update(SETTINGS)
                    .where(SETTINGS.c.id == row["id"])
                    .values(version=SETTINGS.c.version + 1, **values)
                )
            if key_changed and old_secret is not None:
                connection.execute(sa.delete(SECRETS).where(SECRETS.c.id == old_secret))
            after_row = self._locked_row(connection, actor)
            write_audit_event(
                connection,
                actor,
                action="ai.settings_save",
                target_type="ai_setting",
                target_id=after_row["id"],
                before_state=before,
                after_state={**(self._public_state(after_row) or {}), "key_changed": key_changed},
            )
            if consent_changed:
                write_audit_event(
                    connection,
                    actor,
                    action="ai.consent_change",
                    target_type="ai_setting",
                    target_id=after_row["id"],
                    before_state={"consent_given": bool(row and row["consent_given"])},
                    after_state={
                        "consent_given": bool(after_row["consent_given"]),
                        "consent_fingerprint": after_row["consent_fingerprint"],
                        "consent_text_version": after_row["consent_text_version"],
                    },
                )
            return self._view(connection, actor.workspace_id, now)

    @staticmethod
    def _public_state(row: Any) -> dict[str, Any] | None:
        if row is None:
            return None
        return {
            "version": int(row["version"]),
            "mode": row["mode"],
            "provider": row["provider"],
            "model": row["model"],
            "key_set": row["key_secret_id"] is not None,
            "key_storage": row["key_storage"],
            "consent_given": bool(row["consent_given"]),
            "vision_enabled": bool(row["vision_enabled"]),
        }

    @requires("ai.manage")
    def remove_key(self, actor: ActorContext, *, expected_version: int) -> dict[str, Any]:
        now = self.clock()
        with self.engine.begin() as connection:
            row = self._locked_row(connection, actor)
            self._check_version(row, expected_version)
            if row is None or row["key_secret_id"] is None:
                raise AiSettingsError(404, "key_not_set", "No key", "No AI key is stored.")
            secret_id = row["key_secret_id"]
            connection.execute(
                sa.update(SETTINGS)
                .where(SETTINGS.c.id == row["id"])
                .values(
                    key_secret_id=None,
                    version=SETTINGS.c.version + 1,
                    updated_by=actor.actor_id,
                    updated_at=now,
                    last_test_at=None,
                    last_test_ok=None,
                )
            )
            connection.execute(sa.delete(SECRETS).where(SECRETS.c.id == secret_id))
            write_audit_event(
                connection,
                actor,
                action="ai.key_remove",
                target_type="ai_setting",
                target_id=row["id"],
                before_state={"key_set": True},
                after_state={"key_set": False},
            )
            return self._view(connection, actor.workspace_id, now)

    @requires("ai.manage")
    def request_provider_test(self, actor: ActorContext, *, ip: str | None) -> dict[str, Any]:
        now = self.clock()
        events = metadata.tables["rate_limit_event"]
        with self.engine.begin() as connection:
            connection.execute(
                sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": f"reconbench.ai-test:{actor.workspace_id}:{actor.actor_id}"},
            )
            recent = connection.scalar(
                sa.select(sa.func.count())
                .select_from(events)
                .where(
                    events.c.workspace_id == actor.workspace_id,
                    events.c.user_id == actor.actor_id,
                    events.c.kind == "ai_test",
                    events.c.occurred_at >= now - TEST_WINDOW,
                )
            )
            if int(recent or 0) >= TEST_LIMIT:
                raise AiSettingsError(
                    429,
                    "ai_test_rate_limited",
                    "Provider test rate limited",
                    "Wait a minute before testing again.",
                )
            settings = load_settings(connection, actor.workspace_id)
            if settings is None or settings.provider is None or settings.model is None:
                raise AiSettingsError(
                    422,
                    "provider_required",
                    "Provider and model required",
                    "Choose Gemini and a model first.",
                )
            if self.config.gate.mode == "disabled":
                raise AiSettingsError(
                    409,
                    "external_calls_disabled",
                    "External calls disabled",
                    "This deployment does not allow provider calls (RB_AI_EXTERNAL_CALLS).",
                )
            key = load_key(connection, settings, now)
            if isinstance(key, str):
                raise AiSettingsError(
                    409, key.lower(), "Key unavailable", "Enter a key before testing."
                )
            connection.execute(
                sa.insert(events).values(
                    id=new_id(),
                    workspace_id=actor.workspace_id,
                    kind="ai_test",
                    user_id=actor.actor_id,
                    ip=ip,
                    succeeded=True,
                    occurred_at=now,
                )
            )
            request_id = ledger.create_request(
                connection,
                ledger.NewRequest(
                    workspace_id=actor.workspace_id,
                    call_kind="provider_test",
                    hook=None,
                    mode=settings.mode,
                    subject_type="fixture",
                    subject_id=None,
                    subject_version=str(new_id()),
                    operation_key=f"provider_test:{now.date().isoformat()}",
                    settings_version=settings.version,
                    consent_fingerprint=None,
                    provider=settings.provider,
                    model=settings.model,
                    prompt_version=PROVIDER_TEST.fixture_id,
                    schema_version=SCHEMA_VERSIONS["provider_test"],
                    redaction_version=REDACTION_VERSION,
                    requested_by=actor.actor_id,
                ),
            )
            assert request_id is not None
            job_id = self.queue.enqueue_ai_request(ai_request_id=request_id, connection=connection)
            connection.execute(
                sa.update(ledger.REQUESTS)
                .where(ledger.REQUESTS.c.id == request_id)
                .values(job_id=job_id)
            )
            write_audit_event(
                connection,
                actor,
                action="ai.test_requested",
                target_type="ai_request",
                target_id=request_id,
                after_state={"settings_version": settings.version, "model": settings.model},
            )
        return {"request_id": request_id, "state": "queued"}

    @requires("ai.manage")
    def request_state(self, actor: ActorContext, request_id: UUID) -> dict[str, Any]:
        requests = ledger.REQUESTS
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    sa.select(
                        requests.c.id,
                        requests.c.call_kind,
                        requests.c.hook,
                        requests.c.request_state,
                        requests.c.apply_outcome,
                        requests.c.skip_code,
                        requests.c.error_code,
                        requests.c.created_at,
                        requests.c.finished_at,
                        requests.c.acknowledged_at,
                    ).where(
                        requests.c.id == request_id,
                        requests.c.workspace_id == actor.workspace_id,
                    )
                )
                .mappings()
                .first()
            )
        if row is None:
            raise AiSettingsError(404, "not_found", "Not found", "The AI request was not found.")
        return dict(row)

    @requires("ai.manage")
    def usage(self, actor: ActorContext) -> dict[str, Any]:
        now = self.clock()
        limits_state = load_limits(self.config.limits_file)
        prices = load_prices(self.config.price_file)
        with self.engine.connect() as connection:
            settings = load_settings(connection, actor.workspace_id)
            summary = ledger.usage_summary(connection, actor.workspace_id, now)
        model = settings.model if settings else None
        priced = bool(prices and model and model in prices.models)
        return {
            **summary,
            "usd_today": summary["usd_today"] if priced else None,
            "usd_available": priced,
            "usd_notice": (
                "Estimated from the configured price file."
                if priced
                else "Cost is not shown: no price is configured for this model."
            ),
            "token_notice": (
                "Token counts for calls without reported usage are estimates (no local tokenizer)."
            ),
            "limits": limits_state.limits.model_dump() if limits_state.limits else None,
        }

    @requires("ai.manage")
    def acknowledge(self, actor: ActorContext, request_id: UUID) -> dict[str, Any]:
        now = self.clock()
        with self.engine.begin() as connection:
            if not ledger.acknowledge(
                connection,
                workspace_id=actor.workspace_id,
                request_id=request_id,
                user_id=actor.actor_id,
                now=now,
            ):
                raise AiSettingsError(
                    409,
                    "not_acknowledgeable",
                    "Nothing to acknowledge",
                    "Only an unacknowledged uncertain request can be acknowledged.",
                )
            write_audit_event(
                connection,
                actor,
                action="ai.request_acknowledged",
                target_type="ai_request",
                target_id=request_id,
                after_state={"acknowledged_at": now},
                reason="Accepted an uncertain, possibly billed AI call; one new attempt allowed.",
            )
        return {"request_id": request_id, "acknowledged_at": now}


__all__ = [
    "PROVIDER_ERROR_LABELS",
    "AiServiceConfig",
    "AiSettingsError",
    "AiSettingsService",
    "AiSettingsUpdate",
    "ConsentAcceptance",
]
