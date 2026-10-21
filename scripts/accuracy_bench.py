"""Accuracy benchmark (AC-0): extraction → vendor → links → duplicates → reconciliation.

Every file in a hand-labelled truth file (format v2, see ``docs/accuracy-plan.md``
§8) is read by the app's real parsers and then run through vendor resolution,
linking, duplicate detection and the reconciliation engine in memory, as one
fresh workspace. No database or provider call is needed. The output is summary
metrics and per-document outcome labels only; document text and values are never
printed or written to the report.

Run it inside the worker image so OCR matches the app (Tesseract, OCRmyPDF), with
the working tree mounted over the image code:

    make accuracy-bench                      # or, by hand:
    docker run --rm --network none \
      -v "$PWD/backend/reconbench_core:/app/backend/reconbench_core:ro" \
      -v "$PWD/backend/reconbench_app:/app/backend/reconbench_app:ro" \
      -v "$PWD/scripts:/app/scripts:ro" -v "$PWD/work:/app/work" \
      reconbench-worker /app/backend/.venv/bin/python /app/scripts/accuracy_bench.py

Reader outputs are cached in ``--cache`` by file bytes, parser hints, extraction
code and OCR version, so re-runs are deterministic and only re-read files after
an extraction change. ``--refresh`` ignores the cache.

Hard metrics (must stay 0, plan §1): false-safe documents, false auto-links,
false Matched cases and wrong auto-created vendors. The exit code is 1 when any
of them is non-zero.

Vision reader (AC-5, ``scripts/accuracy_vision.py``): ``--vision cached`` (the
default) scores recorded page-image readings; ``--vision provider
--allow-provider`` (owner only, network on, ``BENCH_GEMINI_KEY``) records missing
ones first. Vision values are suggestions, so ``report_hash`` covers only the
main pipeline and the vision block has its own ``vision_hash``.

Evaluation harness (LLM plan WI-EV1): each run reads with exactly one vision
provider, ``--vision-provider gemini`` (default) or ``vllm`` (``make
accuracy-bench-vllm``; ``--vision-base-url`` must be on ``RB_AI_VLLM_ENDPOINTS``
and no Gemini client is built), with ``--vision-model``, ``--vision-thinking``,
``--vision-sampling``, ``--vision-prompt-variant``, ``--vision-strategy full`` and
``--repeat N``. The
counts-only ``vision_eval`` block (own ``vision_eval_hash``) is written when one
of these flags, ``--agreement-policy llm_x_ocr`` or ``--vision-eval`` is given;
without them the report and every hash are as before. Arms are compared by
reading their reports side by side; their answers are never combined.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
from importlib import metadata
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT))

from reconbench_app.db.seed_data import DEFAULT_CONFIG  # noqa: E402
from reconbench_app.document_identity import (  # noqa: E402
    document_identity_collision_key,
    document_rescan_key,
    identity_payload,
    page_hashes_within_distance,
    rescan_identity_payload,
    verified_field_confidence,
)
from reconbench_app.extraction.einvoice_keys import einvoice_keys  # noqa: E402
from reconbench_app.ingestion import (  # noqa: E402
    IngestionLimits,
    document_natural_key,
    parser_hints_from_config,
)
from reconbench_app.mapping_service import mapping_config_from_body  # noqa: E402
from reconbench_app.pdf_sandbox import _result_payload, parse_pdf_isolated  # noqa: E402
from reconbench_app.reconciliation_service import reconciliation_config  # noqa: E402
from reconbench_app.vendor_resolution import (  # noqa: E402
    anchor_document,
    field_verified,
    vendor_config_from_body,
)
from reconbench_core.align import align_lines  # noqa: E402
from reconbench_core.extraction.extract import VERIFIED_CONFIDENCE  # noqa: E402
from reconbench_core.extraction.pages import split_pages  # noqa: E402
from reconbench_core.mapping import (  # noqa: E402
    MappingDocument,
    MappingPO,
    VendorAliasRecord,
    VendorDocument,
    VendorRecord,
    plan_vendor_anchors,
    resolve_links,
    resolve_vendors,
)
from reconbench_core.normalize import (  # noqa: E402
    canonicalize_document_number,
    normalize_vendor_name,
)
from reconbench_core.parsers import ParserHints, parse  # noqa: E402
from reconbench_core.parsers.parties import (  # noqa: E402
    buyer_gstins,
    party_hints_from_config,
)
from reconbench_core.parsers.types import BuyerEntityHint, PageImageHash, ParseResult  # noqa: E402
from reconbench_core.recon import (  # noqa: E402
    ReconciliationDocument,
    ReconciliationLink,
    ReconciliationSnapshot,
    evaluate_components,
    rules_hash,
)
from reconbench_core.schema import (  # noqa: E402
    AlignmentState,
    CaseResult,
    CaseStatus,
    DocLine,
    DocType,
    Document,
    FileFormat,
    LinkLevel,
    LinkState,
    ReasonCode,
)
from reconbench_core.version import ENGINE_VERSION  # noqa: E402

from scripts import accuracy_consensus, accuracy_vision, accuracy_vision_eval  # noqa: E402

TRUTH_FORMAT = "reconbench-accuracy-truth/v2"
_NAMESPACE = UUID("6f1c2a52-6d0b-5a55-9a43-7a0d3c2f4b10")
_CENT = Decimal("0.01")
_MONEY = {"taxable_total", "cgst", "sgst", "igst", "round_off", "grand_total"}
_ZERO_IS_ABSENT = {"cgst", "sgst", "igst", "round_off"}
_HEADER_FIELDS = (
    "doc_no",
    "doc_date",
    "vendor_gstin",
    "vendor_name",
    "buyer_gstin",
    "taxable_total",
    "cgst",
    "sgst",
    "igst",
    "grand_total",
    "po_refs",
)
# A wrong (or unexpected) value in these fields on an unflagged document is a
# false-safe; a missing value counts only for the identity/amount fields.
_CRITICAL = {
    DocType.INVOICE: ("doc_no", "doc_date", "vendor_gstin", "grand_total", "po_refs"),
    DocType.PO: ("doc_no", "doc_date", "vendor_gstin", "grand_total"),
    DocType.GRN: ("doc_no", "doc_date", "vendor_gstin", "po_refs"),
}
_CRITICAL_MISSING = ("doc_no", "doc_date", "grand_total")
_MATCHED = {CaseStatus.MATCHED, CaseStatus.MATCHED_FLAGS}
_LABEL_NAMES = {
    "bill from",
    "bill to",
    "buyer",
    "consignee",
    "m s",
    "ms",
    "seller",
    "ship to",
    "sold by",
    "supplier",
    "vendor",
}
_GSTIN_SHAPE = re.compile(r"\d{2}[A-Z]{5}\d{4}[A-Z][0-9A-Z]Z[0-9A-Z]")
_CODE_PATHS = (
    "backend/reconbench_core/extraction",
    "backend/reconbench_core/parsers",
    "backend/reconbench_core/normalize",
    "backend/reconbench_core/schema",
    "backend/reconbench_app/extraction",
    "backend/reconbench_app/pdf_sandbox.py",
    "backend/reconbench_app/document_identity.py",
)


def _stable_id(kind: str, value: str) -> UUID:
    return uuid5(_NAMESPACE, f"{kind}:{value}")


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _sha256(data: bytes | str) -> str:
    return hashlib.sha256(data.encode("utf-8") if isinstance(data, str) else data).hexdigest()


# --------------------------------------------------------------------------- truth


@dataclass(frozen=True)
class TruthFile:
    file_id: str
    path: str
    zone: DocType


@dataclass(frozen=True)
class TruthDocument:
    doc_id: str
    set_id: str
    doc_type: DocType
    files: tuple[str, ...]
    header: Mapping[str, Any]
    lines: tuple[Mapping[str, Any], ...] | None
    links: tuple[str, ...]
    expected_status: tuple[str, ...]
    # Kept beside the header (not scored as header fields): handwritten/stamped refs
    # and marketplace order numbers; the vision block scores their recall (AC-5b).
    annotation_refs: tuple[str, ...] = ()
    marketplace_order_no: tuple[str, ...] = ()


def _strings(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    items = value if isinstance(value, (list, tuple)) else [value]
    return tuple(str(item) for item in items if str(item).strip())


@dataclass(frozen=True)
class Truth:
    run_date: date
    buyer_entities: tuple[Mapping[str, str], ...]
    files: tuple[TruthFile, ...]
    documents: tuple[TruthDocument, ...]
    digest: str

    def by_id(self) -> dict[str, TruthDocument]:
        return {item.doc_id: item for item in self.documents}

    def for_file(self, file_id: str) -> list[TruthDocument]:
        return [item for item in self.documents if file_id in item.files]

    def buyer_entities_hints(self) -> tuple[BuyerEntityHint, ...]:
        return tuple(
            BuyerEntityHint(gstin=str(item["gstin"]), name=str(item.get("name") or ""))
            for item in self.buyer_entities
            if item.get("gstin")
        )


def load_truth(path: Path) -> Truth:
    raw_bytes = path.read_bytes()
    raw = json.loads(raw_bytes)
    if raw.get("format") != TRUTH_FORMAT:
        raise ValueError(f"truth file must declare format {TRUTH_FORMAT!r}")
    config = raw.get("config", {})
    files = tuple(
        TruthFile(file_id=str(item["id"]), path=str(item["path"]), zone=DocType(item["zone"]))
        for item in raw["files"]
    )
    file_ids = {item.file_id for item in files}
    if len(file_ids) != len(files):
        raise ValueError("truth file ids must be unique")
    documents: list[TruthDocument] = []
    for truth_set in raw["sets"]:
        for item in truth_set["documents"]:
            sources = tuple(str(source["file"]) for source in item["sources"])
            unknown = set(sources) - file_ids
            if unknown:
                raise ValueError(f"{item['id']}: unknown source files {sorted(unknown)}")
            lines = item.get("lines")
            documents.append(
                TruthDocument(
                    doc_id=str(item["id"]),
                    set_id=str(truth_set["id"]),
                    doc_type=DocType(item["doc_type"]),
                    files=sources,
                    header=dict(item.get("header", {})),
                    lines=tuple(lines) if lines is not None else None,
                    links=tuple(str(target) for target in item.get("links", [])),
                    expected_status=tuple(
                        str(status) for status in item.get("expected_status", [])
                    ),
                    annotation_refs=_strings(item.get("annotation_refs")),
                    marketplace_order_no=_strings(item.get("marketplace_order_no")),
                )
            )
    ids = [item.doc_id for item in documents]
    if len(ids) != len(set(ids)):
        raise ValueError("truth document ids must be unique")
    known = set(ids)
    for item in documents:
        if set(item.links) - known:
            raise ValueError(f"{item.doc_id}: links point to unknown documents")
    return Truth(
        run_date=date.fromisoformat(str(config["run_date"])),
        buyer_entities=tuple(config.get("buyer_entities", [])),
        files=files,
        documents=tuple(documents),
        digest=_sha256(raw_bytes),
    )


# --------------------------------------------------------------------------- readers


def _format_for(path: Path) -> FileFormat:
    suffix = path.suffix.casefold()
    if suffix == ".pdf":
        return FileFormat.PDF
    if suffix == ".xml":
        return FileFormat.XML
    if suffix == ".csv":
        return FileFormat.CSV
    raise ValueError(f"unsupported sample format: {path.name}")


def parser_hints(zone: DocType, file_format: FileFormat, body: Mapping[str, Any]) -> ParserHints:
    """Hints exactly as upload builds them from the active config (WI-1 parity)."""

    return parser_hints_from_config(
        body,
        zone_type=zone,
        file_format=file_format,
        limits=IngestionLimits.model_validate(body["app"]["limits"]),
    )


def _tool_version(command: list[str]) -> str:
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return "none"
    output = (completed.stdout or completed.stderr).strip().splitlines()
    return output[0] if output else "none"


def _package_version(name: str) -> str:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return "none"


def reader_fingerprint(
    languages: tuple[str, ...], engine_name: str, einvoice_key_bytes: bytes | None = None
) -> dict[str, str]:
    """Everything that can change a reader's output for the same file bytes and hints."""

    digest = hashlib.sha256()
    for relative in _CODE_PATHS:
        target = ROOT / relative
        paths = sorted(target.rglob("*.py")) if target.is_dir() else [target]
        for path in paths:
            if path.name.startswith(("vision", "consensus")):
                # Vision (AC-5) and consensus (AC-6) never change a rules reading.
                continue
            digest.update(path.relative_to(ROOT).as_posix().encode("utf-8"))
            digest.update(path.read_bytes())
    return {
        "engine_version": ENGINE_VERSION,
        "extraction_code": digest.hexdigest(),
        "tesseract": _tool_version(["tesseract", "--version"]),
        "ocrmypdf": _tool_version(["ocrmypdf", "--version"]),
        "qr_decoder": _package_version("zxing-cpp"),
        "languages": "+".join(languages),
        "engine": engine_name,
        "einvoice_keys": _sha256(einvoice_key_bytes) if einvoice_key_bytes else "none",
    }


