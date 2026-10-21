"""Transactional rebuilding of derived PO quantity ledgers."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, cast
from uuid import UUID

import sqlalchemy as sa
from reconbench_core.align import AlignmentResult, align_lines
from reconbench_core.schema import AlignmentState, DocType
from reconbench_core.version import ENGINE_VERSION
from sqlalchemy.engine import Connection, Engine, RowMapping

from reconbench_app.authorization import requires
from reconbench_app.db.ids import new_id
from reconbench_app.db.metadata import metadata
from reconbench_app.mapping_service import MappingService, _make_document

_ACTIVE_LINK_STATES = ("auto", "suggested", "confirmed")


def _deterministic_link(link: sa.Table) -> sa.ColumnElement[bool]:
    """An AI suggestion awaiting a person never feeds balances or verdicts (SCN-20)."""

    return sa.not_(sa.and_(link.c.ai_suggested, link.c.state == "suggested"))


_SCOPE_PRIORITY = sa.case(
    (metadata.tables["dirty_scope"].c.scope_kind == "vendor", 0),
    (metadata.tables["dirty_scope"].c.scope_kind == "po", 1),
    else_=2,
)


def _hash_payload(payload: object) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _field_value(field: object | None) -> object | None:
    return getattr(field, "value", None) if field is not None else None


def _as_decimal(value: object | None) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal("0")


def _as_date(value: object | None) -> date | None:
    return value if isinstance(value, date) else None


@dataclass(frozen=True, slots=True)
class _CurrentSnapshot:
    """The current documents of one workspace and its governing POs (WI-PERF1, D98).

    A drain batch rebuilds many POs in one transaction; ledger writes never change a
    document or which PO revision governs, so the batch loads this once instead of once
    per PO. A single ``rebuild_po`` still loads its own.
    """

    workspace_id: UUID
    by_document: dict[UUID, tuple[RowMapping, Any]]
    line_rows: dict[UUID, Any]
    governing: frozenset[UUID]


class LedgerRebuildService:
    """Rebuild ledgers from the current PO, active links and source versions."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.mapping = MappingService(engine)
        self._batch_snapshot: _CurrentSnapshot | None = None

    def _current_snapshot(self, connection: Connection, workspace_id: UUID) -> _CurrentSnapshot:
        batch = self._batch_snapshot
        if batch is not None and batch.workspace_id == workspace_id:
            return batch
        rows, line_rows, text_by_version, header_evidence, line_evidence = (
            self.mapping._load_current(connection, workspace_id)
        )
        by_document: dict[UUID, tuple[RowMapping, Any]] = {}
        po_candidates: list[tuple[RowMapping, Any, UUID | None]] = []
        for row in rows:
            document_id = cast(UUID, row["document_id"])
            version_id = cast(UUID, row["version_id"])
            document = _make_document(
                row,
                line_rows.get(version_id, ()),
                text_by_version.get(version_id, ""),
                header_evidence.get(version_id, {}),
                line_evidence,
            )
            by_document[document_id] = (row, document)
            if document.header.doc_type is DocType.PO:
                po_candidates.append((row, document, cast(UUID | None, row["vendor_id"])))
        governing = self.mapping._governing_pos(connection, workspace_id, po_candidates)
        return _CurrentSnapshot(
            workspace_id=workspace_id,
            by_document=by_document,
            line_rows=dict(line_rows),
            governing=frozenset(item.document_id for item in governing),
        )

    @requires("links.edit")
    def rebuild_po(self, workspace_id: UUID, po_document_id: UUID) -> dict[str, int | bool]:
        """Rebuild one PO atomically; identical inputs leave derived rows untouched."""

        with self.engine.begin() as connection:
            return self.rebuild_po_connection(connection, workspace_id, po_document_id)

    @requires("links.edit")
    def rebuild_po_connection(
        self,
        connection: Connection,
        workspace_id: UUID,
        po_document_id: UUID,
    ) -> dict[str, int | bool]:
        connection.execute(
            sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_name, 0))"),
            {"lock_name": f"reconbench.po:{po_document_id}"},
        )
        snapshot = self._current_snapshot(connection, workspace_id)
        by_document = snapshot.by_document
        line_rows = snapshot.line_rows

        target = by_document.get(po_document_id)
        if target is None or target[1].header.doc_type is not DocType.PO:
            raise ValueError("PO document is not current in this workspace")

        if po_document_id not in snapshot.governing:
            return self._remove_obsolete_ledger(connection, workspace_id, po_document_id)

        po_row, po_document = target
        po_version_id = cast(UUID, po_row["version_id"])
        po_line_rows = line_rows.get(po_version_id, [])
        po_lines = {line.line_no: line for line in po_document.lines}
        po_db_lines = {int(line["line_no"]): line for line in po_line_rows}
        mapping_config = self.mapping._mapping_config(connection, workspace_id)
        links = self._active_links(connection, workspace_id, po_document_id)
        opening_rows = self._opening_balances(connection, workspace_id, po_row)
        all_exclusions = self._invoice_exclusions(connection, workspace_id)
        linked_document_ids = {cast(UUID, link["from_document_id"]) for link in links}
        exclusions = {
            document_id: reason
            for document_id, reason in all_exclusions.items()
            if document_id in linked_document_ids
        }

        payload = {
            "po": {
                "id": str(po_document_id),
                "version": str(po_version_id),
                "natural_key": str(po_row["natural_key"]),
            },
            "engine_version": ENGINE_VERSION,
            "links": [
                {
                    "id": str(link["id"]),
                    "state": str(link["state"]),
                    "from": str(link["from_document_id"]),
                    "line": link["from_line_no"],
                    "version": int(link["version_no"]),
                }
                for link in links
            ],
            "sources": [
                {
                    "document": str(link["from_document_id"]),
                    "version": str(
                        by_document[cast(UUID, link["from_document_id"])][0]["version_id"]
                    ),
                    "natural_key": str(
                        by_document[cast(UUID, link["from_document_id"])][0]["natural_key"]
                    ),
                }
                for link in links
                if cast(UUID, link["from_document_id"]) in by_document
            ],
            "opening": [
                {
                    "id": str(item["id"]),
                    "received": str(item["received_qty"]),
                    "billed": str(item["billed_qty"]),
                    "as_of": str(item["as_of_date"]),
                }
                for item in opening_rows
            ],
            "exclusions": {
                str(key): value
                for key, value in sorted(exclusions.items(), key=lambda pair: str(pair[0]))
            },
            "alignment": mapping_config.alignment.model_dump(mode="json"),
        }
        input_hash = _hash_payload(payload)

        ledger = metadata.tables["po_line_ledger"]
        current_hashes = set(
            connection.scalars(
                sa.select(ledger.c.input_hash).where(
                    ledger.c.workspace_id == workspace_id,
                    ledger.c.po_document_id == po_document_id,
                )
            ).all()
        )
        current_count = connection.scalar(
            sa.select(sa.func.count())
            .select_from(ledger)
            .where(
                ledger.c.workspace_id == workspace_id,
                ledger.c.po_document_id == po_document_id,
            )
        )
        if current_count == len(po_lines) and current_hashes == (
            {input_hash} if po_lines else set()
        ):
            return {"lines": len(po_lines), "entries": 0, "changed": False}

        entries_by_po_line: dict[int, list[dict[str, Any]]] = defaultdict(list)
        self._add_opening_entries(entries_by_po_line, opening_rows)
        self._add_document_entries(
            connection=connection,
            workspace_id=workspace_id,
            links=links,
            by_document=by_document,
            line_rows=line_rows,
            po_lines=po_lines,
            po_db_lines=po_db_lines,
            alignment=mapping_config.alignment,
            exclusions=exclusions,
            entries_by_po_line=entries_by_po_line,
        )

        connection.execute(
            sa.delete(ledger).where(
                ledger.c.workspace_id == workspace_id,
                ledger.c.po_document_id == po_document_id,
            )
        )
        ledger_ids: dict[int, UUID] = {}
        ledger_values: list[dict[str, Any]] = []
        for line_no, po_line in sorted(po_lines.items()):
            db_line = po_db_lines[line_no]
            ordered = _as_decimal(_field_value(po_line.quantity))
            events = sorted(entries_by_po_line.get(line_no, []), key=self._event_sort_key)
            accepted = sum((_as_decimal(event["accepted_delta"]) for event in events), Decimal("0"))
            rejected = sum((_as_decimal(event["rejected_delta"]) for event in events), Decimal("0"))
            billed = sum(
                (_as_decimal(event["billed_delta"]) for event in events if event["consumes"]),
                Decimal("0"),
            )
            ledger_id = new_id()
            ledger_ids[line_no] = ledger_id
            ledger_values.append(
                {
                    "id": ledger_id,
                    "workspace_id": workspace_id,
                    "po_document_id": po_document_id,
                    "po_version_id": po_version_id,
                    "po_line_no": line_no,
                    "po_doc_line_id": cast(UUID, db_line["id"]),
                    "uom": _field_value(po_line.uom),
                    "po_rate": _field_value(po_line.rate),
                    "ordered": ordered,
                    "received_accepted": accepted,
                    "received_rejected": rejected,
                    "billed": billed,
                    "completeness_warning": False,
                    "input_hash": input_hash,
                }
            )
        if ledger_values:
            connection.execute(sa.insert(ledger), ledger_values)

        entry_table = metadata.tables["ledger_entry"]
        entry_values: list[dict[str, Any]] = []
        for line_no, events in sorted(entries_by_po_line.items()):
            ledger_id_for_line = ledger_ids.get(line_no)
            if ledger_id_for_line is None:
                continue
            for seq, event in enumerate(sorted(events, key=self._event_sort_key), start=1):
                event.pop("completeness_warning", None)
                entry_values.append(
                    {
                        "id": new_id(),
                        "workspace_id": workspace_id,
                        "po_line_ledger_id": ledger_id_for_line,
                        "seq": seq,
                        **event,
                    }
                )
        if entry_values:
            connection.execute(sa.insert(entry_table), entry_values)
        return {"lines": len(ledger_values), "entries": len(entry_values), "changed": True}

    @requires("links.edit")
    def drain_dirty_scopes(self, workspace_id: UUID, *, limit: int = 100) -> dict[str, int]:
        """Claim and process a coalesced batch; a failure rolls back the whole batch."""

        if limit < 1 or limit > 1000:
            raise ValueError("dirty-scope batch limit must be between 1 and 1000")
        with self.engine.begin() as connection:
            scopes = metadata.tables["dirty_scope"]
            claimed = list(
                connection.execute(
                    sa.select(*scopes.c)
                    .where(
                        scopes.c.workspace_id == workspace_id,
                        scopes.c.processed_at.is_(None),
                    )
                    .order_by(_SCOPE_PRIORITY, scopes.c.first_marked_at, scopes.c.id)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                ).mappings()
            )
            rebuilt: set[UUID] = set()
            for scope in claimed:
                kind = str(scope["scope_kind"])
                scope_id = cast(UUID, scope["scope_id"])
                if kind == "vendor":
                    connection.execute(
                        sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_name, 0))"),
                        {"lock_name": f"reconbench.vendor:{scope_id}"},
                    )
                    vendor_ids = self._merged_vendor_ids(connection, workspace_id, scope_id)
                    document = metadata.tables["document"]
                    po_ids = connection.scalars(
                        sa.select(document.c.id).where(
                            document.c.workspace_id == workspace_id,
                            document.c.doc_type == "po",
                            document.c.withdrawn_at.is_(None),
                            document.c.vendor_id.in_(vendor_ids),
                        )
                    ).all()
                    rebuilt.update(cast(UUID, po_id) for po_id in po_ids)
                elif kind == "po":
                    rebuilt.add(scope_id)
                elif kind == "invoice":
                    link_ids = self._linked_po_ids(connection, workspace_id, scope_id)
                    rebuilt.update(link_ids)
                else:
                    raise ValueError(f"unsupported dirty scope kind: {kind}")

            # One snapshot of the current documents for the whole batch (WI-PERF1, D98).
            self._batch_snapshot = (
                self._current_snapshot(connection, workspace_id) if len(rebuilt) > 1 else None
            )
            try:
                results = [
                    self.rebuild_po_connection(connection, workspace_id, po_id)
                    for po_id in sorted(rebuilt, key=str)
                ]
            finally:
                self._batch_snapshot = None
            if claimed:
                connection.execute(
                    sa.update(scopes)
                    .where(scopes.c.id.in_([row["id"] for row in claimed]))
                    .values(processed_at=sa.func.now(), claimed_at=sa.func.now())
                )
            return {
                "scopes": len(claimed),
                "pos": len(results),
                "changed": sum(bool(item["changed"]) for item in results),
                "entries": sum(int(item["entries"]) for item in results),
            }

    @staticmethod
    def _active_links(
        connection: Connection,
        workspace_id: UUID,
        po_document_id: UUID,
    ) -> list[RowMapping]:
        pointer = metadata.tables["link_current"]
        link = metadata.tables["link"]
        return list(
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
                    pointer.c.to_document_id == po_document_id,
                    pointer.c.link_type.in_(("inv_po", "grn_po")),
                    link.c.state.in_(_ACTIVE_LINK_STATES),
                    _deterministic_link(link),
                )
                .order_by(link.c.from_document_id, link.c.from_line_no, link.c.id)
            ).mappings()
        )

    @staticmethod
    def _opening_balances(
        connection: Connection,
        workspace_id: UUID,
        po_row: RowMapping,
    ) -> list[RowMapping]:
        opening = metadata.tables["opening_balance"]
        po_document_id = cast(UUID, po_row["document_id"])
        vendor_id = cast(UUID | None, po_row["vendor_id"])
        canonical = str(po_row["canonical_doc_no"] or "")
        unresolved_match = (
            sa.and_(
                opening.c.po_document_id.is_(None),
                opening.c.canonical_po_no == canonical,
                sa.or_(opening.c.vendor_id.is_(None), opening.c.vendor_id == vendor_id),
            )
            if canonical
            else sa.false()
        )
        return list(
            connection.execute(
                sa.select(*opening.c)
                .where(
                    opening.c.workspace_id == workspace_id,
                    opening.c.withdrawn_at.is_(None),
                    sa.or_(
                        opening.c.po_document_id == po_document_id,
                        unresolved_match,
                    ),
                )
                .order_by(opening.c.as_of_date, opening.c.id)
            ).mappings()
        )

    @staticmethod
    def _invoice_exclusions(connection: Connection, workspace_id: UUID) -> dict[UUID, str]:
        current = metadata.tables["case_current"]
        rows = connection.execute(
            sa.select(
                current.c.invoice_id,
                current.c.computed_status,
                current.c.effective_status,
            ).where(current.c.workspace_id == workspace_id)
        )
        exclusions: dict[UUID, str] = {}
        for row in rows:
            invoice_id = cast(UUID, row.invoice_id)
            if row.computed_status == "duplicate":
                exclusions[invoice_id] = "duplicate"
            elif row.effective_status == "rejected":
                exclusions[invoice_id] = "rejected"
        return exclusions

    def _add_opening_entries(
        self,
        entries_by_po_line: dict[int, list[dict[str, Any]]],
        opening_rows: list[RowMapping],
    ) -> None:
        for row in opening_rows:
            line_no = int(row["po_line_no"])
            event_key = _hash_payload({"opening_balance_id": str(row["id"])})
            entries_by_po_line[line_no].append(
                {
                    "kind": "opening",
                    "source_document_id": None,
                    "source_doc_line_id": None,
                    "opening_balance_id": cast(UUID, row["id"]),
                    "sort_date": row["as_of_date"],
                    "sort_no": str(row["canonical_po_no"]),
                    "sort_key": event_key,
                    "accepted_delta": _as_decimal(row["received_qty"]),
                    "rejected_delta": Decimal("0"),
                    "billed_delta": _as_decimal(row["billed_qty"]),
                    "consumes": True,
                    "excluded_reason": None,
                    "alignment_score": None,
                    "alignment_state": None,
                    "completeness_warning": False,
                }
            )

    def _add_document_entries(
        self,
        *,
        connection: Connection,
        workspace_id: UUID,
        links: list[RowMapping],
        by_document: dict[UUID, tuple[RowMapping, Any]],
        line_rows: dict[UUID, list[RowMapping]],
        po_lines: dict[int, Any],
        po_db_lines: dict[int, RowMapping],
        alignment: Any,
        exclusions: dict[UUID, str],
        entries_by_po_line: dict[int, list[dict[str, Any]]],
    ) -> None:
        assigned_source_lines: set[tuple[UUID, int]] = set()
        ordered_links = sorted(
            links,
            key=lambda item: (
                str(item["from_document_id"]),
                0 if item["from_line_no"] is not None else 1,
                int(item["from_line_no"] or 0),
                str(item["id"]),
            ),
        )
        for link in ordered_links:
            source_id = cast(UUID, link["from_document_id"])
            source = by_document.get(source_id)
            if source is None:
                continue
            source_row, source_document = source
            source_version_id = cast(UUID, source_row["version_id"])
            source_type = source_document.header.doc_type
            if source_type not in {DocType.GRN, DocType.INVOICE}:
                continue
            source_db_lines = line_rows.get(source_version_id, [])
            source_db_by_line = {int(row["line_no"]): row for row in source_db_lines}
            source_lines = list(source_document.lines)
            if link["from_line_no"] is not None:
                selected_no = int(link["from_line_no"])
                source_lines = [line for line in source_lines if line.line_no == selected_no]
            source_lines = [
                line
                for line in source_lines
                if (source_id, line.line_no) not in assigned_source_lines
            ]
            if not source_lines:
                continue
            assigned_source_lines.update((source_id, line.line_no) for line in source_lines)
            aligned_by_source = {
                result.invoice_line_no: result
                for result in align_lines(
                    source_lines,
                    list(po_lines.values()),
                    alignment,
                    explicit_link=str(link["method"]) == "manual",
                )
            }
            po_numbers = set(po_lines)
            for source_line in source_lines:
                raw_reference = _field_value(source_line.po_line_ref)
                if raw_reference is None:
                    continue
                try:
                    referenced_line = int(str(raw_reference).strip())
                except ValueError:
                    continue
                if referenced_line in po_numbers:
                    aligned_by_source[source_line.line_no] = AlignmentResult(
                        invoice_line_no=source_line.line_no,
                        po_line_no=referenced_line,
                        score=Decimal("1.0000"),
                        state=AlignmentState.ALIGNED,
                    )
            for line in source_lines:
                source_db_line = source_db_by_line.get(line.line_no)
                result = aligned_by_source.get(line.line_no)
                if source_db_line is None or result is None or result.po_line_no is None:
                    continue
                po_line_no = int(result.po_line_no)
                if po_line_no not in po_db_lines or result.state in {
                    AlignmentState.NOT_ON_PO,
                    AlignmentState.UNLINKED,
                    AlignmentState.CHARGE,
                }:
                    continue
                event_key = _hash_payload(
                    {
                        "natural_key": str(source_row["natural_key"]),
                        "line_no": line.line_no,
                        "kind": source_type.value,
                    }
                )
                sort_no = str(source_row["canonical_doc_no"] or source_row["doc_no"] or "")
                sort_date = _as_date(source_row["doc_date"])
                accepted_delta = Decimal("0")
                rejected_delta = Decimal("0")
                billed_delta = Decimal("0")
                consumes = True
                excluded_reason = None
                if source_type is DocType.GRN:
                    received = _field_value(line.received_qty)
                    if received is None:
                        received = _field_value(line.quantity)
                    received_qty = _as_decimal(received)
                    accepted = _field_value(line.accepted_qty)
                    rejected = _field_value(line.rejected_qty)
                    if accepted is not None:
                        accepted_delta = _as_decimal(accepted)
                    elif rejected is not None:
                        rejected_delta = _as_decimal(rejected)
                        accepted_delta = max(Decimal("0"), received_qty - rejected_delta)
                    else:
                        accepted_delta = received_qty
                    if rejected is not None:
                        rejected_delta = _as_decimal(rejected)
                else:
                    billed_delta = _as_decimal(_field_value(line.quantity))
                    excluded_reason = exclusions.get(source_id)
                    consumes = excluded_reason is None
                entries_by_po_line[po_line_no].append(
                    {
                        "kind": "grn" if source_type is DocType.GRN else "invoice",
                        "source_document_id": source_id,
                        "source_doc_line_id": cast(UUID, source_db_line["id"]),
                        "opening_balance_id": None,
                        "sort_date": sort_date,
                        "sort_no": sort_no,
                        "sort_key": event_key,
                        "accepted_delta": accepted_delta,
                        "rejected_delta": rejected_delta,
                        "billed_delta": billed_delta,
                        "consumes": consumes,
                        "excluded_reason": excluded_reason,
                        "alignment_score": result.score,
                        "alignment_state": result.state.value,
                    }
                )

    @staticmethod
    def _event_sort_key(event: dict[str, Any]) -> tuple[int, date, str, str, str]:
        event_date = event["sort_date"]
        return (
            0 if event["kind"] == "opening" else 1,
            event_date if isinstance(event_date, date) else date.min,
            str(event["sort_no"] or ""),
            str(event["sort_key"] or ""),
            str(event["kind"]),
        )

    def _remove_obsolete_ledger(
        self,
        connection: Connection,
        workspace_id: UUID,
        po_document_id: UUID,
    ) -> dict[str, int | bool]:
        ledger = metadata.tables["po_line_ledger"]
        removed = (
            connection.execute(
                sa.delete(ledger).where(
                    ledger.c.workspace_id == workspace_id,
                    ledger.c.po_document_id == po_document_id,
                )
            ).rowcount
            or 0
        )
        return {"lines": 0, "entries": 0, "changed": bool(removed)}

    @staticmethod
    def _merged_vendor_ids(
        connection: Connection,
        workspace_id: UUID,
        vendor_id: UUID,
    ) -> set[UUID]:
        vendor = metadata.tables["vendor"]
        rows = connection.execute(
            sa.select(vendor.c.id, vendor.c.merged_into).where(
                vendor.c.workspace_id == workspace_id,
                sa.or_(vendor.c.id == vendor_id, vendor.c.merged_into == vendor_id),
            )
        )
        return {cast(UUID, row.id) for row in rows} | {vendor_id}

    @staticmethod
    def _linked_po_ids(
        connection: Connection,
        workspace_id: UUID,
        invoice_id: UUID,
    ) -> set[UUID]:
        pointer = metadata.tables["link_current"]
        link = metadata.tables["link"]
        rows = connection.scalars(
            sa.select(pointer.c.to_document_id)
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
                pointer.c.from_document_id == invoice_id,
                pointer.c.link_type == "inv_po",
                link.c.state.in_(_ACTIVE_LINK_STATES),
                _deterministic_link(link),
            )
        ).all()
        return {cast(UUID, item) for item in rows}


__all__ = ["LedgerRebuildService"]
