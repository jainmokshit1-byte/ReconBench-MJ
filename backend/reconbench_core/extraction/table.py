"""Column-based line-item tables from positioned words.

1. Find the heading band (one to three rows) whose cells name at least a
   description, an amount and one of quantity/rate/HSN.
2. Turn heading cells into column bands; printed vertical rules, when present,
   override the gaps between cells.
3. Walk the rows below. A row with a number in the amount column starts an
   item; following rows extend its description until an attribute row (serial
   numbers, "HSN: …", "Place of Supply: …"), from which only HSN/SAC is kept.
   Tax and total rows inside the body become summary rows, not items.
4. A page that continues the table attaches its leading rows to the last item.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

from reconbench_core.extraction.header import norm
from reconbench_core.extraction.layout import PageLayout, TextLine, Word, full_rows, typical_height
from reconbench_core.extraction.values import Reading, read_number


class Col(StrEnum):
    SERIAL = "serial"
    DESCRIPTION = "description"
    HSN = "hsn_sac"
    QTY = "quantity"
    UOM = "uom"
    RATE = "rate"
    DISCOUNT = "discount"
    SUBTOTAL = "subtotal"
    FREIGHT = "freight"
    AMOUNT = "amount"
    TAX_RATE = "tax_rate"
    TAX = "tax"
    TOTAL_INCL = "total_incl"
    OTHER = "other"


_TEXT_COLS = frozenset({"serial", "description", "other", "hsn_sac", "uom"})
NUMERIC = frozenset(
    {
        Col.QTY,
        Col.RATE,
        Col.DISCOUNT,
        Col.SUBTOTAL,
        Col.FREIGHT,
        Col.AMOUNT,
        Col.TAX,
        Col.TOTAL_INCL,
    }
)


@dataclass(frozen=True, slots=True)
class Cell:
    words: tuple[Word, ...]
    col: Col

    @property
    def x0(self) -> int:
        return min(word.x0 for word in self.words)

    @property
    def x1(self) -> int:
        return max(word.x1 for word in self.words)

    @property
    def text(self) -> str:
        ordered = sorted(self.words, key=lambda word: (word.y0 // 20, word.x0))
        return " ".join(word.text for word in ordered)


@dataclass(frozen=True, slots=True)
class Band:
    col: Col
    x0: int
    x1: int


@dataclass(frozen=True, slots=True)
class Heading:
    page: int
    y0: int
    y1: int
    bands: tuple[Band, ...]

    def band_for(self, word: Word, height: int) -> Col | None:
        numeric = read_number(word.text) is not None
        # Right-aligned numbers are placed by their right edge, text by its centre.
        x = word.x1 - height // 2 if numeric else word.cx
        for band in self.bands:
            if band.x0 <= x < band.x1:
                return band.col
        return None

    def has(self, col: Col) -> bool:
        return any(band.col == col for band in self.bands)


@dataclass
class Item:
    page: int
    row_y: int
    values: dict[Col, list[Word]] = field(default_factory=dict)
    description_rows: list[str] = field(default_factory=list)
    item_code: str | None = None
    hsn: Reading[str] | None = None
    words: list[Word] = field(default_factory=list)

    def numbers(self, col: Col) -> list[Reading[Decimal]]:
        readings = []
        for word in self.values.get(col, []):
            reading = read_number(word.text)
            if reading is not None:
                readings.append(reading)
        return readings

    def first_number(self, col: Col) -> Reading[Decimal] | None:
        numbers = self.numbers(col)
        return numbers[0] if numbers else None

    def text(self, col: Col) -> str:
        return " ".join(word.text for word in self.values.get(col, []))


@dataclass(frozen=True, slots=True)
class SummaryRow:
    page: int
    text: str
    amount: Reading[Decimal] | None
    words: tuple[Word, ...]


@dataclass
class TableResult:
    headings: list[Heading] = field(default_factory=list)
    items: list[Item] = field(default_factory=list)
    summary_rows: list[SummaryRow] = field(default_factory=list)


def _classify(text: str) -> Col:
    tokens = [norm(token) for token in re.split(r"[\s/|]+", text) if norm(token)]
    joined = " ".join(tokens)
    has = set(tokens).__contains__
    if not tokens:
        return Col.OTHER
    if set(tokens) <= {"SL", "SI", "SR", "S", "N", "NO", "SNO", "SEQ", "SRNO", "SLNO", "SN"}:
        return Col.SERIAL
    if has("EXTENDED") or ("TOTAL" in tokens and "AMOUNT" in tokens and "TAX" not in tokens):
        return Col.TOTAL_INCL
    if any(token in {"HSN", "SAC", "HSNSAC"} for token in tokens):
        return Col.HSN
    if any(
        token in {"DESCRIPTION", "PARTICULARS", "GOODS", "PRODUCT", "ITEM", "SKU"}
        for token in tokens
    ):
        return Col.DESCRIPTION
    if any(token in {"CGST", "SGST", "IGST", "UTGST", "GST", "TAX"} for token in tokens):
        if "RATE" in tokens and "AMOUNT" not in tokens:
            return Col.TAX_RATE
        return Col.TAX
    if "TAXABLE" in tokens:
        return Col.AMOUNT
    if "SUB" in tokens and "TOTAL" in tokens:
        return Col.SUBTOTAL
    if any(token.startswith("FREIGHT") for token in tokens):
        return Col.FREIGHT
    if any(token.startswith("DIS") for token in tokens):
        return Col.DISCOUNT
    if any(token in {"QTY", "QUANTITY", "QTV", "OTY"} for token in tokens):
        return Col.QTY
    if "PRICE" in tokens or "RATE" in tokens:
        return Col.RATE
    if tokens in (["UNIT"], ["UOM"], ["PER"]):
        return Col.UOM
    if "NET" in tokens or joined in {"AMOUNT", "AMOUNT RS", "AMOUNTRS", "VALUE"}:
        return Col.AMOUNT
    if tokens == ["TOTAL"]:
        return Col.TOTAL_INCL
    return Col.OTHER


def _cells(words: Sequence[Word], splits: Sequence[int], height: int) -> list[Cell]:
    """Group heading words into cells by horizontal overlap and small gaps."""

    ordered = sorted(words, key=lambda word: word.x0)
    groups: list[list[Word]] = []
    for word in ordered:
        if groups:
            group = groups[-1]
            right = max(item.x1 for item in group)
            split_between = any(right <= split <= word.x0 for split in splits)
            if not split_between and word.x0 - right <= int(height * 0.7):
                group.append(word)
                continue
        groups.append([word])
    cells = [
        Cell(
            tuple(group),
            _classify(
                " ".join(
                    w.text for w in sorted(group, key=lambda w: (w.y0 // max(1, height), w.x0))
                )
            ),
        )
        for group in groups
    ]
    # Unknown heading fragments just left of the description belong to it ("IM SKU NUMBER").
    merged: list[Cell] = []
    for cell in cells:
        if (
            merged
            and cell.col == Col.DESCRIPTION
            and merged[-1].col == Col.OTHER
            and (cell.x0 - merged[-1].x1 <= 3 * height)
        ):
            merged[-1] = Cell(merged[-1].words + cell.words, Col.DESCRIPTION)
        else:
            merged.append(cell)
    return merged


_TAX_GROUP_WORDS = frozenset({"CGST", "SGST", "IGST", "UTGST"})


def _tax_subcolumns(cells: list[Cell], top: int, height: int) -> list[Cell]:
    """Rate and Amount sub-headings under a CGST/SGST/IGST group belong to that tax.

    Indian GST tables print each tax as a group heading with Rate and Amount
    sub-columns on the next heading row. Those sub-columns sit to the right of
    the first tax group word and below the top heading row; they are tax values,
    never the line's taxable amount or unit rate.
    """

    tax_words = [
        word for cell in cells for word in cell.words if norm(word.text) in _TAX_GROUP_WORDS
    ]
    if not tax_words:
        return cells
    group_left = min(word.x0 for word in tax_words) - 2 * height
    result: list[Cell] = []
    for cell in cells:
        lower_row = min(word.y0 for word in cell.words) >= top + int(height * 0.8)
        own_tax = any(norm(word.text) in _TAX_GROUP_WORDS for word in cell.words)
        if (
            cell.col in {Col.AMOUNT, Col.RATE}
            and lower_row
            and not own_tax
            and cell.x0 >= group_left
        ):
            cell = Cell(cell.words, Col.TAX_RATE if cell.col == Col.RATE else Col.TAX)
        result.append(cell)
    return result


def _resolve_amount(cells: list[Cell]) -> list[Cell]:
    """Exactly one AMOUNT column: the line's taxable value."""

    amounts = [index for index, cell in enumerate(cells) if cell.col == Col.AMOUNT]
    if not amounts:
        totals = [index for index, cell in enumerate(cells) if cell.col == Col.TOTAL_INCL]
        tax_positions = [
            index for index, cell in enumerate(cells) if cell.col in {Col.TAX, Col.TAX_RATE}
        ]
        for index in totals:
            if not any(position < index for position in tax_positions):
                cells[index] = Cell(cells[index].words, Col.AMOUNT)
                return cells
        return cells
    taxable = [index for index in amounts if "TAXABLE" in norm(cells[index].text)]
    keep = taxable[0] if taxable else amounts[0]
    for index in amounts:
        if index != keep:
            cells[index] = Cell(cells[index].words, Col.TOTAL_INCL)
    return cells


