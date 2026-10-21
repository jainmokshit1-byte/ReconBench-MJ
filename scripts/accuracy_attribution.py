"""Per-stage error attribution for the accuracy bench (scan fix plan WI-1).

Reads only the bench's cached reader outputs for one bench report (no OCR, no
provider): run ``make accuracy-bench`` first, then

    make accuracy-attribution REPORT=work/accuracy-reports/<name>.json

For every truth header field and line cell on every parsed PDF document, a miss
is classified by where it was lost (diagnosis Dx §5.1):

* ``A-absent`` - the value is not in the document's page text at all (OCR/image);
* ``A-glyph``  - it is there only after look-alike folding (O/0, I/1, S/5 ...);
* ``B``        - it is in the text but the rules or table reader missed it;
* ``C``        - party role: the buyer's GSTIN or name was taken as the vendor's;
* ``D``        - the value is only on another page of the file (page split);
* ``E``        - normalisation: a format or day/month difference of a found value.

Documents are labelled by scan type from their pixels (``digital``, ``scan-clean``,
``scan-tilted``, ``scan-rotated``, ``scan-lowdpi``, ``scan-skew>5``, plus
``+handwritten``); the labels are cached in ``work/accuracy-cache/scan-types.json``.
Only counts and masked patterns (``L`` letter, ``D`` digit) are printed.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT))

from rapidfuzz import fuzz  # noqa: E402
from reconbench_core.parsers.types import ParseResult  # noqa: E402
from reconbench_core.schema import Document  # noqa: E402

from scripts import accuracy_bench as bench  # noqa: E402

FOLD = str.maketrans(
    {
        "O": "0",
        "Q": "0",
        "D": "0",
        "I": "1",
        "L": "1",
        "|": "1",
        "S": "5",
        "B": "8",
        "Z": "2",
        "G": "6",
    }
)
MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
GROUPS = {
    "doc_no": "doc_no",
    "doc_date": "date",
    "vendor_gstin": "vendor/gstin",
    "vendor_name": "vendor/gstin",
    "buyer_gstin": "vendor/gstin",
    "po_refs": "po/grn refs",
    "taxable_total": "totals/tax",
    "cgst": "totals/tax",
    "sgst": "totals/tax",
    "igst": "totals/tax",
    "round_off": "totals/tax",
    "grand_total": "totals/tax",
}
_PARTY_FIELDS = {"vendor_gstin", "vendor_name", "buyer_gstin"}
DIGITAL = "digital"


class StaleReport(RuntimeError):
    """The cache or working tree no longer matches the report: re-run the bench."""


# --------------------------------------------------------------------------- text helpers


def collapse(text: str) -> str:
    return re.sub(r"\s+", "", text.upper())


def alnum(text: str) -> str:
    return re.sub(r"[^0-9A-Z]", "", text.upper())


def fold(text: str) -> str:
    return alnum(text).translate(FOLD)


def mask(value: object) -> str:
    return re.sub(r"\d", "D", re.sub(r"[A-Za-z]", "L", str(value)))


def money_forms(value: Decimal) -> set[str]:
    """Plain, western-grouped and Indian-grouped spellings of an amount."""

    whole, frac = f"{value.quantize(Decimal('0.01')):.2f}".split(".")
    negative = whole.startswith("-")
    whole = whole.lstrip("-")
    western = f"{int(whole):,}"
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        parts: list[str] = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        indian = ",".join(parts) + "," + tail
    else:
        indian = whole
    forms: set[str] = set()
    for base in {whole, western, indian}:
        forms.add(f"{base}.{frac}")
        if frac == "00":
            forms.add(base)
    return {("-" if negative else "") + item for item in forms}


def date_forms(value: str) -> set[str]:
    parsed = date.fromisoformat(value)
    days = {f"{parsed.day:02d}", str(parsed.day)}
    months = {f"{parsed.month:02d}", str(parsed.month)}
    years = {str(parsed.year), f"{parsed.year % 100:02d}"}
    month_name = MONTHS[parsed.month - 1]
    forms: set[str] = set()
    for day in days:
        for year in years:
            for month in months:
                for sep in "/-.":
                    forms.add(f"{day}{sep}{month}{sep}{year}")
            for sep in ("-", "", "/", "."):
                forms.add(f"{day}{sep}{month_name}{sep}{year}")
    return {collapse(item) for item in forms}


def present(name: str, truth_value: Any, text: str) -> str:
    """Whether a truth value is in the text: ``exact``, ``folded``, ``absent`` or ``na``."""

    flat = collapse(text)
    if truth_value in (None, "", []):
        return "na"
    if name in bench._MONEY:
        amount = bench._decimal(truth_value)
        if amount is None:
            return "na"
        forms = money_forms(abs(amount))
    elif name == "doc_date":
        forms = date_forms(str(truth_value))
    else:
        forms = set()
    if forms:
        if any(form in flat for form in forms):
            return "exact"
        folded = flat.translate(FOLD)
        return "folded" if any(form.translate(FOLD) in folded for form in forms) else "absent"
    if name == "vendor_name":
        lines = [line.upper() for line in text.splitlines() if line.strip()]
        target = " ".join(str(truth_value).upper().split())
        best = max((fuzz.partial_ratio(target, line) for line in lines), default=0)
        return "exact" if best >= 90 else ("folded" if best >= 75 else "absent")
    values = truth_value if isinstance(truth_value, (list, tuple)) else [truth_value]
    plain_text, folded_text = alnum(text), fold(text)
    states: list[str] = []
    for item in values:
        key = alnum(str(item))
        if not key:
            continue
        if key in plain_text:
            states.append("exact")
        elif key.translate(FOLD) in folded_text:
            states.append("folded")
        else:
            states.append("absent")
    if not states:
        return "na"
    for state in ("absent", "folded"):
        if state in states:
            return state
    return "exact"


def lenient_equal(name: str, truth_value: Any, extracted: Any) -> bool:
    """The extracted value differs from truth only by format or look-alikes (stage E)."""

    if extracted is None:
        return False
    if name in bench._MONEY:
        expected, got = bench._decimal(truth_value), bench._decimal(extracted)
        if expected is None or got is None or expected == 0:
            return False
        return any(got == expected * Decimal(10) ** k for k in (-2, -1, 1, 2)) or abs(got) == abs(
            expected
        )
    if name == "doc_date":
        expected_date = date.fromisoformat(str(truth_value))
        got_date = extracted if isinstance(extracted, date) else date.fromisoformat(str(extracted))
        return (expected_date.day, expected_date.month) == (
            got_date.month,
            got_date.day,
        ) and expected_date.year == got_date.year
    if name == "po_refs":
        expected_refs = truth_value if isinstance(truth_value, list) else [truth_value]
        got_refs = (
            extracted if isinstance(extracted, (list, tuple, set, frozenset)) else [extracted]
        )
        return bool({fold(str(x)) for x in expected_refs} & {fold(str(x)) for x in got_refs})
    if name == "vendor_name":
        return False
    return fold(str(truth_value)) == fold(str(extracted))


def plain(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        return [bench._plain(getattr(item, "value", item)) for item in value]
    return bench._plain(getattr(value, "value", value))


# --------------------------------------------------------------------------- classification


@dataclass(frozen=True)
class Party:
    """The workspace's own GSTINs and names, as the bench run used them."""

    gstins: frozenset[str] = frozenset()
    names: tuple[str, ...] = ()


