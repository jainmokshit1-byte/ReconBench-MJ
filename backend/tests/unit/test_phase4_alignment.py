"""Deterministic weighted item alignment and its quantity/rate-independent scores."""

from __future__ import annotations

from decimal import Decimal

import pytest
from reconbench_core.align import (
    AlignmentConfig,
    AlignmentResult,
    AlignmentWeights,
    align_lines,
    spec_tokens_conflict,
)
from reconbench_core.normalize import normalize_item_text
from reconbench_core.schema import (
    AlignmentState,
    DocLine,
    FieldEvidence,
    FieldSource,
    FieldValue,
    LineType,
)


def _field[T](value: T) -> FieldValue[T]:
    return FieldValue(
        value=value,
        confidence=Decimal("1"),
        source=FieldSource.RULE,
        evidence=FieldEvidence(snippet=str(value)),
    )


def _line(
    line_no: int,
    description: str,
    *,
    code: str | None = None,
    hsn: str | None = None,
    uom: str | None = None,
    quantity: str | None = None,
    rate: str | None = None,
    line_type: LineType = LineType.GOODS,
) -> DocLine:
    return DocLine(
        line_no=line_no,
        line_type=_field(line_type),
        description=_field(description),
        item_code=_field(code) if code is not None else None,
        hsn_sac=_field(hsn) if hsn is not None else None,
        uom=_field(uom) if uom is not None else None,
        quantity=_field(Decimal(quantity)) if quantity is not None else None,
        rate=_field(Decimal(rate)) if rate is not None else None,
    )


def _config() -> AlignmentConfig:
    return AlignmentConfig(
        weights={"desc": "0.55", "code": "0.20", "hsn": "0.15", "uom": "0.10"},
        item_aligned="0.80",
        item_review="0.60",
        item_single_line_explicit="0.50",
        spec_conflict_cap="0.60",
        uom_aliases={"PCS": "NOS", "KGS": "KG", "M": "MTR"},
    )


def test_conflicting_product_specs_cap_description_similarity() -> None:
    invoice = _line(1, "Acer laptop A515-58 16GB RAM")
    po = _line(1, "Acer laptop A315-58 8GB RAM")

    result = align_lines((invoice,), (po,), _config())[0]

    assert result.score == Decimal("0.6000")
    assert result.score < Decimal("0.80")
    assert result.state is AlignmentState.UNSURE


def test_rate_and_quantity_do_not_change_similarity_or_threshold_result() -> None:
    baseline = _line(1, "Acer laptop A515-58 16GB", quantity="1", rate="100")
    changed_price = _line(1, "Acer laptop A515-58 16GB", quantity="20", rate="900")
    po = _line(7, "Acer laptop A515-58 16GB", quantity="1", rate="100")

    baseline_result = align_lines((baseline,), (po,), _config())[0]
    changed_result = align_lines((changed_price,), (po,), _config())[0]

    assert baseline_result.score == changed_result.score == Decimal("1.0000")
    assert baseline_result.state is changed_result.state is AlignmentState.ALIGNED


def test_shuffle_of_input_lines_does_not_change_assignment() -> None:
    invoice_lines = (
        _line(2, "Laptop Acer A315-58 8GB RAM", code="A2", hsn="8471", uom="PCS"),
        _line(1, "Laptop Acer A515-58 16GB RAM", code="A1", hsn="8471", uom="NOS"),
    )
    po_lines = (
        _line(22, "Laptop Acer A315-58 8GB RAM", code="A2", hsn="8471", uom="NOS"),
        _line(11, "Laptop Acer A515-58 16GB RAM", code="A1", hsn="8471", uom="PCS"),
    )

    normal = align_lines(invoice_lines, po_lines, _config())
    shuffled = align_lines(tuple(reversed(invoice_lines)), tuple(reversed(po_lines)), _config())

    assert normal == shuffled
    assert [(row.invoice_line_no, row.po_line_no) for row in normal] == [(1, 11), (2, 22)]
    assert all(row.state is AlignmentState.ALIGNED for row in normal)