def _vertical_splits(page: PageLayout, y0: int, y1: int) -> list[int]:
    """Vertical rules covering at least half of the heading band's height."""

    span = max(1, y1 - y0)
    return sorted(
        rule.x0
        for rule in page.rules
        if not rule.horizontal and min(rule.y1, y1) - max(rule.y0, y0) >= span // 2
    )


_HEADING_MIN_CONF = 50
_VOCAB = (
    "SL",
    "SI",
    "SR",
    "SNO",
    "SEQ",
    "NO",
    "DESCRIPTION",
    "PARTICULARS",
    "GOODS",
    "PRODUCT",
    "ITEM",
    "SKU",
    "NUMBER",
    "HSN",
    "SAC",
    "QTY",
    "QUANTITY",
    "UNIT",
    "UOM",
    "PER",
    "RATE",
    "PRICE",
    "LIST",
    "DISC",
    "DISCOUNT",
    "SUB",
    "TOTAL",
    "FREIGHT",
    "TAXABLE",
    "VALUE",
    "AMOUNT",
    "NET",
    "TAX",
    "TYPE",
    "GST",
    "CGST",
    "SGST",
    "IGST",
    "EXTENDED",
    "CODE",
)


def _is_vocab(text: str) -> bool:
    tokens = [norm(token) for token in re.split(r"[\s/|]+", text) if norm(token)]
    return any(
        token.startswith(word) and len(token) - len(word) <= 2
        for token in tokens
        for word in _VOCAB
        if len(word) >= 3 or token == word
    )


