"""Native Tally "Import Data" exports (accuracy plan WI-IMP1, decision-log D95).

A native export is one ``ENVELOPE`` holding many ``VOUCHER`` records plus masters
(stock items, ledgers), which are ignored: only vouchers become documents. Three things
make such a file different from the generic XML the parser reads:

* **Unreadable markers.** Tally writes the character reference ``&#4;`` in front of
  system values (for example ``&#4; Not Applicable``) and masters may hold raw control
  characters. Both are invalid in XML 1.0, so the whole file fails to parse.
  :func:`sanitize` removes exactly those characters, keeps the text around them, and
  counts each removal by voucher and tag so the parser can log it; it is used only
  after a strict parse has failed, so every file that parses today is read unchanged.
* **Voucher structure.** A line's real item text is ``BASICUSERDESCRIPTION`` (the
  stock item name is often generic, for example ``AMC``); header totals live in the
  party ledger entry and the tax ledger entries; a Receipt Note names its purchase
  order in ``REFERENCE`` and in each line's ``ORDERNO``. :func:`voucher_facts` reads
  these from one voucher.
* **Signs.** Purchase-side inventory amounts are exported negative (a debit:
  ``ISDEEMEDPOSITIVE`` = Yes). They are stored as magnitudes and every changed field is
  logged with its original text.
"""

from __future__ import annotations

import bisect
import re
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from lxml import etree  # type: ignore[import-untyped]

TALLY_NATIVE_VERSION = "tally_native.v1"

# Code points XML 1.0 forbids (C0 controls except tab, LF, CR), as references or raw.
_INVALID_CODES = frozenset({*range(0, 9), 11, 12, *range(14, 32)})
_CHAR_REF = re.compile(r"&#(?:[xX]([0-9a-fA-F]{1,6})|([0-9]{1,7}));")
_RAW_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_VOUCHER_OPEN = re.compile(r"<VOUCHER[\s>/]")
_VOUCHER_CLOSE = "</VOUCHER>"
_DECLARATION = re.compile(r"^\s*<\?xml[^>]*\?>")
_ENCODING = re.compile(rb"""^\s*<\?xml[^>]*encoding\s*=\s*["']([A-Za-z0-9_.-]+)["']""")
_NUMBER = re.compile(r"-?[\d,]*\.?\d+")

INVENTORY_TAGS = frozenset({"ALLINVENTORYENTRIES.LIST", "INVENTORYENTRIES.LIST"})
LEDGER_TAGS = frozenset({"LEDGERENTRIES.LIST", "ALLLEDGERENTRIES.LIST"})


@dataclass(frozen=True, slots=True)
class Sanitized:
    """Sanitised UTF-8 bytes and what was removed: ``(voucher ordinal or None, tag) → n``."""

    content: bytes
    removed: dict[tuple[int | None, str], int]
    voucher_count: int

    def for_voucher(self, ordinal: int) -> Counter[str]:
        return Counter(
            {tag: count for (index, tag), count in self.removed.items() if index == ordinal}
        )

    def outside_vouchers(self) -> int:
        return sum(count for (index, _tag), count in self.removed.items() if index is None)


def _decode(content: bytes) -> str | None:
    if content.startswith((b"\xff\xfe", b"\xfe\xff")):
        encoding = "utf-16"
    elif content.startswith(b"\xef\xbb\xbf"):
        encoding = "utf-8-sig"
    else:
        declared = _ENCODING.match(content[:200])
        encoding = declared.group(1).decode("ascii") if declared else "utf-8"
    try:
        return content.decode(encoding)
    except (LookupError, UnicodeDecodeError):
        return None


def _enclosing_tag(text: str, position: int) -> str:
    start = text.rfind("<", 0, position)
    match = re.match(r"<([A-Za-z_][\w.:-]*)", text[start:]) if start >= 0 else None
    return match.group(1) if match else "?"


