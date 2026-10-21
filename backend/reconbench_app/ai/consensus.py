"""Dark AC-6b crop checks and stored per-field consensus provenance."""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, cast
from uuid import UUID

import sqlalchemy as sa
from pydantic import ValidationError
from reconbench_core.extraction.consensus import (
    CONSENSUS_VERSION,
    ConsensusPolicy,
    ConsensusResult,
    CropCheck,
    CropNeed,
    CropOutcome,
    FieldKey,
    FieldType,
    ReaderKind,
    crop_needs,
    crop_outcome,
    decide,
)
from reconbench_core.parsers.types import ParserHints
from reconbench_core.schema import (
    DocType,
    Document,
    FieldSource,
    GRNHeader,
    InvoiceHeader,
    LineType,
    POHeader,
    POStatus,
    ReasonCode,
)
from sqlalchemy.engine import Connection, Engine, RowMapping

from reconbench_app.ai import vision
from reconbench_app.ai.consent import AUTO_VERIFY_FIELD_TYPES
from reconbench_app.ai.contracts import DocTypeName, InvalidOutput, VisionOutput
from reconbench_app.ai.runtime_checks import load_settings
from reconbench_app.db.ids import new_id
from reconbench_app.db.metadata import metadata
from reconbench_app.extraction.vision_pages import CropReadPage, reread_vision_crops_isolated
from reconbench_app.json_serialization import to_jsonable

_LOGGER = logging.getLogger(__name__)

RUNS = metadata.tables["consensus_run"]
DECISIONS = metadata.tables["consensus_decision"]
REQUESTS = metadata.tables["ai_request"]
CACHE = metadata.tables["ai_cache"]
VERSIONS = metadata.tables["document_version"]
DOCUMENTS = metadata.tables["document"]
LINES = metadata.tables["doc_line"]
EVIDENCE = metadata.tables["field_evidence"]
TEXT_PAGES = metadata.tables["document_text"]

ConsensusJobDeferrer = Callable[[Connection, UUID, UUID], None]
ConsensusEnqueuer = Callable[[Connection, UUID, UUID, UUID, UUID], bool]
ConsensusFollowup = Callable[[Connection, UUID, UUID, UUID, str], None]

_HEADER_COLUMNS: Mapping[str, str] = {
    "doc_no": "doc_no",
    "doc_date": "doc_date",
    "vendor_name": "vendor_name",
    "vendor_gstin": "vendor_gstin",
    "buyer_gstin": "buyer_gstin",
    "currency": "currency",
    "taxable_total": "taxable_total",
    "cgst": "cgst_total",
    "sgst": "sgst_total",
    "igst": "igst_total",
    "round_off": "round_off",
    "grand_total": "grand_total",
    "revision": "revision",
    "status": "po_status",
    "ship_to": "ship_to",
    "payment_terms": "payment_terms_text",
    "delivery_date": "delivery_date",
    "po_refs": "po_refs",
    "grn_refs": "grn_refs",
    "challan_no": "challan_no",
    "supplier_invoice_no": "supplier_invoice_no",
    "e_way_bill_no": "e_way_bill_no",
    "irn": "irn",
    "place_of_supply": "place_of_supply",
}
_LINE_COLUMNS: Mapping[str, str] = {
    "line_type": "line_type",
    "description": "description",
    "item_code": "item_code",
    "hsn_sac": "hsn_sac",
    "uom": "uom_as_read",
    "quantity": "quantity",
    "received_qty": "received_qty",
    "rejected_qty": "rejected_qty",
    "accepted_qty": "accepted_qty",
    "rate": "rate",
    "discount": "discount",
    "amount": "amount",
    "tax_rate": "tax_rate",
    "cgst_amount": "cgst_amount",
    "sgst_amount": "sgst_amount",
    "igst_amount": "igst_amount",
    "po_ref": "po_ref",
    "po_line_ref": "po_line_ref",
}