def header_stage(
    name: str,
    outcome: str,
    truth_value: Any,
    extracted: Any,
    *,
    doc_text: str,
    file_text: str,
    split: bool,
    party: Party,
) -> str:
    """The stage that lost one header field (``outcome`` is the bench's field label)."""

    state = present(name, truth_value, doc_text)
    extracted_up = str(extracted).upper() if extracted is not None else ""
    if (
        name in _PARTY_FIELDS
        and extracted is not None
        and (
            extracted_up in party.gstins
            or any(own and fuzz.ratio(own, extracted_up) >= 85 for own in party.names)
            or (
                name == "vendor_gstin"
                and extracted_up
                and extracted_up in collapse(doc_text)
                and state == "exact"
            )
            or (
                name == "vendor_name"
                and bench._normal("vendor_name", extracted)
                and extracted_up.strip("() ").lower() in bench._LABEL_NAMES
            )
        )
    ):
        stage = "C"
    elif (
        state in {"absent", "folded"}
        and split
        and present(name, truth_value, file_text) == ("exact")
    ):
        stage = "D"
    elif state == "exact" and lenient_equal(name, truth_value, extracted):
        stage = "E"
    elif state == "folded":
        stage = "A-glyph"
    elif state == "absent":
        stage = "A-absent"
    else:
        stage = "B"
    if outcome == "unexpected":
        stage = "C" if stage == "C" else "B"
    return stage


