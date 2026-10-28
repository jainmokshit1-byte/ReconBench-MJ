"""Deterministic guards for the bench's hard-zero metrics (scan fix plan WI-2 step 5).

FALSE_SAFE, FALSE_AUTO_LINKS, FALSE_MATCHED and WRONG_VENDORS_CREATED must stay zero.
Each guard below pins one rule behind them on synthetic data only.
"""

from __future__ import annotations

import io
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from reconbench_app.vendor_resolution import anchor_document, field_verified
from reconbench_core.extraction.consensus import FieldType, decide
from reconbench_core.extraction.extract import REVIEW_FLOOR, extract_document
from reconbench_core.mapping import MappingConfig, MappingDocument, resolve_links
from reconbench_core.mapping.vendor import AnchorPlan, VendorNameStatus, plan_vendor_anchors
from reconbench_core.parsers import parse
from reconbench_core.parsers.parties import buyer_gstins, guard_result
from reconbench_core.parsers.types import BuyerEntityHint, ParseResult, ParserHints
from reconbench_core.recon import ReconciliationConfig, ReconciliationSnapshot, evaluate_components
from reconbench_core.schema import (
    CaseStatus,
    DocType,
    Document,
    FieldOrigin,
    FieldSource,
    FieldValue,
    FieldVerification,
    FileFormat,
    LinkMethod,
    LinkState,
)
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from tests.support.layouts import gstin, row
from tests.unit import test_consensus_core as consensus_case
from tests.unit import test_phase4_mapping_links as mapping_case
from tests.unit import test_phase4_reconciliation as recon_case
from tests.unit.test_party_role_guard import _invoice as invoice_layout
from tests.unit.test_party_role_guard import _po as po_layout

VENDOR = gstin("29AABCU9603R1Z")
BUYER = gstin("27AAPFU0939F1Z")
ENTITIES = (BuyerEntityHint(gstin=BUYER, name="Synthetic Buyer Pvt Ltd"),)


def _text_invoice_pdf(*party_lines: str) -> bytes:
    stream = io.BytesIO()
    pdf = canvas.Canvas(stream, pagesize=letter)
    pdf.setFont("Helvetica", 10)
    lines = (
        "Invoice Number: SYN-INV-0042",
        "Invoice Date: 2026-08-05",
        *party_lines,
        "Taxable Value: 1000.00",
        "CGST: 90.00",
        "SGST: 90.00",
        "Total Value: 1180.00",
    )
    for index, line in enumerate(lines):
        pdf.drawString(36, 748 - index * 17, line)
    pdf.save()
    return stream.getvalue()


def _parse_pdf(content: bytes, entities: tuple[BuyerEntityHint, ...] = ()) -> ParseResult:
    return parse(
        content,
        ParserHints(doc_type=DocType.INVOICE, file_format=FileFormat.PDF, buyer_entities=entities),
    )


# --- WRONG_VENDORS_CREATED: a buyer GSTIN is never the vendor's -----------------------------


def test_configured_buyer_gstin_is_never_the_vendor_on_the_layout_path() -> None:
    extraction = extract_document(
        invoice_layout(row(400, (100, f"GSTIN: {BUYER}"))),
        doc_type=DocType.INVOICE,
        buyer_entities=ENTITIES,
    )

    assert extraction is not None
    assert extraction.document.header.vendor_gstin is None
    assert extraction.document.header.buyer_gstin is not None


def test_configured_buyer_gstin_is_never_the_vendor_on_the_core_path() -> None:
    result = _parse_pdf(_text_invoice_pdf(f"GSTIN: {BUYER}"), ENTITIES)

    assert result.success
    header = result.documents[0].header
    assert header.vendor_gstin is None or header.vendor_gstin.value != BUYER
    assert "VENDOR_GSTIN_IS_BUYER_ENTITY" in {warning.code for warning in result.warnings}


def test_guard_drops_a_buyer_gstin_read_as_vendor_whatever_the_reader() -> None:
    document = consensus_case._document({"vendor_gstin": BUYER}, needs=False, reasons=())
    result = ParseResult(
        success=True, file_format=FileFormat.PDF, documents=(document,), layout_signature="s"
    )

    guarded = guard_result(result, ParserHints(buyer_entities=ENTITIES)).documents[0]

    assert guarded.header.vendor_gstin is None
    assert guarded.needs_verification


def _anchor_plan(document: Document, entities: tuple[BuyerEntityHint, ...]) -> AnchorPlan:
    """The vendor plan one fresh workspace makes from this document (app trust rule)."""

    header = document.header

    def verified(field: FieldValue[Any] | None) -> bool:
        return field is not None and field_verified(
            field.confidence, field.source.value, structured=False
        )

    anchor = anchor_document(
        document_id=uuid4(),
        natural_key="SYN-PO-0042",
        vendor_gstin=str(header.vendor_gstin.value) if header.vendor_gstin else None,
        vendor_name=str(header.vendor_name.value) if header.vendor_name else None,
        gstin_verified=verified(header.vendor_gstin),
        name_verified=verified(header.vendor_name),
        buyers=entities,
    )
    return plan_vendor_anchors([anchor], [], buyer_gstins(entities))


