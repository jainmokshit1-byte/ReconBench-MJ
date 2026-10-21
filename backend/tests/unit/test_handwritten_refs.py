"""Typed handwritten references and misread normalisation (accuracy plan WI-Q2, D88/D92).

Synthetic values only; the misread shapes are the ones the stored dev-set answers show.
"""

from __future__ import annotations

from datetime import date

import pytest
from reconbench_core.extraction.handwritten_refs import (
    normalise_text,
    refs_from_annotation,
    short_of,
    typed_refs,
)

AUG = date(2026, 8, 12)


@pytest.mark.parametrize(
    "written",
    [
        "0126-27108-081",
        "P0126-27/08-081",
        "PO 126-27/108-081",
        "PO 26-27/108-081",
        "Po/26-27/08-081",
        "P0/26-27/08-081",
        "0126-27108-08-1",
        "PO 26-27/08-08-1",
        "PO 26-27108-081",
        "P0126-27108-081",
    ],
)
def test_common_po_misreads_normalise_to_one_value(written: str) -> None:
    (ref,) = refs_from_annotation(written, "po_number", doc_date=AUG)
    assert ref.type == "po" and ref.normalised == "PO/26-27/08-081" and ref.short == "08-081"


def test_every_change_is_logged_with_the_text_before_and_after() -> None:
    value, steps = normalise_text("Po 26 27107 - 077")
    assert value == "PO/26-27/07-077"
    assert [step.rule for step in steps] == [
        "spaces_around_separators",
        "fy_space",
        "slash_read_as_1",
        "po_prefix",
    ]
    assert steps[0].before == "Po 26 27107 - 077" and steps[-1].after == value
    assert normalise_text("PO/26-27/08-081") == ("PO/26-27/08-081", ())


def test_a_typed_short_grn_number_keeps_its_model_type() -> None:
    (ref,) = refs_from_annotation("08-072", "grn_number", doc_date=AUG)
    assert (ref.type, ref.normalised, ref.type_source) == ("grn", "08-072", "model")


def test_two_numbers_in_one_note_the_short_one_stays_untyped() -> None:
    po, other = refs_from_annotation("27/08-227 08-210", "note", doc_date=date(2026, 8, 28))
    assert (po.type, po.normalised, po.type_source) == ("po", "PO/26-27/08-227", "pattern")
    assert po.steps[-1].rule == "fy_completed_from_invoice_date"
    assert (other.type, other.normalised, other.type_source) == ("other", "08-210", "none")


def test_service_po_and_grn_markers_type_the_reference() -> None:
    service, other = refs_from_annotation("servicepur/26-27/08-31 08-031", "note", doc_date=AUG)
    assert (service.type, service.normalised) == ("service_po", "SERVICEPUR/26-27/08-31")
    assert other.type == "other"
    (grn,) = refs_from_annotation("GRN/BCSL/25-26/08-042", "unknown", doc_date=AUG)
    assert (grn.type, grn.normalised, grn.short) == ("grn", "GRN/BCSL/25-26/08-042", "08-042")


def test_date_checks_refuse_impossible_or_far_values() -> None:
    (future,) = refs_from_annotation("PO/26-27/11-081", "po_number", doc_date=AUG)
    assert future.normalised is None  # three months after the invoice
    (old_fy,) = refs_from_annotation("PO/25-26/02-081", "po_number", doc_date=AUG)
    assert old_fy.normalised == "PO/25-26/02-081"  # the previous financial year is allowed
    (wrong_fy,) = refs_from_annotation("PO/23-24/08-081", "po_number", doc_date=AUG)
    assert wrong_fy.normalised is None
    (far_grn,) = refs_from_annotation("05-072", "grn_number", doc_date=AUG)
    assert far_grn.normalised is None
    (bad_month,) = refs_from_annotation("PO/26-27/13-081", "po_number", doc_date=AUG)
    assert bad_month.normalised is None


def test_a_pattern_po_that_the_model_called_a_grn_is_untyped() -> None:
    (ref,) = refs_from_annotation("26-27/08-081", "grn_number", doc_date=AUG)
    assert (ref.type, ref.type_source) == ("other", "none")


def test_only_handwritten_or_stamped_annotations_count_and_dicts_carry_the_version() -> None:
    notes = [
        {"kind": "handwritten", "text": "Po/26-27/08-081", "looks_like": "po_number", "page": 1},
        {"kind": "other", "text": "PO/26-27/08-082", "looks_like": "po_number", "page": 1},
        {"kind": "stamp", "text": "RECEIVED", "looks_like": "note", "page": 1},
    ]
    refs = typed_refs(notes, doc_date=AUG)
    assert [ref.normalised for ref in refs] == ["PO/26-27/08-081"]
    payload = refs[0].as_dict()
    assert payload["version"] == "handwritten_refs.v1" and payload["page"] == 1
    assert payload["steps"][0]["rule"] == "po_prefix"


def test_short_of_reads_the_identifying_part() -> None:
    assert short_of("PO/26-27/08-222") == "08-222"
    assert short_of("GRN/BCSL/25-26/08-222") == "08-222"
    assert short_of("PAPL0826/043") is None
    assert short_of(None) is None


def test_vision_document_carries_typed_refs_beside_annotations_never_as_fields() -> None:
    from reconbench_core.extraction.vision import (
        SentPage,
        VisionAnnotation,
        VisionField,
        VisionReading,
        check_reading,
        vision_document,
    )
    from reconbench_core.schema import DocType

    box = (100, 100, 120, 300)
    reading = VisionReading(
        "invoice",
        fields=(VisionField("doc_date", "12/08/2026", 1, box),),
        annotations=(
            VisionAnnotation("handwritten", "P0126-27108-081", "po_number", 1, box),
            VisionAnnotation("handwritten", "08-072", "grn_number", 1, (130, 100, 150, 300)),
            VisionAnnotation(
                "handwritten", "SERVICE 08-031", "service_po_number", 1, (160, 100, 180, 300)
            ),
        ),
    )
    check = check_reading(reading, pages={1: SentPage(1)})
    document = vision_document(check, DocType.INVOICE)
    assert document.header.po_refs is None and document.header.grn_refs is None
    refs = document.header.extras["typed_refs"]
    assert [(item["type"], item["normalised"]) for item in refs] == [
        ("po", "PO/26-27/08-081"),
        ("grn", "08-072"),
        ("service_po", "08-031"),
    ]
    assert refs[0]["steps"] and refs[0]["version"] == "handwritten_refs.v1"
    assert document.header.extras["annotation_refs"] == [
        "P0126-27108-081",
        "08-072",
        "SERVICE 08-031",
    ]
