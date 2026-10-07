"""Local hybrid engine: text-layer words for digital pages, Tesseract for scans.

A page counts as scanned when one image covers most of it. Scanners such as
Epson add their own invisible OCR text over the image; that text is usually
poor, so scanned pages are always re-read here: render at 300 DPI, detect
orientation, straighten small scanner skew, remove long printed rules (table
grid lines confuse OCR), then run Tesseract in sparse-text mode and keep each
word's box and confidence.
Detected rules are returned too, because gridded tables use them as columns.
"""

from __future__ import annotations

import csv
import io
import os
import subprocess
import tempfile
from dataclasses import dataclass

import numpy as np
import pdfplumber
import pypdfium2  # type: ignore[import-untyped]
from PIL import Image
from reconbench_core.extraction.layout import DocumentLayout, PageLayout, PageSource, Rule, Word

DPI = 300
_POINTS_PER_INCH = 72
SCANNED_IMAGE_COVERAGE = 0.5
_RULE_MIN_FRACTION = 0.06
_DARK = 160
_MAX_SKEW_DEGREES = 5.0
# Grid borders as short as one table row (~2 text rows at 300 DPI) are removed
# when they touch a horizontal rule within this many pixels.
_GRID_RULE_MIN_PX = 60
_GRID_TOUCH_PX = 4
# Leftover fragments of table borders that OCR reads as standalone tokens.
_RULE_GLYPHS = frozenset("|_¦")
_OCR_TIMEOUT_SECONDS = 60
_OSD_TIMEOUT_SECONDS = 20


@dataclass(frozen=True, slots=True)
class OcrSettings:
    languages: tuple[str, ...] = ("eng",)
    psm: int = 11
    timeout_seconds: int = _OCR_TIMEOUT_SECONDS


def _scale(value: float) -> int:
    return round(value * DPI / _POINTS_PER_INCH)


def is_scanned(page: pdfplumber.page.Page) -> bool:
    area = float(page.width * page.height) or 1.0
    for image in page.images:
        width = float(image["x1"]) - float(image["x0"])
        height = float(image["bottom"]) - float(image["top"])
        if width * height / area >= SCANNED_IMAGE_COVERAGE:
            return True
    return False


def text_layer_words(page: pdfplumber.page.Page) -> tuple[Word, ...]:
    words = page.extract_words(keep_blank_chars=False, use_text_flow=False)
    return tuple(
        Word(
            text=str(word["text"]),
            x0=_scale(float(word["x0"])),
            y0=_scale(float(word["top"])),
            x1=_scale(float(word["x1"])),
            y1=_scale(float(word["bottom"])),
        )
        for word in words
        if str(word["text"]).strip()
    )


def _runs(mask: np.ndarray, min_len: int) -> list[tuple[int, int]]:
    padded = np.concatenate(([0], mask.astype(np.int8), [0]))
    edges = np.flatnonzero(np.diff(padded))
    return [
        (int(start), int(end))
        for start, end in zip(edges[::2], edges[1::2], strict=True)
        if end - start >= min_len
    ]


def remove_rules(gray: np.ndarray) -> tuple[np.ndarray, tuple[Rule, ...]]:
    """Whiten long horizontal and vertical dark runs; return them as rules."""

    dark = gray < _DARK
    cleaned = gray.copy()
    rules: list[Rule] = []
    min_h = max(40, int(gray.shape[1] * _RULE_MIN_FRACTION))
    min_v = max(40, int(gray.shape[0] * _RULE_MIN_FRACTION / 2))
    horizontal: list[Rule] = []
    for y in np.flatnonzero(dark.sum(axis=1) >= min_h):
        for start, end in _runs(dark[y], min_h):
            horizontal.append(Rule(start, int(y), end, int(y)))
    for rule in horizontal:
        cleaned[rule.y0, rule.x0 : rule.x1] = 255
    rules.extend(horizontal)
    for x in np.flatnonzero(dark.sum(axis=0) >= _GRID_RULE_MIN_PX):
        for start, end in _runs(dark[:, x], _GRID_RULE_MIN_PX):
            # Long runs are rules anywhere. Shorter ones, such as the cell
            # borders of a one-row table, only count when they meet a
            # horizontal rule; letter stems never do.
            if end - start < min_v and not _meets_horizontal(int(x), start, end, horizontal):
                continue
            cleaned[start:end, x] = 255
            rules.append(Rule(int(x), start, int(x), end))
    return cleaned, _merge_rules(rules)


