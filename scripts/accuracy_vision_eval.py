"""``vision_eval`` report block (LLM plan WI-EV1): one vision provider, counts only.

Scores the bench's vision run (one arm: Gemini **or** the company vLLM server,
plan §0.6) against the truth set, next to the rules reader (Tesseract + rules)
on the same documents. Two arms are compared by putting their reports side by
side; their answers are never combined, so there is no LLM×LLM metric here.

Agreement policies decide which LLM values would be auto-accept candidates:

- ``single``: the LLM value alone, when it passed every standalone check;
- ``llm_x_ocr``: that value only when the rules reader read the same value.

Every number is a count, a ratio of counts or a statistic over counts (Wilson
lower bound, latency percentiles, a cost from ``RB_AI_PRICE_FILE``). No document
value, file id or text is written.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from reconbench_app.ai.limits import cost_usd, load_prices
from reconbench_core.extraction.consensus import HEADER_FIELD_TYPES
from reconbench_core.extraction.vision import VISION_HEADER_FIELDS
from reconbench_core.schema import DocType, Document

from scripts.accuracy_vision import VisionRun, VisionTarget

POLICIES = ("single", "llm_x_ocr")
LINE_CELLS = ("amount", "quantity", "rate", "description", "hsn_sac", "uom")
_CENT = Decimal("0.01")
_QTY = ("quantity", "received_qty", "accepted_qty")
_REF_NAMES = {"po_ref": "po_refs", "grn_ref": "grn_refs"}
_Z95 = 1.959963984540054

Normal = Callable[[str, Any], Any]
Same = Callable[[str, Any, Any], bool]


def wilson_lower(successes: int, total: int, z: float = _Z95) -> float | None:
    """Wilson score 95% lower bound of ``successes / total`` (``None`` when ``total`` is 0)."""

    if total <= 0:
        return None
    p = successes / total
    denominator = 1 + z * z / total
    centre = p + z * z / (2 * total)
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total))
    return round((centre - margin) / denominator, 4)


def percentile(values: Sequence[int], share: float) -> int | None:
    """Nearest-rank percentile (``share`` in 0..1) or ``None`` for no values."""

    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(share * len(ordered)))
    return ordered[rank - 1]


def _fold(value: str) -> str:
    return re.sub(r"[^0-9A-Z]", "", value.upper()).replace("O", "0").replace("I", "1")


def _decimal(value: Any) -> Decimal | None:
    value = getattr(value, "value", value)
    if value is None:
        return None
    try:
        return Decimal(str(value)).quantize(_CENT)
    except InvalidOperation:
        return None


def _text(value: Any) -> str | None:
    value = getattr(value, "value", value)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _header_name(name: str) -> str | None:
    """The document header field a vision answer item name maps to."""

    return VISION_HEADER_FIELDS.get(name) or _REF_NAMES.get(name)


def _field_type(name: str) -> str:
    kind = HEADER_FIELD_TYPES.get(name)
    return kind.value if kind is not None else name


class _Ratio:
    __slots__ = ("correct", "missing", "total", "wrong")

    def __init__(self) -> None:
        self.correct = self.wrong = self.missing = self.total = 0

    def add(self, outcome: str) -> None:
        self.total += 1
        if outcome == "correct":
            self.correct += 1
        elif outcome == "missing":
            self.missing += 1
        else:
            self.wrong += 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "correct": self.correct,
            "wrong": self.wrong,
            "missing": self.missing,
            "total": self.total,
            "accuracy": f"{self.correct}/{self.total}",
        }


def _outcome(name: str, expected: Any, actual: Any, same: Same) -> str | None:
    if expected is None and actual is None:
        return None
    if expected is None:
        return "unexpected"
    if actual is None:
        return "missing"
    return "correct" if same(name, expected, actual) else "wrong"


# --------------------------------------------------------------------------- lines


def _line_values(document: Document) -> list[dict[str, Any]]:
    rows = []
    for line in document.lines:
        quantity = next(
            (getattr(line, name) for name in _QTY if getattr(line, name) is not None), None
        )
        rows.append(
            {
                "amount": _decimal(line.amount),
                "quantity": _decimal(quantity),
                "rate": _decimal(line.rate),
                "description": _text(line.description),
                "hsn_sac": _text(line.hsn_sac),
                "uom": _text(line.uom),
            }
        )
    return rows


def _cell_same(name: str, expected: Any, actual: Any) -> bool:
    if name == "description":
        ratio = SequenceMatcher(
            None, " ".join(str(expected).upper().split()), " ".join(str(actual).upper().split())
        ).ratio()
        return ratio >= 0.85
    if name in {"hsn_sac", "uom"}:
        return _fold(str(expected)) == _fold(str(actual))
    return bool(expected == actual)


def line_cells(truth_lines: Iterable[Mapping[str, Any]], document: Document) -> dict[str, _Ratio]:
    """Per cell type: truth cells read correctly, wrongly or not at all (rows aligned as the
    bench aligns them: by amount, else by quantity)."""

    got = _line_values(document)
    used: set[int] = set()
    cells: dict[str, _Ratio] = defaultdict(_Ratio)
    for item in truth_lines:
        expected = {
            "amount": _decimal(item.get("amount")),
            "quantity": _decimal(item.get("quantity")),
            "rate": _decimal(item.get("rate")),
            "description": _text(item.get("description")),
            "hsn_sac": _text(item.get("hsn_sac") or item.get("hsn")),
            "uom": _text(item.get("uom")),
        }
        match = None
        for index, row in enumerate(got):
            if index in used:
                continue
            if expected["amount"] is not None and row["amount"] != expected["amount"]:
                continue
            if expected["amount"] is None and (
                expected["quantity"] is None or row["quantity"] != expected["quantity"]
            ):
                continue
            match = index
            break
        if match is not None:
            used.add(match)
        for name in LINE_CELLS:
            if expected[name] is None:
                continue
            actual = got[match][name] if match is not None else None
            if actual is None:
                cells[name].add("missing")
            else:
                cells[name].add("correct" if _cell_same(name, expected[name], actual) else "wrong")
    return cells


# --------------------------------------------------------------------------- per document


def _passed_fields(target: VisionTarget) -> set[str]:
    """Header fields whose LLM value passed every standalone check."""

    assert target.validation is not None
    passed: set[str] = set()
    for item in target.validation.check.fields:
        name = _header_name(item.name)
        if name is not None and not item.check_failed:
            passed.add(name)
    return passed


def _unreadable_fields(target: VisionTarget) -> set[str]:
    assert target.validation is not None
    names = {_header_name(item.name) for item in target.validation.check.unreadable}
    return {name for name in names if name is not None}


def _accepted(policy: str, name: str, passed: set[str], llm: Any, ocr: Any, same: Same) -> bool:
    if llm is None or name not in passed:
        return False
    if policy == "single":
        return True
    return ocr is not None and same(name, ocr, llm)


def _simulated(target: VisionTarget, accepted: Iterable[str], *, clears_review: bool) -> Document:
    """The rules document with the policy's accepted LLM header values put in.

    The review flag is cleared only when ``clears_review`` (the policy accepted
    every critical field the truth has): that is the case in which auto-accepted
    values would let a document through unreviewed. Otherwise the rules flag stays.
    """

    assert target.validation is not None
    vision_header = target.validation.document.header
    updates = {name: getattr(vision_header, name) for name in accepted}
    if not updates:
        return target.document
    header = target.document.header.model_copy(update=updates)
    changes: dict[str, Any] = {"header": header}
    if clears_review:
        changes["needs_verification"] = False
    return target.document.model_copy(update=changes)


def _repeat_values(
    validations: Sequence[Any], header_fields: Sequence[str], normal: Normal
) -> tuple[int, int]:
    """(fields compared, fields whose normalised value changed) across the repeats."""

    compared = changed = 0
    for name in header_fields:
        values = [
            normal(name, getattr(item.document.header, name, None)) if item is not None else None
            for item in validations
        ]
        if all(value is None for value in values):
            continue
        compared += 1
        first = values[0]
        changed += any(value != first for value in values[1:])
    rows = [len(item.document.lines) if item is not None else 0 for item in validations]
    if any(rows):
        compared += 1
        changed += len(set(rows)) > 1
    return compared, changed


# --------------------------------------------------------------------------- block


def evaluate(
    run: VisionRun,
    truth_by_id: Mapping[str, Any],
    *,
    normal: Normal,
    same: Same,
    header_fields: Sequence[str],
    critical: Mapping[DocType, Sequence[str]],
    score_document: Callable[..., Any],
    policy: str = "single",
    price_file: Path | None = None,
) -> dict[str, Any]:
    """The ``vision_eval`` block for one arm (counts only)."""

    if policy not in POLICIES:
        raise ValueError(f"agreement policy must be one of {', '.join(POLICIES)}")
    groups: dict[str, dict[str, dict[str, _Ratio]]] = {}

    def bucket(group: str, reader: str, name: str) -> _Ratio:
        return groups.setdefault(group, {}).setdefault(reader, {}).setdefault(name, _Ratio())

    auto: dict[str, Counter[str]] = defaultdict(Counter)
    unreadable: Counter[str] = Counter()
    answered: Counter[str] = Counter()
    not_in_text: Counter[str] = Counter()
    agreement: Counter[str] = Counter()
    false_safe: Counter[str] = Counter()
    repeat: Counter[str] = Counter()
    for target in run.targets:
        if target.validation is None or target.truth_id is None:
            continue
        truth = truth_by_id[target.truth_id]
        vision_doc = target.validation.document
        subsets = ["all_pdf", f"scan_type:{target.scan_type}"]
        if target.ocr_pages:
            subsets.append("scan_page")
        passed = _passed_fields(target)
        listed = _unreadable_fields(target)
        critical_names = set(critical.get(truth.doc_type, ()))
        accepted_names: list[str] = []
        ocr_text = _fold(target.ocr_text)
        for name in header_fields:
            if name not in truth.header:
                continue
            expected = normal(name, truth.header[name])
            llm = normal(name, getattr(vision_doc.header, name, None))
            ocr = normal(name, getattr(target.document.header, name, None))
            field_type = _field_type(name)
            for reader, value in (("llm", llm), ("ocr", ocr)):
                outcome = _outcome(name, expected, value, same)
                if outcome is None:
                    continue
                for subset in subsets:
                    bucket(subset, reader, "header").add(outcome)
                    bucket(subset, reader, f"header:{field_type}").add(outcome)
            llm_right = llm is not None and expected is not None and same(name, expected, llm)
            if llm is None:
                if expected is not None:
                    unreadable["null_answers"] += 1
                    unreadable["listed_unreadable" if name in listed else "silently_omitted"] += 1
            else:
                for scope in ("all", "critical") if name in critical_names else ("all",):
                    answered[f"{scope}_answered"] += 1
                    answered[f"{scope}_wrong"] += int(not llm_right)
                vision_item = next(
                    (
                        item
                        for item in target.validation.check.fields
                        if _header_name(item.name) == name
                    ),
                    None,
                )
                if vision_item is not None and ocr_text:
                    not_in_text["answered"] += 1
                    not_in_text["not_in_ocr_text"] += int(_fold(vision_item.text) not in ocr_text)
            if llm is not None and ocr is not None:
                if same(name, ocr, llm):
                    agreement["agree_correct" if llm_right else "agree_wrong"] += 1
                else:
                    agreement["disagree"] += 1
            counts = auto[field_type]
            if expected is not None:
                counts["truth_fields"] += 1
            if _accepted(policy, name, passed, llm, ocr, same):
                accepted_names.append(name)
                counts["accepted"] += 1
                counts["accepted_correct"] += int(llm_right)
        if truth.lines is not None:
            for reader, document in (("llm", vision_doc), ("ocr", target.document)):
                for name, ratio in line_cells(truth.lines, document).items():
                    for subset in subsets:
                        merged = bucket(subset, reader, f"line:{name}")
                        merged.correct += ratio.correct
                        merged.wrong += ratio.wrong
                        merged.missing += ratio.missing
                        merged.total += ratio.total
        rules = score_document(truth, target.file_id, target.document)
        wanted = {name for name in critical_names if name in truth.header}
        all_critical = bool(wanted) and wanted <= set(accepted_names)
        false_safe["documents"] += 1
        false_safe["rules"] += int(rules.false_safe)
        false_safe["review_cleared_documents"] += int(all_critical)
        for label, clears in (("flags_kept", False), ("review_cleared", all_critical)):
            simulated = _simulated(target, accepted_names, clears_review=clears)
            false_safe[f"simulated_{label}"] += int(
                score_document(truth, target.file_id, simulated).false_safe
            )
        if target.repeat_keys:
            compared, changed = _repeat_values(
                [target.validation, *target.repeat_validations], header_fields, normal
            )
            repeat["documents"] += 1
            repeat["fields_compared"] += compared
            repeat["fields_changed"] += changed

    auto_block: dict[str, Any] = {}
    for field_type, counts in sorted(auto.items()):
        accepted, correct = counts["accepted"], counts["accepted_correct"]
        total = counts["truth_fields"]
        auto_block[field_type] = {
            "accepted": accepted,
            "accepted_correct": correct,
            "truth_fields": total,
            "precision": f"{correct}/{accepted}",
            "coverage": f"{accepted}/{total}",
            "wilson95_lower": wilson_lower(correct, accepted),
        }
    latencies = [value for target in run.targets for value in target.latencies_ms]
    return {
        "arm": run.arm.describe(),
        "model": run.model,
        "agreement_policy": policy,
        "documents_scored": false_safe["documents"],
        "accuracy": {
            group: {
                reader: {name: ratio.as_dict() for name, ratio in sorted(fields.items())}
                for reader, fields in sorted(readers.items())
            }
            for group, readers in sorted(groups.items())
        },
        "auto_accept": auto_block,
        "false_safe": {
            "method": "rules document + policy-accepted LLM header values (lines stay the "
            "rules'); flags_kept keeps the rules review flag, review_cleared clears it when "
            "every critical truth field was accepted",
            **dict(sorted(false_safe.items())),
        },
        "unreadable_to_null": dict(sorted(unreadable.items())),
        "wrong_when_answered": dict(sorted(answered.items())),
        "not_in_ocr_text": dict(sorted(not_in_text.items())),
        "agree_wrong_llm_x_ocr": dict(sorted(agreement.items())),
        "tokens": _tokens(run),
        "cost": _cost(run, price_file),
        "latency_ms": {
            "answers": len(latencies),
            "p50": percentile(latencies, 0.50),
            "p95": percentile(latencies, 0.95),
        },
        "failures_by_code": _failures(run),
        "invalid_answer_rules": _parse_errors(run),
        "repeat_variance": {
            **dict(sorted(repeat.items())),
            "share_changed": f"{repeat['fields_changed']}/{repeat['fields_compared']}",
        },
    }


def _tokens(run: VisionRun) -> dict[str, int]:
    total: Counter[str] = Counter()
    total.update(run.usage)
    total.update(run.repeat_usage)
    return {
        "input": int(total["input_tokens"]),
        "output": int(total["output_tokens"]),
        "thought": int(total["thought_tokens"]),
    }


def _cost(run: VisionRun, price_file: Path | None) -> dict[str, Any]:
    """USD only from the owner's ``RB_AI_PRICE_FILE``; nothing is assumed without it."""

    prices = load_prices(price_file)
    price = prices.models.get(run.model) if prices is not None else None
    if prices is None or price is None:
        return {"usd": None, "price_version": None}
    tokens = _tokens(run)
    usd = cost_usd(
        price, input_tokens=tokens["input"], output_tokens=tokens["output"] + tokens["thought"]
    )
    return {"usd": str(usd.quantize(Decimal("0.0001"))), "price_version": prices.price_version}


def _parse_errors(run: VisionRun) -> dict[str, int]:
    """Schema rules broken by unusable per-page answers (pattern → pages), no values."""

    return dict(sorted(Counter(item for t in run.targets for item in t.parse_errors).items()))


def _failures(run: VisionRun) -> dict[str, int]:
    codes: Counter[str] = Counter()
    for entry in run.failures:
        codes[f"call:{entry.get('code')}"] += 1
    for target in run.targets:
        for code in target.answer_codes:
            if code != "read":
                codes[f"answer:{code}"] += 1
        if target.outcome in {"render_failed", "too_many_pages", "missing"}:
            codes[f"document:{target.outcome}"] += 1
    return dict(sorted(codes.items()))


__all__ = ["LINE_CELLS", "POLICIES", "evaluate", "line_cells", "percentile", "wilson_lower"]
