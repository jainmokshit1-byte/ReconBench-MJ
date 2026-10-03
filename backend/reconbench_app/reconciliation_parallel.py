"""Isolated connected-component evaluation for reconciliation worker processes."""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
from datetime import date
from uuid import UUID

from reconbench_core.normalize import canonicalize_document_number
from reconbench_core.recon.engine import evaluate, evaluate_components, partition_components
from reconbench_core.recon.types import (
    ReconciliationConfig,
    ReconciliationDocument,
    ReconciliationSnapshot,
)
from reconbench_core.schema import CaseResult


def _evaluate_component_payload(
    payload: tuple[str, str, str],
) -> tuple[CaseResult, ...]:
    snapshot_json, config_json, run_date_text = payload
    snapshot = ReconciliationSnapshot.model_validate_json(snapshot_json)
    config = ReconciliationConfig.model_validate_json(config_json)
    return evaluate(snapshot, config, date.fromisoformat(run_date_text))


def evaluate_components_parallel(
    snapshot: ReconciliationSnapshot,
    config: ReconciliationConfig,
    run_date: date,
    *,
    max_workers: int,
) -> tuple[CaseResult, ...]:
    """Evaluate independent ledger/duplicate components in child processes.

    Snapshots are validated again in each child. Related invoice/PO/GRN documents
    and duplicate candidates remain together because partitioning happens first.
    """

    if max_workers < 1:
        raise ValueError("max_workers must be at least one")
    if max_workers == 1:
        return evaluate_components(snapshot, config, run_date)
    components = partition_components(snapshot, config)
    if len(components) < 2:
        return evaluate_components(snapshot, config, run_date)

    config_json = config.model_dump_json()
    payloads = [
        (
            component.model_dump_json(exclude_computed_fields=True),
            config_json,
            run_date.isoformat(),
        )
        for component in components
    ]
    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        results = [
            result
            for component_results in executor.map(_evaluate_component_payload, payloads)
            for result in component_results
        ]
    documents = {item.document_id: item for item in snapshot.documents}
    results.sort(key=lambda item: _result_sort_key(item, documents))
    return tuple(results)


def _result_sort_key(
    result: CaseResult,
    documents: dict[UUID, ReconciliationDocument],
) -> tuple[date, str, str, str]:
    if result.invoice_id is None:
        raise ValueError("reconciliation case is missing its invoice ID")
    record = documents[result.invoice_id]
    header = record.document.header
    invoice_date = getattr(getattr(header, "doc_date", None), "value", None)
    raw_number = getattr(getattr(header, "doc_no", None), "value", None) or ""
    return (
        invoice_date if isinstance(invoice_date, date) else date.max,
        canonicalize_document_number(str(raw_number)),
        record.natural_key or str(record.document_id),
        str(record.document_id),
    )


__all__ = ["evaluate_components_parallel"]