@dataclass(frozen=True)
class CellMiss:
    cell: str
    stage: str
    pattern: str


def _decimal_of(value: Any) -> Decimal | None:
    return bench._decimal(bench._plain(value.value)) if value is not None else None


def line_cells(
    truth_lines: Sequence[Mapping[str, Any]], document: Document, doc_text: str
) -> tuple[Counter[str], list[CellMiss]]:
    """Correct cell counts and the stage of each missed line cell of one document."""

    ok: Counter[str] = Counter()
    misses: list[CellMiss] = []
    lines = list(document.lines)
    used: set[int] = set()
    for item in truth_lines:
        amount = bench._decimal(item.get("amount"))
        qty = bench._decimal(item.get("quantity"))
        rate = bench._decimal(item.get("rate"))
        match = None
        for index, line in enumerate(lines):
            if index in used:
                continue
            got_amount = _decimal_of(line.amount)
            got_qty = bench._line_qty(line)
            got_rate = _decimal_of(line.rate)
            if amount is not None and got_amount != amount:
                continue
            if amount is None and (qty is None or got_qty != qty):
                continue
            if qty is not None and got_qty is not None and got_qty != qty:
                continue
            if rate is not None and got_rate is not None and got_rate != rate:
                continue
            match = index
            break
        if match is not None:
            used.add(match)
        found = lines[match] if match is not None else None
        kind = "row_missing" if found is None else "cell_wrong"
        cells = {
            "amount": (amount, found.amount if found else None),
            "qty": (
                qty,
                (found.quantity or found.received_qty or found.accepted_qty) if found else None,
            ),
            "rate": (rate, found.rate if found else None),
        }
        for cell, (expected, got) in cells.items():
            if expected is None:
                continue
            if _decimal_of(got) == expected:
                ok[cell] += 1
                continue
            state = present("taxable_total", expected, doc_text)
            stage = {"exact": "B", "folded": "A-glyph", "absent": "A-absent"}.get(state, "B")
            misses.append(CellMiss(cell, stage, f"line.{cell}:{kind}:{stage}"))
        if item.get("description"):
            expected_text = " ".join(str(item["description"]).upper().split())
            got_text = (
                " ".join(str(bench._plain(found.description.value)).upper().split())
                if found and found.description
                else ""
            )
            if got_text and fuzz.ratio(expected_text, got_text) >= 85:
                ok["description"] += 1
            else:
                best = max(
                    (
                        fuzz.partial_ratio(expected_text, row.upper())
                        for row in doc_text.splitlines()
                        if row.strip()
                    ),
                    default=0,
                )
                stage = "B" if best >= 85 else ("A-glyph" if best >= 65 else "A-absent")
                text_kind = "row_missing" if found is None else "text_wrong"
                misses.append(
                    CellMiss("description", stage, f"line.description:{text_kind}:{stage}")
                )
        if item.get("hsn_sac"):
            expected_hsn = alnum(str(item["hsn_sac"]))
            got_hsn = (
                alnum(str(bench._plain(found.hsn_sac.value))) if found and found.hsn_sac else ""
            )
            if got_hsn == expected_hsn:
                ok["hsn"] += 1
            else:
                if expected_hsn in alnum(doc_text):
                    stage = "B"
                elif expected_hsn.translate(FOLD) in fold(doc_text):
                    stage = "A-glyph"
                else:
                    stage = "A-absent"
                if found is not None and got_hsn and got_hsn.startswith(expected_hsn[:4]):
                    stage = "E"
                hsn_kind = (
                    "row_missing" if found is None else ("missing" if not got_hsn else "wrong")
                )
                misses.append(CellMiss("hsn", stage, f"line.hsn:{hsn_kind}:{stage}"))
    return ok, misses


