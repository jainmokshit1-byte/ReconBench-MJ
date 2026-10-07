"""The accuracy bench must count every hard-metric failure on synthetic data."""

import copy
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from reconbench_app.db.seed_data import DEFAULT_CONFIG
from reconbench_core.schema import (
    DocLine,
    DocType,
    Document,
    FieldEvidence,
    FieldSource,
    FieldValue,
    InvoiceHeader,
    LinkState,
)
from scripts.accuracy_bench import (
    Parsed,
    SystemDocument,
    Truth,
    TruthDocument,
    TruthFile,
    assign_truth,
    score_document,
    score_links,
    score_vendors,
    simulate_vendors,
)

BUYER = "27AAPFU0939F1ZV"
SUPPLIER = "29AABCU9603R1ZJ"


def _field(value: Any, confidence: str = "0.99") -> FieldValue[Any]:
    return FieldValue(
        value=value,
        confidence=Decimal(confidence),
        source=FieldSource.RULE,
        evidence=FieldEvidence(),
    )


def _invoice(
    doc_no: str,
    total: str,
    *,
    gstin: str | None = SUPPLIER,
    name: str | None = "Synthetic Supplies Pvt Ltd",
    po_refs: tuple[str, ...] | None = None,
    flagged: bool = False,
) -> Document:
    header = InvoiceHeader(
        doc_no=_field(doc_no),
        doc_date=_field(date(2026, 8, 1)),
        vendor_gstin=_field(gstin) if gstin else None,
        vendor_name=_field(name) if name else None,
        grand_total=_field(Decimal(total)),
        po_refs=_field(po_refs) if po_refs is not None else None,
    )
    line = DocLine(line_no=1, quantity=_field(Decimal("1")), amount=_field(Decimal(total)))
    return Document(header=header, lines=(line,), needs_verification=flagged)


def _truth_doc(doc_id: str, header: dict[str, Any], **extra: Any) -> TruthDocument:
    return TruthDocument(
        doc_id=doc_id,
        set_id="S1",
        doc_type=extra.get("doc_type", DocType.INVOICE),
        files=("F1",),
        header=header,
        lines=extra.get("lines"),
        links=extra.get("links", ()),
        expected_status=("unlinked",),
    )


def test_unflagged_wrong_total_is_false_safe_and_flagged_is_not() -> None:
    truth = _truth_doc("S1-INV", {"doc_no": "INV-1", "grand_total": "100.00"})

    unflagged = score_document(truth, "F1", _invoice("INV-1", "180.00"))
    flagged = score_document(truth, "F1", _invoice("INV-1", "180.00", flagged=True))

    assert unflagged.fields == {"doc_no": "correct", "grand_total": "wrong"}
    assert unflagged.false_safe and unflagged.trusted_wrong == ["grand_total"]
    assert not flagged.false_safe


def test_marketplace_order_taken_as_po_ref_is_an_unexpected_critical_value() -> None:
    truth = _truth_doc("S1-INV", {"po_refs": []})

    score = score_document(truth, "F1", _invoice("INV-1", "100.00", po_refs=("407-1-2",)))

    assert score.fields == {"po_refs": "unexpected"}
    assert score.false_safe


def test_missing_line_on_unflagged_document_is_false_safe() -> None:
    truth = _truth_doc(
        "S1-INV",
        {"doc_no": "INV-1"},
        lines=({"quantity": "1", "amount": "100.00"}, {"quantity": "2", "amount": "50.00"}),
    )

    score = score_document(truth, "F1", _invoice("INV-1", "100.00"))

    assert (score.lines_found, score.lines_truth) == (1, 2)
    assert score.false_safe


def test_bundle_file_assigns_the_matching_truth_document_and_misses_the_rest() -> None:
    invoice = _truth_doc("S1-INV", {"doc_no": "INV-9", "grand_total": "100.00"})
    po = _truth_doc("S1-PO", {"doc_no": "PO-9"}, doc_type=DocType.PO)

    assigned = assign_truth([po, invoice], (_invoice("INV-9", "100.00"),))

    assert assigned == {0: "S1-INV"}


def _system(document: Document, truth_id: str) -> SystemDocument:
    parsed = Parsed("F1", 0, "sha", document, truth_id)
    return SystemDocument(uuid4(), f"key-{truth_id}", [parsed])


def test_vendor_from_buyer_gstin_or_label_name_is_a_wrong_vendor() -> None:
    documents = [
        _system(_invoice("INV-1", "100.00", gstin=BUYER, name="(Bill from)"), "S1-INV"),
        _system(_invoice("INV-2", "100.00"), "S2-INV"),
    ]
    truth = Truth(
        run_date=date(2026, 10, 1),
        buyer_entities=({"gstin": BUYER, "name": "Synthetic Buyer Ltd"},),
        files=(TruthFile("F1", "f1.pdf", DocType.INVOICE),),
        documents=(
            _truth_doc("S1-INV", {"vendor_gstin": None}),
            _truth_doc("S2-INV", {"vendor_gstin": SUPPLIER, "vendor_name": "Synthetic Supplies"}),
        ),
        digest="0" * 64,
    )

    vendor_of, vendors = simulate_vendors(documents, DEFAULT_CONFIG)
    report = score_vendors(documents, vendor_of, vendors, truth)

    assert report["vendors_created"] == 2
    assert report["WRONG_VENDORS_CREATED"] == 1
    # The label is never a display name and a GSTIN never names a vendor, so the name stays pending.
    assert report["junk_reasons"] == {"buyer_gstin": 1}
    assert report["document_vendor"] == {"correct": 1, "wrong": 1}


def test_configured_buyer_gstin_never_creates_a_vendor() -> None:
    documents = [
        _system(_invoice("INV-1", "100.00", gstin=BUYER, name="(Bill from)"), "S1-INV"),
        _system(_invoice("INV-2", "100.00"), "S2-INV"),
    ]
    body = copy.deepcopy(DEFAULT_CONFIG)
    body["engine"]["buyer_entities"] = [{"gstin": BUYER, "name": "Synthetic Buyer Ltd"}]

    vendor_of, vendors = simulate_vendors(documents, body)

    assert [vendor.created_from_gstin for vendor in vendors.values()] == [SUPPLIER]
    assert vendor_of[documents[0].document_id] is None


def test_auto_link_to_an_unexpected_po_is_a_false_auto_link() -> None:
    invoice = _system(_invoice("INV-1", "100.00"), "S1-INV")
    right_po = _system(_invoice("PO-1", "100.00"), "S1-PO")
    wrong_po = _system(_invoice("PO-2", "100.00"), "S2-PO")
    resolution = SimpleNamespace(
        links=(
            SimpleNamespace(
                from_document_id=invoice.document_id,
                to_document_id=wrong_po.document_id,
                state=LinkState.AUTO,
            ),
        )
    )
    truth = Truth(
        run_date=date(2026, 10, 1),
        buyer_entities=(),
        files=(),
        documents=(_truth_doc("S1-INV", {}, links=("S1-PO",)),),
        digest="0" * 64,
    )

    report = score_links([invoice, right_po, wrong_po], [resolution], truth)

    assert report["FALSE_AUTO_LINKS"] == 1
    assert report["missed"] == 1