def _meets_horizontal(x: int, start: int, end: int, horizontal: list[Rule]) -> bool:
    return any(
        rule.x0 - _GRID_TOUCH_PX <= x <= rule.x1 + _GRID_TOUCH_PX
        and (abs(rule.y0 - start) <= _GRID_TOUCH_PX or abs(rule.y0 - (end - 1)) <= _GRID_TOUCH_PX)
        for rule in horizontal
    )


def _merge_rules(rules: list[Rule]) -> tuple[Rule, ...]:
    """Collapse the many 1-pixel rows of a thick printed line into one rule."""

    merged: list[Rule] = []
    for rule in sorted(rules, key=lambda r: (r.horizontal, r.y0, r.x0, r.y1)):
        if merged:
            last = merged[-1]
            if (
                rule.horizontal
                and last.horizontal
                and rule.y0 - last.y0 <= 3
                and (abs(rule.x0 - last.x0) <= 6 and abs(rule.x1 - last.x1) <= 6)
            ):
                continue
            if (
                not rule.horizontal
                and not last.horizontal
                and rule.x0 - last.x0 <= 3
                and (abs(rule.y0 - last.y0) <= 6 and abs(rule.y1 - last.y1) <= 6)
            ):
                continue
        merged.append(rule)
    return tuple(merged)


def _profile_score(ink: Image.Image, angle: float) -> float:
    rotated = ink.rotate(angle, resample=Image.Resampling.BILINEAR, fillcolor=0)
    profile = np.asarray(rotated, dtype=np.float64).sum(axis=1)
    return float(np.square(np.diff(profile)).sum())


def estimate_skew(gray: Image.Image) -> float:
    """Angle in degrees (PIL ``rotate`` convention) that levels the text rows.

    Projection-profile search: text rows and table rules give the sharpest
    horizontal profile when level. A coarse 0.5 degree sweep within +/-5
    degrees is refined to 0.1 degree; anything under 0.1 degree is left alone.
    """

    small = gray.reduce(2) if min(gray.size) > 1200 else gray
    ink = Image.fromarray(((np.asarray(small) < _DARK) * 255).astype(np.uint8))
    if not np.asarray(ink).any():
        return 0.0
    limit = int(_MAX_SKEW_DEGREES * 2)
    coarse = [step / 2 for step in range(-limit, limit + 1)]
    best = max(coarse, key=lambda angle: (_profile_score(ink, angle), -abs(angle)))
    fine = [round(best + step / 10, 1) for step in range(-5, 6)]
    best = max(fine, key=lambda angle: (_profile_score(ink, angle), -abs(angle)))
    return best if abs(best) >= 0.1 else 0.0