def test_unassigned_invoice_lines_can_share_a_po_line_after_global_assignment() -> None:
    invoices = (
        _line(1, "Acer laptop A515-58 16GB", code="A1", hsn="8471", uom="NOS"),
        _line(2, "Acer laptop A515-58 16GB", code="A1", hsn="8471", uom="NOS"),
    )
    po_lines = (_line(10, "Acer laptop A515-58 16GB", code="A1", hsn="8471", uom="PCS"),)

    results = align_lines(invoices, po_lines, _config())

    assert [result.po_line_no for result in results] == [10, 10]
    assert all(result.state is AlignmentState.ALIGNED for result in results)


def test_explicit_single_line_link_relaxes_match_floor_and_charge_lines_skip_alignment() -> None:
    invoice = _line(1, "Acer laptop", line_type=LineType.GOODS)
    unrelated_po = _line(8, "Acer notebook", line_type=LineType.GOODS)
    charge = _line(2, "Freight", line_type=LineType.CHARGE)

    relaxed = align_lines((invoice,), (unrelated_po,), _config(), explicit_link=True)[0]
    charge_result = align_lines((charge,), (unrelated_po,), _config())[0]

    assert relaxed.score is not None and Decimal("0.50") <= relaxed.score < Decimal("0.60")
    assert relaxed.po_line_no == 8
    assert relaxed.state is AlignmentState.UNSURE
    assert charge_result.state is AlignmentState.CHARGE
    assert charge_result.po_line_no is None


def test_no_po_candidates_are_marked_unlinked() -> None:
    results = align_lines((_line(1, "Acer laptop"),), (), _config())

    assert results == (
        AlignmentResult(
            invoice_line_no=1,
            po_line_no=None,
            score=None,
            state=AlignmentState.UNLINKED,
        ),
    )


def test_common_uom_and_matching_quantity_rate_are_not_product_identity() -> None:
    invoice = _line(1, "", uom="NOS", quantity="1", rate="500")
    po = _line(7, "", uom="PCS", quantity="1", rate="500")

    result = align_lines((invoice,), (po,), _config())[0]

    assert result.po_line_no is None
    assert result.score == Decimal("0.0000")
    assert result.state is AlignmentState.NOT_ON_PO


def test_hsn_and_uom_without_description_or_item_code_remain_review_only() -> None:
    invoice = _line(1, "", hsn="84717090", uom="NOS")
    po = _line(7, "", hsn="84717090", uom="PCS")

    result = align_lines((invoice,), (po,), _config())[0]

    assert result.po_line_no == 7
    assert result.score == Decimal("0.7999")
    assert result.state is AlignmentState.UNSURE


def test_alignment_config_rejects_invalid_weight_and_threshold_order() -> None:
    with pytest.raises(ValueError, match="sum to 1"):
        AlignmentWeights(desc="0.4", code="0.2", hsn="0.2", uom="0.1")
    with pytest.raises(ValueError, match="thresholds must descend"):
        AlignmentConfig(
            weights=_config().weights,
            item_aligned="0.60",
            item_review="0.70",
            item_single_line_explicit="0.50",
            spec_conflict_cap="0.50",
        )
    with pytest.raises(ValueError, match="spec-conflict cap"):
        AlignmentConfig(
            weights=_config().weights,
            item_aligned="0.80",
            item_review="0.60",
            item_single_line_explicit="0.50",
            spec_conflict_cap="0.90",
        )


def test_alignment_reports_unmatched_line_when_optional_signals_disagree() -> None:
    invoice = _line(1, "", code="INVOICE-CODE", hsn="8471", uom="BOX")
    po = _line(7, "", code="PO-CODE", hsn="8471", uom="NOS")

    result = align_lines((invoice,), (po,), _config())[0]

    assert result.po_line_no is None
    assert result.score is not None and result.score < Decimal("0.60")
    assert result.state is AlignmentState.NOT_ON_PO


