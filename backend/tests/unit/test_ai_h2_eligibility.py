"""H2 eligibility, spec-conflict exclusion and batching without a database."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest
from reconbench_app.ai import h2


@pytest.mark.parametrize(
    ("score", "eligible"),
    [
        (None, False),
        (Decimal("0.5999"), False),
        (Decimal("0.60"), True),
        (Decimal("0.7999"), True),
        (Decimal("0.80"), False),
    ],
)
def test_band_edges(score: Decimal | None, eligible: bool) -> None:
    assert h2.in_band(score) is eligible
    expected = None if eligible else h2.OUTSIDE_BAND
    assert h2.ineligible_code(score, "Dell Latitude Laptop", "Dell Latitude Notebook") == expected


@pytest.mark.parametrize(
    ("invoice", "po", "conflict"),
    [
        ("Kingston DDR4 RAM 16GB", "Kingston DDR4 RAM 8GB", True),
        ("Dell Monitor 24 inch black", "Dell Monitor 27 inch black", True),
        ("Dell Monitor 24 inch black", "Dell LED Monitor 24 inch black", False),
        ("Kingston DDR4 RAM", "Kingston DDR4 RAM 8GB", False),
    ],
)
def test_spec_conflicts_use_the_core_rule_and_are_never_eligible(
    invoice: str, po: str, conflict: bool
) -> None:
    assert h2.spec_conflict(invoice, po) is conflict
    expected = h2.SPEC_CONFLICT if conflict else None
    assert h2.ineligible_code(Decimal("0.70"), invoice, po) == expected


def _line(description: str, po_description: str = "Widget") -> h2.LineCandidate:
    return h2.LineCandidate(
        line_result_id=uuid4(),
        invoice_line_id=uuid4(),
        po_doc_line_id=uuid4(),
        score=Decimal("0.70"),
        invoice=h2.ItemText(description, "SKU-1", "8471", "NOS"),
        po=h2.ItemText(po_description, None, None, None),
        code=None,
    )


def test_batches_are_contiguous_capped_and_redacted() -> None:
    lines = [_line(f"Widget {index}") for index in range(45)]
    lines[0] = _line("Widget ref 27AAPFU0939F1ZV contact buyer@example.com")

    built = h2.batches(lines, 20)

    assert [(batch.start, len(batch.pairs)) for batch in built] == [(0, 20), (20, 20), (40, 5)]
    assert [pair.pair_id for pair in built[2].pairs] == ["p1", "p2", "p3", "p4", "p5"]
    first = built[0].pairs[0].invoice_text
    assert "27AAPFU0939F1ZV" not in first and "buyer@example.com" not in first
    assert built[0].redacted and not built[1].redacted
    # Quantities, rates and amounts are never part of the text.
    assert "Item code: SKU-1" in built[1].pairs[0].invoice_text
    assert "HSN/SAC: 8471" in built[1].pairs[0].invoice_text
    assert "Unit: NOS" in built[1].pairs[0].invoice_text
    parsed = h2.parse_subject_version(built[1].subject_version)
    assert parsed == (20, 20, built[1].fingerprint)
    assert h2.build_batch(lines, 40, 6) is None
    assert h2.batches(lines, 50)[0].pairs[-1].pair_id == "p20"


def test_fingerprint_changes_with_the_text() -> None:
    lines = [_line("Widget A"), _line("Widget B")]
    same = h2.build_batch(lines, 0, 2)
    changed = h2.build_batch([lines[0], _line("Widget C")], 0, 2)
    assert same is not None and changed is not None
    assert same.fingerprint == h2.build_batch(lines, 0, 2).fingerprint  # type: ignore[union-attr]
    assert same.fingerprint != changed.fingerprint
