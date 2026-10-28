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
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, Protocol
from uuid import UUID

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from sqlalchemy.engine import Connection, Engine

from reconbench_app.ai import connection_state, ledger, shadow
from reconbench_app.ai.consent import (
    AUTO_VERIFY_FIELD_LABELS,
    AUTO_VERIFY_FIELD_TYPES,
    DATA_SENT,
    HOOK_LABELS,
    HOOKS_BY_MODE,
    MODE_LABELS,
    consent_text,
    consent_text_version,
    data_destination,
    hooks_for_mode,
)
from reconbench_app.ai.contracts import SCHEMA_VERSIONS
from reconbench_app.ai.endpoint_policy import EndpointNotAllowed, check_allowed
from reconbench_app.ai.fixtures import PROVIDER_TEST
from reconbench_app.ai.gate import DeploymentGate
from reconbench_app.ai.key_seal import SealingKeyError, SealingPublicKey, key_hint, seal
from reconbench_app.ai.limits import (
    TOKEN_BOUND_GUARANTEED,
    load_limits,
    load_prices,
    usd_cap_offered,
)
from reconbench_app.ai.provider_notice import notice_fingerprint
from reconbench_app.ai.redaction import REDACTION_VERSION
from reconbench_app.ai.runtime_checks import (
    BLOCK_KEY_EXPIRED,
    BLOCK_KEY_NOT_SET,
    BLOCK_PROVIDER_DISABLED,
    active_provider_for,
    fingerprint_for,
    load_provider_key,
    load_settings,
)
from reconbench_app.audit import write_audit_event
from reconbench_app.authorization import (
    ROLE_CAPABILITIES,
    ActorContext,
    requires,
    stored_role_grants,
)
from reconbench_app.db.ids import new_id
from reconbench_app.db.metadata import metadata
from reconbench_app.extraction.vision import (
    DEFAULT_VISION_READER,
    reader_for_provider,
    vision_reader,
)

SETTINGS = metadata.tables["ai_setting"]
CONFIGS = metadata.tables["ai_provider_config"]
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
# Served model id on a self-hosted server, e.g. ``qwen3.5:9b`` (plan WI-BE3).
_LOCAL_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}\Z")
_LOCAL_KEY = re.compile(r"[A-Za-z0-9_.~+/=\-]{8,512}\Z")
PROVIDERS = ("gemini", "vllm")
# Per-provider limit overrides a manager may store (applied by later work items).
_PROVIDER_LIMIT_RANGES = {"max_concurrency": (1, 4), "timeout_seconds": (1, 300)}
AutoVerifyFieldType = Literal["doc_date", "doc_no", "gstin", "line_amount", "reference"]

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


def require_ai_manage(engine: Engine, actor: ActorContext, *, attempted: str) -> None:
    """Refuse an AI-settings or provider write unless the stored role grants ``ai.manage``.

    Decision O-18 (WI-PV3): the role is read from ``app_user`` on every write, and an
    inactive user holds no capability. Every other capability keeps the P0 policy. A
    refusal is audited as ``ai.manage_denied`` in its own transaction, so the row
    survives the 403; it names what was attempted, never a value from the request.
    """

    with engine.begin() as connection:
        user = connection.execute(
            sa.select(USERS.c.role, USERS.c.is_active).where(
                USERS.c.workspace_id == actor.workspace_id, USERS.c.id == actor.actor_id
            )
        ).one_or_none()
        stored_role = str(user.role) if user is not None else None
        if user is not None and user.is_active and stored_role_grants(stored_role, "ai.manage"):
            return
        denied_actor = ActorContext(
            workspace_id=actor.workspace_id,
            actor_id=actor.actor_id,
            actor_role=stored_role if stored_role in ROLE_CAPABILITIES else actor.actor_role,
        )
        write_audit_event(
            connection,
            denied_actor,
            action="ai.manage_denied",
            target_type="user",
            target_id=actor.actor_id,
            after_state={
                "attempted": attempted,
                "stored_role": stored_role,
                "is_active": bool(user.is_active) if user is not None else None,
            },
        )
    raise AiSettingsError(
        403,
        "ai_manage_required",
        "Admin permission required",
        "Only a workspace manager can change AI settings or providers.",
    )


def _key_required() -> AiSettingsError:
    return AiSettingsError(
        422,
        "key_required",
        "Re-enter the key",
        "Changing where the key is kept requires entering the key again.",
    )


def _invalid_local_model() -> AiSettingsError:
    return AiSettingsError(
        422,
        "invalid_model",
        "Invalid model",
        "The model must be a served model id such as qwen3.5:9b.",
    )


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


