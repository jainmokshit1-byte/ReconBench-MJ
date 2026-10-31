"""KI-006 diagnostic: answer_shape counts formatting, loops and rows, never values."""

from __future__ import annotations

import json
import re

from reconbench_app.ai.answer_shape import TAIL_PERIOD_MAX, answer_shape

_LINE = {
    "page": 1,
    "box_2d": [400, 30, 425, 970],
    "description": "Synthetic widget",
    "quantity": "10",
    "amount": "1,000.00",
    "uom": None,
}


def _answer(lines: list[dict[str, object]]) -> dict[str, object]:
    return {"doc_kind": "invoice", "fields": [], "lines": lines, "annotations": []}


def test_compact_answer_has_no_formatting_whitespace() -> None:
    compact = json.dumps(_answer([_LINE]), separators=(",", ":"))
    shape = answer_shape(compact)
    assert shape.chars == len(compact)
    assert (shape.ws_outside, shape.newlines_outside, shape.longest_ws_run) == (0, 0, 0)
    assert (shape.rows, shape.boxes, shape.distinct_boxes) == (1, 1, 1)
    assert shape.tail_period == 0


def test_pretty_printed_answer_counts_only_whitespace_outside_strings() -> None:
    line = {**_LINE, "description": "two  spaces\nand a newline"}
    compact = json.dumps(_answer([line]), separators=(",", ":"))
    pretty = json.dumps(_answer([line]), indent=4)
    shape = answer_shape(pretty)
    # Every extra character of the pretty form is formatting; string contents are not.
    assert shape.ws_outside == len(pretty) - len(compact)
    assert shape.newlines_outside == pretty.count("\n")
    assert shape.longest_ws_run > 1 and shape.rows == 1


def test_escaped_quotes_do_not_end_a_string_and_unicode_escapes_are_counted() -> None:
    text = '{"description":"say \\"hi\\" now  ","uom":"\\u20b9"} '
    shape = answer_shape(text)
    assert shape.ws_outside == 1  # only the trailing space; the two inside the string are data
    assert shape.unicode_escapes == 1


def test_a_cut_answer_that_repeats_its_rows_shows_a_loop() -> None:
    row = json.dumps(_LINE, separators=(",", ":")) + ","
    cut = '{"doc_kind":"invoice","lines":[' + row * 6
    shape = answer_shape(cut)
    assert shape.rows == 6 and shape.boxes == 6 and shape.distinct_boxes == 1
    assert shape.tail_period == len(row)


def test_runaway_whitespace_shows_as_a_long_run_with_period_one() -> None:
    cut = '{"doc_kind":"invoice","lines":[' + "\n" + " " * 500
    shape = answer_shape(cut)
    assert shape.longest_ws_run == 501 and shape.tail_period == 1


def test_distinct_rows_are_not_a_loop() -> None:
    lines = [{**_LINE, "box_2d": [400 + 30 * n, 30, 425 + 30 * n, 970]} for n in range(8)]
    cut = json.dumps(_answer(lines), separators=(",", ":"))[:-40]
    shape = answer_shape(cut)
    assert shape.rows == 8 and shape.distinct_boxes == shape.boxes
    assert shape.tail_period == 0


def test_tail_period_is_bounded_and_short_text_has_none() -> None:
    assert answer_shape("ab").tail_period == 0
    unit = "x" * (TAIL_PERIOD_MAX + 1)
    assert answer_shape("{" + unit * 3).tail_period == 1  # "xxx…" repeats with period 1
    long_unit = "".join(str(n) for n in range(1_000))[: TAIL_PERIOD_MAX + 1]
    assert answer_shape(long_unit * 3).tail_period == 0  # longer than the bound


def test_log_text_holds_only_counts() -> None:
    secret = {**_LINE, "description": "SECRET-INVOICE-4417", "amount": "98,765.43"}
    text = answer_shape(json.dumps(_answer([secret]), indent=2)).log_text()
    assert re.fullmatch(r"[a-z_]+=\d+( [a-z_]+=\d+){8}", text)
    assert "SECRET" not in text and "98,765.43" not in text and "4417" not in text