def test_po_issuer_name_and_own_gstin_without_entities_create_no_vendor() -> None:
    layout = po_layout(row(400, (100, f"GSTIN: {BUYER}")))
    extraction = extract_document(layout, doc_type=DocType.PO)

    assert extraction is not None
    assert extraction.document.header.vendor_name is None
    assert "vendor_name" in extraction.document.header.extras["review_fields"]
    assert _anchor_plan(extraction.document, ()).new_vendors == ()


def test_po_issuer_name_never_names_a_vendor_with_or_without_entities() -> None:
    layout = po_layout(row(400, (100, f"Supplier GSTIN: {VENDOR}")))
    unconfigured = extract_document(layout, doc_type=DocType.PO)
    configured = extract_document(layout, doc_type=DocType.PO, buyer_entities=ENTITIES)

    assert unconfigured is not None and configured is not None
    plan = _anchor_plan(unconfigured.document, ())
    # The supplier GSTIN still anchors a vendor; the issuer's own name never names it.
    assert [(item.name, item.name_status) for item in plan.new_vendors] == [
        ("", VendorNameStatus.PENDING)
    ]
    assert plan == _anchor_plan(configured.document, ENTITIES)


def test_unlabelled_heading_beside_an_unproven_gstin_is_withheld_on_the_core_path() -> None:
    content = _text_invoice_pdf("SYNTHETIC TRADERS PVT LTD", f"GSTIN: {VENDOR}")
    unconfigured = _parse_pdf(content)
    configured = _parse_pdf(content, ENTITIES)

    header = unconfigured.documents[0].header
    assert header.vendor_name is None
    assert "vendor_name" in header.extras["review_fields"]
    assert "VENDOR_NAME_ROLE_UNESTABLISHED" in {warning.code for warning in unconfigured.warnings}
    # Configured entities establish the GSTIN's role, so the heading names the seller as before.
    name = configured.documents[0].header.vendor_name
    assert name is not None and name.value == "SYNTHETIC TRADERS PVT LTD"


# --- FALSE_SAFE: an unproven party role is never trusted -------------------------------------


def test_single_unlabelled_gstin_without_entities_needs_review_on_the_layout_path() -> None:
    extraction = extract_document(
        invoice_layout(row(400, (100, f"GSTIN: {BUYER}"))), doc_type=DocType.INVOICE
    )

    assert extraction is not None
    vendor = extraction.document.header.vendor_gstin
    assert vendor is not None and vendor.confidence is not None
    assert vendor.confidence < REVIEW_FLOOR
    assert extraction.document.needs_verification


def test_single_unlabelled_gstin_without_entities_needs_review_on_the_core_path() -> None:
    result = _parse_pdf(_text_invoice_pdf(f"GSTIN: {BUYER}"))

    assert result.success
    document = result.documents[0]
    vendor = document.header.vendor_gstin
    assert vendor is not None and vendor.confidence is not None
    assert vendor.confidence < REVIEW_FLOOR
    assert document.needs_verification
    assert "vendor_gstin" in document.header.extras["review_fields"]
    assert "BUYER_ENTITIES_NOT_CONFIGURED" in {warning.code for warning in result.warnings}


# --- FALSE_MATCHED: low-confidence critical data is never Matched ---------------------------


@pytest.mark.parametrize("name", ["doc_no", "grand_total"])
def test_critical_field_below_the_review_floor_is_never_matched(name: str) -> None:
    po_doc = recon_case._document(recon_case._po_xml().replace("20260101", "20260115"), DocType.PO)
    po = recon_case._record(1, po_doc, DocType.PO)
    grn_doc = recon_case._document(recon_case._grn_xml(10, accepted=10), DocType.GRN)
    grn = recon_case._record(2, grn_doc, DocType.GRN)
    invoice_doc = recon_case._document(recon_case._invoice_xml("INV-2201", 10), DocType.INVOICE)
    field = getattr(invoice_doc.header, name)
    low = field.model_copy(update={"confidence": REVIEW_FLOOR - Decimal("0.01")})
    invoice_doc = invoice_doc.model_copy(
        update={"header": invoice_doc.header.model_copy(update={name: low})}
    )
    invoice = recon_case._record(3, invoice_doc, DocType.INVOICE)
    snapshot = ReconciliationSnapshot(
        documents=(po, grn, invoice),
        links=(
            recon_case._link(10, grn.document_id, po.document_id),
            recon_case._link(11, invoice.document_id, po.document_id),
        ),
    )

    results = evaluate_components(snapshot, ReconciliationConfig(), date(2026, 2, 2))

    assert results
    assert all(
        result.status not in {CaseStatus.MATCHED, CaseStatus.MATCHED_FLAGS} for result in results
    )


