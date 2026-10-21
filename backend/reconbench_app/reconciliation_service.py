"""Persisted reconciliation runs backed by the deterministic core engine."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal, cast
from uuid import NAMESPACE_URL, UUID, uuid5

import sqlalchemy as sa
from reconbench_core.align import AlignmentConfig, AlignmentWeights
from reconbench_core.recon.engine import build_run_manifest, evaluate_components
from reconbench_core.recon.types import (
    OpeningBalance,
    ReconciliationConfig,
    ReconciliationDocument,
    ReconciliationLink,
    ReconciliationSnapshot,
    ToleranceRule,
    ToleranceValues,
)
from reconbench_core.schema import (
    CaseResult,
    CaseStatus,
    DocType,
    LinkLevel,
    LinkMethod,
    LinkState,
    RunStatus,
    ToleranceMode,
)
from reconbench_core.version import ENGINE_VERSION
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Engine, RowMapping

from reconbench_app.authorization import requires
from reconbench_app.db.ids import new_id
from reconbench_app.db.metadata import metadata
from reconbench_app.json_serialization import to_jsonable
from reconbench_app.mapping_service import MappingService, _make_document
from reconbench_app.reconciliation_parallel import evaluate_components_parallel

if TYPE_CHECKING:
    from reconbench_app.ai.enqueue import AiHookEnqueuer

_LOGGER = logging.getLogger(__name__)


def _decimal(value: object, default: str = "0") -> Decimal:
    try:
        parsed = Decimal(str(value))
    except Exception:
        return Decimal(default)
    return parsed if parsed.is_finite() else Decimal(default)


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _tolerance_values(value: object) -> ToleranceValues:
    raw = _mapping(value)
    return ToleranceValues(pct=_decimal(raw.get("pct")), abs=_decimal(raw.get("abs")))


def _vendor_family(vendor_id: str, vendor_roots: Mapping[UUID, UUID]) -> list[str]:
    """Return the other vendor ids in the override vendor's merge family."""

    try:
        own = UUID(vendor_id)
    except ValueError:
        return []
    root = own
    for _ in range(len(vendor_roots) + 1):
        parent = vendor_roots.get(root, root)
        if parent == root:
            break
        root = parent
    family = [
        str(member)
        for member, member_root in vendor_roots.items()
        if member != own and member_root == root
    ]
    if root != own:
        family.append(str(root))
    return list(dict.fromkeys(family))


def _fold_overrides(
    engine: Mapping[str, Any],
    tolerances: dict[str, dict[str, dict[str, ToleranceValues]]],
    vendor_roots: Mapping[UUID, UUID],
) -> None:
    """Apply Schema §5.1 ``engine.overrides`` onto the per-rule override maps.

    Values reach every id in the vendor's merge family first; an override set
    on a vendor id itself is applied last so it is never replaced by a
    relative's override.
    """

    overrides = _mapping(engine.get("overrides"))
    # (rule, map name, key, value, exact) in application order.
    entries: list[tuple[str, str, str, ToleranceValues, bool]] = []

    def scoped(rule: object, target: str, vendor_id: str, suffix: str, value: object) -> None:
        if str(rule) not in tolerances:
            return
        values = _tolerance_values(value)
        for member in _vendor_family(vendor_id, vendor_roots):
            entries.append((str(rule), target, f"{member}{suffix}", values, False))
        entries.append((str(rule), target, f"{vendor_id}{suffix}", values, True))

    for vendor_override in overrides.get("vendors", []) or []:
        item = _mapping(vendor_override)
        if item.get("vendor_id"):
            for rule, value in _mapping(item.get("tolerances")).items():
                scoped(rule, "by_vendor", str(item["vendor_id"]), "", value)
    for item_override in overrides.get("items", []) or []:
        item = _mapping(item_override)
        item_key = str(item.get("item_code") or "").casefold().strip()
        if not item_key:
            continue
        for rule, value in _mapping(item.get("tolerances")).items():
            if item.get("vendor_id"):
                scoped(rule, "by_vendor_item", str(item["vendor_id"]), f"|{item_key}", value)
            elif str(rule) in tolerances:
                entries.append((str(rule), "by_item", item_key, _tolerance_values(value), True))
    for rule, target, key, values, _exact in sorted(entries, key=lambda entry: entry[4]):
        tolerances[rule][target][key] = values