_TIMEOUT = "PDF_PROCESS_TIMEOUT"


@dataclass
class ReadOutcome:
    file_id: str
    sha256: str
    result: ParseResult
    cached: bool
    seconds: float


def read_file(
    truth_file: TruthFile,
    samples: Path,
    cache: Path,
    fingerprint: Mapping[str, str],
    body: Mapping[str, Any],
    *,
    refresh: bool,
    languages: tuple[str, ...],
    engine_name: str,
    einvoice_public_keys: tuple[Any, ...] = (),
) -> ReadOutcome:
    path = samples / truth_file.path
    content = path.read_bytes()
    file_sha = _sha256(content)
    file_format = _format_for(path)
    hints = parser_hints(truth_file.zone, file_format, body)
    key_material = {
        "reader": "local",
        "file_sha256": file_sha,
        "hints": hints.model_dump(mode="json"),
        **fingerprint,
    }
    cache_path = cache / f"{_sha256(_canonical_json(key_material))}.json"
    if not refresh and cache_path.is_file():
        try:
            stored = json.loads(cache_path.read_text(encoding="utf-8"))
            result = ParseResult.model_validate(stored["result"])
        except (ValueError, KeyError):
            pass  # unreadable or old-format entry: read the file again
        else:
            return ReadOutcome(truth_file.file_id, file_sha, result, True, 0.0)
    started = time.monotonic()
    if file_format is FileFormat.PDF:
        result = parse_pdf_isolated(
            content,
            hints,
            languages=languages,
            engine_name=engine_name,
            einvoice_keys=einvoice_public_keys,
        )
    else:
        result = parse(content, hints)
    seconds = time.monotonic() - started
    if result.reason_code == _TIMEOUT:
        # Never cache a timeout: the next run must read the file again.
        return ReadOutcome(truth_file.file_id, file_sha, result, False, seconds)
    cache.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps(
            {"key": key_material, "result": _result_payload(result)},
            sort_keys=True,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return ReadOutcome(truth_file.file_id, file_sha, result, False, seconds)


# --------------------------------------------------------------------------- normalisation


def _plain(value: Any) -> Any:
    return getattr(value, "value", value)


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value)).quantize(_CENT)
    except InvalidOperation:
        return None


def _fold_lookalikes(value: str) -> str:
    return re.sub(r"[^0-9A-Z]", "", value.upper()).replace("O", "0").replace("I", "1")


def _name_ratio(left: str, right: str) -> float:
    return SequenceMatcher(None, normalize_vendor_name(left), normalize_vendor_name(right)).ratio()


def _refs(value: Any) -> frozenset[str]:
    if value is None:
        return frozenset()
    items = value if isinstance(value, (list, tuple)) else [value]
    return frozenset(
        canonical for item in items if (canonical := canonicalize_document_number(str(item)))
    )


def _normal(name: str, value: Any) -> Any:
    value = _plain(value)
    if value is None:
        return None
    if name in _MONEY:
        amount = _decimal(value)
        if amount is not None and amount == 0 and name in _ZERO_IS_ABSENT:
            return None
        return amount
    if name == "po_refs":
        return _refs(value) or None
    if isinstance(value, date):
        return value.isoformat()
    if name == "vendor_name":
        # Keep the spaces: legal suffixes ("PVT LTD") are dropped word by word.
        return " ".join(str(value).split()) or None
    text = "".join(str(value).split()).upper()
    return text or None


def _same(name: str, expected: Any, actual: Any) -> bool:
    if name == "vendor_name":
        return _name_ratio(str(expected), str(actual)) >= 0.90
    return bool(expected == actual)


def _header_field(document: Document, name: str) -> Any:
    return getattr(document.header, name, None)


def _line_qty(line: DocLine) -> Decimal | None:
    for name in ("quantity", "received_qty", "accepted_qty"):
        value = getattr(line, name)
        if value is not None:
            return _decimal(value.value)
    return None


# --------------------------------------------------------------------------- extraction


@dataclass
class DocScore:
    truth_id: str
    file_id: str
    doc_type: DocType
    fields: dict[str, str] = field(default_factory=dict)
    trusted_wrong: list[str] = field(default_factory=list)
    lines_truth: int = 0
    lines_found: int = 0
    lines_extracted: int = 0
    lines_spurious: int = 0
    desc_scored: int = 0
    desc_correct: int = 0
    touch: int = 0
    confirm: int = 0
    flagged: bool = False
    false_safe: bool = False
    critical_wrong: bool = False


def score_document(truth: TruthDocument, file_id: str, document: Document) -> DocScore:
    score = DocScore(truth.doc_id, file_id, truth.doc_type, flagged=document.needs_verification)
    critical = _CRITICAL[truth.doc_type]
    unsafe = False
    for name in _HEADER_FIELDS:
        if name not in truth.header:
            continue
        extracted = _header_field(document, name)
        if name == "po_refs" and not hasattr(document.header, "po_refs"):
            continue
        expected = _normal(name, truth.header[name])
        actual = _normal(name, extracted)
        if expected is None and actual is None:
            continue
        if expected is None:
            outcome = "unexpected"
        elif actual is None:
            outcome = "missing"
        elif _same(name, expected, actual):
            outcome = "correct"
        else:
            outcome = "wrong"
        score.fields[name] = outcome
        trusted = extracted is not None and extracted.confidence >= VERIFIED_CONFIDENCE
        if outcome in {"wrong", "unexpected"}:
            score.touch += 1
            if trusted:
                score.trusted_wrong.append(name)
            if name in critical:
                unsafe = True
                score.critical_wrong = True
        elif outcome == "missing":
            score.touch += 1
            if name in _CRITICAL_MISSING and name in critical:
                unsafe = True
        elif not trusted:
            score.confirm += 1

    if truth.lines is not None:
        extracted_lines = [
            (
                _decimal(_plain(line.amount)) if line.amount else None,
                _line_qty(line),
                _decimal(_plain(line.rate)) if line.rate else None,
                str(_plain(line.description) or "") if line.description else "",
            )
            for line in document.lines
        ]
        used: set[int] = set()
        for item in truth.lines:
            amount = _decimal(item.get("amount"))
            quantity = _decimal(item.get("quantity"))
            rate = _decimal(item.get("rate"))
            cells = sum(value is not None for value in (amount, quantity, rate))
            match = None
            for index, (got_amount, got_qty, got_rate, _) in enumerate(extracted_lines):
                if index in used:
                    continue
                if amount is not None and got_amount != amount:
                    continue
                if amount is None and (quantity is None or got_qty != quantity):
                    continue
                if quantity is not None and got_qty is not None and got_qty != quantity:
                    continue
                if rate is not None and got_rate is not None and got_rate != rate:
                    continue
                match = index
                break
            if match is None:
                score.touch += cells
                continue
            used.add(match)
            score.lines_found += 1
            got_amount, got_qty, got_rate, got_desc = extracted_lines[match]
            score.touch += sum(
                expected is not None and got is None
                for expected, got in (
                    (quantity, got_qty),
                    (rate, got_rate),
                    (amount, got_amount),
                )
            )
            if item.get("description"):
                score.desc_scored += 1
                ratio = SequenceMatcher(
                    None,
                    " ".join(str(item["description"]).upper().split()),
                    " ".join(got_desc.upper().split()),
                ).ratio()
                score.desc_correct += int(ratio >= 0.85)
        score.lines_truth = len(truth.lines)
        score.lines_extracted = len(extracted_lines)
        score.lines_spurious = len(extracted_lines) - len(used)
        score.touch += score.lines_spurious
        if score.lines_found != score.lines_truth or score.lines_spurious:
            unsafe = True
    score.false_safe = unsafe and not document.needs_verification
    return score