# --------------------------------------------------------------------------- scan types


def page_kinds(content: bytes) -> list[dict[str, Any]]:
    """Per-page pixel numbers of one PDF (coverage, text chars, DPI, rotation, skew)."""

    import numpy as np
    import pdfplumber
    import pypdfium2
    from PIL import Image
    from reconbench_app.extraction.local_hybrid import _DARK, _profile_score

    def wide_skew(gray: Image.Image) -> float:
        small = gray.reduce(2) if min(gray.size) > 1200 else gray
        ink = Image.fromarray(((np.asarray(small) < _DARK) * 255).astype(np.uint8))
        if not np.asarray(ink).any():
            return 0.0
        coarse = [step / 2 for step in range(-30, 31)]
        best = max(coarse, key=lambda angle: (_profile_score(ink, angle), -abs(angle)))
        fine = [round(best + step / 10, 1) for step in range(-5, 6)]
        return float(max(fine, key=lambda angle: (_profile_score(ink, angle), -abs(angle))))

    def osd(gray: Image.Image) -> tuple[int, float]:
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "page.png")
            gray.save(path)
            output = subprocess.run(
                ["tesseract", path, "-", "--psm", "0"],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            ).stdout
        rotate, confidence = 0, 0.0
        for row in output.splitlines():
            if row.startswith("Rotate:"):
                rotate = int(row.split(":")[1])
            elif row.startswith("Orientation confidence:"):
                confidence = float(row.split(":")[1])
        return rotate, confidence

    pages: list[dict[str, Any]] = []
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for page in pdf.pages:
            area = float(page.width * page.height) or 1.0
            coverage, dpi = 0.0, None
            for image in page.images:
                width = float(image["x1"]) - float(image["x0"])
                height = float(image["bottom"]) - float(image["top"])
                if width * height / area > coverage:
                    coverage = width * height / area
                    source = image.get("srcsize") or (0, 0)
                    dpi = round(source[0] / (width / 72)) if width else None
            text = page.extract_text() or ""
            pages.append(
                {
                    "coverage": round(coverage, 2),
                    "alnum": sum(char.isalnum() for char in text),
                    "dpi": dpi,
                }
            )
    document = pypdfium2.PdfDocument(content)
    try:
        for index, info in enumerate(pages):
            if info["coverage"] < 0.5 and info["alnum"] >= 20:
                info["kind"] = DIGITAL
                continue
            gray = document[index].render(scale=200 / 72).to_pil().convert("L")
            rotate, confidence = osd(gray)
            if rotate in (90, 180, 270) and confidence >= 2.0:
                gray = gray.rotate(-rotate, expand=True)
            info["rotate"] = rotate if confidence >= 2.0 else 0
            info["skew"] = wide_skew(gray)
            info["kind"] = "scan"
    finally:
        document.close()
    return pages


def scan_label(pages: Sequence[Mapping[str, Any]]) -> str:
    """One label for a run of pages: digital unless a page is a scan, worst problem first."""

    scans = [page for page in pages if page.get("kind") == "scan"]
    if not scans:
        return DIGITAL
    if any(page.get("rotate") for page in scans):
        return "scan-rotated"
    worst = max(abs(float(page.get("skew") or 0)) for page in scans)
    if worst > 5:
        return "scan-skew>5"
    if any((page.get("dpi") or 0) < 200 for page in scans):
        return "scan-lowdpi"
    return "scan-tilted" if worst >= 0.5 else "scan-clean"


