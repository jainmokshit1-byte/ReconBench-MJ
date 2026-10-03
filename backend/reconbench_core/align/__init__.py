"""Deterministic invoice-to-PO line alignment."""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal

from pydantic import Field, model_validator
from rapidfuzz import fuzz
from scipy.optimize import linear_sum_assignment  # type: ignore[import-untyped]

from reconbench_core.normalize import NormalizedItem, normalize_item_text, normalize_uom
from reconbench_core.schema import (
    AlignmentState,
    DecimalValue,
    DocLine,
    FieldValue,
    FrozenModel,
    LineType,
    Probability,
)

_ONE = Decimal("1")
_FOUR_PLACES = Decimal("0.0001")
_MAX_TIE_BREAK = 999
_COLORS = {
    "black",
    "blue",
    "brown",
    "gold",
    "green",
    "grey",
    "gray",
    "orange",
    "pink",
    "purple",
    "red",
    "silver",
    "white",
    "yellow",
}


class AlignmentWeights(FrozenModel):
    """Weights read from ``config.engine.weights.align``."""

    desc: DecimalValue = Field(ge=Decimal("0"))
    code: DecimalValue = Field(ge=Decimal("0"))
    hsn: DecimalValue = Field(ge=Decimal("0"))
    uom: DecimalValue = Field(ge=Decimal("0"))

    @model_validator(mode="after")
    def _weights_sum_to_one(self) -> AlignmentWeights:
        if self.desc + self.code + self.hsn + self.uom != _ONE:
            raise ValueError("item-alignment weights must sum to 1")
        return self


class AlignmentConfig(FrozenModel):
    """Alignment cutoffs and aliases copied from the active engine config."""

    weights: AlignmentWeights
    item_aligned: Probability
    item_review: Probability
    item_single_line_explicit: Probability
    spec_conflict_cap: Probability
    uom_aliases: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_threshold_order(self) -> AlignmentConfig:
        if not self.item_aligned > self.item_review > self.item_single_line_explicit:
            raise ValueError("alignment thresholds must descend aligned > review > explicit")
        if self.spec_conflict_cap > self.item_aligned:
            raise ValueError("spec-conflict cap must not exceed the aligned threshold")
        return self


class AlignmentResult(FrozenModel):
    """One invoice line's best accepted or reviewable PO-line candidate."""

    invoice_line_no: int = Field(ge=1)
    po_line_no: int | None = Field(default=None, ge=1)
    score: Probability | None = None
    state: AlignmentState


def _field_value[T](field: FieldValue[T] | None) -> T | None:
    return None if field is None else field.value


def _line_key(line: DocLine) -> tuple[int, str, str, str]:
    description = _field_value(line.description) or ""
    item_code = _field_value(line.item_code) or ""
    return (
        line.line_no,
        item_code.casefold(),
        description.casefold(),
        line.line_type.value.value if line.line_type else "goods",
    )


def _description_score(left: NormalizedItem, right: NormalizedItem) -> Decimal | None:
    if not left.tokens or not right.tokens:
        return None
    token_set = Decimal(str(fuzz.token_set_ratio(left.description, right.description)))
    token_sort = Decimal(str(fuzz.token_sort_ratio(left.description, right.description)))
    return ((token_set + token_sort) / Decimal("200")).quantize(
        _FOUR_PLACES, rounding=ROUND_HALF_UP
    )


def _spec_category(token: str) -> str | None:
    if re.fullmatch(r"[a-z]{1,6}\d{2,}[a-z0-9]*(?:[-/][a-z0-9]+)*", token):
        return "model"
    if re.fullmatch(r"\d+(?:\.\d+)?(?:gb|tb)", token):
        return "capacity"
    if re.fullmatch(r"\d+(?:\.\d+)?inch", token):
        return "screen_size"
    if re.fullmatch(r"\d+(?:\.\d+)?kw", token):
        return "power"
    if re.fullmatch(r"\d+(?:\.\d+)?x\d+(?:\.\d+)?(?:x\d+(?:\.\d+)?)?(?:mm|cm|m)?", token):
        return "dimensions"
    if token in _COLORS:
        return "colour"
    return None


