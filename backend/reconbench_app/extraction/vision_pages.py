"""Masked page images for the vision reader (accuracy plan AC-5), made in the PDF sandbox.

Each page is rendered at the working resolution and brought into the same
geometry the OCR reader uses: a scanned (or rotated) page is turned upright by
Tesseract's orientation check and straightened by the same skew estimate, so the
OCR word boxes, the masks and the reader's boxes share one coordinate space. A
digital page keeps its text-layer word boxes. Words that hold an e-mail address,
UPI id, phone, bank account, IFSC, PAN or a signature area are blacked out
(``ai.redaction.image_mask_regions``); then the image is scaled to the sent size
and encoded deterministically, so the same page always gives the same hash.

The untrusted PDF is opened only in a separate, limited child process; the
parent receives image bytes and metadata.
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from typing import Any

from PIL import Image, ImageDraw

from reconbench_app.ai.redaction import (
    IMAGE_MASK_VERSION,
    MaskRegion,
    WordBox,
    image_mask_regions,
)
from reconbench_app.ai.vision import RenderedPage
from reconbench_app.extraction.vision import VISION_RENDER, VisionRenderSpec

VISION_CPU_SECONDS = 150
VISION_WALL_SECONDS = 180
_POINTS_PER_INCH = 72


@dataclass(frozen=True, slots=True)
class _Geometry:
    source: str
    rotation: int
    skew: float


def _words_and_image(
    page: Any, document: Any, index: int, spec: VisionRenderSpec, languages: tuple[str, ...]
) -> tuple[list[WordBox], Image.Image, _Geometry]:
    from reconbench_app.extraction.local_hybrid import (
        OcrSettings,
        estimate_skew,
        is_scanned,
        ocr_page_with_image,
        text_layer_words,
    )

    color = document[index].render(scale=spec.work_dpi / _POINTS_PER_INCH).to_pil().convert("RGB")
    rotation = int(getattr(page, "rotation", 0) or 0) % 360
    if not is_scanned(page) and rotation == 0:
        words = [
            WordBox(word.text, word.x0, word.y0, word.x1, word.y1, word.conf)
            for word in text_layer_words(page)
        ]
        return words, color, _Geometry("text_layer", 0, 0.0)
    # Same steps as ``ocr_page_with_image``: orientation, then skew on the upright image.
    layout, gray = ocr_page_with_image(color, index + 1, OcrSettings(languages=languages))
    upright = color.rotate(-layout.rotation, expand=True) if layout.rotation else color
    skew = estimate_skew(upright.convert("L"))
    if skew:
        upright = upright.rotate(skew, resample=Image.Resampling.BICUBIC, fillcolor=(255, 255, 255))
    if upright.size != gray.size:
        upright = upright.resize(gray.size)
    words = [
        WordBox(word.text, word.x0, word.y0, word.x1, word.y1, word.conf) for word in layout.words
    ]
    return words, upright, _Geometry("ocr", layout.rotation, skew)


def _scaled_box(region: MaskRegion, width: int, height: int) -> list[int]:
    """``[ymin, xmin, ymax, xmax]`` on the 0–1000 page scale (the reader's convention)."""

    return [
        region.y0 * 1000 // height,
        region.x0 * 1000 // width,
        -(-region.y1 * 1000 // height),
        -(-region.x1 * 1000 // width),
    ]


def render_page(
    page: Any,
    document: Any,
    index: int,
    *,
    spec: VisionRenderSpec = VISION_RENDER,
    languages: tuple[str, ...] = ("eng",),
) -> RenderedPage:
    words, image, geometry = _words_and_image(page, document, index, spec, languages)
    width, height = image.size
    regions = image_mask_regions(words, width=width, height=height)
    if regions:
        draw = ImageDraw.Draw(image)
        for region in regions:
            draw.rectangle((region.x0, region.y0, region.x1, region.y1), fill=(0, 0, 0))
    scale = min(1.0, spec.max_side / max(width, height))
    sent = image
    if scale < 1.0:
        sent = image.resize(
            (max(1, round(width * scale)), max(1, round(height * scale))),
            Image.Resampling.LANCZOS,
        )
    buffer = io.BytesIO()
    sent.save(buffer, "JPEG", quality=spec.jpeg_quality, subsampling=0, optimize=False)
    data = buffer.getvalue()
    counts: dict[str, int] = {}
    for region in regions:
        counts[region.category] = counts.get(region.category, 0) + 1
    return RenderedPage(
        page=index + 1,
        mime_type=spec.mime_type,
        data=data,
        sha256=hashlib.sha256(data).hexdigest(),
        width=sent.width,
        height=sent.height,
        render={
            "version": spec.version,
            "source": geometry.source,
            "work_dpi": spec.work_dpi,
            "rotation": geometry.rotation,
            "skew": geometry.skew,
            "work_width": width,
            "work_height": height,
            "pdf_width_pt": float(page.width),
            "pdf_height_pt": float(page.height),
        },
        mask={
            "version": IMAGE_MASK_VERSION,
            "counts": dict(sorted(counts.items())),
            "boxes": [_scaled_box(region, width, height) for region in regions],
        },
    )


def render_vision_pages(
    content: bytes,
    pages: tuple[int, ...],
    *,
    languages: tuple[str, ...] = ("eng",),
    spec: VisionRenderSpec = VISION_RENDER,
) -> list[RenderedPage]:
    """Masked images of ``pages`` (1-based) of a PDF. Runs inside the sandbox child."""

    import pdfplumber
    import pypdfium2  # type: ignore[import-untyped]

    rendered: list[RenderedPage] = []
    document = pypdfium2.PdfDocument(content)
    try:
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            for number in pages:
                if not 1 <= number <= len(pdf.pages):
                    raise ValueError("page out of range")
                rendered.append(
                    render_page(
                        pdf.pages[number - 1],
                        document,
                        number - 1,
                        spec=spec,
                        languages=languages,
                    )
                )
    finally:
        document.close()
    return rendered


def _page_payload(page: RenderedPage) -> dict[str, Any]:
    return {
        "page": page.page,
        "mime_type": page.mime_type,
        "data": page.data,
        "sha256": page.sha256,
        "width": page.width,
        "height": page.height,
        "render": dict(page.render),
        "mask": dict(page.mask),
    }


def _vision_child_main(
    connection: Any,
    start_event: Any,
    content: bytes,
    pages: tuple[int, ...],
    languages: tuple[str, ...],
    cpu_seconds: int,
    memory_bytes: int,
) -> None:
    from reconbench_app.pdf_sandbox import _set_posix_limits

    try:
        if not start_event.wait(timeout=10):
            connection.send({"child_error": True})
            return
        _set_posix_limits(cpu_seconds, memory_bytes)
        rendered = render_vision_pages(content, pages, languages=languages)
        connection.send({"pages": [_page_payload(page) for page in rendered]})
    except BaseException:
        connection.send({"child_error": True})
    finally:
        connection.close()


def _pages_from_payload(payload: dict[str, Any]) -> list[RenderedPage]:
    pages: list[RenderedPage] = []
    for item in payload["pages"]:
        data = bytes(item["data"])
        if hashlib.sha256(data).hexdigest() != item["sha256"]:
            raise ValueError("page image hash mismatch")
        pages.append(
            RenderedPage(
                page=int(item["page"]),
                mime_type=item["mime_type"],
                data=data,
                sha256=str(item["sha256"]),
                width=int(item["width"]),
                height=int(item["height"]),
                render=dict(item["render"]),
                mask=dict(item["mask"]),
            )
        )
    return pages


def vision_pages_isolated(
    content: bytes,
    pages: tuple[int, ...],
    *,
    languages: tuple[str, ...] = ("eng",),
    memory_bytes: int | None = None,
) -> list[RenderedPage] | None:
    """Masked page images from a limited child process; ``None`` on any failure."""

    from reconbench_app.pdf_sandbox import PDF_MEMORY_BYTES, _run_child

    limit = memory_bytes or PDF_MEMORY_BYTES
    result: list[RenderedPage] | None = _run_child(
        _vision_child_main,
        (content, pages, languages, VISION_CPU_SECONDS, limit),
        name="reconbench-pdf-vision",
        cpu_seconds=VISION_CPU_SECONDS,
        memory_bytes=limit,
        wall_seconds=VISION_WALL_SECONDS,
        convert=_pages_from_payload,
    )
    return result


__all__ = [
    "VISION_CPU_SECONDS",
    "VISION_WALL_SECONDS",
    "render_page",
    "render_vision_pages",
    "vision_pages_isolated",
]
