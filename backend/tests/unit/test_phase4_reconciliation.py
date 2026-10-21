"""S1/S4 reconciliation behavior on small deterministic document snapshots."""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest
import reconbench_core.recon.engine as engine_module
import reconbench_core.recon.registry as registry_module
from hypothesis import given, settings
from hypothesis import strategies as st
from reconbench_core.parsers import parse
from reconbench_core.recon import (
    CHECK_REGISTRY,
    CheckDefinition,
    OpeningBalance,
    ReconciliationConfig,
    ReconciliationDocument,
    ReconciliationLink,
    ReconciliationSnapshot,
    ToleranceRule,
    ToleranceValues,
    build_run_manifest,
    evaluate,
    evaluate_components,
    partition_components,
    rules_hash,
)
from reconbench_core.recon.engine import _effective_tolerance, _rule, _tolerance_pass
from reconbench_core.schema import (
    CaseStatus,
    DocLine,
    DocType,
    LinkLevel,
    LinkMethod,
    LinkState,
    ReasonCode,
    Tolerance,
    ToleranceMode,
    ToleranceSource,
)

_VENDOR = UUID(int=500)


def _id(value: int) -> UUID:
    return UUID(int=value)


def _document(xml: str, doc_type: DocType):
    parsed = parse(xml.encode(), {"file_format": "xml", "doc_type": doc_type})
    assert parsed.success and len(parsed.documents) == 1
    return parsed.documents[0]


def _po_xml() -> str:
    return (
        "<PurchaseOrder><OrderNo>PO-100</OrderNo><OrderDate>20260101</OrderDate>"
        "<Status>open</Status>"
        "<Supplier>Acme Supplies</Supplier><TaxableTotal>480000</TaxableTotal>"
        "<Line><Description>Widget model A</Description><Qty>10</Qty>"
        "<Rate>48000</Rate><Amount>480000</Amount></Line></PurchaseOrder>"
    )