def load_scan_types(
    files: Iterable[tuple[str, Path]], cache_path: Path
) -> dict[str, list[dict[str, Any]]]:
    """Per-file page numbers, from the cache when the file bytes have not changed."""

    try:
        stored = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        stored = {}
    result: dict[str, list[dict[str, Any]]] = {}
    changed = False
    for file_id, path in files:
        content = path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        entry = stored.get(file_id)
        if not isinstance(entry, dict) or entry.get("sha256") != digest:
            pages = page_kinds(content)
            entry = {"sha256": digest, "label": scan_label(pages), "pages": pages}
            stored[file_id] = entry
            changed = True
        result[file_id] = entry["pages"]
    if changed:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(stored, indent=1, sort_keys=True), encoding="utf-8")
    return result


# --------------------------------------------------------------------------- aggregation


def _reader(result: ParseResult) -> str:
    codes = {warning.code for warning in result.warnings}
    if "OCR_LAYOUT_EXTRACTION" in codes:
        return "layout-ocr"
    if any(page.is_ocr for page in result.page_texts):
        return "core+ocrmypdf"
    if any(page.scanned for page in result.page_texts):
        return "core-text(scanned pages unread)"
    return "text-layer"


def _stage_group(stage: str) -> str:
    return "A" if stage.startswith("A-") else stage


def _grouped(table: Mapping[str, int]) -> dict[str, int]:
    """Miss counts with A-absent and A-glyph folded into A; every stage listed."""

    grouped: Counter[str] = Counter({stage: 0 for stage in ("A", "B", "C", "D", "E")})
    for stage, count in table.items():
        if stage not in {"ok", "total"}:
            grouped[_stage_group(stage)] += count
    return dict(sorted(grouped.items()))


def _counts(table: Mapping[str, Counter[str]]) -> dict[str, dict[str, int]]:
    return {key: dict(sorted(table[key].items())) for key in sorted(table)}


@dataclass
class Attribution:
    readers: Counter[str] = field(default_factory=Counter)
    header: defaultdict[str, Counter[str]] = field(default_factory=lambda: defaultdict(Counter))
    lines: defaultdict[str, Counter[str]] = field(default_factory=lambda: defaultdict(Counter))
    by_scan_type: defaultdict[str, Counter[str]] = field(
        default_factory=lambda: defaultdict(Counter)
    )
    scan_documents: int = 0
    scan_header: Counter[str] = field(default_factory=Counter)
    scan_lines: defaultdict[str, Counter[str]] = field(default_factory=lambda: defaultdict(Counter))
    documents: list[dict[str, Any]] = field(default_factory=list)
    patterns: Counter[str] = field(default_factory=Counter)
    examples: defaultdict[str, list[str]] = field(default_factory=lambda: defaultdict(list))

    def as_json(self) -> dict[str, Any]:
        return {
            "documents_by_reader": dict(sorted(self.readers.items())),
            "header": _counts(self.header),
            "lines": _counts(self.lines),
            "by_scan_type": _counts(self.by_scan_type),
            "scan_pages": {
                "documents": self.scan_documents,
                "header": {
                    "ok": self.scan_header["ok"],
                    "total": self.scan_header["total"],
                    "misses": _grouped(self.scan_header),
                },
                "lines": {
                    cell: {"ok": table["ok"], "total": table["total"], "misses": _grouped(table)}
                    for cell, table in sorted(self.scan_lines.items())
                },
            },
            "documents": self.documents,
            "patterns": [
                {"pattern": key, "count": count, "examples": self.examples.get(key, [])[:3]}
                for key, count in self.patterns.most_common(25)
            ],
        }


