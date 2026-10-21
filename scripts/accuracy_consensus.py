"""Counts-only AC-6a consensus scoring for the accuracy bench."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from reconbench_core.extraction.consensus import (
    ConsensusPolicy,
    CropCheck,
    FieldKey,
    FieldType,
    ReaderKind,
    TrustLevel,
    decide,
)
from reconbench_core.parsers.types import BuyerEntityHint, ParserHints
from reconbench_core.schema import Document

from scripts import accuracy_vision


def shadow_policy(*, run_date: date, require_crop: bool) -> ConsensusPolicy:
    return ConsensusPolicy(
        auto_verify=True,
        reopen=True,
        field_types=frozenset(FieldType),
        require_crop=require_crop,
        as_of=run_date,
    )


def rules_source_for(target: accuracy_vision.VisionTarget) -> ReaderKind:
    source = target.document.header.extras.get("source")
    if source in {ReaderKind.OCR.value, ReaderKind.TEXT_LAYER.value}:
        return ReaderKind(source)
    return ReaderKind.OCR if target.ocr_pages else ReaderKind.TEXT_LAYER


def _plain(document: Document, key: FieldKey) -> Any:
    if key.scope == "header":
        item = getattr(document.header, key.name, None)
    else:
        line = next((line for line in document.lines if line.line_no == key.line_no), None)
        item = getattr(line, key.name, None) if line is not None else None
    return item.value if item is not None else None


def _normal_lines(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None


def summary(
    run: accuracy_vision.VisionRun,
    truth_by_id: Mapping[str, Any],
    score_document: Callable[..., Any],
    extraction_summary: Callable[[list[Any]], dict[str, Any]],
    normal: Callable[[str, Any], Any],
    same: Callable[[str, Any, Any], bool],
    *,
    buyer_entities: Sequence[BuyerEntityHint],
    run_date: date,
    require_crop: bool,
    crops: Mapping[tuple[str, int], Mapping[FieldKey, CropCheck]] | None = None,
) -> dict[str, Any]:
    policy = shadow_policy(run_date=run_date, require_crop=require_crop)
    levels: Counter[str] = Counter()
    rules: Counter[str] = Counter()
    field_counts: dict[str, Counter[str]] = {item.value: Counter() for item in FieldType}
    reopened: Counter[str] = Counter()
    shadow_scores: list[Any] = []
    false_safe_by_type: Counter[str] = Counter()
    documents = 0
    vision_only_lines = 0

    for target in run.targets:
        if target.validation is None:
            continue
        hints = ParserHints(doc_type=target.doc_type, buyer_entities=tuple(buyer_entities))
        result = decide(
            target.document,
            vision=target.validation.check,
            crops=(crops or {}).get((target.file_id, target.index)),
            hints=hints,
            policy=policy,
            rules_source=rules_source_for(target),
        )
        documents += 1
        vision_only_lines += result.vision_only_lines
        for decision in result.decisions:
            levels[decision.level.value] += 1
            rules[decision.rule.value] += 1
            if decision.field_type is not None:
                count = field_counts[decision.field_type.value]
                count[decision.level.value] += 1
                if decision.rule.value == "excluded":
                    count["excluded"] += 1
                if decision.level is TrustLevel.L1 and decision.rule.value == "d42":
                    count["l1"] += 1
                    truth = truth_by_id.get(target.truth_id or "")
                    if truth is None:
                        count["l1_unscored"] += 1
                    elif decision.key.scope == "header":
                        if decision.key.name not in truth.header:
                            count["l1_unscored"] += 1
                        else:
                            expected = normal(decision.key.name, truth.header[decision.key.name])
                            actual = normal(
                                decision.key.name,
                                getattr(target.document.header, decision.key.name, None),
                            )
                            if (
                                expected is not None
                                and actual is not None
                                and same(decision.key.name, expected, actual)
                            ):
                                count["l1_correct"] += 1
                            else:
                                count["l1_wrong"] += 1
                    elif truth.lines is None:
                        count["l1_unscored"] += 1
                    else:
                        actual_value = _normal_lines(_plain(target.document, decision.key))
                        expected_values = [
                            _normal_lines(row.get(decision.key.name))
                            for row in truth.lines
                            if row.get(decision.key.name) is not None
                        ]
                        if actual_value is not None and actual_value in expected_values:
                            count["l1_correct"] += 1
                        else:
                            count["l1_wrong"] += 1
            if decision.reopened:
                reopened["total"] += 1
                truth = truth_by_id.get(target.truth_id or "")
                if truth is None:
                    reopened["unscored"] += 1
                elif decision.key.scope == "header" and decision.key.name in truth.header:
                    expected = normal(decision.key.name, truth.header[decision.key.name])
                    actual = normal(
                        decision.key.name,
                        getattr(target.document.header, decision.key.name, None),
                    )
                    reopened[
                        "ocr_wrong"
                        if expected is None or not same(decision.key.name, expected, actual)
                        else "ocr_right"
                    ] += 1
                elif decision.key.scope == "line" and truth.lines is not None:
                    actual_value = _normal_lines(_plain(target.document, decision.key))
                    expected_values = [
                        _normal_lines(row.get(decision.key.name))
                        for row in truth.lines
                        if row.get(decision.key.name) is not None
                    ]
                    reopened[
                        "ocr_wrong" if actual_value not in expected_values else "ocr_right"
                    ] += 1
                else:
                    reopened["unscored"] += 1

        truth = truth_by_id.get(target.truth_id or "")
        if truth is not None:
            score = score_document(truth, target.file_id, result.document)
            shadow_scores.append(score)
            if score.false_safe:
                for decision in result.decisions:
                    if decision.auto_verified and decision.field_type is not None:
                        false_safe_by_type[decision.field_type.value] += 1

    by_field_type: dict[str, dict[str, Any]] = {}
    for field_type in FieldType:
        counts = field_counts[field_type.value]
        scored_l1 = counts["l1_correct"] + counts["l1_wrong"]
        false_safe = false_safe_by_type[field_type.value]
        gate = (
            "no_evidence"
            if scored_l1 == 0
            else "fail"
            if counts["l1_wrong"] or false_safe
            else "pass"
        )
        by_field_type[field_type.value] = {
            "l1": counts["l1"],
            "l1_correct": counts["l1_correct"],
            "l1_wrong": counts["l1_wrong"],
            "l1_unscored": counts["l1_unscored"],
            "l2": counts[TrustLevel.L2.value],
            "l3": counts[TrustLevel.L3.value],
            "rejected": counts[TrustLevel.REJECTED.value],
            "excluded": counts["excluded"],
            "shadow_false_safe": false_safe,
            "gate": gate,
        }
    return {
        "mode": "shadow",
        "require_crop": require_crop,
        "documents": documents,
        "levels": {key: levels[key] for key in sorted(levels)},
        "rules": {key: rules[key] for key in sorted(rules)},
        "by_field_type": by_field_type,
        "reopened": {key: reopened[key] for key in ("total", "ocr_wrong", "ocr_right", "unscored")},
        "vision_only_lines": vision_only_lines,
        "shadow": extraction_summary(shadow_scores) if shadow_scores else None,
        "shadow_false_safe": sum(score.false_safe for score in shadow_scores),
        "gate": {key: value["gate"] for key, value in by_field_type.items()},
    }


__all__ = ["rules_source_for", "shadow_policy", "summary"]
