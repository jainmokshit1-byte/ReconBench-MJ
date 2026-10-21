"""v4 full pages + the page-1 header band as the default Qwen reading shape (WI-Q2, D93).

Synthetic answers only: the band answer adds annotations (moved to page 1, boxes rescaled),
never a field or a line, and a broken band answer never fails the reading.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest
from reconbench_app.ai.contracts import InvalidOutput
from reconbench_app.ai.vision import merge_answers_with_band, merge_page_answers, reader_params
from reconbench_app.extraction.vision import BAND_DOC_TYPES, BAND_TOP, BAND_VERSION, vision_reader


def _page(page: int, *, notes: list[dict[str, object]] | None = None) -> str:
    return json.dumps(
        {
            "doc_kind": "invoice",
            "pages": [{"page": page, "kind": "invoice", "title": ""}],
            "fields": [
                {
                    "name": "doc_no",
                    "text": f"INV-{page}",
                    "page": page,
                    "box_2d": [10, 10, 20, 50],
                    "label": "Invoice No",
                }
            ],
            "lines": [],
            "annotations": notes or [],
            "unreadable": [],
        }
    )


def _band(notes: list[dict[str, object]], *, field: bool = True) -> str:
    return json.dumps(
        {
            "doc_kind": "invoice",
            "pages": [{"page": 1, "kind": "invoice", "title": ""}],
            "fields": (
                [
                    {
                        "name": "doc_no",
                        "text": "BAND-VALUE",
                        "page": 1,
                        "box_2d": [100, 10, 200, 50],
                        "label": "Invoice No",
                    }
                ]
                if field
                else []
            ),
            "lines": [],
            "annotations": notes,
            "unreadable": [],
        }
    )


def _note(text: str, looks_like: str, box: list[int], page: int = 1) -> dict[str, object]:
    return {
        "kind": "handwritten",
        "text": text,
        "looks_like": looks_like,
        "page": page,
        "box_2d": box,
    }


def test_only_the_qwen_reader_carries_the_band_and_only_it_changes_its_params() -> None:
    qwen = vision_reader("vision_openai_compat")
    gemini = vision_reader("vision_gemini")
    assert qwen is not None and gemini is not None
    assert qwen.prompt_variant == "qwen" and qwen.band_prompt_variant == "qwen_q2"
    assert gemini.band_prompt_variant is None and "band" not in reader_params(gemini)
    assert reader_params(qwen)["band"] == {
        "prompt_variant": "qwen_q2",
        "top": BAND_TOP,
        "version": BAND_VERSION,
    }
    # Without the band the params are exactly the full-page reader's (bench keys unchanged).
    plain = reader_params(replace(qwen, band_prompt_variant=None))
    assert {k: v for k, v in reader_params(qwen).items() if k != "band"} == plain
    assert frozenset({"invoice"}) == BAND_DOC_TYPES


def test_band_annotations_join_page_one_with_rescaled_boxes_and_no_field() -> None:
    band = _band([_note("PO/26-27/08-081", "po_number", [100, 600, 200, 900])])
    merged = json.loads(
        merge_answers_with_band([_page(1), _page(2), band], pages=[1, 2], band_page=1, band_top=350)
    )
    plain = json.loads(merge_page_answers([_page(1), _page(2)], pages=[1, 2]))
    assert merged["fields"] == plain["fields"] and merged["lines"] == plain["lines"]
    assert merged["annotations"] == [_note("PO/26-27/08-081", "po_number", [35, 600, 70, 900])]


def test_xy_boxes_rescale_their_vertical_edges() -> None:
    merged = json.loads(
        merge_answers_with_band(
            [_page(1), _band([_note("08-210", "grn_number", [600, 100, 900, 200])])],
            pages=[1],
            band_page=1,
            band_top=350,
            box_order="xy",
        )
    )
    assert merged["annotations"][0]["box_2d"] == [600, 35, 900, 70]


def test_a_note_the_page_already_holds_is_not_added_twice() -> None:
    already = _note("PO/26-27/08-081", "po_number", [30, 600, 70, 900])
    merged = json.loads(
        merge_answers_with_band(
            [
                _page(1, notes=[already]),
                _band(
                    [
                        _note("PO/26-27/ 08-081", "po_number", [100, 600, 200, 900]),
                        _note("08-210", "grn_number", [210, 600, 260, 800]),
                        _note("elsewhere", "other", [10, 10, 20, 20], page=2),
                        {"kind": "handwritten", "text": "no box", "looks_like": "other", "page": 1},
                    ]
                ),
            ],
            pages=[1],
            band_page=1,
        )
    )
    assert [note["text"] for note in merged["annotations"]] == ["PO/26-27/08-081", "08-210"]


@pytest.mark.parametrize("band", ["not json", '{"doc_kind": 7}', ""])
def test_a_broken_band_answer_is_ignored(band: str) -> None:
    merged = merge_answers_with_band([_page(1), band], pages=[1], band_page=1)
    assert json.loads(merged) == json.loads(merge_page_answers([_page(1)], pages=[1]))


def test_one_answer_per_page_and_one_band_answer_are_required() -> None:
    with pytest.raises(InvalidOutput):
        merge_answers_with_band([_page(1)], pages=[1], band_page=1)
    with pytest.raises(InvalidOutput):
        merge_answers_with_band(["not json", _band([])], pages=[1], band_page=1)