def reconciliation_config(
    body: Mapping[str, Any],
    *,
    effective_date: date,
    vendor_roots: Mapping[UUID, UUID] | None = None,
) -> ReconciliationConfig:
    """Convert the active canonical Schema §10 config into the pure engine model.

    ``vendor_roots`` maps each vendor id to its ``merged_into`` survivor so a
    vendor override covers documents still attached to merged vendor ids.
    """

    engine = _mapping(body.get("engine"))
    raw_tolerances = _mapping(engine.get("tolerances"))
    scoped: dict[str, dict[str, dict[str, ToleranceValues]]] = {}
    for name, raw_value in raw_tolerances.items():
        if name == "limit_mode":
            continue
        raw = _mapping(raw_value)
        scoped[str(name)] = {
            "by_vendor": {
                str(key): _tolerance_values(value)
                for key, value in _mapping(raw.get("by_vendor")).items()
            },
            "by_item": {
                str(key).casefold().strip(): _tolerance_values(value)
                for key, value in _mapping(raw.get("by_item")).items()
            },
            "by_vendor_item": {},
        }
    _fold_overrides(engine, scoped, vendor_roots or {})
    tolerances: dict[str, ToleranceRule] = {}
    for name, raw_value in raw_tolerances.items():
        if name == "limit_mode":
            continue
        raw = _mapping(raw_value)
        tolerances[str(name)] = ToleranceRule(
            pct=_decimal(raw.get("pct")),
            abs=_decimal(raw.get("abs")),
            by_vendor=scoped[str(name)]["by_vendor"],
            by_item=scoped[str(name)]["by_item"],
            by_vendor_item=scoped[str(name)]["by_vendor_item"],
        )

    thresholds = _mapping(engine.get("thresholds"))
    policies = _mapping(engine.get("policies"))
    duplicate = _mapping(policies.get("duplicate"))
    weights = _mapping(engine.get("weights"))
    align_weights = _mapping(weights.get("align"))
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
    buyer_entities = tuple(
        str(item["gstin"])
        for item in engine.get("buyer_entities", [])
        if isinstance(item, Mapping) and item.get("gstin")
    )
    tax_rates: set[Decimal] = set()
    for slab in engine.get("gst_slabs", []):
        if not isinstance(slab, Mapping) or slab.get("rate") is None:
            continue
        starts = date.fromisoformat(str(slab["from"])) if slab.get("from") else None
        ends = date.fromisoformat(str(slab["to"])) if slab.get("to") else None
        if (starts is None or starts <= effective_date) and (
            ends is None or effective_date <= ends
        ):
            tax_rates.add(_decimal(slab["rate"]))
    return ReconciliationConfig(
        tolerances=tolerances or ReconciliationConfig().tolerances,
        limit_mode=ToleranceMode(str(_mapping(engine.get("tolerances")).get("limit_mode", "or"))),
        extraction_gate=_decimal(thresholds.get("extraction_gate"), "0.70"),
        link_auto_accept=_decimal(thresholds.get("link_auto_accept"), "0.95"),
        pending_ageing_days=int(policies.get("pending_ageing_days", 30)),
        duplicate_suspect_days=int(duplicate.get("suspect_days", 3)),
        duplicate_suspect_edit_distance=int(duplicate.get("suspect_edit_distance", 2)),
        payable_rate_mode=cast(
            Literal["pay_at_po_rate", "hold"],
            str(policies.get("payable_rate_mode", "pay_at_po_rate")),
        ),
        date_order_level=cast(
            Literal["fail", "warn"], str(policies.get("date_order_level", "fail"))
        ),
        tax_rates=tuple(sorted(tax_rates)) or ReconciliationConfig().tax_rates,
        buyer_entities=buyer_entities,
        alignment=alignment,
        item_unit_aliases={
            str(key): str(value)
            for key, value in _mapping(_mapping(engine.get("uom")).get("aliases")).items()
        },
    )


