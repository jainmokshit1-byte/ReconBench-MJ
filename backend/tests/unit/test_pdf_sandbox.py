"""The application invokes PDF parsing only inside an isolated process."""

from __future__ import annotations

import io

from reconbench_app.pdf_sandbox import parse_pdf_isolated
from reconbench_core.parsers import ParserHints
from reconbench_core.schema import DocType, FileFormat
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas


def _blank_pdf() -> bytes:
    stream = io.BytesIO()
    page = canvas.Canvas(stream, pagesize=letter)
    page.showPage()
    page.save()
    return stream.getvalue()


def test_pdf_parser_runs_in_the_resource_bounded_child_process() -> None:
    result = parse_pdf_isolated(
        _blank_pdf(),
        ParserHints(file_format=FileFormat.PDF, doc_type=DocType.INVOICE),
    )

    assert result.success
    assert result.documents[0].needs_verification
    assert result.warnings[0].code == "NO_TEXT_LAYER"
