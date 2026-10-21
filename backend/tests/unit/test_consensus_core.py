"""Synthetic AC-6 consensus invariants and edge cases."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st
from reconbench_core.extraction.consensus import (
    ConsensusPolicy,
    CropCheck,
    CropOutcome,
    DecisionRule,
    FieldKey,
    FieldType,
    ReaderKind,
    TrustLevel,
    crop_needs,
    crop_outcome,
    decide,
    field_validators,
    values_agree,
)
from reconbench_core.extraction.vision import (
    AcceptedField,
    Box,
    SentPage,
    VisionAnnotation,
    VisionCheck,
    VisionField,
    VisionLine,
    VisionPageKind,
    VisionReading,
    check_reading,
)
from reconbench_core.normalize.gstin import calculate_gstin_checksum
from reconbench_core.parsers.types import BuyerEntityHint, ParserHints
from reconbench_core.schema import DocType, Document, ReasonCode

SUPPLIER = "27AAPFU0939F1Z" + calculate_gstin_checksum("27AAPFU0939F1Z")
BUYER = "27AABCS1234Q1Z" + calculate_gstin_checksum("27AABCS1234Q1Z")
BUYERS = (BuyerEntityHint(gstin=BUYER, name="Synthetic Buyer Pvt Ltd"),)
PAGES = {1: SentPage(1)}
BOX = (100, 100, 120, 300)


def _field(value: Any, *, confidence: str = "0.65", source: str = "rule") -> dict[str, Any]:
    return {
        "value": value,
        "confidence": confidence,
        "source": source,
        "evidence": {},
    }


def _document(
    fields: dict[str, Any] | None = None,
    *,
    source: str = "rule",
    extras: dict[str, Any] | None = None,
    lines: tuple[dict[str, Any], ...] = (),
    needs: bool = True,
    reasons: tuple[ReasonCode, ...] = (ReasonCode.LOW_CONFIDENCE_DATA,),
) -> Document:
    header = {name: _field(value, source=source) for name, value in (fields or {}).items()}
    header["doc_type"] = "invoice"
    header["extras"] = extras or {}
    return Document.model_validate(
        {
            "header": header,
            "lines": lines,
            "needs_verification": needs,
            "reason_codes": reasons,
        }
    )


def _line(
    line_no: int = 1,
    *,
    quantity: str = "2",
    rate: str = "50.00",
    amount: str = "100.00",
    confidence: str = "0.65",
) -> dict[str, Any]:
    return {
        "line_no": line_no,
        "quantity": _field(Decimal(quantity), confidence=confidence),
        "rate": _field(Decimal(rate), confidence=confidence),
        "amount": _field(Decimal(amount), confidence=confidence),
        "description": _field("Synthetic widget", confidence=confidence),
    }


def _check(
    *fields: VisionField,
    lines: tuple[VisionLine, ...] = (),
    annotations: tuple[VisionAnnotation, ...] = (),
    pages: tuple[VisionPageKind, ...] = (),
    ocr: Document | None = None,
) -> VisionCheck:
    return check_reading(
        VisionReading("invoice", fields=fields, lines=lines, annotations=annotations, pages=pages),
        pages=PAGES,
        buyer_entities=BUYERS,
        ocr=ocr,
    )


def _vision_field(name: str, text: str, box: tuple[int, ...] = BOX) -> VisionField:
    return VisionField(name, text, 1, box)


def _policy(
    *,
    auto: bool = False,
    reopen: bool = False,
    crop: bool = True,
    types: tuple[FieldType, ...] = (),
    reopen_types: tuple[FieldType, ...] | None = None,
) -> ConsensusPolicy:
    return ConsensusPolicy(
        auto_verify=auto,
        reopen=reopen,
        field_types=frozenset(types),
        reopen_field_types=(frozenset(reopen_types) if reopen_types is not None else None),
        require_crop=crop,
        as_of=date(2026, 10, 8),
    )


def _decision(result: Any, key: str):
    return next(item for item in result.decisions if item.key.text() == key)


def test_ocr_and_vision_agreement_with_crop_and_validators_is_l1() -> None:
    document = _document({"doc_no": "INV-000123"})
    check = _check(_vision_field("doc_no", "INV-000123"))
    result = decide(
        document,
        vision=check,
        crops={FieldKey("header", "doc_no"): CropCheck(CropOutcome.AGREE, "INV-000123")},
        hints=ParserHints(doc_type=DocType.INVOICE, buyer_entities=BUYERS),
        policy=_policy(auto=True, types=(FieldType.DOC_NO,)),
    )
    decision = _decision(result, "header:doc_no")
    assert (decision.level, decision.rule, decision.auto_verified) == (
        TrustLevel.L1,
        DecisionRule.D42,
        True,
    )
    assert result.document.header.doc_no is not None
    assert result.document.header.doc_no.value == "INV-000123"
    assert result.document.header.doc_no.confidence == Decimal("0.98")


def test_policy_off_returns_the_same_document() -> None:
    document = _document({"doc_no": "INV-000123"})
    result = decide(
        document,
        vision=_check(_vision_field("doc_no", "INV-000123")),
        crops={FieldKey("header", "doc_no"): CropCheck(CropOutcome.AGREE)},
        hints=ParserHints(doc_type=DocType.INVOICE),
        policy=_policy(),
    )
    assert not result.changed
    assert result.document is document


def test_auto_verify_needs_the_field_type_in_the_policy() -> None:
    document = _document({"doc_no": "INV-000123"})
    result = decide(
        document,
        vision=_check(_vision_field("doc_no", "INV-000123")),
        crops={FieldKey("header", "doc_no"): CropCheck(CropOutcome.AGREE)},
        hints=ParserHints(doc_type=DocType.INVOICE),
        policy=_policy(auto=True, types=(FieldType.AMOUNT,)),
    )
    assert not _decision(result, "header:doc_no").auto_verified
    assert result.document is document


def test_vision_only_value_is_l2_and_never_written() -> None:
    document = _document()
    result = decide(
        document,
        vision=_check(_vision_field("doc_no", "INV-000123")),
        crops=None,
        hints=ParserHints(doc_type=DocType.INVOICE),
        policy=_policy(auto=True, crop=False, types=(FieldType.DOC_NO,)),
    )
    assert _decision(result, "header:doc_no").level is TrustLevel.L2
    assert _decision(result, "header:doc_no").rule is DecisionRule.VISION_ONLY
    assert result.document.header.doc_no is None


@pytest.mark.parametrize("structured", [False, True])
def test_vision_sourced_value_is_one_reader_and_never_l1(structured: bool) -> None:
    # The stored value came from one vision provider and a second vision reading agrees:
    # both are LLM readings, so there is no independent non-LLM reader (WI-G1).
    document = _document({"doc_no": "INV-000123"}, source="ai")
    key = FieldKey("header", "doc_no")
    result = decide(
        document,
        vision=_check(_vision_field("doc_no", "INV-000123")),
        crops={key: CropCheck(CropOutcome.AGREE, "INV-000123")},
        hints=ParserHints(doc_type=DocType.INVOICE, buyer_entities=BUYERS),
        policy=_policy(auto=True, crop=False, types=tuple(FieldType)),
        structured=structured,
    )
    decision = _decision(result, "header:doc_no")
    assert {reading.reader for reading in decision.readings} == {ReaderKind.VISION}
    assert (decision.level, decision.rule, decision.auto_verified) == (
        TrustLevel.L2,
        DecisionRule.SINGLE,
        False,
    )
    assert result.document is document


def test_vision_sourced_value_without_a_vision_check_stays_unverified() -> None:
    document = _document({"doc_no": "INV-000123"}, source="ai")
    result = decide(
        document,
        vision=None,
        crops=None,
        hints=ParserHints(doc_type=DocType.INVOICE, buyer_entities=BUYERS),
        policy=_policy(auto=True, crop=False, types=tuple(FieldType)),
    )
    decision = _decision(result, "header:doc_no")
    assert decision.readings[0].reader is ReaderKind.VISION
    assert decision.level is not TrustLevel.L1
    assert not decision.auto_verified


def test_vision_sourced_conflict_crops_at_the_vision_reading_geometry() -> None:
    document = _document({"doc_no": "INV-000123"}, source="ai")
    result = decide(
        document,
        vision=_check(_vision_field("doc_no", "INV-000124")),
        crops=None,
        hints=ParserHints(doc_type=DocType.INVOICE),
        policy=_policy(),
    )
    assert _decision(result, "header:doc_no").rule is DecisionRule.CONFLICT
    [need] = crop_needs(result)
    assert need.key == FieldKey("header", "doc_no")


def test_disagreement_is_l3_with_both_readings() -> None:
    document = _document({"doc_no": "INV-000123"})
    result = decide(
        document,
        vision=_check(_vision_field("doc_no", "INV-000124")),
        crops=None,
        hints=ParserHints(doc_type=DocType.INVOICE),
        policy=_policy(),
    )
    decision = _decision(result, "header:doc_no")
    assert decision.level is TrustLevel.L3
    assert {reading.value for reading in decision.readings} == {"INV-000123", "INV-000124"}


def test_a_dissenting_third_reader_blocks_l1() -> None:
    document = _document(
        {"doc_no": "INV-000123"},
        extras={
            "einvoice": {
                "fields": {"doc_no": "conflict"},
                "other_readings": {"doc_no": "INV-000124"},
            }
        },
    )
    result = decide(
        document,
        vision=_check(_vision_field("doc_no", "INV-000123")),
        crops=None,
        hints=ParserHints(doc_type=DocType.INVOICE),
        policy=_policy(crop=False),
    )
    decision = _decision(result, "header:doc_no")
    assert decision.level is TrustLevel.L3
    assert len(decision.readings) == 3


def test_rules_verified_value_stays_l1_existing() -> None:
    document = _document({"doc_no": "INV-000123"})
    document = document.model_copy(
        update={
            "header": document.header.model_copy(
                update={
                    "doc_no": document.header.doc_no.model_copy(
                        update={"confidence": Decimal("0.98")}
                    )
                }
            )
        }
    )
    result = decide(
        document,
        vision=_check(_vision_field("doc_no", "INV-000123")),
        crops=None,
        hints=ParserHints(doc_type=DocType.INVOICE),
        policy=_policy(),
    )
    decision = _decision(result, "header:doc_no")
    assert decision.level is TrustLevel.L1 and decision.rule is DecisionRule.EXISTING


def test_vision_disagreement_reopens_verified_field_only_when_policy_reopens() -> None:
    document = _document({"doc_no": "INV-000123"}, extras={"review_fields": []})
    field_value = document.header.doc_no
    assert field_value is not None
    document = document.model_copy(
        update={
            "header": document.header.model_copy(
                update={"doc_no": field_value.model_copy(update={"confidence": Decimal("0.98")})}
            )
        }
    )
    check = _check(_vision_field("doc_no", "INV-000124"))
    common = {
        "vision": check,
        "crops": None,
        "hints": ParserHints(doc_type=DocType.INVOICE),
    }
    closed = decide(document, policy=_policy(reopen=False), **common)
    opened = decide(document, policy=_policy(reopen=True), **common)
    assert not closed.changed
    assert opened.document.header.doc_no is not None
    assert opened.document.header.doc_no.confidence == Decimal("0.65")
    assert opened.document.header.extras["review_fields"] == ["doc_no"]

    selected_only = decide(
        document,
        policy=_policy(reopen=True, reopen_types=(FieldType.GSTIN,)),
        **common,
    )
    assert not selected_only.changed


def test_manual_structured_and_signed_qr_values_are_l0_and_untouched() -> None:
    manual = _document({"doc_no": "INV-000123"}, source="manual")
    manual_result = decide(
        manual,
        vision=None,
        crops=None,
        hints=ParserHints(doc_type=DocType.INVOICE),
        policy=_policy(auto=True, reopen=True, types=(FieldType.DOC_NO,)),
    )
    assert _decision(manual_result, "header:doc_no").level is TrustLevel.L0
    assert not manual_result.changed

    structured = _document({"doc_no": "INV-000123"})
    structured_result = decide(
        structured,
        vision=None,
        crops=None,
        hints=ParserHints(doc_type=DocType.INVOICE),
        policy=_policy(),
        structured=True,
    )
    assert _decision(structured_result, "header:doc_no").level is TrustLevel.L0

    signed = _document(
        {"doc_no": "INV-000123"},
        extras={"einvoice": {"fields": {"doc_no": "signed"}}},
    )
    signed_result = decide(
        signed,
        vision=None,
        crops=None,
        hints=ParserHints(doc_type=DocType.INVOICE),
        policy=_policy(),
    )
    assert _decision(signed_result, "header:doc_no").level is TrustLevel.L0


def test_hard_validators_reject() -> None:
    invalid = "27AAPFU0939F1Z0"
    cases = (
        (_document({"vendor_gstin": BUYER}), "supplier_gstin", BUYER, "buyer_entity"),
        (_document({"vendor_gstin": invalid}), "supplier_gstin", invalid, "gstin_checksum"),
        (
            _document({"vendor_gstin": SUPPLIER, "buyer_gstin": BUYER, "igst": Decimal("18.00")}),
            "igst_total",
            "18.00",
            "gst_type",
        ),
    )
    for document, vision_name, vision_text, _expected in cases:
        if vision_name == "supplier_gstin":
            accepted = AcceptedField(
                "supplier_gstin", "gstin", str(vision_text), vision_text, 1, Box(*BOX)
            )
        else:
            accepted = AcceptedField(
                "igst_total", "money", str(vision_text), Decimal(vision_text), 1, Box(*BOX)
            )
        check = VisionCheck("invoice", (accepted,), (), (), (), {})
        key = "header:vendor_gstin" if vision_name == "supplier_gstin" else "header:igst"
        result = decide(
            document,
            vision=check,
            crops=None,
            hints=ParserHints(doc_type=DocType.INVOICE, buyer_entities=BUYERS),
            policy=_policy(crop=False),
        )
        decision = _decision(result, key)
        assert decision.level is TrustLevel.REJECTED
        assert _expected in decision.failed_validators


def test_soft_validators_keep_l2() -> None:
    document = _document({"doc_no": "INV-000123", "doc_date": date(2020, 1, 1)})
    hints = ParserHints(doc_type=DocType.INVOICE)
    assert "date_window" in field_validators(
        FieldKey("header", "doc_date"),
        date(2020, 1, 1),
        document,
        hints=hints,
        policy=ConsensusPolicy(as_of=None),
    )
    assert "lookalike" in field_validators(
        FieldKey("header", "doc_no"),
        "INVO0123",
        document,
        hints=hints,
        policy=_policy(),
    )
    bad_math = _document(
        {
            "taxable_total": Decimal("100.00"),
            "cgst": Decimal("9.00"),
            "sgst": Decimal("9.00"),
            "grand_total": Decimal("130.00"),
        }
    )
    assert "totals_arithmetic" in field_validators(
        FieldKey("header", "grand_total"),
        Decimal("130.00"),
        bad_math,
        hints=hints,
        policy=_policy(),
    )


def test_lookalike_document_number_is_never_auto_verified() -> None:
    document = _document({"doc_no": "INVO0123"})
    result = decide(
        document,
        vision=_check(_vision_field("doc_no", "INVO0123")),
        crops={FieldKey("header", "doc_no"): CropCheck(CropOutcome.AGREE)},
        hints=ParserHints(doc_type=DocType.INVOICE),
        policy=_policy(auto=True, types=(FieldType.DOC_NO,)),
    )
    decision = _decision(result, "header:doc_no")
    assert decision.level is TrustLevel.L2
    assert not decision.auto_verified


def test_handwritten_or_stamped_overlap_is_never_auto_verified() -> None:
    document = _document({"doc_no": "INV-000123"})
    result = decide(
        document,
        vision=_check(
            _vision_field("doc_no", "INV-000123"),
            annotations=(VisionAnnotation("handwritten", "INV-000123", "invoice_number", 1, BOX),),
        ),
        crops={FieldKey("header", "doc_no"): CropCheck(CropOutcome.AGREE, "INV-000123")},
        hints=ParserHints(doc_type=DocType.INVOICE),
        policy=_policy(auto=True, reopen=True, types=(FieldType.DOC_NO,)),
    )
    decision = _decision(result, "header:doc_no")
    assert decision.reason == "handwritten_or_stamped_mark"
    assert not decision.auto_verified and not decision.reopened
    assert not result.changed


def test_crop_missing_or_disagreeing_keeps_l2_and_required_crop_false_allows_l1() -> None:
    document = _document({"doc_no": "INV-000123"})
    check = _check(_vision_field("doc_no", "INV-000123"))
    common = {"vision": check, "hints": ParserHints(doc_type=DocType.INVOICE)}
    missing = decide(document, crops=None, policy=_policy(), **common)
    wrong = decide(
        document,
        crops={FieldKey("header", "doc_no"): CropCheck(CropOutcome.DISAGREE)},
        policy=_policy(),
        **common,
    )
    waived = decide(document, crops=None, policy=_policy(crop=False), **common)
    assert _decision(missing, "header:doc_no").reason == "crop_missing"
    assert _decision(wrong, "header:doc_no").level is TrustLevel.L2
    assert _decision(waived, "header:doc_no").level is TrustLevel.L1


def test_validator_failure_stays_blocked_after_targeted_crop_agrees() -> None:
    invalid = "27AAPFU0939F1Z0"
    document = _document({"vendor_gstin": invalid})
    accepted = AcceptedField("supplier_gstin", "gstin", invalid, invalid, 1, Box(*BOX))
    check = VisionCheck("invoice", (accepted,), (), (), (), {})
    key = FieldKey("header", "vendor_gstin")
    hints = ParserHints(doc_type=DocType.INVOICE, buyer_entities=BUYERS)
    first = decide(document, vision=check, crops=None, hints=hints, policy=_policy())

    [need] = crop_needs(first)
    assert need.key == key
    assert need.kind == "gstin" and need.mode == "line"

    reread = decide(
        document,
        vision=check,
        crops={key: CropCheck(CropOutcome.AGREE, invalid)},
        hints=hints,
        policy=_policy(auto=True, types=(FieldType.GSTIN,)),
    )
    decision = _decision(reread, "header:vendor_gstin")
    assert decision.reason == "hard_validator"
    assert decision.crop == CropCheck(CropOutcome.AGREE, invalid)
    assert "gstin_checksum" in decision.failed_validators
    assert not decision.auto_verified


def test_reader_conflict_gets_crop_evidence_but_stays_conflicted() -> None:
    document = _document({"doc_no": "INV-000123"})
    check = _check(_vision_field("doc_no", "INV-000124"))
    key = FieldKey("header", "doc_no")
    hints = ParserHints(doc_type=DocType.INVOICE)
    first = decide(document, vision=check, crops=None, hints=hints, policy=_policy())

    [need] = crop_needs(first)
    assert need.key == key
    assert need.value == "INV-000123"

    reread = decide(
        document,
        vision=check,
        crops={key: CropCheck(CropOutcome.AGREE, "INV-000123")},
        hints=hints,
        policy=_policy(auto=True, types=(FieldType.DOC_NO,)),
    )
    decision = _decision(reread, "header:doc_no")
    assert decision.rule is DecisionRule.CONFLICT
    assert decision.reason == "readers_disagree"
    assert decision.crop == CropCheck(CropOutcome.AGREE, "INV-000123")
    assert not decision.auto_verified


def test_never_automatic_fields_are_excluded() -> None:
    document = _document({"vendor_name": "Synthetic Supplier Limited"})
    result = decide(
        document,
        vision=_check(_vision_field("supplier_name", "Synthetic Supplier Limited")),
        crops=None,
        hints=ParserHints(doc_type=DocType.INVOICE),
        policy=_policy(auto=True, crop=False, types=tuple(FieldType)),
    )
    decision = _decision(result, "header:vendor_name")
    assert decision.level is TrustLevel.L2
    assert decision.rule is DecisionRule.EXCLUDED
    assert not decision.auto_verified


def test_split_unconfirmed_document_never_auto_verifies() -> None:
    document = _document(
        {"doc_no": "INV-000123"},
        extras={"page_split": {"state": "suggested", "pages": [1]}},
    )
    result = decide(
        document,
        vision=_check(_vision_field("doc_no", "INV-000123")),
        crops=None,
        hints=ParserHints(doc_type=DocType.INVOICE),
        policy=_policy(auto=True, crop=False, types=(FieldType.DOC_NO,)),
    )
    decision = _decision(result, "header:doc_no")
    assert "split_unconfirmed" in decision.failed_validators
    assert not decision.auto_verified


def test_review_flag_clears_only_when_every_listed_reason_is_resolved() -> None:
    document = _document(
        {"doc_no": "INV-000123", "grand_total": Decimal("100.00")},
        extras={"review_fields": ["doc_no", "grand_total"]},
    )
    hints = ParserHints(doc_type=DocType.INVOICE)
    only_doc_no = decide(
        document,
        vision=_check(_vision_field("doc_no", "INV-000123")),
        crops=None,
        hints=hints,
        policy=_policy(auto=True, crop=False, types=(FieldType.DOC_NO, FieldType.AMOUNT)),
    )
    assert only_doc_no.document.needs_verification
    assert only_doc_no.document.header.extras["review_fields"] == ["grand_total"]

    all_resolved = decide(
        document,
        vision=_check(
            _vision_field("doc_no", "INV-000123"), _vision_field("grand_total", "100.00")
        ),
        crops=None,
        hints=hints,
        policy=_policy(auto=True, crop=False, types=(FieldType.DOC_NO, FieldType.AMOUNT)),
    )
    assert not all_resolved.document.needs_verification
    assert all_resolved.document.reason_codes == ()

    other_reason = document.model_copy(update={"reason_codes": (ReasonCode.DUP_SUSPECTED,)})
    kept = decide(
        other_reason,
        vision=_check(
            _vision_field("doc_no", "INV-000123"), _vision_field("grand_total", "100.00")
        ),
        crops=None,
        hints=hints,
        policy=_policy(auto=True, crop=False, types=(FieldType.DOC_NO, FieldType.AMOUNT)),
    )
    assert kept.document.needs_verification
    assert ReasonCode.DUP_SUSPECTED in kept.document.reason_codes


def test_line_cells_pair_with_vision_rows_and_use_the_row_crop() -> None:
    document = _document(lines=(_line(),))
    line_one = VisionLine(
        1, BOX, {"description": "Synthetic widget", "quantity": "2", "rate": "50", "amount": "100"}
    )
    unpaired = VisionLine(
        1,
        (300, 100, 320, 300),
        {"description": "Unmatched part", "quantity": "9", "rate": "9", "amount": "81"},
    )
    check = _check(lines=(line_one, unpaired))
    key = FieldKey("line", "amount", 1)
    result = decide(
        document,
        vision=check,
        crops={
            key: CropCheck(
                CropOutcome.AGREE,
                "1 100.00",
            )
        },
        hints=ParserHints(doc_type=DocType.INVOICE),
        policy=_policy(auto=True, crop=True, types=(FieldType.LINE_AMOUNT,)),
    )
    decision = _decision(result, "line:1:amount")
    assert decision.level is TrustLevel.L1
    assert decision.auto_verified
    assert result.vision_only_lines == 1
    assert result.document.lines[0].amount is not None
    assert result.document.lines[0].amount.value == Decimal("100.00")


def test_grn_vision_quantity_is_read_as_received_quantity() -> None:
    grn = Document.model_validate(
        {
            "header": {"doc_type": "grn"},
            "lines": [
                {
                    "line_no": 1,
                    "received_qty": _field(Decimal("2")),
                    "description": _field("Synthetic widget"),
                }
            ],
        }
    )
    reading = VisionReading(
        "goods_receipt",
        lines=(VisionLine(1, BOX, {"description": "Synthetic widget", "quantity": "2"}),),
    )
    check = check_reading(reading, pages=PAGES, ocr=grn)
    result = decide(
        grn,
        vision=check,
        crops={FieldKey("line", "received_qty", 1): CropCheck(CropOutcome.AGREE)},
        hints=ParserHints(doc_type=DocType.GRN),
        policy=_policy(crop=True),
    )
    assert _decision(result, "line:1:received_qty").level is TrustLevel.L1


@given(
    auto=st.booleans(),
    reopen=st.booleans(),
    same_reading=st.booleans(),
    crop=st.sampled_from(tuple(CropOutcome)),
)
def test_values_never_change(
    auto: bool, reopen: bool, same_reading: bool, crop: CropOutcome
) -> None:
    document = _document({"doc_no": "INV-000123"})
    value = "INV-000123" if same_reading else "INV-000124"
    result = decide(
        document,
        vision=_check(_vision_field("doc_no", value)),
        crops={FieldKey("header", "doc_no"): CropCheck(crop)},
        hints=ParserHints(doc_type=DocType.INVOICE),
        policy=_policy(auto=auto, reopen=reopen, types=(FieldType.DOC_NO,)),
    )
    assert result.document.header.doc_no is not None
    assert result.document.header.doc_no.value == document.header.doc_no.value


def test_decide_is_order_independent() -> None:
    document = _document(lines=(_line(1), _line(2, quantity="1", rate="20.00", amount="20.00")))
    check = _check(
        lines=(
            VisionLine(
                1,
                BOX,
                {"description": "Synthetic widget", "quantity": "2", "rate": "50", "amount": "100"},
            ),
            VisionLine(
                1,
                (300, 100, 320, 300),
                {
                    "description": "Another synthetic part",
                    "quantity": "1",
                    "rate": "20",
                    "amount": "20",
                },
            ),
        )
    )
    reversed_check = replace(check, lines=tuple(reversed(check.lines)))
    options = {
        "crops": None,
        "hints": ParserHints(doc_type=DocType.INVOICE),
        "policy": _policy(crop=False),
    }
    first = decide(document, vision=check, **options)
    second = decide(document, vision=reversed_check, **options)
    assert first.decisions_json() == second.decisions_json()


@pytest.mark.parametrize(
    ("kind", "value", "text", "mode", "expected"),
    [
        ("money", Decimal("1000.00"), "₹ 1,000.00", "line", CropOutcome.AGREE),
        ("number", Decimal("2500"), "2 500 PCS", "line", CropOutcome.AGREE),
        ("date", date(2026, 8, 12), "12-Aug-26", "line", CropOutcome.AGREE),
        ("gstin", SUPPLIER, SUPPLIER, "line", CropOutcome.AGREE),
        ("doc_no", "INV-000123", "INV-000123", "line", CropOutcome.AGREE),
        ("refs", ("PO/26-27/08-236",), "PO/26-27/08-236", "line", CropOutcome.AGREE),
        ("money", Decimal("1000.00"), "1 1000.00", "block", CropOutcome.AGREE),
        ("money", Decimal("1.00"), "1 998.00", "block", CropOutcome.DISAGREE),
        ("money", Decimal("1000.00"), "", "line", CropOutcome.EMPTY),
    ],
)
def test_crop_outcome_candidates(
    kind: str, value: Any, text: str, mode: str, expected: CropOutcome
) -> None:
    assert crop_outcome(kind, value, text, mode=mode) is expected  # type: ignore[arg-type]


def test_hints_carry_annotations_and_marketplace_orders_but_no_fields() -> None:
    reading = VisionReading(
        "invoice",
        fields=(_vision_field("marketplace_order_ref", "407-0121734-2276374"),),
        annotations=(VisionAnnotation("handwritten", "PO/26-27/04-777", "po_number", 1, BOX),),
        pages=(VisionPageKind(1, "invoice"),),
    )
    result = decide(
        _document(),
        vision=check_reading(reading, pages=PAGES),
        crops=None,
        hints=ParserHints(doc_type=DocType.INVOICE),
        policy=_policy(),
    )
    assert result.hints == {
        "annotation_refs": ("PO/26-27/04-777",),
        "marketplace_order_refs": ("407-0121734-2276374",),
        "page_kinds": ("1:invoice",),
    }
    assert not result.document.header.po_refs


def test_crop_needs_targets_failed_checks_and_selects_summary_or_row_geometry() -> None:
    document = _document({"doc_no": "INV-000123"})
    result = decide(
        document,
        vision=_check(_vision_field("doc_no", "INV-000123")),
        crops=None,
        hints=ParserHints(doc_type=DocType.INVOICE),
        policy=_policy(),
    )
    [need] = crop_needs(result)
    assert (need.key, need.kind, need.page, need.box, need.mode) == (
        FieldKey("header", "doc_no"),
        "doc_no",
        1,
        BOX,
        "line",
    )
    total = _document({"grand_total": Decimal("100.00")})
    total_result = decide(
        total,
        vision=_check(_vision_field("grand_total", "100.00")),
        crops=None,
        hints=ParserHints(doc_type=DocType.INVOICE),
        policy=_policy(),
    )
    [summary_need] = crop_needs(total_result)
    assert summary_need.mode == "block"

    line_document = _document(lines=(_line(amount="120.00"),))
    line_check = _check(
        lines=(
            VisionLine(
                1,
                BOX,
                {"description": "Synthetic widget", "quantity": "2", "rate": "50", "amount": "120"},
            ),
        )
    )
    line_result = decide(
        line_document,
        vision=line_check,
        crops=None,
        hints=ParserHints(doc_type=DocType.INVOICE),
        policy=_policy(),
    )
    [line_need] = (need for need in crop_needs(line_result) if need.key.name == "amount")
    assert line_need.key == FieldKey("line", "amount", 1)
    assert line_need.mode == "block"
    assert _decision(line_result, "line:1:amount").reason == "validator_failed"

    invalid_math = _document(
        {
            "taxable_total": Decimal("100.00"),
            "cgst": Decimal("9.00"),
            "sgst": Decimal("9.00"),
            "grand_total": Decimal("130.00"),
        }
    )
    failed_validation = decide(
        invalid_math,
        vision=_check(_vision_field("grand_total", "130.00")),
        crops=None,
        hints=ParserHints(doc_type=DocType.INVOICE),
        policy=_policy(),
    )
    assert _decision(failed_validation, "header:grand_total").reason == "validator_failed"
    assert any(need.key.name == "grand_total" for need in crop_needs(failed_validation))


def test_decisions_json_is_stable_and_serialisable() -> None:
    document = _document({"doc_no": "INV-000123"})
    result = decide(
        document,
        vision=_check(_vision_field("doc_no", "INV-000123")),
        crops=None,
        hints=ParserHints(doc_type=DocType.INVOICE),
        policy=_policy(),
    )
    first = json.dumps(result.decisions_json(), sort_keys=True)
    assert json.dumps(result.decisions_json(), sort_keys=True) == first


def test_value_agreement_does_not_fold_lookalikes() -> None:
    assert not values_agree("doc_no", "INV-001", "INVO-001")
