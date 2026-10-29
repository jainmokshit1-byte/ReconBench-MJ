"""Database adapter for deterministic vendor-to-PO link resolution."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

import sqlalchemy as sa
from reconbench_core.align import AlignmentConfig, AlignmentWeights
from reconbench_core.mapping.links import (
    MANUAL_SELECTION_EVIDENCE,
    OPENING_SUPPORT,
    FieldVerificationUpdate,
    LinkResolution,
    MappingConfig,
    MappingDocument,
    MappingPO,
    ProposedLink,
    resolve_links,
)
from reconbench_core.normalize import canonicalize_document_number
from reconbench_core.schema import (
    DocLine,
    DocType,
    Document,
    FieldEvidence,
    FieldOrigin,
    FieldSource,
    FieldValue,
    FieldVerification,
    GRNHeader,
    InvoiceHeader,
    LineType,
    LinkLevel,
    LinkMethod,
    LinkState,
    LinkType,
    POHeader,
    POStatus,
    ResolutionOutcome,
    ToleranceMode,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection, Engine, RowMapping

from reconbench_app import reading_state
from reconbench_app.audit import write_audit_event
from reconbench_app.authorization import ActorContext, requires
from reconbench_app.db.ids import new_id
from reconbench_app.db.metadata import metadata
from reconbench_app.db.repository import VersionConflict

if TYPE_CHECKING:
    from reconbench_app.ai.enqueue import AiHookEnqueuer

_LOGGER = logging.getLogger(__name__)
_PAN_PATTERN = re.compile(r"[A-Z]{5}[0-9]{4}[A-Z]")


@dataclass(frozen=True, slots=True)
class MappingSnapshot:
    """Current sources, governing POs and config exactly as one resolution sees them."""

    sources: tuple[MappingDocument, ...]
    purchase_orders: tuple[MappingPO, ...]
    config: MappingConfig
    input_hash: str


@dataclass(frozen=True, slots=True)
class MappingSummary:
    documents_seen: int
    linked: int
    suggested: int
    unlinked: int
    link_versions_written: int
    resolutions_written: int
    scopes_marked: int


def _field(value: object | None, evidence_row: RowMapping | None) -> FieldValue[Any] | None:
    if value is None:
        return None
    confidence = cast(Decimal | None, evidence_row["confidence"]) if evidence_row else None
    source = (
        FieldSource(str(evidence_row["source"])) if evidence_row is not None else FieldSource.RULE
    )
    page = int(evidence_row["page"]) if evidence_row and evidence_row["page"] else None
    bbox_value = evidence_row["bbox"] if evidence_row else None
    bbox = (
        cast(
            tuple[Decimal, Decimal, Decimal, Decimal],
            tuple(Decimal(str(item)) for item in bbox_value),
        )
        if isinstance(bbox_value, (list, tuple)) and len(bbox_value) == 4
        else None
    )
    return FieldValue(
        value=value,
        confidence=confidence,
        source=source,
        evidence=FieldEvidence(
            page=page,
            snippet=str(evidence_row["snippet"])
            if evidence_row and evidence_row["snippet"]
            else None,
            bbox=bbox,
        ),
        origin=(
            FieldOrigin(str(evidence_row["origin"]))
            if evidence_row is not None and evidence_row["origin"]
            else None
        ),
        verification=(
            FieldVerification(str(evidence_row["verification"]))
            if evidence_row is not None and evidence_row["verification"]
            else FieldVerification.NONE
        ),
        verified_by=(
            str(evidence_row["verified_by"])
            if evidence_row is not None and evidence_row["verified_by"]
            else None
        ),
        verified_at=(evidence_row["verified_at"] if evidence_row is not None else None),
    )


def _as_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(item) for item in value if item is not None)


def _json_hash(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _decimal(value: object, default: str) -> Decimal:
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal(default)


def mapping_config_from_body(body: object) -> MappingConfig:
    """Build link thresholds from an active config body (missing values use defaults)."""

    engine = body.get("engine", {}) if isinstance(body, Mapping) else {}
    if not isinstance(engine, Mapping):
        engine = {}
    thresholds = engine.get("thresholds", {})
    thresholds = thresholds if isinstance(thresholds, Mapping) else {}
    ladder = engine.get("ladder_confidence", {})
    ladder = ladder if isinstance(ladder, Mapping) else {}
    policies = engine.get("policies", {})
    policies = policies if isinstance(policies, Mapping) else {}
    reverse = policies.get("reverse_lookup", {})
    reverse = reverse if isinstance(reverse, Mapping) else {}
    tolerances = engine.get("tolerances", {})
    tolerances = tolerances if isinstance(tolerances, Mapping) else {}
    total_tolerance = tolerances.get("total_vs_expected", {})
    total_tolerance = total_tolerance if isinstance(total_tolerance, Mapping) else {}
    rate_tolerance = tolerances.get("unit_rate", {})
    rate_tolerance = rate_tolerance if isinstance(rate_tolerance, Mapping) else {}
    quantity_tolerance = tolerances.get("qty_vs_grn", {})
    quantity_tolerance = quantity_tolerance if isinstance(quantity_tolerance, Mapping) else {}
    weights = engine.get("weights", {})
    weights = weights if isinstance(weights, Mapping) else {}
    align_weights = weights.get("align", {})
    align_weights = align_weights if isinstance(align_weights, Mapping) else {}
    alignment = AlignmentConfig(
        weights=AlignmentWeights(
            desc=_decimal(align_weights.get("desc"), "0.55"),
            code=_decimal(align_weights.get("code"), "0.20"),
            hsn=_decimal(align_weights.get("hsn"), "0.15"),
            uom=_decimal(align_weights.get("uom"), "0.10"),
        ),
        item_aligned=_decimal(thresholds.get("item_aligned"), "0.80"),
        item_review=_decimal(thresholds.get("item_review"), "0.60"),
        item_single_line_explicit=_decimal(thresholds.get("item_single_line_explicit"), "0.50"),
        spec_conflict_cap=_decimal(thresholds.get("spec_conflict_cap"), "0.60"),
    )
    return MappingConfig(
        link_auto_accept=_decimal(thresholds.get("link_auto_accept"), "0.95"),
        confidence_labeled=_decimal(ladder.get("labeled"), "0.99"),
        confidence_reverse=_decimal(ladder.get("reverse"), "0.97"),
        confidence_via_grn=_decimal(ladder.get("via_grn"), "0.95"),
        confidence_manual=_decimal(ladder.get("manual"), "1.00"),
        reverse_lookup_window_runs=int(reverse.get("window_runs", 6)),
        step3_min_score=_decimal(thresholds.get("step3_min_score"), "0.80"),
        step3_min_lead=_decimal(thresholds.get("step3_min_lead"), "0.15"),
        amount_tolerance_pct=_decimal(total_tolerance.get("pct"), "1"),
        amount_tolerance_abs=_decimal(total_tolerance.get("abs"), "10"),
        rate_tolerance_pct=_decimal(rate_tolerance.get("pct"), "0.5"),
        rate_tolerance_abs=_decimal(rate_tolerance.get("abs"), "1"),
        quantity_tolerance_pct=_decimal(quantity_tolerance.get("pct"), "0"),
        quantity_tolerance_abs=_decimal(quantity_tolerance.get("abs"), "0"),
        limit_mode=ToleranceMode(str(tolerances.get("limit_mode", "or"))),
        alignment=alignment,
    )


def _make_document(
    row: RowMapping,
    line_rows: Sequence[RowMapping],
    text: str,
    header_evidence: Mapping[str, RowMapping],
    line_evidence: Mapping[UUID, Mapping[str, RowMapping]],
) -> Document:
    """Rehydrate the core model from normalized immutable database values."""

    doc_type = DocType(str(row["doc_type"]))
    common: dict[str, Any] = {
        "doc_no": _field(row["doc_no"], header_evidence.get("doc_no")),
        "doc_date": _field(row["doc_date"], header_evidence.get("doc_date")),
        "vendor_name": _field(row["vendor_name"], header_evidence.get("vendor_name")),
        "vendor_gstin": _field(row["vendor_gstin"], header_evidence.get("vendor_gstin")),
        "buyer_gstin": _field(row["buyer_gstin"], header_evidence.get("buyer_gstin")),
        "currency": _field(row["currency"], header_evidence.get("currency")),
        "taxable_total": _field(row["taxable_total"], header_evidence.get("taxable_total")),
        "cgst": _field(row["cgst_total"], header_evidence.get("cgst")),
        "sgst": _field(row["sgst_total"], header_evidence.get("sgst")),
        "igst": _field(row["igst_total"], header_evidence.get("igst")),
        "round_off": _field(row["round_off"], header_evidence.get("round_off")),
        "grand_total": _field(row["grand_total"], header_evidence.get("grand_total")),
        "extras": {},
    }
    extras = row["extras"]
    if isinstance(extras, Mapping) and isinstance(extras.get("header"), Mapping):
        common["extras"] = dict(extras["header"])
    if doc_type is DocType.PO:
        header: POHeader | GRNHeader | InvoiceHeader = POHeader(
            **common,
            revision=_field(row["revision"], header_evidence.get("revision")),
            status=(
                _field(POStatus(str(row["po_status"])), header_evidence.get("status"))
                if row["po_status"]
                else None
            ),
            ship_to=_field(row["ship_to"], header_evidence.get("ship_to")),
            payment_terms=_field(row["payment_terms_text"], header_evidence.get("payment_terms")),
            delivery_date=_field(row["delivery_date"], header_evidence.get("delivery_date")),
        )
    elif doc_type is DocType.GRN:
        header = GRNHeader(
            **common,
            po_refs=_field(_as_tuple(row["po_refs"]), header_evidence.get("po_refs")),
            challan_no=_field(row["challan_no"], header_evidence.get("challan_no")),
            supplier_invoice_no=_field(
                row["supplier_invoice_no"], header_evidence.get("supplier_invoice_no")
            ),
        )
    else:
        header = InvoiceHeader(
            **common,
            po_refs=_field(_as_tuple(row["po_refs"]), header_evidence.get("po_refs")),
            grn_refs=_field(_as_tuple(row["grn_refs"]), header_evidence.get("grn_refs")),
            challan_no=_field(row["challan_no"], header_evidence.get("challan_no")),
            e_way_bill_no=_field(row["e_way_bill_no"], header_evidence.get("e_way_bill_no")),
            irn=_field(row["irn"], header_evidence.get("irn")),
            place_of_supply=_field(row["place_of_supply"], header_evidence.get("place_of_supply")),
        )

    lines: list[DocLine] = []
    line_fields = (
        "description",
        "item_code",
        "hsn_sac",
        "uom",
        "quantity",
        "received_qty",
        "rejected_qty",
        "accepted_qty",
        "rate",
        "discount",
        "amount",
        "tax_rate",
        "cgst_amount",
        "sgst_amount",
        "igst_amount",
        "po_ref",
        "po_line_ref",
    )
    for line in line_rows:
        line_id = cast(UUID, line["id"])
        evidence = line_evidence.get(line_id, {})
        values: dict[str, Any] = {
            name: _field(line[name], evidence.get(name)) for name in line_fields
        }
        values["line_type"] = _field(LineType(str(line["line_type"])), evidence.get("line_type"))
        line_extras = line["extras"]
        values["extras"] = dict(line_extras) if isinstance(line_extras, Mapping) else {}
        lines.append(DocLine(line_no=int(line["line_no"]), **values))
    locator = row["locator"]
    source_locator = None
    if isinstance(locator, Mapping):
        raw_locator = locator.get("source")
        source_locator = str(raw_locator) if raw_locator is not None else None
    verification_reasons = row["verification_reasons"]
    return Document(
        header=header,
        lines=tuple(lines),
        natural_key=str(row["natural_key"]),
        source_locator=source_locator,
        raw_text=text or None,
        needs_verification=bool(row["needs_verification"]),
        reason_codes=tuple(verification_reasons or ()),
    )


class MappingService:
    """Resolve current invoices/GRNs to governing POs and persist append-only results."""

    def __init__(
        self,
        engine: Engine,
        queue: Any | None = None,
        ai_hooks: AiHookEnqueuer | None = None,
    ) -> None:
        self.engine = engine
        self.queue = queue
        self.ai_hooks = ai_hooks

    def _enqueue_pending_recomputes(self, workspace_id: UUID, connection: Connection) -> None:
        if self.queue is not None:
            self.queue.enqueue_pending_recomputes(
                workspace_id=workspace_id,
                connection=connection,
            )

    @requires("links.edit")
    def resolve_workspace(self, workspace_id: UUID) -> MappingSummary:
        with self.engine.begin() as connection:
            connection.execute(
                sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_name, 0))"),
                {"lock_name": f"reconbench.mapping:{workspace_id}"},
            )
            snapshot = self.load_snapshot(connection, workspace_id)
            if snapshot is None:
                return MappingSummary(0, 0, 0, 0, 0, 0, 0)
            source_documents = snapshot.sources
            input_hash = snapshot.input_hash
            resolutions = resolve_links(snapshot.sources, snapshot.purchase_orders, snapshot.config)
            system_updates = {
                (item.document_id, item.field_name, item.line_no): item
                for result in resolutions
                for item in result.field_verifications
            }
            for update in system_updates.values():
                self._persist_system_check(connection, workspace_id, update)
            result_by_id = {item.document_id: item for item in resolutions}
            current = self._current_links(connection, workspace_id)
            inserted = resolution_count = 0
            affected_documents: set[UUID] = set()
            linked = suggested = unlinked = 0
            for source in source_documents:
                result = result_by_id[source.document_id]
                linked += int(result.outcome is ResolutionOutcome.LINKED)
                suggested += int(result.outcome is ResolutionOutcome.SUGGESTED)
                unlinked += int(result.outcome is ResolutionOutcome.UNLINKED)
                wrote, affected = self._persist_resolution(
                    connection,
                    workspace_id=workspace_id,
                    source=source,
                    result=result,
                    current=current,
                    input_hash=input_hash,
                )
                inserted += wrote
                resolution_count += 1
                affected_documents.update(affected)
            scopes_marked = self._mark_affected_scopes(
                connection,
                workspace_id=workspace_id,
                document_ids=affected_documents,
            )
            self._queue_po_reranks(connection, workspace_id, snapshot)
            return MappingSummary(
                documents_seen=len(source_documents),
                linked=linked,
                suggested=suggested,
                unlinked=unlinked,
                link_versions_written=inserted,
                resolutions_written=resolution_count,
                scopes_marked=scopes_marked,
            )

    @requires("data.view")
    def list_vendors(
        self,
        workspace_id: UUID,
        *,
        search: str | None = None,
        has_issues: bool = False,
    ) -> list[dict[str, Any]]:
        vendor = metadata.tables["vendor"]
        registration = metadata.tables["vendor_gstin"]
        alias = metadata.tables["vendor_alias"]
        document = metadata.tables["document"]
        link = metadata.tables["link"]
        pointer = metadata.tables["link_current"]
        with self.engine.connect() as connection:
            vendors = list(
                connection.execute(
                    sa.select(
                        vendor.c.id,
                        vendor.c.canonical_name,
                        vendor.c.normalized_name,
                        vendor.c.name_status,
                        vendor.c.version,
                        vendor.c.pan,
                        vendor.c.anchor,
                        vendor.c.merged_into,
                    )
                    .where(vendor.c.workspace_id == workspace_id, vendor.c.merged_into.is_(None))
                    .order_by(vendor.c.normalized_name, vendor.c.id)
                ).mappings()
            )
            gstin_rows = connection.execute(
                sa.select(registration.c.vendor_id, registration.c.gstin)
                .where(registration.c.workspace_id == workspace_id)
                .order_by(registration.c.gstin)
            ).all()
            alias_rows = connection.execute(
                sa.select(alias.c.vendor_id, alias.c.alias_raw)
                .where(alias.c.workspace_id == workspace_id)
                .order_by(alias.c.alias_norm)
            ).all()
            doc_rows = connection.execute(
                sa.select(
                    document.c.id,
                    document.c.doc_type,
                    document.c.vendor_id,
                    vendor.c.merged_into.label("vendor_merged_into"),
                )
                .select_from(
                    document.outerjoin(
                        vendor,
                        sa.and_(
                            vendor.c.workspace_id == document.c.workspace_id,
                            vendor.c.id == document.c.vendor_id,
                        ),
                    )
                )
                .where(document.c.workspace_id == workspace_id, document.c.withdrawn_at.is_(None))
            ).all()
            link_rows = connection.execute(
                sa.select(link.c.from_document_id, link.c.state)
                .select_from(
                    pointer.join(
                        link,
                        sa.and_(
                            pointer.c.workspace_id == link.c.workspace_id,
                            pointer.c.current_link_id == link.c.id,
                        ),
                    )
                )
                .where(pointer.c.workspace_id == workspace_id)
            ).all()

        gstins: dict[UUID, list[str]] = defaultdict(list)
        for vendor_id, gstin in gstin_rows:
            gstins[cast(UUID, vendor_id)].append(str(gstin))
        aliases: dict[UUID, list[str]] = defaultdict(list)
        for vendor_id, raw_alias in alias_rows:
            aliases[cast(UUID, vendor_id)].append(str(raw_alias))
        state_by_document: dict[UUID, list[str]] = defaultdict(list)
        for from_document_id, state in link_rows:
            state_by_document[cast(UUID, from_document_id)].append(str(state))
        counts: dict[UUID | None, dict[str, int]] = defaultdict(
            lambda: {"po": 0, "grn": 0, "invoice": 0, "linked": 0, "suggested": 0, "unlinked": 0}
        )
        for document_id, doc_type, vendor_id, merged_into in doc_rows:
            effective_vendor = cast(UUID | None, merged_into or vendor_id)
            counts[effective_vendor][str(doc_type)] += 1
            if doc_type not in {DocType.INVOICE.value, DocType.GRN.value}:
                continue
            states = state_by_document.get(cast(UUID, document_id), [])
            if any(state in {"auto", "confirmed"} for state in states):
                counts[effective_vendor]["linked"] += 1
            elif "suggested" in states:
                counts[effective_vendor]["suggested"] += 1
            else:
                counts[effective_vendor]["unlinked"] += 1

        search_text = search.casefold().strip() if search else ""
        result: list[dict[str, Any]] = []
        unassigned_counts = counts.get(None, {key: 0 for key in next(iter(counts.values()), {})})
        if unassigned_counts and any(unassigned_counts.values()):
            result.append(
                {
                    "id": None,
                    "canonical_name": "Unassigned",
                    "gstins": [],
                    "pan": None,
                    "anchor": "name",
                    "counts": unassigned_counts,
                    "has_issues": True,
                    "unassigned": True,
                }
            )
        for vendor_row in vendors:
            vendor_id = cast(UUID, vendor_row["id"])
            values = counts.get(
                vendor_id,
                {key: 0 for key in ("po", "grn", "invoice", "linked", "suggested", "unlinked")},
            )
            vendor_gstins = gstins.get(vendor_id, [])
            vendor_aliases = aliases.get(vendor_id, [])
            matches = not search_text or any(
                search_text in value.casefold()
                for value in (
                    str(vendor_row["canonical_name"]),
                    str(vendor_row["pan"] or ""),
                    *vendor_gstins,
                    *vendor_aliases,
                )
            )
            has_vendor_issues = values["unlinked"] > 0 or values["suggested"] > 0
            if not matches or (has_issues and not has_vendor_issues):
                continue
            result.append(
                {
                    "id": vendor_id,
                    "canonical_name": vendor_row["canonical_name"],
                    "name_status": vendor_row["name_status"],
                    "version": vendor_row["version"],
                    "gstins": vendor_gstins,
                    "pan": vendor_row["pan"],
                    "anchor": vendor_row["anchor"],
                    "aliases": vendor_aliases,
                    "counts": values,
                    "has_issues": has_vendor_issues,
                    "unassigned": False,
                }
            )
        return result

    @requires("data.view")
    def list_vendor_documents(
        self,
        workspace_id: UUID,
        vendor_id: UUID | None,
        *,
        doc_type: str | None = None,
    ) -> list[dict[str, Any]]:
        document = metadata.tables["document"]
        version = metadata.tables["document_version"]
        vendor = metadata.tables["vendor"]
        pointer = metadata.tables["link_current"]
        link = metadata.tables["link"]
        target_document = document.alias("target_document")
        target_version = version.alias("target_version")
        saved_by = metadata.tables["app_user"].alias("link_saved_by")
        selection = [
            document.c.id,
            document.c.doc_type,
            document.c.vendor_id,
            document.c.current_version_id,
            document.c.needs_verification,
            version.c.vendor_name,
            version.c.vendor_gstin,
            version.c.doc_no,
            version.c.doc_date,
            version.c.grand_total,
            version.c.currency,
            version.c.source_file_id,
            vendor.c.merged_into.label("vendor_merged_into"),
        ]
        statement = (
            sa.select(*selection)
            .select_from(
                document.join(
                    version,
                    sa.and_(
                        document.c.workspace_id == version.c.workspace_id,
                        document.c.current_version_id == version.c.id,
                    ),
                ).outerjoin(
                    vendor,
                    sa.and_(
                        vendor.c.workspace_id == document.c.workspace_id,
                        vendor.c.id == document.c.vendor_id,
                    ),
                )
            )
            .where(document.c.workspace_id == workspace_id, document.c.withdrawn_at.is_(None))
        )
        if vendor_id is None:
            statement = statement.where(document.c.vendor_id.is_(None))
        else:
            statement = statement.where(
                sa.func.coalesce(vendor.c.merged_into, document.c.vendor_id) == vendor_id
            )
        if doc_type is not None:
            statement = statement.where(document.c.doc_type == doc_type)
        with self.engine.connect() as connection:
            rows = list(
                connection.execute(
                    statement.order_by(version.c.doc_date.desc().nullslast(), version.c.doc_no)
                ).mappings()
            )
            ids = [cast(UUID, row["id"]) for row in rows]
            link_rows = (
                connection.execute(
                    sa.select(
                        link.c.id,
                        link.c.version_no,
                        link.c.from_document_id,
                        link.c.to_document_id,
                        link.c.method,
                        link.c.confidence,
                        link.c.state,
                        link.c.ai_suggested,
                        link.c.evidence_snippet,
                        link.c.level,
                        link.c.from_line_no,
                        link.c.detail_code,
                        link.c.created_at,
                        saved_by.c.display_name.label("saved_by_name"),
                        target_version.c.doc_no.label("po_no"),
                    )
                    .select_from(
                        pointer.join(
                            link,
                            sa.and_(
                                pointer.c.workspace_id == link.c.workspace_id,
                                pointer.c.current_link_id == link.c.id,
                            ),
                        ).outerjoin(saved_by, saved_by.c.id == link.c.created_by)
                    )
                    .join(
                        target_document,
                        sa.and_(
                            target_document.c.workspace_id == pointer.c.workspace_id,
                            target_document.c.id == pointer.c.to_document_id,
                        ),
                    )
                    .join(
                        target_version,
                        sa.and_(
                            target_version.c.workspace_id == target_document.c.workspace_id,
                            target_version.c.id == target_document.c.current_version_id,
                        ),
                    )
                    .where(
                        pointer.c.workspace_id == workspace_id, pointer.c.from_document_id.in_(ids)
                    )
                ).all()
                if ids
                else []
            )
            doc_line = metadata.tables["doc_line"]
            version_ids = [row["current_version_id"] for row in rows]
            line_rows = (
                connection.execute(
                    sa.select(doc_line.c.document_version_id, doc_line.c.line_no)
                    .where(
                        doc_line.c.workspace_id == workspace_id,
                        doc_line.c.document_version_id.in_(version_ids),
                    )
                    .order_by(doc_line.c.document_version_id, doc_line.c.line_no)
                ).all()
                if version_ids
                else []
            )
            resolution = metadata.tables["link_resolution"]
            resolution_rows = (
                connection.execute(
                    sa.select(
                        resolution.c.document_id,
                        resolution.c.outcome,
                        resolution.c.resolved_step,
                        resolution.c.detail_code,
                        resolution.c.cited_refs,
                        resolution.c.candidates,
                    ).where(
                        resolution.c.workspace_id == workspace_id,
                        resolution.c.document_id.in_(ids),
                    )
                )
                .mappings()
                .all()
                if ids
                else []
            )
        link_by_document: dict[UUID, list[dict[str, Any]]] = defaultdict(list)
        for link_row in link_rows:
            link_by_document[cast(UUID, link_row.from_document_id)].append(
                {
                    "id": link_row.id,
                    "version_no": link_row.version_no,
                    "po_document_id": link_row.to_document_id,
                    "po_no": link_row.po_no,
                    "method": link_row.method,
                    "confidence": link_row.confidence,
                    "state": link_row.state,
                    "ai_suggested": link_row.ai_suggested,
                    "evidence": link_row.evidence_snippet,
                    "level": link_row.level,
                    "line_no": link_row.from_line_no,
                    # Who saved this state and when, for "Saved · time" and "Removed by"
                    # (PD-4b); no name for the system's own links.
                    "detail_code": link_row.detail_code,
                    "saved_at": link_row.created_at,
                    "saved_by_name": link_row.saved_by_name,
                }
            )
        line_nos_by_version: dict[UUID, list[int]] = defaultdict(list)
        for line_row in line_rows:
            line_nos_by_version[cast(UUID, line_row.document_version_id)].append(
                int(line_row.line_no)
            )
        resolution_by_document = {
            cast(UUID, row["document_id"]): {
                "outcome": row["outcome"],
                "resolved_step": row["resolved_step"],
                "detail_code": row["detail_code"],
                "cited_refs": row["cited_refs"],
                "candidates": row["candidates"],
            }
            for row in resolution_rows
        }
        return [
            {
                "id": row["id"],
                "doc_type": row["doc_type"],
                "doc_no": row["doc_no"],
                "vendor_name": row["vendor_name"],
                "vendor_gstin": row["vendor_gstin"],
                "doc_date": row["doc_date"],
                "grand_total": row["grand_total"],
                "currency": row["currency"],
                "source_file_id": row["source_file_id"],
                "vendor_id": row["vendor_merged_into"] or row["vendor_id"],
                "needs_verification": row["needs_verification"],
                "links": link_by_document.get(cast(UUID, row["id"]), []),
                # Line numbers of the current version, so a reviewer sees which lines a
                # line-level link set still leaves without a PO link.
                "line_nos": line_nos_by_version.get(cast(UUID, row["current_version_id"]), []),
                "resolution": resolution_by_document.get(cast(UUID, row["id"])),
            }
            for row in rows
        ]

    @requires("data.view")
    def get_vendor(self, workspace_id: UUID, vendor_id: UUID) -> dict[str, Any] | None:
        return next(
            (row for row in self.list_vendors(workspace_id) if row["id"] == vendor_id),
            None,
        )

    @requires("data.view")
    def list_links(
        self,
        workspace_id: UUID,
        *,
        source_document_id: UUID | None = None,
        state: str | None = None,
    ) -> list[dict[str, Any]]:
        pointer = metadata.tables["link_current"]
        link = metadata.tables["link"]
        document = metadata.tables["document"]
        version = metadata.tables["document_version"]
        source = document.alias("source_document")
        target = document.alias("target_document")
        source_version = version.alias("source_version")
        target_version = version.alias("target_version")
        statement = (
            sa.select(
                link.c.id,
                link.c.link_type,
                link.c.level,
                link.c.from_document_id,
                link.c.from_line_no,
                link.c.to_document_id,
                link.c.version_no,
                link.c.method,
                link.c.confidence,
                link.c.state,
                link.c.ai_suggested,
                link.c.evidence_snippet,
                link.c.detail_code,
                source_version.c.doc_no.label("source_doc_no"),
                target_version.c.doc_no.label("target_doc_no"),
            )
            .select_from(
                pointer.join(
                    link,
                    sa.and_(
                        pointer.c.workspace_id == link.c.workspace_id,
                        pointer.c.current_link_id == link.c.id,
                    ),
                )
                .join(
                    source,
                    sa.and_(
                        source.c.workspace_id == pointer.c.workspace_id,
                        source.c.id == pointer.c.from_document_id,
                    ),
                )
                .join(
                    source_version,
                    sa.and_(
                        source_version.c.workspace_id == source.c.workspace_id,
                        source_version.c.id == source.c.current_version_id,
                    ),
                )
                .join(
                    target,
                    sa.and_(
                        target.c.workspace_id == pointer.c.workspace_id,
                        target.c.id == pointer.c.to_document_id,
                    ),
                )
                .join(
                    target_version,
                    sa.and_(
                        target_version.c.workspace_id == target.c.workspace_id,
                        target_version.c.id == target.c.current_version_id,
                    ),
                )
            )
            .where(pointer.c.workspace_id == workspace_id)
        )
        if source_document_id is not None:
            statement = statement.where(pointer.c.from_document_id == source_document_id)
        if state is not None:
            statement = statement.where(link.c.state == state)
        with self.engine.connect() as connection:
            rows = connection.execute(
                statement.order_by(source_version.c.doc_date, source_version.c.doc_no, link.c.id)
            ).mappings()
            return [dict(row) for row in rows]

    @requires("data.view")
    def list_merge_suggestions(self, workspace_id: UUID) -> list[dict[str, Any]]:
        suggestion = metadata.tables["vendor_merge_suggestion"]
        vendor_a = metadata.tables["vendor"].alias("vendor_a")
        vendor_b = metadata.tables["vendor"].alias("vendor_b")
        registration = metadata.tables["vendor_gstin"]
        statement = (
            sa.select(
                suggestion.c.id,
                suggestion.c.vendor_a_id,
                suggestion.c.vendor_b_id,
                suggestion.c.score,
                suggestion.c.state,
                suggestion.c.version,
                suggestion.c.created_at,
                # Advisory H4 opinion; it never decides or merges anything.
                suggestion.c.ai_opinion,
                suggestion.c.ai_reason,
                vendor_a.c.canonical_name.label("vendor_a_name"),
                vendor_a.c.pan.label("vendor_a_pan"),
                vendor_b.c.canonical_name.label("vendor_b_name"),
                vendor_b.c.pan.label("vendor_b_pan"),
            )
            .select_from(
                suggestion.join(
                    vendor_a,
                    sa.and_(
                        vendor_a.c.workspace_id == suggestion.c.workspace_id,
                        vendor_a.c.id == suggestion.c.vendor_a_id,
                    ),
                ).join(
                    vendor_b,
                    sa.and_(
                        vendor_b.c.workspace_id == suggestion.c.workspace_id,
                        vendor_b.c.id == suggestion.c.vendor_b_id,
                    ),
                )
            )
            .where(suggestion.c.workspace_id == workspace_id, suggestion.c.state == "pending")
            .order_by(suggestion.c.score.desc(), suggestion.c.created_at, suggestion.c.id)
        )
        with self.engine.connect() as connection:
            rows = list(connection.execute(statement).mappings())
            vendor_ids = {
                cast(UUID, row[key]) for row in rows for key in ("vendor_a_id", "vendor_b_id")
            }
            gstin_rows = (
                connection.execute(
                    sa.select(registration.c.vendor_id, registration.c.gstin, registration.c.pan)
                    .where(
                        registration.c.workspace_id == workspace_id,
                        registration.c.vendor_id.in_(vendor_ids),
                    )
                    .order_by(registration.c.vendor_id, registration.c.gstin)
                ).all()
                if vendor_ids
                else []
            )
        gstins: dict[UUID, list[str]] = defaultdict(list)
        registration_pans: dict[UUID, set[str]] = defaultdict(set)
        for vendor_id, gstin, pan in gstin_rows:
            gstins[cast(UUID, vendor_id)].append(str(gstin))
            if pan:
                registration_pans[cast(UUID, vendor_id)].add(str(pan).upper())
        return [
            {
                **dict(row),
                "vendor_a_gstins": gstins.get(cast(UUID, row["vendor_a_id"]), []),
                "vendor_b_gstins": gstins.get(cast(UUID, row["vendor_b_id"]), []),
                "pan_conflict": bool(
                    len(
                        registration_pans.get(cast(UUID, row["vendor_a_id"]), set())
                        | registration_pans.get(cast(UUID, row["vendor_b_id"]), set())
                        | ({str(row["vendor_a_pan"]).upper()} if row["vendor_a_pan"] else set())
                        | ({str(row["vendor_b_pan"]).upper()} if row["vendor_b_pan"] else set())
                    )
                    > 1
                ),
            }
            for row in rows
        ]

    @requires("links.edit")
    def decide_merge_suggestion(
        self,
        actor: ActorContext,
        suggestion_id: UUID,
        *,
        decision: str,
        survivor_vendor_id: UUID | None = None,
        expected_version: int | None = None,
    ) -> dict[str, Any] | None:
        if decision not in {"accept", "reject"}:
            raise ValueError("decision must be accept or reject")
        suggestion = metadata.tables["vendor_merge_suggestion"]
        with self.engine.begin() as connection:
            row = (
                connection.execute(
                    sa.select(suggestion)
                    .where(
                        suggestion.c.workspace_id == actor.workspace_id,
                        suggestion.c.id == suggestion_id,
                    )
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                return None
            if row["state"] != "pending":
                raise ValueError("this merge suggestion has already been decided")
            if expected_version is not None and int(row["version"]) != expected_version:
                raise VersionConflict(dict(row))
            before = {"state": row["state"], "survivor_vendor_id": row["survivor_vendor_id"]}
            if decision == "accept":
                if survivor_vendor_id not in {row["vendor_a_id"], row["vendor_b_id"]}:
                    raise ValueError("the survivor must be one of the suggested vendors")
                source_vendor_id = (
                    row["vendor_b_id"]
                    if survivor_vendor_id == row["vendor_a_id"]
                    else row["vendor_a_id"]
                )
                self._merge_vendors_connection(
                    connection,
                    actor,
                    source_vendor_id=cast(UUID, source_vendor_id),
                    survivor_vendor_id=survivor_vendor_id,
                    suggestion_id=suggestion_id,
                )
                next_state = "accepted"
            else:
                if survivor_vendor_id is not None:
                    raise ValueError("a rejected suggestion cannot select a survivor")
                next_state = "rejected"
            updated = (
                connection.execute(
                    sa.update(suggestion)
                    .where(
                        suggestion.c.workspace_id == actor.workspace_id,
                        suggestion.c.id == suggestion_id,
                    )
                    .values(
                        state=next_state,
                        survivor_vendor_id=survivor_vendor_id,
                        decided_by=actor.actor_id,
                        decided_at=sa.func.now(),
                        version=suggestion.c.version + 1,
                    )
                    .returning(*suggestion.c)
                )
                .mappings()
                .one()
            )
            write_audit_event(
                connection,
                actor,
                action="vendor.suggestion_decide",
                target_type="vendor",
                target_id=cast(UUID, survivor_vendor_id or row["vendor_a_id"]),
                before_state=before,
                after_state={
                    "suggestion_id": str(suggestion_id),
                    "state": next_state,
                    "survivor_vendor_id": survivor_vendor_id,
                },
            )
            return dict(updated)

    @requires("links.edit")
    def merge_vendors(
        self,
        actor: ActorContext,
        *,
        source_vendor_id: UUID,
        survivor_vendor_id: UUID,
    ) -> dict[str, Any] | None:
        with self.engine.begin() as connection:
            return self._merge_vendors_connection(
                connection,
                actor,
                source_vendor_id=source_vendor_id,
                survivor_vendor_id=survivor_vendor_id,
                suggestion_id=None,
            )

    @requires("links.edit")
    def create_vendor(
        self,
        actor: ActorContext,
        *,
        name: str,
        gstin: str | None = None,
        pan: str | None = None,
    ) -> dict[str, Any]:
        """A person creates a vendor, e.g. an unregistered supplier without a GSTIN (AC-4)."""

        from reconbench_core.normalize import normalize_vendor_name, validate_gstin
        from reconbench_core.parsers.parties import buyer_gstins

        vendor = metadata.tables["vendor"]
        registration = metadata.tables["vendor_gstin"]
        with self.engine.begin() as connection:
            buyers = self._buyer_entities(connection, actor.workspace_id)
            display = self._checked_vendor_name(name, buyers)
            facts = validate_gstin(gstin) if gstin and gstin.strip() else None
            if facts is not None and (
                not facts.is_valid or facts.state_code is None or facts.pan is None
            ):
                raise ValueError("the GSTIN is not valid (format or checksum)")
            if facts is not None and facts.normalized in buyer_gstins(buyers):
                raise ValueError("the GSTIN is one of your own registrations, not a supplier")
            pan_value = pan.strip().upper() if pan and pan.strip() else None
            if pan_value is not None and not _PAN_PATTERN.fullmatch(pan_value):
                raise ValueError("the PAN must look like AAAAA9999A")
            if facts is not None and pan_value is not None and facts.pan != pan_value:
                raise ValueError("the PAN does not match the GSTIN")
            pan_value = facts.pan if facts is not None else pan_value
            connection.execute(
                sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_name, 0))"),
                {"lock_name": f"reconbench.vendor-resolution:{actor.workspace_id}"},
            )
            if facts is not None:
                owner = connection.scalar(
                    sa.select(registration.c.vendor_id).where(
                        registration.c.workspace_id == actor.workspace_id,
                        registration.c.gstin == facts.normalized,
                    )
                )
                if owner is not None:
                    raise ValueError("this GSTIN already belongs to a vendor")
            if pan_value is not None:
                pan_owner = connection.scalar(
                    sa.select(vendor.c.id).where(
                        vendor.c.workspace_id == actor.workspace_id,
                        vendor.c.merged_into.is_(None),
                        vendor.c.pan == pan_value,
                    )
                )
                if pan_owner is not None:
                    raise ValueError("a vendor with this PAN already exists; add the GSTIN there")
            created = (
                connection.execute(
                    sa.insert(vendor)
                    .values(
                        id=new_id(),
                        workspace_id=actor.workspace_id,
                        canonical_name=display,
                        normalized_name=normalize_vendor_name(display),
                        pan=pan_value,
                        anchor="gstin" if facts is not None else "pan" if pan_value else "name",
                        created_by_rule="manual",
                        name_status="manual",
                    )
                    .returning(*vendor.c)
                )
                .mappings()
                .one()
            )
            vendor_id = cast(UUID, created["id"])
            if facts is not None:
                connection.execute(
                    sa.insert(registration).values(
                        id=new_id(),
                        workspace_id=actor.workspace_id,
                        vendor_id=vendor_id,
                        gstin=facts.normalized,
                        state_code=facts.state_code,
                        pan=facts.pan,
                        checksum_ok=True,
                    )
                )
            write_audit_event(
                connection,
                actor,
                action="vendor.create",
                target_type="vendor",
                target_id=vendor_id,
                after_state={
                    "canonical_name": display,
                    "gstin": facts.normalized if facts is not None else None,
                    "pan": pan_value,
                },
            )
            return {
                "id": vendor_id,
                "canonical_name": display,
                "name_status": "manual",
                "gstins": [facts.normalized] if facts is not None else [],
                "pan": pan_value,
                "anchor": created["anchor"],
                "version": created["version"],
            }

    @requires("links.edit")
    def rename_vendor(
        self,
        actor: ActorContext,
        vendor_id: UUID,
        *,
        name: str,
        expected_version: int,
    ) -> dict[str, Any] | None:
        """A person sets the display name; resolution never replaces it afterwards.

        The previous readable name stays an alias so documents keep matching it.
        """

        from reconbench_core.normalize import normalize_vendor_name
        from reconbench_core.parsers.parties import name_problem

        vendor = metadata.tables["vendor"]
        alias = metadata.tables["vendor_alias"]
        with self.engine.begin() as connection:
            row = (
                connection.execute(
                    sa.select(*vendor.c)
                    .where(
                        vendor.c.workspace_id == actor.workspace_id,
                        vendor.c.id == vendor_id,
                        vendor.c.merged_into.is_(None),
                    )
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                return None
            if int(row["version"]) != expected_version:
                raise VersionConflict(dict(row))
            display = self._checked_vendor_name(
                name, self._buyer_entities(connection, actor.workspace_id)
            )
            old_name = str(row["canonical_name"])
            old_norm = normalize_vendor_name(old_name)
            new_norm = normalize_vendor_name(display)
            if old_norm and old_norm != new_norm and name_problem(old_name) is None:
                connection.execute(
                    pg_insert(alias)
                    .values(
                        id=new_id(),
                        workspace_id=actor.workspace_id,
                        vendor_id=vendor_id,
                        alias_raw=old_name,
                        alias_norm=old_norm,
                        source="manual",
                        created_by=actor.actor_id,
                    )
                    .on_conflict_do_nothing(
                        index_elements=[alias.c.workspace_id, alias.c.alias_norm]
                    )
                )
            # The new name is the canonical name now, not an alias of it.
            connection.execute(
                sa.delete(alias).where(
                    alias.c.workspace_id == actor.workspace_id,
                    alias.c.vendor_id == vendor_id,
                    alias.c.alias_norm == new_norm,
                )
            )
            updated = (
                connection.execute(
                    sa.update(vendor)
                    .where(vendor.c.workspace_id == actor.workspace_id, vendor.c.id == vendor_id)
                    .values(
                        canonical_name=display,
                        normalized_name=new_norm,
                        name_status="manual",
                        input_seq=vendor.c.input_seq + 1,
                        updated_at=sa.func.now(),
                        version=vendor.c.version + 1,
                    )
                    .returning(*vendor.c)
                )
                .mappings()
                .one()
            )
            self._bump_vendor_scope(
                connection,
                workspace_id=actor.workspace_id,
                vendor_ids={vendor_id},
                reason="vendor_renamed",
            )
            write_audit_event(
                connection,
                actor,
                action="vendor.rename",
                target_type="vendor",
                target_id=vendor_id,
                before_state={"canonical_name": old_name, "name_status": row["name_status"]},
                after_state={"canonical_name": display, "name_status": "manual"},
            )
            return dict(updated)

    @staticmethod
    def _buyer_entities(connection: Connection, workspace_id: UUID) -> Any:
        from reconbench_core.parsers.parties import party_hints_from_config

        active = metadata.tables["config_active"]
        version = metadata.tables["config_version"]
        body = connection.scalar(
            sa.select(version.c.body)
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
        engine = body.get("engine") if isinstance(body, Mapping) else None
        return party_hints_from_config(engine if isinstance(engine, Mapping) else {})[
            "buyer_entities"
        ]

    @staticmethod
    def _checked_vendor_name(name: str, buyers: Any) -> str:
        from reconbench_core.normalize import normalize_vendor_name
        from reconbench_core.parsers.parties import is_buyer_name, name_problem

        display = " ".join(name.split())
        if not normalize_vendor_name(display):
            raise ValueError("the vendor name must contain letters")
        if name_problem(display) is not None:
            raise ValueError("the vendor name looks like a label, a GSTIN or line text")
        if is_buyer_name(display, buyers):
            raise ValueError("the vendor name is one of your own company names")
        return display

    @requires("data.view")
    def list_vendor_aliases(
        self, workspace_id: UUID, vendor_id: UUID | None = None
    ) -> list[dict[str, Any]]:
        alias = metadata.tables["vendor_alias"]
        statement = sa.select(*alias.c).where(alias.c.workspace_id == workspace_id)
        if vendor_id is not None:
            statement = statement.where(alias.c.vendor_id == vendor_id)
        with self.engine.connect() as connection:
            return [
                dict(row)
                for row in connection.execute(statement.order_by(alias.c.alias_norm)).mappings()
            ]

    @requires("links.edit")
    def add_vendor_alias(
        self, actor: ActorContext, *, vendor_id: UUID, alias_raw: str
    ) -> dict[str, Any] | None:
        from reconbench_core.normalize import normalize_vendor_name

        normalized = normalize_vendor_name(alias_raw)
        if not normalized:
            raise ValueError("alias must contain at least one searchable character")
        vendor = metadata.tables["vendor"]
        alias = metadata.tables["vendor_alias"]
        with self.engine.begin() as connection:
            connection.execute(
                sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:alias_lock, 0))"),
                {"alias_lock": f"reconbench.vendor-alias:{actor.workspace_id}:{normalized}"},
            )
            owner = (
                connection.execute(
                    sa.select(vendor.c.id, vendor.c.canonical_name)
                    .where(
                        vendor.c.workspace_id == actor.workspace_id,
                        vendor.c.id == vendor_id,
                        vendor.c.merged_into.is_(None),
                    )
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if owner is None:
                return None
            if normalized == normalize_vendor_name(str(owner["canonical_name"])):
                raise ValueError("alias is the same as the vendor's canonical name")
            existing = (
                connection.execute(
                    sa.select(*alias.c).where(
                        alias.c.workspace_id == actor.workspace_id, alias.c.alias_norm == normalized
                    )
                )
                .mappings()
                .one_or_none()
            )
            if existing is not None:
                if existing["vendor_id"] != vendor_id:
                    raise ValueError("this alias already belongs to another vendor")
                return dict(existing)
            inserted = (
                connection.execute(
                    sa.insert(alias)
                    .values(
                        id=new_id(),
                        workspace_id=actor.workspace_id,
                        vendor_id=vendor_id,
                        alias_raw=alias_raw.strip(),
                        alias_norm=normalized,
                        source="manual",
                        created_by=actor.actor_id,
                    )
                    .returning(*alias.c)
                )
                .mappings()
                .one()
            )
            self._bump_vendor_scope(
                connection,
                workspace_id=actor.workspace_id,
                vendor_ids={vendor_id},
                reason="vendor_alias_added",
            )
            self._mark_vendor_documents_dirty(
                connection, actor.workspace_id, {vendor_id}, "vendor_alias_added"
            )
            write_audit_event(
                connection,
                actor,
                action="vendor.alias_add",
                target_type="vendor",
                target_id=vendor_id,
                after_state={"alias": alias_raw.strip(), "alias_norm": normalized},
            )
            return dict(inserted)

    @requires("links.edit")
    def delete_vendor_alias(self, actor: ActorContext, alias_id: UUID) -> bool:
        alias = metadata.tables["vendor_alias"]
        with self.engine.begin() as connection:
            row = (
                connection.execute(
                    sa.select(alias.c.id, alias.c.vendor_id, alias.c.alias_raw, alias.c.alias_norm)
                    .where(alias.c.workspace_id == actor.workspace_id, alias.c.id == alias_id)
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                return False
            connection.execute(
                sa.delete(alias).where(
                    alias.c.workspace_id == actor.workspace_id, alias.c.id == alias_id
                )
            )
            vendor_id = cast(UUID, row["vendor_id"])
            self._bump_vendor_scope(
                connection,
                workspace_id=actor.workspace_id,
                vendor_ids={vendor_id},
                reason="vendor_alias_deleted",
            )
            self._mark_vendor_documents_dirty(
                connection, actor.workspace_id, {vendor_id}, "vendor_alias_deleted"
            )
            write_audit_event(
                connection,
                actor,
                action="vendor.alias_delete",
                target_type="vendor",
                target_id=vendor_id,
                before_state={"alias": row["alias_raw"], "alias_norm": row["alias_norm"]},
            )
            return True

    @requires("links.edit")
    def bulk_confirm(
        self,
        connection: Connection,
        actor: ActorContext,
        *,
        vendor_id: UUID | None,
        threshold: Decimal = Decimal("0.95"),
    ) -> dict[str, Any]:
        pointer = metadata.tables["link_current"]
        link = metadata.tables["link"]
        source = metadata.tables["document"].alias("source_document")
        target = metadata.tables["document"].alias("target_document")
        statement = (
            sa.select(*link.c)
            .select_from(
                pointer.join(link, pointer.c.current_link_id == link.c.id)
                .join(
                    source,
                    sa.and_(
                        source.c.workspace_id == pointer.c.workspace_id,
                        source.c.id == pointer.c.from_document_id,
                    ),
                )
                .join(
                    target,
                    sa.and_(
                        target.c.workspace_id == pointer.c.workspace_id,
                        target.c.id == pointer.c.to_document_id,
                    ),
                )
            )
            .where(
                pointer.c.workspace_id == actor.workspace_id,
                link.c.state.in_(["auto", "suggested"]),
                link.c.ai_suggested.is_(False),
                link.c.confidence >= threshold,
                source.c.vendor_id.is_not_distinct_from(target.c.vendor_id),
            )
            .order_by(link.c.id)
            .with_for_update(of=link, skip_locked=True)
        )
        if vendor_id is not None:
            statement = statement.where(
                sa.func.coalesce(source.c.vendor_id, target.c.vendor_id) == vendor_id
            )
        rows = list(connection.execute(statement).mappings())
        confirmed: list[dict[str, Any]] = []
        affected: set[UUID] = set()
        for row in rows:
            proposal = ProposedLink(
                from_document_id=cast(UUID, row["from_document_id"]),
                to_document_id=cast(UUID, row["to_document_id"]),
                link_type=LinkType(str(row["link_type"])),
                level=LinkLevel(str(row["level"])),
                method=LinkMethod(str(row["method"])),
                confidence=cast(Decimal, row["confidence"]),
                state=LinkState.CONFIRMED,
                evidence=str(row["evidence_snippet"] or "Confirmed in bulk by an operator."),
                line_no=cast(int | None, row["from_line_no"]),
            )
            next_id = self._append_link(
                connection,
                workspace_id=actor.workspace_id,
                proposal=proposal,
                old=row,
                detail_code=cast(str | None, row["detail_code"]),
                actor_id=actor.actor_id,
            )
            confirmed.append({"link_id": row["id"], "new_link_id": next_id, "state": "confirmed"})
            affected.update((proposal.from_document_id, proposal.to_document_id))
            write_audit_event(
                connection,
                actor,
                action="link.confirm",
                target_type="link",
                target_id=next_id,
                before_state={
                    "state": row["state"],
                    "method": row["method"],
                    "confidence": row["confidence"],
                },
                after_state={"state": "confirmed", "bulk": True},
            )
        scopes_marked = self._mark_affected_scopes(
            connection, workspace_id=actor.workspace_id, document_ids=affected
        )
        return {"confirmed": len(confirmed), "items": confirmed, "scopes_marked": scopes_marked}

    def _merge_vendors_connection(
        self,
        connection: Connection,
        actor: ActorContext,
        *,
        source_vendor_id: UUID,
        survivor_vendor_id: UUID,
        suggestion_id: UUID | None,
    ) -> dict[str, Any] | None:
        from reconbench_core.normalize import normalize_vendor_name

        if source_vendor_id == survivor_vendor_id:
            raise ValueError("a vendor cannot be merged into itself")
        vendor = metadata.tables["vendor"]
        registration = metadata.tables["vendor_gstin"]
        alias = metadata.tables["vendor_alias"]
        ids = sorted((source_vendor_id, survivor_vendor_id), key=str)
        rows = list(
            connection.execute(
                sa.select(*vendor.c)
                .where(vendor.c.workspace_id == actor.workspace_id, vendor.c.id.in_(ids))
                .order_by(vendor.c.id)
                .with_for_update()
            ).mappings()
        )
        by_id = {cast(UUID, row["id"]): row for row in rows}
        source = by_id.get(source_vendor_id)
        survivor = by_id.get(survivor_vendor_id)
        if source is None or survivor is None:
            return None
        if source["merged_into"] is not None or survivor["merged_into"] is not None:
            raise ValueError("only active, unmerged vendors can be merged")
        registrations = connection.execute(
            sa.select(registration.c.vendor_id, registration.c.pan).where(
                registration.c.workspace_id == actor.workspace_id, registration.c.vendor_id.in_(ids)
            )
        ).all()
        pans = {str(row["pan"]).upper() for row in (source, survivor) if row["pan"]}
        pans.update(str(pan).upper() for _, pan in registrations if pan)
        if len(pans) > 1:
            raise ValueError("vendors with different PANs cannot be merged")
        from reconbench_core.mapping.vendor import VendorNameStatus
        from reconbench_core.parsers.parties import name_problem

        source_name = str(source["canonical_name"])
        survivor_name = str(survivor["canonical_name"])
        name_rank = [status.value for status in VendorNameStatus]
        # The weaker of the two names becomes the alias; a GSTIN or label "name"
        # (pre-AC-4 junk) is never kept, and a pending survivor takes the source name.
        alias_name = source_name
        if name_problem(source_name) is None and normalize_vendor_name(source_name):
            if name_rank.index(str(source["name_status"])) < name_rank.index(
                str(survivor["name_status"])
            ):
                connection.execute(
                    sa.update(vendor)
                    .where(
                        vendor.c.workspace_id == actor.workspace_id,
                        vendor.c.id == survivor_vendor_id,
                    )
                    .values(
                        canonical_name=source_name,
                        normalized_name=normalize_vendor_name(source_name),
                        name_status=source["name_status"],
                    )
                )
                connection.execute(
                    sa.delete(alias).where(
                        alias.c.workspace_id == actor.workspace_id,
                        alias.c.vendor_id == survivor_vendor_id,
                        alias.c.alias_norm == normalize_vendor_name(source_name),
                    )
                )
                alias_name, survivor_name = survivor_name, source_name
        else:
            alias_name = ""
        alias_norm = normalize_vendor_name(alias_name) if name_problem(alias_name) is None else ""
        survivor_norm = normalize_vendor_name(survivor_name)
        if alias_norm and alias_norm != survivor_norm:
            existing_alias = connection.execute(
                sa.select(alias.c.vendor_id).where(
                    alias.c.workspace_id == actor.workspace_id, alias.c.alias_norm == alias_norm
                )
            ).scalar_one_or_none()
            if existing_alias is not None and existing_alias != survivor_vendor_id:
                raise ValueError("the source vendor name is already an alias of another vendor")
            if existing_alias is None:
                connection.execute(
                    sa.insert(alias).values(
                        id=new_id(),
                        workspace_id=actor.workspace_id,
                        vendor_id=survivor_vendor_id,
                        alias_raw=alias_name,
                        alias_norm=alias_norm,
                        source="merge",
                        suggestion_id=suggestion_id,
                        created_by=actor.actor_id,
                    )
                )

        connection.execute(
            sa.update(registration)
            .where(
                registration.c.workspace_id == actor.workspace_id,
                registration.c.vendor_id == source_vendor_id,
            )
            .values(vendor_id=survivor_vendor_id)
        )
        connection.execute(
            sa.update(vendor)
            .where(vendor.c.workspace_id == actor.workspace_id, vendor.c.id == source_vendor_id)
            .values(
                merged_into=survivor_vendor_id,
                merged_at=sa.func.now(),
                merged_by=actor.actor_id,
                input_seq=vendor.c.input_seq + 1,
                version=vendor.c.version + 1,
                updated_at=sa.func.now(),
            )
        )
        self._bump_vendor_scope(
            connection,
            workspace_id=actor.workspace_id,
            vendor_ids={survivor_vendor_id},
            reason="vendor_merged",
        )
        self._mark_scope(
            connection,
            workspace_id=actor.workspace_id,
            scope_kind="vendor",
            scope_id=source_vendor_id,
            reason="vendor_merged",
        )
        self._mark_vendor_documents_dirty(
            connection, actor.workspace_id, {source_vendor_id, survivor_vendor_id}, "vendor_merged"
        )
        write_audit_event(
            connection,
            actor,
            action="vendor.merge",
            target_type="vendor",
            target_id=source_vendor_id,
            before_state={"canonical_name": source_name, "merged_into": None},
            after_state={
                "merged_into": str(survivor_vendor_id),
                "documents_retain_vendor_id": True,
            },
        )
        return {
            "source_vendor_id": source_vendor_id,
            "survivor_vendor_id": survivor_vendor_id,
            "source_name": source_name,
            "merged": True,
        }

    def _mark_vendor_documents_dirty(
        self,
        connection: Connection,
        workspace_id: UUID,
        vendor_ids: set[UUID],
        reason: str,
    ) -> None:
        if not vendor_ids:
            return
        document = metadata.tables["document"]
        rows = list(
            connection.execute(
                sa.select(document.c.id, document.c.doc_type)
                .where(
                    document.c.workspace_id == workspace_id,
                    document.c.vendor_id.in_(vendor_ids),
                    document.c.withdrawn_at.is_(None),
                )
                .with_for_update()
            ).all()
        )
        for document_id, doc_type in rows:
            connection.execute(
                sa.update(document)
                .where(document.c.workspace_id == workspace_id, document.c.id == document_id)
                .values(input_seq=document.c.input_seq + 1)
            )
            if doc_type == DocType.PO.value:
                self._mark_scope(
                    connection,
                    workspace_id=workspace_id,
                    scope_kind="po",
                    scope_id=cast(UUID, document_id),
                    reason=reason,
                )
            elif doc_type == DocType.INVOICE.value:
                self._mark_scope(
                    connection,
                    workspace_id=workspace_id,
                    scope_kind="invoice",
                    scope_id=cast(UUID, document_id),
                    reason=reason,
                )

    def _mark_scope(
        self,
        connection: Connection,
        *,
        workspace_id: UUID,
        scope_kind: str,
        scope_id: UUID,
        reason: str,
    ) -> None:
        scopes = metadata.tables["dirty_scope"]
        statement = pg_insert(scopes).values(
            id=new_id(),
            workspace_id=workspace_id,
            scope_kind=scope_kind,
            scope_id=scope_id,
            reason=reason,
        )
        connection.execute(
            statement.on_conflict_do_update(
                index_elements=[scopes.c.workspace_id, scopes.c.scope_kind, scopes.c.scope_id],
                index_where=scopes.c.processed_at.is_(None),
                set_={
                    "mark_count": scopes.c.mark_count + 1,
                    "last_marked_at": sa.func.now(),
                    "reason": statement.excluded.reason,
                },
            )
        )
        self._enqueue_pending_recomputes(workspace_id, connection)

    @requires("links.edit")
    def assign_document_vendor(
        self,
        actor: ActorContext,
        document_id: UUID,
        vendor_id: UUID,
    ) -> dict[str, Any] | None:
        document = metadata.tables["document"]
        version = metadata.tables["document_version"]
        vendor = metadata.tables["vendor"]
        registration = metadata.tables["vendor_gstin"]
        with self.engine.begin() as connection:
            row = (
                connection.execute(
                    sa.select(document)
                    .where(
                        document.c.workspace_id == actor.workspace_id,
                        document.c.id == document_id,
                        document.c.withdrawn_at.is_(None),
                    )
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                return None
            selected_vendor = (
                connection.execute(
                    sa.select(vendor.c.id, vendor.c.canonical_name)
                    .where(
                        vendor.c.workspace_id == actor.workspace_id,
                        vendor.c.id == vendor_id,
                        vendor.c.merged_into.is_(None),
                    )
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if selected_vendor is None:
                raise ValueError("the selected vendor does not exist in this workspace")
            extracted_gstin = connection.scalar(
                sa.select(version.c.vendor_gstin).where(
                    version.c.workspace_id == actor.workspace_id,
                    version.c.id == row["current_version_id"],
                )
            )
            vendor_gstins = set(
                connection.execute(
                    sa.select(registration.c.gstin).where(
                        registration.c.workspace_id == actor.workspace_id,
                        registration.c.vendor_id == vendor_id,
                    )
                ).scalars()
            )
            if extracted_gstin and vendor_gstins:
                normalized_gstin = "".join(
                    character for character in str(extracted_gstin).upper() if character.isalnum()
                )
                normalized_vendor_gstins = {
                    "".join(character for character in value.upper() if character.isalnum())
                    for value in vendor_gstins
                }
                if normalized_gstin not in normalized_vendor_gstins:
                    raise ValueError(
                        "the document GSTIN does not belong to the selected vendor; "
                        "resolve the supplier identity before assigning it"
                    )
            old_vendor_id = cast(UUID | None, row["vendor_id"])
            changed = old_vendor_id != vendor_id or not bool(row["vendor_locked"])
            if changed:
                connection.execute(
                    sa.update(document)
                    .where(
                        document.c.workspace_id == actor.workspace_id, document.c.id == document_id
                    )
                    .values(
                        vendor_id=vendor_id,
                        vendor_rule="manual",
                        vendor_score=None,
                        vendor_locked=True,
                        vendor_resolved_at=sa.func.now(),
                        updated_at=sa.func.now(),
                        version=document.c.version + 1,
                    )
                )
                self._mark_affected_scopes(
                    connection,
                    workspace_id=actor.workspace_id,
                    document_ids={document_id},
                )
                self._bump_vendor_scope(
                    connection,
                    workspace_id=actor.workspace_id,
                    vendor_ids={value for value in (old_vendor_id, vendor_id) if value is not None},
                    reason="document_vendor_manually_assigned",
                )
                write_audit_event(
                    connection,
                    actor,
                    action="vendor.assign",
                    target_type="vendor",
                    target_id=vendor_id,
                    before_state={"document_id": str(document_id), "vendor_id": old_vendor_id},
                    after_state={"document_id": str(document_id), "vendor_id": vendor_id},
                )
            return {
                "document_id": document_id,
                "vendor_id": vendor_id,
                "vendor_name": selected_vendor["canonical_name"],
                "vendor_locked": True,
                "changed": changed,
            }

    @requires("data.view")
    def load_snapshot(self, connection: Connection, workspace_id: UUID) -> MappingSnapshot | None:
        """Load the inputs one resolution uses; ``None`` when nothing is current."""

        rows, line_rows, text_by_version, header_evidence, line_evidence = self._load_current(
            connection, workspace_id
        )
        if not rows:
            return None
        link_context = self._current_link_context(connection, workspace_id)
        source_documents: list[MappingDocument] = []
        po_candidates: list[tuple[RowMapping, Document, UUID | None]] = []
        versions: dict[UUID, UUID] = {}
        for row in rows:
            document_id = cast(UUID, row["document_id"])
            version_id = cast(UUID, row["version_id"])
            versions[document_id] = version_id
            document = _make_document(
                row,
                line_rows.get(version_id, ()),
                text_by_version.get(version_id, ""),
                header_evidence.get(version_id, {}),
                line_evidence,
            )
            vendor_id = cast(UUID | None, row["vendor_id"])
            if document.header.doc_type is DocType.PO:
                po_candidates.append((row, document, vendor_id))
            else:
                manual_ids, rejected_ids = link_context.get(document_id, (set(), set()))
                source_documents.append(
                    MappingDocument(
                        document_id=document_id,
                        natural_key=str(row["natural_key"]),
                        vendor_id=vendor_id,
                        document=document,
                        source_text=document.raw_text,
                        manual_po_ids=tuple(sorted(manual_ids, key=str)),
                        rejected_po_ids=tuple(sorted(rejected_ids, key=str)),
                    )
                )

        purchase_orders = self._governing_pos(connection, workspace_id, po_candidates)
        config = self._mapping_config(connection, workspace_id)
        input_hash = _json_hash(
            {
                "sources": [item.model_dump(mode="json") for item in source_documents],
                "purchase_orders": [item.model_dump(mode="json") for item in purchase_orders],
                "config": config.model_dump(mode="json"),
            }
        )
        return MappingSnapshot(
            sources=tuple(source_documents),
            purchase_orders=tuple(purchase_orders),
            config=config,
            input_hash=input_hash,
        )

    def _queue_po_reranks(
        self, connection: Connection, workspace_id: UUID, snapshot: MappingSnapshot
    ) -> None:
        """Queue advisory H3 re-ranks; a failure never blocks link resolution."""

        if self.ai_hooks is None:
            return
        try:
            with connection.begin_nested():
                self.ai_hooks.po_reranks(connection, workspace_id, snapshot)
        except Exception as exc:  # noqa: BLE001 - AI is optional; rules continue.
            _LOGGER.warning("ai_enqueue_failed hook=h3_rerank error_type=%s", type(exc).__name__)

    @staticmethod
    def _persist_system_check(
        connection: Connection,
        workspace_id: UUID,
        update: FieldVerificationUpdate,
    ) -> None:
        """Append a system verification state without changing extracted evidence."""

        evidence = metadata.tables["field_evidence"]
        document = metadata.tables["document"]
        version_id = connection.scalar(
            sa.select(document.c.current_version_id).where(
                document.c.workspace_id == workspace_id,
                document.c.id == update.document_id,
                document.c.withdrawn_at.is_(None),
            )
        )
        if version_id is None:
            return
        if update.line_no is None:
            owner_clause = evidence.c.document_version_id == version_id
        else:
            line = metadata.tables["doc_line"]
            line_id = connection.scalar(
                sa.select(line.c.id).where(
                    line.c.workspace_id == workspace_id,
                    line.c.document_version_id == version_id,
                    line.c.line_no == update.line_no,
                )
            )
            if line_id is None:
                return
            owner_clause = evidence.c.doc_line_id == line_id
        latest = (
            connection.execute(
                sa.select(*evidence.c)
                .where(
                    evidence.c.workspace_id == workspace_id,
                    owner_clause,
                    evidence.c.field_name == update.field_name,
                )
                .order_by(evidence.c.created_at.desc(), evidence.c.id.desc())
                .limit(1)
            )
            .mappings()
            .one_or_none()
        )
        if latest is None or latest["verification"] == FieldVerification.USER.value:
            return
        latest_state = FieldVerification(str(latest["verification"]))
        if latest_state is update.verification:
            return
        row = dict(latest)
        row.update(
            id=new_id(),
            verification=update.verification.value,
            verified_by=(
                "system" if update.verification is FieldVerification.SYSTEM_CHECK else None
            ),
            verified_at=(
                sa.func.now() if update.verification is FieldVerification.SYSTEM_CHECK else None
            ),
        )
        row.pop("created_at", None)
        connection.execute(sa.insert(evidence).values(**row))

    @requires("links.edit")
    def record_ai_link_suggestion(
        self,
        connection: Connection,
        *,
        workspace_id: UUID,
        invoice_id: UUID,
        po_document_id: UUID,
        score: Decimal,
        reason: str,
        ai_call_id: UUID,
    ) -> bool:
        """Insert one ``suggested`` AI header link, or nothing when it is no longer safe.

        Never supersedes a live link, never re-suggests a PO a person rejected, and
        never marks scopes dirty: a pending AI suggestion feeds no balance or verdict.
        The caller holds the invoice's ``link_resolution`` row lock.
        """

        pointer = metadata.tables["link_current"]
        link = metadata.tables["link"]
        document = metadata.tables["document"]
        vendor = metadata.tables["vendor"]
        if connection.scalar(sa.select(sa.exists().where(link.c.ai_call_id == ai_call_id))):
            return False
        current = list(
            connection.execute(
                sa.select(*link.c)
                .select_from(
                    pointer.join(
                        link,
                        sa.and_(
                            pointer.c.workspace_id == link.c.workspace_id,
                            pointer.c.current_link_id == link.c.id,
                        ),
                    )
                )
                .where(
                    pointer.c.workspace_id == workspace_id,
                    pointer.c.link_type == LinkType.INV_PO.value,
                    pointer.c.from_document_id == invoice_id,
                )
                .with_for_update(of=link)
            ).mappings()
        )
        if any(row["state"] != LinkState.REJECTED.value for row in current):
            return False
        if any(
            row["to_document_id"] == po_document_id and row["method"] == LinkMethod.MANUAL.value
            for row in current
        ):
            return False
        vendors = {
            row.id: (row.doc_type, row.effective_vendor_id)
            for row in connection.execute(
                sa.select(
                    document.c.id,
                    document.c.doc_type,
                    sa.func.coalesce(vendor.c.merged_into, document.c.vendor_id).label(
                        "effective_vendor_id"
                    ),
                )
                .select_from(
                    document.outerjoin(
                        vendor,
                        sa.and_(
                            vendor.c.workspace_id == document.c.workspace_id,
                            vendor.c.id == document.c.vendor_id,
                        ),
                    )
                )
                .where(
                    document.c.workspace_id == workspace_id,
                    document.c.id.in_((invoice_id, po_document_id)),
                    document.c.withdrawn_at.is_(None),
                )
            )
        }
        invoice_row, po_row = vendors.get(invoice_id), vendors.get(po_document_id)
        if (
            invoice_row is None
            or po_row is None
            or invoice_row[0] != DocType.INVOICE.value
            or po_row[0] != DocType.PO.value
            or invoice_row[1] is None
            or invoice_row[1] != po_row[1]
        ):
            return False
        old = next(
            (
                row
                for row in current
                if row["to_document_id"] == po_document_id and row["from_line_no"] is None
            ),
            None,
        )
        self._append_link(
            connection,
            workspace_id=workspace_id,
            proposal=ProposedLink(
                from_document_id=invoice_id,
                to_document_id=po_document_id,
                link_type=LinkType.INV_PO,
                level=LinkLevel.HEADER,
                method=LinkMethod.CONTENT,
                confidence=min(score, Decimal("0.90")),
                state=LinkState.SUGGESTED,
                evidence=(
                    "AI choice among the top content candidates; operator confirmation required."
                ),
            ),
            old=old,
            detail_code="ai_rerank",
            ai_suggested=True,
            ai_call_id=ai_call_id,
            evidence={"hook": "h3_rerank", "reason": reason, "content_score": format(score, "f")},
        )
        return True

    @requires("links.edit")
    def edit_link(
        self,
        actor: ActorContext,
        link_id: UUID,
        *,
        action: str,
        expected_version: int,
        to_document_id: UUID | None = None,
    ) -> dict[str, Any] | None:
        with self.engine.begin() as connection:
            return self._edit_link_in(
                connection,
                actor,
                link_id,
                action=action,
                expected_version=expected_version,
                to_document_id=to_document_id,
            )

    @requires("links.edit")
    def confirm_document_links(
        self,
        actor: ActorContext,
        document_id: UUID,
        *,
        to_document_id: UUID,
        links: Sequence[tuple[UUID, int]],
    ) -> list[dict[str, Any]]:
        """Confirm the listed current links of one source document to one PO, all or nothing.

        Each link goes through the same checks, audit row and scope marking as a single
        confirm. Any stale version, foreign link or AI proposal fails the whole request, so
        a person never confirms a set that differs from what they saw.
        """

        if not links:
            raise ValueError("choose at least one link to confirm")
        if len({link_id for link_id, _version in links}) != len(links):
            raise ValueError("each link can be confirmed only once per request")
        pointer = metadata.tables["link_current"]
        link = metadata.tables["link"]
        confirmed: list[dict[str, Any]] = []
        with self.engine.begin() as connection:
            for link_id, expected_version in links:
                current = (
                    connection.execute(
                        sa.select(link.c.from_document_id, link.c.to_document_id, link.c.state)
                        .add_columns(link.c.ai_suggested)
                        .select_from(
                            pointer.join(
                                link,
                                sa.and_(
                                    pointer.c.workspace_id == link.c.workspace_id,
                                    pointer.c.current_link_id == link.c.id,
                                ),
                            )
                        )
                        .where(pointer.c.workspace_id == actor.workspace_id, link.c.id == link_id)
                    )
                    .mappings()
                    .one_or_none()
                )
                if current is None:
                    # Raise, not return: leaving the block normally would commit earlier confirms.
                    raise LookupError("a listed link is not current")
                if current["from_document_id"] != document_id:
                    raise ValueError("a listed link belongs to another document")
                if current["to_document_id"] != to_document_id:
                    raise ValueError("a listed link points to a different purchase order")
                if current["state"] not in {LinkState.AUTO.value, LinkState.SUGGESTED.value}:
                    raise ValueError("only auto or suggested links can be confirmed")
                if bool(current["ai_suggested"]):
                    raise ValueError("AI-suggested links are confirmed one at a time")
                result = self._edit_link_in(
                    connection,
                    actor,
                    link_id,
                    action="confirm",
                    expected_version=expected_version,
                )
                if result is None:
                    raise LookupError("a listed link is not current")
                confirmed.append(result)
        return confirmed

    def _edit_link_in(
        self,
        connection: Connection,
        actor: ActorContext,
        link_id: UUID,
        *,
        action: str,
        expected_version: int,
        to_document_id: UUID | None = None,
    ) -> dict[str, Any] | None:
        pointer = metadata.tables["link_current"]
        link = metadata.tables["link"]
        document = metadata.tables["document"]
        vendor = metadata.tables["vendor"]
        source_document = document.alias("edit_source_document")
        source_vendor = vendor.alias("edit_source_vendor")
        old_target_document = document.alias("edit_old_target_document")
        old_target_vendor = vendor.alias("edit_old_target_vendor")
        current = (
            connection.execute(
                sa.select(*link.c)
                .select_from(
                    pointer.join(
                        link,
                        sa.and_(
                            pointer.c.workspace_id == link.c.workspace_id,
                            pointer.c.current_link_id == link.c.id,
                        ),
                    )
                )
                .where(pointer.c.workspace_id == actor.workspace_id, link.c.id == link_id)
                .with_for_update(of=link)
            )
            .mappings()
            .one_or_none()
        )
        if current is None:
            return None
        if int(current["version_no"]) != expected_version:
            raise VersionConflict(dict(current))
        if action not in {"confirm", "change", "unlink", "restore"}:
            raise ValueError("link action must be confirm, change, unlink, or restore")
        if action == "restore" and (
            current["state"] != LinkState.REJECTED.value
            or current["detail_code"] != "manual_unlink"
        ):
            # Undo and "Link again" apply only to a line a person removed (PD-4b).
            raise ValueError("only a link a person removed can be linked again")
        source_id = cast(UUID, current["from_document_id"])
        old_target_id = cast(UUID, current["to_document_id"])
        source_row = connection.execute(
            sa.select(
                source_document.c.doc_type,
                sa.func.coalesce(source_vendor.c.merged_into, source_document.c.vendor_id).label(
                    "effective_vendor_id"
                ),
            )
            .select_from(
                source_document.outerjoin(
                    source_vendor,
                    sa.and_(
                        source_vendor.c.workspace_id == source_document.c.workspace_id,
                        source_vendor.c.id == source_document.c.vendor_id,
                    ),
                )
            )
            .where(
                source_document.c.workspace_id == actor.workspace_id,
                source_document.c.id == source_id,
            )
        ).one_or_none()
        old_target = connection.execute(
            sa.select(
                old_target_document.c.doc_type,
                sa.func.coalesce(
                    old_target_vendor.c.merged_into, old_target_document.c.vendor_id
                ).label("effective_vendor_id"),
            )
            .select_from(
                old_target_document.outerjoin(
                    old_target_vendor,
                    sa.and_(
                        old_target_vendor.c.workspace_id == old_target_document.c.workspace_id,
                        old_target_vendor.c.id == old_target_document.c.vendor_id,
                    ),
                )
            )
            .where(
                old_target_document.c.workspace_id == actor.workspace_id,
                old_target_document.c.id == old_target_id,
            )
        ).one_or_none()
        if source_row is None or old_target is None:
            return None
        if source_row.doc_type not in {DocType.INVOICE.value, DocType.GRN.value}:
            raise ValueError("only invoice or GRN links can be edited")
        if old_target.doc_type != DocType.PO.value:
            raise ValueError("the current link target is not a purchase order")
        if action in {"confirm", "restore"} and (
            source_row.effective_vendor_id is None
            or old_target.effective_vendor_id is None
            or source_row.effective_vendor_id != old_target.effective_vendor_id
        ):
            raise ValueError(
                "a link cannot be confirmed until source and PO have the same resolved vendor"
            )

        affected_ids = {source_id, old_target_id}
        audit_action: str
        if action == "confirm" and bool(current["ai_suggested"]):
            # An accepted AI suggestion becomes the operator's own selection: the
            # same manual header link the resolver re-proposes on every later run.
            proposal = ProposedLink(
                from_document_id=source_id,
                to_document_id=old_target_id,
                link_type=LinkType(str(current["link_type"])),
                level=LinkLevel.HEADER,
                method=LinkMethod.MANUAL,
                confidence=Decimal("1.00"),
                state=LinkState.CONFIRMED,
                evidence=MANUAL_SELECTION_EVIDENCE,
            )
            created_id = self._append_link(
                connection,
                workspace_id=actor.workspace_id,
                proposal=proposal,
                old=current,
                detail_code=cast(str | None, current["detail_code"]),
                actor_id=actor.actor_id,
                ai_suggested=True,
            )
            audit_action = "link.confirm"
        elif action == "confirm":
            proposal = ProposedLink(
                from_document_id=source_id,
                to_document_id=old_target_id,
                link_type=LinkType(str(current["link_type"])),
                level=LinkLevel(str(current["level"])),
                method=LinkMethod(str(current["method"])),
                confidence=cast(Decimal, current["confidence"]),
                state=LinkState.CONFIRMED,
                evidence=str(current["evidence_snippet"] or "Confirmed by an operator."),
                line_no=cast(int | None, current["from_line_no"]),
            )
            created_id = self._append_link(
                connection,
                workspace_id=actor.workspace_id,
                proposal=proposal,
                old=current,
                detail_code=cast(str | None, current["detail_code"]),
                actor_id=actor.actor_id,
            )
            audit_action = "link.confirm"
        elif action == "restore":
            # The same pairing a person removed, now confirmed by the person who restores it.
            proposal = ProposedLink(
                from_document_id=source_id,
                to_document_id=old_target_id,
                link_type=LinkType(str(current["link_type"])),
                level=LinkLevel(str(current["level"])),
                method=LinkMethod.MANUAL,
                confidence=Decimal("1.00"),
                state=LinkState.CONFIRMED,
                evidence="Linked again by an operator.",
                line_no=cast(int | None, current["from_line_no"]),
            )
            created_id = self._append_link(
                connection,
                workspace_id=actor.workspace_id,
                proposal=proposal,
                old=current,
                detail_code="manual_link_again",
                actor_id=actor.actor_id,
            )
            audit_action = "link.restore"
        elif action == "unlink":
            proposal = ProposedLink(
                from_document_id=source_id,
                to_document_id=old_target_id,
                link_type=LinkType(str(current["link_type"])),
                level=LinkLevel(str(current["level"])),
                method=LinkMethod.MANUAL,
                confidence=Decimal("1.00"),
                state=LinkState.REJECTED,
                evidence="Unlinked by an operator; this decision persists on re-run.",
                line_no=cast(int | None, current["from_line_no"]),
            )
            created_id = self._append_link(
                connection,
                workspace_id=actor.workspace_id,
                proposal=proposal,
                old=current,
                detail_code="manual_unlink",
                actor_id=actor.actor_id,
            )
            audit_action = "link.reject"
        else:
            if to_document_id is None:
                raise ValueError("a replacement purchase order is required")
            target_row = connection.execute(
                sa.select(
                    document.c.doc_type,
                    sa.func.coalesce(vendor.c.merged_into, document.c.vendor_id).label(
                        "effective_vendor_id"
                    ),
                )
                .select_from(
                    document.outerjoin(
                        vendor,
                        sa.and_(
                            vendor.c.workspace_id == document.c.workspace_id,
                            vendor.c.id == document.c.vendor_id,
                        ),
                    )
                )
                .where(
                    document.c.workspace_id == actor.workspace_id,
                    document.c.id == to_document_id,
                    document.c.withdrawn_at.is_(None),
                )
                .with_for_update(of=document)
            ).one_or_none()
            if target_row is None or target_row.doc_type != DocType.PO.value:
                raise ValueError("the replacement target must be a current workspace PO")
            if (
                source_row.effective_vendor_id is None
                or target_row.effective_vendor_id is None
                or source_row.effective_vendor_id != target_row.effective_vendor_id
            ):
                raise ValueError("a link can only point to a PO in the same resolved vendor group")
            rejected_old = ProposedLink(
                from_document_id=source_id,
                to_document_id=old_target_id,
                link_type=LinkType(str(current["link_type"])),
                level=LinkLevel(str(current["level"])),
                method=LinkMethod.MANUAL,
                confidence=Decimal("1.00"),
                state=LinkState.REJECTED,
                evidence="Replaced by an operator-selected PO.",
                line_no=cast(int | None, current["from_line_no"]),
            )
            self._append_link(
                connection,
                workspace_id=actor.workspace_id,
                proposal=rejected_old,
                old=current,
                detail_code="manual_link_change",
                actor_id=actor.actor_id,
            )
            new_link_type = (
                LinkType.INV_PO if source_row.doc_type == DocType.INVOICE.value else LinkType.GRN_PO
            )
            proposal = ProposedLink(
                from_document_id=source_id,
                to_document_id=to_document_id,
                link_type=new_link_type,
                level=LinkLevel(str(current["level"])),
                method=LinkMethod.MANUAL,
                confidence=Decimal("1.00"),
                state=LinkState.CONFIRMED,
                evidence="Manually selected by an operator.",
                line_no=cast(int | None, current["from_line_no"]),
            )
            target_pointer = (
                connection.execute(
                    sa.select(link)
                    .select_from(
                        pointer.join(
                            link,
                            sa.and_(
                                pointer.c.workspace_id == link.c.workspace_id,
                                pointer.c.current_link_id == link.c.id,
                            ),
                        )
                    )
                    .where(
                        pointer.c.workspace_id == actor.workspace_id,
                        pointer.c.link_type == new_link_type.value,
                        pointer.c.from_document_id == source_id,
                        pointer.c.from_line_no.is_not_distinct_from(current["from_line_no"]),
                        pointer.c.to_document_id == to_document_id,
                    )
                    .with_for_update(of=link)
                )
                .mappings()
                .one_or_none()
            )
            created_id = self._append_link(
                connection,
                workspace_id=actor.workspace_id,
                proposal=proposal,
                old=target_pointer,
                detail_code="manual_link_change",
                actor_id=actor.actor_id,
            )
            affected_ids.add(to_document_id)
            audit_action = "link.create"

        self._mark_affected_scopes(
            connection,
            workspace_id=actor.workspace_id,
            document_ids=affected_ids,
        )
        write_audit_event(
            connection,
            actor,
            action=cast(Any, audit_action),
            target_type="link",
            target_id=created_id,
            before_state={"link_id": str(link_id), "state": current["state"]},
            after_state={"link_id": str(created_id), "action": action},
        )
        return dict(
            connection.execute(sa.select(*link.c).where(link.c.id == created_id)).mappings().one()
        )

    @staticmethod
    def _load_current(
        connection: Connection,
        workspace_id: UUID,
    ) -> tuple[
        list[RowMapping],
        dict[UUID, list[RowMapping]],
        dict[UUID, str],
        dict[UUID, dict[str, RowMapping]],
        dict[UUID, dict[str, RowMapping]],
    ]:
        document = metadata.tables["document"]
        version = metadata.tables["document_version"]
        line = metadata.tables["doc_line"]
        text_table = metadata.tables["document_text"]
        evidence_table = metadata.tables["field_evidence"]
        selection = [
            document.c.id.label("document_id"),
            document.c.doc_type,
            document.c.natural_key,
            document.c.vendor_id,
            document.c.vendor_locked,
            document.c.current_version_id,
            document.c.input_seq,
            version.c.id.label("version_id"),
            version.c.seq.label("version_seq"),
            version.c.revision_rank,
            version.c.doc_no,
            version.c.canonical_doc_no,
            version.c.doc_date,
            version.c.po_status,
            version.c.revision,
            version.c.vendor_name,
            version.c.vendor_gstin,
            version.c.buyer_gstin,
            version.c.currency,
            version.c.taxable_total,
            version.c.cgst_total,
            version.c.sgst_total,
            version.c.igst_total,
            version.c.round_off,
            version.c.grand_total,
            version.c.po_refs,
            version.c.grn_refs,
            version.c.challan_no,
            version.c.supplier_invoice_no,
            version.c.e_way_bill_no,
            version.c.irn,
            version.c.place_of_supply,
            version.c.ship_to,
            version.c.payment_terms_text,
            version.c.delivery_date,
            version.c.extras,
            version.c.locator,
            version.c.needs_verification,
            version.c.verification_reasons,
        ]
        rows = list(
            connection.execute(
                sa.select(*selection)
                .select_from(
                    document.join(
                        version,
                        sa.and_(
                            document.c.workspace_id == version.c.workspace_id,
                            document.c.current_version_id == version.c.id,
                        ),
                    )
                )
                .where(
                    document.c.workspace_id == workspace_id,
                    document.c.withdrawn_at.is_(None),
                    # A document waiting for its AI reading is not used yet (PD-2f).
                    reading_state.ready_clause(document),
                )
                .order_by(document.c.natural_key, document.c.id)
            ).mappings()
        )
        version_ids = [cast(UUID, row["version_id"]) for row in rows]
        line_rows: dict[UUID, list[RowMapping]] = defaultdict(list)
        text_by_version: dict[UUID, str] = defaultdict(str)
        header_evidence: dict[UUID, dict[str, RowMapping]] = defaultdict(dict)
        line_evidence: dict[UUID, dict[str, RowMapping]] = defaultdict(dict)
        if version_ids:
            line_ids: list[UUID] = []
            for line_row in connection.execute(
                sa.select(*line.c)
                .where(
                    line.c.workspace_id == workspace_id, line.c.document_version_id.in_(version_ids)
                )
                .order_by(line.c.document_version_id, line.c.line_no)
            ).mappings():
                line_rows[cast(UUID, line_row["document_version_id"])].append(line_row)
                line_ids.append(cast(UUID, line_row["id"]))
            evidence_scope = [evidence_table.c.document_version_id.in_(version_ids)]
            if line_ids:
                evidence_scope.append(evidence_table.c.doc_line_id.in_(line_ids))
            for evidence_row in connection.execute(
                sa.select(*evidence_table.c)
                .where(
                    evidence_table.c.workspace_id == workspace_id,
                    sa.or_(*evidence_scope),
                )
                .order_by(evidence_table.c.created_at, evidence_table.c.id)
            ).mappings():
                document_version_id = cast(UUID | None, evidence_row["document_version_id"])
                doc_line_id = cast(UUID | None, evidence_row["doc_line_id"])
                field_name = str(evidence_row["field_name"])
                if document_version_id is not None:
                    header_evidence[document_version_id][field_name] = evidence_row
                elif doc_line_id is not None:
                    line_evidence[doc_line_id][field_name] = evidence_row
            page_groups: dict[UUID, list[str]] = defaultdict(list)
            for page in connection.execute(
                sa.select(text_table.c.document_version_id, text_table.c.content)
                .where(
                    text_table.c.workspace_id == workspace_id,
                    text_table.c.document_version_id.in_(version_ids),
                )
                .order_by(text_table.c.document_version_id, text_table.c.page)
            ):
                page_groups[cast(UUID, page.document_version_id)].append(str(page.content))
            text_by_version = {key: "\n".join(values) for key, values in page_groups.items()}
        return rows, line_rows, text_by_version, header_evidence, line_evidence

    @staticmethod
    def _current_link_context(
        connection: Connection,
        workspace_id: UUID,
    ) -> dict[UUID, tuple[set[UUID], set[UUID]]]:
        pointer = metadata.tables["link_current"]
        link = metadata.tables["link"]
        output: dict[UUID, tuple[set[UUID], set[UUID]]] = {}
        rows = connection.execute(
            sa.select(
                link.c.from_document_id,
                link.c.to_document_id,
                link.c.method,
                link.c.state,
            )
            .select_from(
                pointer.join(
                    link,
                    sa.and_(
                        pointer.c.workspace_id == link.c.workspace_id,
                        pointer.c.current_link_id == link.c.id,
                    ),
                )
            )
            .where(pointer.c.workspace_id == workspace_id)
        )
        for row in rows:
            source_id = cast(UUID, row.from_document_id)
            manual, rejected = output.setdefault(source_id, (set(), set()))
            if row.method == LinkMethod.MANUAL.value and row.state == LinkState.CONFIRMED.value:
                manual.add(cast(UUID, row.to_document_id))
            if row.method == LinkMethod.MANUAL.value and row.state == LinkState.REJECTED.value:
                rejected.add(cast(UUID, row.to_document_id))
        return output

    def _governing_pos(
        self,
        connection: Connection,
        workspace_id: UUID,
        candidates: Sequence[tuple[RowMapping, Document, UUID | None]],
    ) -> list[MappingPO]:
        grouped: dict[tuple[str, str], tuple[RowMapping, Document, UUID | None]] = {}
        for candidate_item in candidates:
            candidate_row, document, vendor_id = candidate_item
            canonical_no = str(
                candidate_row["canonical_doc_no"]
                or canonicalize_document_number(
                    document.header.doc_no.value if document.header.doc_no else ""
                )
            )
            key = (str(vendor_id or ""), canonical_no or str(candidate_row["document_id"]))
            current = grouped.get(key)
            rank = (
                int(candidate_row["revision_rank"] or 0),
                int(candidate_row["version_seq"]),
                str(candidate_row["document_id"]),
            )
            current_rank = (
                (
                    int(current[0]["revision_rank"] or 0),
                    int(current[0]["version_seq"]),
                    str(current[0]["document_id"]),
                )
                if current
                else None
            )
            if current is None or rank > cast(tuple[int, int, str], current_rank):
                grouped[key] = candidate_item

        selected = list(grouped.values())
        po_ids = [cast(UUID, selected_row["document_id"]) for selected_row, _, _ in selected]
        line = metadata.tables["doc_line"]
        ledger = metadata.tables["po_line_ledger"]
        ledger_rows: dict[UUID, dict[int, RowMapping]] = defaultdict(dict)
        if po_ids:
            for ledger_row in connection.execute(
                sa.select(*ledger.c).where(
                    ledger.c.workspace_id == workspace_id, ledger.c.po_document_id.in_(po_ids)
                )
            ).mappings():
                ledger_rows[cast(UUID, ledger_row["po_document_id"])][
                    int(ledger_row["po_line_no"])
                ] = ledger_row
        support_by_source = self._support_by_source(connection, workspace_id, po_ids)

        result: list[MappingPO] = []
        for selected_row, document, vendor_id in selected:
            document_id = cast(UUID, selected_row["document_id"])
            version_id = cast(UUID, selected_row["version_id"])
            line_rows = connection.execute(
                sa.select(*line.c)
                .where(
                    line.c.workspace_id == workspace_id,
                    line.c.document_version_id == version_id,
                )
                .order_by(line.c.line_no)
            ).mappings()
            open_qty: dict[int, Decimal] = {}
            grn_qty: dict[int, Decimal] = {}
            open_grn_qty: dict[int, Decimal] = {}
            derived_balance = Decimal("0")
            for po_line_row in line_rows:
                line_no = int(po_line_row["line_no"])
                quantity = cast(Decimal | None, po_line_row["quantity"]) or Decimal("0")
                rate = cast(Decimal | None, po_line_row["rate"]) or Decimal("0")
                prior = ledger_rows.get(document_id, {}).get(line_no)
                available = (
                    cast(Decimal | None, prior["open_qty"]) if prior is not None else quantity
                )
                open_qty[line_no] = available if available is not None else quantity
                grn_qty[line_no] = (
                    cast(Decimal | None, prior["received_accepted"]) or Decimal("0")
                    if prior is not None
                    else Decimal("0")
                )
                open_grn_qty[line_no] = (
                    cast(Decimal | None, prior["grni_qty"]) or Decimal("0")
                    if prior is not None
                    else Decimal("0")
                )
                derived_balance += open_qty[line_no] * rate
            result.append(
                MappingPO(
                    document_id=document_id,
                    vendor_id=vendor_id,
                    document=document,
                    open_balance=derived_balance,
                    open_qty_by_line=open_qty,
                    grn_support_qty_by_line=grn_qty,
                    open_grn_qty_by_line=open_grn_qty,
                    grn_support_by_source=support_by_source.get(document_id, {}),
                )
            )
        return result

    @staticmethod
    def _support_by_source(
        connection: Connection, workspace_id: UUID, po_ids: Sequence[UUID]
    ) -> dict[UUID, dict[UUID, dict[int, Decimal]]]:
        """Receipt support per PO, per source document (WI-D4, D90: no self-support).

        Only ledger receipts whose GRN-to-PO link is already auto or confirmed count,
        plus opening balances (key ``OPENING_SUPPORT``); a suggested link supports no
        check, and the matcher leaves out each source's own receipt in its own check.
        """

        if not po_ids:
            return {}
        ledger = metadata.tables["po_line_ledger"]
        entry = metadata.tables["ledger_entry"]
        pointer = metadata.tables["link_current"]
        link = metadata.tables["link"]
        verified = {
            (cast(UUID, row.from_document_id), cast(UUID, row.to_document_id))
            for row in connection.execute(
                sa.select(link.c.from_document_id, link.c.to_document_id)
                .select_from(
                    pointer.join(
                        link,
                        sa.and_(
                            pointer.c.workspace_id == link.c.workspace_id,
                            pointer.c.current_link_id == link.c.id,
                        ),
                    )
                )
                .where(
                    pointer.c.workspace_id == workspace_id,
                    pointer.c.to_document_id.in_(po_ids),
                    pointer.c.link_type == "grn_po",
                    link.c.state.in_((LinkState.AUTO.value, LinkState.CONFIRMED.value)),
                )
            )
        }
        support: dict[UUID, dict[UUID, dict[int, Decimal]]] = defaultdict(dict)
        for row in connection.execute(
            sa.select(
                ledger.c.po_document_id,
                ledger.c.po_line_no,
                entry.c.source_document_id,
                entry.c.opening_balance_id,
                entry.c.accepted_delta,
            )
            .select_from(ledger.join(entry, entry.c.po_line_ledger_id == ledger.c.id))
            .where(
                ledger.c.workspace_id == workspace_id,
                entry.c.workspace_id == workspace_id,
                ledger.c.po_document_id.in_(po_ids),
            )
        ):
            accepted = cast(Decimal | None, row.accepted_delta) or Decimal("0")
            if not accepted:
                continue
            po_id = cast(UUID, row.po_document_id)
            source_id = cast(UUID | None, row.source_document_id)
            if row.opening_balance_id is not None:
                key = OPENING_SUPPORT
            elif source_id is not None and (source_id, po_id) in verified:
                key = source_id
            else:
                continue
            lines = support[po_id].setdefault(key, {})
            line_no = int(row.po_line_no)
            lines[line_no] = lines.get(line_no, Decimal("0")) + accepted
        return support

    @staticmethod
    def _mapping_config(connection: Connection, workspace_id: UUID) -> MappingConfig:
        active = metadata.tables["config_active"]
        version = metadata.tables["config_version"]
        body = connection.scalar(
            sa.select(version.c.body)
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
        return mapping_config_from_body(body)

    @staticmethod
    def _current_links(
        connection: Connection,
        workspace_id: UUID,
    ) -> dict[tuple[UUID, int | None, UUID], RowMapping]:
        pointer = metadata.tables["link_current"]
        link = metadata.tables["link"]
        rows = connection.execute(
            sa.select(*link.c)
            .select_from(
                pointer.join(
                    link,
                    sa.and_(
                        pointer.c.workspace_id == link.c.workspace_id,
                        pointer.c.current_link_id == link.c.id,
                    ),
                )
            )
            .where(pointer.c.workspace_id == workspace_id)
        ).mappings()
        return {
            (
                cast(UUID, row["from_document_id"]),
                cast(int | None, row["from_line_no"]),
                cast(UUID, row["to_document_id"]),
            ): row
            for row in rows
        }

    def _persist_resolution(
        self,
        connection: Connection,
        *,
        workspace_id: UUID,
        source: MappingDocument,
        result: LinkResolution,
        current: dict[tuple[UUID, int | None, UUID], RowMapping],
        input_hash: str,
    ) -> tuple[int, set[UUID]]:
        resolution = metadata.tables["link_resolution"]
        old_hash = connection.scalar(
            sa.select(resolution.c.input_hash).where(
                resolution.c.workspace_id == workspace_id,
                resolution.c.document_id == source.document_id,
            )
        )
        desired = {
            (link.from_document_id, link.line_no, link.to_document_id): link
            for link in result.links
        }
        existing_for_source = {
            key: row for key, row in current.items() if key[0] == source.document_id
        }
        if old_hash == input_hash and all(
            key in existing_for_source and self._keeps(existing_for_source[key], link)
            for key, link in desired.items()
        ):
            return 0, set()

        inserted = 0
        affected: set[UUID] = set()
        for key, prior_link_row in existing_for_source.items():
            if key in desired or prior_link_row["state"] == LinkState.REJECTED.value:
                continue
            rejected = ProposedLink(
                from_document_id=cast(UUID, prior_link_row["from_document_id"]),
                to_document_id=cast(UUID, prior_link_row["to_document_id"]),
                link_type=LinkType(str(prior_link_row["link_type"])),
                level=LinkLevel(str(prior_link_row["level"])),
                method=LinkMethod(str(prior_link_row["method"])),
                confidence=cast(Decimal, prior_link_row["confidence"]),
                state=LinkState.REJECTED,
                evidence="Superseded by a later deterministic resolution.",
                line_no=cast(int | None, prior_link_row["from_line_no"]),
            )
            new_id_value = self._append_link(
                connection,
                workspace_id=workspace_id,
                proposal=rejected,
                old=prior_link_row,
                detail_code="superseded_by_resolution",
            )
            inserted += 1
            current[key] = self._load_link_row(connection, new_id_value)
            affected.update((source.document_id, cast(UUID, prior_link_row["to_document_id"])))

        for key, proposal in desired.items():
            prior_desired_link = existing_for_source.get(key)
            if prior_desired_link is not None and self._keeps(prior_desired_link, proposal):
                continue
            new_id_value = self._append_link(
                connection,
                workspace_id=workspace_id,
                proposal=proposal,
                old=prior_desired_link,
                detail_code=(result.reason_detail if result.reason_detail else None),
            )
            inserted += 1
            current[key] = self._load_link_row(connection, new_id_value)
            affected.update((source.document_id, proposal.to_document_id))

        if result.links:
            resolved_step = min(self._step_for_method(proposal.method) for proposal in result.links)
        else:
            resolved_step = None
        values = {
            "id": new_id(),
            "workspace_id": workspace_id,
            "document_id": source.document_id,
            "outcome": result.outcome.value,
            "resolved_step": resolved_step,
            "detail_code": result.reason_detail,
            "cited_refs": [result.reference_text] if result.reference_text else [],
            "candidates": [
                {
                    "po_document_id": str(candidate.po_document_id),
                    "po_no": candidate.po_no,
                    "score": format(candidate.score, "f"),
                    "evidence": candidate.evidence,
                    "line_no": candidate.line_no,
                }
                for candidate in result.candidates
            ],
            "input_hash": input_hash,
        }
        connection.execute(
            pg_insert(resolution)
            .values(**values)
            .on_conflict_do_update(
                index_elements=[resolution.c.workspace_id, resolution.c.document_id],
                set_={
                    key: value
                    for key, value in values.items()
                    if key not in {"id", "workspace_id", "document_id"}
                },
            )
        )
        return inserted, affected

    @staticmethod
    def _matches(current: RowMapping, proposed: ProposedLink) -> bool:
        return bool(
            current["link_type"] == proposed.link_type.value
            and current["level"] == proposed.level.value
            and current["method"] == proposed.method.value
            and current["state"] == proposed.state.value
            and current["confidence"] == proposed.confidence
            and current["evidence_snippet"] == proposed.evidence
            and current["from_line_no"] == proposed.line_no
        )

    @classmethod
    def _keeps(cls, current: RowMapping, proposed: ProposedLink) -> bool:
        """Whether the current link already stands for this proposal.

        A person's confirmation of the same pairing (document, line, PO, link type) stays
        while the resolver still proposes that pairing; a re-run must not turn it back into
        a suggestion. A pairing the resolver no longer proposes is superseded as before.
        """

        if cls._matches(current, proposed):
            return True
        return bool(
            current["state"] == LinkState.CONFIRMED.value
            and proposed.state is not LinkState.REJECTED
            and current["link_type"] == proposed.link_type.value
            and current["level"] == proposed.level.value
            and current["from_line_no"] == proposed.line_no
            and current["to_document_id"] == proposed.to_document_id
        )

    @staticmethod
    def _step_for_method(method: LinkMethod) -> int:
        return {
            LinkMethod.LABELED: 1,
            LinkMethod.REVERSE: 2,
            LinkMethod.VIA_GRN: 3,
            LinkMethod.CONTENT: 4,
            LinkMethod.MANUAL: 0,
        }[method]

    def _append_link(
        self,
        connection: Connection,
        *,
        workspace_id: UUID,
        proposal: ProposedLink,
        old: RowMapping | None,
        detail_code: str | None,
        actor_id: UUID | None = None,
        ai_suggested: bool = False,
        ai_call_id: UUID | None = None,
        evidence: Mapping[str, Any] | None = None,
    ) -> UUID:
        link = metadata.tables["link"]
        pointer = metadata.tables["link_current"]
        link_id = new_id()
        version_no = int(old["version_no"]) + 1 if old is not None else 1
        connection.execute(
            sa.insert(link).values(
                id=link_id,
                workspace_id=workspace_id,
                link_type=proposal.link_type.value,
                level=proposal.level.value,
                from_document_id=proposal.from_document_id,
                from_line_no=proposal.line_no,
                to_document_id=proposal.to_document_id,
                version_no=version_no,
                supersedes_link_id=old["id"] if old is not None else None,
                method=proposal.method.value,
                confidence=proposal.confidence,
                state=proposal.state.value,
                ai_suggested=ai_suggested,
                ai_call_id=ai_call_id,
                evidence_snippet=proposal.evidence,
                evidence=dict(evidence)
                if evidence is not None
                else {"via_document_id": str(proposal.via_document_id)}
                if proposal.via_document_id
                else {},
                detail_code=detail_code,
                created_by=actor_id,
                confirmed_by=actor_id if proposal.state is LinkState.CONFIRMED else None,
                confirmed_at=sa.func.now() if proposal.state is LinkState.CONFIRMED else None,
            )
        )
        key = [
            pointer.c.workspace_id,
            pointer.c.link_type,
            pointer.c.from_document_id,
            pointer.c.from_line_no,
            pointer.c.to_document_id,
        ]
        statement = pg_insert(pointer).values(
            id=new_id(),
            workspace_id=workspace_id,
            link_type=proposal.link_type.value,
            from_document_id=proposal.from_document_id,
            from_line_no=proposal.line_no,
            to_document_id=proposal.to_document_id,
            current_link_id=link_id,
        )
        connection.execute(
            statement.on_conflict_do_update(
                index_elements=key,
                set_={"current_link_id": link_id, "updated_at": sa.func.now()},
            )
        )
        return link_id

    def _bump_vendor_scope(
        self,
        connection: Connection,
        *,
        workspace_id: UUID,
        vendor_ids: set[UUID],
        reason: str,
    ) -> int:
        vendor = metadata.tables["vendor"]
        scopes = metadata.tables["dirty_scope"]
        for vendor_id in sorted(vendor_ids, key=str):
            connection.execute(
                sa.update(vendor)
                .where(vendor.c.workspace_id == workspace_id, vendor.c.id == vendor_id)
                .values(
                    input_seq=vendor.c.input_seq + 1,
                    updated_at=sa.func.now(),
                    version=vendor.c.version + 1,
                )
            )
            statement = pg_insert(scopes).values(
                id=new_id(),
                workspace_id=workspace_id,
                scope_kind="vendor",
                scope_id=vendor_id,
                reason=reason,
            )
            connection.execute(
                statement.on_conflict_do_update(
                    index_elements=[scopes.c.workspace_id, scopes.c.scope_kind, scopes.c.scope_id],
                    index_where=scopes.c.processed_at.is_(None),
                    set_={
                        "mark_count": scopes.c.mark_count + 1,
                        "last_marked_at": sa.func.now(),
                        "reason": statement.excluded.reason,
                    },
                )
            )
        self._enqueue_pending_recomputes(workspace_id, connection)
        return len(vendor_ids)

    @staticmethod
    def _load_link_row(connection: Connection, link_id: UUID) -> RowMapping:
        link = metadata.tables["link"]
        row = connection.execute(sa.select(*link.c).where(link.c.id == link_id)).mappings().one()
        return row

    def _mark_affected_scopes(
        self,
        connection: Connection,
        *,
        workspace_id: UUID,
        document_ids: set[UUID],
    ) -> int:
        if not document_ids:
            return 0
        document = metadata.tables["document"]
        scopes = metadata.tables["dirty_scope"]
        rows = connection.execute(
            sa.select(document.c.id, document.c.doc_type, document.c.vendor_id)
            .where(document.c.workspace_id == workspace_id, document.c.id.in_(document_ids))
            .order_by(document.c.id)
        ).all()
        count = 0
        for document_id, doc_type, vendor_id in rows:
            document_id = cast(UUID, document_id)
            if doc_type == DocType.PO.value:
                connection.execute(
                    sa.update(document)
                    .where(document.c.workspace_id == workspace_id, document.c.id == document_id)
                    .values(input_seq=document.c.input_seq + 1)
                )
                scope_rows = [("po", document_id)]
            else:
                connection.execute(
                    sa.update(document)
                    .where(document.c.workspace_id == workspace_id, document.c.id == document_id)
                    .values(input_seq=document.c.input_seq + 1)
                )
                scope_rows = [("invoice", document_id)]
                if vendor_id is not None:
                    scope_rows.append(("vendor", cast(UUID, vendor_id)))
            for scope_kind, scope_id in scope_rows:
                statement = pg_insert(scopes).values(
                    id=new_id(),
                    workspace_id=workspace_id,
                    scope_kind=scope_kind,
                    scope_id=scope_id,
                    reason="link_resolution_changed",
                )
                connection.execute(
                    statement.on_conflict_do_update(
                        index_elements=[
                            scopes.c.workspace_id,
                            scopes.c.scope_kind,
                            scopes.c.scope_id,
                        ],
                        index_where=scopes.c.processed_at.is_(None),
                        set_={
                            "mark_count": scopes.c.mark_count + 1,
                            "last_marked_at": sa.func.now(),
                            "reason": statement.excluded.reason,
                        },
                    )
                )
                count += 1
        self._enqueue_pending_recomputes(workspace_id, connection)
        return count


__all__ = ["MappingService", "MappingSummary"]
