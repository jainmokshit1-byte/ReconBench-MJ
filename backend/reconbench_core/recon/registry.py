"""Versioned check catalog and deterministic rules fingerprint."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from reconbench_core.recon.types import ReconciliationConfig


@dataclass(frozen=True, order=True)
class CheckDefinition:
    """Stable public identity for one pure reconciliation check."""

    check_id: str
    version: int
    stage: str


CHECK_REGISTRY = (
    CheckDefinition("s1.ai_values_verified", 1, "S1"),
    CheckDefinition("s1.exact_duplicate", 1, "S1"),
    # Version 2 (PD-2b): a value a person verified or corrected counts as certain.
    CheckDefinition("s1.extraction_gate", 2, "S1"),
    CheckDefinition("s1.po_open", 1, "S1"),
    CheckDefinition("s1.suspected_duplicate", 1, "S1"),
    CheckDefinition("s1.vendor_resolved", 1, "S1"),
    CheckDefinition("s2.link_confidence", 1, "S2"),
    CheckDefinition("s2.multiple_pos_line", 1, "S2"),
    CheckDefinition("s2.po_link", 1, "S2"),
    CheckDefinition("s3.item_alignment", 1, "S3"),
    CheckDefinition("s4.cumulative_qty_vs_grn", 1, "S4"),
    CheckDefinition("s4.cumulative_qty_vs_po", 1, "S4"),
    CheckDefinition("s4.grn_over_po", 1, "S4"),
    CheckDefinition("s4.grn_pending", 1, "S4"),
    CheckDefinition("s4.grn_quantity_fallback", 1, "S4"),
    CheckDefinition("s4.ledger_completeness", 1, "S4"),
    CheckDefinition("s5.cumulative_po_value", 1, "S5"),
    CheckDefinition("s5.extra_charge", 1, "S5"),
    CheckDefinition("s5.grand_total", 1, "S5"),
    CheckDefinition("s5.line_math", 1, "S5"),
    CheckDefinition("s5.taxable_sum", 1, "S5"),
    CheckDefinition("s5.total_vs_expected", 1, "S5"),
    CheckDefinition("s5.unit_rate", 1, "S5"),
    CheckDefinition("s6.buyer_entity", 1, "S6"),
    CheckDefinition("s6.buyer_gstin_valid", 1, "S6"),
    CheckDefinition("s6.computed_tax", 1, "S6"),
    CheckDefinition("s6.currency", 1, "S6"),
    CheckDefinition("s6.date_order", 1, "S6"),
    CheckDefinition("s6.grn_date_order", 1, "S6"),
    CheckDefinition("s6.hsn", 1, "S6"),
    CheckDefinition("s6.late_delivery", 1, "S6"),
    CheckDefinition("s6.tax_rate_diff", 1, "S6"),
    CheckDefinition("s6.tax_slab", 1, "S6"),
    CheckDefinition("s6.tax_type", 1, "S6"),
    CheckDefinition("s6.uom", 1, "S6"),
    CheckDefinition("s6.vendor_gstin_valid", 1, "S6"),
    CheckDefinition("s6.vendor_match", 1, "S6"),
    CheckDefinition("s6.vendor_pan", 1, "S6"),
)

_BY_ID = {item.check_id: item for item in CHECK_REGISTRY}


def check_definition(check_id: str) -> CheckDefinition:
    """Return registered metadata or fail on an undeclared rule identifier."""

    try:
        return _BY_ID[check_id]
    except KeyError as exc:
        raise ValueError(f"check ID is not registered: {check_id}") from exc


def rules_hash(
    config: ReconciliationConfig,
    registry: Iterable[CheckDefinition] = CHECK_REGISTRY,
) -> str:
    """Fingerprint only the rule registry and engine configuration."""

    return rules_hash_for_engine(config.model_dump(mode="json"), registry)


def rules_hash_for_engine(
    engine: Mapping[str, object],
    registry: Iterable[CheckDefinition] = CHECK_REGISTRY,
) -> str:
    """Fingerprint the versioned check catalog and canonical Schema §5.1 engine body."""

    payload = {
        "checks": [
            {
                "check_id": item.check_id,
                "stage": item.stage,
                "version": item.version,
            }
            for item in sorted(registry)
        ],
        "engine": engine,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
