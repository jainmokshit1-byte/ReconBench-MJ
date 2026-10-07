"""OCR-tolerant readings of amounts, quantities, dates, GSTINs and amounts in words.

Every reading reports whether characters were repaired (for example ``@`` or
``O`` read for ``0``). A repaired value is never trusted on its own: it is only
accepted when a cross-check (arithmetic or checksum) confirms it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from itertools import product

from reconbench_core.normalize.gstin import validate_gstin

# Characters OCR commonly reads in place of a digit, inside an otherwise numeric token.
_DIGIT_REPAIRS = {
    "@": "0",
    "O": "0",
    "o": "0",
    "D": "0",
    "Q": "0",
    "l": "1",
    "I": "1",
    "|": "1",
    "i": "1",
    "S": "5",
    "s": "5",
    "B": "8",
    "Z": "2",
    "z": "2",
    "G": "6",
    "b": "6",
    "g": "9",
}
_EDGE_JUNK = "()[]{}_|'\"`~*,.:;-/\\!?"
_CURRENCY = re.compile(r"^(?:₹|Rs\.?|INR|Z|%|\?)\s*", re.I)
_AMOUNT_SHAPE = re.compile(r"^-?\d{1,3}(?:,\d{2,3})*(?:\.\d{1,4})?$|^-?\d+(?:\.\d{1,4})?$")


@dataclass(frozen=True, slots=True)
class Reading[T]:
    value: T
    raw: str
    repaired: bool = False


def _strip_edges(token: str) -> str:
    text = token.strip()
    text = _CURRENCY.sub("", text)
    while text and not text[-1].isdigit() and text[-1] != ")" and text[-1] not in _DIGIT_REPAIRS:
        text = text[:-1]
    while text and text[0] in _EDGE_JUNK and text[0] not in "-(":
        if text[0] == "." and len(text) > 1 and text[1].isdigit():
            break
        text = text[1:]
    return text


def read_number(token: str, *, max_repairs: int = 2) -> Reading[Decimal] | None:
    """Read an amount/quantity token such as ``1,24,250.00`` or ``2,979,420.0@``.

    Accepts Indian and international grouping and parenthesised negatives.
    Up to ``max_repairs`` look-alike characters are repaired, but only when the
    token is otherwise digits and separators.
    """

    text = token.strip()
    negative = False
    if text.startswith("-"):
        negative, text = True, text[1:]
    text = _strip_edges(text)
    if not text:
        return None
    if text.startswith("(") and text.endswith(")"):
        negative = True
    text = text.strip("()")
    if text.startswith("-"):
        negative, text = True, text[1:]
    if text.startswith("."):
        text = "0" + text
    repaired = 0
    chars: list[str] = []
    for char in text:
        if char.isdigit() or char in ",.":
            chars.append(char)
        elif char in _DIGIT_REPAIRS:
            chars.append(_DIGIT_REPAIRS[char])
            repaired += 1
        else:
            return None
    if repaired > max_repairs or sum(char.isdigit() for char in chars) < max(1, repaired * 2):
        return None
    candidate = "".join(chars).strip(",")
    if "." not in candidate and re.fullmatch(r"\d{4,}(?:,\d{3})*,\d{2}", candidate):
        # "20689,01": OCR read the decimal point as a comma.
        head, tail = candidate.rsplit(",", 1)
        candidate = head.replace(",", "") + "." + tail
        repaired += 1
    if candidate.count(".") > 1 or not _AMOUNT_SHAPE.match(candidate):
        return None
    try:
        value = Decimal(candidate.replace(",", ""))
    except InvalidOperation:
        return None
    if negative:
        value = -value
    return Reading(value, token, repaired > 0)


_MONTHS = {
    name: index
    for index, names in enumerate(
        (
            ("JAN", "JANUARY"),
            ("FEB", "FEBRUARY"),
            ("MAR", "MARCH"),
            ("APR", "APRIL"),
            ("MAY",),
            ("JUN", "JUNE"),
            ("JUL", "JULY"),
            ("AUG", "AUGUST"),
            ("SEP", "SEPT", "SEPTEMBER"),
            ("OCT", "OCTOBER"),
            ("NOV", "NOVEMBER"),
            ("DEC", "DECEMBER"),
        ),
        start=1,
    )
    for name in names
}
_NUMERIC_DATE = re.compile(r"(?<![\d.])(\d{1,4})[./-](\d{1,2})[./-](\d{2,4})(?![\d.])")
_MONTH_NAMES = (
    "JANUARY|FEBRUARY|MARCH|APRIL|JUNE|JULY|AUGUST|SEPTEMBER|SEPT|OCTOBER|NOVEMBER|DECEMBER"
    "|JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC"
)
_NAMED_DATE = re.compile(
    rf"(?<!\d)(\d{{1,2}})[\s./-]*({_MONTH_NAMES})[\s.,/-]*(\d{{2,4}})(?!\d)", re.I
)
# A month name with its last letter lost ("12- Au -26"), only where two letters
# name one month and the date keeps its dashes. The reading counts as repaired.
_SHORT_MONTHS = {"JA": 1, "FE": 2, "AP": 4, "AU": 8, "SE": 9, "OC": 10, "NO": 11, "DE": 12}
_SHORT_MONTH_DATE = re.compile(
    r"(?<![\d.])(\d{1,2})\s*-\s*(JA|FE|AP|AU|SE|OC|NO|DE)\s*-\s*(\d{2})(?![\d.])", re.I
)


def _year(raw: str) -> int:
    value = int(raw)
    return 2000 + value if value < 100 else value


def find_dates(text: str) -> list[Reading[date]]:
    """All calendar dates in ``text`` (day-first for slash dates, as in India)."""

    found: list[tuple[int, Reading[date]]] = []
    for match in _NUMERIC_DATE.finditer(text):
        first, second, third = match.groups()
        try:
            if len(first) == 4:
                value = date(int(first), int(second), int(third))
            else:
                value = date(_year(third), int(second), int(first))
        except ValueError:
            continue
        found.append((match.start(), Reading(value, match.group(0))))
    for match in _NAMED_DATE.finditer(text):
        day, month_name, year = match.groups()
        month = _MONTHS.get(month_name.upper())
        if month is None:
            continue
        try:
            value = date(_year(year), month, int(day))
        except ValueError:
            continue
        found.append((match.start(), Reading(value, match.group(0))))
    for match in _SHORT_MONTH_DATE.finditer(text):
        day, month_name, year = match.groups()
        try:
            value = date(_year(year), _SHORT_MONTHS[month_name.upper()], int(day))
        except ValueError:
            continue
        found.append((match.start(), Reading(value, match.group(0), True)))
    found.sort(key=lambda item: item[0])
    return [reading for _position, reading in found]


def parse_iso_date(value: str) -> date | None:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


# GSTIN: 2 digits (state), PAN (5 letters, 4 digits, 1 letter), entity, 'Z', checksum.
_GSTIN_KINDS = "DDLLLLLDDDDLAZA"
_TO_DIGIT = {
    "O": "0",
    "D": "0",
    "Q": "0",
    "I": "1",
    "L": "1",
    "Z": "2",
    "S": "5",
    "G": "6",
    "B": "8",
    "T": "7",
}
_TO_LETTER = {"0": "O", "1": "I", "2": "Z", "5": "S", "6": "G", "8": "B", "7": "T", "4": "A"}
_GSTIN_LIKE = re.compile(r"[0-9A-Z]{15}")
# More look-alike swaps than this turn arbitrary text (a PO number and a date)
# into some checksum-valid GSTIN; a real misreading rarely needs more.
_MAX_GSTIN_REPAIRS = 2


def _gstin_options(char: str, kind: str) -> tuple[str, ...]:
    if kind == "D":
        return (char,) if char.isdigit() else ((_TO_DIGIT[char],) if char in _TO_DIGIT else ())
    if kind == "L":
        return (char,) if char.isalpha() else ((_TO_LETTER[char],) if char in _TO_LETTER else ())
    if kind == "Z":
        return ("Z",) if char in {"Z", "2", "7"} else ()
    swapped = _TO_DIGIT.get(char) if char.isalpha() else _TO_LETTER.get(char)
    return (char, swapped) if swapped else (char,)


def read_gstin(token: str) -> Reading[str] | None:
    """A checksum-valid GSTIN, repairing look-alike characters only if unambiguous."""

    text = re.sub(r"[^0-9A-Za-z]", "", token).upper()
    for start in range(0, max(1, len(text) - 14)):
        window = text[start : start + 15]
        if len(window) != 15:
            break
        if validate_gstin(window).is_valid:
            return Reading(window, token, False)
        options = [
            _gstin_options(char, kind) for char, kind in zip(window, _GSTIN_KINDS, strict=True)
        ]
        if any(not option for option in options):
            continue
        variants = 1
        for option in options:
            variants *= len(option)
        if variants > 64:
            continue
        valid = {
            "".join(choice)
            for choice in product(*options)
            if sum(a != b for a, b in zip(choice, window, strict=True)) <= _MAX_GSTIN_REPAIRS
            and validate_gstin("".join(choice)).is_valid
        }
        if len(valid) == 1:
            value = valid.pop()
            return Reading(value, token, value != window)
    return None


def gstins_in(text: str) -> list[Reading[str]]:
    compact = re.sub(r"[^0-9A-Za-z]", "", text).upper()
    found: list[Reading[str]] = []
    seen: set[str] = set()
    for match in _GSTIN_LIKE.finditer(compact):
        reading = read_gstin(match.group(0))
        if reading is not None and reading.value not in seen:
            seen.add(reading.value)
            found.append(reading)
    if not found:
        for index in range(0, max(0, len(compact) - 14)):
            reading = read_gstin(compact[index : index + 15])
            if reading is not None and reading.value not in seen:
                seen.add(reading.value)
                found.append(reading)
    return found


_UNITS = {
    "ZERO": 0,
    "ONE": 1,
    "TWO": 2,
    "THREE": 3,
    "FOUR": 4,
    "FIVE": 5,
    "SIX": 6,
    "SEVEN": 7,
    "EIGHT": 8,
    "NINE": 9,
    "TEN": 10,
    "ELEVEN": 11,
    "TWELVE": 12,
    "THIRTEEN": 13,
    "FOURTEEN": 14,
    "FIFTEEN": 15,
    "SIXTEEN": 16,
    "SEVENTEEN": 17,
    "EIGHTEEN": 18,
    "NINETEEN": 19,
    "TWENTY": 20,
    "THIRTY": 30,
    "FORTY": 40,
    "FOURTY": 40,
    "FIFTY": 50,
    "SIXTY": 60,
    "SEVENTY": 70,
    "EIGHTY": 80,
    "NINETY": 90,
    "NINTY": 90,
}
_SCALES = {
    "HUNDRED": 100,
    "THOUSAND": 1_000,
    "LAKH": 100_000,
    "LAKHS": 100_000,
    "LAC": 100_000,
    "LACS": 100_000,
    "CRORE": 10_000_000,
    "CRORES": 10_000_000,
    "MILLION": 1_000_000,
    "BILLION": 1_000_000_000,
}


def _number_run(tokens: list[str]) -> list[str] | None:
    """The number words in ``tokens``, ignoring trailing noise such as "E & O E".

    Noise between number words ("Sixty One Tines Hundred") makes the reading
    ambiguous, so nothing is returned.
    """

    known = set(_UNITS) | set(_SCALES) | {"AND"}
    start = next((index for index, token in enumerate(tokens) if token in known), None)
    if start is None:
        return None
    end = start
    while end < len(tokens) and tokens[end] in known:
        end += 1
    if any(token in known and token != "AND" for token in tokens[end:]):
        return None
    if any(token not in known and len(token) > 2 for token in tokens[:start]):
        return None
    return tokens[start:end]


def _words_to_int(words: list[str]) -> int | None:
    total = 0
    current = 0
    seen = False
    for word in words:
        if word in _UNITS:
            current += _UNITS[word]
            seen = True
        elif word == "HUNDRED":
            current = max(current, 1) * 100
            seen = True
        elif word in _SCALES:
            total += max(current, 1) * _SCALES[word]
            current = 0
            seen = True
        elif word in {"AND"}:
            continue
        else:
            return None
    return total + current if seen else None


def amount_in_words(text: str) -> Decimal | None:
    """Read e.g. 'INR One Lakh Forty Six Thousand Six Hundred Fifteen Only'."""

    upper = re.sub(r"[^A-Z ]", " ", text.upper().replace("-", " "))
    tokens = [token for token in upper.split() if token]
    for noise in (
        "INR",
        "RUPEES",
        "RUPEE",
        "INDIAN",
        "RS",
        "ONLY",
        "AMOUNT",
        "CHARGEABLE",
        "IN",
        "WORDS",
        "TOTAL",
        "TAX",
        "THE",
        "SUM",
        "OF",
    ):
        tokens = [token for token in tokens if token != noise]
    if not tokens:
        return None
    paise: list[str] = []
    if "PAISE" not in tokens and "PAISA" not in tokens:
        run = _number_run(tokens)
        if run is None:
            return None
        tokens = run
    for marker in ("PAISE", "PAISA"):
        if marker in tokens:
            index = tokens.index(marker)
            before = tokens[:index]
            if "AND" in before:
                split = len(before) - before[::-1].index("AND") - 1
                paise, tokens = before[split + 1 :], before[:split]
            else:
                tokens = before
    rupees = _words_to_int(tokens)
    if rupees is None:
        return None
    value = Decimal(rupees)
    if paise:
        cents = _words_to_int(paise)
        if cents is None or cents >= 100:
            return None
        value += Decimal(cents) / 100
    return value.quantize(Decimal("0.01"))


_LETTER_FORMS = frozenset("OoIlLSsBZ|")
_DIGIT_FORMS = frozenset("01582")


def has_lookalike(text: str) -> bool:
    """True when a letter/digit look-alike sits where either reading is plausible.

    "PAPLO826" (O between a letter and a digit) or "76ID26" (I after a digit) are
    ambiguous; "INV" or "BOM5" are not.
    """

    for index, char in enumerate(text):
        if char not in _LETTER_FORMS and char not in _DIGIT_FORMS:
            continue
        left = text[index - 1] if index > 0 else ""
        right = text[index + 1] if index + 1 < len(text) else ""
        sides = {
            "digit" if side.isdigit() else "letter" if side.isalpha() else "other"
            for side in (left, right)
        }
        if char in _LETTER_FORMS and "digit" in sides:
            return True
        if char in _DIGIT_FORMS and sides == {"digit", "letter"}:
            return True
    return False


__all__ = [
    "Reading",
    "has_lookalike",
    "amount_in_words",
    "find_dates",
    "gstins_in",
    "parse_iso_date",
    "read_gstin",
    "read_number",
]
