"""Local deterministic XML/CSV reconciliation command-line entry point.

Input files are read-only. Reconciliation is performed from a frozen in-memory
snapshot and the requested JSON result is written only after evaluation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5

from pydantic import ValidationError

from reconbench_core.align import align_lines
from reconbench_core.mapping import MappingConfig, MappingDocument, MappingPO, resolve_links
from reconbench_core.normalize import validate_gstin
from reconbench_core.parsers import ParserHints, parse
from reconbench_core.recon import (
    ReconciliationConfig,
    ReconciliationDocument,
    ReconciliationLink,
    ReconciliationSnapshot,
    build_run_manifest,
    evaluate_components,
    rules_hash,
)
from reconbench_core.schema import AlignmentState, DocType, FileFormat, LinkLevel, LinkState
from reconbench_core.version import ENGINE_VERSION

_ZONE_TYPES = {
    "po": DocType.PO,
    "purchase-order": DocType.PO,
    "purchase_orders": DocType.PO,
    "grn": DocType.GRN,
    "goods-receipt": DocType.GRN,
    "goods_receipts": DocType.GRN,
    "invoice": DocType.INVOICE,
    "invoices": DocType.INVOICE,
}
_UUID_NAMESPACE = UUID("1e8570d0-202b-5fc4-a3c0-46c8b950ac74")


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _load_configuration(path: Path) -> tuple[ReconciliationConfig, dict[str, Any], str]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("configuration root must be a JSON object")
    body = raw.get("body", raw)
    if not isinstance(body, dict):
        raise ValueError("configuration body must be a JSON object")
    engine_values = body.get("engine", body)
    if not isinstance(engine_values, dict):
        raise ValueError("configuration engine must be a JSON object")
    parser_hints = raw.get("parser_hints", body.get("parser_hints", {}))
    if not isinstance(parser_hints, dict):
        raise ValueError("parser_hints must be a JSON object keyed by relative file path or zone")
    config = ReconciliationConfig.model_validate(engine_values)
    config_hash = hashlib.sha256(_canonical_json(raw).encode("utf-8")).hexdigest()
    return config, parser_hints, config_hash


def _directory_doc_type(relative_path: Path) -> DocType | None:
    for part in relative_path.parts[:-1]:
        selected = _ZONE_TYPES.get(part.casefold())
        if selected is not None:
            return selected
    return None


def _format_for(path: Path) -> FileFormat | None:
    suffix = path.suffix.casefold()
    if suffix == ".xml":
        return FileFormat.XML
    if suffix == ".csv":
        return FileFormat.CSV
    return None


def _parser_hints(
    relative_path: Path,
    file_format: FileFormat,
    configured: dict[str, Any],
) -> ParserHints:
    relative = relative_path.as_posix()
    zone_type = _directory_doc_type(relative_path)
    zone_key = next(
        (part.casefold() for part in relative_path.parts[:-1] if part.casefold() in _ZONE_TYPES),
        None,
    )
    selected = configured.get(relative)
    if selected is None and zone_key is not None:
        selected = configured.get(zone_key)
    if selected is None:
        selected = {}
    if not isinstance(selected, dict):
        raise ValueError(f"parser hints for {relative} must be a JSON object")
    values: dict[str, Any] = {"file_format": file_format.value}
    if zone_type is not None:
        values["doc_type"] = zone_type.value
    values.update(selected)
    return ParserHints.model_validate(values)


def _field_value(document: Any, field: str) -> Any | None:
    value = getattr(document.header, field, None)
    return None if value is None else value.value


def _stable_id(namespace: str, value: str) -> UUID:
    return uuid5(_UUID_NAMESPACE, f"{namespace}:{value}")


def _read_documents(
    folder: Path,
    parser_config: dict[str, Any],
) -> tuple[list[ReconciliationDocument], list[dict[str, Any]], list[dict[str, Any]]]:
    candidates = sorted(
        (
            path
            for path in folder.rglob("*")
            if path.is_file() and not path.is_symlink() and _format_for(path) is not None
        ),
        key=lambda path: (path.relative_to(folder).as_posix().casefold(), path.name),
    )
    records: list[ReconciliationDocument] = []
    file_reports: list[dict[str, Any]] = []
    file_errors: list[dict[str, Any]] = []
    seen_content: dict[tuple[str, str], str] = {}

    for path in candidates:
        relative_path = path.relative_to(folder)
        relative = relative_path.as_posix()
        file_format = _format_for(path)
        if file_format is None:
            continue
        try:
            content = path.read_bytes()
        except OSError:
            file_errors.append({"path": relative, "code": "FILE_READ_FAILED"})
            continue
        content_hash = hashlib.sha256(content).hexdigest()
        try:
            hints = _parser_hints(relative_path, file_format, parser_config)
        except (ValidationError, ValueError):
            file_errors.append({"path": relative, "code": "PARSER_HINTS_INVALID"})
            continue
        dedupe_key = (content_hash, _canonical_json(hints.model_dump(mode="json")))
        if dedupe_key in seen_content:
            file_reports.append(
                {
                    "path": relative,
                    "sha256": content_hash,
                    "status": "duplicate_bytes_skipped",
                    "duplicate_of": seen_content[dedupe_key],
                    "document_count": 0,
                }
            )
            continue
        seen_content[dedupe_key] = relative
        parsed = parse(content, hints)
        warnings = [warning.model_dump(mode="json") for warning in parsed.warnings]
        if not parsed.success or not parsed.documents:
            file_errors.append(
                {
                    "path": relative,
                    "code": parsed.reason_code or "NO_DOCUMENTS",
                    "warnings": warnings,
                }
            )
            file_reports.append(
                {
                    "path": relative,
                    "sha256": content_hash,
                    "status": "failed",
                    "document_count": 0,
                    "layout_signature": parsed.layout_signature,
                    "warnings": warnings,
                }
            )
            continue

        document_records = []
        for index, document in enumerate(parsed.documents):
            identity_material = ":".join(
                (
                    content_hash,
                    hints.doc_type.value if hints.doc_type else "auto",
                    str(index),
                    document.header.doc_type.value,
                    _canonical_json(hints.model_dump(mode="json")),
                )
            )
            document_id = _stable_id("document", identity_material)
            version_id = _stable_id("document-version", identity_material)
            doc_no = _field_value(document, "doc_no") or ""
            natural_key = document.natural_key or f"{document.header.doc_type.value}:{doc_no}"
            document_records.append(
                ReconciliationDocument(
                    document_id=document_id,
                    version_id=version_id,
                    natural_key=natural_key,
                    document=document,
                )
            )
        records.extend(document_records)
        file_reports.append(
            {
                "path": relative,
                "sha256": content_hash,
                "status": "parsed",
                "document_count": len(document_records),
                "layout_signature": parsed.layout_signature,
                "warnings": warnings,
            }
        )

    records.sort(
        key=lambda item: (
            item.document.header.doc_type.value,
            str(_field_value(item.document, "doc_no") or "").casefold(),
            item.natural_key,
            str(item.document_id),
        )
    )
    file_reports.sort(key=lambda item: item["path"])
    file_errors.sort(key=lambda item: item["path"])
    return records, file_reports, file_errors


def _vendor_anchors(
    documents: list[ReconciliationDocument],
) -> tuple[list[ReconciliationDocument], dict[UUID, str]]:
    pan_by_vendor: dict[UUID, str] = {}
    resolved: list[ReconciliationDocument] = []
    for record in documents:
        gstin = _field_value(record.document, "vendor_gstin")
        validation = validate_gstin(gstin) if isinstance(gstin, str) else None
        vendor_id: UUID | None = None
        if validation is not None and validation.is_valid and validation.pan:
            vendor_id = _stable_id("vendor-pan", validation.pan)
            pan_by_vendor[vendor_id] = validation.pan
        resolved.append(record.model_copy(update={"vendor_id": vendor_id}))
    return resolved, pan_by_vendor


def _mapping_snapshot(
    documents: list[ReconciliationDocument], config: ReconciliationConfig
) -> ReconciliationSnapshot:
    documents, vendor_pans = _vendor_anchors(documents)
    po_records = [item for item in documents if item.document.header.doc_type is DocType.PO]
    source_records = [
        item
        for item in documents
        if item.document.header.doc_type in {DocType.GRN, DocType.INVOICE}
    ]
    mapping_pos: list[MappingPO] = []
    for record in po_records:
        open_qty: dict[int, Decimal] = {}
        open_balance = Decimal("0")
        for line in record.document.lines:
            quantity = line.quantity.value if line.quantity is not None else None
            rate = line.rate.value if line.rate is not None else None
            amount = line.amount.value if line.amount is not None else None
            if quantity is not None:
                open_qty[line.line_no] = quantity
            if quantity is not None and rate is not None:
                open_balance += quantity * rate
            elif amount is not None:
                open_balance += amount
        mapping_pos.append(
            MappingPO(
                document_id=record.document_id,
                vendor_id=record.vendor_id,
                document=record.document,
                open_balance=open_balance,
                open_qty_by_line=open_qty,
            )
        )

    mapping_documents = [
        MappingDocument(
            document_id=record.document_id,
            natural_key=record.natural_key,
            vendor_id=record.vendor_id,
            document=record.document,
            source_text=record.document.raw_text,
        )
        for record in source_records
    ]
    link_config = MappingConfig(
        link_auto_accept=config.link_auto_accept,
        alignment=config.alignment,
    )
    grn_documents = [
        item for item in mapping_documents if item.document.header.doc_type is DocType.GRN
    ]
    preliminary_grn_links = resolve_links(grn_documents, mapping_pos, link_config)
    preliminary_by_grn = {item.document_id: item for item in preliminary_grn_links}

    support: dict[UUID, dict[int, Decimal]] = {item.document_id: {} for item in mapping_pos}
    po_by_id = {item.document_id: item for item in mapping_pos}
    doc_by_id = {item.document_id: item for item in mapping_documents}
    for grn_id, resolution in preliminary_by_grn.items():
        source = doc_by_id[grn_id]
        for link in resolution.links:
            if link.state not in {LinkState.AUTO, LinkState.CONFIRMED}:
                continue
            po = po_by_id[link.to_document_id]
            for aligned in align_lines(
                source.document.lines,
                po.document.lines,
                config.alignment,
                explicit_link=link.level is LinkLevel.LINE,
            ):
                if aligned.po_line_no is None or aligned.state is not AlignmentState.ALIGNED:
                    continue
                grn_line = next(
                    (
                        line
                        for line in source.document.lines
                        if line.line_no == aligned.invoice_line_no
                    ),
                    None,
                )
                if grn_line is None:
                    continue
                accepted = grn_line.accepted_qty
                received = grn_line.received_qty or grn_line.quantity
                rejected = grn_line.rejected_qty.value if grn_line.rejected_qty else Decimal("0")
                quantity = (
                    accepted.value
                    if accepted is not None
                    else max(Decimal("0"), received.value - rejected)
                    if received is not None
                    else Decimal("0")
                )
                current = support[po.document_id].get(aligned.po_line_no, Decimal("0"))
                support[po.document_id][aligned.po_line_no] = current + quantity

    mapping_pos = [
        item.model_copy(update={"grn_support_qty_by_line": support[item.document_id]})
        for item in mapping_pos
    ]
    resolutions = resolve_links(mapping_documents, mapping_pos, link_config)
    link_records: dict[UUID, ReconciliationLink] = {}
    for resolution in resolutions:
        for proposed in resolution.links:
            link_key = ":".join(
                (
                    str(proposed.from_document_id),
                    str(proposed.to_document_id),
                    proposed.level.value,
                    str(proposed.line_no or ""),
                    proposed.method.value,
                )
            )
            link_id = _stable_id("link", link_key)
            link_records[link_id] = ReconciliationLink(
                link_id=link_id,
                from_document_id=proposed.from_document_id,
                to_document_id=proposed.to_document_id,
                method=proposed.method,
                state=proposed.state,
                confidence=proposed.confidence,
                level=proposed.level,
                line_no=proposed.line_no,
            )

    return ReconciliationSnapshot(
        documents=tuple(documents),
        links=tuple(sorted(link_records.values(), key=lambda item: str(item.link_id))),
        vendor_pan_by_id=vendor_pans,
    )


def _run(folder: Path, config_path: Path, run_date: date, output_path: Path) -> int:
    if not folder.is_dir():
        raise ValueError("input folder does not exist or is not a directory")
    config, parser_config, config_hash = _load_configuration(config_path)
    documents, file_reports, file_errors = _read_documents(folder, parser_config)
    snapshot = _mapping_snapshot(documents, config)
    fingerprint = rules_hash(config)
    config_version_id = _stable_id("config", config_hash)
    manifest = build_run_manifest(
        snapshot,
        config,
        run_date,
        config_version_id=config_version_id,
        engine_version=ENGINE_VERSION,
    )
    cases = evaluate_components(snapshot, config, run_date)
    report = {
        "schema_version": 1,
        "engine_version": ENGINE_VERSION,
        "rules_hash": fingerprint,
        "run_date": run_date.isoformat(),
        "manifest_hash": manifest.manifest_hash,
        "config_hash": config_hash,
        "files": file_reports,
        "file_errors": file_errors,
        "document_count": len(documents),
        "case_count": len(cases),
        "cases": [item.model_dump(mode="json") for item in cases],
    }
    rendered = json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(rendered, encoding="utf-8", newline="\n")
    print(
        f"Reconciled {len(documents)} document(s) into {len(cases)} case(s); "
        f"rules_hash={fingerprint}; output={output_path}"
    )
    return 2 if file_errors or not documents else 0


def _run_snapshot(snapshot_path: Path, output_path: Path) -> int:
    """Evaluate a previously frozen application snapshot for parity checks."""

    raw = json.loads(snapshot_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("snapshot input must be a JSON object")
    snapshot = ReconciliationSnapshot.model_validate(raw.get("snapshot"))
    config = ReconciliationConfig.model_validate(raw.get("config"))
    run_date = date.fromisoformat(str(raw.get("run_date", "")))
    cases = evaluate_components(snapshot, config, run_date)
    rendered = (
        json.dumps(
            [item.model_dump(mode="json") for item in cases],
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )
        + "\n"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(rendered, encoding="utf-8", newline="\n")
    print(f"Evaluated {len(cases)} case(s) from frozen snapshot; output={output_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run deterministic XML/CSV reconciliation.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run", help="parse a folder and write reconciliation JSON")
    run_parser.add_argument("folder", type=Path)
    run_parser.add_argument("--config", required=True, type=Path)
    run_parser.add_argument("--run-date", required=True, type=date.fromisoformat)
    run_parser.add_argument("--out", required=True, type=Path)
    snapshot_parser = subparsers.add_parser(
        "run-snapshot", help="evaluate a frozen application snapshot for parity checks"
    )
    snapshot_parser.add_argument("snapshot", type=Path)
    snapshot_parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            return _run(args.folder, args.config, args.run_date, args.out)
        if args.command == "run-snapshot":
            return _run_snapshot(args.snapshot, args.out)
        raise ValueError(f"unsupported command: {args.command}")
    except (OSError, json.JSONDecodeError, ValidationError, ValueError) as exc:
        print(f"reconbench: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