def spec_tokens_conflict(left: NormalizedItem, right: NormalizedItem) -> bool:
    """Return true only when both items state different values for the same spec."""

    left_by_category: dict[str, set[str]] = {}
    right_by_category: dict[str, set[str]] = {}
    for token in left.spec_tokens:
        category = _spec_category(token)
        if category is not None:
            left_by_category.setdefault(category, set()).add(token)
    for token in right.spec_tokens:
        category = _spec_category(token)
        if category is not None:
            right_by_category.setdefault(category, set()).add(token)
    for category in left_by_category.keys() & right_by_category.keys():
        if left_by_category[category] != right_by_category[category]:
            return True
    return False


def _normalized_code(line: DocLine) -> str | None:
    value = _field_value(line.item_code)
    return re.sub(r"\s+", "", value).casefold() if value else None


def _normalized_hsn(line: DocLine) -> str | None:
    value = _field_value(line.hsn_sac)
    digits_only = re.sub(r"\D", "", value or "")
    return digits_only or None


def _normalized_uom(line: DocLine, aliases: dict[str, str]) -> tuple[str | None, bool]:
    value = _field_value(line.uom)
    if not value:
        return None, False
    result = normalize_uom(value, aliases)
    if result.canonical is not None:
        return result.canonical, True
    raw = re.sub(r"[^A-Z0-9]", "", value.upper())
    return (raw or None), False


def _line_similarity(left: DocLine, right: DocLine, config: AlignmentConfig) -> Decimal:
    left_description = normalize_item_text(_field_value(left.description) or "")
    right_description = normalize_item_text(_field_value(right.description) or "")
    description = _description_score(left_description, right_description)
    if description is not None and spec_tokens_conflict(left_description, right_description):
        description = min(description, config.spec_conflict_cap)

    components: dict[str, Decimal | None] = {
        "desc": description,
        "code": None,
        "hsn": None,
        "uom": None,
    }

    left_code, right_code = _normalized_code(left), _normalized_code(right)
    if left_code is not None and right_code is not None:
        components["code"] = _ONE if left_code == right_code else Decimal("0")

    left_hsn, right_hsn = _normalized_hsn(left), _normalized_hsn(right)
    if left_hsn is not None and right_hsn is not None:
        if left_hsn == right_hsn:
            components["hsn"] = _ONE
        elif len(left_hsn) >= 4 and len(right_hsn) >= 4 and left_hsn[:4] == right_hsn[:4]:
            components["hsn"] = Decimal("0.7")
        else:
            components["hsn"] = Decimal("0")

    aliases = config.uom_aliases
    left_uom, _ = _normalized_uom(left, aliases)
    right_uom, _ = _normalized_uom(right, aliases)
    if left_uom is not None and right_uom is not None:
        components["uom"] = _ONE if left_uom == right_uom else Decimal("0")

    active = [
        (getattr(config.weights, name), score)
        for name, score in components.items()
        if score is not None
    ]
    total_weight = sum((weight for weight, _ in active), Decimal("0"))
    if total_weight == 0:
        return Decimal("0.0000")
    weighted = sum((weight * score for weight, score in active), Decimal("0")) / total_weight
    return weighted.quantize(_FOUR_PLACES, rounding=ROUND_HALF_UP)


def _relative_proximity(left: Decimal | None, right: Decimal | None) -> Decimal | None:
    if left is None or right is None:
        return None
    denominator = max(abs(left), abs(right), _ONE)
    difference = min(abs(left - right) / denominator, _ONE)
    return _ONE - difference


def _tie_break_cost(
    invoice_line: DocLine,
    po_line: DocLine,
    po_rank: int,
    po_count: int,
) -> int:
    """Use quantity/rate only after score; then favor the lower stable PO line key."""

    proximities = [
        _relative_proximity(_field_value(invoice_line.quantity), _field_value(po_line.quantity)),
        _relative_proximity(_field_value(invoice_line.rate), _field_value(po_line.rate)),
    ]
    available = [value for value in proximities if value is not None]
    proximity = sum(available, Decimal("0")) / len(available) if available else Decimal("0")
    stable_rank = min(po_rank, _MAX_TIE_BREAK)
    proximity_range = max(0, _MAX_TIE_BREAK - min(po_count - 1, _MAX_TIE_BREAK))
    proximity_cost = int(
        ((_ONE - proximity) * proximity_range).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )
    return min(_MAX_TIE_BREAK, proximity_cost + stable_rank)


def _score_units(score: Decimal) -> int:
    return int((score * Decimal("10000")).to_integral_value(rounding=ROUND_HALF_UP))


