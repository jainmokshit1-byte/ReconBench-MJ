"""Header band of page 1 (accuracy plan WI-Q2): crop, mask boxes and hashes. Synthetic image."""

from __future__ import annotations

import hashlib
import io

import pytest
from PIL import Image
from reconbench_app.ai.vision import PageRow
from reconbench_app.ai.vision_bands import BAND_TOP, BAND_VERSION, header_band


def _row(boxes: list[list[int]]) -> PageRow:
    image = Image.new("RGB", (400, 1000), "white")
    image.paste((0, 0, 0), (40, 30, 120, 60))  # a masked region near the top
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=90)
    data = buffer.getvalue()
    return PageRow(
        page=1,
        sha256=hashlib.sha256(data).hexdigest(),
        mime_type="image/jpeg",
        width=400,
        height=1000,
        render={"version": "vision.render.v1", "rotation": 0},
        mask={"version": "image_mask.v1", "boxes": boxes},
        data=data,
    )


def test_the_band_is_the_top_of_the_page_with_its_own_hash_and_version() -> None:
    row = _row([[30, 100, 60, 300]])
    band = header_band(row)
    assert band is not None and band.page == 1 and band.mime_type == "image/jpeg"
    assert (band.width, band.height) == (400, 350) and band.sha256 != row.sha256
    assert band.data is not None and hashlib.sha256(band.data).hexdigest() == band.sha256
    assert band.render["band"] == {"version": BAND_VERSION, "top": BAND_TOP}
    with Image.open(io.BytesIO(band.data)) as image:
        assert image.size == (400, 350)


def test_mask_boxes_move_into_band_coordinates_and_boxes_below_the_band_go() -> None:
    row = _row([[35, 100, 70, 300], [300, 0, 400, 1000], [600, 0, 700, 1000]])
    band = header_band(row)
    assert band is not None
    assert band.mask["boxes"] == [[100, 100, 200, 300], [857, 0, 1000, 1000]]
    assert band.sent_page.masks[0].as_list() == [100, 100, 200, 300]


def test_no_bytes_or_a_changed_image_gives_no_band() -> None:
    row = _row([])
    assert header_band(PageRow(1, row.sha256, "image/jpeg", 400, 1000, {}, {}, None)) is None
    assert header_band(PageRow(1, "0" * 64, "image/jpeg", 400, 1000, {}, {}, row.data)) is None
    with pytest.raises(ValueError):
        header_band(row, top=0)
