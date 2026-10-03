"""Validated, immutable workspace configuration reads and writes."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any, Literal, cast
from uuid import UUID

import sqlalchemy as sa
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    model_validator,
)
from reconbench_core.recon.registry import rules_hash_for_engine
from sqlalchemy.engine import Engine

from reconbench_app.audit import write_audit_event
from reconbench_app.authorization import ActorContext, requires
from reconbench_app.db.ids import new_id
from reconbench_app.db.metadata import metadata
from reconbench_app.db.repository import VersionConflict


def _decimal_string(value: object) -> Decimal:
    if not isinstance(value, str):
        raise ValueError("decimal values must be JSON strings")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("value must be a decimal string") from exc
    if not result.is_finite():
        raise ValueError("decimal values must be finite")
    return result


DecimalString = Annotated[
    Decimal,
    BeforeValidator(_decimal_string, json_schema_input_type=str),
]
NonNegativeDecimal = Annotated[DecimalString, Field(ge=0)]
ProbabilityString = Annotated[DecimalString, Field(ge=0, le=1)]


class _ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ToleranceValuesBody(_ConfigModel):
    pct: NonNegativeDecimal
    abs: NonNegativeDecimal


class ToleranceBody(ToleranceValuesBody):
    by_vendor: dict[str, ToleranceValuesBody] = Field(default_factory=dict)
    by_item: dict[str, ToleranceValuesBody] = Field(default_factory=dict)


class TolerancesBody(_ConfigModel):
    limit_mode: Literal["or", "both"]
    qty_vs_grn: ToleranceBody
    cum_qty_vs_po: ToleranceBody
    unit_rate: ToleranceBody
    total_vs_expected: ToleranceBody
    arithmetic: ToleranceBody
    cum_value_vs_po: ToleranceBody


class ThresholdsBody(_ConfigModel):
    extraction_gate: ProbabilityString
    link_auto_accept: ProbabilityString
    vendor_fuzzy_merge: ProbabilityString
    vendor_fuzzy_suggest: ProbabilityString
    item_aligned: ProbabilityString
    item_review: ProbabilityString
    item_single_line_explicit: ProbabilityString
    spec_conflict_cap: ProbabilityString
    step3_min_score: ProbabilityString
    step3_min_lead: ProbabilityString


class LadderConfidenceBody(_ConfigModel):
    labeled: ProbabilityString
    reverse: ProbabilityString
    via_grn: ProbabilityString
    manual: ProbabilityString


class Step3WeightsBody(_ConfigModel):
    line_coverage: NonNegativeDecimal
    qty_feasible: NonNegativeDecimal
    rate_agree: NonNegativeDecimal
    date_ok: NonNegativeDecimal
    grn_support: NonNegativeDecimal
    buyer_match: NonNegativeDecimal


class AlignWeightsBody(_ConfigModel):
    desc: NonNegativeDecimal
    code: NonNegativeDecimal
    hsn: NonNegativeDecimal
    uom: NonNegativeDecimal


class WeightsBody(_ConfigModel):
    step3: Step3WeightsBody
    align: AlignWeightsBody


class DuplicatePolicyBody(_ConfigModel):
    suspect_days: int = Field(ge=0)
    suspect_edit_distance: int = Field(ge=0)


class ReverseLookupBody(_ConfigModel):
    min_canonical_len: int = Field(ge=1)
    window_runs: int = Field(ge=1, le=6)


class PoliciesBody(_ConfigModel):
    payable_rate_mode: Literal["pay_at_po_rate", "hold"]
    date_order_level: Literal["fail", "warn"]
    pending_ageing_days: int = Field(ge=0)
    duplicate: DuplicatePolicyBody
    reverse_lookup: ReverseLookupBody
    nonexact_tail_min_digits: int = Field(ge=1)


class SynonymsBody(_ConfigModel):
    po_refs: list[str]
    grn_refs: list[str]
    supplier_invoice_no: list[str]
    invoice_no: list[str]
    vendor_name: list[str]
    buyer: list[str]
    quantity: list[str]
    rate_amount: list[str]
    totals: list[str]
    dates: list[str]
    tax: list[str]


class UomConversionBody(_ConfigModel):
    item_code: str
    from_: str = Field(alias="from")
    to: str
    factor: NonNegativeDecimal


class UomBody(_ConfigModel):
    aliases: dict[str, str]
    conversions: list[UomConversionBody]


class BuyerEntityBody(_ConfigModel):
    gstin: str
    name: str


class GstSlabBody(_ConfigModel):
    rate: NonNegativeDecimal
    from_: date | None = Field(alias="from")
    to: date | None


class PaymentTermBody(_ConfigModel):
    label: str
    days: int = Field(ge=0)


class VendorOverrideBody(_ConfigModel):
    vendor_id: UUID
    tolerances: dict[str, ToleranceValuesBody]


class ItemOverrideBody(_ConfigModel):
    item_code: str
    vendor_id: UUID | None
    tolerances: dict[str, ToleranceValuesBody]


class OverridesBody(_ConfigModel):
    vendors: list[VendorOverrideBody]
    items: list[ItemOverrideBody]


class EngineBody(_ConfigModel):
    tolerances: TolerancesBody
    thresholds: ThresholdsBody
    ladder_confidence: LadderConfidenceBody
    weights: WeightsBody
    policies: PoliciesBody
    synonyms: SynonymsBody
    uom: UomBody
    charge_keywords: list[str]
    buyer_entities: list[BuyerEntityBody]
    gst_slabs: list[GstSlabBody]
    payment_terms: list[PaymentTermBody]
    overrides: OverridesBody

    @model_validator(mode="after")
    def validate_interdependent_rules(self) -> EngineBody:
        if not (self.thresholds.vendor_fuzzy_suggest < self.thresholds.vendor_fuzzy_merge):
            raise ValueError("vendor fuzzy suggestion threshold must be below auto-merge")
        if not (
            self.thresholds.item_aligned
            > self.thresholds.item_review
            > self.thresholds.item_single_line_explicit
        ):
            raise ValueError("item thresholds must descend aligned > review > explicit")
        if self.thresholds.spec_conflict_cap > self.thresholds.item_aligned:
            raise ValueError("spec conflict cap must not exceed the item-aligned threshold")
        if sum(self.weights.step3.model_dump().values(), Decimal("0")) != Decimal("1"):
            raise ValueError("Step 3 weights must sum to 1")
        if sum(self.weights.align.model_dump().values(), Decimal("0")) != Decimal("1"):
            raise ValueError("item alignment weights must sum to 1")
        return self


class LimitsBody(_ConfigModel):
    max_file_mb: Annotated[StrictInt, Field(ge=1)]
    max_pages: Annotated[StrictInt, Field(ge=1)]
    zip_max_entries: Annotated[StrictInt, Field(ge=1)]
    zip_max_uncompressed_gb: Annotated[StrictInt, Field(ge=1)]
    zip_max_ratio: Annotated[StrictInt, Field(ge=1)]


class UiBody(_ConfigModel):
    poll_seconds: Annotated[StrictInt, Field(ge=1)]
    lakh_crore_in_excel: StrictBool


class RetentionBody(_ConfigModel):
    log_days: Annotated[StrictInt, Field(ge=0)] | None
    export_days: Annotated[StrictInt, Field(ge=0)] | None
    document_days: Annotated[StrictInt, Field(ge=0)] | None


class SecurityBody(_ConfigModel):
    session_idle_minutes: Annotated[StrictInt, Field(gt=0)]
    session_absolute_hours: Annotated[StrictInt, Field(gt=0)]
    login_email_max_failures: Annotated[StrictInt, Field(gt=0)]
    login_email_window_minutes: Annotated[StrictInt, Field(gt=0)]
    login_email_lockout_minutes: Annotated[StrictInt, Field(gt=0)]
    login_ip_max_failures: Annotated[StrictInt, Field(gt=0)]
    login_ip_window_minutes: Annotated[StrictInt, Field(gt=0)]
    login_ip_throttle_minutes: Annotated[StrictInt, Field(gt=0)]


class AppBody(_ConfigModel):
    limits: LimitsBody
    ui: UiBody
    retention: RetentionBody
    security: SecurityBody


class ConfigurationBody(_ConfigModel):
    """Complete Schema §5.1 body; `app` and `engine` are versioned together."""

    engine: EngineBody
    app: AppBody


class ConfigurationResponse(_ConfigModel):
    config_version_id: UUID
    seq: int
    body: dict[str, Any]
    engine_hash: str
    rules_hash: str
    body_hash: str
    created_at: datetime
    change_note: str | None


class ReasonCodeItem(_ConfigModel):
    code: str
    level: Literal["block", "fail", "review", "pending", "warn", "info"]
    stage: Literal["S1", "S2", "S3", "S4", "S5", "S6", "S7"]
    title: str
    message: str
    suggested_action: str
    overridable: bool
    level_configurable: bool
    is_proposed: bool
    is_active: bool


class ReasonCodeCatalog(_ConfigModel):
    items: list[ReasonCodeItem]


def canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _body_value(body: ConfigurationBody) -> dict[str, Any]:
    return body.model_dump(mode="json", by_alias=True, exclude_unset=True)


class ConfigurationService:
    """Read active settings and atomically append/activate a new immutable version."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    @staticmethod
    def _current(connection: sa.Connection, workspace_id: UUID) -> dict[str, Any] | None:
        active = metadata.tables["config_active"]
        version = metadata.tables["config_version"]
        row = (
            connection.execute(
                sa.select(
                    active.c.id.label("active_id"),
                    active.c.version.label("active_version"),
                    version.c.id.label("config_version_id"),
                    version.c.seq,
                    version.c.body,
                    version.c.engine_hash,
                    version.c.body_hash,
                    version.c.created_at,
                    version.c.change_note,
                )
                .select_from(
                    active.join(
                        version,
                        sa.and_(
                            active.c.workspace_id == version.c.workspace_id,
                            active.c.config_version_id == version.c.id,
                        ),
                    )
                )
                .where(active.c.workspace_id == workspace_id)
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        body = cast(dict[str, Any], row["body"])
        return {
            "active_id": row["active_id"],
            "version": int(row["active_version"]),
            "config_version_id": row["config_version_id"],
            "seq": int(row["seq"]),
            "body": body,
            "engine_hash": str(row["engine_hash"]),
            "rules_hash": rules_hash_for_engine(body["engine"]),
            "body_hash": str(row["body_hash"]),
            "created_at": row["created_at"],
            "change_note": row["change_note"],
        }

    @staticmethod
    def _response(current: dict[str, Any]) -> ConfigurationResponse:
        return ConfigurationResponse(
            config_version_id=current["config_version_id"],
            seq=current["seq"],
            body=current["body"],
            engine_hash=current["engine_hash"],
            rules_hash=current["rules_hash"],
            body_hash=current["body_hash"],
            created_at=current["created_at"],
            change_note=current["change_note"],
        )

    @requires("data.view")
    def get(self, workspace_id: UUID) -> tuple[ConfigurationResponse, int]:
        with self.engine.connect() as connection:
            current = self._current(connection, workspace_id)
        if current is None:
            raise VersionConflict(None)
        return self._response(current), current["version"]

    @requires("data.view")
    def list_reason_codes(self, workspace_id: UUID) -> list[ReasonCodeItem]:
        table = metadata.tables["reason_code"]
        with self.engine.connect() as connection:
            rows = (
                connection.execute(
                    sa.select(
                        table.c.code,
                        table.c.level,
                        table.c.stage,
                        table.c.title,
                        table.c.message,
                        table.c.suggested_action,
                        table.c.overridable,
                        table.c.level_configurable,
                        table.c.is_proposed,
                        table.c.is_active,
                    )
                    .where(table.c.workspace_id == workspace_id)
                    .order_by(table.c.stage, table.c.code)
                )
                .mappings()
                .all()
            )
        return [ReasonCodeItem.model_validate(dict(row)) for row in rows]

    @requires("settings.change")
    def update(
        self,
        actor: ActorContext,
        *,
        expected_version: int,
        body: ConfigurationBody,
    ) -> tuple[ConfigurationResponse, int]:
        body_json = _body_value(body)
        active = metadata.tables["config_active"]
        version = metadata.tables["config_version"]
        with self.engine.begin() as connection:
            locked = (
                connection.execute(
                    sa.select(active.c.id, active.c.version, active.c.config_version_id)
                    .where(active.c.workspace_id == actor.workspace_id)
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if locked is None:
                raise VersionConflict(None)
            current = self._current(connection, actor.workspace_id)
            if current is None:
                raise VersionConflict(None)
            if int(locked["version"]) != expected_version:
                raise VersionConflict(
                    {
                        "version": current["version"],
                        "etag": f'"{current["version"]}"',
                        "config": self._response(current).model_dump(mode="json"),
                    }
                )

            config_id = new_id()
            new_seq = int(current["seq"]) + 1
            engine_hash = canonical_hash(body_json["engine"])
            body_hash = canonical_hash(body_json)
            connection.execute(
                sa.insert(version).values(
                    id=config_id,
                    workspace_id=actor.workspace_id,
                    seq=new_seq,
                    parent_version_id=current["config_version_id"],
                    body=body_json,
                    engine_hash=engine_hash,
                    body_hash=body_hash,
                    change_note="Configuration updated",
                    created_by=actor.actor_id,
                )
            )
            connection.execute(
                sa.update(active)
                .where(
                    active.c.id == locked["id"],
                    active.c.workspace_id == actor.workspace_id,
                    active.c.version == expected_version,
                )
                .values(
                    config_version_id=config_id,
                    activated_by=actor.actor_id,
                    activated_at=sa.func.now(),
                    version=active.c.version + 1,
                )
            )
            created_at = connection.scalar(
                sa.select(version.c.created_at).where(version.c.id == config_id)
            )
            write_audit_event(
                connection,
                actor,
                action="config.save",
                target_type="config",
                target_id=config_id,
                before_state={
                    "config_version_id": str(current["config_version_id"]),
                    "seq": current["seq"],
                    "body_hash": current["body_hash"],
                },
                after_state={
                    "config_version_id": str(config_id),
                    "seq": new_seq,
                    "body_hash": body_hash,
                    "engine_hash": engine_hash,
                    "rules_hash": rules_hash_for_engine(body_json["engine"]),
                },
            )

            result = {
                "config_version_id": config_id,
                "seq": new_seq,
                "body": body_json,
                "engine_hash": engine_hash,
                "rules_hash": rules_hash_for_engine(body_json["engine"]),
                "body_hash": body_hash,
                "created_at": created_at,
                "change_note": "Configuration updated",
                "version": expected_version + 1,
            }
        return self._response(result), expected_version + 1


__all__ = [
    "AppBody",
    "ConfigurationBody",
    "ConfigurationResponse",
    "ConfigurationService",
    "EngineBody",
    "ReasonCodeCatalog",
    "ReasonCodeItem",
    "canonical_hash",
]