_MONEY_TOKEN = re.compile(r"\d[\d,]*\.\d{2}")


def _looks_like_data(line: TextLine) -> bool:
    return sum(1 for word in line.words if _MONEY_TOKEN.search(word.text)) >= 1


def find_heading(page: PageLayout, rows: Sequence[TextLine], height: int) -> Heading | None:
    best: tuple[int, Heading] | None = None
    for index, row in enumerate(rows):
        if _looks_like_data(row):
            continue
        band_rows = [row]
        for follow in rows[index + 1 : index + 3]:
            if _looks_like_data(follow):
                break
            if follow.y0 - band_rows[-1].y1 <= int(height * 1.6):
                band_rows.append(follow)
        for count in range(1, len(band_rows) + 1):
            subset = band_rows[:count]
            words = [
                word
                for line in subset
                for word in line.words
                if norm(word.text) and (word.conf >= _HEADING_MIN_CONF or _is_vocab(word.text))
            ]
            if len(words) < 3:
                continue
            y0 = min(word.y0 for word in words)
            y1 = max(word.y1 for word in words)
            splits = _vertical_splits(page, y0, y1)
            cells = _resolve_amount(_tax_subcolumns(_cells(words, splits, height), y0, height))
            kinds = {cell.col for cell in cells}
            if Col.DESCRIPTION not in kinds or Col.AMOUNT not in kinds:
                continue
            support = len(kinds & {Col.QTY, Col.RATE, Col.HSN, Col.SUBTOTAL, Col.FREIGHT})
            if support == 0:
                continue
            noise = sum(1 for cell in cells if cell.col == Col.OTHER)
            score = len(kinds) * 10 + support * 5 - noise * 3 + count
            heading = Heading(page.number, y0, y1, _bands(cells, page, splits))
            if best is None or score > best[0]:
                best = (score, heading)
    return best[1] if best else None