def test_spec_conflict_categories_cover_model_capacity_size_power_dimensions_and_color() -> None:
    left = normalize_item_text("A515-58 16GB 15inch 2kw 600x500x50mm silver")
    right = normalize_item_text("A315-58 8GB 14inch 1kw 600x400x50mm black")

    assert spec_tokens_conflict(left, right)
    assert not spec_tokens_conflict(left, left)


def _pinned(line: DocLine, po_line_no: str, source: FieldSource) -> DocLine:
    ref = _field(po_line_no).model_copy(update={"source": source})
    return line.model_copy(update={"po_line_ref": ref})


def test_a_persons_accepted_item_match_aligns_a_weak_line_to_that_po_line() -> None:
    invoice = _line(1, "", hsn="84717090", uom="NOS")
    pos = (_line(7, "", hsn="84717090", uom="PCS"), _line(8, "Unrelated chair"))

    result = align_lines((_pinned(invoice, "8", FieldSource.MANUAL),), pos, _config())[0]

    assert (result.po_line_no, result.state) == (8, AlignmentState.ALIGNED)


@pytest.mark.parametrize(("ref", "source"), [("8", FieldSource.RULE), ("99", FieldSource.MANUAL)])
def test_a_read_or_unknown_po_line_reference_does_not_pin(ref: str, source: FieldSource) -> None:
    # Only a person's value pins: never a printed or AI-read reference, nor a missing PO line.
    invoice = _line(1, "", hsn="84717090", uom="NOS")
    pos = (_line(7, "", hsn="84717090", uom="PCS"), _line(8, "Unrelated chair"))

    result = align_lines((_pinned(invoice, ref, source),), pos, _config())[0]

    assert (result.po_line_no, result.state) == (7, AlignmentState.UNSURE)


def test_a_serial_number_beside_the_item_is_not_a_spec_conflict() -> None:
    invoice = _line(1, "Gaming RAM DDR5 16GB CL40 25476593921 25476596394")
    po = _line(1, "GAMING RAM DDR5 16GB CL40")

    result = align_lines((invoice,), (po,), _config())[0]

    assert result.state is AlignmentState.ALIGNED


def test_a_word_run_into_a_model_code_is_not_a_spec_conflict() -> None:
    spaced = normalize_item_text("Mid tower cabinet RGB 2024 compact black")
    joined = normalize_item_text("MID TOWER CABINET RGB 2024COMPACT BLACK")
    plain = normalize_item_text("Graphics card RTX 4070")

    assert not spec_tokens_conflict(spaced, joined)
    # A suffix the other side never prints is a different model.
    assert spec_tokens_conflict(normalize_item_text("Graphics card RTX4070super"), plain)


def test_a_long_title_with_equal_quantity_and_rate_is_aligned() -> None:
    title = (
        "Travel pouch organizer 8x5 inch with 1 year warranty portable storage organizer"
        " for cable charger earphone power bank hard disk gadgets travel bag"
    )
    po = _line(
        1, "TRAVEL POUCH ORGANIZER 8X5 INCH WITH 1 YEAR WARRANTY", quantity="1", rate="268.58"
    )

    same = align_lines((_line(1, title, quantity="1", rate="268.58"),), (po,), _config())[0]
    other_rate = align_lines((_line(1, title, quantity="1", rate="270"),), (po,), _config())[0]

    assert same.state is AlignmentState.ALIGNED
    assert same.score == Decimal("0.8000")
    assert other_rate.state is AlignmentState.UNSURE


def test_equal_quantity_and_rate_never_override_a_spec_conflict() -> None:
    invoice = _line(1, "Acer laptop A515-58 16GB RAM", quantity="1", rate="50000")
    po = _line(1, "Acer laptop A515-58 8GB RAM", quantity="1", rate="50000")

    result = align_lines((invoice,), (po,), _config())[0]

    assert result.state is AlignmentState.UNSURE