def _match_score(truth: TruthDocument, document: Document) -> int:
    header = document.header
    points = 4 if header.doc_type is truth.doc_type else 0
    doc_no = _plain(header.doc_no)
    if doc_no and truth.header.get("doc_no"):
        points += 3 * (
            _fold_lookalikes(str(doc_no)) == _fold_lookalikes(str(truth.header["doc_no"]))
        )
    expected_total = _decimal(truth.header.get("grand_total"))
    if expected_total is not None:
        points += 2 * (_decimal(_plain(header.grand_total)) == expected_total)
    expected_date = truth.header.get("doc_date")
    if expected_date and header.doc_date is not None:
        points += int(header.doc_date.value.isoformat() == expected_date)
    expected_gstin = truth.header.get("vendor_gstin")
    if expected_gstin and header.vendor_gstin is not None:
        points += int(_normal("vendor_gstin", header.vendor_gstin) == expected_gstin.upper())
    return points


def assign_truth(
    truth_docs: list[TruthDocument], documents: tuple[Document, ...]
) -> dict[int, str]:
    """Greedy one-to-one assignment of a file's parsed documents to its truth documents."""

    if len(truth_docs) == 1 and len(documents) == 1:
        return {0: truth_docs[0].doc_id}
    pairs = sorted(
        (
            (-_match_score(truth_doc, document), truth_index, doc_index)
            for truth_index, truth_doc in enumerate(truth_docs)
            for doc_index, document in enumerate(documents)
        )
    )
    assigned: dict[int, str] = {}
    used_truth: set[int] = set()
    for negative, truth_index, doc_index in pairs:
        if negative >= 0 or truth_index in used_truth or doc_index in assigned:
            continue
        assigned[doc_index] = truth_docs[truth_index].doc_id
        used_truth.add(truth_index)
    return assigned


# --------------------------------------------------------------------------- workspace


@dataclass
class Parsed:
    file_id: str
    index: int
    file_sha: str
    document: Document
    truth_id: str | None
    # Read from XML/CSV (a structured record), not from a PDF.
    structured: bool = False
    page_hashes: tuple[PageImageHash, ...] = ()


@dataclass
class SystemDocument:
    document_id: UUID
    natural_key: str
    versions: list[Parsed]
    current_version_index: int = 0

    @property
    def current(self) -> Parsed:
        return self.versions[self.current_version_index]


@dataclass
class SimVendor:
    vendor_id: UUID
    name: str
    pan: str
    gstins: list[str]
    aliases: list[str]
    created_from_gstin: str
    name_status: str = "provisional"


def build_workspace(parsed: list[Parsed]) -> list[SystemDocument]:
    """Group evidence-matched re-scans in upload order, matching the app policy."""

    documents: list[SystemDocument] = []
    for item in parsed:
        incoming_identity = rescan_identity_payload(
            item.document,
            item.file_sha,
            page_hashes=item.page_hashes,
            structured=item.structured,
        )
        rescan_key = document_rescan_key(
            item.document,
            item.file_sha,
            page_hashes=item.page_hashes,
            structured=item.structured,
        )
        natural_key = document_natural_key(
            item.document,
            item.file_sha,
            page_hashes=item.page_hashes,
            structured=item.structured,
        )
        incoming_natural_identity = identity_payload(
            item.document,
            item.file_sha,
            page_hashes=item.page_hashes,
            structured=item.structured,
        )
        matching_documents: set[UUID] = set()
        for candidate in documents:
            for version in candidate.versions:
                if (
                    document_rescan_key(
                        version.document,
                        version.file_sha,
                        page_hashes=version.page_hashes,
                        structured=version.structured,
                    )
                    == rescan_key
                ):
                    matching_documents.add(candidate.document_id)
                    break

        if not matching_documents and incoming_identity["kind"] == "fingerprint":
            same_source_documents = {
                candidate.document_id
                for candidate in documents
                for version in candidate.versions
                if version.file_sha == item.file_sha
                and version.document.source_locator == item.document.source_locator
                and version.document.header.doc_type is item.document.header.doc_type
                and rescan_identity_payload(
                    version.document,
                    version.file_sha,
                    page_hashes=version.page_hashes,
                    structured=version.structured,
                )["kind"]
                == "source"
            }
            if len(same_source_documents) == 1:
                matching_documents = same_source_documents

        if not matching_documents and incoming_identity["kind"] == "fingerprint":
            for candidate in documents:
                for version in candidate.versions:
                    candidate_identity = rescan_identity_payload(
                        version.document,
                        version.file_sha,
                        page_hashes=version.page_hashes,
                        structured=version.structured,
                    )
                    if (
                        candidate_identity["kind"] == "fingerprint"
                        and incoming_identity["content_fingerprint"]
                        == candidate_identity["content_fingerprint"]
                        and page_hashes_within_distance(
                            incoming_identity["page_hashes"],
                            candidate_identity["page_hashes"],
                        )
                    ):
                        matching_documents.add(candidate.document_id)
                        break

        if not matching_documents and incoming_natural_identity["kind"] == "verified":
            matching_documents = {
                candidate.document_id
                for candidate in documents
                if candidate.natural_key == natural_key
            }
        existing = None
        if len(matching_documents) == 1:
            existing = next(
                candidate for candidate in documents if candidate.document_id in matching_documents
            )
        if existing is None:
            if any(candidate.natural_key == natural_key for candidate in documents):
                natural_key = document_identity_collision_key(
                    item.document,
                    item.file_sha,
                    page_hashes=item.page_hashes,
                    structured=item.structured,
                )
            documents.append(
                SystemDocument(_stable_id("document", rescan_key), natural_key, [item])
            )
        elif split_pages(item.document) and existing.current.structured:
            # As ``_upsert_document``: a bundle page never replaces a structured version.
            continue
        else:
            existing.versions.append(item)
            current_confidence = verified_field_confidence(
                existing.current.document, structured=existing.current.structured
            )
            incoming_confidence = verified_field_confidence(
                item.document, structured=item.structured
            )
            if current_confidence is None or (
                incoming_confidence is not None and incoming_confidence >= current_confidence
            ):
                existing.current_version_index = len(existing.versions) - 1
                existing.natural_key = natural_key
    return documents


def simulate_vendors(
    documents: list[SystemDocument], body: Mapping[str, Any]
) -> tuple[dict[UUID, UUID | None], dict[UUID, SimVendor]]:
    """Vendor creation and resolution as one fresh workspace would run them.

    Uses the same pure ``plan_vendor_anchors`` as
    ``VendorResolutionService._ensure_gstin_anchors`` (AC-4: only a verified,
    non-buyer GSTIN creates a vendor; a missing name stays pending; aliases only
    from verified names), with the app's ``anchor_document``/``field_verified``
    trust rule, followed by the pure ``resolve_vendors``.
    """

    buyers = party_hints_from_config(body.get("engine", {}))["buyer_entities"]
    anchors = []
    for item in documents:
        header = item.current.document.header
        gstin_field = header.vendor_gstin
        name_field = header.vendor_name
        anchors.append(
            anchor_document(
                document_id=item.document_id,
                natural_key=item.natural_key,
                vendor_gstin=_plain(gstin_field),
                vendor_name=_plain(name_field),
                gstin_verified=gstin_field is not None
                and field_verified(
                    gstin_field.confidence,
                    gstin_field.source.value,
                    structured=item.current.structured,
                ),
                name_verified=name_field is not None
                and field_verified(
                    name_field.confidence,
                    name_field.source.value,
                    structured=item.current.structured,
                ),
                buyers=buyers,
            )
        )
    plan = plan_vendor_anchors(anchors, [], buyer_gstins(buyers))
    vendors: dict[UUID, SimVendor] = {}
    by_key: dict[str, SimVendor] = {}
    for planned in plan.new_vendors:
        vendor_id = _stable_id("vendor", planned.key)
        vendor = SimVendor(
            vendor_id, planned.name, planned.pan, [], [], "", planned.name_status.value
        )
        vendors[vendor_id] = vendor
        by_key[planned.key] = vendor
    for registration in plan.registrations:
        vendor = by_key[str(registration.new_vendor_key)]
        vendor.gstins.append(registration.gstin)
        vendor.created_from_gstin = vendor.created_from_gstin or registration.gstin
    for planned_alias in plan.aliases:
        by_key[str(planned_alias.new_vendor_key)].aliases.append(planned_alias.alias)

    records = [
        VendorRecord(
            vendor_id=value.vendor_id,
            display_name=value.name,
            gstins=tuple(value.gstins),
            pan=value.pan,
            aliases=tuple(value.aliases),
        )
        for value in vendors.values()
    ]
    alias_records = [
        VendorAliasRecord(vendor_id=value.vendor_id, alias=alias)
        for value in vendors.values()
        for alias in value.aliases
    ]
    batch = resolve_vendors(
        [
            VendorDocument(
                document_id=item.document_id,
                natural_key=item.natural_key,
                vendor_name=_plain(item.current.document.header.vendor_name),
                vendor_gstin=_plain(item.current.document.header.vendor_gstin),
            )
            for item in documents
        ],
        records,
        alias_records,
        vendor_config_from_body(body),
    )
    return {item.document_id: item.vendor_id for item in batch.resolutions}, vendors