def _eligible_floor(config: AlignmentConfig, explicit_single_line: bool) -> Decimal:
    return config.item_single_line_explicit if explicit_single_line else config.item_review


def _alignment_state(
    score: Decimal, config: AlignmentConfig, explicit_single_line: bool
) -> AlignmentState:
    if score >= config.item_aligned:
        return AlignmentState.ALIGNED
    if score >= config.item_review:
        return AlignmentState.UNSURE
    if explicit_single_line and score >= config.item_single_line_explicit:
        return AlignmentState.UNSURE
    return AlignmentState.NOT_ON_PO


def align_lines(
    invoice_lines: tuple[DocLine, ...] | list[DocLine],
    po_lines: tuple[DocLine, ...] | list[DocLine],
    config: AlignmentConfig,
    *,
    explicit_link: bool = False,
) -> tuple[AlignmentResult, ...]:
    """Assign invoice lines to PO lines by similarity, independent of input order.

    The Hungarian solve first enforces one-to-one assignments. Remaining invoice
    lines may then reuse their best qualifying PO line, allowing split billing.
    Missing optional features are excluded and the remaining configured weights
    are renormalized; quantity and rate affect only equal-score tie-breaking.
    """

    sorted_invoices = sorted(invoice_lines, key=_line_key)
    sorted_pos = sorted(po_lines, key=_line_key)
    explicit_single_line = explicit_link and len(sorted_invoices) == len(sorted_pos) == 1

    result_by_line: dict[tuple[int, str, str, str], AlignmentResult] = {}
    invoice_goods = [
        line for line in sorted_invoices if _field_value(line.line_type) is not LineType.CHARGE
    ]
    for line in sorted_invoices:
        key = _line_key(line)
        if _field_value(line.line_type) is LineType.CHARGE:
            result_by_line[key] = AlignmentResult(
                invoice_line_no=line.line_no,
                po_line_no=None,
                score=None,
                state=AlignmentState.CHARGE,
            )

    if not invoice_goods:
        return tuple(result_by_line[_line_key(line)] for line in sorted_invoices)
    if not sorted_pos:
        for line in invoice_goods:
            result_by_line[_line_key(line)] = AlignmentResult(
                invoice_line_no=line.line_no,
                po_line_no=None,
                score=None,
                state=AlignmentState.UNLINKED,
            )
        return tuple(result_by_line[_line_key(line)] for line in sorted_invoices)

    scores = [
        [_line_similarity(invoice_line, po_line, config) for po_line in sorted_pos]
        for invoice_line in invoice_goods
    ]
    costs = [
        [
            -_score_units(score) * 1000
            + _tie_break_cost(invoice_line, po_line, po_index, len(sorted_pos))
            for po_index, (po_line, score) in enumerate(zip(sorted_pos, row, strict=True))
        ]
        for invoice_line, row in zip(invoice_goods, scores, strict=True)
    ]
    row_indexes, column_indexes = linear_sum_assignment(costs)
    assigned = {
        int(row_index): int(column_index)
        for row_index, column_index in zip(row_indexes, column_indexes, strict=True)
    }

    for row_index, invoice_line in enumerate(invoice_goods):
        selected = assigned.get(row_index)
        floor = _eligible_floor(config, explicit_single_line)
        if selected is None or scores[row_index][selected] < floor:
            candidates = [
                column_index
                for column_index, score in enumerate(scores[row_index])
                if score >= floor
            ]
            selected = (
                max(
                    candidates,
                    key=lambda column_index: (scores[row_index][column_index], -column_index),
                )
                if candidates
                else None
            )

        if selected is None:
            result_by_line[_line_key(invoice_line)] = AlignmentResult(
                invoice_line_no=invoice_line.line_no,
                po_line_no=None,
                score=max(scores[row_index]),
                state=AlignmentState.NOT_ON_PO,
            )
            continue

        score = scores[row_index][selected]
        result_by_line[_line_key(invoice_line)] = AlignmentResult(
            invoice_line_no=invoice_line.line_no,
            po_line_no=sorted_pos[selected].line_no,
            score=score,
            state=_alignment_state(score, config, explicit_single_line),
        )

    return tuple(result_by_line[_line_key(line)] for line in sorted_invoices)


__all__ = [
    "AlignmentConfig",
    "AlignmentResult",
    "AlignmentWeights",
    "align_lines",
    "spec_tokens_conflict",
]
