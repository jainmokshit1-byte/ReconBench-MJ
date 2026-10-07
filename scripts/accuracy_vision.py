"""Vision reader in the accuracy bench (accuracy plan AC-5): cached page-image readings.

For every PDF document the bench parsed, the app's own sandbox code renders and
masks its pages (``vision_pages_isolated``), and the app's own prompt, payload
digest and cache key identify the request the AI worker would send. A recorded
answer for that key (``work/accuracy-cache/vision/<key>.json``) is checked by
the same rules as in the app and scored *alone* against the truth, and next to
the OCR reading (the agreement matrix AC-6 needs). In AC-5 vision values are
suggestions only, so the main pipeline (vendors, links, reconciliation) never
sees them.

Provider calls happen only in the owner's run: ``--vision provider
--allow-provider`` with ``BENCH_GEMINI_KEY`` set and a page budget
(``--max-provider-pages``) that covers every missing page; otherwise nothing is
sent. Masked page images and answers stay in ``work/`` (git-ignored); the report
holds counts only, never document values.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from reconbench_app.ai.cache import cache_key
from reconbench_app.ai.contracts import SCHEMA_VERSIONS, InvalidOutput, VisionOutput, parse_output
from reconbench_app.ai.gate import payload_digest
from reconbench_app.ai.limits import AiLimits
from reconbench_app.ai.prompts import PROMPT_VERSIONS, PromptPayload, vision_prompt
from reconbench_app.ai.provider import ProviderFailure, ProviderRequest
from reconbench_app.ai.redaction import IMAGE_MASK_VERSION
from reconbench_app.ai.vision import (
    HOOK,
    PageRow,
    RenderedPage,
    VisionContext,
    VisionValidation,
    provider_images,
    validate_vision,
)
from reconbench_app.extraction.vision import VISION_RENDER, vision_reader
from reconbench_app.extraction.vision_pages import vision_pages_isolated
from reconbench_core.extraction.pages import split_pages
from reconbench_core.parsers.types import BuyerEntityHint
from reconbench_core.schema import DocType, Document

READER = "vision_gemini"
MAX_PAGES = AiLimits().h6_vision.max_pages or 8
_PAGE_KIND_TYPES = {
    "invoice": "invoice",
    "credit_note": "invoice",
    "debit_note": "invoice",
    "purchase_order": "po",
    "goods_receipt": "grn",
}
_VISION_CODE = (
    "backend/reconbench_app/extraction/vision_pages.py",
    "backend/reconbench_app/extraction/vision.py",
    "backend/reconbench_app/extraction/local_hybrid.py",
    "backend/reconbench_app/ai/redaction.py",
)


@dataclass
class VisionTarget:
    """One parsed PDF document and the pages the vision reader would read."""

    file_id: str
    index: int
    truth_id: str | None
    doc_type: DocType
    document: Document
    content: bytes = field(repr=False)
    pages: tuple[int, ...]
    rows: list[PageRow] = field(default_factory=list)
    payload: PromptPayload | None = None
    key: str | None = None
    validation: VisionValidation | None = None
    outcome: str = "pending"
    output_tokens: int | None = None
    """Output plus thought tokens of the recorded answer (for the output cap decision)."""


def _sha256(data: bytes | str) -> str:
    return hashlib.sha256(data.encode("utf-8") if isinstance(data, str) else data).hexdigest()


def code_fingerprint(root: Path, tool_versions: Mapping[str, str]) -> str:
    digest = hashlib.sha256()
    for relative in _VISION_CODE:
        digest.update(relative.encode("utf-8"))
        digest.update((root / relative).read_bytes())
    digest.update(json.dumps(dict(tool_versions), sort_keys=True).encode("utf-8"))
    digest.update(f"{VISION_RENDER.version}|{IMAGE_MASK_VERSION}".encode())
    return digest.hexdigest()


def document_pages(document: Document, page_count: int) -> tuple[int, ...]:
    own = split_pages(document)
    return tuple(own) if own else tuple(range(1, page_count + 1))


def _rows(pages: Sequence[RenderedPage]) -> list[PageRow]:
    return [
        PageRow(
            page.page,
            page.sha256,
            page.mime_type,
            page.width,
            page.height,
            page.render,
            page.mask,
            page.data,
        )
        for page in pages
    ]


def load_or_render(
    target: VisionTarget,
    cache: Path,
    fingerprint: str,
    languages: tuple[str, ...],
    *,
    refresh: bool,
) -> list[PageRow] | None:
    """Masked page images from the page cache, rendered by the app's sandbox on a miss."""

    key = _sha256(
        json.dumps(
            {
                "file": _sha256(target.content),
                "pages": list(target.pages),
                "code": fingerprint,
                "languages": list(languages),
            },
            sort_keys=True,
        )
    )
    meta_path = cache / f"{key}.json"
    if not refresh and meta_path.is_file():
        try:
            stored = json.loads(meta_path.read_text(encoding="utf-8"))
            rows = []
            for item in stored["pages"]:
                data = (cache / f"{item['sha256']}.jpg").read_bytes()
                if _sha256(data) != item["sha256"]:
                    raise ValueError("page image changed on disk")
                rows.append(
                    PageRow(
                        int(item["page"]),
                        item["sha256"],
                        item["mime_type"],
                        int(item["width"]),
                        int(item["height"]),
                        item["render"],
                        item["mask"],
                        data,
                    )
                )
            return rows
        except (OSError, ValueError, KeyError):
            pass
    rendered = vision_pages_isolated(target.content, target.pages, languages=languages)
    if rendered is None:
        return None
    cache.mkdir(parents=True, exist_ok=True)
    for page in rendered:
        (cache / f"{page.sha256}.jpg").write_bytes(page.data)
    meta_path.write_text(
        json.dumps(
            {
                "pages": [
                    {
                        "page": page.page,
                        "sha256": page.sha256,
                        "mime_type": page.mime_type,
                        "width": page.width,
                        "height": page.height,
                        "render": dict(page.render),
                        "mask": dict(page.mask),
                    }
                    for page in rendered
                ]
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return _rows(rendered)


def request_key(target: VisionTarget, model: str) -> tuple[PromptPayload, str] | None:
    """The app's prompt and ``ai_cache`` key for this document's pages."""

    reader = vision_reader(READER)
    assert reader is not None
    images = provider_images(target.rows, reader.media_resolution)
    if not images:
        return None
    payload = vision_prompt(target.doc_type.value, images)  # type: ignore[arg-type]
    digest = payload_digest(
        system_instruction=payload.system_instruction,
        input_text=payload.input_text,
        response_schema=payload.response_schema,
        images=payload.image_digest_parts(),
    )
    key = cache_key(
        hook=HOOK,
        model=model,
        prompt_version=PROMPT_VERSIONS[HOOK],
        schema_version=SCHEMA_VERSIONS[HOOK],
        redaction_version=IMAGE_MASK_VERSION,
        payload_digest=digest,
    )
    return payload, key


def _answer_path(cache: Path, key: str) -> Path:
    return cache / f"{key}.json"


def send(
    targets: Sequence[VisionTarget],
    cache: Path,
    *,
    model: str,
    api_key: str,
    complete: Callable[[ProviderRequest, str], Any] | None = None,
    max_output_tokens: int | None = None,
    failures: list[dict[str, Any]] | None = None,
    pause_seconds: float = 0.0,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, int]:
    """Owner-run only: send the missing requests and record the raw answers.

    Each failure is appended to ``failures`` with its code and, from
    :mod:`scripts.vision_diagnostics`, Google's error text or the answer's status,
    tokens and row counts (never a document value). Failed answers are not
    recorded, so the next run sends them again. The run stops early when the
    rest would fail anyway (daily quota, bad key, a rejected request shape, or
    the provider down several times in a row), so a small free quota is not
    spent on calls that cannot succeed; the counts say ``stopped:<reason>``.
    """

    last: dict[str, Any] = {}
    if complete is None:
        from reconbench_app.ai.worker.gemini import GeminiProvider

        from scripts.vision_diagnostics import DiagnosticTransport

        def complete(request: ProviderRequest, key: str) -> Any:
            transport = DiagnosticTransport()
            try:
                return GeminiProvider(inner_transport=transport).complete(request, api_key=key)
            finally:
                last.clear()
                last.update(transport.info)

    limits = AiLimits()
    cap = max_output_tokens or limits.h6_vision.max_output_tokens
    counts: Counter[str] = Counter()
    cache.mkdir(parents=True, exist_ok=True)
    streak: tuple[str, int] = ("", 0)
    wait = 0.0
    for number, target in enumerate(targets):
        assert target.payload is not None and target.key is not None
        if _answer_path(cache, target.key).is_file():
            continue  # a copy with the same request key was answered in this run
        if number and max(pause_seconds, wait) > 0:
            sleep(max(pause_seconds, wait))
        wait = 0.0
        started = time.monotonic()
        last.clear()
        try:
            result = complete(
                ProviderRequest(
                    model=model,
                    system_instruction=target.payload.system_instruction,
                    input_text=target.payload.input_text,
                    response_schema=target.payload.response_schema,
                    max_output_tokens=cap,
                    timeout_ms=limits.timeout_for(HOOK) * 1_000,
                    images=target.payload.images,
                ),
                api_key,
            )
        except ProviderFailure as failure:
            counts[f"failed:{failure.code}"] += 1
            if failures is not None:
                failures.append(failure_entry(failure, last, len(target.payload.images), cap))
            streak = (failure.code, streak[1] + 1 if streak[0] == failure.code else 1)
            stop, wait = _after_failure(failure.code, str(last.get("error_message", "")), streak[1])
            if stop is not None:
                counts[f"stopped:{stop}"] = len(targets) - number - 1
                break
            continue
        streak = ("", 0)
        usage = result.usage
        _answer_path(cache, target.key).write_text(
            json.dumps(
                {
                    "model": model,
                    "output_text": result.output_text,
                    "usage": None
                    if usage is None
                    else {
                        "input_tokens": usage.input_tokens,
                        "output_tokens": usage.output_tokens,
                        "thought_tokens": usage.thought_tokens,
                    },
                    "latency_ms": int((time.monotonic() - started) * 1_000),
                    "pages": len(target.payload.images),
                    "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        counts["sent"] += 1
    return dict(counts)


_FATAL_CODES = frozenset(
    {
        "bad_key",
        "PERMISSION_DENIED",
        "PREPAY_DEPLETED",
        "BILLING_OR_PREREQUISITE",
        "QUOTA_EXHAUSTED",
    }
)


def _after_failure(code: str, message: str, streak: int) -> tuple[str | None, float]:
    """(stop reason or ``None``, seconds to wait before the next call) after one failure."""

    lowered = message.lower()
    if code in _FATAL_CODES:
        return code, 0.0
    if code == "rate_limited":
        if "per day" in lowered or streak >= 3:
            return "daily_quota" if "per day" in lowered else "rate_limited", 0.0
        return None, 60.0
    if code == "INVALID_REQUEST":
        if "recitation" in lowered:
            return None, 0.0  # this document's output was filtered; the next may pass
        return ("request_rejected" if streak >= 2 else None), 0.0
    if code in {"PROVIDER_UNAVAILABLE", "timeout"}:
        return ("provider_down" if streak >= 5 else None), 30.0
    return None, 0.0


def failure_entry(
    failure: ProviderFailure, info: Mapping[str, Any], pages: int, cap: int
) -> dict[str, Any]:
    """One failed call: code, pages, and the counts-only diagnostics (no document values)."""

    from scripts.vision_diagnostics import at_output_cap, looks_like_a_loop

    entry: dict[str, Any] = {"code": failure.code, "pages": pages, **info}
    usage = failure.usage
    if usage is not None:
        entry.setdefault("output_tokens", usage.output_tokens)
        entry.setdefault("thought_tokens", usage.thought_tokens)
        entry.setdefault("input_tokens", usage.input_tokens)
    if entry.get("status") not in {None, "completed"} or failure.code == "invalid_output":
        entry["max_output_tokens"] = cap
        entry["at_output_cap"] = at_output_cap(entry, cap)
        entry["loop_suspected"] = looks_like_a_loop(entry)
    return entry


def _fold(value: str) -> str:
    # Letters and digits only, zeros kept: a handwritten "04-077" must still be found
    # inside "PO/26-27/04-077" (the document-number canonical form drops the zeros).
    return re.sub(r"[^0-9A-Z]", "", value.upper()).replace("O", "0").replace("I", "1")


_MIN_PARTIAL_REF = 5
"""A partial reading counts only when the shorter side has at least this many characters."""


def _ref_hit(expected: Iterable[str], found: Iterable[str]) -> tuple[int, int]:
    wanted = [_fold(str(item)) for item in expected if str(item).strip()]
    seen = [_fold(str(item)) for item in found if str(item).strip()]

    def hit(item: str, other: str) -> bool:
        if not item or not other:
            return False
        if item == other:
            return True
        short = min(item, other, key=len)
        return len(short) >= _MIN_PARTIAL_REF and (item in other or other in item)

    hits = sum(any(hit(item, other) for other in seen) for item in wanted)
    return hits, len(wanted)


@dataclass
class VisionRun:
    mode: str
    model: str
    targets: list[VisionTarget]
    usage: Counter[str] = field(default_factory=Counter)
    provider: dict[str, int] = field(default_factory=dict)
    failures: list[dict[str, Any]] = field(default_factory=list)


def prepare(
    targets: list[VisionTarget],
    *,
    root: Path,
    pages_cache: Path,
    tool_versions: Mapping[str, str],
    languages: tuple[str, ...],
    refresh: bool,
    model: str,
    workers: int,
) -> None:
    fingerprint = code_fingerprint(root, tool_versions)

    def render(target: VisionTarget) -> None:
        if len(target.pages) > MAX_PAGES:
            target.outcome = "too_many_pages"
            return
        rows = load_or_render(target, pages_cache, fingerprint, languages, refresh=refresh)
        if rows is None:
            target.outcome = "render_failed"
            return
        target.rows = rows
        keyed = request_key(target, model)
        if keyed is None:
            target.outcome = "render_failed"
            return
        target.payload, target.key = keyed
        target.outcome = "missing"

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        list(pool.map(render, targets))


def check_answers(
    targets: Sequence[VisionTarget],
    answers: Path,
    buyer_entities: Sequence[BuyerEntityHint],
    usage: Counter[str],
) -> None:
    for target in targets:
        if target.key is None:
            continue
        path = _answer_path(answers, target.key)
        if not path.is_file():
            continue
        stored = json.loads(path.read_text(encoding="utf-8"))
        for name, value in (stored.get("usage") or {}).items():
            if isinstance(value, int):
                usage[name] += value
        recorded = stored.get("usage") or {}
        if isinstance(recorded.get("output_tokens"), int):
            target.output_tokens = recorded["output_tokens"] + int(
                recorded.get("thought_tokens") or 0
            )
        try:
            parsed = parse_output(HOOK, str(stored["output_text"]))
            assert isinstance(parsed, VisionOutput)
            target.validation = validate_vision(
                parsed,
                VisionContext(
                    doc_type=target.doc_type.value,  # type: ignore[arg-type]
                    pages={row.page: row.sent_page for row in target.rows},
                    buyer_entities=tuple(buyer_entities),
                    ocr=target.document,
                ),
            )
            target.outcome = "read"
        except InvalidOutput as error:
            target.outcome = error.code


def summary(
    run: VisionRun,
    truth_by_id: Mapping[str, Any],
    score_document: Callable[..., Any],
    extraction_summary: Callable[[list[Any]], dict[str, Any]],
    normal: Callable[[str, Any], Any],
    same: Callable[[str, Any, Any], bool],
    header_fields: Sequence[str],
) -> dict[str, Any]:
    """Counts only: what was read, vision alone per field, and OCR × vision agreement."""

    outcomes = Counter(target.outcome for target in run.targets)
    masked: Counter[str] = Counter()
    pages = 0
    for target in run.targets:
        for row in target.rows:
            pages += 1
            masked.update(row.mask.get("counts", {}))
    scores = []
    agreement: dict[str, Counter[str]] = defaultdict(Counter)
    dropped: Counter[str] = Counter()
    annotation = [0, 0]
    marketplace = [0, 0]
    handwritten: Counter[str] = Counter()
    kinds = Counter()
    line_cells: Counter[str] = Counter()
    for target in run.targets:
        if target.validation is not None:
            line_cells.update(_line_cells(target.validation.check.lines))
    for target in run.targets:
        if target.validation is None or target.truth_id is None:
            continue
        truth = truth_by_id[target.truth_id]
        vision_doc = target.validation.document
        dropped.update(target.validation.check.dropped)
        scores.append(score_document(truth, target.file_id, vision_doc))
        for name in header_fields:
            if name not in truth.header:
                continue
            expected = normal(name, truth.header[name])
            ocr = normal(name, getattr(target.document.header, name, None))
            seen = normal(name, getattr(vision_doc.header, name, None))
            if ocr is None and seen is None:
                outcome = "both_missing"
            elif ocr is None:
                outcome = "vision_only_" + (
                    "correct" if expected is not None and same(name, expected, seen) else "wrong"
                )
            elif seen is None:
                outcome = "ocr_only_" + (
                    "correct" if expected is not None and same(name, expected, ocr) else "wrong"
                )
            elif same(name, ocr, seen):
                right = expected is not None and same(name, expected, ocr)
                outcome = "agree_correct" if right else "agree_wrong"
            else:
                ocr_right = expected is not None and same(name, expected, ocr)
                vision_right = expected is not None and same(name, expected, seen)
                outcome = (
                    "disagree_ocr_right"
                    if ocr_right
                    else "disagree_vision_right"
                    if vision_right
                    else "disagree_both_wrong"
                )
            agreement[name][outcome] += 1
        extras = vision_doc.header.extras
        expected_refs = _truth_refs(truth, "annotation_refs")
        if expected_refs:
            hits, total = _ref_hit(expected_refs, extras.get("annotation_refs", []))
            annotation[0] += hits
            annotation[1] += total
            handwritten.update(_handwritten_outcomes(expected_refs, vision_doc))
        expected_orders = _truth_refs(truth, "marketplace_order_no")
        if expected_orders:
            found = extras.get("marketplace_order_no")
            hits, total = _ref_hit(expected_orders, [found] if found else [])
            marketplace[0] += hits
            marketplace[1] += total
        for item in extras.get("vision", {}).get("page_kinds", []):
            doc_type = _PAGE_KIND_TYPES.get(str(item.get("kind")))
            if doc_type is not None:
                kinds["correct" if doc_type == truth.doc_type.value else "wrong"] += 1
    totals: Counter[str] = Counter()
    for counts in agreement.values():
        totals.update(counts)
    return {
        "mode": run.mode,
        "model": run.model,
        "pdf_documents": len(run.targets),
        "pages": pages,
        "outcomes": dict(sorted(outcomes.items())),
        "masked_regions": dict(sorted(masked.items())),
        "usage": dict(sorted(run.usage.items())),
        "provider": dict(sorted(run.provider.items())),
        "alone": extraction_summary(scores) if scores else None,
        "agreement": {
            "all": dict(sorted(totals.items())),
            **{name: dict(sorted(counts.items())) for name, counts in sorted(agreement.items())},
        },
        "annotation_refs_found": f"{annotation[0]}/{annotation[1]}",
        "handwritten_refs": dict(sorted(handwritten.items())),
        "marketplace_order_found": f"{marketplace[0]}/{marketplace[1]}",
        "page_kinds": dict(sorted(kinds.items())),
        "line_cells": dict(sorted(line_cells.items())),
        "dropped": dict(sorted(dropped.items())),
        "output_tokens_per_call": _output_spread(run.targets),
        "failures": _failure_summary(run.failures),
    }


def _output_spread(targets: Sequence[VisionTarget]) -> dict[str, int]:
    """How close recorded answers came to the output cap (output + thought tokens)."""

    used = [target.output_tokens for target in targets if target.output_tokens is not None]
    if not used:
        return {}
    return {
        "answers": len(used),
        "max": max(used),
        "over_4096": sum(value > 4_096 for value in used),
        "over_8192": sum(value > 8_192 for value in used),
        "over_16384": sum(value > 16_384 for value in used),
    }


def _truth_refs(truth: Any, name: str) -> list[str]:
    """Handwritten refs / marketplace orders sit beside the header in truth v2."""

    value = getattr(truth, name, None) or truth.header.get(name)
    if not value:
        return []
    items = value if isinstance(value, (list, tuple)) else [value]
    return [str(item) for item in items if str(item).strip()]


def _handwritten_outcomes(expected: Sequence[str], document: Document) -> Counter[str]:
    """Where each handwritten truth ref turned up in the vision reading (counts only).

    ``typed``: an annotation the model called a PO/GRN/invoice number (what the
    review shows as a reference); ``annotation_other``: an annotation of another
    kind; ``printed_field``: only in a printed reference field (rule 7 broken);
    ``missed``: nowhere.
    """

    extras = document.header.extras
    typed = [str(item) for item in extras.get("annotation_refs", [])]
    notes = [str(item.get("text", "")) for item in extras.get("vision", {}).get("annotations", [])]
    printed: list[str] = []
    for name in ("po_refs", "grn_refs"):
        item = getattr(document.header, name, None)
        if item is not None and item.value:
            printed.extend(str(value) for value in item.value)
    for values in extras.get("vision", {}).get("other_fields", {}).values():
        printed.extend(str(value) for value in values)
    outcomes: Counter[str] = Counter()
    for ref in expected:
        if _ref_hit([ref], typed)[0]:
            outcomes["typed"] += 1
        elif _ref_hit([ref], notes)[0]:
            outcomes["annotation_other"] += 1
        elif _ref_hit([ref], printed)[0]:
            outcomes["printed_field"] += 1
        else:
            outcomes["missed"] += 1
    outcomes["expected"] += len(expected)
    return outcomes


def _line_cells(lines: Iterable[Any]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for line in lines:
        counts["rows"] += 1
        names = {cell.name for cell in line.cells}
        counts["with_description"] += "description" in names
        counts["with_amount"] += "amount" in names
        counts["with_quantity"] += bool(names & {"quantity", "received_qty", "accepted_qty"})
        counts["with_rate"] += "rate" in names
    return counts


def _failure_summary(failures: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Failed calls grouped by what went wrong; counts and Google's error text only."""

    grouped: Counter[str] = Counter()
    for entry in failures:
        grouped[json.dumps(dict(entry), sort_keys=True, default=str)] += 1
    return [{**json.loads(key), "calls": count} for key, count in sorted(grouped.items())]


def provider_key() -> str:
    key = os.environ.get("BENCH_GEMINI_KEY", "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.\-]{20,256}", key):
        raise SystemExit("--vision provider needs BENCH_GEMINI_KEY (the Gemini API key)")
    return key


__all__ = [
    "MAX_PAGES",
    "VisionRun",
    "VisionTarget",
    "check_answers",
    "document_pages",
    "prepare",
    "provider_key",
    "send",
    "summary",
]
