"""AI_LINES_MISSING: lines the rules read and the AI reading left out are shown to a person."""

from __future__ import annotations

from reconbench_app.ingestion import rules_only_line_numbers
from reconbench_core.extraction.merge import CROSS_CHECK_KEY
from reconbench_core.schema import CaseStatus, DocType, Document, ReasonCode
from tests.unit import test_phase4_reconciliation as recon_case
from tests.unit.test_pd2b_data_gate import _evaluate, _invoice


def _missing_lines(invoice: Document) -> Document:
    return invoice.model_copy(update={"reason_codes": (ReasonCode.AI_LINES_MISSING,)})


def _checks(case_checks: object, check_id: str) -> list[object]:
    return [check for check in case_checks if check.check_id == check_id]  # type: ignore[attr-defined]


def test_an_invoice_without_the_reason_has_no_lines_check() -> None:
    result = _evaluate(_invoice())

    assert not _checks(result.checks, "s1.ai_lines_read")
    assert ReasonCode.AI_LINES_MISSING not in result.reason_codes


def test_the_reason_adds_a_review_check_and_a_case_reason() -> None:
    result = _evaluate(_missing_lines(_invoice()))

    [check] = _checks(result.checks, "s1.ai_lines_read")
    assert check.code is ReasonCode.AI_LINES_MISSING  # type: ignore[attr-defined]
    assert ReasonCode.AI_LINES_MISSING in result.reason_codes
    # Hard zero: an invoice that is otherwise Matched is never Matched with missing lines.
    assert result.status is CaseStatus.NEEDS_REVIEW


def test_the_reason_holds_a_case_that_would_otherwise_match() -> None:
    assert _evaluate(_invoice()).status is CaseStatus.MATCHED
    assert _evaluate(_missing_lines(_invoice())).status is not CaseStatus.MATCHED


def test_rules_line_numbers_come_from_the_ocr_only_cross_checks() -> None:
    document = recon_case._document(recon_case._invoice_xml("INV-1", 1), DocType.INVOICE)
    extras = {
        CROSS_CHECK_KEY: [
            {"field": "line:3:*", "outcome": "ocr_only_line", "ai": None, "ocr": "7.00"},
            {"field": "line:1:*", "outcome": "ocr_only_line", "ai": None, "ocr": "9.00"},
            {"field": "line:2:amount", "outcome": "agree", "ai": "1", "ocr": "1"},
            {"field": "header:doc_no", "outcome": "agree", "ai": "A", "ocr": "A"},
        ]
    }
    header = document.header.model_copy(update={"extras": extras})

    assert rules_only_line_numbers(document.model_copy(update={"header": header})) == [1, 3]


def test_no_cross_checks_means_no_line_numbers() -> None:
    document = recon_case._document(recon_case._invoice_xml("INV-1", 1), DocType.INVOICE)

    assert rules_only_line_numbers(document) == []