# --- FALSE_AUTO_LINKS: content alone never auto-links (D-6) ---------------------------------


def test_content_only_link_is_suggested_even_with_the_most_permissive_config() -> None:
    invoice = mapping_case._source(
        10, mapping_case._document(mapping_case._invoice_xml("INV-10"), DocType.INVOICE)
    )
    po = mapping_case._po(20, mapping_case._document(mapping_case._po_xml("PO-100"), DocType.PO))
    config = MappingConfig(
        link_auto_accept=Decimal("0"),
        step3_min_score=Decimal("0"),
        step3_min_lead=Decimal("0"),
    )

    result = resolve_links([invoice], [po], config)[0]

    content = [link for link in result.links if link.method is LinkMethod.CONTENT]
    assert content
    assert all(link.state is LinkState.SUGGESTED for link in content)


# --- FALSE_AUTO_LINKS: a reference read by AI never auto-links (PD-2a) ----------------------


@pytest.mark.parametrize(
    ("origin", "source"),
    [
        (FieldOrigin.AI, FieldSource.AI),
        (FieldOrigin.AI, FieldSource.RULE),
        (FieldOrigin.OCR, FieldSource.AI),
    ],
)
def test_ai_read_po_reference_at_full_confidence_is_suggested_never_auto(
    origin: FieldOrigin, source: FieldSource
) -> None:
    invoice = mapping_case._source(
        10,
        mapping_case._document(
            mapping_case._invoice_xml("INV-10", po_ref="PO-100", qty="100", amount="1000"),
            DocType.INVOICE,
        ),
    )
    po = mapping_case._po(20, mapping_case._document(mapping_case._po_xml("PO-100"), DocType.PO))

    def read_by(origin: FieldOrigin, source: FieldSource) -> MappingDocument:
        po_refs = invoice.document.header.po_refs
        assert po_refs is not None
        header = invoice.document.header.model_copy(
            update={
                "po_refs": po_refs.model_copy(
                    update={"origin": origin, "source": source, "confidence": Decimal("1")}
                )
            }
        )
        document = invoice.document.model_copy(update={"header": header})
        return invoice.model_copy(update={"document": document})

    control = resolve_links([read_by(FieldOrigin.OCR, FieldSource.RULE)], [po])[0]
    result = resolve_links([read_by(origin, source)], [po])[0]

    # The same reference read by OCR passes the system check on the matching amount.
    assert [link.state for link in control.links] == [LinkState.AUTO]
    assert len(control.field_verifications) == 1
    assert result.links
    assert all(link.state is LinkState.SUGGESTED for link in result.links)
    assert not result.field_verifications


# --- FALSE_MATCHED: an invoice read by AI is never Matched before a person verifies it (PD-2b)


@pytest.mark.parametrize(
    "verification", [FieldVerification.NONE, FieldVerification.SYSTEM_CHECK]
)
def test_ai_read_invoice_at_full_confidence_is_never_matched_until_a_person_verifies_it(
    verification: FieldVerification,
) -> None:
    po_doc = recon_case._document(recon_case._po_xml().replace("20260101", "20260115"), DocType.PO)
    po = recon_case._record(1, po_doc, DocType.PO)
    grn_doc = recon_case._document(recon_case._grn_xml(10, accepted=10), DocType.GRN)
    grn = recon_case._record(2, grn_doc, DocType.GRN)
    invoice_doc = recon_case._document(recon_case._invoice_xml("INV-2201", 10), DocType.INVOICE)

    def read_by_ai(verification: FieldVerification) -> Document:
        def read(value: object) -> object:
            if not isinstance(value, FieldValue):
                return value
            return value.model_copy(
                update={
                    "origin": FieldOrigin.AI,
                    "source": FieldSource.AI,
                    "confidence": Decimal("1"),
                    "verification": verification,
                }
            )

        header = invoice_doc.header.model_copy(
            update={
                name: read(getattr(invoice_doc.header, name))
                for name in type(invoice_doc.header).model_fields
            }
        )
        lines = tuple(
            line.model_copy(
                update={name: read(getattr(line, name)) for name in type(line).model_fields}
            )
            for line in invoice_doc.lines
        )
        return invoice_doc.model_copy(update={"header": header, "lines": lines})

    def evaluate(document: Document) -> CaseStatus:
        invoice = recon_case._record(3, document, DocType.INVOICE)
        link = recon_case._link(11, invoice.document_id, po.document_id)
        snapshot = ReconciliationSnapshot(
            documents=(po, grn, invoice),
            links=(
                recon_case._link(10, grn.document_id, po.document_id),
                link.model_copy(update={"state": LinkState.CONFIRMED}),
            ),
        )
        results = evaluate_components(snapshot, ReconciliationConfig(), date(2026, 2, 2))
        return next(result.status for result in results if result.invoice_id == invoice.document_id)

    assert evaluate(read_by_ai(verification)) is CaseStatus.NEEDS_REVIEW
    # The same values, each verified by a person, are Matched.
    assert evaluate(read_by_ai(FieldVerification.USER)) is CaseStatus.MATCHED