def attribute(
    truth: bench.Truth,
    results: Mapping[str, ParseResult],
    scan_pages: Mapping[str, Sequence[Mapping[str, Any]]],
) -> Attribution:
    """Classify every header and line miss of the parsed PDF documents in ``results``.

    The party (stage C) always comes from the truth's buyer entities, whatever the
    bench run configured, so a role error is visible in every ``--buyer-entities`` mode.
    """

    party = Party(
        gstins=frozenset(str(item.get("gstin", "")).upper() for item in truth.buyer_entities),
        names=tuple(str(item.get("name") or "").upper() for item in truth.buyer_entities),
    )
    out = Attribution()
    by_id = truth.by_id()
    annotated = {item.doc_id for item in truth.documents if item.annotation_refs}
    for truth_file in truth.files:
        result = results.get(truth_file.file_id)
        if result is None:
            continue
        file_reader = _reader(result)
        page_text = {page.page: page.content for page in result.page_texts}
        file_text = "\n".join(page_text.values())
        file_pages = scan_pages.get(truth_file.file_id, ())
        assigned = bench.assign_truth(truth.for_file(truth_file.file_id), result.documents)
        for index, document in enumerate(result.documents):
            truth_id = assigned.get(index)
            if truth_id is None:
                continue
            truth_doc = by_id[truth_id]
            pages = bench.split_pages(document) or tuple(page_text)
            text = "\n".join(page_text.get(page, "") for page in pages)
            # Reader and scan type are set per document: no carry-over from the
            # previous document of the same file.
            own_pages = [file_pages[page - 1] for page in pages if 0 < page <= len(file_pages)]
            if own_pages:
                label = scan_label(own_pages)
            elif file_pages:
                label = scan_label(file_pages)
            else:
                label = DIGITAL if file_reader == "text-layer" else "scan-clean"
            reader = "layout-textlayer" if own_pages and label == DIGITAL else file_reader
            scan_type = label + ("+handwritten" if truth_id in annotated else "")
            is_scan = label != DIGITAL
            out.readers[reader] += 1
            out.scan_documents += int(is_scan)
            score = bench.score_document(truth_doc, truth_file.file_id, document)
            split = len(pages) < len(page_text)
            header_misses: Counter[str] = Counter()
            for name, outcome in score.fields.items():
                truth_value = truth_doc.header.get(name)
                extracted = plain(getattr(document.header, name, None))
                stage = (
                    "ok"
                    if outcome == "correct"
                    else header_stage(
                        name,
                        outcome,
                        truth_value,
                        extracted,
                        doc_text=text,
                        file_text=file_text,
                        split=split,
                        party=party,
                    )
                )
                out.header[GROUPS.get(name, name)][stage] += 1
                out.by_scan_type[f"{scan_type} header"][stage] += 1
                if is_scan:
                    out.scan_header["total"] += 1
                    out.scan_header[stage] += 1
                if stage == "ok":
                    continue
                header_misses[stage] += 1
                key = f"{name}:{outcome}:{stage}"
                out.patterns[key] += 1
                if name != "vendor_name" and len(out.examples[key]) < 3:
                    got = mask(extracted) if extracted is not None else "-"
                    out.examples[key].append(f"truth={mask(truth_value)} got={got}")
            line_misses: Counter[str] = Counter()
            if truth_doc.lines is not None:
                ok, misses = line_cells(truth_doc.lines, document, text)
                for cell, count in ok.items():
                    out.lines[cell]["ok"] += count
                    out.by_scan_type[f"{scan_type} lines"]["ok"] += count
                    if is_scan:
                        out.scan_lines[cell]["ok"] += count
                        out.scan_lines[cell]["total"] += count
                for miss in misses:
                    out.lines[miss.cell][miss.stage] += 1
                    out.by_scan_type[f"{scan_type} lines"][miss.stage] += 1
                    out.patterns[miss.pattern] += 1
                    line_misses[miss.stage] += 1
                    if is_scan:
                        out.scan_lines[miss.cell][miss.stage] += 1
                        out.scan_lines[miss.cell]["total"] += 1
            out.documents.append(
                {
                    "truth": truth_id,
                    "file": truth_file.file_id,
                    "scan_type": scan_type,
                    "reader": reader,
                    "header": dict(sorted(header_misses.items())),
                    "lines": dict(sorted(line_misses.items())),
                    "line_recall": f"{score.lines_found}/{score.lines_truth}",
                    "flagged": document.needs_verification,
                }
            )
    return out