def enqueue_consensus_run(
    connection: Connection,
    *,
    workspace_id: UUID,
    document_version_id: UUID,
    ai_request_id: UUID,
    ai_call_id: UUID,
    defer_job: ConsensusJobDeferrer,
) -> bool:
    """Create a run and its parse-queue job in the caller's transaction."""

    existing = connection.execute(
        sa.select(RUNS.c.status).where(
            RUNS.c.workspace_id == workspace_id,
            RUNS.c.ai_request_id == ai_request_id,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return str(existing) in {"queued", "succeeded"}
    run_id = new_id()
    connection.execute(
        sa.insert(RUNS).values(
            id=run_id,
            workspace_id=workspace_id,
            document_version_id=document_version_id,
            ai_request_id=ai_request_id,
            ai_call_id=ai_call_id,
            consensus_version=CONSENSUS_VERSION,
            status="queued",
        )
    )
    defer_job(connection, workspace_id, run_id)
    return True


def _enum_or_value(enum_type: Any, value: Any) -> Any:
    if value is None or isinstance(value, enum_type):
        return value
    return enum_type(str(value))


def _value_payload(
    value: Any,
    evidence: RowMapping | Mapping[str, Any] | None,
    *,
    default_source: FieldSource,
    default_confidence: Decimal,
    enum_type: Any | None = None,
) -> dict[str, Any]:
    if enum_type is not None:
        value = _enum_or_value(enum_type, value)
    elif isinstance(value, list):
        value = tuple(value)
    source = default_source
    confidence = default_confidence
    evidence_payload: dict[str, Any] = {}
    if evidence is not None:
        source = _enum_or_value(FieldSource, evidence["source"])
        confidence = cast(Decimal, evidence["confidence"])
        for name in ("page", "snippet"):
            if evidence.get(name) is not None:
                evidence_payload[name] = evidence[name]
        if evidence.get("bbox") is not None:
            evidence_payload["bbox"] = tuple(evidence["bbox"])
    return {
        "value": value,
        "confidence": confidence,
        "source": source,
        "evidence": evidence_payload,
    }


def document_from_rows(
    document_row: RowMapping,
    version_row: RowMapping,
    line_rows: Sequence[RowMapping],
    evidence_rows: Sequence[RowMapping],
) -> Document:
    """Rebuild the immutable parser document from its version and evidence rows."""

    doc_type = DocType(str(document_row["doc_type"]))
    header_type = cast(
        Any,
        {
            DocType.PO: POHeader,
            DocType.GRN: GRNHeader,
            DocType.INVOICE: InvoiceHeader,
        }[doc_type],
    )
    default_source = (
        FieldSource.MANUAL
        if str(version_row["extraction_method"]) == "manual"
        else FieldSource.RULE
    )
    default_confidence = cast(Decimal | None, version_row["extraction_conf"]) or Decimal("0.65")
    header_evidence = {
        str(row["field_name"]): row
        for row in evidence_rows
        if row["document_version_id"] == version_row["id"]
    }
    header: dict[str, Any] = {"doc_type": doc_type}
    for field_name, column in _HEADER_COLUMNS.items():
        if field_name not in header_type.model_fields:
            continue
        value = version_row[column]
        if value is None:
            continue
        status_enum_type = POStatus if field_name == "status" else None
        header[field_name] = _value_payload(
            value,
            header_evidence.get(field_name),
            default_source=default_source,
            default_confidence=default_confidence,
            enum_type=status_enum_type,
        )
    extras = version_row["extras"] if isinstance(version_row["extras"], Mapping) else {}
    header_extras = extras.get("header", {})
    header["extras"] = dict(header_extras) if isinstance(header_extras, Mapping) else {}

    line_evidence = {
        (cast(UUID, row["doc_line_id"]), str(row["field_name"])): row
        for row in evidence_rows
        if row["doc_line_id"] is not None
    }
    lines: list[dict[str, Any]] = []
    for row in line_rows:
        line: dict[str, Any] = {"line_no": int(row["line_no"])}
        line_id = cast(UUID, row["id"])
        for field_name, column in _LINE_COLUMNS.items():
            value = row[column]
            if field_name == "uom" and value is None:
                value = row["uom"]
            if value is None:
                continue
            line_enum_type = LineType if field_name == "line_type" else None
            line[field_name] = _value_payload(
                value,
                line_evidence.get((line_id, field_name)),
                default_source=default_source,
                default_confidence=default_confidence,
                enum_type=line_enum_type,
            )
        line_extras = row["extras"] if isinstance(row["extras"], Mapping) else {}
        line["extras"] = dict(line_extras)
        lines.append(line)

    locator = version_row["locator"]
    source_locator = locator.get("source") if isinstance(locator, Mapping) else None
    reason_codes = tuple(ReasonCode(str(item)) for item in version_row["verification_reasons"])
    return Document.model_validate(
        {
            "header": header,
            "lines": lines,
            "natural_key": document_row["natural_key"],
            "source_locator": source_locator,
            "needs_verification": bool(version_row["needs_verification"]),
            "reason_codes": reason_codes,
        }
    )


def _masked(page: vision.PageRow, box: tuple[int, int, int, int]) -> bool:
    top, left, bottom, right = box
    raw_boxes = page.mask.get("boxes", [])
    for raw in raw_boxes:
        if not isinstance(raw, list) or len(raw) != 4:
            continue
        mask_top, mask_left, mask_bottom, mask_right = (int(value) for value in raw)
        if max(top, mask_top) < min(bottom, mask_bottom) and max(left, mask_left) < min(
            right, mask_right
        ):
            return True
    return False


def _crop_checks(
    pages: Sequence[vision.PageRow],
    needs: Sequence[CropNeed],
    *,
    languages: tuple[str, ...],
) -> tuple[dict[FieldKey, CropCheck], dict[FieldKey, list[dict[str, object]]]]:
    page_by_number = {page.page: page for page in pages}
    checks: dict[FieldKey, CropCheck] = {}
    attempts: dict[FieldKey, list[dict[str, object]]] = {}
    readable: list[CropNeed] = []
    for need in needs:
        page = page_by_number.get(need.page)
        if page is None or page.data is None:
            checks[need.key] = CropCheck(CropOutcome.UNAVAILABLE)
            attempts[need.key] = [_crop_attempt(1, need, CropOutcome.UNAVAILABLE, None)]
        elif _masked(page, need.box):
            checks[need.key] = CropCheck(CropOutcome.MASKED)
            attempts[need.key] = [_crop_attempt(1, need, CropOutcome.MASKED, None)]
        else:
            readable.append(need)
    rendered = [
        CropReadPage(page.page, page.data, page.sha256, page.width, page.height)
        for page in pages
        if page.data is not None and any(need.page == page.page for need in readable)
    ]
    read_text = reread_vision_crops_isolated(rendered, readable, languages=languages, round_no=1)
    retry: list[CropNeed] = []
    for need in readable:
        text = read_text.get(need.key.text()) if read_text is not None else None
        outcome = _crop_outcome(need, text)
        checks[need.key] = CropCheck(outcome, (text or "")[:200])
        attempts[need.key] = [_crop_attempt(1, need, outcome, text)]
        if outcome is not CropOutcome.AGREE:
            retry.append(need)

    if retry:
        retry_text = reread_vision_crops_isolated(rendered, retry, languages=languages, round_no=2)
        for need in retry:
            text = retry_text.get(need.key.text()) if retry_text is not None else None
            outcome = _crop_outcome(need, text)
            first = checks[need.key]
            attempts[need.key].append(_crop_attempt(2, need, outcome, text))
            final_outcome = (
                CropOutcome.DISAGREE
                if first.outcome is CropOutcome.DISAGREE or outcome is CropOutcome.DISAGREE
                else CropOutcome.AGREE
                if outcome is CropOutcome.AGREE
                else CropOutcome.EMPTY
                if first.outcome is CropOutcome.EMPTY or outcome is CropOutcome.EMPTY
                else CropOutcome.UNAVAILABLE
            )
            checks[need.key] = CropCheck(final_outcome, (text or first.text)[:200])
    return checks, attempts


def _crop_outcome(need: CropNeed, text: str | None) -> CropOutcome:
    if text is None:
        return CropOutcome.UNAVAILABLE
    if not text.strip():
        return CropOutcome.EMPTY
    return crop_outcome(need.kind, need.value, text, mode=need.mode)


def _crop_attempt(
    round_no: int, need: CropNeed, outcome: CropOutcome, text: str | None
) -> dict[str, object]:
    if round_no == 1:
        strategy = "psm7_line" if need.mode == "line" else "psm6_block"
    elif need.kind == "gstin":
        strategy = "psm7_gstin_whitelist"
    else:
        strategy = "psm11_block" if need.mode == "block" else "psm6_line"
    return {
        "round": round_no,
        "strategy": strategy,
        "outcome": outcome.value,
        "text": text[:200] if text is not None else None,
    }


class ConsensusService:
    """Run bounded consensus, persist provenance and apply only D42 opt-ins."""

    def __init__(
        self,
        engine: Engine,
        *,
        ocr_languages: tuple[str, ...] = ("eng",),
        enqueue_followup: ConsensusFollowup | None = None,
    ) -> None:
        self.engine = engine
        self.ocr_languages = ocr_languages
        self.enqueue_followup = enqueue_followup

    def _fail(self, workspace_id: UUID, consensus_run_id: UUID, error_code: str) -> None:
        with self.engine.begin() as connection:
            run = (
                connection.execute(
                    sa.select(RUNS)
                    .where(RUNS.c.workspace_id == workspace_id, RUNS.c.id == consensus_run_id)
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if run is None or run["status"] != "queued":
                return
            connection.execute(
                sa.update(RUNS)
                .where(RUNS.c.workspace_id == workspace_id, RUNS.c.id == consensus_run_id)
                .values(status="failed", error_code=error_code[:80], finished_at=sa.func.now())
            )
            vision.purge_version_pages(
                connection, workspace_id, cast(UUID, run["document_version_id"]), datetime.now(UTC)
            )

    def _process(self, workspace_id: UUID, consensus_run_id: UUID) -> str:
        inputs = self._load(workspace_id, consensus_run_id)
        if inputs is None:
            return self._finish_non_success(workspace_id, consensus_run_id, "stale")
        run, request, document_row, version_row, line_rows, evidence_rows, pages, response = inputs
        if not pages or vision.fingerprint(pages) != str(request["subject_version"]):
            return self._finish_non_success(workspace_id, consensus_run_id, "stale")
        try:
            document = document_from_rows(document_row, version_row, line_rows, evidence_rows)
            with self.engine.connect() as connection:
                buyers, rates = vision.party_hints(connection, workspace_id)
                context = vision.context_for(
                    connection,
                    workspace_id,
                    cast(UUID, version_row["id"]),
                    cast(DocTypeName, document_row["doc_type"]),
                    pages,
                )
            validation = vision.validate_vision(VisionOutput.model_validate(response), context)
        except (InvalidOutput, ValidationError, ValueError, TypeError):
            _LOGGER.info("consensus run unavailable: stored vision answer no longer validates")
            return self._finish_non_success(
                workspace_id, consensus_run_id, "failed", "VISION_ANSWER_INVALID"
            )

        structured = str(version_row["extraction_method"]) in {"xml_profile", "csv_mapping"}
        source_hint = document.header.extras.get("source")
        rules_source = (
            ReaderKind.OCR
            if source_hint == "ocr"
            or (source_hint is None and version_row["extraction_method"] == "pdf_ocr")
            else ReaderKind.TEXT_LAYER
        )
        with self.engine.connect() as connection:
            settings = load_settings(connection, workspace_id)
        selected = tuple(
            field_type
            for field_type in (settings.auto_verify_field_types if settings else ())
            if field_type in AUTO_VERIFY_FIELD_TYPES
        )
        if settings is None or not settings.consent_current:
            selected = ()
        enabled_types = frozenset(FieldType(field_type) for field_type in selected)
        policy = ConsensusPolicy(
            auto_verify=bool(enabled_types),
            reopen=bool(enabled_types),
            field_types=enabled_types,
            reopen_field_types=enabled_types,
            require_crop=True,
            as_of=cast(datetime, version_row["created_at"]).date(),
        )
        hints = ParserHints(
            doc_type=DocType(str(document_row["doc_type"])), buyer_entities=buyers, gst_rates=rates
        )
        first = decide(
            document,
            vision=validation.check,
            crops=None,
            hints=hints,
            policy=policy,
            structured=structured,
            rules_source=rules_source,
        )
        needs = crop_needs(first)
        crops, crop_attempts = _crop_checks(pages, needs, languages=self.ocr_languages)
        result: ConsensusResult = decide(
            document,
            vision=validation.check,
            crops=crops,
            hints=hints,
            policy=policy,
            structured=structured,
            rules_source=rules_source,
        )
        if any(
            decision.auto_verified and decision.field_type not in enabled_types
            for decision in result.decisions
        ):
            raise RuntimeError("consensus applied a field type outside the workspace opt-in")
        self._persist(
            workspace_id,
            consensus_run_id,
            cast(UUID, version_row["id"]),
            result,
            crop_attempts=crop_attempts,
            selected_types=enabled_types,
        )
        return "succeeded"

    def _load(self, workspace_id: UUID, consensus_run_id: UUID) -> tuple[Any, ...] | None:
        with self.engine.connect() as connection:
            run = (
                connection.execute(
                    sa.select(RUNS).where(
                        RUNS.c.workspace_id == workspace_id, RUNS.c.id == consensus_run_id
                    )
                )
                .mappings()
                .one_or_none()
            )
            if run is None or run["status"] != "queued":
                return None
            request = (
                connection.execute(
                    sa.select(REQUESTS).where(
                        REQUESTS.c.workspace_id == workspace_id,
                        REQUESTS.c.id == run["ai_request_id"],
                    )
                )
                .mappings()
                .one_or_none()
            )
            if (
                request is None
                or request["request_state"] != "succeeded"
                or request["apply_outcome"] != "applied"
                or request["cache_key"] is None
                or request["subject_id"] != run["document_version_id"]
            ):
                return None
            document_row = (
                connection.execute(
                    sa.select(DOCUMENTS).where(
                        DOCUMENTS.c.workspace_id == workspace_id,
                        DOCUMENTS.c.current_version_id == run["document_version_id"],
                        DOCUMENTS.c.withdrawn_at.is_(None),
                    )
                )
                .mappings()
                .one_or_none()
            )
            if document_row is None:
                return None
            version_row = (
                connection.execute(
                    sa.select(VERSIONS).where(
                        VERSIONS.c.workspace_id == workspace_id,
                        VERSIONS.c.id == run["document_version_id"],
                    )
                )
                .mappings()
                .one_or_none()
            )
            if version_row is None:
                return None
            line_rows = (
                connection.execute(
                    sa.select(LINES)
                    .where(
                        LINES.c.workspace_id == workspace_id,
                        LINES.c.document_version_id == run["document_version_id"],
                    )
                    .order_by(LINES.c.line_no)
                )
                .mappings()
                .all()
            )
            line_ids = [row["id"] for row in line_rows]
            evidence_query = sa.select(EVIDENCE).where(
                EVIDENCE.c.workspace_id == workspace_id,
                sa.or_(
                    EVIDENCE.c.document_version_id == run["document_version_id"],
                    EVIDENCE.c.doc_line_id.in_(line_ids) if line_ids else sa.false(),
                ),
            )
            evidence_rows = connection.execute(evidence_query).mappings().all()
            pages = vision.load_pages(
                connection, workspace_id, cast(UUID, run["document_version_id"]), with_bytes=True
            )
            response = connection.scalar(
                sa.select(CACHE.c.response).where(
                    CACHE.c.workspace_id == workspace_id,
                    CACHE.c.cache_key == request["cache_key"],
                )
            )
            if not isinstance(response, Mapping):
                return None
            return (
                dict(run),
                dict(request),
                dict(document_row),
                dict(version_row),
                [dict(row) for row in line_rows],
                [dict(row) for row in evidence_rows],
                pages,
                dict(response),
            )

    def _persist(
        self,
        workspace_id: UUID,
        consensus_run_id: UUID,
        document_version_id: UUID,
        result: ConsensusResult,
        *,
        crop_attempts: Mapping[FieldKey, list[dict[str, object]]],
        selected_types: frozenset[FieldType],
    ) -> None:
        with self.engine.begin() as connection:
            run = (
                connection.execute(
                    sa.select(RUNS)
                    .where(RUNS.c.workspace_id == workspace_id, RUNS.c.id == consensus_run_id)
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if run is None or run["status"] != "queued":
                return
            document_row = (
                connection.execute(
                    sa.select(DOCUMENTS)
                    .where(
                        DOCUMENTS.c.workspace_id == workspace_id,
                        DOCUMENTS.c.current_version_id == document_version_id,
                        DOCUMENTS.c.withdrawn_at.is_(None),
                    )
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if document_row is None:
                connection.execute(
                    sa.update(RUNS)
                    .where(RUNS.c.workspace_id == workspace_id, RUNS.c.id == consensus_run_id)
                    .values(status="stale", finished_at=sa.func.now())
                )
                vision.purge_version_pages(
                    connection, workspace_id, document_version_id, datetime.now(UTC)
                )
                return
            settings = load_settings(connection, workspace_id, for_update=True)
            live_types = frozenset(
                FieldType(field_type)
                for field_type in (settings.auto_verify_field_types if settings else ())
                if field_type in AUTO_VERIFY_FIELD_TYPES
            )
            if settings is None or not settings.consent_current:
                live_types = frozenset()
            if live_types != selected_types:
                connection.execute(
                    sa.update(RUNS)
                    .where(RUNS.c.workspace_id == workspace_id, RUNS.c.id == consensus_run_id)
                    .values(status="stale", finished_at=sa.func.now())
                )
                vision.purge_version_pages(
                    connection, workspace_id, document_version_id, datetime.now(UTC)
                )
                return

            version_row = (
                connection.execute(
                    sa.select(VERSIONS).where(
                        VERSIONS.c.workspace_id == workspace_id,
                        VERSIONS.c.id == document_version_id,
                    )
                )
                .mappings()
                .one()
            )
            line_rows = (
                connection.execute(
                    sa.select(LINES)
                    .where(
                        LINES.c.workspace_id == workspace_id,
                        LINES.c.document_version_id == document_version_id,
                    )
                    .order_by(LINES.c.line_no)
                )
                .mappings()
                .all()
            )
            line_ids = [row["id"] for row in line_rows]
            evidence_rows = (
                connection.execute(
                    sa.select(EVIDENCE).where(
                        EVIDENCE.c.workspace_id == workspace_id,
                        sa.or_(
                            EVIDENCE.c.document_version_id == document_version_id,
                            EVIDENCE.c.doc_line_id.in_(line_ids) if line_ids else sa.false(),
                        ),
                    )
                )
                .mappings()
                .all()
            )
            result_version_id = None
            if result.changed:
                result_version_id = _append_verified_version(
                    connection,
                    workspace_id=workspace_id,
                    document_row=document_row,
                    version_row=version_row,
                    line_rows=line_rows,
                    evidence_rows=evidence_rows,
                    result=result,
                )
            for decision in result.decisions:
                key = decision.key
                connection.execute(
                    sa.insert(DECISIONS).values(
                        id=new_id(),
                        workspace_id=workspace_id,
                        consensus_run_id=consensus_run_id,
                        document_version_id=document_version_id,
                        field_key=key.text(),
                        scope=key.scope,
                        line_no=key.line_no if key.scope == "line" else None,
                        field_name=key.name,
                        field_type=decision.field_type.value if decision.field_type else None,
                        trust_level=decision.level.value,
                        decision_rule=decision.rule.value,
                        reason=decision.reason,
                        readings=[
                            {
                                "reader": reading.reader.value,
                                "value": _json_consensus_value(reading.value),
                                "page": reading.page,
                                "box": list(reading.box) if reading.box is not None else None,
                            }
                            for reading in decision.readings
                        ],
                        supporting=[reader.value for reader in decision.supporting],
                        crop=(
                            {
                                "outcome": decision.crop.outcome.value,
                                "text": decision.crop.text,
                                "rounds": crop_attempts.get(key, []),
                            }
                            if decision.crop is not None
                            else None
                        ),
                        failed_validators=list(decision.failed_validators),
                        auto_verified=decision.auto_verified,
                        reopened=decision.reopened,
                    )
                )
            connection.execute(
                sa.update(RUNS)
                .where(RUNS.c.workspace_id == workspace_id, RUNS.c.id == consensus_run_id)
                .values(
                    status="succeeded",
                    summary=result.summary(),
                    result_version_id=result_version_id,
                    finished_at=sa.func.now(),
                )
            )
            if result_version_id is not None and self.enqueue_followup is not None:
                self.enqueue_followup(
                    connection,
                    workspace_id,
                    cast(UUID, document_row["id"]),
                    cast(UUID, version_row["source_file_id"]),
                    str(document_row["doc_type"]),
                )
            vision.purge_version_pages(
                connection, workspace_id, document_version_id, datetime.now(UTC)
            )

    def _finish_non_success(
        self,
        workspace_id: UUID,
        consensus_run_id: UUID,
        status: str,
        error_code: str | None = None,
    ) -> str:
        with self.engine.begin() as connection:
            run = (
                connection.execute(
                    sa.select(RUNS)
                    .where(RUNS.c.workspace_id == workspace_id, RUNS.c.id == consensus_run_id)
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if run is None or run["status"] != "queued":
                return str(run["status"]) if run else "missing"
            values: dict[str, Any] = {"status": status, "finished_at": sa.func.now()}
            if error_code is not None:
                values["error_code"] = error_code
            connection.execute(
                sa.update(RUNS)
                .where(RUNS.c.workspace_id == workspace_id, RUNS.c.id == consensus_run_id)
                .values(**values)
            )
            vision.purge_version_pages(
                connection, workspace_id, cast(UUID, run["document_version_id"]), datetime.now(UTC)
            )
        return status


def _append_verified_version(
    connection: Connection,
    *,
    workspace_id: UUID,
    document_row: RowMapping,
    version_row: RowMapping,
    line_rows: Sequence[RowMapping],
    evidence_rows: Sequence[RowMapping],
    result: ConsensusResult,
) -> UUID:
    """Append a same-value version carrying only the consensus verification state."""

    document_id = cast(UUID, document_row["id"])
    previous_version_id = cast(UUID, version_row["id"])
    version_id = new_id()
    latest_seq = connection.scalar(
        sa.select(sa.func.max(VERSIONS.c.seq)).where(
            VERSIONS.c.workspace_id == workspace_id,
            VERSIONS.c.document_id == document_id,
        )
    )
    new_version = dict(version_row)
    extras = dict(new_version.get("extras") or {})
    extras["header"] = dict(result.document.header.extras)
    new_version.update(
        id=version_id,
        seq=int(latest_seq or 0) + 1,
        derived_from_version_id=previous_version_id,
        created_by=None,
        extraction_conf=result.document.confidence,
        needs_verification=result.document.needs_verification,
        verification_reasons=[reason.value for reason in result.document.reason_codes],
        extras=extras,
    )
    new_version.pop("created_at", None)

    old_line_by_id = {cast(UUID, row["id"]): row for row in line_rows}
    new_line_ids: dict[UUID, UUID] = {}
    copied_lines: list[dict[str, Any]] = []
    for old_row in line_rows:
        row = dict(old_row)
        old_id = cast(UUID, row["id"])
        new_id_value = new_id()
        new_line_ids[old_id] = new_id_value
        row.update(id=new_id_value, document_version_id=version_id)
        copied_lines.append(row)

    changed_values: dict[tuple[str, int | None, str], Any] = {}
    for decision in result.decisions:
        if not (decision.auto_verified or decision.reopened):
            continue
        field_value = _result_field_value(result.document, decision.key)
        if field_value is None:
            continue
        changed_values[
            (
                decision.key.scope,
                decision.key.line_no if decision.key.scope == "line" else None,
                decision.key.name,
            )
        ] = field_value

    old_line_by_number = {int(row["line_no"]): row for row in line_rows}
    copied_evidence: list[dict[str, Any]] = []
    copied_evidence_keys: set[tuple[str, int | None, str]] = set()
    for old_row in evidence_rows:
        row = dict(old_row)
        old_line_id = cast(UUID | None, row["doc_line_id"])
        field_key: tuple[str, int | None, str]
        if old_line_id is None:
            field_key = ("header", None, str(row["field_name"]))
            row["document_version_id"] = version_id
        else:
            old_line = old_line_by_id.get(old_line_id)
            new_line_id = new_line_ids.get(old_line_id)
            if old_line is None or new_line_id is None:
                continue
            field_key = ("line", int(old_line["line_no"]), str(row["field_name"]))
            row["doc_line_id"] = new_line_id
            row["document_version_id"] = None
        updated_value = changed_values.get(field_key)
        if updated_value is not None:
            row["confidence"] = updated_value.confidence
            copied_evidence_keys.add(field_key)
        row["id"] = new_id()
        row.pop("created_at", None)
        copied_evidence.append(row)

    for field_key, value in changed_values.items():
        if field_key in copied_evidence_keys:
            continue
        scope, line_no, field_name = field_key
        line_id = (
            new_line_ids[cast(UUID, old_line_by_number[line_no]["id"])]
            if scope == "line" and line_no is not None and line_no in old_line_by_number
            else None
        )
        if scope == "line" and line_id is None:
            continue
        evidence = value.evidence
        copied_evidence.append(
            {
                "id": new_id(),
                "workspace_id": workspace_id,
                "document_version_id": version_id if scope == "header" else None,
                "doc_line_id": line_id,
                "field_name": field_name,
                "value_text": _field_text(value.value),
                "confidence": value.confidence,
                "source": value.source.value,
                "page": evidence.page,
                "bbox": list(evidence.bbox) if evidence.bbox is not None else None,
                "snippet": evidence.snippet,
                "ai_call_id": None,
            }
        )

    version_hash_payload = result.document.model_dump(mode="json")
    encoded = json.dumps(
        to_jsonable(version_hash_payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    new_version["content_hash"] = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    connection.execute(sa.insert(VERSIONS).values(**new_version))
    if copied_lines:
        connection.execute(sa.insert(LINES), copied_lines)
    if copied_evidence:
        connection.execute(sa.insert(EVIDENCE), copied_evidence)

    pages = connection.execute(
        sa.select(TEXT_PAGES).where(
            TEXT_PAGES.c.workspace_id == workspace_id,
            TEXT_PAGES.c.document_version_id == previous_version_id,
        )
    ).mappings()
    for old_page in pages:
        row = dict(old_page)
        row["document_version_id"] = version_id
        row.pop("created_at", None)
        connection.execute(sa.insert(TEXT_PAGES).values(**row))

    connection.execute(
        sa.update(DOCUMENTS)
        .where(DOCUMENTS.c.workspace_id == workspace_id, DOCUMENTS.c.id == document_id)
        .values(
            current_version_id=version_id,
            needs_verification=result.document.needs_verification,
            updated_at=sa.func.now(),
            version=DOCUMENTS.c.version + 1,
        )
    )
    return version_id


def _result_field_value(document: Document, key: FieldKey) -> Any | None:
    if key.scope == "header":
        return getattr(document.header, key.name, None)
    line = next((item for item in document.lines if item.line_no == key.line_no), None)
    return getattr(line, key.name, None) if line is not None else None


def _field_text(value: Any) -> str:
    if isinstance(value, (tuple, list)):
        return ";".join(_field_text(item) for item in value)
    if isinstance(value, (date, Decimal)):
        return str(value)
    return str(value)


def _json_consensus_value(value: Any) -> str | list[str]:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, tuple):
        return [str(item) for item in value]
    return str(value)


__all__ = [
    "ConsensusEnqueuer",
    "ConsensusJobDeferrer",
    "ConsensusService",
    "document_from_rows",
    "enqueue_consensus_run",
]