def build_snapshot(
    documents: list[SystemDocument],
    vendor_of: Mapping[UUID, UUID | None],
    vendors: Mapping[UUID, SimVendor],
    body: Mapping[str, Any],
    run_date: date,
) -> tuple[ReconciliationSnapshot, list[Any]]:
    """Fresh-period mapping snapshot: GRNs first (PO support), then all sources.

    Follows ``reconbench_core.cli._mapping_snapshot`` with the app's vendor
    assignment and PO open balance (grand total, else open qty × rate).
    """

    config = reconciliation_config(body, effective_date=run_date)
    mapping_config = mapping_config_from_body(body)
    records = [
        ReconciliationDocument(
            document_id=item.document_id,
            version_id=_stable_id("version", f"{item.natural_key}:{len(item.versions)}"),
            vendor_id=vendor_of.get(item.document_id),
            natural_key=item.natural_key,
            document=item.current.document,
        )
        for item in sorted(documents, key=lambda value: str(value.document_id))
    ]
    mapping_pos: list[MappingPO] = []
    for record in records:
        if record.document.header.doc_type is not DocType.PO:
            continue
        open_qty: dict[int, Decimal] = {}
        derived = Decimal("0")
        for line in record.document.lines:
            quantity = _plain(line.quantity) or Decimal("0")
            rate = _plain(line.rate) or Decimal("0")
            open_qty[line.line_no] = quantity
            derived += quantity * rate
        grand_total = _plain(record.document.header.grand_total)
        mapping_pos.append(
            MappingPO(
                document_id=record.document_id,
                vendor_id=record.vendor_id,
                document=record.document,
                open_balance=grand_total if grand_total is not None else derived,
                open_qty_by_line=open_qty,
            )
        )
    sources = [
        MappingDocument(
            document_id=record.document_id,
            natural_key=record.natural_key,
            vendor_id=record.vendor_id,
            document=record.document,
            source_text=record.document.raw_text,
        )
        for record in records
        if record.document.header.doc_type in {DocType.GRN, DocType.INVOICE}
    ]
    grns = [item for item in sources if item.document.header.doc_type is DocType.GRN]
    support: dict[UUID, dict[int, Decimal]] = {item.document_id: {} for item in mapping_pos}
    po_by_id = {item.document_id: item for item in mapping_pos}
    source_by_id = {item.document_id: item for item in sources}
    for resolution in resolve_links(grns, mapping_pos, mapping_config):
        grn = source_by_id[resolution.document_id]
        for link in resolution.links:
            if link.state not in {LinkState.AUTO, LinkState.CONFIRMED}:
                continue
            po = po_by_id[link.to_document_id]
            for aligned in align_lines(
                grn.document.lines,
                po.document.lines,
                config.alignment,
                explicit_link=link.level is LinkLevel.LINE,
            ):
                if aligned.po_line_no is None or aligned.state is not AlignmentState.ALIGNED:
                    continue
                grn_line = next(
                    (
                        line
                        for line in grn.document.lines
                        if line.line_no == aligned.invoice_line_no
                    ),
                    None,
                )
                if grn_line is None:
                    continue
                accepted = grn_line.accepted_qty
                received = grn_line.received_qty or grn_line.quantity
                rejected = grn_line.rejected_qty.value if grn_line.rejected_qty else Decimal("0")
                quantity = (
                    accepted.value
                    if accepted is not None
                    else max(Decimal("0"), received.value - rejected)
                    if received is not None
                    else Decimal("0")
                )
                current = support[po.document_id].get(aligned.po_line_no, Decimal("0"))
                support[po.document_id][aligned.po_line_no] = current + quantity
    mapping_pos = [
        item.model_copy(update={"grn_support_qty_by_line": support[item.document_id]})
        for item in mapping_pos
    ]
    resolutions = list(resolve_links(sources, mapping_pos, mapping_config))
    links: dict[UUID, ReconciliationLink] = {}
    for resolution in resolutions:
        for proposed in resolution.links:
            key = ":".join(
                (
                    str(proposed.from_document_id),
                    str(proposed.to_document_id),
                    proposed.level.value,
                    str(proposed.line_no or ""),
                    proposed.method.value,
                )
            )
            link_id = _stable_id("link", key)
            links[link_id] = ReconciliationLink(
                link_id=link_id,
                from_document_id=proposed.from_document_id,
                to_document_id=proposed.to_document_id,
                method=proposed.method,
                state=proposed.state,
                confidence=proposed.confidence,
                level=proposed.level,
                line_no=proposed.line_no,
            )
    snapshot = ReconciliationSnapshot(
        documents=tuple(records),
        links=tuple(sorted(links.values(), key=lambda item: str(item.link_id))),
        vendor_pan_by_id={
            vendor_id: vendors[vendor_id].pan
            for vendor_id in {record.vendor_id for record in records}
            if vendor_id is not None and vendor_id in vendors
        },
    )
    return snapshot, resolutions


# --------------------------------------------------------------------------- downstream scoring


def score_vendors(
    documents: list[SystemDocument],
    vendor_of: Mapping[UUID, UUID | None],
    vendors: Mapping[UUID, SimVendor],
    truth: Truth,
) -> dict[str, Any]:
    truth_by_id = truth.by_id()
    buyer_gstins = {str(item.get("gstin", "")).upper() for item in truth.buyer_entities}
    supplier_gstins: set[str] = set()
    supplier_names: dict[str, str] = {}
    for item in truth.documents:
        gstin = item.header.get("vendor_gstin")
        name = item.header.get("vendor_name")
        if isinstance(gstin, str):
            supplier_gstins.add(gstin.upper())
            if isinstance(name, str):
                supplier_names.setdefault(gstin.upper(), name)

    junk: Counter[str] = Counter()
    wrong_vendors = 0
    for vendor in sorted(vendors.values(), key=lambda value: str(value.vendor_id)):
        reasons: list[str] = []
        if buyer_gstins & set(vendor.gstins):
            reasons.append("buyer_gstin")
        if vendor.created_from_gstin not in supplier_gstins | buyer_gstins:
            reasons.append("unknown_gstin")
        normal = normalize_vendor_name(vendor.name).replace("(", "").replace(")", "").strip()
        if not vendor.name.strip():
            pass  # Name pending (AC-4): honest, shown as "(name pending)", not junk.
        elif normal in _LABEL_NAMES or vendor.name.strip().startswith("("):
            reasons.append("label_name")
        elif _GSTIN_SHAPE.search(vendor.name.upper().replace(" ", "")):
            reasons.append("gstin_name")
        else:
            expected_name = supplier_names.get(vendor.created_from_gstin)
            if expected_name and _name_ratio(vendor.name, expected_name) < 0.80:
                reasons.append("name_mismatch")
        junk.update(reasons)
        wrong_vendors += bool(reasons)

    outcomes: Counter[str] = Counter()
    for system in documents:
        truth_id = system.current.truth_id
        if truth_id is None or "vendor_gstin" not in truth_by_id[truth_id].header:
            outcomes["not_scored"] += 1
            continue
        expected = truth_by_id[truth_id].header["vendor_gstin"]
        assigned = vendor_of.get(system.document_id)
        chosen = vendors.get(assigned) if assigned is not None else None
        if expected is None:
            outcomes["correct" if chosen is None else "wrong"] += 1
        elif chosen is None:
            outcomes["pending"] += 1
        else:
            outcomes["correct" if str(expected).upper() in chosen.gstins else "wrong"] += 1
    return {
        "vendors_created": len(vendors),
        "WRONG_VENDORS_CREATED": wrong_vendors,
        "junk_reasons": dict(sorted(junk.items())),
        "name_status": dict(
            sorted(Counter(value.name_status for value in vendors.values()).items())
        ),
        "aliases_learned": sum(len(value.aliases) for value in vendors.values()),
        "document_vendor": dict(sorted(outcomes.items())),
    }


