"""Dual-read may confirm a field but must never clear unrelated review reasons."""

from __future__ import annotations

from reconbench_app.extraction.pipeline import _dual_read
from reconbench_core.extraction.extract import extract_document
from reconbench_core.extraction.layout import DocumentLayout
from reconbench_core.schema import DocType
from tests.unit.test_extraction_core import _invoice, _page


class _AgreeingEngine:
    """A second reading that always agrees with the first one."""

    def __init__(self, text: str) -> None:
        self.text = text

    def reread(self, page: int, bbox: tuple[int, ...]) -> str | None:
        del page, bbox
        return self.text


def _without_table() -> DocumentLayout:
    page = _invoice().pages[0]
    header_words = tuple(word for word in page.words if word.y0 < 700 or word.y0 >= 1000)
    return DocumentLayout((_page(1, [list(header_words)]),), 1)


def test_dual_read_keeps_review_when_no_lines_were_read() -> None:
    extraction = extract_document(_without_table(), doc_type=DocType.INVOICE)
    assert extraction is not None
    document = extraction.document
    assert not document.lines
    assert document.needs_verification
    assert "lines" in document.header.extras["review_fields"]

    reviewed, confirmed = _dual_read(_AgreeingEngine("SYN-INV-0042"), document)

    assert "doc_no" in confirmed
    assert reviewed.needs_verification
    assert "lines" in reviewed.header.extras["review_fields"]


def test_dual_read_clears_review_when_the_number_was_the_only_reason() -> None:
    extraction = extract_document(_invoice(), doc_type=DocType.INVOICE)
    assert extraction is not None
    document = extraction.document.model_copy(update={"needs_verification": True})
    header = document.header.model_copy(
        update={"extras": {**document.header.extras, "review_fields": ["doc_no"]}}
    )
    document = document.model_copy(update={"header": header})

    reviewed, confirmed = _dual_read(_AgreeingEngine("SYN-INV-0042"), document)

    assert confirmed == ["doc_no"]
    assert not reviewed.needs_verification