def _bands(cells: Sequence[Cell], page: PageLayout, splits: Sequence[int] = ()) -> tuple[Band, ...]:
    ordered = sorted(cells, key=lambda cell: cell.x0)
    edges = [0]
    for index in range(1, len(ordered)):
        previous, cell = ordered[index - 1], ordered[index]
        ruled = [split for split in splits if previous.x1 <= split <= cell.x0]
        if ruled:
            # A printed column border is the true boundary; right-aligned values
            # run up to it, well past their heading word.
            middle = (previous.x1 + cell.x0) / 2
            edges.append(min(ruled, key=lambda split: abs(split - middle)))
        elif cell.col == Col.DESCRIPTION:
            # Descriptions are left-aligned: they start right after the previous column.
            edges.append(previous.x1 + 2)
        elif previous.col == Col.DESCRIPTION and cell.col not in NUMERIC:
            # A left-aligned column (HSN, UOM) after a long description: wrapped
            # description text such as "Box of 500" runs well past the heading
            # word, so most of the gap belongs to the description.
            edges.append(cell.x0 - (cell.x0 - previous.x1) // 4)
        else:
            edges.append((previous.x1 + cell.x0) // 2)
    edges.append(page.width)
    return tuple(
        Band(cell.col, edges[index], edges[index + 1]) for index, cell in enumerate(ordered)
    )


_STOP = re.compile(
    r"^(SUBTOTAL|SUB TOTAL|TOTAL|TOTEL|GRAND TOTAL|AMOUNT CHARGEABLE|AMOUNT IN WORDS|"
    r"RUPEES|INR|TAX AMOUNT|CONTINUED|TOTAL VALUE)\b"
)
_SUMMARY = re.compile(
    r"\b(CGST|SGST|IGST|UTGST|OUTPUT|ROUND|ROUNDED|ROUNDOFF|TOTAL|TOTEL|SUBTOTAL)\b"
)
_TOTAL_WORD = re.compile(r"\b(TOTAL|TOTEL|SUBTOTAL|GRAND TOTAL)\b")
# A tax or rounding row inside the item body ("Input IGST @ 18%", "Less: Rounding Off").
_TAX_ROW = re.compile(
    r"^(?:(?:INPUT|OUTPUT|ADD|LESS)\s+)?(?:CGST|SGST|IGST|UTGST|ROUND(?:ING|ED)?\s*OFF)\b"
)
_ATTRIBUTE = re.compile(
    r"(:|#|\*\*\*|"
    r"^(SERIAL|S/?N|SR\.? ?NO|TAG|HSN|SAC|TYPE|VEND|EU|RE |MC|PLACE OF|ORDER|BATCH|IMEI))",
    re.I,
)
_HSN = re.compile(r"\b(?:HSN|SAC)\s*(?:CODE)?\s*[:#-]?\s*([0-9@O.]{4,12})", re.I)
_HSN_SHAPE = re.compile(r"^(?:\d{4}|\d{6}|\d{8})$")
_SMALL_QTY = re.compile(r"^\d{1,5}(?:\.0{1,3})?$")
_CODE_LIKE = re.compile(r"^[A-Z0-9][A-Z0-9/#._-]{5,}$", re.I)


_UOM_WORDS = frozenset(
    {
        "NO",
        "NOS",
        "NUMBER",
        "NUMBERS",
        "PCS",
        "PC",
        "EA",
        "EACH",
        "UNIT",
        "UNITS",
        "SET",
        "SETS",
        "LOT",
        "KG",
        "KGS",
        "MTR",
        "MTRS",
        "M",
        "BOX",
        "PKT",
        "PAIR",
        "LTR",
        "ROLL",
    }
)


def _qty_uom_pair(words: Sequence[Word]) -> tuple[Word, Word] | None:
    """A number immediately followed by a unit word, e.g. "1.00 NOS" or "2 EA"."""

    for index in range(len(words) - 1):
        number = read_number(words[index].text)
        unit = norm(words[index + 1].text)
        if (
            number is not None
            and not number.repaired
            and number.value > 0
            and unit in _UOM_WORDS
            and words[index + 1].x0 - words[index].x1 < 4 * max(1, words[index].height)
        ):
            return words[index], words[index + 1]
    return None


def _drop_leading_noise(text: str) -> str:
    """Drop a leading serial number or OCR speck before the description proper."""

    tokens = text.split()
    while len(tokens) > 1 and (
        (len(tokens[0]) <= 2 and not tokens[0].isalpha())
        or (len(tokens[0]) == 1)
        or tokens[0].isdigit()
        and len(tokens[0]) <= 3
    ):
        tokens = tokens[1:]
    # OCR reads a cell border or speck just before the first letter as a quote.
    return " ".join(tokens).lstrip("\"'`|_,;:")


def _row_text(words: Sequence[Word]) -> str:
    return " ".join(word.text for word in words)


def _is_code_like(text: str) -> bool:
    token = text.strip()
    return (
        " " not in token
        and bool(_CODE_LIKE.match(token))
        and any(char.isdigit() for char in token)
        and sum(char.isdigit() for char in token) >= 3
    )


def _hsn_from(text: str) -> Reading[str] | None:
    match = _HSN.search(text)
    if not match:
        return None
    digits = match.group(1).replace("@", "0").replace("O", "0").replace(".", "")
    if not digits.isdigit() or len(digits) not in {4, 6, 8}:
        return None
    return Reading(digits, match.group(0), digits != match.group(1).replace(".", ""))


def _hsn_cell(words: Sequence[Word]) -> Reading[str] | None:
    for word in words:
        raw = word.text.replace(".", "")
        digits = raw.replace("@", "0").replace("O", "0")
        if digits.isdigit() and len(digits) in {4, 6, 8}:
            return Reading(digits, word.text, digits != raw)
    return None


def extract_tables(pages: Sequence[PageLayout]) -> TableResult:
    result = TableResult()
    found: dict[int, tuple[Heading, tuple[TextLine, ...], int]] = {}
    for page in pages:
        if not page.words:
            continue
        height = typical_height(page.words)
        rows = full_rows(page)
        heading = find_heading(page, rows, height)
        if heading is not None:
            found[page.number] = (heading, rows, height)
    # Pages of one document share the layout; a heading unreadable on one page
    # (ticks, stamps) is taken from the page where it was read most completely.
    master = max(
        (item[0] for item in found.values()),
        key=lambda heading: len({band.col for band in heading.bands} - {Col.OTHER}),
        default=None,
    )
    for page in pages:
        if page.number not in found:
            continue
        heading, rows, height = found[page.number]
        richer = master is not None and len({b.col for b in master.bands}) > len(
            {b.col for b in heading.bands}
        )
        if (
            master is not None
            and master is not heading
            and (_compatible(master, heading) or richer)
        ):
            heading = Heading(page.number, heading.y0, heading.y1, master.bands)
        result.headings.append(heading)
        current: Item | None = result.items[-1] if result.items else None
        open_description = False
        for row in rows:
            if row.cy <= heading.y1:
                continue
            by_col: dict[Col, list[Word]] = {}
            for word in row.words:
                col = heading.band_for(word, height)
                if col is not None:
                    by_col.setdefault(col, []).append(word)
            if not heading.has(Col.HSN):
                for col in list(by_col):
                    if col not in NUMERIC:
                        continue
                    numbers = [w for w in by_col[col] if read_number(w.text)]
                    if len(numbers) < 3:
                        continue
                    for word in numbers:
                        if _HSN_SHAPE.match(word.text):
                            by_col[col].remove(word)
                            by_col.setdefault(Col.HSN, []).append(word)
                            break
            if not heading.has(Col.QTY):
                rate_words = [w for w in by_col.get(Col.RATE, []) if read_number(w.text)]
                if len(rate_words) >= 2 and _SMALL_QTY.match(rate_words[0].text):
                    by_col[Col.QTY] = [rate_words[0]]
                    by_col[Col.RATE] = rate_words[1:]
            text = _row_text(row.words)
            upper = norm_spaces(text)
            description = _row_text(by_col.get(Col.DESCRIPTION, []))
            left_text = norm_spaces(
                _row_text([w for c, ws in by_col.items() if c in _TEXT_COLS for w in ws])
            )
            amount_words = sorted(by_col.get(Col.AMOUNT, []), key=lambda word: word.x1)
            # Right-aligned: the amount is the rightmost number in its column.
            amount = next((r for w in reversed(amount_words) if (r := read_number(w.text))), None)
            if _STOP.match(left_text) or _STOP.match(upper):
                if amount is not None or any(read_number(w.text) for w in row.words):
                    result.summary_rows.append(
                        SummaryRow(page.number, text, amount, tuple(row.words))
                    )
                if "CONTINUED" in upper or _STOP.match(left_text):
                    break
                continue
            if not any(read_number(w.text) for w in by_col.get(Col.QTY, [])):
                pair = _qty_uom_pair(
                    [w for w in row.words if heading.band_for(w, height) != Col.DESCRIPTION]
                )
                if pair is not None:
                    for col_words in by_col.values():
                        if pair[0] in col_words:
                            col_words.remove(pair[0])
                    by_col[Col.QTY] = [pair[0]]
                    by_col.setdefault(Col.UOM, [pair[1]])
            has_qty = any(read_number(w.text) for w in by_col.get(Col.QTY, []))
            has_rate = any(read_number(w.text) for w in by_col.get(Col.RATE, []))
            if amount is not None and _TOTAL_WORD.search(upper) and not (has_qty and has_rate):
                # A totals row whose "Total" label sits in a numeric column.
                result.summary_rows.append(SummaryRow(page.number, text, amount, tuple(row.words)))
                break
            # "Input IGST @ 18%  18 %  800.70": the rate column holds the tax percent.
            if (
                amount is not None
                and not has_qty
                and (not has_rate or _TAX_ROW.match(_drop_leading_noise(left_text)))
                and _SUMMARY.search(left_text)
            ):
                result.summary_rows.append(SummaryRow(page.number, text, amount, tuple(row.words)))
                open_description = False
                continue
            has_text = sum(char.isalnum() for char in description) >= 3
            if amount is not None and has_text:
                current = Item(page=page.number, row_y=row.cy)
                current.values = by_col
                current.words = list(row.words)
                description = _drop_leading_noise(description)
                first = description.split(" ", 1)
                if (
                    description
                    and _is_code_like(first[0])
                    and (len(first) == 1 or _is_code_like(first[0]))
                ):
                    current.item_code = first[0]
                    rest = first[1] if len(first) > 1 else ""
                    if rest.strip():
                        current.description_rows.append(rest.strip())
                elif description:
                    current.description_rows.append(description)
                current.hsn = _hsn_cell(by_col.get(Col.HSN, []))
                result.items.append(current)
                open_description = True
                continue
            if current is None:
                continue
            for col in (Col.TAX, Col.TAX_RATE, Col.TOTAL_INCL):
                extra = [w for w in by_col.get(col, []) if read_number(w.text) or "%" in w.text]
                if extra and current.page == page.number:
                    current.values.setdefault(col, []).extend(extra)
            hsn = _hsn_from(text)
            if hsn is not None and current.hsn is None:
                current.hsn = hsn
            if not open_description or not description.strip():
                continue
            if _ATTRIBUTE.search(description) or _is_code_like(description):
                open_description = False
                continue
            if sum(char.isalpha() for char in description) < 3:
                continue
            if len(current.description_rows) >= 3:
                open_description = False
                continue
            current.description_rows.append(description)
    return result


def _compatible(master: Heading, heading: Heading) -> bool:
    """Same table layout: the amount columns line up."""

    def amount(h: Heading) -> Band | None:
        return next((band for band in h.bands if band.col == Col.AMOUNT), None)

    a, b = amount(master), amount(heading)
    if a is None or b is None:
        return False
    overlap = min(a.x1, b.x1) - max(a.x0, b.x0)
    return overlap >= min(a.x1 - a.x0, b.x1 - b.x0) // 2


def norm_spaces(text: str) -> str:
    return " ".join(re.sub(r"[^A-Z0-9 ]", " ", text.upper()).split())


__all__ = [
    "Band",
    "Col",
    "Heading",
    "Item",
    "SummaryRow",
    "TableResult",
    "extract_tables",
    "find_heading",
]