def score_links(
    documents: list[SystemDocument], resolutions: list[Any], truth: Truth
) -> dict[str, Any]:
    truth_of = {item.document_id: item.current.truth_id for item in documents}
    best: dict[tuple[str | None, str | None], str] = {}
    for resolution in resolutions:
        for link in resolution.links:
            pair = (
                truth_of.get(link.from_document_id),
                truth_of.get(link.to_document_id),
            )
            state = "auto" if link.state in {LinkState.AUTO, LinkState.CONFIRMED} else "suggested"
            if best.get(pair) != "auto":
                best[pair] = state
    expected = {(item.doc_id, target) for item in truth.documents for target in item.links}
    outcomes: Counter[str] = Counter()
    for pair in sorted(expected):
        outcomes[best.get(pair, "missed")] += 1
    false_auto = sum(1 for pair, state in best.items() if state == "auto" and pair not in expected)
    false_suggested = sum(
        1 for pair, state in best.items() if state == "suggested" and pair not in expected
    )
    found = outcomes["auto"] + outcomes["suggested"]

    def label(pair: tuple[str | None, str | None]) -> str:
        return f"{pair[0] or '?'}->{pair[1] or '?'}"

    return {
        "expected_links": len(expected),
        "found_auto": outcomes["auto"],
        "found_suggested": outcomes["suggested"],
        "missed": outcomes["missed"],
        "recall": f"{found}/{len(expected)}",
        "FALSE_AUTO_LINKS": false_auto,
        "false_suggestions": false_suggested,
        "pairs": {
            "missed": sorted(label(pair) for pair in expected if pair not in best),
            "false_auto": sorted(
                label(pair)
                for pair, state in best.items()
                if state == "auto" and pair not in expected
            ),
            "false_suggested": sorted(
                label(pair)
                for pair, state in best.items()
                if state == "suggested" and pair not in expected
            ),
        },
    }


def _has_duplicate_flag(case: CaseResult) -> bool:
    return case.status is CaseStatus.DUPLICATE or ReasonCode.DUP_SUSPECTED in case.reason_codes


def score_recon(
    documents: list[SystemDocument],
    cases: tuple[CaseResult, ...],
    truth: Truth,
    critical_wrong: Mapping[tuple[str, int], bool],
) -> dict[str, Any]:
    truth_by_id = truth.by_id()
    by_document = {item.document_id: item for item in documents}
    truth_of = {item.document_id: item.current.truth_id for item in documents}
    cases_by_invoice = {case.invoice_id: case for case in cases if case.invoice_id is not None}
    system_by_truth: dict[str, list[SystemDocument]] = defaultdict(list)
    for item in sorted(documents, key=lambda value: str(value.document_id)):
        if item.current.truth_id is not None:
            system_by_truth[item.current.truth_id].append(item)

    extra_documents = sum(len(items) - 1 for items in system_by_truth.values())
    extra_documents_by_type: Counter[str] = Counter()
    for truth_id, items in system_by_truth.items():
        extra_documents_by_type[truth_by_id[truth_id].doc_type.value] += max(0, len(items) - 1)
    wrong_merges = sum(
        len({version.truth_id for version in item.versions if version.truth_id}) > 1
        for item in documents
    )
    caught = missed = false_duplicates = 0
    for case in cases:
        if not _has_duplicate_flag(case) or case.duplicate_of_id is None:
            continue
        own = truth_of.get(case.invoice_id) if case.invoice_id is not None else None
        if own is None or own != truth_of.get(case.duplicate_of_id):
            false_duplicates += 1
    for truth_id, copies in system_by_truth.items():
        if truth_by_id[truth_id].doc_type is not DocType.INVOICE or len(copies) < 2:
            continue
        flagged = 0
        for copy in copies:
            copy_case = cases_by_invoice.get(copy.document_id)
            if (
                copy_case is not None
                and _has_duplicate_flag(copy_case)
                and copy_case.duplicate_of_id is not None
                and truth_of.get(copy_case.duplicate_of_id) == truth_id
            ):
                flagged += 1
        caught += min(flagged, len(copies) - 1)
        missed += max(0, len(copies) - 1 - flagged)

    agree = disagree = not_scored = 0
    confusion: Counter[str] = Counter()
    disagreements: list[str] = []
    primary_ids: set[UUID] = set()
    for expected_doc in truth.documents:
        if expected_doc.doc_type is not DocType.INVOICE:
            continue
        candidates = [
            (system, cases_by_invoice.get(system.document_id))
            for system in system_by_truth.get(expected_doc.doc_id, [])
        ]
        primary = next(
            (pair for pair in candidates if pair[1] and not _has_duplicate_flag(pair[1])),
            candidates[0] if candidates else None,
        )
        if primary is None or primary[1] is None or not expected_doc.expected_status:
            not_scored += 1
            continue
        primary_ids.add(primary[0].document_id)
        status = primary[1].status.value
        confusion[f"{'|'.join(expected_doc.expected_status)}->{status}"] += 1
        if status in expected_doc.expected_status:
            agree += 1
        else:
            disagree += 1
            disagreements.append(f"{expected_doc.doc_id}:{status}")

    false_matched = 0
    for case in cases:
        if case.status not in _MATCHED or case.invoice_id is None:
            continue
        matched_doc = by_document.get(case.invoice_id)
        matched_truth = truth_of.get(case.invoice_id)
        current = matched_doc.current if matched_doc is not None else None
        bad = (
            matched_truth is None
            or case.status.value not in truth_by_id[matched_truth].expected_status
            or case.invoice_id not in primary_ids
            or (current is not None and critical_wrong.get((current.file_id, current.index), False))
        )
        false_matched += int(bad)
    case_status = Counter(case.status.value for case in cases)
    return {
        "system_documents": len(documents),
        "extra_documents": extra_documents,
        "extra_documents_by_type": dict(sorted(extra_documents_by_type.items())),
        "wrong_merges": wrong_merges,
        "duplicates_caught": caught,
        "duplicates_missed": missed,
        "false_duplicates": false_duplicates,
        "cases": len(cases),
        "case_status": dict(sorted(case_status.items())),
        "status_agree": agree,
        "status_disagree": disagree,
        "status_not_scored": not_scored,
        "status_confusion": dict(sorted(confusion.items())),
        "status_disagreements": sorted(disagreements),
        "FALSE_MATCHED": false_matched,
    }


# --------------------------------------------------------------------------- report


def _ratio(numerator: int, denominator: int) -> str:
    return f"{numerator}/{denominator}" + (
        f" ({100 * numerator / denominator:.1f}%)" if denominator else ""
    )


def extraction_summary(scores: list[DocScore]) -> dict[str, Any]:
    def block(items: list[DocScore]) -> dict[str, Any]:
        outcomes: Counter[str] = Counter()
        per_field: dict[str, Counter[str]] = defaultdict(Counter)
        for score in items:
            for name, outcome in score.fields.items():
                outcomes[outcome] += 1
                per_field[name][outcome] += 1
        scored = sum(outcomes.values())
        documents = len(items)
        return {
            "documents": documents,
            "header_correct": _ratio(outcomes["correct"], scored),
            "header_wrong": outcomes["wrong"] + outcomes["unexpected"],
            "header_missing": outcomes["missing"],
            "per_field_correct": {
                name: f"{counts['correct']}/{sum(counts.values())}"
                for name, counts in sorted(per_field.items())
            },
            "trusted_but_wrong": sum(len(score.trusted_wrong) for score in items),
            "line_recall": _ratio(
                sum(score.lines_found for score in items),
                sum(score.lines_truth for score in items),
            ),
            "line_spurious": sum(score.lines_spurious for score in items),
            "line_description_ok": _ratio(
                sum(score.desc_correct for score in items),
                sum(score.desc_scored for score in items),
            ),
            "auto_verified": _ratio(sum(not score.flagged for score in items), documents),
            "fields_to_touch_avg": round(sum(score.touch for score in items) / documents, 2)
            if documents
            else 0,
            "fields_to_confirm_avg": round(sum(score.confirm for score in items) / documents, 2)
            if documents
            else 0,
            "FALSE_SAFE": sum(score.false_safe for score in items),
        }

    by_type: dict[str, list[DocScore]] = defaultdict(list)
    for score in scores:
        by_type[score.doc_type.value].append(score)
    return {
        "all": block(scores),
        **{name: block(items) for name, items in sorted(by_type.items())},
    }


def _print_summary(report: Mapping[str, Any]) -> None:
    hard = report["hard"]
    extraction = report["extraction"]["all"]
    print("=" * 72)
    print(
        f"Accuracy bench  engine={report['meta']['engine_version']}  "
        f"truth={report['meta']['truth_sha256'][:12]}  report={report['report_hash'][:12]}"
    )
    print("-" * 72)
    print(
        f"Files read: {report['segmentation']['files']}  "
        f"parsed documents: {report['segmentation']['parsed_documents']}  "
        f"missed: {report['segmentation']['missed_documents']}  "
        f"spurious: {report['segmentation']['spurious_documents']}"
    )
    for name in ("all", "invoice", "po", "grn"):
        block = report["extraction"].get(name)
        if not block:
            continue
        print(
            f"[{name:7}] docs={block['documents']:3}  header={block['header_correct']:>18}  "
            f"lines={block['line_recall']:>18}  auto-verified={block['auto_verified']:>16}  "
            f"touch/doc={block['fields_to_touch_avg']}"
        )
    print(f"Trusted-but-wrong fields: {extraction['trusted_but_wrong']}")
    vendors = report["vendors"]
    print(
        f"Vendors created: {vendors['vendors_created']}  junk: {vendors['junk_reasons']}  "
        f"per-document: {vendors['document_vendor']}"
    )
    links = report["links"]
    print(
        f"Links: expected={links['expected_links']} auto={links['found_auto']} "
        f"suggested={links['found_suggested']} missed={links['missed']} "
        f"false-suggestions={links['false_suggestions']}"
    )
    recon = report["recon"]
    print(
        f"Identity: system docs={recon['system_documents']} extra={recon['extra_documents']} "
        f"extra-by-type={recon['extra_documents_by_type']} "
        f"wrong-merges={recon['wrong_merges']}  duplicates caught={recon['duplicates_caught']} "
        f"missed={recon['duplicates_missed']} false={recon['false_duplicates']}"
    )
    print(
        f"Recon: cases={recon['cases']} {recon['case_status']}  status agree="
        f"{recon['status_agree']} disagree={recon['status_disagree']}"
    )
    print("-" * 72)
    print("HARD (must be 0): " + "  ".join(f"{name}={value}" for name, value in hard.items()))
    print("GATE: " + ("PASS" if not any(hard.values()) else "FAIL"))
    print("=" * 72)