class ProviderConfigUpdate(BaseModel):
    """One provider card; Gemini and the vLLM server are configured separately."""

    model_config = ConfigDict(extra="forbid")

    provider: Literal["gemini", "vllm"]
    enabled: bool | None = None
    model: str | None = Field(default=None, max_length=128)
    base_url: str | None = Field(default=None, max_length=512)
    api_key: SecretStr | None = Field(default=None)
    key_storage: Literal["session", "saved"] | None = None
    remove_key: bool = False
    limits: dict[str, int] | None = None

    @model_validator(mode="after")
    def _provider_fields(self) -> ProviderConfigUpdate:
        if self.provider == "gemini":
            if self.base_url is not None:
                raise ValueError("Gemini has no base URL")
            if self.remove_key:
                raise ValueError("remove the Gemini key with DELETE /ai/key")
            if self.model is not None and not _MODEL.fullmatch(self.model):
                raise ValueError("model must be a Gemini model id such as gemini-3.8-flash")
            if self.api_key is not None:
                secret = self.api_key.get_secret_value()
                if not _KEY.fullmatch(secret) or secret.startswith("gemini-"):
                    raise ValueError("the API key format is not valid")
        else:
            if self.model is not None and not _LOCAL_MODEL.fullmatch(self.model):
                raise ValueError("model must be a served model id such as qwen3.5:9b")
            if self.api_key is not None:
                if not _LOCAL_KEY.fullmatch(self.api_key.get_secret_value()):
                    raise ValueError("the API key format is not valid")
                if self.remove_key:
                    raise ValueError("either set or remove the key")
        for name, value in (self.limits or {}).items():
            bounds = _PROVIDER_LIMIT_RANGES.get(name)
            if bounds is None or not bounds[0] <= value <= bounds[1]:
                raise ValueError(f"limit {name!r} is not supported or out of range")
        return self


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
    # D42 field types whose benchmark gate passed; empty by default.
    auto_verify_field_types: list[AutoVerifyFieldType] | None = None
    # Separate providers (WI-BE3): one card each, and which one reads page images.
    providers: list[ProviderConfigUpdate] | None = Field(default=None, max_length=2)
    reader_strategy: Literal["off", "shadow", "single"] | None = None
    primary_reader: Literal["gemini", "vllm"] | None = None
    apply_mode: Literal["suggest", "merge"] | None = None

    @field_validator("providers")
    @classmethod
    def _providers_are_unique(
        cls, value: list[ProviderConfigUpdate] | None
    ) -> list[ProviderConfigUpdate] | None:
        if value is not None and len({item.provider for item in value}) != len(value):
            raise ValueError("each provider may appear once")
        return value

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

    @field_validator("auto_verify_field_types")
    @classmethod
    def _auto_verify_field_types_are_unique(
        cls, value: list[AutoVerifyFieldType] | None
    ) -> list[AutoVerifyFieldType] | None:
        if value is not None and len(value) != len(set(value)):
            raise ValueError("auto_verify_field_types must not contain duplicates")
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

    def reader_available(self, provider: str | None) -> bool:
        return reader_for_provider(self.vision_reader, provider) is not None


@dataclass(frozen=True, slots=True)
class _Strategy:
    reader_strategy: str
    primary_reader: str | None
    apply_mode: str


def _row_strategy(row: Any) -> _Strategy:
    """Stored strategy; a row with only the old ``vision_enabled`` reads as ``single``+Gemini."""

    if row is None:
        return _Strategy("off", None, "suggest")
    strategy = str(row["reader_strategy"])
    primary = row["primary_reader"]
    if strategy == "off" and row["vision_enabled"]:
        strategy, primary = "single", "gemini"
    return _Strategy(strategy, primary, str(row["apply_mode"]))


def _origin(base_url: str | None) -> str | None:
    if base_url is None:
        return None
    scheme, _, rest = base_url.partition("://")
    return f"{scheme}://{rest.split('/', 1)[0]}"


