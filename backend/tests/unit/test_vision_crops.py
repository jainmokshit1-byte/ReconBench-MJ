"""WI-BE8 cropped second pass (D97): crops, page coordinates, row merge, reasons. Synthetic."""

from __future__ import annotations

import hashlib
import io

import pytest
from PIL import Image
from reconbench_app.ai.vision import PageRow
from reconbench_app.ai.vision_crops import (
    CROP_BANDS,
    CROP_VERSION,
    crop_band,
    crop_reasons,
    merge_page_crops,
    totals_open,
)


def _row(boxes: list[list[int]]) -> PageRow:
    image = Image.new("RGB", (400, 1000), "white")
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=90)
    data = buffer.getvalue()
    return PageRow(
        page=2,
        sha256=hashlib.sha256(data).hexdigest(),
        mime_type="image/jpeg",
        width=400,
        height=1000,
        render={"version": "vision.render.v1"},
        mask={"version": "image_mask.v1", "boxes": boxes},
        data=data,
    )


def test_three_overlapping_crops_cover_the_page() -> None:
    assert CROP_BANDS[0][0] == 0 and CROP_BANDS[-1][1] == 1000
    for (_, bottom), (top, _) in zip(CROP_BANDS, CROP_BANDS[1:], strict=False):
        assert top < bottom  # every cut is inside the next crop


def test_a_crop_has_its_own_hash_version_and_masks_in_its_coordinates() -> None:
    row = _row([[350, 0, 450, 1000], [50, 0, 60, 100]])
    crop = crop_band(row, 300, 700)
    assert crop is not None and crop.page == 2 and (crop.width, crop.height) == (400, 400)
    assert crop.render["crop"] == {"version": CROP_VERSION, "top": 300, "bottom": 700}
    assert crop.mask["boxes"] == [[125, 0, 375, 1000]]
    assert crop.data is not None and hashlib.sha256(crop.data).hexdigest() == crop.sha256
    assert crop_band(PageRow(2, row.sha256, "image/jpeg", 400, 1000, {}, {}, None), 0, 400) is None
    with pytest.raises(ValueError):
        crop_band(row, 500, 400)


def _line(amount: str, box: list[int], *, page: int = 2, qty: str = "1") -> dict[str, object]:
    return {
        "page": page,
        "box_2d": box,
        "description": "Item",
        "quantity": qty,
        "rate": amount,
        "amount": amount,
    }


def test_crop_rows_move_to_the_page_and_known_rows_are_not_added_twice() -> None:
    page = {
        "doc_kind": "invoice",
        "pages": [],
        "fields": [],
        "lines": [_line("100", [310, 0, 330, 900])],
        "annotations": [],
        "unreadable": [],
    }
    crop = {
        "doc_kind": "invoice",
        "lines": [
            _line("100", [25, 0, 75, 900]),  # the row pass 1 has (same values)
            _line("250", [500, 0, 550, 900]),  # a row pass 1 missed
            _line("999", [5, 0, 10, 900]),  # cut by the crop's top edge
            _line("300", [500, 0, 550, 900], page=1),  # another page
        ],
    }
    merged, stats = merge_page_crops(page, [(crop, (300, 700))], page=2)
    assert [line["amount"] for line in merged["lines"]] == ["100", "250"]
    assert merged["lines"][1]["box_2d"] == [500, 0, 520, 900]
    assert stats["rows_duplicate"] == 1 and stats["rows_added"] == 1
    assert stats["rows_at_cut_edge"] == 1 and stats["off_page"] == 1


def test_an_overlapping_row_with_other_values_removes_both() -> None:
    page = {"lines": [_line("100", [310, 0, 330, 900])]}
    crop = {"lines": [_line("180", [25, 0, 75, 900])]}
    merged, stats = merge_page_crops(page, [(crop, (300, 700))], page=2)
    assert merged["lines"] == [] and stats["region_conflict"] == 1


def test_fields_and_notes_are_added_once_and_unreadable_items_are_not_used() -> None:
    page = {"fields": [{"name": "doc_no", "text": "INV 1", "page": 2, "box_2d": [1, 1, 2, 2]}]}
    crop = {
        "fields": [
            {"name": "doc_no", "text": "INV1", "page": 2, "box_2d": [100, 1, 200, 2]},
            {"name": "taxable_total", "text": "350", "page": 2, "box_2d": [900, 1, 950, 2]},
        ],
        "unreadable": [{"name": "doc_date", "page": 2, "box_2d": [1, 1, 2, 2]}],
    }
    merged, stats = merge_page_crops(page, [(crop, (600, 1000))], page=2)
    assert [f["name"] for f in merged["fields"]] == ["doc_no", "taxable_total"]
    assert merged["fields"][1]["box_2d"] == [960, 1, 980, 2]
    assert merged["unreadable"] == [] and stats["fields_added"] == 1


def test_a_page_lost_to_the_cap_is_rebuilt_from_its_crops() -> None:
    crop = {"doc_kind": "invoice", "lines": [_line("100", [100, 0, 150, 900])]}
    merged, _ = merge_page_crops(None, [(crop, (0, 400))], page=2)
    assert merged["doc_kind"] == "invoice" and merged["pages"][0]["page"] == 2
    assert merged["lines"][0]["box_2d"] == [40, 0, 60, 900]


def test_reasons_for_a_second_pass() -> None:
    answer = {"lines": [_line("100", [1, 1, 2, 2])]}
    assert crop_reasons(None, page=2, rules_rows=0, totals_open=False, capped=True) == ("cap",)
    assert crop_reasons(answer, page=2, rules_rows=3, totals_open=False, capped=False) == (
        "fewer_rows",
    )
    assert crop_reasons(answer, page=2, rules_rows=1, totals_open=True, capped=False) == (
        "totals_open",
    )
    assert crop_reasons(answer, page=2, rules_rows=1, totals_open=False, capped=False) == ()


def test_totals_close_or_not() -> None:
    fields = [{"name": "taxable_total", "text": "1,000.00"}]
    closed = {"fields": fields, "lines": [{"amount": "600"}, {"amount": "400.00"}]}
    assert not totals_open(closed)
    assert totals_open({"fields": fields, "lines": [{"amount": "600"}]})
    assert not totals_open({"fields": [], "lines": [{"amount": "600"}]})
    assert not totals_open(None)