class ReconciliationService:
    """Loads one immutable workspace snapshot, evaluates it, and persists append-only results."""

    def __init__(
        self,
        engine: Engine,
        *,
        process_workers: int = 1,
        ai_hooks: AiHookEnqueuer | None = None,
    ) -> None:
        self.engine = engine
        self.ai_hooks = ai_hooks
        self.mapping = MappingService(engine)
        if process_workers < 1 or process_workers > 8:
            raise ValueError("process_workers must be between one and eight")
        self.process_workers = process_workers

    @requires("recon.run")
    def enqueue_pending_grn_rechecks(
        self,
        workspace_id: UUID,
        grn_document_ids: Sequence[UUID],
        queue: Any,
    ) -> tuple[UUID, ...]:
        """Queue one idempotent auto-pending run for pending invoices supported by new GRNs."""

        requested_grn_ids = sorted(set(grn_document_ids), key=str)
        if not requested_grn_ids:
            return ()

        document = metadata.tables["document"]
        version = metadata.tables["document_version"]
        link = metadata.tables["link"]
        link_current = metadata.tables["link_current"]
        current = metadata.tables["case_current"]
        recon_case = metadata.tables["recon_case"]
        run = metadata.tables["recon_run"]
        active = metadata.tables["config_active"]
        config_version = metadata.tables["config_version"]
        queued_runs: list[UUID] = []

        with self.engine.begin() as connection:
            grn_rows = list(
                connection.execute(
                    sa.select(document.c.id, version.c.id.label("version_id"), document.c.input_seq)
                    .select_from(
                        document.join(version, document.c.current_version_id == version.c.id)
                    )
                    .where(
                        document.c.workspace_id == workspace_id,
                        document.c.id.in_(requested_grn_ids),
                        document.c.doc_type == DocType.GRN.value,
                        document.c.withdrawn_at.is_(None),
                    )
                    .order_by(document.c.id)
                ).mappings()
            )
            if not grn_rows:
                return ()

            purchase_order = document.alias("pending_recheck_po")
            affected_po_ids = set(
                connection.scalars(
                    sa.select(link.c.to_document_id)
                    .select_from(
                        link_current.join(
                            link,
                            sa.and_(
                                link_current.c.workspace_id == link.c.workspace_id,
                                link_current.c.current_link_id == link.c.id,
                            ),
                        ).join(
                            purchase_order,
                            sa.and_(
                                purchase_order.c.workspace_id == link.c.workspace_id,
                                purchase_order.c.id == link.c.to_document_id,
                            ),
                        )
                    )
                    .where(
                        link_current.c.workspace_id == workspace_id,
                        link_current.c.link_type == "grn_po",
                        link_current.c.from_document_id.in_([row["id"] for row in grn_rows]),
                        link.c.state.in_((LinkState.AUTO.value, LinkState.CONFIRMED.value)),
                        purchase_order.c.doc_type == DocType.PO.value,
                        purchase_order.c.withdrawn_at.is_(None),
                    )
                ).all()
            )
            if not affected_po_ids:
                return ()

            pending_rows = connection.execute(
                sa.select(current.c.invoice_id, recon_case.c.po_seqs)
                .select_from(current.join(recon_case, current.c.case_id == recon_case.c.id))
                .where(
                    current.c.workspace_id == workspace_id,
                    current.c.effective_status == CaseStatus.PENDING.value,
                )
                .order_by(current.c.invoice_id)
            ).all()
            affected_invoice_ids = sorted(
                {
                    cast(UUID, invoice_id)
                    for invoice_id, po_seqs in pending_rows
                    if isinstance(po_seqs, Mapping)
                    and set(po_seqs).intersection(str(po_id) for po_id in affected_po_ids)
                },
                key=str,
            )
            if not affected_invoice_ids:
                return ()

            scope = {
                "kind": "auto_pending",
                "invoice_ids": [str(item) for item in affected_invoice_ids],
                "purchase_order_ids": [str(item) for item in sorted(affected_po_ids, key=str)],
                "trigger_versions": [
                    {
                        "document_id": str(row["id"]),
                        "version_id": str(row["version_id"]),
                        "input_seq": int(row["input_seq"]),
                    }
                    for row in grn_rows
                ],
            }
            scope_hash = _canonical_hash(scope)
            run_id = uuid5(
                NAMESPACE_URL,
                f"reconbench:auto-pending:{workspace_id}:{scope_hash}",
            )
            config_row = connection.execute(
                sa.select(config_version.c.id, config_version.c.engine_hash)
                .select_from(
                    active.join(
                        config_version,
                        sa.and_(
                            active.c.workspace_id == config_version.c.workspace_id,
                            active.c.config_version_id == config_version.c.id,
                        ),
                    )
                )
                .where(active.c.workspace_id == workspace_id)
            ).one_or_none()
            if config_row is None:
                return ()

            inserted_id = connection.scalar(
                pg_insert(run)
                .values(
                    id=run_id,
                    workspace_id=workspace_id,
                    status=RunStatus.QUEUED.value,
                    trigger_kind="auto_pending",
                    scope=scope,
                    scope_hash=scope_hash,
                    run_date=date.today(),
                    engine_version=ENGINE_VERSION,
                    rules_hash=str(config_row.engine_hash),
                    config_version_id=cast(UUID, config_row.id),
                    ai_mode="rules_only",
                    ai_consent=False,
                    total_cases=0,
                    done_cases=0,
                    status_counts={},
                    metrics={},
                )
                .on_conflict_do_nothing(index_elements=[run.c.id])
                .returning(run.c.id)
            )
            if inserted_id is None:
                return ()

            job_id = queue.enqueue_reconciliation(
                run_id=run_id,
                connection=connection,
            )
            connection.execute(sa.update(run).where(run.c.id == run_id).values(job_id=job_id))
            queued_runs.append(run_id)

        return tuple(queued_runs)

    @requires("recon.run")
    def evaluate_run(self, run_id: UUID) -> tuple[CaseResult, ...]:
        """Execute a queued run; a failed evaluation leaves no partial case rows."""

        run_table = metadata.tables["recon_run"]
        started = time.monotonic()
        with self.engine.begin() as connection:
            run = (
                connection.execute(
                    sa.select(*run_table.c).where(run_table.c.id == run_id).with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if run is None or run["status"] != RunStatus.QUEUED.value:
                return ()
            connection.execute(
                sa.update(run_table)
                .where(run_table.c.id == run_id)
                .values(
                    status=RunStatus.RUNNING.value,
                    started_at=datetime.now(UTC),
                    version=run_table.c.version + 1,
                )
            )

        try:
            run_date = cast(date, run["run_date"])
            scope = _mapping(run["scope"])
            snapshot_data = self._load_snapshot(
                cast(UUID, run["workspace_id"]), run_date=run_date, scope=scope
            )
            (
                snapshot,
                manifest,
                config,
                config_version_id,
                target_invoice_ids,
                row_by_id,
                line_rows,
            ) = snapshot_data
            with self.engine.begin() as connection:
                run_table = metadata.tables["recon_run"]
                connection.execute(
                    sa.update(run_table)
                    .where(run_table.c.id == run_id, run_table.c.status == RunStatus.RUNNING.value)
                    .values(total_cases=len(target_invoice_ids), version=run_table.c.version + 1)
                )
            results = evaluate_components_parallel(
                snapshot,
                config,
                cast(date, run["run_date"]),
                max_workers=self.process_workers,
            )
            selected_results = tuple(
                result for result in results if result.invoice_id in target_invoice_ids
            )
            duration_ms = max(0, int((time.monotonic() - started) * 1000))
            self._persist_results(
                run_id=run_id,
                workspace_id=cast(UUID, run["workspace_id"]),
                snapshot=snapshot,
                manifest=manifest,
                config_version_id=config_version_id,
                results=selected_results,
                row_by_id=row_by_id,
                line_rows=line_rows,
                duration_ms=duration_ms,
            )
        except Exception:
            self._mark_failed(run_id)
            raise
        self._queue_item_opinions(cast(UUID, run["workspace_id"]), run_id)
        return selected_results

    def _queue_item_opinions(self, workspace_id: UUID, run_id: UUID) -> None:
        """Queue advisory H2 opinions once the run has succeeded; the run never waits for them."""

        if self.ai_hooks is None:
            return
        try:
            with self.engine.begin() as connection:
                self.ai_hooks.item_opinions(connection, workspace_id, run_id)
        except Exception as exc:  # noqa: BLE001 - AI is optional; the run already succeeded.
            _LOGGER.warning("ai_enqueue_failed hook=h2_item error_type=%s", type(exc).__name__)

    @requires("recon.run")
    def manual_reconcile(
        self,
        workspace_id: UUID,
        *,
        invoice_id: UUID,
        po_ids: Sequence[UUID],
        grn_ids: Sequence[UUID],
        run_date: date | None = None,
    ) -> CaseResult:
        """Evaluate one invoice immediately with operator-selected PO/GRN evidence."""

        effective_date = run_date or date.today()
        snapshot, _manifest, config, _config_id, _targets, _rows, _lines = self._load_snapshot(
            workspace_id, run_date=effective_date, scope={"kind": "all"}
        )
        records = {item.document_id: item for item in snapshot.documents}
        invoice = records.get(invoice_id)
        if invoice is None or invoice.document.header.doc_type is not DocType.INVOICE:
            raise ValueError("the selected invoice is not current in this workspace")
        if not po_ids:
            raise ValueError("at least one PO must be selected for Manual Recon")
        if any(
            po_id not in records or records[po_id].document.header.doc_type is not DocType.PO
            for po_id in po_ids
        ):
            raise ValueError("every selected PO must be current in this workspace")
        if any(
            grn_id not in records or records[grn_id].document.header.doc_type is not DocType.GRN
            for grn_id in grn_ids
        ):
            raise ValueError("every selected GRN must be current in this workspace")

        selected_sources = {invoice_id, *grn_ids}
        links = [link for link in snapshot.links if link.from_document_id not in selected_sources]
        for source_id in (invoice_id, *grn_ids):
            for po_id in po_ids:
                links.append(
                    ReconciliationLink(
                        link_id=uuid5(
                            NAMESPACE_URL,
                            f"reconbench:manual:{workspace_id}:{source_id}:{po_id}",
                        ),
                        from_document_id=source_id,
                        to_document_id=po_id,
                        method=LinkMethod.MANUAL,
                        state=LinkState.CONFIRMED,
                        confidence=Decimal("1"),
                        level=LinkLevel.HEADER,
                    )
                )
        manual_snapshot = snapshot.model_copy(update={"links": tuple(links)})
        result = next(
            (
                item
                for item in evaluate_components(manual_snapshot, config, effective_date)
                if item.invoice_id == invoice_id
            ),
            None,
        )
        if result is None:
            raise RuntimeError("the engine did not return the selected invoice case")
        return result

    def _load_snapshot(
        self, workspace_id: UUID, *, run_date: date, scope: Mapping[str, Any]
    ) -> tuple[
        ReconciliationSnapshot,
        Any,
        ReconciliationConfig,
        UUID,
        set[UUID],
        dict[UUID, RowMapping],
        dict[UUID, list[RowMapping]],
    ]:
        with self.engine.connect() as connection:
            connection = connection.execution_options(isolation_level="REPEATABLE READ")
            with connection.begin():
                rows, line_rows, text_by_version, header_evidence, line_evidence = (
                    self.mapping._load_current(connection, workspace_id)
                )
                row_by_id: dict[UUID, RowMapping] = {}
                records: list[ReconciliationDocument] = []
                for row in rows:
                    document_id = cast(UUID, row["document_id"])
                    version_id = cast(UUID, row["version_id"])
                    row_by_id[document_id] = row
                    records.append(
                        ReconciliationDocument(
                            document_id=document_id,
                            version_id=version_id,
                            vendor_id=cast(UUID | None, row["vendor_id"]),
                            natural_key=str(row["natural_key"]),
                            document=_make_document(
                                row,
                                line_rows.get(version_id, ()),
                                text_by_version.get(version_id, ""),
                                header_evidence.get(version_id, {}),
                                line_evidence,
                            ),
                        )
                    )

                link_current = metadata.tables["link_current"]
                link = metadata.tables["link"]
                link_rows = list(
                    connection.execute(
                        sa.select(*link.c)
                        .select_from(
                            link_current.join(
                                link,
                                sa.and_(
                                    link_current.c.workspace_id == link.c.workspace_id,
                                    link_current.c.current_link_id == link.c.id,
                                ),
                            )
                        )
                        .where(
                            link_current.c.workspace_id == workspace_id,
                            # Only PO edges feed the snapshot: an invoice-to-GRN relation
                            # is evidence for matching, not a PO link (WI-IMP1, D95).
                            link_current.c.link_type.in_(("inv_po", "grn_po")),
                            # A pending AI suggestion never feeds verdicts (SCN-20).
                            sa.not_(sa.and_(link.c.ai_suggested, link.c.state == "suggested")),
                        )
                        .order_by(link.c.id)
                    ).mappings()
                )
                links = tuple(
                    ReconciliationLink(
                        link_id=cast(UUID, row["id"]),
                        from_document_id=cast(UUID, row["from_document_id"]),
                        to_document_id=cast(UUID, row["to_document_id"]),
                        method=LinkMethod(str(row["method"])),
                        state=LinkState(str(row["state"])),
                        confidence=cast(Decimal, row["confidence"]),
                        level=LinkLevel(str(row["level"])),
                        line_no=cast(int | None, row["from_line_no"]),
                    )
                    for row in link_rows
                    if cast(UUID, row["from_document_id"]) in row_by_id
                    and cast(UUID, row["to_document_id"]) in row_by_id
                )

                opening_table = metadata.tables["opening_balance"]
                opening_rows = connection.execute(
                    sa.select(
                        opening_table.c.id,
                        opening_table.c.po_document_id,
                        opening_table.c.po_line_no,
                        opening_table.c.received_qty,
                        opening_table.c.billed_qty,
                    ).where(
                        opening_table.c.workspace_id == workspace_id,
                        opening_table.c.withdrawn_at.is_(None),
                        opening_table.c.po_document_id.is_not(None),
                    )
                ).mappings()
                opening_balances_list: list[OpeningBalance] = []
                for row in opening_rows:
                    po_document_id = cast(UUID | None, row["po_document_id"])
                    if po_document_id is None or po_document_id not in row_by_id:
                        continue
                    opening_balances_list.append(
                        OpeningBalance(
                            opening_balance_id=row["id"],
                            po_document_id=po_document_id,
                            po_line_no=int(row["po_line_no"]),
                            received_qty=_decimal(row["received_qty"]),
                            billed_qty=_decimal(row["billed_qty"]),
                        )
                    )
                opening_balances = tuple(opening_balances_list)
                action = metadata.tables["case_action"]
                latest_actions = connection.execute(
                    sa.select(action.c.invoice_id, action.c.action)
                    .where(
                        action.c.workspace_id == workspace_id,
                        action.c.action.in_(("approve", "hold", "reject", "release")),
                    )
                    .order_by(action.c.invoice_id, action.c.occurred_at.desc(), action.c.id.desc())
                )
                rejected: set[UUID] = set()
                seen_actions: set[UUID] = set()
                for action_row in latest_actions:
                    invoice_id = cast(UUID, action_row.invoice_id)
                    if invoice_id in seen_actions:
                        continue
                    seen_actions.add(invoice_id)
                    if action_row.action == "reject":
                        rejected.add(invoice_id)

                vendor_table = metadata.tables["vendor"]
                vendor_rows = list(
                    connection.execute(
                        sa.select(
                            vendor_table.c.id, vendor_table.c.pan, vendor_table.c.merged_into
                        ).where(vendor_table.c.workspace_id == workspace_id)
                    )
                )
                vendor_pans = {
                    cast(UUID, vendor_id): str(pan)
                    for vendor_id, pan, _merged_into in vendor_rows
                    if pan is not None
                }
                effective_vendor_ids = {
                    cast(UUID, vendor_id): cast(UUID, merged_into or vendor_id)
                    for vendor_id, _pan, merged_into in vendor_rows
                }
                active = metadata.tables["config_active"]
                versions = metadata.tables["config_version"]
                config_row = (
                    connection.execute(
                        sa.select(versions.c.id, versions.c.body)
                        .select_from(
                            active.join(
                                versions,
                                sa.and_(
                                    active.c.workspace_id == versions.c.workspace_id,
                                    active.c.config_version_id == versions.c.id,
                                ),
                            )
                        )
                        .where(active.c.workspace_id == workspace_id)
                    )
                    .mappings()
                    .one()
                )
                config_version_id = cast(UUID, config_row["id"])
                body = _mapping(config_row["body"])
                config = reconciliation_config(
                    body, effective_date=run_date, vendor_roots=effective_vendor_ids
                )
                snapshot = ReconciliationSnapshot(
                    documents=tuple(records),
                    links=links,
                    opening_balances=opening_balances,
                    vendor_pan_by_id=vendor_pans,
                    rejected_invoice_ids=tuple(rejected),
                )

                effective_vendor_id: UUID | None = None
                if scope.get("kind") == "vendor":
                    try:
                        requested_vendor_id = UUID(str(scope.get("vendor_id")))
                    except ValueError as exc:
                        raise ValueError("vendor-scoped runs require a valid vendor_id") from exc
                    vendor = metadata.tables["vendor"]
                    selected_vendor = connection.execute(
                        sa.select(vendor.c.id, vendor.c.merged_into).where(
                            vendor.c.workspace_id == workspace_id,
                            vendor.c.id == requested_vendor_id,
                        )
                    ).one_or_none()
                    if selected_vendor is None:
                        raise ValueError("the selected vendor does not exist in this workspace")
                    effective_vendor_id = cast(
                        UUID, selected_vendor.merged_into or selected_vendor.id
                    )
                elif scope.get("kind", "all") not in {"all", "auto_pending"}:
                    raise ValueError("run scope must be 'all', 'vendor' or 'auto_pending'")
                target_invoice_ids = {
                    item.document_id
                    for item in records
                    if item.document.header.doc_type is DocType.INVOICE
                    and (
                        effective_vendor_id is None
                        or (
                            item.vendor_id is not None
                            and effective_vendor_ids.get(item.vendor_id) == effective_vendor_id
                        )
                    )
                }
                if scope.get("kind") == "auto_pending":
                    try:
                        requested_invoice_ids = {
                            UUID(str(item)) for item in scope.get("invoice_ids", [])
                        }
                    except (TypeError, ValueError) as exc:
                        raise ValueError("auto-pending scopes require valid invoice_ids") from exc
                    case_current = metadata.tables["case_current"]
                    still_pending = set(
                        connection.scalars(
                            sa.select(case_current.c.invoice_id).where(
                                case_current.c.workspace_id == workspace_id,
                                case_current.c.invoice_id.in_(requested_invoice_ids),
                                case_current.c.effective_status == CaseStatus.PENDING.value,
                            )
                        ).all()
                    )
                    target_invoice_ids.intersection_update(still_pending)
                manifest = build_run_manifest(
                    snapshot,
                    config,
                    run_date,
                    config_version_id=config_version_id,
                    engine_version=ENGINE_VERSION,
                )
                return (
                    snapshot,
                    manifest,
                    config,
                    config_version_id,
                    target_invoice_ids,
                    row_by_id,
                    line_rows,
                )

    def _persist_results(
        self,
        *,
        run_id: UUID,
        workspace_id: UUID,
        snapshot: ReconciliationSnapshot,
        manifest: Any,
        config_version_id: UUID,
        results: Sequence[CaseResult],
        row_by_id: Mapping[UUID, RowMapping],
        line_rows: Mapping[UUID, Sequence[RowMapping]],
        duration_ms: int,
    ) -> None:
        run = metadata.tables["recon_run"]
        manifest_table = metadata.tables["run_manifest"]
        case = metadata.tables["recon_case"]
        line_result = metadata.tables["recon_line_result"]
        pointer = metadata.tables["case_current"]
        line_ids_by_version = {
            version_id: {int(row["line_no"]): row["id"] for row in rows}
            for version_id, rows in line_rows.items()
        }
        snapshot_by_document = {item.document_id: item for item in snapshot.documents}
        links_by_source: dict[UUID, list[Any]] = defaultdict(list)
        links_by_target: dict[UUID, list[Any]] = defaultdict(list)
        grn_ids_by_po: dict[UUID, set[UUID]] = defaultdict(set)
        for link in snapshot.links:
            links_by_source[link.from_document_id].append(link)
            links_by_target[link.to_document_id].append(link)
            source = snapshot_by_document[link.from_document_id]
            target = snapshot_by_document[link.to_document_id]
            if (
                source.document.header.doc_type is DocType.GRN
                and target.document.header.doc_type is DocType.PO
            ):
                grn_ids_by_po[link.to_document_id].add(link.from_document_id)
        po_input_seq = {
            document_id: int(row["input_seq"])
            for document_id, row in row_by_id.items()
            if row["input_seq"] is not None
        }
        vendor_table = metadata.tables["vendor"]
        with self.engine.connect() as connection:
            vendor_seq = {
                vendor_id: int(seq)
                for vendor_id, seq in connection.execute(
                    sa.select(vendor_table.c.id, vendor_table.c.input_seq).where(
                        vendor_table.c.workspace_id == workspace_id
                    )
                )
            }
        case_values: list[dict[str, Any]] = []
        line_values: list[dict[str, Any]] = []
        counts: Counter[str] = Counter()
        case_records: list[tuple[UUID, CaseResult, UUID, UUID | None]] = []
        manifest_hash = manifest.manifest_hash
        for result in results:
            if result.invoice_id is None or result.invoice_version_id is None:
                continue
            invoice_id = result.invoice_id
            current_record = snapshot_by_document[invoice_id]
            vendor_id = current_record.vendor_id
            linked_po_ids = {
                link.to_document_id
                for link in links_by_source.get(invoice_id, ())
                if snapshot_by_document[link.to_document_id].document.header.doc_type is DocType.PO
            }
            related_document_ids = {invoice_id, *linked_po_ids}
            related_document_ids.update(
                grn_id for po_id in linked_po_ids for grn_id in grn_ids_by_po.get(po_id, ())
            )
            case_link_ids = {link.link_id for link in links_by_source.get(invoice_id, ())}
            for po_id in linked_po_ids:
                case_link_ids.update(link.link_id for link in links_by_target.get(po_id, ()))
            case_id = new_id()
            case_input = {
                "manifest_hash": manifest_hash,
                "invoice_version_id": str(result.invoice_version_id),
                "document_versions": {
                    str(document_id): str(snapshot_by_document[document_id].version_id)
                    for document_id in sorted(related_document_ids, key=str)
                    if document_id in snapshot_by_document
                },
                "link_ids": sorted(str(link_id) for link_id in case_link_ids),
                "po_versions": {
                    str(item): str(snapshot_by_document[item].version_id)
                    for item in sorted(linked_po_ids, key=str)
                    if item in snapshot_by_document
                },
                "config_version_id": str(config_version_id),
            }
            fingerprint = _canonical_hash(case_input)
            po_sequences = {
                str(po_id): po_input_seq[po_id] for po_id in linked_po_ids if po_id in po_input_seq
            }
            case_values.append(
                {
                    "id": case_id,
                    "workspace_id": workspace_id,
                    "run_id": run_id,
                    "invoice_id": invoice_id,
                    "invoice_version_id": result.invoice_version_id,
                    "vendor_id": vendor_id,
                    "status": result.status.value,
                    "aged": result.aged,
                    "pending_days": result.pending_days,
                    "duplicate_of_id": result.duplicate_of_id,
                    "invoice_taxable": result.invoice_taxable,
                    "invoice_total": result.invoice_total,
                    "expected_taxable": result.expected_taxable,
                    "variance": result.variance,
                    "variance_pct": result.variance_pct,
                    "payable_taxable": result.payable_taxable,
                    "at_risk": result.at_risk,
                    "at_risk_gst": result.at_risk_gst,
                    "blocked_value": result.blocked_value,
                    "payment_due_date": result.payment_due_date,
                    "po_nos": list(result.po_nos),
                    "grn_nos": list(result.grn_nos),
                    "link_summary": to_jsonable(result.link_summary),
                    "reason_codes": [item.value for item in result.reason_codes],
                    "checks": to_jsonable(result.checks),
                    "inputs": case_input,
                    "input_fingerprint": fingerprint,
                    "vendor_seq": vendor_seq.get(vendor_id) if vendor_id else None,
                    "po_seqs": po_sequences,
                }
            )
            invoice_line_ids = line_ids_by_version.get(result.invoice_version_id, {})
            for line in result.lines:
                po_line_id = None
                if line.po_document_id is not None and line.po_line_no is not None:
                    po_version = snapshot_by_document.get(line.po_document_id)
                    if po_version is not None:
                        po_line_id = line_ids_by_version.get(po_version.version_id, {}).get(
                            line.po_line_no
                        )
                line_values.append(
                    {
                        "id": new_id(),
                        "workspace_id": workspace_id,
                        "run_id": run_id,
                        "case_id": case_id,
                        "invoice_line_id": invoice_line_ids.get(line.line_no),
                        "line_no": line.line_no,
                        "line_type": line.line_type.value,
                        "status": line.status.value,
                        "po_document_id": line.po_document_id,
                        "po_line_no": line.po_line_no,
                        "po_doc_line_id": po_line_id,
                        "alignment_score": line.alignment_score,
                        "alignment_state": line.alignment_state.value,
                        "grn_document_ids": list(line.grn_document_ids),
                        "uom": line.uom,
                        "po_qty": line.po_qty,
                        "po_cap": line.po_cap,
                        "billed_before": line.billed_before,
                        "billed_qty": line.billed_qty,
                        "grn_accepted_cum": line.grn_accepted_cum,
                        "grn_rejected_cum": line.grn_rejected_cum,
                        "eligible_qty": line.eligible_qty,
                        "balance_after": line.balance_after,
                        "shortfall_qty": line.shortfall_qty,
                        "po_rate": line.po_rate,
                        "invoice_net_rate": line.invoice_net_rate,
                        "payable_rate": line.payable_rate,
                        "line_taxable": line.line_taxable,
                        "payable_taxable": line.payable_taxable,
                        "at_risk": line.at_risk,
                        "reason_codes": [item.value for item in line.reason_codes],
                        "checks": to_jsonable(line.checks),
                    }
                )
            counts[result.status.value] += 1
            case_records.append((case_id, result, invoice_id, vendor_id))

        with self.engine.begin() as connection:
            connection.execute(
                sa.insert(manifest_table).values(
                    id=new_id(),
                    workspace_id=workspace_id,
                    run_id=run_id,
                    manifest_hash=manifest.manifest_hash,
                    item_count=len(manifest.documents)
                    + len(manifest.links)
                    + len(manifest.opening_balances),
                    items=manifest.model_dump(mode="json"),
                )
            )
            if case_values:
                connection.execute(sa.insert(case), case_values)
            if line_values:
                connection.execute(sa.insert(line_result), line_values)

            current_values_batch: list[dict[str, Any]] = []
            for case_id, result, invoice_id, vendor_id in case_records:
                invoice_date = cast(date | None, row_by_id[invoice_id]["doc_date"])
                current_values_batch.append(
                    {
                        "id": new_id(),
                        "workspace_id": workspace_id,
                        "invoice_id": invoice_id,
                        "case_id": case_id,
                        "run_id": run_id,
                        "vendor_id": vendor_id,
                        "invoice_date": invoice_date,
                        "computed_status": result.status.value,
                        "overlay": None,
                        "effective_status": result.status.value,
                        "severity_rank": _severity_rank(result.status),
                        "human_touched": False,
                        "latest_action_id": None,
                    }
                )
            for offset in range(0, len(current_values_batch), 500):
                statement = pg_insert(pointer).values(current_values_batch[offset : offset + 500])
                connection.execute(
                    statement.on_conflict_do_update(
                        index_elements=[pointer.c.workspace_id, pointer.c.invoice_id],
                        set_={
                            key: statement.excluded[key]
                            for key in (
                                "case_id",
                                "run_id",
                                "vendor_id",
                                "invoice_date",
                                "computed_status",
                                "severity_rank",
                            )
                        }
                        | {
                            "overlay": pointer.c.overlay,
                            "effective_status": sa.func.coalesce(
                                sa.cast(pointer.c.overlay, sa.Text()),
                                sa.cast(statement.excluded.computed_status, sa.Text()),
                            ),
                            "human_touched": sa.or_(
                                pointer.c.human_touched, statement.excluded.human_touched
                            ),
                            "latest_action_id": sa.func.coalesce(
                                pointer.c.latest_action_id, statement.excluded.latest_action_id
                            ),
                        }
                        | {"version": pointer.c.version + 1, "updated_at": sa.func.now()},
                    )
                )
            connection.execute(
                sa.update(run)
                .where(run.c.id == run_id)
                .values(
                    status=RunStatus.SUCCEEDED.value,
                    config_version_id=config_version_id,
                    engine_version=ENGINE_VERSION,
                    manifest_hash=manifest.manifest_hash,
                    rules_hash=manifest.rules_hash,
                    total_cases=len(case_values),
                    done_cases=len(case_values),
                    status_counts=dict(counts),
                    metrics={
                        "duration_ms": duration_ms,
                        "case_count": len(case_values),
                        "engine_version": ENGINE_VERSION,
                        "rules_hash": manifest.rules_hash,
                    },
                    finished_at=datetime.now(UTC),
                    error_code=None,
                    error_message=None,
                    version=run.c.version + 1,
                )
            )

    def _mark_failed(self, run_id: UUID) -> None:
        run = metadata.tables["recon_run"]
        with self.engine.begin() as connection:
            connection.execute(
                sa.update(run)
                .where(run.c.id == run_id, run.c.status == RunStatus.RUNNING.value)
                .values(
                    status=RunStatus.FAILED.value,
                    error_code="RECONCILIATION_FAILED",
                    error_message="The run could not be completed. Review server logs and retry.",
                    finished_at=datetime.now(UTC),
                    version=run.c.version + 1,
                )
            )


def _severity_rank(status: CaseStatus) -> int:
    return {
        CaseStatus.EXCEPTION: 1,
        CaseStatus.NEEDS_REVIEW: 2,
        CaseStatus.UNLINKED: 3,
        CaseStatus.DUPLICATE: 4,
        CaseStatus.PENDING: 5,
        CaseStatus.MATCHED_FLAGS: 6,
        CaseStatus.MATCHED: 7,
    }[status]


__all__ = ["ReconciliationService", "reconciliation_config"]
