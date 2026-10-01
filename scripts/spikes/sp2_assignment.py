"""Synthetic determinism spike for sorted, four-decimal Hungarian assignments."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from itertools import permutations

from scipy.optimize import linear_sum_assignment

INVOICES = ["INV-SYN-03", "INV-SYN-01", "INV-SYN-02"]
PO_LINES = ["PO-SYN-02", "PO-SYN-03", "PO-SYN-01"]
SCORES = {
    ("INV-SYN-01", "PO-SYN-01"): Decimal("0.97004"),
    ("INV-SYN-01", "PO-SYN-02"): Decimal("0.81002"),
    ("INV-SYN-01", "PO-SYN-03"): Decimal("0.50000"),
    ("INV-SYN-02", "PO-SYN-01"): Decimal("0.81001"),
    ("INV-SYN-02", "PO-SYN-02"): Decimal("0.96003"),
    ("INV-SYN-02", "PO-SYN-03"): Decimal("0.51000"),
    ("INV-SYN-03", "PO-SYN-01"): Decimal("0.50000"),
    ("INV-SYN-03", "PO-SYN-02"): Decimal("0.51000"),
    ("INV-SYN-03", "PO-SYN-03"): Decimal("0.95002"),
}


def quantized_score(invoice: str, po_line: str) -> Decimal:
    return SCORES[(invoice, po_line)].quantize(
        Decimal("0.0001"), rounding=ROUND_HALF_UP
    )


def assignment(
    invoice_order: tuple[str, ...], po_order: tuple[str, ...]
) -> tuple[tuple[str, str], ...]:
    sorted_invoices = sorted(invoice_order)
    sorted_pos = sorted(po_order)
    costs = [
        [
            float(Decimal(1) - quantized_score(invoice, po_line))
            for po_line in sorted_pos
        ]
        for invoice in sorted_invoices
    ]
    row_indices, column_indices = linear_sum_assignment(costs)
    return tuple(
        sorted(
            (sorted_invoices[row], sorted_pos[column])
            for row, column in zip(row_indices, column_indices, strict=True)
        )
    )


def main() -> int:
    expected = assignment(tuple(INVOICES), tuple(PO_LINES))
    count = 0
    for invoice_order in permutations(INVOICES):
        for po_order in permutations(PO_LINES):
            actual = assignment(invoice_order, po_order)
            if actual != expected:
                raise SystemExit(
                    f"Assignment drifted for shuffled inputs: {actual} != {expected}"
                )
            count += 1
    print(
        f"PASS: {count} shuffled input orders produced the same assignment: {expected}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
