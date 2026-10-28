"""Canonical Phase 4 core models remain frozen and decimal-safe."""

from __future__ import annotations

import ast
import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError
from reconbench_core.schema import (
    DATABASE_ENUMS,
    REASON_CODES,
    CaseResult,
    CaseStatus,
    DecimalValue,
    DocLine,
    Document,
    FieldEvidence,
    FieldSource,
    FieldValue,
    InvoiceHeader,
    LineResult,
    LineType,
    Manifest,
    ManifestDocument,
    POHeader,
    ReasonCode,
    _manifest_document_key,
)


def test_reason_code_catalog_has_the_planned_29_codes() -> None:
    # The plan's 28 codes and AI_VALUE_UNVERIFIED (PD-2b).
    assert len(REASON_CODES) == 29
    assert len({code.value for code in REASON_CODES}) == 29


def test_core_enums_match_all_database_enum_values() -> None:
    migration = Path(__file__).parents[2] / "migrations" / "versions" / "0001_setup.py"
    source = migration.read_text(encoding="utf-8")
    database_values = {
        name: tuple(member.strip().strip("'") for member in values.split(","))
        for name, values in re.findall(r"CREATE TYPE\s+(\w+)\s+AS ENUM\s*\(([^)]*)\)", source)
    }

    assert set(DATABASE_ENUMS) == set(database_values)
    for name, enum_type in DATABASE_ENUMS.items():
        assert tuple(member.value for member in enum_type) == database_values[name]


def test_header_discriminator_and_unknown_extras_are_preserved() -> None:
    parsed = Document.model_validate(
        {
            "header": {
                "doc_type": "invoice",
                "extras": {"CUSTOM-VOUCHER-TAG": {"raw": "kept"}},
            }
        }
    )

    assert isinstance(parsed.header, InvoiceHeader)
    assert parsed.header.extras == {"CUSTOM-VOUCHER-TAG": {"raw": "kept"}}


def test_line_values_reject_binary_float() -> None:
    with pytest.raises(ValidationError, match="binary floating-point"):
        DocLine(
            line_no=1,
            quantity={
                "value": 1.25,
                "confidence": "1",
                "source": "rule",
                "evidence": {"snippet": "1.25 NOS"},
            },
        )
    with pytest.raises(ValidationError, match="binary floating-point"):
        FieldValue[DecimalValue].model_validate_json(
            '{"value":1.25,"confidence":"1","source":"rule","evidence":{}}'
        )


def test_numeric_fields_do_not_annotate_float() -> None:
    schema_path = Path(__file__).parents[2] / "reconbench_core" / "schema" / "__init__.py"
    tree = ast.parse(schema_path.read_text(encoding="utf-8"))
    protected_fields = {
        "taxable_total",
        "cgst",
        "sgst",
        "igst",
        "round_off",
        "grand_total",
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
        "alignment_score",
        "po_qty",
        "po_cap",
        "billed_before",
        "billed_qty",
        "grn_accepted_cum",
        "grn_rejected_cum",
        "eligible_qty",
        "balance_after",
        "shortfall_qty",
        "po_rate",
        "invoice_net_rate",
        "payable_rate",
        "line_taxable",
        "payable_taxable",
        "at_risk",
        "invoice_taxable",
        "invoice_total",
        "expected_taxable",
        "variance",
        "variance_pct",
        "at_risk_gst",
        "blocked_value",
    }
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id in protected_fields
        ):
            assert "float" not in ast.unparse(node.annotation), node.target.id


def test_frozen_case_result_rejects_attribute_reassignment() -> None:
    result = CaseResult(
        engine_version="0.1.0",
        rules_hash="a" * 64,
        status=CaseStatus.MATCHED,
    )
    with pytest.raises(ValidationError):
        result.status = CaseStatus.EXCEPTION  # type: ignore[misc]


def test_frozen_result_schemas_require_engine_and_rules_fingerprints() -> None:
    for model in (CaseResult, LineResult):
        required = set(model.model_json_schema()["required"])
        assert {"engine_version", "rules_hash"}.issubset(required)


def test_manifest_orders_inputs_and_hashes_stably() -> None:
    first_id = "00000000-0000-0000-0000-000000000001"
    second_id = "00000000-0000-0000-0000-000000000002"
    config_id = "00000000-0000-0000-0000-000000000003"
    common = {
        "engine_version": "0.1.0",
        "rules_hash": "a" * 64,
        "config_version_id": config_id,
        "run_date": "2026-10-02",
    }
    left = Manifest(
        **common,
        documents=(ManifestDocument(version_id=second_id), ManifestDocument(version_id=first_id)),
        links=(second_id, first_id),
    )
    right = Manifest(
        **common,
        documents=(ManifestDocument(version_id=first_id), ManifestDocument(version_id=second_id)),
        links=(first_id, second_id),
    )

    assert left.documents == right.documents
    assert left.links == right.links
    assert left.manifest_hash == right.manifest_hash


def test_invoice_document_confidence_returns_zero_when_critical_groups_are_missing() -> None:
    def field(value: object) -> FieldValue[object]:
        return FieldValue(
            value=value,
            confidence=Decimal("0.9"),
            source=FieldSource.RULE,
            evidence=FieldEvidence(snippet=str(value)),
        )

    base_header = {
        "doc_no": field("INV-1"),
        "doc_date": field(date(2026, 4, 1)),
        "grand_total": field(Decimal("10")),
    }
    missing_vendor = Document(
        header=InvoiceHeader(**base_header),
        lines=(DocLine(line_no=1, quantity=field(Decimal("1"))),),
    )
    missing_line = Document(
        header=InvoiceHeader(**base_header, vendor_name=field("Vendor")),
        lines=(DocLine(line_no=1),),
    )
    po_document = Document(header=POHeader())

    assert missing_vendor.confidence == Decimal("0")
    assert missing_line.confidence == Decimal("0")
    assert po_document.confidence is None


def test_manifest_prevalidator_handles_partial_and_non_mapping_inputs() -> None:
    first_id = "00000000-0000-0000-0000-000000000001"
    second_id = "00000000-0000-0000-0000-000000000002"

    assert Manifest._sort_manifest_collections(None) is None
    partial = Manifest._sort_manifest_collections({"links": [second_id, first_id]})

    assert partial["links"] == [first_id, second_id]
    assert _manifest_document_key({"version_id": second_id}) == second_id
    assert _manifest_document_key({}) == ""
    assert _manifest_document_key("version-like") == "version-like"


def test_enum_and_field_value_types_serialize_as_canonical_strings() -> None:
    value = FieldValue[DecimalValue](
        value="123.4500",
        confidence="0.95",
        source=FieldSource.RULE,
        evidence=FieldEvidence(page=1, snippet="₹123.45"),
    )
    line = DocLine(
        line_no=1,
        line_type={"value": "goods", "confidence": "1", "source": "rule", "evidence": {}},
    )

    assert str(value.value) == "123.4500"
    assert line.line_type is not None and line.line_type.value is LineType.GOODS
    assert ReasonCode.RATE_HIGH.value == "RATE_HIGH"