def _invoice_xml(number: str, quantity: int, rate: int | Decimal = 48000) -> str:
    taxable = quantity * rate
    total = taxable + taxable * 18 // 100
    return (
        f"<Invoice><InvoiceNo>{number}</InvoiceNo><InvoiceDate>20260201</InvoiceDate>"
        "<Supplier>Acme Supplies</Supplier><TaxableTotal>"
        + str(taxable)
        + "</TaxableTotal><IGST>"
        + str(taxable * 18 // 100)
        + "</IGST><InvoiceTotal>"
        + str(total)
        + "</InvoiceTotal><Line><Description>Widget model A</Description><Qty>"
        + str(quantity)
        + "</Qty><Rate>"
        + str(rate)
        + "</Rate><Amount>"
        + str(taxable)
        + "</Amount></Line></Invoice>"
    )


def _grn_xml(received: int, rejected: int = 0, accepted: int | None = None) -> str:
    accepted_xml = f"<AcceptedQty>{accepted}</AcceptedQty>" if accepted is not None else ""
    return (
        "<GRN><GRNNo>GRN-1</GRNNo><GRNDate>20260115</GRNDate>"
        "<Supplier>Acme Supplies</Supplier><PORef>PO-100</PORef>"
        "<Line><Description>Widget model A</Description>"
        + accepted_xml
        + "<ReceivedQty>"
        + str(received)
        + "</ReceivedQty><RejectedQty>"
        + str(rejected)
        + "</RejectedQty></Line></GRN>"
    )


def _record(number: int, document, doc_type: DocType) -> ReconciliationDocument:
    return ReconciliationDocument(
        document_id=_id(number),
        version_id=_id(number + 1000),
        vendor_id=_VENDOR,
        natural_key=str(number),
        document=document,
    )


def _evaluate_worker(
    arguments: tuple[str, str, str],
) -> tuple[str, ...]:
    snapshot_json, config_json, run_date_text = arguments
    snapshot = ReconciliationSnapshot.model_validate_json(snapshot_json)
    config = ReconciliationConfig.model_validate_json(config_json)
    run_date = date.fromisoformat(run_date_text)
    return tuple(result.model_dump_json() for result in evaluate(snapshot, config, run_date))


def _contains_float(value: object) -> bool:
    if isinstance(value, float):
        return True
    if isinstance(value, dict):
        return any(_contains_float(key) or _contains_float(item) for key, item in value.items())
    if isinstance(value, (tuple, list, set)):
        return any(_contains_float(item) for item in value)
    return False


def _link(number: int, source_id: UUID, target_id: UUID) -> ReconciliationLink:
    return ReconciliationLink(
        link_id=_id(number),
        from_document_id=source_id,
        to_document_id=target_id,
        method=LinkMethod.LABELED,
        state=LinkState.AUTO,
        confidence=Decimal("0.99"),
        level=LinkLevel.HEADER,
    )


def test_scn_04_billing_over_po_and_received_sets_both_quantity_failures() -> None:
    po = _record(1, _document(_po_xml(), DocType.PO), DocType.PO)
    grn = _record(2, _document(_grn_xml(10), DocType.GRN), DocType.GRN)
    invoice = _record(3, _document(_invoice_xml("INV-2201", 15), DocType.INVOICE), DocType.INVOICE)
    snapshot = ReconciliationSnapshot(
        documents=(po, grn, invoice),
        links=(
            _link(10, grn.document_id, po.document_id),
            _link(11, invoice.document_id, po.document_id),
        ),
    )

    result = evaluate(snapshot, ReconciliationConfig(), date(2026, 2, 2))[0]

    assert result.status is CaseStatus.EXCEPTION
    assert ReasonCode.QTY_OVER_PO in result.reason_codes
    assert ReasonCode.QTY_OVER_GRN in result.reason_codes
    assert result.lines[0].eligible_qty == Decimal("10")
    assert result.lines[0].billed_before == Decimal("0")
    assert result.lines[0].balance_after == Decimal("0")
    assert result.expected_taxable == Decimal("720000")
    assert result.payable_taxable == Decimal("480000")
    assert result.at_risk == Decimal("240000")
    assert result.at_risk_gst == Decimal("43200")


def test_scn_01_one_po_one_grn_one_invoice_is_matched() -> None:
    po_doc = _document(_po_xml().replace("20260101", "20260115"), DocType.PO)
    po = _record(1, po_doc, DocType.PO)
    grn = _record(2, _document(_grn_xml(10, accepted=10), DocType.GRN), DocType.GRN)
    invoice = _record(3, _document(_invoice_xml("INV-2201", 10), DocType.INVOICE), DocType.INVOICE)
    snapshot = ReconciliationSnapshot(
        documents=(po, grn, invoice),
        links=(
            _link(10, grn.document_id, po.document_id),
            _link(11, invoice.document_id, po.document_id),
        ),
    )

    result = evaluate(snapshot, ReconciliationConfig(), date(2026, 2, 2))[0]

    assert result.status is CaseStatus.MATCHED
    assert result.payable_taxable == Decimal("480000")
    assert result.at_risk == Decimal("0")


def test_scn_02_two_invoices_consume_the_cumulative_receipt_and_po_balance() -> None:
    po_xml = _po_xml().replace("20260101", "20260115")
    po_xml = po_xml.replace("<Qty>10</Qty>", "<Qty>100</Qty>")
    po_xml = po_xml.replace(
        "<TaxableTotal>480000</TaxableTotal>", "<TaxableTotal>4800000</TaxableTotal>"
    )
    po_xml = po_xml.replace("<Amount>480000</Amount>", "<Amount>4800000</Amount>")
    po = _record(1, _document(po_xml, DocType.PO), DocType.PO)
    grn_90 = _record(
        2, _document(_grn_xml(90, accepted=90).replace("GRN-1", "GRN-90"), DocType.GRN), DocType.GRN
    )
    grn_10 = _record(
        4, _document(_grn_xml(10, accepted=10).replace("GRN-1", "GRN-10"), DocType.GRN), DocType.GRN
    )
    invoice_90 = _record(
        3,
        _document(_invoice_xml("ACR/26-27/2201", 90), DocType.INVOICE),
        DocType.INVOICE,
    )
    invoice_10 = _record(
        5,
        _document(_invoice_xml("ACR/26-27/2260", 10), DocType.INVOICE),
        DocType.INVOICE,
    )
    snapshot = ReconciliationSnapshot(
        documents=(po, grn_90, grn_10, invoice_90, invoice_10),
        links=(
            _link(10, grn_90.document_id, po.document_id),
            _link(11, grn_10.document_id, po.document_id),
            _link(12, invoice_90.document_id, po.document_id),
            _link(13, invoice_10.document_id, po.document_id),
        ),
    )

    results = evaluate(snapshot, ReconciliationConfig(), date(2026, 2, 2))

    assert [item.status for item in results] == [CaseStatus.MATCHED, CaseStatus.MATCHED]
    assert [item.lines[0].billed_before for item in results] == [Decimal("0"), Decimal("90")]
    assert [item.lines[0].eligible_qty for item in results] == [Decimal("90"), Decimal("10")]
    assert [item.invoice_taxable for item in results] == [
        Decimal("4320000"),
        Decimal("480000"),
    ]
    assert [item.payable_taxable for item in results] == [
        Decimal("4320000"),
        Decimal("480000"),
    ]
    assert [item.at_risk for item in results] == [Decimal("0"), Decimal("0")]
    assert results[-1].lines[0].balance_after == Decimal("0")


def test_scn_03_multiple_grns_are_summed_for_one_invoice() -> None:
    po_xml = _po_xml().replace("20260101", "20260115")
    po_xml = po_xml.replace("<Qty>10</Qty>", "<Qty>100</Qty>")
    po_xml = po_xml.replace("<Rate>48000</Rate>", "<Rate>10</Rate>")
    po_xml = po_xml.replace(
        "<TaxableTotal>480000</TaxableTotal>", "<TaxableTotal>1000</TaxableTotal>"
    )
    po_xml = po_xml.replace("<Amount>480000</Amount>", "<Amount>1000</Amount>")
    po = _record(1, _document(po_xml, DocType.PO), DocType.PO)
    grn_60 = _record(
        2, _document(_grn_xml(60, accepted=60).replace("GRN-1", "GRN-60"), DocType.GRN), DocType.GRN
    )
    grn_30 = _record(
        4, _document(_grn_xml(30, accepted=30).replace("GRN-1", "GRN-30"), DocType.GRN), DocType.GRN
    )
    invoice = _record(
        3, _document(_invoice_xml("INV-2290", 90, rate=10), DocType.INVOICE), DocType.INVOICE
    )
    snapshot = ReconciliationSnapshot(
        documents=(po, grn_60, grn_30, invoice),
        links=(
            _link(10, grn_60.document_id, po.document_id),
            _link(11, grn_30.document_id, po.document_id),
            _link(12, invoice.document_id, po.document_id),
        ),
    )

    result = evaluate(snapshot, ReconciliationConfig(), date(2026, 2, 2))[0]

    assert result.status is CaseStatus.MATCHED
    assert result.lines[0].grn_accepted_cum == Decimal("90")
    assert result.lines[0].eligible_qty == Decimal("90")


def test_scn_05_missing_grn_is_pending_then_ages_to_exception() -> None:
    po = _record(1, _document(_po_xml().replace("20260101", "20260115"), DocType.PO), DocType.PO)
    invoice = _record(3, _document(_invoice_xml("INV-2201", 10), DocType.INVOICE), DocType.INVOICE)
    snapshot = ReconciliationSnapshot(
        documents=(po, invoice),
        links=(_link(11, invoice.document_id, po.document_id),),
    )

    fresh = evaluate(snapshot, ReconciliationConfig(), date(2026, 2, 2))[0]
    aged = evaluate(snapshot, ReconciliationConfig(), date(2026, 3, 5))[0]
    delivered_grn = _record(2, _document(_grn_xml(10, accepted=10), DocType.GRN), DocType.GRN)
    delivered_snapshot = ReconciliationSnapshot(
        documents=(po, invoice, delivered_grn),
        links=(
            _link(10, delivered_grn.document_id, po.document_id),
            _link(11, invoice.document_id, po.document_id),
        ),
    )
    delivered = evaluate(delivered_snapshot, ReconciliationConfig(), date(2026, 3, 5))[0]

    assert fresh.status is CaseStatus.PENDING
    assert ReasonCode.GRN_PENDING in fresh.reason_codes
    assert not fresh.aged
    assert aged.status is CaseStatus.EXCEPTION
    assert aged.aged
    assert aged.pending_days == 32
    assert delivered.status is CaseStatus.MATCHED


def test_scn_05_aged_grn_pending_review_case_is_promoted_to_exception() -> None:
    po = _record(1, _document(_po_xml().replace("20260101", "20260115"), DocType.PO), DocType.PO)
    invoice = _record(3, _document(_invoice_xml("INV-2201", 10), DocType.INVOICE), DocType.INVOICE)
    review_document = invoice.document.model_copy(
        update={"reason_codes": (ReasonCode.NO_TEXT_LAYER,)}
    )
    invoice = invoice.model_copy(update={"document": review_document})
    snapshot = ReconciliationSnapshot(
        documents=(po, invoice),
        links=(_link(11, invoice.document_id, po.document_id),),
    )

    review = evaluate(snapshot, ReconciliationConfig(), date(2026, 2, 28))[0]
    aged = evaluate(snapshot, ReconciliationConfig(), date(2026, 3, 5))[0]

    assert review.status is CaseStatus.NEEDS_REVIEW
    assert not review.aged
    assert aged.status is CaseStatus.EXCEPTION
    assert aged.aged
    assert ReasonCode.GRN_PENDING in aged.reason_codes
    assert ReasonCode.NO_TEXT_LAYER in aged.reason_codes


def test_scn_17_prior_period_documents_are_in_scope_and_absent_po_stays_unlinked() -> None:
    po = _record(
        1,
        _document(_po_xml().replace("20260101", "20260120"), DocType.PO),
        DocType.PO,
    )
    grn = _record(
        2,
        _document(_grn_xml(10, accepted=10).replace("20260115", "20260125"), DocType.GRN),
        DocType.GRN,
    )
    in_scope_invoice = _record(
        3,
        _document(
            _invoice_xml("INV-PRIOR-PERIOD", 10).replace("20260201", "20260901"), DocType.INVOICE
        ),
        DocType.INVOICE,
    )
    in_scope = ReconciliationSnapshot(
        documents=(po, grn, in_scope_invoice),
        links=(
            _link(10, grn.document_id, po.document_id),
            _link(11, in_scope_invoice.document_id, po.document_id),
        ),
        opening_balances=(
            OpeningBalance(
                opening_balance_id=_id(12),
                po_document_id=po.document_id,
                po_line_no=1,
            ),
        ),
    )
    absent_invoice = _record(
        13,
        _document(
            _invoice_xml("INV-NOT-IN-POOL", 1).replace(
                "</Invoice>", "<PORef>PO-NOT-UPLOADED</PORef></Invoice>"
            ),
            DocType.INVOICE,
        ),
        DocType.INVOICE,
    )
    out_of_scope = ReconciliationSnapshot(documents=(absent_invoice,))

    in_scope_result = evaluate(in_scope, ReconciliationConfig(), date(2026, 9, 30))[0]
    out_of_scope_result = evaluate(out_of_scope, ReconciliationConfig(), date(2026, 9, 30))[0]

    assert in_scope_result.po_nos == ("PO-100",)
    assert in_scope_result.status is CaseStatus.MATCHED
    assert out_of_scope_result.status is CaseStatus.UNLINKED
    assert ReasonCode.NO_PO_LINK in out_of_scope_result.reason_codes


def test_scn_18_shuffled_snapshot_order_produces_identical_result_bytes() -> None:
    po = _record(1, _document(_po_xml(), DocType.PO), DocType.PO)
    grn = _record(2, _document(_grn_xml(10, accepted=10), DocType.GRN), DocType.GRN)
    september_invoice = _record(
        3, _document(_invoice_xml("INV-1018-SEP", 4), DocType.INVOICE), DocType.INVOICE
    )
    october_invoice = _record(
        4,
        _document(_invoice_xml("INV-1018-OCT", 6).replace("20260201", "20261001"), DocType.INVOICE),
        DocType.INVOICE,
    )
    links = (
        _link(10, grn.document_id, po.document_id),
        _link(11, september_invoice.document_id, po.document_id),
        _link(12, october_invoice.document_id, po.document_id),
    )
    first_order = ReconciliationSnapshot(
        documents=(po, grn, september_invoice, october_invoice), links=links
    )
    shuffled_order = ReconciliationSnapshot(
        documents=(october_invoice, september_invoice, grn, po),
        links=tuple(reversed(links)),
    )

    first = evaluate_components(first_order, ReconciliationConfig(), date(2026, 10, 2))
    shuffled = evaluate_components(shuffled_order, ReconciliationConfig(), date(2026, 10, 2))

    assert [item.model_dump_json() for item in first] == [
        item.model_dump_json() for item in shuffled
    ]
    assert len(first) == 2


def test_scn_06_rejected_receipt_shortfall_is_qty_over_grn() -> None:
    po = _record(1, _document(_po_xml(), DocType.PO), DocType.PO)
    grn = _record(2, _document(_grn_xml(10, 2), DocType.GRN), DocType.GRN)
    invoice = _record(3, _document(_invoice_xml("INV-2201", 10), DocType.INVOICE), DocType.INVOICE)
    snapshot = ReconciliationSnapshot(
        documents=(po, grn, invoice),
        links=(
            _link(10, grn.document_id, po.document_id),
            _link(11, invoice.document_id, po.document_id),
        ),
    )

    result = evaluate(snapshot, ReconciliationConfig(), date(2026, 2, 2))[0]

    assert result.status is CaseStatus.EXCEPTION
    assert ReasonCode.QTY_OVER_GRN in result.reason_codes
    assert ReasonCode.GRN_PENDING not in result.reason_codes
    assert result.lines[0].grn_rejected_cum == Decimal("2")
    assert result.payable_taxable == Decimal("384000")
    assert result.at_risk == Decimal("96000")


def test_opening_balance_seeds_billed_before_and_received_cumulative() -> None:
    po = _record(1, _document(_po_xml(), DocType.PO), DocType.PO)
    invoice = _record(3, _document(_invoice_xml("INV-OPEN", 5), DocType.INVOICE), DocType.INVOICE)
    snapshot = ReconciliationSnapshot(
        documents=(po, invoice),
        links=(_link(11, invoice.document_id, po.document_id),),
        opening_balances=(
            OpeningBalance(
                opening_balance_id=_id(50),
                po_document_id=po.document_id,
                po_line_no=1,
                received_qty=Decimal("10"),
                billed_qty=Decimal("3"),
            ),
        ),
    )

    result = evaluate(snapshot, ReconciliationConfig(), date(2026, 2, 2))[0]

    assert result.lines[0].billed_before == Decimal("3")
    assert result.lines[0].grn_accepted_cum == Decimal("10")
    assert result.lines[0].eligible_qty == Decimal("5")
    assert result.lines[0].balance_after == Decimal("2")
    assert "ledger_completeness" not in {check.check_id for check in result.lines[0].checks}


def test_input_order_and_registry_extension_preserve_existing_results(monkeypatch) -> None:
    po = _record(1, _document(_po_xml(), DocType.PO), DocType.PO)
    grn = _record(2, _document(_grn_xml(10), DocType.GRN), DocType.GRN)
    invoice = _record(3, _document(_invoice_xml("INV-2201", 10), DocType.INVOICE), DocType.INVOICE)
    links = (
        _link(10, grn.document_id, po.document_id),
        _link(11, invoice.document_id, po.document_id),
    )
    snapshot = ReconciliationSnapshot(documents=(po, grn, invoice), links=links)
    shuffled = ReconciliationSnapshot(
        documents=(invoice, grn, po),
        links=tuple(reversed(links)),
    )
    config = ReconciliationConfig()

    first = evaluate(snapshot, config, date(2026, 2, 2))
    second = evaluate(shuffled, config, date(2026, 2, 2))
    manifest = build_run_manifest(snapshot, config, date(2026, 2, 2), _id(99), "0.1.0")
    shuffled_manifest = build_run_manifest(shuffled, config, date(2026, 2, 2), _id(99), "0.1.0")

    assert first == second
    assert manifest.canonical_json() == shuffled_manifest.canonical_json()
    assert manifest.manifest_hash == shuffled_manifest.manifest_hash
    assert len(manifest.documents) == 3
    assert len(manifest.links) == 2
    assert rules_hash(config) == rules_hash(config)
    extended_registry = (*CHECK_REGISTRY, CheckDefinition("s6.test_extension", 1, "S6"))
    monkeypatch.setitem(registry_module._BY_ID, "s6.test_extension", extended_registry[-1])
    assert evaluate(snapshot, config, date(2026, 2, 2)) == first
    assert rules_hash(config, extended_registry) != rules_hash(config)


def test_scn_14_multiple_invoice_lines_use_a_global_po_line_assignment() -> None:
    po_document = _document(
        "<PurchaseOrder><OrderNo>PO-100</OrderNo><OrderDate>20260101</OrderDate>"
        "<Supplier>Acme Supplies</Supplier><TaxableTotal>100</TaxableTotal>"
        "<Line><Description>Widget model A</Description><Qty>5</Qty><Rate>10</Rate>"
        "<Amount>50</Amount></Line><Line><Description>Widget model A</Description>"
        "<Qty>5</Qty><Rate>10</Rate><Amount>50</Amount></Line></PurchaseOrder>",
        DocType.PO,
    )
    invoice_document = _document(
        "<Invoice><InvoiceNo>INV-MULTI-LINE</InvoiceNo><InvoiceDate>20260201</InvoiceDate>"
        "<Supplier>Acme Supplies</Supplier><TaxableTotal>40</TaxableTotal>"
        "<InvoiceTotal>47.2</InvoiceTotal><Line><Description>Widget model A</Description>"
        "<Qty>2</Qty><Rate>10</Rate><Amount>20</Amount></Line>"
        "<Line><Description>Widget model A</Description><Qty>2</Qty><Rate>10</Rate>"
        "<Amount>20</Amount></Line></Invoice>",
        DocType.INVOICE,
    )
    po = _record(1, po_document, DocType.PO)
    invoice = _record(3, invoice_document, DocType.INVOICE)
    snapshot = ReconciliationSnapshot(
        documents=(po, invoice),
        links=(_link(11, invoice.document_id, po.document_id),),
    )

    result = evaluate(snapshot, ReconciliationConfig(), date(2026, 2, 2))[0]

    assert len(result.lines) == 2
    assert {line.po_line_no for line in result.lines} == {1, 2}


def test_scn_11_exact_duplicate_does_not_consume_the_po_ledger_twice() -> None:
    po = _record(1, _document(_po_xml(), DocType.PO), DocType.PO)
    grn = _record(2, _document(_grn_xml(10, accepted=10), DocType.GRN), DocType.GRN)
    first = _record(3, _document(_invoice_xml("INV-2201", 6), DocType.INVOICE), DocType.INVOICE)
    duplicate = _record(4, _document(_invoice_xml("INV-2201", 6), DocType.INVOICE), DocType.INVOICE)
    second = _record(5, _document(_invoice_xml("INV-2202", 4), DocType.INVOICE), DocType.INVOICE)
    snapshot = ReconciliationSnapshot(
        documents=(po, grn, first, duplicate, second),
        links=(
            _link(10, grn.document_id, po.document_id),
            _link(11, first.document_id, po.document_id),
            _link(12, duplicate.document_id, po.document_id),
            _link(13, second.document_id, po.document_id),
        ),
        vendor_pan_by_id={_VENDOR: "ABCDE1234F"},
    )

    results = {
        item.invoice_id: item
        for item in evaluate(snapshot, ReconciliationConfig(), date(2026, 2, 2))
    }

    assert results[first.document_id].status in {
        CaseStatus.MATCHED,
        CaseStatus.MATCHED_FLAGS,
    }
    assert ReasonCode.QTY_OVER_PO not in results[first.document_id].reason_codes
    assert ReasonCode.PO_VALUE_EXCEEDED not in results[first.document_id].reason_codes
    assert results[duplicate.document_id].status is CaseStatus.DUPLICATE
    assert results[duplicate.document_id].duplicate_of_id == first.document_id
    assert results[duplicate.document_id].blocked_value == Decimal("288000")
    assert results[second.document_id].lines[0].billed_before == Decimal("6")
    assert results[second.document_id].lines[0].eligible_qty == Decimal("4")
    # PO and first receipt fall in the same month: no earlier activity is missing.
    assert "s4.ledger_completeness" not in {
        check.check_id for line in results[first.document_id].lines for check in line.checks
    }


def test_ledger_completeness_warns_only_for_a_po_older_than_the_pool() -> None:
    po = _record(
        1,
        _document(
            _po_xml().replace("<OrderDate>20260101</OrderDate>", "<OrderDate>20251120</OrderDate>"),
            DocType.PO,
        ),
        DocType.PO,
    )
    grn = _record(2, _document(_grn_xml(10, accepted=10), DocType.GRN), DocType.GRN)
    invoice = _record(3, _document(_invoice_xml("INV-2201", 6), DocType.INVOICE), DocType.INVOICE)
    snapshot = ReconciliationSnapshot(
        documents=(po, grn, invoice),
        links=(
            _link(10, grn.document_id, po.document_id),
            _link(11, invoice.document_id, po.document_id),
        ),
    )

    result = evaluate(snapshot, ReconciliationConfig(), date(2026, 2, 2))[0]

    assert "s4.ledger_completeness" in {
        check.check_id for line in result.lines for check in line.checks
    }


def test_scn_11_same_irn_is_exact_duplicate_even_when_invoice_numbers_differ() -> None:
    po = _record(1, _document(_po_xml(), DocType.PO), DocType.PO)
    first_xml = _invoice_xml("INV-2201", 4).replace(
        "<Supplier>Acme Supplies</Supplier>",
        "<Supplier>Acme Supplies</Supplier><IRN>IRN-SAME-100</IRN>",
    )
    second_xml = _invoice_xml("INV-2202", 4).replace(
        "<Supplier>Acme Supplies</Supplier>",
        "<Supplier>Acme Supplies</Supplier><IRN>IRN-SAME-100</IRN>",
    )
    first = _record(3, _document(first_xml, DocType.INVOICE), DocType.INVOICE)
    second = _record(4, _document(second_xml, DocType.INVOICE), DocType.INVOICE)
    snapshot = ReconciliationSnapshot(
        documents=(po, first, second),
        links=(
            _link(10, first.document_id, po.document_id),
            _link(11, second.document_id, po.document_id),
        ),
    )

    results = evaluate(snapshot, ReconciliationConfig(), date(2026, 2, 2))

    assert results[0].status in {CaseStatus.PENDING, CaseStatus.MATCHED_FLAGS}
    assert results[1].status is CaseStatus.DUPLICATE
    assert results[1].duplicate_of_id == first.document_id


def test_near_duplicate_is_flagged_for_review_but_not_blocked_as_exact() -> None:
    po = _record(1, _document(_po_xml(), DocType.PO), DocType.PO)
    grn = _record(2, _document(_grn_xml(10, accepted=10), DocType.GRN), DocType.GRN)
    first = _record(3, _document(_invoice_xml("INV-2201", 1), DocType.INVOICE), DocType.INVOICE)
    similar = _record(4, _document(_invoice_xml("INV-2202", 1), DocType.INVOICE), DocType.INVOICE)
    snapshot = ReconciliationSnapshot(
        documents=(po, grn, first, similar),
        links=(
            _link(10, grn.document_id, po.document_id),
            _link(11, first.document_id, po.document_id),
            _link(12, similar.document_id, po.document_id),
        ),
    )

    results = evaluate(
        snapshot,
        ReconciliationConfig(duplicate_suspect_edit_distance=1),
        date(2026, 2, 2),
    )

    assert results[1].status is CaseStatus.NEEDS_REVIEW
    assert ReasonCode.DUP_SUSPECTED in results[1].reason_codes
    assert results[1].lines[0].billed_before == Decimal("1")
    assert any(
        check.check_id == "s1.suspected_duplicate"
        and check.evidence.get("possible_duplicate_of_id") == str(first.document_id)
        for check in results[1].checks
    )


def test_reconciliation_helpers_cover_amount_ledger_rate_and_payment_terms() -> None:
    line_with_rate = DocLine(
        line_no=1,
        quantity=None,
        rate=None,
    )
    grn_without_received = _document(
        "<GRN><GRNNo>GRN-EMPTY</GRNNo><Line><Description>Widget</Description></Line></GRN>",
        DocType.GRN,
    ).lines[0]
    grn_with_rejected = _document(_grn_xml(8, rejected=2), DocType.GRN).lines[0]
    invoice = _document(
        "<Invoice><InvoiceNo>INV-DUE</InvoiceNo><InvoiceDate>20260201</InvoiceDate>"
        "<Supplier>Acme Supplies</Supplier><Line><Description>Widget</Description>"
        "<Qty>3</Qty><Rate>10</Rate></Line></Invoice>",
        DocType.INVOICE,
    )
    po = _document(
        _po_xml().replace("</Supplier>", "</Supplier><PaymentTerms>NET 30 Days</PaymentTerms>"),
        DocType.PO,
    )
    unsupported_terms_po = _document(
        _po_xml().replace("</Supplier>", "</Supplier><PaymentTerms>Due on receipt</PaymentTerms>"),
        DocType.PO,
    )

    assert engine_module._line_amount(line_with_rate) is None
    assert engine_module._line_amount(invoice.lines[0]) == Decimal("30")
    assert engine_module._accepted_grn_qty(grn_without_received) == (
        Decimal("0"),
        Decimal("0"),
        True,
    )
    assert engine_module._accepted_grn_qty(grn_with_rejected) == (Decimal("6"), Decimal("2"), False)
    assert engine_module._invoice_net_rate(invoice.lines[0]) == Decimal("10")
    assert engine_module._payment_due(invoice, po) == date(2026, 3, 3)
    assert engine_module._payment_due(invoice, unsupported_terms_po) is None
    assert _rule(ReconciliationConfig(tolerances={}), "missing", _VENDOR, None).pct == 0


def test_duplicate_suspicion_uses_folded_number_or_complete_line_amounts() -> None:
    first = _record(
        21,
        _document(_invoice_xml("INV-OI-2201", 1, rate=100), DocType.INVOICE),
        DocType.INVOICE,
    )
    folded_number = _record(
        22,
        _document(
            _invoice_xml("INV-01-2201", 1, rate=101).replace("20260201", "20260204"),
            DocType.INVOICE,
        ),
        DocType.INVOICE,
    )
    line_amount_match = _record(
        23,
        _document(
            _invoice_xml("UNRELATED-2204", 1, rate=100).replace("20260201", "20260203"),
            DocType.INVOICE,
        ),
        DocType.INVOICE,
    ).model_copy(update={"vendor_id": UUID(int=501)})
    wrong_total = _record(
        24,
        _document(
            _invoice_xml("INV-01-2201", 1, rate=103).replace("20260201", "20260204"),
            DocType.INVOICE,
        ),
        DocType.INVOICE,
    )
    outside_window = _record(
        25,
        _document(
            _invoice_xml("UNRELATED-2206", 1, rate=100).replace("20260201", "20260210"),
            DocType.INVOICE,
        ),
        DocType.INVOICE,
    )
    snapshot = ReconciliationSnapshot(
        documents=(first, folded_number, line_amount_match, wrong_total, outside_window),
    )

    exact, suspected = engine_module._duplicate_map(
        (first, folded_number, line_amount_match, wrong_total, outside_window),
        snapshot,
        ReconciliationConfig(),
    )

    assert not exact
    assert suspected == {
        folded_number.document_id: first.document_id,
        line_amount_match.document_id: first.document_id,
    }


def test_duplicate_scan_skips_non_invoice_records_and_invoices_without_a_date() -> None:
    po = _record(25, _document(_po_xml(), DocType.PO), DocType.PO)
    no_date = _record(
        26,
        _document(
            "<Invoice><InvoiceNo>INV-NODATE</InvoiceNo><Supplier>Acme Supplies</Supplier>"
            "<TaxableTotal>10</TaxableTotal></Invoice>",
            DocType.INVOICE,
        ),
        DocType.INVOICE,
    )
    snapshot = ReconciliationSnapshot(documents=(po, no_date))

    exact, suspected = engine_module._duplicate_map((po, no_date), snapshot, ReconciliationConfig())

    assert not exact
    assert not suspected


def test_reconciliation_snapshot_rejects_duplicate_and_invalid_references() -> None:
    po = _record(31, _document(_po_xml(), DocType.PO), DocType.PO)
    invoice = _record(
        32, _document(_invoice_xml("INV-VALIDATION", 1), DocType.INVOICE), DocType.INVOICE
    )
    valid_link = _link(33, invoice.document_id, po.document_id)
    valid_opening = OpeningBalance(
        opening_balance_id=_id(34),
        po_document_id=po.document_id,
        po_line_no=1,
        received_qty=1,
        billed_qty=0,
    )

    with pytest.raises(ValueError, match="document IDs must be unique"):
        ReconciliationSnapshot(documents=(po, po))
    with pytest.raises(ValueError, match="links must reference"):
        ReconciliationSnapshot(
            documents=(po, invoice),
            links=(valid_link.model_copy(update={"to_document_id": _id(999)}),),
        )
    with pytest.raises(ValueError, match="connect an invoice or GRN to a PO"):
        ReconciliationSnapshot(
            documents=(po, invoice),
            links=(valid_link.model_copy(update={"from_document_id": po.document_id}),),
        )
    with pytest.raises(ValueError, match="opening balances must reference"):
        ReconciliationSnapshot(
            documents=(po, invoice),
            opening_balances=(valid_opening.model_copy(update={"po_document_id": _id(998)}),),
        )
    with pytest.raises(ValueError, match="link IDs must be unique"):
        ReconciliationSnapshot(
            documents=(po, invoice),
            links=(
                valid_link,
                valid_link.model_copy(
                    update={
                        "from_document_id": invoice.document_id,
                        "to_document_id": po.document_id,
                    }
                ),
            ),
        )
    with pytest.raises(ValueError, match="opening balance IDs must be unique"):
        ReconciliationSnapshot(
            documents=(po,),
            opening_balances=(
                valid_opening,
                valid_opening.model_copy(
                    update={"po_document_id": po.document_id, "po_line_no": 2}
                ),
            ),
        )
    with pytest.raises(ValueError, match="rejected IDs must reference invoices"):
        ReconciliationSnapshot(documents=(po, invoice), rejected_invoice_ids=(_id(997),))


def test_scn_07_rate_increase_reports_po_rate_and_hold_modes() -> None:
    po = _record(1, _document(_po_xml(), DocType.PO), DocType.PO)
    grn = _record(2, _document(_grn_xml(10, accepted=10), DocType.GRN), DocType.GRN)
    invoice = _record(
        3, _document(_invoice_xml("INV-RATE", 10, rate=49000), DocType.INVOICE), DocType.INVOICE
    )
    snapshot = ReconciliationSnapshot(
        documents=(po, grn, invoice),
        links=(
            _link(10, grn.document_id, po.document_id),
            _link(11, invoice.document_id, po.document_id),
        ),
    )

    pay_at_po_rate = evaluate(snapshot, ReconciliationConfig(), date(2026, 2, 2))[0]
    hold = evaluate(
        snapshot,
        ReconciliationConfig(payable_rate_mode="hold"),
        date(2026, 2, 2),
    )[0]

    assert ReasonCode.RATE_HIGH in pay_at_po_rate.reason_codes
    assert pay_at_po_rate.payable_taxable == Decimal("480000")
    assert pay_at_po_rate.at_risk == Decimal("10000")
    assert pay_at_po_rate.at_risk_gst == Decimal("1800")
    assert hold.payable_taxable == Decimal("0")
    assert hold.at_risk == Decimal("490000")
    assert hold.at_risk_gst == Decimal("88200")


def test_unknown_po_status_and_unresolved_vendor_cannot_be_silently_matched() -> None:
    po_doc = _document(_po_xml().replace("<Status>open</Status>", ""), DocType.PO)
    po = _record(1, po_doc, DocType.PO)
    grn = _record(2, _document(_grn_xml(10, accepted=10), DocType.GRN), DocType.GRN)
    invoice = _record(
        3, _document(_invoice_xml("INV-UNKNOWN", 10), DocType.INVOICE), DocType.INVOICE
    ).model_copy(update={"vendor_id": None})
    snapshot = ReconciliationSnapshot(
        documents=(po, grn, invoice),
        links=(
            _link(10, grn.document_id, po.document_id),
            _link(11, invoice.document_id, po.document_id),
        ),
    )

    result = evaluate(snapshot, ReconciliationConfig(), date(2026, 2, 2))[0]

    assert result.status is CaseStatus.NEEDS_REVIEW
    assert {check.check_id for check in result.checks} >= {"s1.po_open", "s1.vendor_resolved"}

    closed_po = _record(
        10,
        _document(
            _po_xml().replace("<Status>open</Status>", "<Status>closed</Status>"), DocType.PO
        ),
        DocType.PO,
    )
    closed_invoice = _record(
        12,
        _document(_invoice_xml("INV-CLOSED-PO", 10), DocType.INVOICE),
        DocType.INVOICE,
    )
    closed_snapshot = ReconciliationSnapshot(
        documents=(closed_po, closed_invoice),
        links=(_link(14, closed_invoice.document_id, closed_po.document_id),),
    )
    closed_result = evaluate(closed_snapshot, ReconciliationConfig(), date(2026, 2, 2))[0]
    assert closed_result.status is CaseStatus.EXCEPTION
    assert ReasonCode.PO_NOT_OPEN in closed_result.reason_codes


def test_scn_08_unaligned_item_and_unconfirmed_grn_link_are_not_silent_passes() -> None:
    po_doc = _document(
        "<PurchaseOrder><OrderNo>PO-100</OrderNo><OrderDate>20260101</OrderDate>"
        "<Status>open</Status><Supplier>Acme Supplies</Supplier><TaxableTotal>480000</TaxableTotal>"
        "<Line><Description>Completely different product</Description><Qty>10</Qty>"
        "<Rate>48000</Rate><Amount>480000</Amount></Line></PurchaseOrder>",
        DocType.PO,
    )
    po = _record(1, po_doc, DocType.PO)
    grn = _record(2, _document(_grn_xml(10, accepted=10), DocType.GRN), DocType.GRN)
    invoice = _record(
        3, _document(_invoice_xml("INV-UNSURE", 10), DocType.INVOICE), DocType.INVOICE
    )
    grn_link = _link(10, grn.document_id, po.document_id).model_copy(
        update={"state": LinkState.SUGGESTED, "confidence": Decimal("0.90")}
    )
    snapshot = ReconciliationSnapshot(
        documents=(po, grn, invoice),
        links=(grn_link, _link(11, invoice.document_id, po.document_id)),
    )

    result = evaluate(snapshot, ReconciliationConfig(), date(2026, 2, 2))[0]

    assert result.status in {CaseStatus.NEEDS_REVIEW, CaseStatus.EXCEPTION}
    assert ReasonCode.ITEM_NOT_ON_PO in result.reason_codes
    assert any(
        check.check_id == "s2.link_confidence" and check.evidence.get("document_type") == "grn"
        for check in result.checks
    )


def test_scn_14_one_invoice_can_link_lines_to_two_pos_and_uses_worst_line() -> None:
    po_one_xml = _po_xml().replace("PO-100", "PO-200").replace("20260101", "20260115")
    po_one_xml = po_one_xml.replace("<Qty>10</Qty>", "<Qty>5</Qty>")
    po_one_xml = po_one_xml.replace("<Rate>48000</Rate>", "<Rate>10</Rate>")
    po_one_xml = po_one_xml.replace(
        "<TaxableTotal>480000</TaxableTotal>", "<TaxableTotal>50</TaxableTotal>"
    )
    po_one_xml = po_one_xml.replace("<Amount>480000</Amount>", "<Amount>50</Amount>")
    po_two_xml = _po_xml().replace("PO-100", "PO-300").replace("20260101", "20260115")
    po_two_xml = po_two_xml.replace("Widget model A", "Widget model B")
    po_two_xml = po_two_xml.replace("<Qty>10</Qty>", "<Qty>5</Qty>")
    po_two_xml = po_two_xml.replace("<Rate>48000</Rate>", "<Rate>10</Rate>")
    po_two_xml = po_two_xml.replace(
        "<TaxableTotal>480000</TaxableTotal>", "<TaxableTotal>50</TaxableTotal>"
    )
    po_two_xml = po_two_xml.replace("<Amount>480000</Amount>", "<Amount>50</Amount>")
    po_one = _record(1, _document(po_one_xml, DocType.PO), DocType.PO)
    po_two = _record(2, _document(po_two_xml, DocType.PO), DocType.PO)
    grn_one = _record(
        3,
        _document(_grn_xml(5, accepted=5), DocType.GRN),
        DocType.GRN,
    )
    grn_two_xml = _grn_xml(6, accepted=6).replace("Widget model A", "Widget model B")
    grn_two = _record(4, _document(grn_two_xml, DocType.GRN), DocType.GRN)
    invoice_xml = (
        "<Invoice><InvoiceNo>INV-TWO-PO</InvoiceNo><InvoiceDate>20260201</InvoiceDate>"
        "<Supplier>Acme Supplies</Supplier><TaxableTotal>110</TaxableTotal><IGST>19.8</IGST>"
        "<InvoiceTotal>129.8</InvoiceTotal><Line><Description>Widget model A</Description>"
        "<Qty>5</Qty><Rate>10</Rate><Amount>50</Amount></Line>"
        "<Line><Description>Widget model B</Description><Qty>6</Qty><Rate>10</Rate>"
        "<Amount>60</Amount></Line></Invoice>"
    )
    invoice = _record(5, _document(invoice_xml, DocType.INVOICE), DocType.INVOICE)
    links = (
        _link(10, grn_one.document_id, po_one.document_id),
        _link(11, grn_two.document_id, po_two.document_id),
        ReconciliationLink(
            link_id=_id(12),
            from_document_id=invoice.document_id,
            to_document_id=po_one.document_id,
            method=LinkMethod.LABELED,
            state=LinkState.AUTO,
            confidence=Decimal("0.99"),
            level=LinkLevel.LINE,
            line_no=1,
        ),
        ReconciliationLink(
            link_id=_id(13),
            from_document_id=invoice.document_id,
            to_document_id=po_two.document_id,
            method=LinkMethod.LABELED,
            state=LinkState.AUTO,
            confidence=Decimal("0.99"),
            level=LinkLevel.LINE,
            line_no=2,
        ),
    )
    snapshot = ReconciliationSnapshot(
        documents=(po_one, po_two, grn_one, grn_two, invoice),
        links=links,
    )

    result = evaluate(snapshot, ReconciliationConfig(), date(2026, 2, 2))[0]

    assert result.po_nos == ("PO-200", "PO-300")
    assert len(result.lines) == 2
    assert result.lines[0].status is CaseStatus.MATCHED
    assert result.lines[1].status is CaseStatus.EXCEPTION
    assert result.status is CaseStatus.EXCEPTION
    assert ReasonCode.QTY_OVER_PO in result.reason_codes


def test_scn_15_intra_state_igst_is_flagged() -> None:
    po_xml = _po_xml().replace("20260101", "20260115")
    po = _record(1, _document(po_xml, DocType.PO), DocType.PO)
    grn = _record(2, _document(_grn_xml(10, accepted=10), DocType.GRN), DocType.GRN)
    invoice_xml = _invoice_xml("INV-TAX-TYPE", 10).replace(
        "<Supplier>Acme Supplies</Supplier>",
        "<Supplier>Acme Supplies</Supplier><GSTIN>27AAPFU0939F1ZV</GSTIN>"
        "<BuyerGSTIN>27AAPFU0939F1ZV</BuyerGSTIN>",
    )
    invoice = _record(3, _document(invoice_xml, DocType.INVOICE), DocType.INVOICE)
    snapshot = ReconciliationSnapshot(
        documents=(po, grn, invoice),
        links=(
            _link(10, grn.document_id, po.document_id),
            _link(11, invoice.document_id, po.document_id),
        ),
    )

    result = evaluate(snapshot, ReconciliationConfig(), date(2026, 2, 2))[0]

    assert result.status is CaseStatus.MATCHED_FLAGS
    assert ReasonCode.TAX_TYPE_MISMATCH in result.reason_codes


def test_s6_compliance_checks_compare_dates_currency_item_fields_and_tax() -> None:
    po_xml = _po_xml().replace("20260101", "20260215")
    po_xml = po_xml.replace(
        "<Supplier>Acme Supplies</Supplier>",
        "<Supplier>Acme Supplies</Supplier><Currency>INR</Currency>",
    )
    po_xml = po_xml.replace(
        "<Status>open</Status>",
        "<Status>open</Status><PaymentTerms>Net 30 Days</PaymentTerms>",
    )
    po_xml = po_xml.replace(
        "<TaxableTotal>480000</TaxableTotal>",
        "<TaxableTotal>480000</TaxableTotal><DeliveryDate>20260110</DeliveryDate>",
    )
    po_xml = po_xml.replace(
        "<Description>Widget model A</Description>",
        "<Description>Widget model A</Description><HSN>1234</HSN>"
        "<UOM>PCS</UOM><TaxRate>18</TaxRate>",
    )
    po = _record(1, _document(po_xml, DocType.PO), DocType.PO)
    grn = _record(2, _document(_grn_xml(10, accepted=10), DocType.GRN), DocType.GRN)
    invoice_xml = _invoice_xml("INV-COMPLIANCE", 10)
    invoice_xml = invoice_xml.replace("</InvoiceDate>", "</InvoiceDate><Currency>USD</Currency>")
    invoice_xml = invoice_xml.replace(
        "<Description>Widget model A</Description>",
        "<Description>Widget model A</Description><HSN>5678</HSN>"
        "<UOM>KG</UOM><TaxRate>12</TaxRate>",
    )
    invoice = _record(3, _document(invoice_xml, DocType.INVOICE), DocType.INVOICE)
    snapshot = ReconciliationSnapshot(
        documents=(po, grn, invoice),
        links=(
            _link(10, grn.document_id, po.document_id),
            _link(11, invoice.document_id, po.document_id),
        ),
    )

    result = evaluate(snapshot, ReconciliationConfig(), date(2026, 2, 2))[0]

    assert result.status in {CaseStatus.NEEDS_REVIEW, CaseStatus.EXCEPTION}
    assert {
        ReasonCode.DATE_ORDER,
        ReasonCode.CURRENCY_DIFF,
        ReasonCode.HSN_DIFF,
        ReasonCode.UOM_MISMATCH,
        ReasonCode.TAX_RATE_DIFF,
        ReasonCode.MATH_ERROR,
        ReasonCode.LATE_DELIVERY,
    }.issubset(set(result.reason_codes))
    assert result.payment_due_date == date(2026, 3, 3)


def test_scn_16_unapproved_freight_is_flagged_and_excluded_from_payable() -> None:
    po_xml = _po_xml().replace("20260101", "20260115")
    po = _record(1, _document(po_xml, DocType.PO), DocType.PO)
    grn = _record(2, _document(_grn_xml(10, accepted=10), DocType.GRN), DocType.GRN)
    invoice_xml = _invoice_xml("INV-FREIGHT", 10)
    invoice_xml = invoice_xml.replace(
        "<TaxableTotal>480000</TaxableTotal>", "<TaxableTotal>481000</TaxableTotal>"
    )
    invoice_xml = invoice_xml.replace("<IGST>86400</IGST>", "<IGST>86580</IGST>")
    invoice_xml = invoice_xml.replace(
        "<InvoiceTotal>566400</InvoiceTotal>", "<InvoiceTotal>567580</InvoiceTotal>"
    )
    invoice_xml = invoice_xml.replace(
        "</Line></Invoice>",
        "</Line><Line><Description>Freight</Description><Qty>1</Qty><Rate>1000</Rate>"
        "<Amount>1000</Amount></Line></Invoice>",
    )
    invoice = _record(3, _document(invoice_xml, DocType.INVOICE), DocType.INVOICE)
    snapshot = ReconciliationSnapshot(
        documents=(po, grn, invoice),
        links=(
            _link(10, grn.document_id, po.document_id),
            _link(11, invoice.document_id, po.document_id),
        ),
    )

    result = evaluate(snapshot, ReconciliationConfig(), date(2026, 2, 2))[0]

    assert result.status is CaseStatus.MATCHED_FLAGS
    assert ReasonCode.EXTRA_CHARGE in result.reason_codes
    assert result.payable_taxable == Decimal("480000")
    assert result.at_risk == Decimal("1000")


def test_tolerance_boundaries_follow_or_and_both_modes() -> None:
    po = _record(1, _document(_po_xml().replace("20260101", "20260115"), DocType.PO), DocType.PO)
    grn = _record(2, _document(_grn_xml(10, accepted=10), DocType.GRN), DocType.GRN)
    config_or = ReconciliationConfig()
    config_both = ReconciliationConfig(limit_mode="both")

    def run(rate: int | Decimal, config: ReconciliationConfig):
        invoice = _record(
            3,
            _document(_invoice_xml("INV-TOLERANCE", 10, rate=rate), DocType.INVOICE),
            DocType.INVOICE,
        )
        snapshot = ReconciliationSnapshot(
            documents=(po, grn, invoice),
            links=(
                _link(10, grn.document_id, po.document_id),
                _link(11, invoice.document_id, po.document_id),
            ),
        )
        return evaluate(snapshot, config, date(2026, 2, 2))[0]

    plus_one = run(48001, config_both)
    minus_one = run(47999, config_both)
    half_percent_or = run(48240, config_or)
    half_percent_both = run(48240, config_both)
    one_percent_or = run(48480, config_or)
    one_percent_both = run(48480, config_both)
    three_tenths_percent = run(48144, config_or)

    def check(result, check_id: str):
        return next(item for item in result.checks if item.check_id == check_id)

    assert check(plus_one, "s5.unit_rate").result.value == "pass"
    assert check(minus_one, "s5.unit_rate").result.value == "pass"
    assert check(plus_one, "s5.total_vs_expected").result.value == "pass"
    assert check(minus_one, "s5.total_vs_expected").result.value == "pass"
    assert check(half_percent_or, "s5.unit_rate").result.value == "pass"
    assert check(half_percent_both, "s5.unit_rate").result.value == "fail"
    assert check(one_percent_or, "s5.total_vs_expected").result.value == "pass"
    assert check(one_percent_both, "s5.total_vs_expected").result.value == "fail"
    assert check(three_tenths_percent, "s5.unit_rate").result.value == "pass"
    assert three_tenths_percent.status is CaseStatus.MATCHED


def test_tolerances_choose_item_then_vendor_then_global_and_zero_expected_is_safe() -> None:
    rule = ToleranceRule(
        pct=5,
        abs=10,
        by_vendor={str(_VENDOR): ToleranceValues(pct=1, abs=5)},
        by_item={"widget model a": ToleranceValues(pct=0, abs=1)},
    )
    item = _effective_tolerance(rule, ToleranceMode.OR, _VENDOR, "widget model a")
    vendor = _effective_tolerance(rule, ToleranceMode.OR, _VENDOR, "other item")
    global_only = _effective_tolerance(rule, ToleranceMode.OR, None, "other item")

    assert item.source is ToleranceSource.ITEM
    assert item.pct == Decimal("0") and item.abs == Decimal("1")
    assert vendor.source is ToleranceSource.VENDOR
    assert vendor.pct == Decimal("1") and vendor.abs == Decimal("5")
    assert global_only.source is ToleranceSource.GLOBAL
    assert global_only.pct == Decimal("5") and global_only.abs == Decimal("10")

    zero_expected = Tolerance(
        pct=100,
        abs=0,
        mode=ToleranceMode.OR,
        source=ToleranceSource.GLOBAL,
    )
    assert not _tolerance_pass(Decimal("1"), Decimal("0"), zero_expected)


def test_vendor_scoped_item_override_wins_over_item_vendor_and_global() -> None:
    other_vendor = UUID("00000000-0000-0000-0000-0000000000ff")
    rule = ToleranceRule(
        pct=5,
        abs=10,
        by_vendor={str(_VENDOR): ToleranceValues(pct=1, abs=5)},
        by_item={"widget model a": ToleranceValues(pct=0, abs=1)},
        by_vendor_item={f"{_VENDOR}|widget model a": ToleranceValues(pct=2, abs=3)},
    )
    scoped = _effective_tolerance(rule, ToleranceMode.OR, _VENDOR, "widget model a")
    other = _effective_tolerance(rule, ToleranceMode.OR, other_vendor, "widget model a")

    assert scoped.source is ToleranceSource.ITEM
    assert scoped.pct == Decimal("2") and scoped.abs == Decimal("3")
    assert other.source is ToleranceSource.ITEM
    assert other.pct == Decimal("0") and other.abs == Decimal("1")


def test_s4_quantity_ledger_properties_never_overpay_or_recover_after_more_billing() -> None:
    @settings(max_examples=30, deadline=None)
    @given(
        ordered=st.integers(min_value=1, max_value=25),
        received=st.integers(min_value=0, max_value=30),
        billed=st.integers(min_value=1, max_value=35),
    )
    def verify(ordered: int, received: int, billed: int) -> None:
        po_xml = _po_xml().replace("20260101", "20260115")
        po_xml = po_xml.replace("<Qty>10</Qty>", f"<Qty>{ordered}</Qty>")
        po_xml = po_xml.replace(
            "<TaxableTotal>480000</TaxableTotal>", f"<TaxableTotal>{ordered * 10}</TaxableTotal>"
        )
        po_xml = po_xml.replace("<Rate>48000</Rate>", "<Rate>10</Rate>")
        po_xml = po_xml.replace("<Amount>480000</Amount>", f"<Amount>{ordered * 10}</Amount>")
        po = _record(1, _document(po_xml, DocType.PO), DocType.PO)
        grn = _record(
            2,
            _document(_grn_xml(received, accepted=received), DocType.GRN),
            DocType.GRN,
        )
        invoice = _record(
            3,
            _document(_invoice_xml("INV-PROPERTY", billed, rate=10), DocType.INVOICE),
            DocType.INVOICE,
        )
        snapshot = ReconciliationSnapshot(
            documents=(po, grn, invoice),
            links=(
                _link(10, grn.document_id, po.document_id),
                _link(11, invoice.document_id, po.document_id),
            ),
        )
        result = evaluate(snapshot, ReconciliationConfig(), date(2026, 2, 2))[0]
        line = result.lines[0]
        assert line.eligible_qty is not None
        assert (
            Decimal("0")
            <= line.eligible_qty
            <= min(Decimal(billed), Decimal(ordered), Decimal(received))
        )
        assert line.balance_after is not None and line.balance_after >= Decimal("0")
        assert not _contains_float(result.model_dump(mode="python"))

        if ReasonCode.QTY_OVER_PO in result.reason_codes:
            larger_invoice = _record(
                4,
                _document(
                    _invoice_xml("INV-PROPERTY-LARGER", billed + 1, rate=10), DocType.INVOICE
                ),
                DocType.INVOICE,
            )
            larger_snapshot = ReconciliationSnapshot(
                documents=(po, grn, larger_invoice),
                links=(
                    _link(10, grn.document_id, po.document_id),
                    _link(12, larger_invoice.document_id, po.document_id),
                ),
            )
            larger_result = evaluate(larger_snapshot, ReconciliationConfig(), date(2026, 2, 2))[0]
            assert ReasonCode.QTY_OVER_PO in larger_result.reason_codes

    verify()


def test_independent_components_evaluate_to_same_results_in_multiple_processes() -> None:
    po_one = _record(1, _document(_po_xml(), DocType.PO), DocType.PO)
    grn_one = _record(2, _document(_grn_xml(10, accepted=10), DocType.GRN), DocType.GRN)
    invoice_one = _record(
        3, _document(_invoice_xml("INV-APPLE", 10), DocType.INVOICE), DocType.INVOICE
    )
    po_two = _record(11, _document(_po_xml(), DocType.PO), DocType.PO)
    grn_two = _record(12, _document(_grn_xml(10, accepted=10), DocType.GRN), DocType.GRN)
    invoice_two = _record(
        13, _document(_invoice_xml("INV-ZEBRA", 9), DocType.INVOICE), DocType.INVOICE
    )
    snapshot = ReconciliationSnapshot(
        documents=(po_two, invoice_one, grn_two, po_one, invoice_two, grn_one),
        links=(
            _link(20, grn_one.document_id, po_one.document_id),
            _link(21, invoice_one.document_id, po_one.document_id),
            _link(22, grn_two.document_id, po_two.document_id),
            _link(23, invoice_two.document_id, po_two.document_id),
        ),
    )
    config = ReconciliationConfig()
    run_date = date(2026, 2, 2)
    components = partition_components(snapshot, config)
    serial = evaluate_components(snapshot, config, run_date)

    with ProcessPoolExecutor(max_workers=2) as executor:
        parallel = tuple(
            item
            for group in executor.map(
                _evaluate_worker,
                (
                    (
                        component.model_dump_json(exclude_computed_fields=True),
                        config.model_dump_json(),
                        run_date.isoformat(),
                    )
                    for component in components
                ),
            )
            for item in group
        )

    assert len(components) == 2
    assert serial == evaluate(snapshot, config, run_date)
    assert parallel == tuple(item.model_dump_json() for item in serial)


def test_duplicate_invoices_on_separate_pos_are_joined_into_one_component() -> None:
    po_one = _record(1, _document(_po_xml(), DocType.PO), DocType.PO)
    po_two = _record(11, _document(_po_xml().replace("PO-100", "PO-200"), DocType.PO), DocType.PO)
    first = _record(3, _document(_invoice_xml("INV-SAME", 2), DocType.INVOICE), DocType.INVOICE)
    second = _record(13, _document(_invoice_xml("INV-SAME", 2), DocType.INVOICE), DocType.INVOICE)
    snapshot = ReconciliationSnapshot(
        documents=(po_one, first, po_two, second),
        links=(
            _link(20, first.document_id, po_one.document_id),
            _link(21, second.document_id, po_two.document_id),
        ),
        vendor_pan_by_id={_VENDOR: "ABCDE1234F"},
    )

    components = partition_components(snapshot, ReconciliationConfig())
    results = evaluate_components(snapshot, ReconciliationConfig(), date(2026, 2, 2))

    assert len(components) == 1
    assert results[1].status is CaseStatus.DUPLICATE


def test_invoice_from_an_unconfirmed_page_split_is_never_matched() -> None:
    po = _record(1, _document(_po_xml().replace("20260101", "20260115"), DocType.PO), DocType.PO)
    grn = _record(2, _document(_grn_xml(10, accepted=10), DocType.GRN), DocType.GRN)
    invoice_doc = _document(_invoice_xml("INV-2201", 10), DocType.INVOICE)
    marker = {"pages": [3], "group": 3, "groups": 3, "state": "unconfirmed"}
    header = invoice_doc.header.model_copy(update={"extras": {"page_split": marker}})
    invoice = _record(3, invoice_doc.model_copy(update={"header": header}), DocType.INVOICE)
    snapshot = ReconciliationSnapshot(
        documents=(po, grn, invoice),
        links=(
            _link(10, grn.document_id, po.document_id),
            _link(11, invoice.document_id, po.document_id),
        ),
    )

    result = evaluate(snapshot, ReconciliationConfig(), date(2026, 2, 2))[0]

    assert result.status not in {CaseStatus.MATCHED, CaseStatus.MATCHED_FLAGS}
    assert ReasonCode.LOW_CONFIDENCE_DATA in result.reason_codes