def sanitize(content: bytes) -> Sanitized | None:
    """Remove the characters XML 1.0 forbids; ``None`` when there is nothing to remove.

    Only invalid character references (``&#4;``) and raw C0 control characters are
    removed; the text around them is kept. The result is re-encoded as UTF-8 with a
    UTF-8 declaration.
    """

    text = _decode(content)
    if text is None:
        return None
    opens = [match.start() for match in _VOUCHER_OPEN.finditer(text)]
    closes = [match.end() for match in re.finditer(re.escape(_VOUCHER_CLOSE), text)]
    spans = list(zip(opens, closes, strict=False)) if len(opens) == len(closes) else []
    starts = [start for start, _end in spans]
    removed: Counter[tuple[int | None, str]] = Counter()

    def owner(position: int) -> int | None:
        index = bisect.bisect_right(starts, position) - 1
        if index >= 0 and spans[index][0] <= position < spans[index][1]:
            return index
        return None

    def drop_reference(match: re.Match[str]) -> str:
        code = int(match.group(1), 16) if match.group(1) else int(match.group(2))
        if code not in _INVALID_CODES:
            return match.group(0)
        removed[(owner(match.start()), _enclosing_tag(text, match.start()))] += 1
        return ""

    for match in _RAW_CONTROL.finditer(text):
        removed[(owner(match.start()), _enclosing_tag(text, match.start()))] += 1
    # Positions are taken from the original text before anything is removed.
    cleaned = _RAW_CONTROL.sub("", _CHAR_REF.sub(drop_reference, text))
    if not removed:
        return None
    cleaned = _DECLARATION.sub("", cleaned, count=1).lstrip("﻿")
    output = ('<?xml version="1.0" encoding="UTF-8"?>' + cleaned).encode("utf-8")
    return Sanitized(output, dict(removed), len(spans))


def removal_message(counts: Counter[str]) -> str:
    detail = ", ".join(f"{tag} {count}" for tag, count in sorted(counts.items()))
    return (
        f"Removed {sum(counts.values())} characters XML does not allow (Tally's &#4; marker "
        f"or a control character) before parsing; the text around them was kept ({detail})."
    )


# --------------------------------------------------------------------------- vouchers


def _local(element: etree._Element) -> str:
    return etree.QName(element).localname if isinstance(element.tag, str) else ""


def _child_text(element: etree._Element, tag: str) -> str:
    for child in element:
        if _local(child) == tag:
            return " ".join(part.strip() for part in child.itertext() if part.strip())
    return ""


def _amount(text: str) -> Decimal | None:
    match = _NUMBER.search(text.replace(" ", ""))
    if match is None:
        return None
    try:
        return Decimal(match.group(0).replace(",", ""))
    except InvalidOperation:
        return None


def is_native_voucher(element: etree._Element) -> bool:
    """A Tally voucher with its own inventory or ledger entry lists."""

    return _local(element) == "VOUCHER" and any(
        _local(child) in INVENTORY_TAGS | LEDGER_TAGS for child in element
    )


@dataclass(frozen=True, slots=True)
class LineFacts:
    stock_item: str
    user_description: str
    amount_text: str
    deemed_positive: bool
    order_numbers: tuple[str, ...]
    gst_rate: Decimal | None = None
    """The line's full GST rate from ``RATEDETAILS.LIST``: the IGST head's rate, else
    CGST + SGST/UTGST (the first ``GSTRATE`` of a line is the CGST head's half rate)."""


@dataclass(frozen=True, slots=True)
class VoucherFacts:
    lines: dict[etree._Element, LineFacts]
    reference: str
    party_amount: Decimal | None
    party_entries: int
    inventory_total: Decimal
    taxes: dict[str, Decimal]
    round_off: Decimal
    other_ledgers: int
    order_numbers: tuple[str, ...] = field(default=())

    def totals(self) -> dict[str, Decimal] | None:
        """Header totals when the party amount closes against lines, taxes and round-off."""

        if self.party_entries != 1 or self.party_amount is None or self.other_ledgers:
            return None
        grand = abs(self.party_amount)
        computed = self.inventory_total + sum(self.taxes.values()) + self.round_off
        if abs(grand - computed) > Decimal("1"):
            return None
        totals = {"taxable_total": self.inventory_total, "grand_total": grand}
        totals.update({name: value for name, value in self.taxes.items() if value})
        if self.round_off:
            totals["round_off"] = self.round_off
        return totals