def vision_arm(arguments: argparse.Namespace) -> accuracy_vision.VisionArm:
    """The run's one vision provider and reader settings (WI-EV1; refused before any read)."""

    if getattr(arguments, "vision_primary_sim", False):
        raise accuracy_vision.ArmRefused(
            "--vision-primary-sim needs merge_readings from WI-BE7, which does not exist yet; "
            "nothing was run"
        )
    arm = accuracy_vision.VisionArm(
        provider=getattr(arguments, "vision_provider", "gemini"),
        thinking=getattr(arguments, "vision_thinking", None),
        sampling=getattr(arguments, "vision_sampling", None),
        strategy=getattr(arguments, "vision_strategy", "full"),
        base_url=getattr(arguments, "vision_base_url", None),
        repeats=getattr(arguments, "repeat", 1),
        prompt_variant=getattr(arguments, "vision_prompt_variant", None),
        send_from_repeat=getattr(arguments, "send_from_repeat", 0),
    )
    return accuracy_vision.check_arm(
        arm, allowlist=os.environ.get("RB_AI_VLLM_ENDPOINTS"), model=arguments.vision_model
    )


def _scan_type(pages: Iterable[Any]) -> str:
    flags = [page.is_ocr for page in pages]
    if not flags:
        return "unknown"
    return "scan" if all(flags) else "mixed" if any(flags) else "digital"


def run_vision(
    arguments: argparse.Namespace,
    truth: Truth,
    reads: Mapping[str, ReadOutcome],
    parsed: list[Parsed],
    languages: tuple[str, ...],
    fingerprint: Mapping[str, str],
    arm: accuracy_vision.VisionArm = accuracy_vision.DEFAULT_ARM,
) -> tuple[dict[str, Any], accuracy_vision.VisionRun]:
    """Vision readings for every parsed PDF document (AC-5); counts only."""

    paths = {item.file_id: item.path for item in truth.files}
    targets: list[accuracy_vision.VisionTarget] = []
    for item in parsed:
        if item.structured:
            continue
        outcome = reads[item.file_id]
        page_count = max((page.page for page in outcome.result.page_texts), default=0)
        pages = accuracy_vision.document_pages(item.document, page_count)
        own = [page for page in outcome.result.page_texts if page.page in pages]
        targets.append(
            accuracy_vision.VisionTarget(
                file_id=item.file_id,
                index=item.index,
                truth_id=item.truth_id,
                doc_type=item.document.header.doc_type,
                document=item.document,
                content=(arguments.samples / paths[item.file_id]).read_bytes(),
                pages=pages,
                ocr_pages=any(page.is_ocr for page in outcome.result.page_texts),
                scan_type=_scan_type(own),
                ocr_text="\n".join(page.content for page in own),
            )
        )
    accuracy_vision.prepare(
        targets,
        root=ROOT,
        pages_cache=arguments.vision_pages,
        tool_versions={key: fingerprint[key] for key in ("tesseract", "languages")},
        languages=languages,
        refresh=arguments.refresh,
        model=arguments.vision_model,
        workers=arguments.workers,
        arm=arm,
    )
    run = accuracy_vision.VisionRun(arguments.vision, arguments.vision_model, targets, arm=arm)
    compat_v1 = arguments.vision_cache_v1 if arguments.vision_cache_compat == "v1" else None
    if compat_v1 is not None and not accuracy_vision.compat_v1_applies(arm):
        print("Vision: --vision-cache-compat v1 ignored (reader parameters differ from v1).")
    missing = [
        target
        for target in targets
        if target.key is not None and _missing_answers(target, arguments, compat_v1, arm)
    ]
    retry: frozenset[str] = frozenset()
    if getattr(arguments, "vision_retry_invalid", False):
        # Only requests whose recorded answer is unusable; no new document is added.
        retry = accuracy_vision.unusable_keys(targets, arguments.vision_cache)
        missing = [
            target
            for target in targets
            if any(key in retry for key in (target.key, *target.repeat_keys))
        ]
    if missing:
        # Most informative documents first, so a run cut short still has the best data.
        waiting = {id(target) for target in missing}
        covered = frozenset(
            str(target.truth_id)
            for target in targets
            if not retry
            and target.key is not None
            and target.truth_id
            and id(target) not in waiting
        )
        missing = _priority_subset(missing, truth.by_id(), arguments.max_provider_calls, covered)
    if arguments.vision == "provider" and missing:
        if not arguments.allow_provider:
            raise SystemExit(
                "--vision provider sends page images; add --allow-provider (owner only)"
            )
        pages = sum(
            len(target.rows) * _missing_answers(target, arguments, compat_v1, arm, retry)
            for target in missing
        )
        destination = "the vLLM server" if arm.local else arguments.vision_model
        print(
            f"Vision: {len(missing)} document(s), {pages} masked page image(s) to send to "
            f"{destination}; page budget {arguments.max_provider_pages}."
        )
        if pages > arguments.max_provider_pages:
            run.provider = {"not_sent_over_budget": len(missing)}
            print("Vision: over the page budget, nothing was sent.")
        else:
            run.provider = accuracy_vision.send(
                missing,
                arguments.vision_cache,
                model=arguments.vision_model,
                # The company server needs no key (plan §0.5); Gemini's is never read here.
                api_key=os.environ.get("BENCH_VLLM_KEY", "")
                if arm.local
                else accuracy_vision.provider_key(),
                max_output_tokens=arguments.vision_max_output_tokens,
                failures=run.failures,
                pause_seconds=arguments.vision_pause,
                arm=arm,
                retry=retry,
            )
    crops: dict[str, Any] | None = None
    if arm.strategy in accuracy_vision.CROP_STRATEGIES:
        # WI-BE8 (D97): pass 2 on flagged pages; crops are sent only with the flag.
        crops = accuracy_vision.crop_pass(
            targets,
            arguments.vision_cache,
            arm=arm,
            model=arguments.vision_model,
            send_crops=bool(arguments.vision_crops_send and arguments.allow_provider),
            max_calls=arguments.max_provider_pages,
            failures=run.failures,
            api_key=os.environ.get("BENCH_VLLM_KEY", ""),
        )
        print(f"Vision crops: {crops}")
    accuracy_vision.check_answers(
        targets,
        arguments.vision_cache,
        truth.buyer_entities_hints(),
        run.usage,
        compat_v1=compat_v1,
        arm=arm,
        repeat_usage=run.repeat_usage,
    )
    if compat_v1 is not None:
        served = sum(
            1
            for target in targets
            if target.key is not None
            and not (arguments.vision_cache / f"{target.key}.json").is_file()
            and accuracy_vision.answer_path(target, arguments.vision_cache, compat_v1, arm)
            is not None
        )
        print(f"Vision: {served} answer(s) read from the v1 cache (read-only).")
    result = accuracy_vision.summary(
        run,
        truth.by_id(),
        score_document,
        extraction_summary,
        _normal,
        _same,
        _HEADER_FIELDS,
    )
    if crops is not None:
        result["crops"] = crops
    return result, run


def _missing_answers(
    target: accuracy_vision.VisionTarget,
    arguments: argparse.Namespace,
    compat_v1: Path | None,
    arm: accuracy_vision.VisionArm,
    retry: frozenset[str] = frozenset(),
) -> int:
    """How many of the target's requests (repeat 0 and each repeat) will be sent: no
    answer yet, or (``--vision-retry-invalid``) an unusable one."""

    start = arm.send_from_repeat  # earlier repeats are scored, never sent
    main = accuracy_vision.answer_path(target, arguments.vision_cache, compat_v1, arm) is None
    return int(start == 0 and (main or target.key in retry)) + sum(
        not (arguments.vision_cache / f"{key}.json").is_file() or key in retry
        for index, key in enumerate(target.repeat_keys, start=1)
        if index >= start
    )


def _print_consensus(block: Mapping[str, Any]) -> None:
    upper = block.get("upper_bound", {})
    fields = upper.get("by_field_type", {})
    print("CONSENSUS SHADOW (upper bound; crop not required)")
    for name, counts in sorted(fields.items()):
        print(
            f"  {name}: L1={counts['l1']} correct={counts['l1_correct']} "
            f"wrong={counts['l1_wrong']} gate={counts['gate']}"
        )


def _priority_subset(
    missing: list[accuracy_vision.VisionTarget],
    truth_by_id: Mapping[str, TruthDocument],
    limit: int | None,
    covered: frozenset[str] = frozenset(),
) -> list[accuracy_vision.VisionTarget]:
    """At most ``limit`` (or all) distinct requests, most informative first.

    Order: truth with handwritten refs, truth with lines, other truth, no truth; copies
    with the same request key count once. Deterministic for a given truth file.

    With a ``limit``, each request is a different truth document: another file or
    bundle page of a truth document already chosen, or already answered
    (``covered``), takes no slot and is not sent. Documents without truth each count
    as their own. Without a limit every missing request is kept.
    """

    def rank(target: accuracy_vision.VisionTarget) -> tuple[int, str, int]:
        item = truth_by_id.get(target.truth_id or "")
        if item is None:
            score = 3
        elif item.annotation_refs:
            score = 0
        elif item.lines:
            score = 1
        else:
            score = 2
        return score, target.file_id, target.index

    chosen: list[accuracy_vision.VisionTarget] = []
    keys: set[str] = set()
    truths: set[str] = set(covered)
    for target in sorted(missing, key=rank):
        if target.key in keys:
            chosen.append(target)
            continue
        if limit is not None:
            if len(keys) >= limit or (target.truth_id is not None and target.truth_id in truths):
                continue
            if target.truth_id is not None:
                truths.add(target.truth_id)
        keys.add(str(target.key))
        chosen.append(target)
    return chosen