# --- FALSE_SAFE: a vision reading alone never verifies a value (D42) ------------------------


def test_vision_sourced_value_is_never_verified_by_its_own_reading() -> None:
    document = consensus_case._document({"doc_no": "INV-000123"}, source=FieldSource.AI.value)
    check = consensus_case._check(consensus_case._vision_field("doc_no", "INV-000123"))

    result = decide(
        document,
        vision=check,
        crops=None,
        hints=ParserHints(buyer_entities=consensus_case.BUYERS),
        policy=consensus_case._policy(auto=True, crop=False, types=tuple(FieldType)),
    )

    decision = consensus_case._decision(result, "header:doc_no")
    assert not decision.auto_verified
    doc_no = result.document.header.doc_no
    assert doc_no is not None and doc_no.confidence is not None
    assert doc_no.confidence < Decimal("0.98")


# --- AI HOOK & ROUTING GUARDS (PD-3a / PD-1a) -----------------------------------------------


def test_hard_zero_no_mode_lists_h2_h3_h4() -> None:
    from reconbench_app.ai.consent import HOOKS_BY_MODE, hooks_for_mode

    forbidden = ("h2_item", "h3_rerank", "h4_vendor")
    for mode in ("rules_only", "ai_assisted", "ai_first"):
        for vision in (False, True):
            hooks = hooks_for_mode(mode, vision=vision)
            assert not any(any(h.startswith(f) for f in forbidden) for h in hooks)

    for _mode, hook_tuple in HOOKS_BY_MODE.items():
        assert not any(any(h.startswith(f) for f in forbidden) for h in hook_tuple)


def test_hard_zero_target_never_returns_inactive_provider() -> None:
    from reconbench_app.ai.runtime_checks import ProviderConfig, SettingsSnapshot

    vllm = ProviderConfig("vllm", True, "qwen3.5:9b", "http://vllm.example.test/v1", None, 1)
    gemini = ProviderConfig("gemini", True, "gemini-3.8-flash", None, None, 1)

    # vLLM active -> Gemini is inactive, target() must NEVER return gemini
    vllm_snap = SettingsSnapshot(
        workspace_id=uuid4(),
        mode="ai_assisted",
        provider="gemini",
        model="gemini-3.8-flash",
        version=1,
        consent_given=True,
        consent_fingerprint="fp",
        key_secret_id=None,
        key_storage="saved",
        reader_strategy="single",
        primary_reader="vllm",
        vllm=vllm,
        gemini=gemini,
    )
    for hook in ("h1_extract", "h6_vision", "h2_item", "h3_rerank", "h4_vendor"):
        target_provider, _ = vllm_snap.target(hook)
        assert target_provider != "gemini"
        assert target_provider == "vllm"

    # Gemini active -> vLLM is inactive, target() must NEVER return vllm
    gemini_snap = SettingsSnapshot(
        workspace_id=uuid4(),
        mode="ai_assisted",
        provider="gemini",
        model="gemini-3.8-flash",
        version=1,
        consent_given=True,
        consent_fingerprint="fp",
        key_secret_id=None,
        key_storage="saved",
        reader_strategy="single",
        primary_reader="gemini",
        vllm=vllm,
        gemini=gemini,
    )
    for hook in ("h1_extract", "h6_vision", "h2_item", "h3_rerank", "h4_vendor"):
        target_provider, _ = gemini_snap.target(hook)
        assert target_provider != "vllm"
        assert target_provider == "gemini"

    # rules_only -> target() must return (None, None)
    rules_snap = SettingsSnapshot(
        workspace_id=uuid4(),
        mode="rules_only",
        provider="gemini",
        model="gemini-3.8-flash",
        version=1,
        consent_given=True,
        consent_fingerprint="fp",
        key_secret_id=None,
        key_storage="saved",
        reader_strategy="single",
        primary_reader="vllm",
        vllm=vllm,
        gemini=gemini,
    )
    for hook in ("h1_extract", "h6_vision", "h2_item", "h3_rerank", "h4_vendor"):
        target_provider, target_model = rules_snap.target(hook)
        assert target_provider is None
        assert target_model is None

