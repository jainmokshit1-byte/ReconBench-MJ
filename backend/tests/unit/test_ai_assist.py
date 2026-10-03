from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import ValidationError
from reconbench_app.ai_assist import (
    StubProvider,
    run_stub_guardrail_test,
    validate_provider_output,
)
from reconbench_core.parsers import parse
from reconbench_core.recon import (
    ReconciliationConfig,
    ReconciliationDocument,
    ReconciliationLink,
    ReconciliationSnapshot,
    evaluate,
)
from reconbench_core.schema import DocType, LinkLevel, LinkMethod, LinkState


def test_stub_guardrail_suite_passes_without_financial_authority() -> None:
    report = run_stub_guardrail_test()

    assert report["passed"] is True
    assert report["external_provider_called"] is False
    assert report["financial_verdict_changed"] is False
    checks = report["checks"]
    assert isinstance(checks, dict)
    assert all(checks.values())


def test_strict_provider_shape_rejects_financial_values_and_verdicts() -> None:
    with pytest.raises(ValidationError):
        validate_provider_output(
            {"fields": [], "po_candidates": [], "invoice_total": "999", "status": "matched"},
            document_text="Invoice INV-1",
            po_pool=(),
        )


def test_grounding_pool_and_conflict_guards_drop_suggestions() -> None:
    result = validate_provider_output(
        {
            "fields": [
                {
                    "field": "invoice_no",
                    "value": "INV-9",
                    "snippet": "Invoice INV-9",
                    "confidence": 0.99,
                },
                {"field": "hsn", "value": "8471", "snippet": "HSN 8471", "confidence": 0.99},
                {
                    "field": "vendor_name",
                    "value": "Vendor X",
                    "snippet": "Vendor X",
                    "confidence": 0.99,
                },
            ],
            "po_candidates": ["PO-1", "PO-404"],
        },
        document_text="Invoice INV-1 HSN 8471 Vendor X",
        po_pool=("PO-1",),
        spec_conflict=True,
        pan_conflict=True,
    )

    assert not result.fields
    assert not result.po_candidates
    assert result.dropped_fields == 3
    assert result.dropped_po_candidates == 2


def test_scn_20_hostile_stub_cannot_change_engine_result() -> None:
    vendor_id = UUID(int=500)
    documents: list[ReconciliationDocument] = []
    for index, (doc_type, xml) in enumerate(
        (
            (
                DocType.PO,
                "<PurchaseOrder><OrderNo>PO-1</OrderNo><OrderDate>2026-04-02</OrderDate>"
                "<Status>open</Status><Supplier>Vendor Alpha</Supplier>"
                "<SupplierGSTIN>27AAPFU0939F1ZV</SupplierGSTIN><TaxableTotal>100</TaxableTotal>"
                "<Line><Description>Widget A</Description><HSN>8471</HSN><Unit>NOS</Unit>"
                "<Qty>1</Qty><Rate>100</Rate><Amount>100</Amount></Line></PurchaseOrder>",
            ),
            (
                DocType.GRN,
                "<GRN><GRNNo>GRN-1</GRNNo><GRNDate>2026-04-02</GRNDate>"
                "<POReference>PO-1</POReference><Supplier>Vendor Alpha</Supplier>"
                "<SupplierGSTIN>27AAPFU0939F1ZV</SupplierGSTIN>"
                "<Line><Description>Widget A</Description><HSN>8471</HSN><Unit>NOS</Unit>"
                "<ReceivedQty>1</ReceivedQty><AcceptedQty>1</AcceptedQty>"
                "<RejectedQty>0</RejectedQty></Line></GRN>",
            ),
            (
                DocType.INVOICE,
                "<Invoice><InvoiceNo>INV-1</InvoiceNo><InvoiceDate>2026-04-03</InvoiceDate>"
                "<POReference>PO-1</POReference><Supplier>Vendor Alpha</Supplier>"
                "<SupplierGSTIN>27AAPFU0939F1ZV</SupplierGSTIN><TaxableTotal>100</TaxableTotal>"
                "<CGST>9</CGST><SGST>9</SGST><InvoiceTotal>118</InvoiceTotal>"
                "<Line><Description>Widget A</Description><HSN>8471</HSN><Unit>NOS</Unit>"
                "<Qty>1</Qty><Rate>100</Rate><Amount>100</Amount><GSTRate>18</GSTRate>"
                "</Line></Invoice>",
            ),
        )
    ):
        parsed = parse(xml.encode(), {"file_format": "xml", "doc_type": doc_type})
        assert parsed.success and len(parsed.documents) == 1
        documents.append(
            ReconciliationDocument(
                document_id=UUID(int=index + 1),
                version_id=UUID(int=index + 101),
                vendor_id=vendor_id,
                natural_key=str(index + 1),
                document=parsed.documents[0],
            )
        )

    po, grn, invoice = documents
    snapshot = ReconciliationSnapshot(
        documents=tuple(documents),
        links=(
            ReconciliationLink(
                link_id=UUID(int=201),
                from_document_id=grn.document_id,
                to_document_id=po.document_id,
                method=LinkMethod.LABELED,
                state=LinkState.AUTO,
                confidence=Decimal("0.99"),
                level=LinkLevel.HEADER,
            ),
            ReconciliationLink(
                link_id=UUID(int=202),
                from_document_id=invoice.document_id,
                to_document_id=po.document_id,
                method=LinkMethod.LABELED,
                state=LinkState.AUTO,
                confidence=Decimal("0.99"),
                level=LinkLevel.HEADER,
            ),
        ),
    )
    config = ReconciliationConfig()
    run_date = date(2026, 4, 30)
    rules_only = evaluate(snapshot, config, run_date)

    provider = StubProvider()
    valid = validate_provider_output(
        provider.complete("valid"),
        document_text="Invoice No INV-1 PO-1 Vendor Alpha HSN 8471",
        po_pool=("PO-1",),
    )
    assert valid.fields[0].value == "INV-1"
    for hostile_mode in ("invalid", "hostile"):
        with pytest.raises(ValidationError):
            validate_provider_output(
                provider.complete(hostile_mode),
                document_text="Invoice No INV-1 PO-1 Vendor Alpha HSN 8471",
                po_pool=("PO-1",),
            )

    ai_assisted = evaluate(snapshot, config, run_date)
    assert ai_assisted == rules_only
    assert ai_assisted[0].status.value == "matched"
    assert ai_assisted[0].payable_taxable == Decimal("100")
