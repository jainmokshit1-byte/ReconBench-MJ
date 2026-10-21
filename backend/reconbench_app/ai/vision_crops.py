"""Cropped second pass for dense pages (accuracy plan WI-BE8, §4.5; decision-log D97).

Pass 1 reads every page whole (the v4 prompt, one page per call). A page gets a second
pass only when pass 1 lost it to the answer cap, returned fewer line rows than the rules
reader found on it, or the document's line amounts do not close against its taxable
total. The second pass reads three overlapping horizontal crops of that page with the
same prompt; their answers are moved back to page coordinates and merged into the page:

* header fields and annotations are added as further items of the page; the core
  check then applies the D77 rule (two different values for one field: never both;
  the OCR reading decides or the value stays a ``page_conflict`` suggestion);
* a line row the page already has (same amount, quantity and rate) is not added again;
  a crop row that overlaps another row (IoU >= 0.5) with different values removes both
  (``region_conflict``: no value); a row cut by a crop edge is left to the crop that
  holds it whole;
* ``unreadable`` items and page kinds of a crop are not used (a crop sees part of a page).

Every merged answer then goes through the same parse, checks and safety contract as a
full-page answer. Masked regions stay masked: they are black in the pixels, and their
boxes move into each crop's coordinates.
"""

from __future__ import annotations

import hashlib
import io
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from PIL import Image

from reconbench_app.ai.vision import PageRow
from reconbench_app.extraction.vision import VISION_RENDER

CROP_VERSION = "vision.crops.v1"
CROP_BANDS: tuple[tuple[int, int], ...] = ((0, 400), (300, 700), (600, 1000))
"""Top and bottom of each crop on the 0-1000 page scale: 40 % high, 10 % overlap."""
_SCALE = 1000
_EDGE = 15
"""A crop row whose box comes within this of a cut edge (0-1000 crop scale) is dropped."""
_IOU_SAME_ROW = 0.5
_NUMBER = re.compile(r"[^0-9.]")
CropReason = Literal["cap", "fewer_rows", "totals_open"]


def _crop_masks(boxes: Any, top: int, bottom: int) -> list[list[int]]:
    height = bottom - top
    moved: list[list[int]] = []
    for raw in boxes if isinstance(boxes, list) else []:
        if not (isinstance(raw, list) and len(raw) == 4 and all(isinstance(v, int) for v in raw)):
            continue
        ymin, xmin, ymax, xmax = raw
        if ymax <= top or ymin >= bottom:
            continue
        crop_min = round((max(ymin, top) - top) * _SCALE / height)
        crop_max = round((min(ymax, bottom) - top) * _SCALE / height)
        if crop_max > crop_min:
            moved.append([crop_min, xmin, crop_max, xmax])
    return moved


def crop_band(row: PageRow, top: int, bottom: int) -> PageRow | None:
    """One horizontal crop of a rendered page as its own page image (``None`` without bytes)."""

    if not 0 <= top < bottom <= _SCALE:
        raise ValueError("a crop must lie inside the page")
    if row.data is None or hashlib.sha256(row.data).hexdigest() != row.sha256:
        return None
    with Image.open(io.BytesIO(row.data)) as image:
        image.load()
        width, height = image.size
        upper = round(height * top / _SCALE)
        lower = max(upper + 1, round(height * bottom / _SCALE))
        band = image.crop((0, upper, width, lower)).convert("RGB")
    output = io.BytesIO()
    band.save(output, format="JPEG", quality=VISION_RENDER.jpeg_quality)
    data = output.getvalue()
    return PageRow(
        page=row.page,
        sha256=hashlib.sha256(data).hexdigest(),
        mime_type="image/jpeg",
        width=width,
        height=lower - upper,
        render={
            **dict(row.render),
            "crop": {"version": CROP_VERSION, "top": top, "bottom": bottom},
        },
        mask={**dict(row.mask), "boxes": _crop_masks(row.mask.get("boxes"), top, bottom)},
        data=data,
    )


def _line_value(line: Mapping[str, Any], name: str) -> str:
    return _NUMBER.sub("", str(line.get(name) or ""))


def _line_key(line: Mapping[str, Any]) -> tuple[str, str, str] | None:
    key = (_line_value(line, "amount"), _line_value(line, "quantity"), _line_value(line, "rate"))
    return key if any(key) else None


def crop_reasons(
    page_answer: Mapping[str, Any] | None,
    *,
    page: int,
    rules_rows: int,
    totals_open: bool,
    capped: bool,
) -> tuple[CropReason, ...]:
    """Why a page gets the second pass (empty: it does not)."""

    reasons: list[CropReason] = []
    if capped or page_answer is None:
        reasons.append("cap")
        return tuple(reasons)
    lines = page_answer.get("lines")
    rows = sum(
        1
        for line in (lines if isinstance(lines, list) else [])
        if isinstance(line, Mapping) and line.get("page") == page
    )
    if rows < rules_rows:
        reasons.append("fewer_rows")
    if totals_open and rows:
        reasons.append("totals_open")
    return tuple(reasons)


