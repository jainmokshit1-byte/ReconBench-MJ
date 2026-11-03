"""A row that bills nothing (e.g. "Shipping 0.00") is not an item missing from the PO."""

from __future__ import annotations

from reconbench_core.schema import CaseStatus, DocType, ReasonCode
from tests.unit import test_phase4_reconciliation as recon_case
from tests.unit.test_pd2b_data_gate import _evaluate


def _invoice_with_extra_row(amount: str):  # type: ignore[no-untyped-def]
    row = (
        f"<Line><Description>Shipping charges</Description><Qty>1</Qty><Rate>{amount}</Rate>"
        f"<Amount>{amount}</Amount></Line></Invoice>"
    )
    xml = recon_case._invoice_xml("INV-2201", 10).replace("</Invoice>", row)
    return recon_case._document(xml, DocType.INVOICE)


def _codes(result) -> set[ReasonCode]:  # type: ignore[no-untyped-def]
    return set(result.reason_codes) | {c for line in result.lines for c in line.reason_codes}


def test_a_zero_amount_row_keeps_an_otherwise_matched_case_matched() -> None:
    result = _evaluate(_invoice_with_extra_row("0"))

    assert ReasonCode.ITEM_NOT_ON_PO not in _codes(result)
    assert result.status is CaseStatus.MATCHED


def test_a_billed_row_that_is_not_on_the_po_still_fails() -> None:
    result = _evaluate(_invoice_with_extra_row("500"))

    assert ReasonCode.ITEM_NOT_ON_PO in _codes(result)
    assert result.status is not CaseStatus.MATCHED