def print_summary(data: Mapping[str, Any]) -> None:
    scan = data["scan_pages"]
    header = scan["header"]
    print(f"Documents by reader: {data['documents_by_reader']}")
    print(
        f"Scan-page documents: {scan['documents']}  header {header['ok']}/{header['total']}  "
        f"misses {header['misses']}"
    )
    for cell, table in scan["lines"].items():
        print(f"  line {cell}: {table['ok']}/{table['total']}  misses {table['misses']}")
    for title, key in (("Header (group x stage)", "header"), ("Lines (cell x stage)", "lines")):
        print(f"\n{title}")
        for row, counts in data[key].items():
            print(f"  {row:14} {counts}")
    print("\nBy scan type")
    for row, counts in data["by_scan_type"].items():
        print(f"  {row:34} {counts}")
    print("\nPer document (truth id, file id, scan type: miss stages)")
    for row in data["documents"]:
        print(
            f"  {row['truth']:16} {row['file']:10} {row['scan_type']:28} "
            f"header {row['header']} lines {row['lines']} ({row['line_recall']})"
        )
    print("\nTop patterns")
    for item in data["patterns"]:
        print(f"  {item['count']:3d} {item['pattern']}  {item['examples'][:2]}")


# --------------------------------------------------------------------------- CLI


def _forbidden(*_args: Any, **_kwargs: Any) -> ParseResult:
    raise StaleReport("fresh read forbidden")


def cached_results(
    truth: bench.Truth, meta: Mapping[str, Any], samples: Path, cache: Path
) -> dict[str, ParseResult]:
    """The report's PDF reads from the bench cache only; a cache miss raises StaleReport."""

    pinned = dict(meta["reader"])
    languages = tuple(str(pinned["languages"]).split("+"))
    current = bench.reader_fingerprint(languages, str(pinned["engine"]))
    if current["extraction_code"] != pinned["extraction_code"]:
        raise StaleReport("extraction code changed since the report")
    if truth.digest != meta.get("truth_sha256"):
        raise StaleReport("truth file changed since the report")
    body = bench.config_body(truth, str(meta.get("buyer_entities", "truth")))
    bench.parse_pdf_isolated = _forbidden
    results: dict[str, ParseResult] = {}
    for truth_file in truth.files:
        if bench._format_for(Path(truth_file.path)) is not bench.FileFormat.PDF:
            continue
        outcome = bench.read_file(
            truth_file,
            samples,
            cache,
            pinned,
            body,
            refresh=False,
            languages=languages,
            engine_name=str(pinned["engine"]),
        )
        results[truth_file.file_id] = outcome.result
    return results


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    work = ROOT / "work"
    parser.add_argument("--report", type=Path, required=True, help="bench report to explain")
    parser.add_argument("--truth", type=Path, default=work / "accuracy-truth" / "truth.json")
    parser.add_argument("--samples", type=Path, default=work / "accuracy-samples")
    parser.add_argument("--cache", type=Path, default=work / "accuracy-cache")
    parser.add_argument(
        "--scan-types", type=Path, default=work / "accuracy-cache" / "scan-types.json"
    )
    parser.add_argument("--json", type=Path, default=None, help="also write the result here")
    arguments = parser.parse_args(list(argv) if argv is not None else None)

    meta = json.loads(arguments.report.read_text(encoding="utf-8"))["meta"]
    truth = bench.load_truth(arguments.truth)
    try:
        results = cached_results(truth, meta, arguments.samples, arguments.cache)
    except StaleReport as error:
        print(f"{error}: re-run the bench for this working tree, then attribute its report.")
        return 2
    scan_pages = load_scan_types(
        (
            (item.file_id, arguments.samples / item.path)
            for item in truth.files
            if item.file_id in results
        ),
        arguments.scan_types,
    )
    data = attribute(truth, results, scan_pages).as_json()
    print_summary(data)
    if arguments.json is not None:
        arguments.json.parent.mkdir(parents=True, exist_ok=True)
        arguments.json.write_text(
            json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(f"\nWrote {arguments.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
