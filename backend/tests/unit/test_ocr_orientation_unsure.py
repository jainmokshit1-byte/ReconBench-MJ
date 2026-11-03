"""A page whose orientation Tesseract could not confirm is flagged, not silently trusted."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from PIL import Image
from reconbench_app.extraction import local_hybrid
from reconbench_app.extraction.local_hybrid import OcrSettings, ocr_page_with_image
from reconbench_core.extraction.layout import (
    DocumentLayout,
    PageLayout,
    PageSource,
    layout_from_dict,
    layout_to_dict,
)


def _osd(monkeypatch: pytest.MonkeyPatch, output: str | Exception) -> None:
    def fake(image_path: str, arguments: list[str], timeout: int) -> str:
        if arguments[:2] == ["--psm", "0"]:
            if isinstance(output, Exception):
                raise output
            return output
        return "level\tleft\ttop\twidth\theight\tconf\ttext\n"

    monkeypatch.setattr(local_hybrid, "_tesseract", fake)


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("Rotate: 180\nOrientation confidence: 9.50\n", (180, False)),
        ("Rotate: 0\nOrientation confidence: 7.10\n", (0, False)),
        ("Rotate: 90\nOrientation confidence: 1.20\n", (0, True)),
        ("", (0, True)),
        ("Rotate: x\nOrientation confidence: 9\n", (0, True)),
        (subprocess.TimeoutExpired("tesseract", 1), (0, True)),
    ],
)
def test_orientation_reports_when_it_could_not_decide(
    monkeypatch: pytest.MonkeyPatch, output: str | Exception, expected: tuple[int, bool]
) -> None:
    _osd(monkeypatch, output)

    assert local_hybrid._orientation("page.png") == expected


def test_an_undecided_page_keeps_rotation_zero_and_carries_the_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _osd(monkeypatch, "Rotate: 90\nOrientation confidence: 0.5\n")

    layout, _gray = ocr_page_with_image(Image.new("RGB", (60, 40), "white"), 3, OcrSettings())

    assert layout.rotation == 0
    assert layout.orientation_unsure is True


def test_a_confirmed_page_has_no_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    _osd(monkeypatch, "Rotate: 0\nOrientation confidence: 8.0\n")

    layout, _gray = ocr_page_with_image(Image.new("RGB", (60, 40), "white"), 1, OcrSettings())

    assert layout.orientation_unsure is False


def test_the_flag_survives_the_layout_round_trip() -> None:
    page = PageLayout(2, 100, 100, (), PageSource.OCR, orientation_unsure=True)
    layout = DocumentLayout((page,), 1)

    assert layout_from_dict(layout_to_dict(layout)) == layout
    clean = DocumentLayout((PageLayout(1, 100, 100, (), PageSource.OCR),), 1)
    assert "orientation_unsure" not in layout_to_dict(clean)["pages"][0]  # type: ignore[index]


def test_the_pipeline_warns_with_the_page_number(tmp_path: Path) -> None:
    from reconbench_app.extraction import pipeline  # noqa: PLC0415

    page = PageLayout(2, 100, 100, (), PageSource.OCR, orientation_unsure=True)
    warnings = pipeline.orientation_warnings(DocumentLayout((page,), 2))

    assert [(w.code, w.message) for w in warnings] == [
        (
            "OCR_ORIENTATION_UNSURE",
            "Page 2 orientation could not be confirmed; check the page is upright.",
        )
    ]
