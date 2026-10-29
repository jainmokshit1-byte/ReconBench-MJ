"""Connect, rotate and disconnect a provider from the API (accuracy plan WI-PV2).

The same three actions work for both providers (Gemini and the company vLLM server):

* **connect** saves the credentials (sealed on receipt), the admin's declared tier (Gemini),
  the provider notice they accepted (stored with who and when) and queues a server-side
  test. The card is *Not connected* until the AI worker's test passes; any change resets it.
* **rotate** is test-then-swap: the candidate key is sealed as ``pending_key_secret_id`` and
  tested; the old key stays active until the test passes, and a failed test removes the
  candidate and changes nothing else.
* **disconnect** deletes every key of the card, records who and when, and moves the
  workspace's settings version so a request still in flight is discarded, never applied.

The API never calls a provider: it enqueues the synthetic provider test and the AI worker
sends it (``AiRequestRunner``). Keys are write-only; nothing here returns, logs or audits
one. Audit rows carry booleans, the tier, the model and the base URL's origin only.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError
from sqlalchemy.engine import Connection

from reconbench_app.ai import connection_state, ledger
from reconbench_app.ai.contracts import SCHEMA_VERSIONS
from reconbench_app.ai.fixtures import PROVIDER_IMAGE_TEST, PROVIDER_TEST
from reconbench_app.ai.limits import load_prices
from reconbench_app.ai.provider_notice import Tier, notice, notice_fingerprint
from reconbench_app.ai.redaction import REDACTION_VERSION
from reconbench_app.ai.runtime_checks import load_settings
from reconbench_app.ai.settings_service import (
    IMPLICIT_VERSION,
    TEST_LIMIT,
    TEST_WINDOW,
    AiSettingsError,
    AiSettingsService,
    ProviderConfigUpdate,
    require_ai_manage,
)
from reconbench_app.audit import write_audit_event
from reconbench_app.authorization import ActorContext, requires
from reconbench_app.db.ids import new_id
from reconbench_app.db.metadata import metadata

CONFIGS = metadata.tables["ai_provider_config"]
SETTINGS = metadata.tables["ai_setting"]
SECRETS = metadata.tables["secret"]

# The Gemini model that is live today and that the cost cap is priced for (D81).
PINNED_GEMINI_MODEL = "gemini-3.5-flash-lite"
Provider = Literal["gemini", "vllm"]


class ConsentToNotice(BaseModel):
    """The admin accepts the notice shown for this provider card."""

    model_config = ConfigDict(extra="forbid")

    accept: Literal[True]
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class ConnectBody(BaseModel):
    """Credentials and declarations for one provider card; the key is write-only."""

    model_config = ConfigDict(extra="forbid")

    # Gemini only: the API cannot tell the tier of a key, so the admin declares it.
    tier: Literal["free", "paid"] | None = None
    model: str | None = Field(default=None, max_length=128)
    # Company vLLM server only.
    base_url: str | None = Field(default=None, max_length=512)
    api_key: SecretStr | None = None
    key_storage: Literal["session", "saved"] | None = None
    consent: ConsentToNotice


class RotateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_key: SecretStr
    key_storage: Literal["session", "saved"] | None = None


def _error(status: int, code: str, title: str, detail: str) -> AiSettingsError:
    return AiSettingsError(status, code, title, detail)


class ProviderConnectionService:
    """Uses the settings service's engine, queue, clock, sealing and allowlist checks."""

    def __init__(self, settings: AiSettingsService) -> None:
        self.settings = settings

    @property
    def _engine(self) -> sa.Engine:
        return self.settings.engine

    def _now(self) -> datetime:
        return self.settings.clock()

    # -- notice --------------------------------------------------------------------------------

    @requires("ai.manage")
    def connect_notice(
        self,
        actor: ActorContext,
        *,
        provider: Provider,
        tier: Tier | None,
        model: str | None,
        base_url: str | None,
    ) -> dict[str, Any]:
        """The text an admin accepts for this card, with its fingerprint (nothing is written)."""

        # The model a connect would use when none is given: the workspace's own, else pinned.
        with self._engine.connect() as connection:
            existing = connection.execute(
                sa.select(SETTINGS.c.provider, SETTINGS.c.model).where(
                    SETTINGS.c.workspace_id == actor.workspace_id
                )
            ).first()
        resolved_tier, resolved_model, resolved_url = self._declared(
            provider,
            tier=tier,
            model=model,
            base_url=base_url,
            existing_model=existing[1] if existing and existing[0] == "gemini" else None,
        )
        return {
            **notice(provider, tier=resolved_tier, model=resolved_model, base_url=resolved_url),
            "fingerprint": notice_fingerprint(
                provider, tier=resolved_tier, model=resolved_model, base_url=resolved_url
            ),
        }

    def _declared(
        self,
        provider: Provider,
        *,
        tier: Tier | None,
        model: str | None,
        base_url: str | None,
        existing_model: str | None,
    ) -> tuple[Tier, str, str | None]:
        """Validate the declarations of one card; returns ``(tier, model, base_url)``."""

        if provider == "gemini":
            if base_url is not None:
                raise _error(422, "invalid_base_url", "No base URL", "Gemini has no base URL.")
            if tier is None:
                raise _error(
                    422,
                    "tier_required",
                    "Declare the key's tier",
                    "Say whether this Gemini key is on the free tier or a billed (paid) project.",
                )
            chosen = model or existing_model or PINNED_GEMINI_MODEL
            try:
                ProviderConfigUpdate(provider="gemini", model=chosen)
            except ValidationError:
                raise _error(
                    422,
                    "invalid_model",
                    "Invalid model",
                    "The model must be a Gemini model id such as gemini-3.5-flash-lite.",
                ) from None
            return tier, chosen, None
        if tier is not None:
            raise _error(
                422,
                "invalid_tier",
                "No tier for the company server",
                "The key tier applies to Gemini only.",
            )
        if not model:
            raise _error(
                422,
                "invalid_model",
                "Model required",
                "Enter the served model id, e.g. qwen3.5:9b.",
            )
        if not base_url:
            raise _error(
                422, "provider_not_configured", "Server URL required", "Enter the server URL."
            )
        try:
            ProviderConfigUpdate(provider="vllm", model=model)
        except ValidationError:
            raise _error(
                422,
                "invalid_model",
                "Invalid model",
                "The model must be a served model id such as qwen3.5:9b.",
            ) from None
        return "free", model, self.settings._allowed_endpoint(base_url)

    @staticmethod
    def _checked_key(
        provider: Provider, model: str | None, key: SecretStr | None
    ) -> SecretStr | None:
        """The key, validated with the card's own rules (never echoed in an error)."""

        if key is None:
            return None
        try:
            ProviderConfigUpdate(provider=provider, model=model, api_key=key)
        except ValidationError:
            raise _error(
                422, "invalid_key", "Invalid key", "The API key format is not valid."
            ) from None
        return key

    # -- shared checks -------------------------------------------------------------------------

    def _check_gate(self, provider: Provider, *, base_url: str | None) -> None:
        gate = self.settings.config.gate
        if provider == "vllm":
            if gate.vllm_mode == "disabled":
                raise _error(
                    409,
                    "local_calls_disabled",
                    "Company server calls disabled",
                    "This deployment does not allow calls to the company server "
                    "(RB_AI_VLLM_CALLS).",
                )
            if base_url is not None and not gate.endpoint_allowed(base_url):
                raise _error(
                    409,
                    "endpoint_not_allowed",
                    "Server address not allowed",
                    "The server URL is not on RB_AI_VLLM_ENDPOINTS.",
                )
        elif gate.mode == "disabled":
            raise _error(
                409,
                "external_calls_disabled",
                "External calls disabled",
                "This deployment does not allow provider calls (RB_AI_EXTERNAL_CALLS).",
            )

    def _check_rate(self, connection: Connection, actor: ActorContext, now: datetime) -> None:
        events = metadata.tables["rate_limit_event"]
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
            raise _error(
                429,
                "ai_test_rate_limited",
                "Provider test rate limited",
                "Wait a minute before testing again.",
            )

    @staticmethod
    def _record_test_event(
        connection: Connection, actor: ActorContext, ip: str | None, now: datetime
    ) -> None:
        connection.execute(
            sa.insert(metadata.tables["rate_limit_event"]).values(
                id=new_id(),
                workspace_id=actor.workspace_id,
                kind="ai_test",
                user_id=actor.actor_id,
                ip=ip,
                succeeded=True,
                occurred_at=now,
            )
        )

    def _ensure_settings_row(
        self, connection: Connection, actor: ActorContext, now: datetime
    ) -> Any:
        row = self.settings._locked_row(connection, actor)
        if row is None:
            connection.execute(
                sa.insert(SETTINGS).values(
                    id=new_id(),
                    workspace_id=actor.workspace_id,
                    version=IMPLICIT_VERSION + 1,
                    mode="rules_only",
                    updated_by=actor.actor_id,
                    updated_at=now,
                )
            )
            row = self.settings._locked_row(connection, actor)
        return row

    def _queue_test(
        self,
        connection: Connection,
        actor: ActorContext,
        *,
        provider: Provider,
        model: str,
        operation_key: str,
        image: bool = False,
    ) -> UUID:
        """One synthetic test request; ``image`` sends the image fixture (PD-1b)."""

        settings = load_settings(connection, actor.workspace_id)
        assert settings is not None
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
                operation_key=operation_key,
                settings_version=settings.version,
                consent_fingerprint=None,
                provider=provider,
                model=model,
                prompt_version=(PROVIDER_IMAGE_TEST if image else PROVIDER_TEST).fixture_id,
                schema_version=SCHEMA_VERSIONS["provider_image_test" if image else "provider_test"],
                redaction_version=REDACTION_VERSION,
                requested_by=actor.actor_id,
            ),
        )
        assert request_id is not None
        job_id = self.settings.queue.enqueue_ai_request(
            ai_request_id=request_id, connection=connection
        )
        connection.execute(
            sa.update(ledger.REQUESTS)
            .where(ledger.REQUESTS.c.id == request_id)
            .values(job_id=job_id)
        )
        return request_id

    @staticmethod
    def _card(connection: Connection, workspace_id: UUID, provider: Provider) -> Any:
        return (
            connection.execute(
                sa.select(CONFIGS)
                .where(CONFIGS.c.workspace_id == workspace_id, CONFIGS.c.provider == provider)
                .with_for_update()
            )
            .mappings()
            .first()
        )

    @staticmethod
    def _public(card: Any, legacy_secret: UUID | None) -> dict[str, Any] | None:
        """Audit view of a card: booleans, tier, model and origin only; never a key."""

        if card is None:
            return None
        origin = None
        if card["base_url"]:
            scheme, _, rest = str(card["base_url"]).partition("://")
            origin = f"{scheme}://{rest.split('/', 1)[0]}"
        return {
            "provider": card["provider"],
            "tier": card["key_tier"] if card["provider"] == "gemini" else None,
            "model": card["model"],
            "base_url_origin": origin,
            "key_set": card["key_secret_id"] is not None or legacy_secret is not None,
            "rotation_pending": card["pending_key_secret_id"] is not None,
            "connected": card["connected_at"] is not None and not card["last_error_code"],
            "consent_fingerprint": card["consent_fingerprint"],
            "version": int(card["version"]),
        }

    @staticmethod
    def _legacy_secret(connection: Connection, workspace_id: UUID) -> UUID | None:
        return connection.scalar(
            sa.select(SETTINGS.c.key_secret_id).where(SETTINGS.c.workspace_id == workspace_id)
        )

    # -- connect -------------------------------------------------------------------------------

    @requires("ai.manage")
    def connect(
        self,
        actor: ActorContext,
        *,
        session_id: UUID,
        provider: Provider,
        body: ConnectBody,
        ip: str | None,
    ) -> dict[str, Any]:
        require_ai_manage(self._engine, actor, attempted="provider_connect")
        now = self._now()
        key = self._checked_key(provider, body.model, body.api_key)
        if provider == "gemini" and key is None:
            raise _error(
                422, "key_required", "Enter the key", "A Gemini API key is required to connect."
            )
        with self._engine.begin() as connection:
            row = self._ensure_settings_row(connection, actor, now)
            existing_model = row["model"] if row["provider"] == "gemini" else None
            tier, model, base_url = self._declared(
                provider,
                tier=body.tier,
                model=body.model,
                base_url=body.base_url,
                existing_model=existing_model,
            )
            if (
                provider == "gemini"
                and row["mode"] != "rules_only"
                and row["model"] is not None
                and model != row["model"]
            ):
                raise _error(
                    409,
                    "model_change_via_settings",
                    "Change the model in the AI settings",
                    "AI is on with another model; change the model in the AI settings so the "
                    "workspace consent is asked again.",
                )
            if body.consent.fingerprint != notice_fingerprint(
                provider, tier=tier, model=model, base_url=base_url
            ):
                raise _error(
                    409,
                    "consent_mismatch",
                    "Consent is for a different notice",
                    "The tier, model or server changed since the notice was shown. "
                    "Review it again.",
                )
            prices = load_prices(self.settings.config.price_file)
            if tier == "paid" and not (prices and model in prices.models):
                raise _error(
                    422,
                    "price_not_configured",
                    "No price for this model",
                    "A paid key needs a price entry for the model before the cost cap can apply.",
                )
            self._check_gate(provider, base_url=base_url)
            self._check_rate(connection, actor, now)

            card = self._card(connection, actor.workspace_id, provider)
            legacy = self._legacy_secret(connection, actor.workspace_id)
            before = self._public(card, legacy)
            old_secrets: list[UUID] = []
            if card is not None:
                old_secrets += [s for s in (card["pending_key_secret_id"],) if s is not None]
            values: dict[str, Any] = {
                **connection_state.reset_values(),
                "model": model,
                "pending_key_secret_id": None,
                "consent_fingerprint": body.consent.fingerprint,
                "consent_accepted_by": actor.actor_id,
                "consent_accepted_at": now,
                "updated_by": actor.actor_id,
                "updated_at": now,
            }
            if provider == "gemini":
                values["key_tier"] = tier
            else:
                values["enabled"] = True
                values["base_url"] = base_url
            storage = body.key_storage or (
                str(row["key_storage"]) if provider == "gemini" else "saved"
            )
            if key is not None:
                values["key_secret_id"] = self.settings._seal_key(
                    connection, actor, session_id, key, storage
                )
                if card is not None and card["key_secret_id"] is not None:
                    old_secrets.append(card["key_secret_id"])
                if provider == "gemini" and legacy is not None:
                    old_secrets.append(legacy)
                values["key_rotated_at"] = None
            if card is None:
                connection.execute(
                    sa.insert(CONFIGS).values(
                        id=new_id(),
                        workspace_id=actor.workspace_id,
                        provider=provider,
                        enabled=bool(values.pop("enabled", False)),
                        **values,
                    )
                )
            else:
                connection.execute(
                    sa.update(CONFIGS)
                    .where(CONFIGS.c.id == card["id"])
                    .values(version=CONFIGS.c.version + 1, **values)
                )
            setting_values: dict[str, Any] = {
                "updated_by": actor.actor_id,
                "updated_at": now,
                "last_test_at": None,
                "last_test_ok": None,
            }
            if provider == "gemini":
                setting_values["provider"] = "gemini"
                setting_values["model"] = model
                setting_values["key_storage"] = storage
                if key is not None:
                    setting_values["key_secret_id"] = None
            connection.execute(
                sa.update(SETTINGS)
                .where(SETTINGS.c.id == row["id"])
                .values(version=SETTINGS.c.version + 1, **setting_values)
            )
            if old_secrets:
                connection.execute(sa.delete(SECRETS).where(SECRETS.c.id.in_(old_secrets)))
            self._record_test_event(connection, actor, ip, now)
            request_id = self._queue_test(
                connection,
                actor,
                provider=provider,
                model=model,
                operation_key=f"provider_test:{now.date().isoformat()}",
            )
            # The company server is also checked for reading an image (PD-1b): one more
            # logged request, which records only ``supports_vision`` on the card.
            image_request_id = (
                self._queue_test(
                    connection,
                    actor,
                    provider=provider,
                    model=model,
                    operation_key=f"{connection_state.IMAGE_PREFIX}{now.date().isoformat()}",
                    image=True,
                )
                if provider == "vllm"
                else None
            )
            after_card = self._card(connection, actor.workspace_id, provider)
            write_audit_event(
                connection,
                actor,
                action="ai.provider_connect",
                target_type="ai_setting",
                target_id=row["id"],
                before_state=before,
                after_state={
                    **(self._public(after_card, None) or {}),
                    "request_id": str(request_id),
                    **(
                        {"image_request_id": str(image_request_id)}
                        if image_request_id is not None
                        else {}
                    ),
                },
            )
            view = self.settings._view(connection, actor.workspace_id, now)
        return {
            "request_id": request_id,
            "image_request_id": image_request_id,
            "state": "queued",
            "settings": view,
        }

    # -- rotate --------------------------------------------------------------------------------

    @requires("ai.manage")
    def rotate(
        self,
        actor: ActorContext,
        *,
        session_id: UUID,
        provider: Provider,
        body: RotateBody,
        ip: str | None,
    ) -> dict[str, Any]:
        require_ai_manage(self._engine, actor, attempted="provider_rotate")
        now = self._now()
        key = self._checked_key(provider, None, body.api_key)
        assert key is not None
        with self._engine.begin() as connection:
            row = self.settings._locked_row(connection, actor)
            card = self._card(connection, actor.workspace_id, provider)
            legacy = self._legacy_secret(connection, actor.workspace_id)
            current_secret = card["key_secret_id"] if card is not None else None
            if provider == "gemini":
                current_secret = current_secret or legacy
            if row is None or card is None or (provider == "gemini" and current_secret is None):
                raise _error(
                    409,
                    "not_connected",
                    "Not connected",
                    "Connect the provider before rotating its key.",
                )
            model = card["model"] or (row["model"] if provider == "gemini" else None)
            if model is None:
                raise _error(409, "not_connected", "Not connected", "The card has no model.")
            self._check_gate(provider, base_url=card["base_url"])
            self._check_rate(connection, actor, now)
            session_bound = (
                connection.execute(
                    sa.select(SECRETS.c.session_id).where(SECRETS.c.id == current_secret)
                ).scalar_one_or_none()
                if current_secret is not None
                else None
            )
            storage = body.key_storage or ("session" if session_bound else "saved")
            pending = self.settings._seal_key(connection, actor, session_id, key, storage)
            before = self._public(card, legacy)
            old_pending = card["pending_key_secret_id"]
            connection.execute(
                sa.update(CONFIGS)
                .where(CONFIGS.c.id == card["id"])
                .values(
                    pending_key_secret_id=pending,
                    version=CONFIGS.c.version + 1,
                    updated_by=actor.actor_id,
                    updated_at=now,
                )
            )
            if old_pending is not None:
                connection.execute(sa.delete(SECRETS).where(SECRETS.c.id == old_pending))
            self._record_test_event(connection, actor, ip, now)
            request_id = self._queue_test(
                connection,
                actor,
                provider=provider,
                model=str(model),
                operation_key=connection_state.rotation_operation_key(provider, pending),
            )
            write_audit_event(
                connection,
                actor,
                action="ai.provider_rotate",
                target_type="ai_setting",
                target_id=row["id"],
                before_state=before,
                after_state={
                    **(
                        self._public(self._card(connection, actor.workspace_id, provider), legacy)
                        or {}
                    ),
                    "request_id": str(request_id),
                    "phase": "candidate_testing",
                },
            )
            view = self.settings._view(connection, actor.workspace_id, now)
        return {"request_id": request_id, "state": "queued", "settings": view}

    # -- disconnect ----------------------------------------------------------------------------

    @requires("ai.manage")
    def disconnect(self, actor: ActorContext, *, provider: Provider) -> dict[str, Any]:
        require_ai_manage(self._engine, actor, attempted="provider_disconnect")
        now = self._now()
        with self._engine.begin() as connection:
            row = self.settings._locked_row(connection, actor)
            card = self._card(connection, actor.workspace_id, provider)
            if row is None or card is None:
                raise _error(
                    404, "not_connected", "Not connected", "The provider is not connected."
                )
            legacy = (
                self._legacy_secret(connection, actor.workspace_id)
                if provider == "gemini"
                else None
            )
            before = self._public(card, legacy)
            doomed = [
                s
                for s in (card["key_secret_id"], card["pending_key_secret_id"], legacy)
                if s is not None
            ]
            card_values: dict[str, Any] = {
                **connection_state.reset_values(),
                "key_secret_id": None,
                "pending_key_secret_id": None,
                "consent_fingerprint": None,
                "consent_accepted_by": None,
                "consent_accepted_at": None,
                "key_rotated_at": None,
                "updated_by": actor.actor_id,
                "updated_at": now,
            }
            if provider == "vllm":
                card_values["enabled"] = False
            connection.execute(
                sa.update(CONFIGS)
                .where(CONFIGS.c.id == card["id"])
                .values(version=CONFIGS.c.version + 1, **card_values)
            )
            connection.execute(
                sa.update(SETTINGS)
                .where(SETTINGS.c.id == row["id"])
                .values(
                    version=SETTINGS.c.version + 1,
                    updated_by=actor.actor_id,
                    updated_at=now,
                    last_test_at=None,
                    last_test_ok=None,
                    **({"key_secret_id": None} if provider == "gemini" else {}),
                )
            )
            if doomed:
                connection.execute(sa.delete(SECRETS).where(SECRETS.c.id.in_(doomed)))
            after = self._public(self._card(connection, actor.workspace_id, provider), None)
            write_audit_event(
                connection,
                actor,
                action="ai.provider_disconnect",
                target_type="ai_setting",
                target_id=row["id"],
                before_state=before,
                after_state=after,
            )
            if provider == "gemini" and before and before["key_set"]:
                # The legacy status line ("Key removed by ...") reads this event.
                write_audit_event(
                    connection,
                    actor,
                    action="ai.key_remove",
                    target_type="ai_setting",
                    target_id=row["id"],
                    before_state={"key_set": True},
                    after_state={"key_set": False},
                )
            return self.settings._view(connection, actor.workspace_id, now)


__all__ = [
    "PINNED_GEMINI_MODEL",
    "ConnectBody",
    "ConsentToNotice",
    "ProviderConnectionService",
    "RotateBody",
]