def totals_open(answer: Mapping[str, Any] | None, *, tolerance_pct: float = 0.5) -> bool:
    """Whether the answer's line amounts fail to close against its taxable total."""

    if answer is None:
        return False
    taxable = None
    for item in answer.get("fields") or []:
        if isinstance(item, Mapping) and item.get("name") == "taxable_total":
            taxable = _number(item.get("text"))
            break
    amounts = [
        _number(line.get("amount"))
        for line in answer.get("lines") or []
        if isinstance(line, Mapping)
    ]
    if taxable is None or not amounts or any(value is None for value in amounts):
        return False
    total = sum(value for value in amounts if value is not None)
    return abs(total - taxable) > max(1.0, abs(taxable) * tolerance_pct / 100)


def _number(text: object) -> float | None:
    cleaned = _NUMBER.sub("", str(text or ""))
    try:
        return float(cleaned) if cleaned and cleaned != "." else None
    except ValueError:
        return None


def _to_page(box: Any, top: int, bottom: int, order: str) -> list[int] | None:
    if not (isinstance(box, list) and len(box) == 4 and all(type(v) is int for v in box)):
        return None
    moved = list(box)
    for index in (0, 2) if order == "yx" else (1, 3):
        moved[index] = round(top + moved[index] * (bottom - top) / _SCALE)
    return moved


def _at_cut_edge(box: list[int], top: int, bottom: int, order: str) -> bool:
    ymin, ymax = (box[0], box[2]) if order == "yx" else (box[1], box[3])
    return (top > 0 and ymin <= _EDGE) or (bottom < _SCALE and ymax >= _SCALE - _EDGE)


def _iou(left: list[int], right: list[int]) -> float:
    y0, x0 = max(left[0], right[0]), max(left[1], right[1])
    y1, x1 = min(left[2], right[2]), min(left[3], right[3])
    inter = max(0, y1 - y0) * max(0, x1 - x0)
    area = (left[2] - left[0]) * (left[3] - left[1]) + (right[2] - right[0]) * (right[3] - right[1])
    return inter / (area - inter) if area - inter > 0 else 0.0


def _note_key(item: Mapping[str, Any], name_key: str) -> tuple[str, str]:
    return (str(item.get(name_key, "")), "".join(str(item.get("text", "")).split()).upper())


def merge_page_crops(
    page_answer: Mapping[str, Any] | None,
    crop_answers: Sequence[tuple[Mapping[str, Any], tuple[int, int]]],
    *,
    page: int,
    box_order: str = "yx",
) -> tuple[dict[str, Any], Counter[str]]:
    """One page's answer with its crops merged in (see the module notes); and counts."""

    stats: Counter[str] = Counter()
    base: dict[str, Any] = {
        "doc_kind": "other",
        "pages": [],
        "fields": [],
        "lines": [],
        "annotations": [],
        "unreadable": [],
    }
    if page_answer is not None:
        base.update({key: value for key, value in page_answer.items()})
        for name in ("pages", "fields", "lines", "annotations", "unreadable"):
            base[name] = [dict(item) for item in base.get(name) or [] if isinstance(item, Mapping)]
    elif crop_answers:
        kinds = [str(answer.get("doc_kind") or "other") for answer, _ in crop_answers]
        base["doc_kind"] = next((kind for kind in kinds if kind != "other"), "other")
        base["pages"] = [{"page": page, "kind": base["doc_kind"], "title": ""}]
    fields_seen = {_note_key(item, "name") for item in base["fields"]}
    notes_seen = {_note_key(item, "looks_like") for item in base["annotations"]}
    lines: list[dict[str, Any] | None] = list(base["lines"])
    for answer, (top, bottom) in crop_answers:
        for name in ("fields", "annotations", "lines"):
            for raw in answer.get(name) or []:
                if not isinstance(raw, Mapping) or raw.get("page") != page:
                    stats["off_page"] += 1
                    continue
                crop_box = raw.get("box_2d")
                box = _to_page(crop_box, top, bottom, box_order)
                if box is None:
                    stats["no_box"] += 1
                    continue
                item = {**dict(raw), "box_2d": box}
                if name == "fields":
                    key = _note_key(item, "name")
                    if key not in fields_seen:
                        fields_seen.add(key)
                        base["fields"].append(item)
                        stats["fields_added"] += 1
                    continue
                if name == "annotations":
                    key = _note_key(item, "looks_like")
                    if key not in notes_seen:
                        notes_seen.add(key)
                        base["annotations"].append(item)
                        stats["annotations_added"] += 1
                    continue
                if _at_cut_edge(list(raw["box_2d"]), top, bottom, box_order):
                    stats["rows_at_cut_edge"] += 1
                    continue
                row_key = _line_key(item)
                duplicate = conflict = False
                for index, other in enumerate(lines):
                    if other is None:
                        continue
                    if row_key is not None and _line_key(other) == row_key:
                        duplicate = True
                        break
                    other_box = other.get("box_2d")
                    if (
                        isinstance(other_box, list)
                        and len(other_box) == 4
                        and _iou(box, other_box) >= _IOU_SAME_ROW
                    ):
                        lines[index] = None
                        conflict = True
                        break
                if duplicate:
                    stats["rows_duplicate"] += 1
                elif conflict:
                    stats["region_conflict"] += 1
                else:
                    lines.append(item)
                    stats["rows_added"] += 1
    base["lines"] = [line for line in lines if line is not None]
    return base, stats


__all__ = [
    "CROP_BANDS",
    "CROP_VERSION",
    "CropReason",
    "crop_band",
    "crop_reasons",
    "merge_page_crops",
    "totals_open",
]
