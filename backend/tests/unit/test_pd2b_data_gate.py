"""PD-2b: the data gate counts a person's work, and an AI value only once a person verified it."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from decimal import Decimal

import pytest
from reconbench_core.recon import ReconciliationConfig, ReconciliationSnapshot, evaluate
from reconbench_core.schema import (
    CaseResult,
    CaseStatus,
    DocType,
    Document,
    FieldOrigin,
    FieldSource,
    FieldValue,
    FieldVerification,
    ReasonCode,
)
from tests.unit import test_phase4_reconciliation as recon_case

_MATCHED = {CaseStatus.MATCHED, CaseStatus.MATCHED_FLAGS}
# Every way a value can be marked as read by AI: its origin, its source, or both.
_AI_READ = (
    (FieldOrigin.AI, FieldSource.AI),
    (FieldOrigin.AI, FieldSource.RULE),
    (FieldOrigin.OCR, FieldSource.AI),
    (FieldOrigin.ERP_IMPORT, FieldSource.AI),
    (None, FieldSource.AI),
)
Change = Callable[[str, FieldValue[object]], FieldValue[object]]


def _invoice() -> Document:
    return recon_case._document(recon_case._invoice_xml("INV-2201", 10), DocType.INVOICE)


def _evaluate(invoice: Document) -> CaseResult:
    """SCN-01 (one PO, one GRN, one invoice): Matched when the invoice is read by rules."""

    po_doc = recon_case._document(recon_case._po_xml().replace("20260101", "20260115"), DocType.PO)
    po = recon_case._record(1, po_doc, DocType.PO)
    grn_doc = recon_case._document(recon_case._grn_xml(10, accepted=10), DocType.GRN)
    grn = recon_case._record(2, grn_doc, DocType.GRN)
    record = recon_case._record(3, invoice, DocType.INVOICE)
    snapshot = ReconciliationSnapshot(
        documents=(po, grn, record),
        links=(
            recon_case._link(10, grn.document_id, po.document_id),
            recon_case._link(11, record.document_id, po.document_id),
        ),
    )
    return evaluate(snapshot, ReconciliationConfig(), date(2026, 2, 2))[0]


def _change(document: Document, change: Change) -> Document:
    """Apply ``change`` to every header and line value, named as the engine names them."""

    header = document.header.model_copy(
        update={
            name: change(name, value)
            for name in type(document.header).model_fields
            if isinstance(value := getattr(document.header, name), FieldValue)
        }
    )
    lines = tuple(
        line.model_copy(
            update={
                name: change(f"line {line.line_no} {name}", value)
                for name in type(line).model_fields
                if isinstance(value := getattr(line, name), FieldValue)
            }
        )
        for line in document.lines
    )
    return document.model_copy(update={"header": header, "lines": lines})


def _only(names: set[str], change: Change) -> Change:
    return lambda name, value: change(name, value) if name in names else value


def _typed_by_a_person(_name: str, value: FieldValue[object]) -> FieldValue[object]:
    # A person's correction: manual, no confidence of its own, verified by that person.
    return value.model_copy(
        update={
            "source": FieldSource.MANUAL,
            "origin": FieldOrigin.MANUAL,
            "confidence": None,
            "verification": FieldVerification.USER,
        }
    )


def _verified(_name: str, value: FieldValue[object]) -> FieldValue[object]:
    return value.model_copy(update={"verification": FieldVerification.USER})


def _gate_check(result: CaseResult) -> list[str]:
    return [check.check_id for check in result.checks if check.check_id == "s1.extraction_gate"]


def _ai_check_fields(result: CaseResult) -> list[str]:
    checks = [check for check in result.checks if check.check_id == "s1.ai_values_verified"]
    assert len(checks) <= 1
    return list(checks[0].evidence["fields"]) if checks else []


def test_the_rules_read_invoice_is_matched_without_either_check() -> None:
    result = _evaluate(_invoice())

    assert result.status is CaseStatus.MATCHED
    assert not _gate_check(result) and not _ai_check_fields(result)


def test_an_invoice_a_person_typed_in_passes_the_gate() -> None:
    # F2: none of its values has a confidence, so Document.confidence stays at 0.
    typed = _change(_invoice(), _typed_by_a_person)
    assert typed.confidence == Decimal("0")

    result = _evaluate(typed)

    assert result.status is CaseStatus.MATCHED
    assert ReasonCode.LOW_CONFIDENCE_DATA not in result.reason_codes


def test_a_corrected_vendor_and_lines_no_longer_leave_the_gate_at_zero() -> None:
    corrected = _change(
        _invoice(),
        _only(
            {"vendor_name", "line 1 quantity", "line 1 rate", "line 1 amount"},
            _typed_by_a_person,
        ),
    )
    assert corrected.confidence == Decimal("0")

    assert _evaluate(corrected).status is CaseStatus.MATCHED


@pytest.mark.parametrize("name", ["doc_no", "doc_date", "grand_total", "line 1 quantity"])
def test_a_low_confidence_value_counts_once_a_person_verifies_it(name: str) -> None:
    low = _change(
        _invoice(),
        _only({name}, lambda _name, value: value.model_copy(update={"confidence": Decimal("0.5")})),
    )
    system_checked = _change(
        low,
        _only(
            {name},
            lambda _name, value: value.model_copy(
                update={"verification": FieldVerification.SYSTEM_CHECK}
            ),
        ),
    )

    for unchecked in (low, system_checked):
        result = _evaluate(unchecked)
        assert result.status is CaseStatus.NEEDS_REVIEW
        assert ReasonCode.LOW_CONFIDENCE_DATA in result.reason_codes
        assert all(line.status is CaseStatus.NEEDS_REVIEW for line in result.lines)
    assert _evaluate(_change(low, _only({name}, _verified))).status is CaseStatus.MATCHED


def test_the_gate_check_reports_version_2() -> None:
    low = _change(
        _invoice(),
        _only(
            {"grand_total"},
            lambda _name, value: value.model_copy(update={"confidence": Decimal("0.5")}),
        ),
    )

    checks = [check for check in _evaluate(low).checks if check.check_id == "s1.extraction_gate"]

    assert [check.check_version for check in checks] == [2]
    assert checks[0].actual == Decimal("0.5")


def test_a_missing_key_value_fails_the_gate_however_much_was_verified() -> None:
    invoice = _change(_invoice(), _verified)
    missing = invoice.model_copy(
        update={"header": invoice.header.model_copy(update={"doc_date": None})}
    )

    result = _evaluate(missing)

    assert result.status not in _MATCHED
    assert ReasonCode.LOW_CONFIDENCE_DATA in result.reason_codes


@pytest.mark.parametrize(("origin", "source"), _AI_READ)
@pytest.mark.parametrize("verification", [FieldVerification.NONE, FieldVerification.SYSTEM_CHECK])
@pytest.mark.parametrize("name", ["doc_no", "grand_total", "line 1 description", "line 1 rate"])
def test_an_unverified_ai_value_needs_review_whatever_its_confidence(
    origin: FieldOrigin | None,
    source: FieldSource,
    verification: FieldVerification,
    name: str,
) -> None:
    read = _change(
        _invoice(),
        _only(
            {name},
            lambda _name, value: value.model_copy(
                update={
                    "origin": origin,
                    "source": source,
                    "confidence": Decimal("1"),
                    "verification": verification,
                }
            ),
        ),
    )

    result = _evaluate(read)

    assert result.status is CaseStatus.NEEDS_REVIEW
    assert ReasonCode.AI_VALUE_UNVERIFIED in result.reason_codes
    assert _ai_check_fields(result) == [name]
    assert all(line.status not in _MATCHED for line in result.lines)


def test_ai_values_count_once_a_person_verifies_each_one() -> None:
    def read_by_ai(_name: str, value: FieldValue[object]) -> FieldValue[object]:
        return value.model_copy(
            update={"origin": FieldOrigin.AI, "source": FieldSource.AI, "confidence": Decimal("1")}
        )

    read = _change(_invoice(), read_by_ai)
    names = _ai_check_fields(_evaluate(read))
    assert "doc_no" in names and "line 1 quantity" in names

    all_but_one = _change(read, _only(set(names) - {"line 1 description"}, _verified))
    result = _evaluate(all_but_one)
    assert result.status is CaseStatus.NEEDS_REVIEW
    assert _ai_check_fields(result) == ["line 1 description"]

    result = _evaluate(_change(read, _verified))
    assert result.status is CaseStatus.MATCHED
    assert not _ai_check_fields(result)