def _tax_head(ledger_name: str) -> str | None:
    name = ledger_name.upper()
    for head, words in (("cgst", ("CGST",)), ("sgst", ("SGST", "UTGST")), ("igst", ("IGST",))):
        if any(word in name for word in words):
            return head
    return None


def is_order_number(text: str) -> bool:
    """An order number has a digit; Tally writes its system value ``&#4; Not Applicable``
    in ``ORDERNO`` when a line tracks no order, and that is not a reference."""

    return any(character.isdigit() for character in text)


def _gst_rate(entry: etree._Element) -> Decimal | None:
    heads: dict[str, Decimal] = {}
    for details in entry:
        if _local(details) != "RATEDETAILS.LIST":
            continue
        head = _child_text(details, "GSTRATEDUTYHEAD").upper()
        rate = _amount(_child_text(details, "GSTRATE"))
        if head and rate is not None:
            heads[head] = rate
    if heads.get("IGST"):
        return heads["IGST"]
    state = heads.get("SGST/UTGST", heads.get("SGST", Decimal("0")))
    split = heads.get("CGST", Decimal("0")) + state
    return split or (Decimal("0") if heads else None)


def voucher_facts(element: etree._Element) -> VoucherFacts:
    lines: dict[etree._Element, LineFacts] = {}
    inventory_total = Decimal("0")
    orders: list[str] = []
    for entry in element:
        if _local(entry) not in INVENTORY_TAGS:
            continue
        user = [
            " ".join(text.split())
            for node in entry.iter()
            if _local(node) == "BASICUSERDESCRIPTION"
            for text in [" ".join(part.strip() for part in node.itertext() if part.strip())]
            if text.strip()
        ]
        entry_orders = tuple(
            dict.fromkeys(
                text
                for node in entry.iter()
                if _local(node) == "ORDERNO"
                for text in [" ".join(part.strip() for part in node.itertext()).strip()]
                if is_order_number(text)
            )
        )
        orders.extend(entry_orders)
        amount_text = _child_text(entry, "AMOUNT")
        lines[entry] = LineFacts(
            stock_item=_child_text(entry, "STOCKITEMNAME"),
            user_description=" ".join(user),
            amount_text=amount_text,
            deemed_positive=_child_text(entry, "ISDEEMEDPOSITIVE").casefold() == "yes",
            order_numbers=entry_orders,
            gst_rate=_gst_rate(entry),
        )
        inventory_total += abs(_amount(amount_text) or Decimal("0"))
    party: list[Decimal | None] = []
    taxes = {"cgst": Decimal("0"), "sgst": Decimal("0"), "igst": Decimal("0")}
    round_off = Decimal("0")
    other = 0
    for entry in element:
        if _local(entry) not in LEDGER_TAGS:
            continue
        amount = _amount(_child_text(entry, "AMOUNT"))
        if _child_text(entry, "ISPARTYLEDGER").casefold() == "yes":
            party.append(amount)
            continue
        name = _child_text(entry, "LEDGERNAME")
        head = _tax_head(name)
        if head is not None:
            taxes[head] += abs(amount or Decimal("0"))
        elif "ROUND" in name.upper():
            # A debit (negative in the export) adds to the total.
            round_off += -(amount or Decimal("0"))
        else:
            other += 1
    reference = _child_text(element, "REFERENCE")
    return VoucherFacts(
        lines=lines,
        reference=reference if is_order_number(reference) else "",
        party_amount=party[0] if len(party) == 1 else None,
        party_entries=len(party),
        inventory_total=inventory_total,
        taxes=taxes,
        round_off=round_off,
        other_ledgers=other,
        order_numbers=tuple(dict.fromkeys(orders)),
    )


def plain_decimal(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.01")), "f")


__all__ = [
    "INVENTORY_TAGS",
    "LEDGER_TAGS",
    "TALLY_NATIVE_VERSION",
    "LineFacts",
    "Sanitized",
    "VoucherFacts",
    "is_native_voucher",
    "is_order_number",
    "plain_decimal",
    "removal_message",
    "sanitize",
    "voucher_facts",
]
