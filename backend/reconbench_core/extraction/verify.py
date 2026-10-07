"""Cross-check solver: confirm, correct or flag every extracted number.

A value is **confirmed** only when an arithmetic relation between independently
read values holds exactly. An OCR misreading is **corrected** only when the
relation determines the true value *and* that value differs from the reading
by known OCR confusions (``0``↔``8``, ``6``↔``0``, ``3``↔``8`` …, at most two
digits) or by lost leading digits (a tick mark over ``27,`` in ``27,650.00``).
A quantity obtained only by division stays **derived** unless a further check
confirms it. Anything else is **unverified** and must be reviewed by a person.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

CENT = Decimal("0.01")
ZERO = Decimal("0")
ONE = Decimal("1")
_CONFUSIONS = frozenset(
    {
        ("0", "8"),
        ("8", "0"),
        ("0", "6"),
        ("6", "0"),
        ("6", "8"),
        ("8", "6"),
        ("3", "8"),
        ("8", "3"),
        ("5", "6"),
        ("6", "5"),
        ("1", "7"),
        ("7", "1"),
        ("0", "9"),
        ("9", "0"),
        ("2", "7"),
        ("7", "2"),
        ("4", "1"),
        ("1", "4"),
    }
)


class Status(StrEnum):
    CONFIRMED = "confirmed"
    CORRECTED = "corrected"
    DERIVED = "derived"
    # A field that no arithmetic can check (number, date) read by OCR with every
    # character at high confidence and no look-alike ambiguity.
    CLEAR = "clear"
    UNVERIFIED = "unverified"
    MISSING = "missing"


VERIFIED = frozenset({Status.CONFIRMED, Status.CORRECTED})


@dataclass
class Num:
    ocr: Decimal | None
    value: Decimal | None = None
    status: Status = Status.MISSING
    note: str = ""

    def __post_init__(self) -> None:
        if self.value is None and self.ocr is not None:
            self.value = self.ocr
            self.status = Status.UNVERIFIED

    @property
    def known(self) -> bool:
        return self.value is not None

    @property
    def verified(self) -> bool:
        return self.status in VERIFIED


def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _digits(value: Decimal) -> str:
    return format(abs(money(value)), "f").replace(".", "")


def confusable(reading: Decimal, truth: Decimal) -> bool:
    """True when ``reading`` is a plausible OCR misreading of ``truth``."""

    if money(reading) == money(truth):
        return True
    seen, actual = _digits(reading), _digits(truth)
    if len(seen) == len(actual):
        diffs = [(a, b) for a, b in zip(seen, actual, strict=True) if a != b]
        return 0 < len(diffs) <= 2 and all(pair in _CONFUSIONS for pair in diffs)
    # Leading digits hidden by a tick mark: the reading is a suffix of the truth.
    return len(seen) >= 4 and len(actual) > len(seen) and actual.endswith(seen)


def _set(num: Num, value: Decimal, *, note: str, derived_ok: bool = True) -> bool:
    """Record a value implied by a relation. Returns True if anything changed."""

    if num.verified:
        return False
    if num.ocr is not None and money(num.ocr) == money(value):
        num.value, num.status, num.note = num.ocr, Status.CONFIRMED, note
        return True
    if num.ocr is not None and confusable(num.ocr, value):
        num.value, num.status, num.note = value, Status.CORRECTED, note
        return True
    if num.ocr is None and derived_ok:
        num.value, num.status, num.note = value, Status.CONFIRMED, note
        return True
    return False


def _confirm(*nums: Num, note: str) -> bool:
    changed = False
    for num in nums:
        if num.known and not num.verified:
            num.status, num.note = Status.CONFIRMED, note
            changed = True
    return changed


@dataclass
class LineNumbers:
    quantity: Num
    rate: Num
    amount: Num
    subtotal: Num = field(default_factory=lambda: Num(None))
    discount: Num = field(default_factory=lambda: Num(None))
    freight: Num = field(default_factory=lambda: Num(None))
    extended: Num = field(default_factory=lambda: Num(None))
    tax_rate: Decimal | None = None
    taxes: list[Num] = field(default_factory=list)

    def numbers(self) -> Iterable[Num]:
        yield from (
            self.quantity,
            self.rate,
            self.amount,
            self.subtotal,
            self.discount,
            self.freight,
            self.extended,
            *self.taxes,
        )

    @property
    def verified(self) -> bool:
        required = [self.amount, self.quantity, self.rate]
        return all(num.verified for num in required) and all(
            num.verified
            for num in (self.subtotal, self.freight, self.extended, *self.taxes)
            if num.known
        )


def _adjustments(line: LineNumbers) -> Decimal:
    value = Decimal("0")
    if line.freight.known:
        value += line.freight.value or ZERO
    if line.discount.known:
        value -= abs(line.discount.value or ZERO)
    return value


def _clean_quantity(value: Decimal) -> bool:
    return value > 0 and value == value.quantize(Decimal("0.001")) and value < Decimal("1000000")


def solve_line(line: LineNumbers, *, rounds: int = 4) -> LineNumbers:
    """Propagate the line relations until nothing changes."""

    for _ in range(rounds):
        changed = False
        q, r, a, s = line.quantity, line.rate, line.amount, line.subtotal
        adjust = _adjustments(line)
        adjustments_ok = all(num.verified or not num.known for num in (line.freight, line.discount))

        # extended = amount + taxes
        if line.extended.known and line.taxes and all(t.known for t in line.taxes):
            tax_total = sum((t.value or ZERO for t in line.taxes), Decimal("0"))
            if (
                a.known
                and abs((a.value or ZERO) + tax_total - (line.extended.value or ZERO)) <= CENT * 2
            ):
                changed |= _confirm(a, line.extended, *line.taxes, note="amount + taxes = extended")
            elif not a.verified:
                changed |= _set(
                    a, (line.extended.value or ZERO) - tax_total, note="extended − taxes"
                )
        # tax = amount × rate
        if line.tax_rate is not None and a.known and line.taxes:
            expected = money((a.value or ZERO) * line.tax_rate / 100)
            for tax in line.taxes:
                if tax.known and abs((tax.value or ZERO) - expected) <= CENT * 2:
                    changed |= _confirm(tax, note="tax = amount × rate")
                elif not tax.verified and a.verified:
                    changed |= _set(tax, expected, note="tax = amount × rate")
        # subtotal = quantity × rate
        if s.known or s.ocr is not None:
            if (
                q.known
                and r.known
                and s.known
                and money((q.value or ZERO) * (r.value or ZERO)) == money(s.value or ZERO)
            ):
                changed |= _confirm(q, r, s, note="qty × rate = subtotal")
            if s.known and r.ocr is not None and not r.verified and not (q.known and q.verified):
                # Rate partly hidden (a tick over its leading digits): find the one
                # whole quantity whose subtotal ÷ quantity ends in the digits read.
                fits = [
                    (count, money((s.value or ZERO) / count))
                    for count in range(1, 1001)
                    if money((s.value or ZERO) / count) * count == money(s.value or ZERO)
                    and money((s.value or ZERO) / count) != money(r.ocr)
                    and confusable(r.ocr, money((s.value or ZERO) / count))
                ]
                if len(fits) == 1:
                    count, rate = fits[0]
                    q.value, q.status = (
                        Decimal(count),
                        (Status.CORRECTED if q.ocr is not None else Status.CONFIRMED),
                    )
                    q.note = "subtotal ÷ rate (rate partly hidden)"
                    r.value, r.status, r.note = rate, Status.CORRECTED, "subtotal ÷ qty"
                    s.status, s.note = Status.CONFIRMED, "qty × rate = subtotal"
                    changed = True
            if s.verified and q.known and not r.verified and (q.value or ZERO) > 0:
                changed |= _set(r, (s.value or ZERO) / (q.value or ONE), note="subtotal ÷ qty")
            if s.verified and r.known and (r.value or ZERO) > 0 and not q.verified:
                derived = (s.value or ZERO) / (r.value or ONE)
                if _clean_quantity(derived) and q.ocr is not None and confusable(q.ocr, derived):
                    changed |= _set(q, derived, note="subtotal ÷ rate")
            if q.verified and r.verified and not s.verified:
                changed |= _set(s, (q.value or ZERO) * (r.value or ZERO), note="qty × rate")
            # amount = subtotal + adjustments
            if a.known and s.known and money((s.value or ZERO) + adjust) == money(a.value or ZERO):
                changed |= _confirm(
                    a, s, line.freight, line.discount, note="subtotal ± adjustments = amount"
                )
            elif a.verified and adjustments_ok and not s.verified:
                changed |= _set(s, (a.value or ZERO) - adjust, note="amount ∓ adjustments")
            elif s.verified and adjustments_ok and not a.verified:
                changed |= _set(a, (s.value or ZERO) + adjust, note="subtotal ± adjustments")
            elif a.verified and s.verified and not line.freight.verified and line.freight.known:
                changed |= _set(
                    line.freight, (a.value or ZERO) - (s.value or ZERO), note="amount − subtotal"
                )
        else:
            base = (q.value or ZERO) * (r.value or ZERO) if q.known and r.known else None
            if (
                base is not None
                and a.known
                and adjustments_ok
                and money(base + adjust) == money(a.value or ZERO)
            ):
                changed |= _confirm(q, r, a, note="qty × rate = amount")
            if (
                a.verified
                and r.ocr is not None
                and not r.verified
                and money(r.ocr) == money((a.value or ZERO) - adjust)
            ):
                # Rate and amount read independently and identical: the quantity is 1.
                r.value, r.status, r.note = r.ocr, Status.CONFIRMED, "rate = amount"
                if q.value != ONE:
                    q.value = ONE
                    q.status = Status.CORRECTED if q.ocr is not None else Status.CONFIRMED
                else:
                    q.status = Status.CONFIRMED
                q.note = "rate = amount, so qty 1"
                changed = True
            if a.verified and q.known and not r.verified and (q.value or ZERO) > 0:
                changed |= _set(
                    r, ((a.value or ZERO) - adjust) / (q.value or ONE), note="amount ÷ qty"
                )
            if a.verified and r.verified and not q.verified and (r.value or ZERO) > 0:
                derived = ((a.value or ZERO) - adjust) / (r.value or ONE)
                if _clean_quantity(derived):
                    if r.ocr is not None and money(r.ocr) == money((a.value or ZERO) - adjust):
                        changed |= _set(
                            q, Decimal("1"), note="rate = amount, so qty 1", derived_ok=True
                        )
                        if q.ocr is not None and not q.verified:
                            q.value, q.status, q.note = (
                                Decimal("1"),
                                Status.CORRECTED,
                                "rate = amount",
                            )
                            changed = True
                    elif q.ocr is not None and confusable(q.ocr, derived):
                        changed |= _set(q, derived, note="amount ÷ rate")
                    elif not q.verified:
                        q.value, q.status, q.note = derived, Status.DERIVED, "amount ÷ rate"
            if q.verified and r.verified and not a.verified:
                changed |= _set(
                    a, (q.value or ZERO) * (r.value or ZERO) + adjust, note="qty × rate"
                )
        if not changed:
            break
    return line


@dataclass
class DocumentNumbers:
    taxable: Num
    cgst: Num
    sgst: Num
    igst: Num
    grand_total: Num
    round_off: Num = field(default_factory=lambda: Num(None))
    words_totals: list[Decimal] = field(default_factory=list)
    total_quantity: Decimal | None = None
    taxable_candidates: list[Decimal] = field(default_factory=list)
    grand_candidates: list[Decimal] = field(default_factory=list)
    tax_candidates: dict[str, list[Decimal]] = field(default_factory=dict)
    # Supplier and buyer in different GST states (IGST only), the same state
    # (CGST + SGST), or unknown.
    interstate: bool | None = None
    # Total GST rates (CGST + SGST, or IGST) in percent.
    gst_rates: tuple[Decimal, ...] = ()
    # The page prints "₹" before amounts, which OCR may read as a leading digit.
    rupee_glyph: bool = False


def _pick(
    num: Num, candidates: Sequence[Decimal], target: Decimal, *, tolerance: Decimal, note: str
) -> bool:
    for candidate in [num.ocr, *candidates]:
        if candidate is not None and abs(candidate - target) <= tolerance:
            num.value, num.status, num.note = candidate, Status.CONFIRMED, note
            return True
    return _set(num, target, note=note, derived_ok=False)


def _unique(values: Iterable[Decimal | None]) -> list[Decimal]:
    seen: list[Decimal] = []
    for value in values:
        if value is not None and value >= 0 and all(value != item for item in seen):
            seen.append(value)
    return seen


_STANDARD_RATES = tuple(Decimal(rate) for rate in ("5", "12", "18", "28"))


def _standard_rate(tax: Decimal, taxable: Decimal, rates: Sequence[Decimal] = ()) -> bool:
    """``tax`` (CGST + SGST, or IGST) is ``taxable`` at one GST slab."""

    if taxable <= 0:
        return False
    rate = tax / taxable * 100
    return any(abs(rate - standard) <= Decimal("0.05") for standard in rates or _STANDARD_RATES)


# Digits OCR reads in place of a "₹" printed right before an amount.
_RUPEE_DIGITS = frozenset("12347")


def _without_rupee_glyph(values: Iterable[Decimal]) -> list[Decimal]:
    """Each value with a leading digit that may be a misread "₹" removed."""

    variants: list[Decimal] = []
    for value in values:
        text = format(value, "f")
        head = text.split(".")[0]
        if value <= 0 or len(head) < 2 or head[0] not in _RUPEE_DIGITS or head[1] == "0":
            continue
        variants.append(Decimal(text[1:]))
    return variants


# Per-line tax rounding can leave a paisa or two between printed totals.
_SUM_TOLERANCE = Decimal("0.02")


def _round_off_limit(grand: Decimal) -> Decimal:
    """Largest gap a round-off explains: under ₹1 to a whole-rupee total, else none."""

    return ONE if grand == grand.to_integral_value() else _SUM_TOLERANCE


def solve_document(doc: DocumentNumbers, lines: Sequence[LineNumbers]) -> DocumentNumbers:
    """Pick the one set of totals that agrees with the lines, each other and the words.

    Every candidate read for taxable value, CGST, SGST, IGST and grand total is
    tried; a combination is valid when taxable + taxes = grand total exactly, or
    within ₹1 when the grand total is a whole rupee (an implicit round-off; a
    total with paise was not rounded). The best valid combination is
    the one confirmed by the most independent facts: lines summing to the taxable
    value, the amount in words, CGST = SGST, a standard GST rate and exactness.
    """

    lines_sum: Decimal | None = None
    if lines and all(line.amount.known for line in lines):
        lines_sum = sum((line.amount.value or ZERO for line in lines), ZERO)
    quantities_known = bool(lines) and all(line.quantity.known for line in lines)
    if (
        doc.total_quantity is not None
        and quantities_known
        and sum((line.quantity.value or ZERO for line in lines), ZERO) == doc.total_quantity
    ):
        for line in lines:
            if not line.quantity.verified:
                line.quantity.status, line.quantity.note = Status.CONFIRMED, "quantities sum"

    taxables = _unique([doc.taxable.ocr, *doc.taxable_candidates, lines_sum])
    grands = _unique([doc.grand_total.ocr, *doc.grand_candidates, *doc.words_totals])
    cgsts = _unique([doc.cgst.ocr, *doc.tax_candidates.get("cgst", [])])
    sgsts = _unique([doc.sgst.ocr, *doc.tax_candidates.get("sgst", [])])
    igsts = _unique([doc.igst.ocr, *doc.tax_candidates.get("igst", [])])
    glyphs: set[Decimal] = set()
    if doc.rupee_glyph:
        # A "₹" read as a digit: the shorter value is an alternative, trusted only
        # when the arithmetic (and so other readings) agree with it.
        for group in (taxables, grands, cgsts, sgsts, igsts):
            for variant in _without_rupee_glyph(list(group)):
                if variant not in group:
                    group.append(variant)
                    glyphs.add(variant)
    tax_options: list[tuple[Decimal | None, Decimal | None, Decimal | None]] = [(None, None, None)]
    tax_options += [(c, s, None) for c in cgsts for s in sgsts]
    # One of CGST/SGST unreadable: intra-state invoices charge them equally.
    tax_options += [(c, c, None) for c in cgsts if c not in sgsts]
    tax_options += [(s, s, None) for s in sgsts if s not in cgsts]
    tax_options += [(None, None, i) for i in igsts]
    best: (
        tuple[int, Decimal, Decimal, tuple[Decimal | None, Decimal | None, Decimal | None]] | None
    ) = None
    for taxable in taxables:
        for grand in grands:
            for cgst, sgst, igst in tax_options:
                tax = (cgst or ZERO) + (sgst or ZERO) + (igst or ZERO)
                difference = grand - taxable - tax
                if abs(difference) > _round_off_limit(grand) or (tax == ZERO and grand != taxable):
                    continue
                score = 0
                if doc.interstate is True and (cgst is not None or sgst is not None):
                    score -= 6
                if doc.interstate is False and igst is not None:
                    score -= 6
                score -= sum(1 for value in (taxable, grand, cgst, sgst, igst) if value in glyphs)
                if lines_sum is not None and abs(taxable - lines_sum) <= _SUM_TOLERANCE:
                    score += 4
                if grand in doc.words_totals:
                    score += 4
                if cgst is not None and cgst == sgst:
                    score += 2
                    if cgst not in cgsts or sgst not in sgsts:
                        score -= 1
                if tax and _standard_rate(tax, taxable, doc.gst_rates):
                    score += 2
                if abs(difference) <= CENT:
                    score += 1
                if tax == ZERO:
                    score -= 3
                if best is None or score > best[0]:
                    best = (score, taxable, grand, (cgst, sgst, igst))
    if best is not None and best[0] >= 4:
        _score, taxable, grand, (cgst, sgst, igst) = best
        note = "taxable + taxes = grand total"
        for num, value in (
            (doc.taxable, taxable),
            (doc.grand_total, grand),
            (doc.cgst, cgst),
            (doc.sgst, sgst),
            (doc.igst, igst),
        ):
            if value is not None:
                num.value, num.status, num.note = value, Status.CONFIRMED, note
            elif num.known and num is not doc.taxable and num is not doc.grand_total:
                num.value, num.status = None, Status.MISSING
        difference = grand - taxable - ((cgst or ZERO) + (sgst or ZERO) + (igst or ZERO))
        if difference != ZERO:
            doc.round_off.value, doc.round_off.status = money(difference), Status.CONFIRMED
            doc.round_off.note = "grand total − taxable − taxes"
        if lines_sum is not None and abs(taxable - lines_sum) <= _SUM_TOLERANCE:
            for line in lines:
                if not line.amount.verified:
                    line.amount.status, line.amount.note = Status.CONFIRMED, "lines sum to taxable"
    elif doc.words_totals:
        # Words confirm a total only when a separately read figure agrees with them.
        numeric = _unique([doc.grand_total.ocr, *doc.grand_candidates])
        for num_value in numeric:
            if num_value in doc.words_totals:
                doc.grand_total.value, doc.grand_total.status = num_value, Status.CONFIRMED
                doc.grand_total.note = "amount in words"
                break
        else:
            if doc.rupee_glyph:
                for num_value in _without_rupee_glyph(numeric):
                    if num_value in doc.words_totals:
                        doc.grand_total.value = num_value
                        doc.grand_total.status = Status.CORRECTED
                        doc.grand_total.note = "amount in words (rupee sign read as a digit)"
                        break
    return doc


__all__ = [
    "VERIFIED",
    "DocumentNumbers",
    "LineNumbers",
    "Num",
    "Status",
    "confusable",
    "money",
    "solve_document",
    "solve_line",
]