def _tesseract(image_path: str, arguments: list[str], timeout: int) -> str:
    completed = subprocess.run(  # noqa: S603 - fixed binary, controlled arguments
        ["tesseract", image_path, "-", *arguments],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    return completed.stdout


def _orientation(image_path: str) -> int:
    """Clockwise rotation (0/90/180/270) Tesseract recommends, 0 when unsure."""

    try:
        output = _tesseract(image_path, ["--psm", "0"], _OSD_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        return 0
    rotate = 0
    confidence = 0.0
    for line in output.splitlines():
        if line.startswith("Rotate:"):
            rotate = int(line.split(":", 1)[1].strip() or 0)
        elif line.startswith("Orientation confidence:"):
            confidence = float(line.split(":", 1)[1].strip() or 0)
    return rotate if rotate in {90, 180, 270} and confidence >= 2.0 else 0


def ocr_page(image: Image.Image, number: int, settings: OcrSettings) -> PageLayout:
    layout, _gray = ocr_page_with_image(image, number, settings)
    return layout


def ocr_page_with_image(
    image: Image.Image, number: int, settings: OcrSettings
) -> tuple[PageLayout, Image.Image]:
    with tempfile.TemporaryDirectory(prefix="reconbench-ocr-") as directory:
        path = os.path.join(directory, "page.png")
        gray = image.convert("L")
        gray.save(path)
        rotation = _orientation(path)
        if rotation:
            gray = gray.rotate(-rotation, expand=True)
        skew = estimate_skew(gray)
        if skew:
            gray = gray.rotate(skew, resample=Image.Resampling.BICUBIC, fillcolor=255)
        cleaned, rules = remove_rules(np.array(gray))
        Image.fromarray(cleaned).save(path)
        tsv = _tesseract(
            path,
            [
                "--psm",
                str(settings.psm),
                "-l",
                "+".join(settings.languages),
                "-c",
                "preserve_interword_spaces=1",
                "tsv",
            ],
            settings.timeout_seconds,
        )
    words: list[Word] = []
    for row in csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE):
        text = (row.get("text") or "").strip()
        if row.get("level") != "5" or not text or set(text) <= _RULE_GLYPHS:
            continue
        left, top = int(row["left"]), int(row["top"])
        words.append(
            Word(
                text=text,
                x0=left,
                y0=top,
                x1=left + int(row["width"]),
                y1=top + int(row["height"]),
                conf=max(0, min(100, round(float(row["conf"])))),
            )
        )
    return (
        PageLayout(
            number=number,
            width=gray.width,
            height=gray.height,
            words=tuple(words),
            source=PageSource.OCR,
            rules=rules,
            rotation=rotation,
        ),
        gray,
    )


class LocalHybridEngine:
    name = "local_hybrid"

    def __init__(self, *, languages: tuple[str, ...] = ("eng",)) -> None:
        self.settings = OcrSettings(languages=languages)
        self._images: dict[int, Image.Image] = {}

    def reread(self, page: int, bbox: tuple[int, ...]) -> str | None:
        """Second, independent reading of one field: 2x crop, single-line mode."""

        image = self._images.get(page)
        if image is None or len(bbox) != 4:
            return None
        x0, y0, x1, y1 = bbox
        margin = max(6, (y1 - y0) // 3)
        crop = image.crop(
            (
                max(0, x0 - margin),
                max(0, y0 - margin),
                min(image.width, x1 + margin),
                min(image.height, y1 + margin),
            )
        )
        crop = crop.resize((crop.width * 2, crop.height * 2), Image.Resampling.LANCZOS)
        with tempfile.TemporaryDirectory(prefix="reconbench-reread-") as directory:
            path = os.path.join(directory, "field.png")
            crop.save(path)
            try:
                text = _tesseract(
                    path,
                    ["--psm", "7", "-l", "+".join(self.settings.languages)],
                    _OSD_TIMEOUT_SECONDS,
                )
            except subprocess.TimeoutExpired:
                return None
        return text.strip() or None

    def layout(self, content: bytes, *, max_pages: int) -> DocumentLayout:
        pages: list[PageLayout] = []
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            page_count = len(pdf.pages)
            scanned = [is_scanned(page) for page in pdf.pages[:max_pages]]
            for index, page in enumerate(pdf.pages[:max_pages]):
                if not scanned[index]:
                    words = text_layer_words(page)
                    pages.append(
                        PageLayout(
                            number=index + 1,
                            width=_scale(float(page.width)),
                            height=_scale(float(page.height)),
                            words=words,
                            source=PageSource.TEXT_LAYER,
                        )
                    )
        if any(scanned):
            document = pypdfium2.PdfDocument(content)
            try:
                for index, is_scan in enumerate(scanned):
                    if not is_scan:
                        continue
                    image = document[index].render(scale=DPI / _POINTS_PER_INCH).to_pil()
                    layout, gray = ocr_page_with_image(image, index + 1, self.settings)
                    self._images[index + 1] = gray
                    pages.append(layout)
            finally:
                document.close()
        pages.sort(key=lambda page: page.number)
        return DocumentLayout(pages=tuple(pages), page_count=page_count)


__all__ = [
    "DPI",
    "LocalHybridEngine",
    "OcrSettings",
    "estimate_skew",
    "is_scanned",
    "ocr_page",
    "remove_rules",
]
