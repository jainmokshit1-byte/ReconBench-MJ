from __future__ import annotations

import hashlib
from typing import Any

import pytest
from PIL import Image
from reconbench_app.extraction import local_hybrid, vision_pages
from reconbench_app.extraction.vision_pages import CropReadPage
from reconbench_core.extraction.consensus import CropNeed, FieldKey


def test_reread_crop_uses_line_or_block_psm_and_returns_trimmed_text(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    del tmp_path
    calls: list[list[str]] = []

    def tesseract(_path: str, args: list[str], _timeout: int) -> str:
        calls.append(args)
        return "  SYN-INV-0042 \n"

    monkeypatch.setattr(local_hybrid, "_tesseract", tesseract)
    image = Image.new("RGB", (100, 80), "white")
    assert local_hybrid.reread_crop(image, (10, 10, 60, 30), mode="line") == "SYN-INV-0042"
    assert local_hybrid.reread_crop(image, (10, 10, 60, 30), mode="block") == "SYN-INV-0042"
    assert calls[0][:2] == ["--psm", "7"]
    assert calls[1][:2] == ["--psm", "6"]


def test_reread_crop_rejects_invalid_boxes_and_reports_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image = Image.new("RGB", (100, 80), "white")
    assert local_hybrid.reread_crop(image, (10, 10, 110, 30)) is None
    with pytest.raises(ValueError, match="mode"):
        local_hybrid.reread_crop(image, (10, 10, 60, 30), mode="invalid")

    def timeout(*_args: Any) -> str:
        raise local_hybrid.subprocess.TimeoutExpired("tesseract", 15)

    monkeypatch.setattr(local_hybrid, "_tesseract", timeout)
    assert local_hybrid.reread_crop(image, (10, 10, 60, 30)) is None


def test_second_gstin_read_uses_a_character_whitelist(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def tesseract(_path: str, args: list[str], _timeout: int) -> str:
        calls.append(args)
        return "27AABCS1234Q1Z5"

    monkeypatch.setattr(local_hybrid, "_tesseract", tesseract)
    image = Image.new("RGB", (100, 80), "white")
    text = local_hybrid.reread_crop(image, (10, 10, 60, 30), mode="line", kind="gstin", round_no=2)
    assert text == "27AABCS1234Q1Z5"
    assert calls[0] == [
        "--psm",
        "7",
        "-l",
        "eng",
        "-c",
        "tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
    ]


def test_isolated_crop_reader_passes_only_masked_page_bytes_and_checked_boxes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = b"synthetic masked bytes"
    page = CropReadPage(
        page=1,
        data=data,
        sha256=hashlib.sha256(data).hexdigest(),
        width=100,
        height=80,
    )
    need = CropNeed(
        key=FieldKey("header", "doc_no"),
        kind="doc_no",
        value="SYN-INV-0042",
        page=1,
        box=(100, 100, 300, 500),
        mode="line",
    )
    observed: dict[str, Any] = {}

    def run_child(target: Any, arguments: tuple[Any, ...], **options: Any) -> Any:
        observed["target"] = target
        observed["arguments"] = arguments
        observed["options"] = options
        return options["convert"]({"reads": [("header:doc_no", "SYN-INV-0042")]})

    monkeypatch.setattr("reconbench_app.pdf_sandbox._run_child", run_child)
    assert vision_pages.reread_vision_crops_isolated([page], [need]) == {
        "header:doc_no": "SYN-INV-0042"
    }
    assert observed["arguments"][0] == ((1, data, page.sha256, 100, 80),)
    assert observed["arguments"][1] == (("header:doc_no", 1, need.box, "line", "doc_no"),)
    assert observed["arguments"][3] == 1
    assert observed["options"]["name"] == "reconbench-consensus-crops"