def _config_public(row: Any) -> dict[str, Any] | None:
    """Audit view of a provider card: never the key, only the base URL's origin."""

    if row is None:
        return None
    return {
        "provider": row["provider"],
        "enabled": bool(row["enabled"]),
        "model": row["model"],
        "base_url_origin": _origin(row["base_url"]),
        "key_set": row["key_secret_id"] is not None,
        "tier": row["key_tier"] if row["provider"] == "gemini" else None,
        "limits": dict(row["limits"] or {}),
        "version": int(row["version"]),
    }


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
        strategy = _row_strategy(row)
        vision_on = strategy.reader_strategy == "single"
        auto_verify_fields = tuple(row["auto_verify_field_types"] or ()) if row else ()
        configs = self._configs(connection, workspace_id)
        vllm = configs.get("vllm")
        local_reader = strategy.primary_reader == "vllm"
        active_provider = active_provider_for(
            mode=mode,
            reader_strategy=strategy.reader_strategy,
            primary_reader=strategy.primary_reader,
            provider=provider,
        )
        active_model = (
            (vllm["model"] if vllm else None) if active_provider == "vllm"
            else model if active_provider == "gemini"
            else None
        )
        fingerprint = (
            fingerprint_for(
                provider=active_provider or provider or "gemini",
                model=model or "",
                mode=mode,
                reader_strategy=strategy.reader_strategy,
                primary_reader=strategy.primary_reader,
                apply_mode=strategy.apply_mode,
                auto_verify_field_types=auto_verify_fields,
                vllm_model=vllm["model"] if vllm else None,
                vllm_base_url=vllm["base_url"] if vllm else None,
            )
            if mode != "rules_only" and active_provider and active_model
            else None
        )
        key = self._secret_view(
            connection, self._gemini_secret_id(row, configs.get("gemini"))
        )
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
                "text_version": consent_text_version(auto_verify_field_types=auto_verify_fields),
                "required_fingerprint": fingerprint,
                "text": consent_text(
                    provider=active_provider or "gemini",
                    model=model or "the selected model",
                    auto_verify_field_types=auto_verify_fields,
                    reader_strategy=strategy.reader_strategy,
                    vision_provider=strategy.primary_reader,
                    vision_model=(vllm["model"] if vllm else None) if local_reader else model,
                    vision_base_url=(vllm["base_url"] if vllm else None) if local_reader else None,
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
            "reader_strategy": strategy.reader_strategy,
            "primary_reader": strategy.primary_reader,
            # Providers are separate alternatives (plan §0.6): never a second reader.
            "secondary_reader": None,
            "apply_mode": strategy.apply_mode,
            "vllm_calls": self.config.gate.vllm_mode,
            "vllm_endpoints_allowed": list(self.config.gate.vllm_endpoints),
            "providers": self._providers_view(connection, row, configs, key, now),
            "auto_verify": {
                "field_types": list(auto_verify_fields),
                "available_field_types": [
                    {"type": field_type, "label": AUTO_VERIFY_FIELD_LABELS[field_type]}
                    for field_type in AUTO_VERIFY_FIELD_TYPES
                ],
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

    @staticmethod
    def _configs(
        connection: Connection, workspace_id: UUID, *, for_update: bool = False
    ) -> dict[str, Any]:
        query = sa.select(CONFIGS).where(CONFIGS.c.workspace_id == workspace_id)
        if for_update:
            query = query.with_for_update()
        return {str(item["provider"]): item for item in connection.execute(query).mappings().all()}

    def _providers_view(
        self,
        connection: Connection,
        row: Any,
        configs: dict[str, Any],
        gemini_key: dict[str, Any],
        now: datetime,
    ) -> list[dict[str, Any]]:
        """One card per provider; keys appear only as set/hint/storage (write-only)."""

        del now
        gemini = configs.get("gemini")
        vllm = configs.get("vllm")
        return [
            {
                "provider": "gemini",
                "enabled": (
                    bool(gemini["enabled"])
                    if gemini is not None
                    else bool(row and row["mode"] != "rules_only" and row["provider"] == "gemini")
                ),
                "model": row["model"] if row else None,
                "base_url": None,
                "key": gemini_key,
                "limits": dict(gemini["limits"] or {}) if gemini is not None else {},
                "external": True,
                "data_destination": data_destination("gemini"),
                "last_test": {
                    "at": row["last_test_at"] if row else None,
                    "ok": row["last_test_ok"] if row else None,
                },
                "health": {
                    "at": gemini["last_health_at"] if gemini is not None else None,
                    "ok": gemini["last_health_ok"] if gemini is not None else None,
                },
                "tier": str(gemini["key_tier"]) if gemini is not None else "free",
                "connection": connection_state.derive(gemini, usable=bool(gemini_key["set"])),
                "consent": self._card_consent(gemini, row),
            },
            {
                "provider": "vllm",
                "enabled": bool(vllm["enabled"]) if vllm is not None else False,
                "model": vllm["model"] if vllm is not None else None,
                "base_url": vllm["base_url"] if vllm is not None else None,
                "key": self._secret_view(
                    connection, vllm["key_secret_id"] if vllm is not None else None
                ),
                "limits": dict(vllm["limits"] or {}) if vllm is not None else {},
                "external": False,
                "data_destination": data_destination(
                    "vllm", vllm["base_url"] if vllm is not None else None
                ),
                "last_test": {
                    "at": vllm["last_test_at"] if vllm is not None else None,
                    "ok": vllm["last_test_ok"] if vllm is not None else None,
                },
                "health": {
                    "at": vllm["last_health_at"] if vllm is not None else None,
                    "ok": vllm["last_health_ok"] if vllm is not None else None,
                },
                "tier": None,
                "connection": connection_state.derive(
                    vllm,
                    usable=bool(
                        vllm is not None and vllm["enabled"] and vllm["model"] and vllm["base_url"]
                    ),
                ),
                "consent": self._card_consent(vllm, None),
            },
        ]

    @staticmethod
    def _card_consent(card: Any, settings_row: Any) -> dict[str, Any]:
        """Who accepted the card's provider notice and whether it still matches the card.

        A workspace that connected Gemini before WI-PV2 has no card notice; its AI consent
        on ``ai_setting`` stays what authorises sends, and is reported as ``legacy``.
        """

        if card is None or card["consent_fingerprint"] is None:
            legacy = bool(settings_row is not None and settings_row["consent_given"])
            return {
                "accepted": False,
                "current": False,
                "at": None,
                "legacy_workspace_consent": legacy,
            }
        model = card["model"]
        expected = (
            notice_fingerprint(
                str(card["provider"]),
                tier=str(card["key_tier"]),  # type: ignore[arg-type]
                model=model,
                base_url=card["base_url"],
            )
            if model
            else None
        )
        return {
            "accepted": True,
            "current": card["consent_fingerprint"] == expected,
            "at": card["consent_accepted_at"],
            "legacy_workspace_consent": False,
        }

    @staticmethod
    def _gemini_secret_id(row: Any, card: Any) -> UUID | None:
        """The Gemini key reference: the card's, else the legacy ``ai_setting`` pointer."""

        if card is not None and card["key_secret_id"] is not None:
            return UUID(str(card["key_secret_id"]))
        return row["key_secret_id"] if row is not None else None

    @staticmethod
    def _secret_view(connection: Connection, secret_id: UUID | None) -> dict[str, Any]:
        if secret_id is None:
            return {"set": False, "hint": None, "storage": None, "expires_at": None}
        secret = (
            connection.execute(
                sa.select(SECRETS.c.key_hint, SECRETS.c.expires_at, SECRETS.c.session_id).where(
                    SECRETS.c.id == secret_id
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
        active_provider = settings.active_provider
        if active_provider is None:
            return status("OFF", "Off: rules only")
        key = load_provider_key(connection, settings, active_provider, now)
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
        if key == BLOCK_PROVIDER_DISABLED:
            return status("PROVIDER_DISABLED", "Provider disabled")
        assert not isinstance(key, str)
        key_created_at = key.created_at if key is not None else None
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
                    *(
                        [requests.c.created_at >= key_created_at]
                        if key_created_at is not None
                        else []
                    ),
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
                requests.c.provider == active_provider,
                requests.c.settings_version == settings.version,
                requests.c.request_state == "succeeded",
                requests.c.apply_outcome == "applied",
                *([requests.c.created_at >= key_created_at] if key_created_at is not None else []),
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
        self,
        actor: ActorContext,
        *,
        mode: str,
        model: str,
        vision: bool = False,
        auto_verify_field_types: tuple[str, ...] = (),
        reader_strategy: str | None = None,
        primary_reader: str | None = None,
        vllm_model: str | None = None,
        vllm_base_url: str | None = None,
        apply_mode: str = "suggest",
    ) -> dict[str, Any]:
        """What a person would consent to for unsaved settings; nothing is written.

        The browser cannot compute the fingerprint (it covers server-side prompt, schema
        and redaction versions), so the card fetches it here before the save that turns
        AI on. ``vision=true`` is the legacy spelling of ``single`` with Gemini.
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
        if reader_strategy is None:
            reader_strategy = "single" if vision else "off"
            primary_reader = "gemini" if vision else None
        if vllm_model is not None and not _LOCAL_MODEL.fullmatch(vllm_model):
            raise _invalid_local_model()
        if vllm_base_url is not None:
            vllm_base_url = self._allowed_endpoint(vllm_base_url)
        self._check_strategy(
            _Strategy(reader_strategy, primary_reader, apply_mode),
            vllm_ready=bool(vllm_model and vllm_base_url),
        )
        if reader_strategy == "shadow":
            raise AiSettingsError(
                422,
                "shadow_not_allowed",
                "Shadow mode is not allowed",
                "Shadow reader mode is not supported.",
            )
        vision = reader_strategy == "single"
        local = primary_reader == "vllm"
        active_provider = active_provider_for(
            mode=mode,
            reader_strategy=reader_strategy,
            primary_reader=primary_reader,
            provider="gemini",
        )
        if any(field_type not in AUTO_VERIFY_FIELD_TYPES for field_type in auto_verify_field_types):
            raise AiSettingsError(
                422,
                "auto_verify_field_type_invalid",
                "Unsupported auto-verify field",
                "Choose only a field type whose D42 benchmark gate passed.",
            )
        auto_verify_fields = tuple(
            field_type
            for field_type in AUTO_VERIFY_FIELD_TYPES
            if field_type in auto_verify_field_types
        )
        return {
            "mode": mode,
            "mode_label": MODE_LABELS.get(mode, mode),
            "provider": active_provider or "gemini",
            "model": model,
            "text_version": consent_text_version(auto_verify_field_types=auto_verify_fields),
            "text": consent_text(
                provider=active_provider or "gemini",
                model=model,
                auto_verify_field_types=auto_verify_fields,
                reader_strategy=reader_strategy,
                vision_provider=primary_reader,
                vision_model=vllm_model if local else model,
                vision_base_url=vllm_base_url if local else None,
            ),
            "fingerprint": fingerprint_for(
                provider=active_provider or "gemini",
                model=model,
                mode=mode,
                reader_strategy=reader_strategy,
                primary_reader=primary_reader,
                apply_mode=apply_mode,
                auto_verify_field_types=auto_verify_fields,
                vllm_model=vllm_model,
                vllm_base_url=vllm_base_url,
            ),
            "vision": vision,
            "reader_strategy": reader_strategy,
            "primary_reader": primary_reader,
            "apply_mode": apply_mode,
            "providers": [
                {
                    "provider": "gemini",
                    "model": model,
                    "external": True,
                    "data_destination": data_destination("gemini"),
                },
                {
                    "provider": "vllm",
                    "model": vllm_model,
                    "base_url": vllm_base_url,
                    "external": False,
                    "data_destination": data_destination("vllm", vllm_base_url),
                },
            ],
            "auto_verify_field_types": list(auto_verify_fields),
            "hooks": [
                {"hook": hook, "label": HOOK_LABELS[hook], "data_sent": DATA_SENT[hook]}
                for hook in hooks_for_mode(mode, vision=vision)
            ],
        }

    # -- validation ----------------------------------------------------------------------------

    def _allowed_endpoint(self, base_url: str) -> str:
        """The normalised URL when it is on ``RB_AI_VLLM_ENDPOINTS`` (SSRF guard on save)."""

        try:
            return check_allowed(base_url, self.config.gate.vllm_endpoints)
        except EndpointNotAllowed:
            raise AiSettingsError(
                422,
                "endpoint_not_allowed",
                "Server address not allowed",
                "The server URL must match an address this deployment allows "
                "(RB_AI_VLLM_ENDPOINTS) exactly.",
            ) from None

    def _check_strategy(self, strategy: _Strategy, *, vllm_ready: bool) -> None:
        if strategy.reader_strategy == "shadow":
            raise AiSettingsError(
                422,
                "shadow_not_allowed",
                "Shadow mode is not allowed",
                "Shadow reader mode is not supported.",
            )
        if strategy.apply_mode == "merge":
            raise AiSettingsError(
                422,
                "merge_not_enabled",
                "Merging is not enabled",
                "AI answers stay suggestions until merging is approved (WI-BE7).",
            )
        if strategy.reader_strategy == "off":
            if strategy.primary_reader is not None:
                raise AiSettingsError(
                    422,
                    "invalid_reader_strategy",
                    "No reader with page images off",
                    "Turn page images on (single or shadow) before choosing a reader.",
                )
            return
        if strategy.primary_reader is None:
            raise AiSettingsError(
                422,
                "primary_reader_required",
                "Choose a reader",
                "Choose Gemini or the company server to read page images.",
            )
        if not self.config.reader_available(strategy.primary_reader):
            raise _vision_unavailable()
        if strategy.primary_reader == "vllm" and not vllm_ready:
            raise AiSettingsError(
                422,
                "provider_not_configured",
                "Company server not set up",
                "Enable the company server card with a model and an allowed URL first.",
            )

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
        require_ai_manage(self.engine, actor, attempted="settings_update")
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
        gemini_update = next((p for p in body.providers or () if p.provider == "gemini"), None)
        vllm_update = next((p for p in body.providers or () if p.provider == "vllm"), None)
        if gemini_update is not None and (
            (gemini_update.api_key is not None and body.api_key is not None)
            or (
                gemini_update.model is not None
                and body.model is not None
                and gemini_update.model != body.model
            )
        ):
            raise AiSettingsError(
                422,
                "conflicting_fields",
                "Conflicting Gemini settings",
                "Send the Gemini model and key either at the top level or in its card.",
            )
        api_key = body.api_key or (gemini_update.api_key if gemini_update else None)
        key_storage_choice = body.key_storage or (
            gemini_update.key_storage if gemini_update else None
        )
        with self.engine.begin() as connection:
            row = self._locked_row(connection, actor)
            self._check_version(row, expected_version)
            configs = self._configs(connection, actor.workspace_id, for_update=True)
            before = self._public_state(row, configs.get("gemini"))
            mode = body.mode or (str(row["mode"]) if row else "rules_only")
            provider = body.provider or (row["provider"] if row else None)
            if provider is None and gemini_update is not None:
                provider = "gemini"
            model = (
                body.model
                or (gemini_update.model if gemini_update else None)
                or (row["model"] if row else None)
            )
            # gemini_required removed: inactive provider may be switched off.
            auto_verify_fields = tuple(
                body.auto_verify_field_types
                if body.auto_verify_field_types is not None
                else (row["auto_verify_field_types"] or () if row else ())
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

            # The company vLLM server card: URL checked against the allowlist on save.
            vllm_row = configs.get("vllm")
            vllm_values: dict[str, Any] | None = None
            if vllm_update is not None:
                base_url = vllm_row["base_url"] if vllm_row else None
                if vllm_update.base_url is not None:
                    base_url = self._allowed_endpoint(vllm_update.base_url)
                vllm_values = {
                    "enabled": (
                        vllm_update.enabled
                        if vllm_update.enabled is not None
                        else bool(vllm_row and vllm_row["enabled"])
                    ),
                    "model": vllm_update.model or (vllm_row["model"] if vllm_row else None),
                    "base_url": base_url,
                    "limits": (
                        vllm_update.limits
                        if vllm_update.limits is not None
                        else dict(vllm_row["limits"] or {})
                        if vllm_row
                        else {}
                    ),
                }
                if vllm_values["enabled"] and not (vllm_values["model"] and base_url):
                    raise AiSettingsError(
                        422,
                        "provider_not_configured",
                        "Company server not set up",
                        "An enabled company server needs a model and an allowed URL.",
                    )
            vllm_state: Any = vllm_values if vllm_values is not None else vllm_row
            vllm_ready = bool(
                vllm_state
                and vllm_state["enabled"]
                and vllm_state["model"]
                and vllm_state["base_url"]
            )

            current = _row_strategy(row)
            reader_strategy, primary_reader = current.reader_strategy, current.primary_reader
            if body.reader_strategy is not None:
                reader_strategy = body.reader_strategy
                primary_reader = (
                    body.primary_reader
                    if body.primary_reader is not None
                    else None
                    if reader_strategy == "off"
                    else current.primary_reader or "gemini"
                )
            elif body.vision is not None:
                reader_strategy = "single" if body.vision else "off"
                primary_reader = (current.primary_reader or "gemini") if body.vision else None
            elif body.primary_reader is not None:
                primary_reader = body.primary_reader
            strategy = _Strategy(
                reader_strategy, primary_reader, body.apply_mode or current.apply_mode
            )
            if strategy != current or vllm_update is not None:
                self._check_strategy(strategy, vllm_ready=vllm_ready)
            active_provider = active_provider_for(
                mode=mode,
                reader_strategy=strategy.reader_strategy,
                primary_reader=strategy.primary_reader,
                provider=provider,
            )
            if mode != "rules_only" and active_provider is not None:
                if (
                    active_provider == "gemini"
                    and gemini_update is not None
                    and gemini_update.enabled is False
                ):
                    raise AiSettingsError(
                        422,
                        "active_provider_in_use",
                        "Active provider in use",
                        "The active provider's connection cannot be disabled "
                        "while AI assist is on.",
                    )
                if (
                    active_provider == "vllm"
                    and vllm_update is not None
                    and vllm_update.enabled is False
                ):
                    raise AiSettingsError(
                        422,
                        "active_provider_in_use",
                        "Active provider in use",
                        "The active provider's connection cannot be disabled "
                        "while AI assist is on.",
                    )
            vision_on = strategy.reader_strategy == "single"
            fingerprint = (
                fingerprint_for(
                    provider=active_provider or provider or "gemini",
                    model=model,
                    mode=mode,
                    reader_strategy=strategy.reader_strategy,
                    primary_reader=strategy.primary_reader,
                    apply_mode=strategy.apply_mode,
                    auto_verify_field_types=auto_verify_fields,
                    vllm_model=vllm_state["model"] if vllm_state else None,
                    vllm_base_url=vllm_state["base_url"] if vllm_state else None,
                )
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
                    "consent_text_version": consent_text_version(
                        auto_verify_field_types=auto_verify_fields
                    ),
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
            old_card_secret = (
                configs["gemini"]["key_secret_id"] if configs.get("gemini") else None
            )
            old_legacy_secret = row["key_secret_id"] if row else None
            old_secret = old_card_secret or old_legacy_secret
            new_secret: UUID | None = None
            key_changed = False
            if api_key is not None:
                storage = key_storage_choice or (str(row["key_storage"]) if row else "session")
                new_secret = self._seal_key(connection, actor, session_id, api_key, storage)
                # The key lives on the Gemini card (WI-PV2); the old pointer is cleared.
                key_values = {"key_secret_id": None, "key_storage": storage}
                key_changed = True
            elif (
                key_storage_choice is not None
                and old_secret is not None
                and (row is None or key_storage_choice != row["key_storage"])
            ):
                raise _key_required()
            elif key_storage_choice is not None:
                key_values = {"key_storage": key_storage_choice}

            values: dict[str, Any] = {
                "mode": mode,
                "provider": provider,
                "model": model,
                "vision_enabled": vision_on,
                "reader_strategy": strategy.reader_strategy,
                "primary_reader": strategy.primary_reader,
                "apply_mode": strategy.apply_mode,
                "auto_verify_field_types": list(auto_verify_fields),
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

            # Gemini's card mirrors the text-hook settings and holds the key.
            gemini_row = configs.get("gemini")
            if (provider == "gemini" or key_changed) and (
                gemini_row is not None
                or gemini_update is not None
                or mode != "rules_only"
                or key_changed
            ):
                gemini_enabled = (
                    gemini_update.enabled
                    if gemini_update is not None and gemini_update.enabled is not None
                    else (mode != "rules_only")
                    if active_provider == "gemini"
                    else bool(gemini_row["enabled"])
                    if gemini_row is not None
                    else (mode != "rules_only")
                )
                self._upsert_config(
                    connection,
                    actor,
                    gemini_row,
                    "gemini",
                    {
                        "enabled": gemini_enabled,
                        "model": model,
                        **({"key_secret_id": new_secret} if key_changed else {}),
                        "limits": (
                            gemini_update.limits
                            if gemini_update is not None and gemini_update.limits is not None
                            else dict(gemini_row["limits"] or {})
                            if gemini_row
                            else {}
                        ),
                    },
                    now,
                )
            if key_changed:
                stale = [item for item in (old_card_secret, old_legacy_secret) if item]
                if stale:
                    connection.execute(sa.delete(SECRETS).where(SECRETS.c.id.in_(stale)))
            if vllm_values is not None:
                assert vllm_update is not None
                old_vllm_secret = vllm_row["key_secret_id"] if vllm_row else None
                vllm_key_changed = False
                if vllm_update.api_key is not None:
                    storage = vllm_update.key_storage or "saved"
                    vllm_values["key_secret_id"] = self._seal_key(
                        connection, actor, session_id, vllm_update.api_key, storage
                    )
                    vllm_key_changed = True
                elif vllm_update.remove_key and old_vllm_secret is not None:
                    vllm_values["key_secret_id"] = None
                    vllm_key_changed = True
                elif vllm_update.key_storage is not None and old_vllm_secret is not None:
                    raise _key_required()
                self._upsert_config(connection, actor, vllm_row, "vllm", vllm_values, now)
                if vllm_key_changed and old_vllm_secret is not None:
                    connection.execute(sa.delete(SECRETS).where(SECRETS.c.id == old_vllm_secret))

            after_row = self._locked_row(connection, actor)
            after_configs = self._configs(connection, actor.workspace_id)
            write_audit_event(
                connection,
                actor,
                action="ai.settings_save",
                target_type="ai_setting",
                target_id=after_row["id"],
                before_state=before,
                after_state={
                    **(self._public_state(after_row, after_configs.get("gemini")) or {}),
                    "key_changed": key_changed,
                },
            )
            for name in PROVIDERS:
                before_config = _config_public(configs.get(name))
                after_config = _config_public(after_configs.get(name))
                if after_config is not None and before_config != after_config:
                    write_audit_event(
                        connection,
                        actor,
                        action="ai.provider_config_save",
                        target_type="ai_setting",
                        target_id=after_row["id"],
                        before_state=before_config,
                        after_state=after_config,
                    )
            before_active_provider = active_provider_for(
                mode=str(row["mode"]) if row else "rules_only",
                reader_strategy=current.reader_strategy,
                primary_reader=current.primary_reader,
                provider=row["provider"] if row else None,
            )
            if (strategy.reader_strategy, strategy.primary_reader) != (
                current.reader_strategy,
                current.primary_reader,
            ):
                write_audit_event(
                    connection,
                    actor,
                    action="ai.reader_strategy_change",
                    target_type="ai_setting",
                    target_id=after_row["id"],
                    before_state={
                        "reader_strategy": current.reader_strategy,
                        "primary_reader": current.primary_reader,
                    },
                    after_state={
                        "reader_strategy": strategy.reader_strategy,
                        "primary_reader": strategy.primary_reader,
                    },
                )
            if before_active_provider != active_provider:
                write_audit_event(
                    connection,
                    actor,
                    action="ai.active_provider_change",
                    target_type="ai_setting",
                    target_id=after_row["id"],
                    before_state={"active_provider": before_active_provider},
                    after_state={"active_provider": active_provider},
                )
            if strategy.apply_mode != current.apply_mode:
                write_audit_event(
                    connection,
                    actor,
                    action="ai.apply_mode_change",
                    target_type="ai_setting",
                    target_id=after_row["id"],
                    before_state={"apply_mode": current.apply_mode},
                    after_state={"apply_mode": strategy.apply_mode},
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

    def _seal_key(
        self,
        connection: Connection,
        actor: ActorContext,
        session_id: UUID,
        api_key: SecretStr,
        storage: str,
    ) -> UUID:
        """Seal a write-only key for the AI worker and store it; returns the secret id."""

        public_key = self._public_key()
        if public_key is None:
            raise AiSettingsError(
                503,
                "key_storage_unavailable",
                "Key storage unavailable",
                "The AI sealing key is not configured on the server.",
            )
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
        plaintext = api_key.get_secret_value()
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
        return secret_id

    @staticmethod
    def _upsert_config(
        connection: Connection,
        actor: ActorContext,
        existing: Any,
        provider: str,
        values: dict[str, Any],
        now: datetime,
    ) -> None:
        if existing is None:
            connection.execute(
                sa.insert(CONFIGS).values(
                    id=new_id(),
                    workspace_id=actor.workspace_id,
                    provider=provider,
                    updated_by=actor.actor_id,
                    updated_at=now,
                    **values,
                )
            )
            return
        changed = {key: value for key, value in values.items() if existing[key] != value}
        if not changed:
            return
        # A changed server, model or key invalidates the last test and health result, and
        # puts the card back to Not connected until it is tested again (WI-PV2).
        resets: dict[str, Any] = {"last_test_at": None, "last_test_ok": None}
        if "base_url" in changed:
            resets |= {"last_health_at": None, "last_health_ok": None}
        if changed.keys() & {"base_url", "model", "key_secret_id"}:
            resets |= connection_state.reset_values()
        connection.execute(
            sa.update(CONFIGS)
            .where(CONFIGS.c.id == existing["id"])
            .values(
                version=CONFIGS.c.version + 1,
                updated_by=actor.actor_id,
                updated_at=now,
                **resets,
                **values,
            )
        )

    @staticmethod
    def _public_state(row: Any, card: Any = None) -> dict[str, Any] | None:
        if row is None:
            return None
        active_provider = active_provider_for(
            mode=row["mode"],
            reader_strategy=row["reader_strategy"],
            primary_reader=row["primary_reader"],
            provider=row["provider"],
        )
        return {
            "version": int(row["version"]),
            "mode": row["mode"],
            "provider": row["provider"],
            "active_provider": active_provider,
            "model": row["model"],
            "key_set": row["key_secret_id"] is not None
            or (card is not None and card["key_secret_id"] is not None),
            "key_storage": row["key_storage"],
            "consent_given": bool(row["consent_given"]),
            "vision_enabled": bool(row["vision_enabled"]),
            "reader_strategy": row["reader_strategy"],
            "primary_reader": row["primary_reader"],
            "apply_mode": row["apply_mode"],
            "auto_verify_field_types": list(row["auto_verify_field_types"] or ()),
        }

    @requires("ai.manage")
    def remove_key(self, actor: ActorContext, *, expected_version: int) -> dict[str, Any]:
        require_ai_manage(self.engine, actor, attempted="key_remove")
        now = self.clock()
        with self.engine.begin() as connection:
            row = self._locked_row(connection, actor)
            self._check_version(row, expected_version)
            card = self._configs(connection, actor.workspace_id, for_update=True).get("gemini")
            secret_id = self._gemini_secret_id(row, card)
            if row is None or secret_id is None:
                raise AiSettingsError(404, "key_not_set", "No key", "No AI key is stored.")
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
            doomed = {secret_id, row["key_secret_id"]}
            if card is not None:
                doomed.add(card["pending_key_secret_id"])
                connection.execute(
                    sa.update(CONFIGS)
                    .where(CONFIGS.c.id == card["id"])
                    .values(
                        key_secret_id=None,
                        pending_key_secret_id=None,
                        version=CONFIGS.c.version + 1,
                        updated_by=actor.actor_id,
                        updated_at=now,
                        **connection_state.reset_values(),
                    )
                )
            connection.execute(
                sa.delete(SECRETS).where(SECRETS.c.id.in_([item for item in doomed if item]))
            )
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
    def request_provider_test(
        self,
        actor: ActorContext,
        *,
        ip: str | None,
        provider: Literal["gemini", "vllm"] = "gemini",
        kind: Literal["text", "image"] = "text",
    ) -> dict[str, Any]:
        """Queue the synthetic text fixture for one provider; the AI worker sends it.

        ``{gemini, text}`` is today's test. The vLLM server is tested with the same
        synthetic fixture, without a key unless one is stored. The synthetic page-image
        test (``kind=image``) needs an image fixture that does not exist yet.
        """

        require_ai_manage(self.engine, actor, attempted="provider_test")
        if kind == "image":
            raise AiSettingsError(
                422,
                "image_test_not_available",
                "Image test not available",
                "The synthetic page-image test is not available yet; run the text test.",
            )
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
            if provider == "vllm":
                vllm = settings.vllm if settings is not None else None
                if settings is None or vllm is None or not vllm.enabled or not vllm.model:
                    raise AiSettingsError(
                        422,
                        "provider_required",
                        "Company server not set up",
                        "Enable the company server card with a model and an allowed URL first.",
                    )
                if self.config.gate.vllm_mode == "disabled":
                    raise AiSettingsError(
                        409,
                        "local_calls_disabled",
                        "Company server calls disabled",
                        "This deployment does not allow calls to the company server "
                        "(RB_AI_VLLM_CALLS).",
                    )
                if not self.config.gate.endpoint_allowed(vllm.base_url):
                    raise AiSettingsError(
                        409,
                        "endpoint_not_allowed",
                        "Server address not allowed",
                        "The saved server URL is no longer on RB_AI_VLLM_ENDPOINTS.",
                    )
                test_model: str = vllm.model
            else:
                gemini = settings.gemini if settings is not None else None
                model = (
                    gemini.model
                    if gemini and gemini.model
                    else (settings.model if settings else None)
                )
                gemini_configured = bool(
                    settings is not None
                    and (gemini and gemini.enabled or settings.provider == "gemini")
                    and model is not None
                )
                if not gemini_configured or model is None:
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
                test_model = model
            key = load_provider_key(connection, settings, provider, now)
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
                    provider=provider,
                    model=test_model,
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
                after_state={
                    "settings_version": settings.version,
                    "provider": provider,
                    "model": test_model,
                },
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
    def usage(
        self, actor: ActorContext, *, provider: Literal["gemini", "vllm"] | None = None
    ) -> dict[str, Any]:
        """Usage today and this month; ``provider`` narrows it to one provider's calls."""

        now = self.clock()
        limits_state = load_limits(self.config.limits_file)
        prices = load_prices(self.config.price_file)
        with self.engine.connect() as connection:
            settings = load_settings(connection, actor.workspace_id)
            summary = ledger.usage_summary(connection, actor.workspace_id, now, provider=provider)
        model = settings.model if settings else None
        if provider == "vllm":
            # The company server is not billed: no price applies to its calls.
            model = None
        priced = bool(prices and model and model in prices.models)
        return {
            **summary,
            "provider": provider,
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
    def shadow_report(self, actor: ActorContext, *, since: date | None = None) -> dict[str, Any]:
        """Shadow reader aggregates (WI-BE6): labels per provider and field, never values."""

        with self.engine.connect() as connection:
            return shadow.workspace_report(connection, actor.workspace_id, since=since)

    @requires("ai.manage")
    def acknowledge(self, actor: ActorContext, request_id: UUID) -> dict[str, Any]:
        require_ai_manage(self.engine, actor, attempted="request_acknowledge")
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