def _print_vision(block: Mapping[str, Any]) -> None:
    alone = (block.get("alone") or {}).get("all") or {}
    print(
        f"Vision ({block['mode']}, {block['model']}): pdf docs={block['pdf_documents']} "
        f"pages={block['pages']} outcomes={block['outcomes']}"
    )
    print(f"  masked regions: {block['masked_regions']}  usage: {block['usage']}")
    if block.get("provider"):
        print(f"  provider calls: {block['provider']}")
    for failure in block.get("failures", []):
        print(f"  failed call: {json.dumps(failure, sort_keys=True)}")
    if alone:
        print(
            f"  vision alone: header={alone['header_correct']} lines={alone['line_recall']} "
            f"handwritten refs={block['annotation_refs_found']} "
            f"marketplace order={block['marketplace_order_found']}"
        )
        print(f"  handwritten refs (where found): {block.get('handwritten_refs', {})}")
        print(f"  line rows read: {block.get('line_cells', {})}")
        print(f"  OCR x vision: {block['agreement']['all']}")
        print(f"  dropped by checks: {block.get('dropped', {})}")
        print(f"  output tokens per answer: {block.get('output_tokens_per_call', {})}")


def _print_vision_eval(block: Mapping[str, Any]) -> None:
    arm = block["arm"]
    print(
        f"VISION EVAL ({arm['provider']}, {block['model']}, thinking={arm['thinking']}, "
        f"sampling={arm['sampling_profile']}, prompt={arm.get('prompt_version')}, "
        f"repeats={arm['repeats']}, "
        f"policy={block['agreement_policy']}): documents={block['documents_scored']}"
    )
    for group in ("all_pdf", "scan_page"):
        readers = block["accuracy"].get(group, {})
        header = {
            reader: fields.get("header", {}).get("accuracy") for reader, fields in readers.items()
        }
        print(f"  {group} header: {header}")
    print(f"  false-safe: {block['false_safe']}")
    print(f"  wrong when answered: {block['wrong_when_answered']}")
    print(f"  LLM x OCR: {block['agree_wrong_llm_x_ocr']}")
    print(f"  latency ms: {block['latency_ms']}  tokens: {block['tokens']}")
    print(f"  failures: {block['failures_by_code']}  repeats: {block['repeat_variance']}")


def write_prefill(path: Path, truth: Truth, reads: Mapping[str, ReadOutcome]) -> int:
    """Draft truth documents for files that have none yet (owner corrects them)."""

    drafts: list[dict[str, Any]] = []
    for truth_file in truth.files:
        if truth.for_file(truth_file.file_id):
            continue
        outcome = reads[truth_file.file_id]
        for index, document in enumerate(outcome.result.documents):
            header: dict[str, Any] = {}
            for name in _HEADER_FIELDS:
                value = _plain(_header_field(document, name))
                if value is None:
                    continue
                header[name] = (
                    list(value)
                    if isinstance(value, tuple)
                    else (value.isoformat() if isinstance(value, date) else str(value))
                )
            drafts.append(
                {
                    "id": f"DRAFT-{truth_file.file_id}-{index + 1}",
                    "doc_type": document.header.doc_type.value,
                    "status": "draft: values as extracted, correct before use",
                    "sources": [{"file": truth_file.file_id}],
                    "header": header,
                    "lines": [
                        {
                            "description": str(_plain(line.description) or ""),
                            "quantity": str(_line_qty(line))
                            if _line_qty(line) is not None
                            else None,
                            "rate": str(_plain(line.rate)) if line.rate else None,
                            "amount": str(_plain(line.amount)) if line.amount else None,
                        }
                        for line in document.lines
                    ],
                }
            )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"draft_documents": drafts}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return len(drafts)


def config_body(truth: Truth, buyer_entities: str) -> dict[str, Any]:
    """The seed config with own GSTINs from the truth file, the seed as is, or none."""

    body: dict[str, Any] = json.loads(json.dumps(DEFAULT_CONFIG))
    if buyer_entities == "truth":
        body["engine"]["buyer_entities"] = [dict(item) for item in truth.buyer_entities]
    elif buyer_entities == "none":
        body["engine"]["buyer_entities"] = []
    elif buyer_entities != "config":
        raise ValueError(f"unknown buyer_entities mode: {buyer_entities}")
    return body


