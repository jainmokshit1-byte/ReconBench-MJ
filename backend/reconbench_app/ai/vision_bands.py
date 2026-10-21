"""The header band of page 1, read a second time for handwriting (accuracy plan WI-Q2).

Handwritten PO/GRN numbers sit in the margin or header of the first page. A second read
of that band alone gives the model the handwriting at a larger share of the image. The
page images are already upright (the sandbox turns a rotated scan by the OCR orientation
check and straightens its skew), so the band is the top of the rendered page. The masked
regions stay masked: they are already blacked out in the pixels, and their boxes are
moved into the band's coordinates so a value inside one is still dropped. WI-BE8 (cropped
two-pass reading) reuses this module.
"""

from __future__ import annotations

import hashlib
import io
from typing import Any

from PIL import Image

from reconbench_app.ai.vision import PageRow
from reconbench_app.extraction.vision import BAND_TOP, BAND_VERSION, VISION_RENDER

_SCALE = 1000


def _band_masks(boxes: Any, top: int) -> list[list[int]]:
    moved: list[list[int]] = []
    for raw in boxes if isinstance(boxes, list) else []:
        if not (isinstance(raw, list) and len(raw) == 4 and all(isinstance(v, int) for v in raw)):
            continue
        ymin, xmin, ymax, xmax = raw
        if ymin >= top:
            continue
        band_min = round(ymin * _SCALE / top)
        band_max = min(_SCALE, round(min(ymax, top) * _SCALE / top))
        if band_max > band_min:
            moved.append([band_min, xmin, band_max, xmax])
    return moved


def header_band(row: PageRow, *, top: int = BAND_TOP) -> PageRow | None:
    """The top band of one rendered page as its own page image (``None`` without bytes)."""

    if not 0 < top < _SCALE:
        raise ValueError("the band must end inside the page")
    if row.data is None or hashlib.sha256(row.data).hexdigest() != row.sha256:
        return None
    with Image.open(io.BytesIO(row.data)) as image:
        image.load()
        width, height = image.size
        cut = max(1, round(height * top / _SCALE))
        band = image.crop((0, 0, width, cut)).convert("RGB")
    output = io.BytesIO()
    band.save(output, format="JPEG", quality=VISION_RENDER.jpeg_quality)
    data = output.getvalue()
    return PageRow(
        page=row.page,
        sha256=hashlib.sha256(data).hexdigest(),
        mime_type="image/jpeg",
        width=width,
        height=cut,
        render={**dict(row.render), "band": {"version": BAND_VERSION, "top": top}},
        mask={**dict(row.mask), "boxes": _band_masks(row.mask.get("boxes"), top)},
        data=data,
    )


__all__ = ["BAND_TOP", "BAND_VERSION", "header_band"]