def run(arguments: argparse.Namespace) -> int:
    arm = vision_arm(arguments) if arguments.vision != "off" else accuracy_vision.DEFAULT_ARM
    policy = getattr(arguments, "agreement_policy", "single")
    evaluate = arguments.vision != "off" and (
        getattr(arguments, "vision_eval", False)
        or arm != accuracy_vision.DEFAULT_ARM
        or policy != "single"
    )
    truth = load_truth(arguments.truth)
    body = config_body(truth, arguments.buyer_entities)
    languages = tuple(arguments.languages.split("+"))
    key_file: Path | None = arguments.einvoice_key_file
    key_bytes = key_file.read_bytes() if key_file is not None else None
    public_keys = einvoice_keys(key_file)
    fingerprint = reader_fingerprint(languages, arguments.engine, key_bytes)

    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=max(1, arguments.workers)) as pool:
        futures = {
            item.file_id: pool.submit(
                read_file,
                item,
                arguments.samples,
                arguments.cache,
                fingerprint,
                body,
                refresh=arguments.refresh,
                languages=languages,
                engine_name=arguments.engine,
                einvoice_public_keys=public_keys,
            )
            for item in truth.files
        }
        reads = {file_id: future.result() for file_id, future in futures.items()}
    read_seconds = time.monotonic() - started

    parsed: list[Parsed] = []
    scores: list[DocScore] = []
    details: list[dict[str, Any]] = []
    critical_wrong: dict[tuple[str, int], bool] = {}
    missed_documents = spurious_documents = failed_files = 0
    truth_by_id = truth.by_id()
    for truth_file in truth.files:
        outcome = reads[truth_file.file_id]
        documents = outcome.result.documents if outcome.result.success else ()
        failed_files += int(not outcome.result.success)
        truth_docs = truth.for_file(truth_file.file_id)
        assigned = assign_truth(truth_docs, documents)
        missed_documents += len(truth_docs) - len(set(assigned.values()))
        for index, document in enumerate(documents):
            truth_id = assigned.get(index)
            own_pages = split_pages(document)
            document_page_hashes = tuple(
                page_hash
                for page_hash in outcome.result.page_image_hashes
                if not own_pages or page_hash.page in own_pages
            )
            parsed.append(
                Parsed(
                    truth_file.file_id,
                    index,
                    outcome.sha256,
                    document,
                    truth_id,
                    structured=_format_for(Path(truth_file.path)) is not FileFormat.PDF,
                    page_hashes=document_page_hashes,
                )
            )
            if truth_id is None:
                spurious_documents += 1
                details.append({"file": truth_file.file_id, "index": index, "truth": None})
                continue
            score = score_document(truth_by_id[truth_id], truth_file.file_id, document)
            scores.append(score)
            critical_wrong[(truth_file.file_id, index)] = score.critical_wrong
            details.append(
                {
                    "file": truth_file.file_id,
                    "index": index,
                    "truth": truth_id,
                    "fields": score.fields,
                    "trusted_but_wrong": score.trusted_wrong,
                    "lines": f"{score.lines_found}/{score.lines_truth} "
                    f"(+{score.lines_spurious} extra)",
                    "touch": score.touch,
                    "flagged": score.flagged,
                    "false_safe": score.false_safe,
                }
            )

    vision_block: dict[str, Any] | None = None
    vision_run: accuracy_vision.VisionRun | None = None
    if arguments.vision != "off":
        vision_block, vision_run = run_vision(
            arguments, truth, reads, parsed, languages, fingerprint, arm
        )

    workspace = build_workspace(parsed)
    vendor_of, vendors = simulate_vendors(workspace, body)
    snapshot, resolutions = build_snapshot(workspace, vendor_of, vendors, body, truth.run_date)
    cases = evaluate_components(
        snapshot,
        reconciliation_config(body, effective_date=truth.run_date),
        truth.run_date,
    )

    extraction = extraction_summary(scores)
    qr_fields = [
        document.document.header.extras["einvoice"]
        for document in parsed
        if "einvoice" in document.document.header.extras
    ]
    extraction["einvoice_qr"] = {
        "documents": len(qr_fields),
        "signed": sum(bool(item.get("signed")) for item in qr_fields),
        "fields": dict(
            sorted(
                Counter(
                    status for item in qr_fields for status in item.get("fields", {}).values()
                ).items()
            )
        ),
        "not_used": sum(
            warning.code.startswith("EINVOICE_")
            and warning.code != "EINVOICE_QR_APPLIED"
            and warning.code != "EINVOICE_ITEM_COUNT_MISMATCH"
            for outcome in reads.values()
            for warning in outcome.result.warnings
        ),
    }
    vendor_report = score_vendors(workspace, vendor_of, vendors, truth)
    link_report = score_links(workspace, resolutions, truth)
    recon_report = score_recon(workspace, cases, truth, critical_wrong)
    report: dict[str, Any] = {
        "meta": {
            "engine_version": ENGINE_VERSION,
            "rules_hash": rules_hash(reconciliation_config(body, effective_date=truth.run_date)),
            "reader": fingerprint,
            "truth_sha256": truth.digest,
            "run_date": truth.run_date.isoformat(),
            "buyer_entities": arguments.buyer_entities,
        },
        "segmentation": {
            "files": len(truth.files),
            "failed_files": failed_files,
            "truth_documents": len(truth.documents),
            "parsed_documents": len(parsed),
            "missed_documents": missed_documents,
            "spurious_documents": spurious_documents,
        },
        "extraction": extraction,
        "vendors": vendor_report,
        "links": link_report,
        "recon": recon_report,
        "hard": {
            "FALSE_SAFE": extraction["all"]["FALSE_SAFE"],
            "FALSE_AUTO_LINKS": link_report["FALSE_AUTO_LINKS"],
            "FALSE_MATCHED": recon_report["FALSE_MATCHED"],
            "WRONG_VENDORS_CREATED": vendor_report["WRONG_VENDORS_CREATED"],
        },
        "documents": details,
    }
    report["report_hash"] = _sha256(_canonical_json(report))
    if vision_block is not None:
        report["vision"] = vision_block
        report["vision_hash"] = _sha256(_canonical_json(vision_block))
    if evaluate and vision_run is not None:
        price_file = os.environ.get("RB_AI_PRICE_FILE")
        eval_block = accuracy_vision_eval.evaluate(
            vision_run,
            truth.by_id(),
            normal=_normal,
            same=_same,
            header_fields=_HEADER_FIELDS,
            critical=_CRITICAL,
            score_document=score_document,
            policy=policy,
            price_file=Path(price_file) if price_file else None,
        )
        report["vision_eval"] = eval_block
        report["vision_eval_hash"] = _sha256(_canonical_json(eval_block))
    if getattr(arguments, "consensus", "shadow") == "shadow" and vision_run is not None:
        block = {
            "upper_bound": accuracy_consensus.summary(
                vision_run,
                truth.by_id(),
                score_document,
                extraction_summary,
                _normal,
                _same,
                buyer_entities=truth.buyer_entities_hints(),
                run_date=truth.run_date,
                require_crop=False,
            )
        }
        report["consensus"] = block
        report["consensus_hash"] = _sha256(_canonical_json(block))
    report["timing"] = {
        "read_seconds": round(read_seconds, 1),
        "cache_hits": sum(outcome.cached for outcome in reads.values()),
        "files_read": sum(not outcome.cached for outcome in reads.values()),
        "timeouts": sum(outcome.result.reason_code == _TIMEOUT for outcome in reads.values()),
    }
    arguments.out.parent.mkdir(parents=True, exist_ok=True)
    arguments.out.write_text(
        json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    if arguments.prefill is not None:
        count = write_prefill(arguments.prefill, truth, reads)
        print(f"Wrote {count} draft truth document(s) to {arguments.prefill}")
    if arguments.details:
        for row in details:
            print(json.dumps(row, sort_keys=True))
    _print_summary(report)
    if vision_block is not None:
        _print_vision(vision_block)
    if "consensus" in report:
        _print_consensus(report["consensus"])
    if "vision_eval" in report:
        _print_vision_eval(report["vision_eval"])
    print(
        f"Report: {arguments.out}  (cache hits {report['timing']['cache_hits']}, "
        f"read {report['timing']['files_read']} file(s) in {report['timing']['read_seconds']} s)"
    )
    if report["timing"]["timeouts"]:
        print(
            f"WARNING: {report['timing']['timeouts']} file(s) hit {_TIMEOUT}; "
            "these reads are not cached and the run must be repeated."
        )
    return 1 if any(report["hard"].values()) else 0


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    work = ROOT / "work"
    parser.add_argument("--truth", type=Path, default=work / "accuracy-truth" / "truth.json")
    parser.add_argument("--samples", type=Path, default=work / "accuracy-samples")
    parser.add_argument("--cache", type=Path, default=work / "accuracy-cache")
    parser.add_argument("--out", type=Path, default=work / "accuracy-reports" / "latest.json")
    parser.add_argument("--prefill", type=Path, default=None)
    parser.add_argument("--refresh", action="store_true", help="ignore cached reader outputs")
    parser.add_argument("--details", action="store_true", help="print per-document outcomes")
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="parallel fresh reads; more than 1 risks sandbox timeouts (cached reads are instant)",
    )
    parser.add_argument("--languages", default=os.environ.get("RB_PDF_OCR_LANGUAGES", "eng"))
    parser.add_argument("--engine", default=os.environ.get("RB_EXTRACTION_ENGINE", "local_hybrid"))
    parser.add_argument(
        "--buyer-entities",
        choices=("truth", "config", "none"),
        default="truth",
        help="own GSTINs: from the truth file (history), the seed config as is, or none",
    )
    parser.add_argument(
        "--einvoice-key-file",
        type=Path,
        default=Path(os.environ["RB_EINVOICE_PUBLIC_KEY_FILE"])
        if os.environ.get("RB_EINVOICE_PUBLIC_KEY_FILE")
        else None,
        help="e-invoice portal public key(s), loaded as the app loads them",
    )
    parser.add_argument(
        "--vision",
        choices=("off", "cached", "provider"),
        default="cached",
        help="page-image reader (AC-5): off, recorded answers only, or record missing ones",
    )
    parser.add_argument(
        "--consensus",
        choices=("off", "shadow"),
        default="shadow",
        help="AC-6 consensus: off or counts-only shadow scoring",
    )
    parser.add_argument(
        "--allow-provider",
        action="store_true",
        help="owner only: allow --vision provider to send masked page images to Gemini",
    )
    parser.add_argument(
        "--max-provider-pages",
        type=int,
        default=0,
        help="page budget for --vision provider; nothing is sent when more pages are missing",
    )
    parser.add_argument(
        "--vision-provider",
        choices=accuracy_vision.PROVIDERS,
        default="gemini",
        help="the one vision provider of this run (Gemini and vLLM are never combined)",
    )
    parser.add_argument(
        "--vision-base-url",
        default=None,
        help="vLLM only: the server base URL; refused unless it is on RB_AI_VLLM_ENDPOINTS",
    )
    parser.add_argument(
        "--vision-model",
        default=None,
        help="default: RB_BENCH_VISION_MODEL or gemini-3.8-flash (Gemini), "
        f"{accuracy_vision.DEFAULT_VLLM_MODEL} (vLLM)",
    )
    parser.add_argument(
        "--vision-thinking",
        choices=accuracy_vision.THINKING_CHOICES,
        default=None,
        help="default: the reader's own (Gemini low, vLLM off; vLLM takes off only)",
    )
    parser.add_argument(
        "--vision-sampling",
        choices=accuracy_vision.SAMPLING_CHOICES,
        default=None,
        help="default: the reader's own (Gemini provider_default, vLLM qwen_card_nonthinking)",
    )
    parser.add_argument(
        "--send-from-repeat",
        type=int,
        default=0,
        help="send only repeats >= N (earlier ones are scored from the cache, never sent)",
    )
    parser.add_argument(
        "--vision-prompt-variant",
        choices=tuple(accuracy_vision.VISION_PROMPT_VARIANTS),
        default=None,
        help="default: the reader's own (Gemini gemini, vLLM the app's current Qwen wording)",
    )
    parser.add_argument(
        "--vision-strategy",
        choices=accuracy_vision.STRATEGY_CHOICES,
        default="full",
        help="full page images; band reads the top band of page 1 only (WI-Q2); "
        "crops re-reads flagged pages of the newest repeat as three crops (WI-BE8); "
        "crops-control scores that newest repeat alone",
    )
    parser.add_argument(
        "--vision-crops-send",
        action="store_true",
        help="send the WI-BE8 crop calls (with --allow-provider); otherwise cached crops only",
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="read every request N times (the repeat index is in the cache key)",
    )
    parser.add_argument(
        "--agreement-policy",
        choices=accuracy_vision_eval.POLICIES,
        default="single",
        help="vision_eval auto-accept candidates: the LLM alone, or only where OCR agrees",
    )
    parser.add_argument(
        "--vision-primary-sim",
        action="store_true",
        help="refused until WI-BE7's merge_readings exists",
    )
    parser.add_argument(
        "--vision-retry-invalid",
        action="store_true",
        help="send again only requests whose recorded answer fails the strict parse",
    )
    parser.add_argument(
        "--vision-eval",
        action="store_true",
        help="write the counts-only vision_eval block for the default reader too",
    )
    parser.add_argument(
        "--vision-max-output-tokens",
        type=int,
        default=None,
        help="output token cap per vision call (default: the app's h6_vision limit)",
    )
    parser.add_argument(
        "--max-provider-calls",
        type=int,
        default=None,
        help="send at most N distinct vision requests, most informative documents first",
    )
    parser.add_argument(
        "--vision-pause",
        type=float,
        default=0.0,
        help="seconds to wait between vision calls (for low per-minute quotas)",
    )
    parser.add_argument("--vision-cache", type=Path, default=work / "accuracy-cache" / "vision")
    parser.add_argument(
        "--vision-cache-compat",
        choices=("none", "v1"),
        default="none",
        help="v1: also read answers recorded under the pre-WI-BE5 cache key (read-only)",
    )
    parser.add_argument(
        "--vision-cache-v1", type=Path, default=work / "accuracy-cache" / "vision-v1"
    )
    parser.add_argument(
        "--vision-pages", type=Path, default=work / "accuracy-cache" / "vision-pages"
    )
    arguments = parser.parse_args(list(argv) if argv is not None else None)
    if arguments.vision_model is None:
        arguments.vision_model = (
            accuracy_vision.DEFAULT_VLLM_MODEL
            if arguments.vision_provider == "vllm"
            else os.environ.get("RB_BENCH_VISION_MODEL", "gemini-3.8-flash")
        )
    return run(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
