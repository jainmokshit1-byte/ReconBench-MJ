"""Counts that describe the shape of one raw AI answer, never its values (KI-006).

A self-hosted reader answer that hits the output cap is lost, and a stored answer
is re-serialised compactly, so neither shows where the output tokens went. These
counts tell the cases apart: insignificant whitespace (formatting), a repeating tail
(a loop) or simply many rows (a long answer). Only integers are produced; no text,
number or coordinate from the answer is ever returned or logged.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from reconbench_core.extraction.vision import FIELD_KINDS

TAIL_PERIOD_MAX = 400
"""Longest repeating unit (characters) looked for at the end of an answer."""
TAIL_REPEATS = 3
"""How many back-to-back copies at the end count as a repeating tail."""

_ROW_KEY = re.compile(r'"description"\s*:')
_BOX = re.compile(r'"box_2d"\s*:\s*\[([^\]]*)\]')
_SPACE = re.compile(r"\s+")
_NAME = re.compile(r'"name"\s*:\s*"([a-z_]+)"')
NAMES_LOGGED = 12
"""Most repeated field names that the log line lists."""


@dataclass(frozen=True, slots=True)
class AnswerShape:
    chars: int
    """Length of the raw answer."""
    ws_outside: int
    """Whitespace characters outside JSON strings (formatting the parser ignores)."""
    newlines_outside: int
    longest_ws_run: int
    """Longest run of whitespace outside JSON strings."""
    rows: int
    """``"description":`` keys, so line rows started (a cut answer counts its last one)."""
    boxes: int
    distinct_boxes: int
    """Distinct ``box_2d`` arrays; far fewer than ``boxes`` means rows repeat."""
    unicode_escapes: int
    """``\\u`` escapes, which cost several tokens each."""
    tail_period: int
    """Smallest unit (1..TAIL_PERIOD_MAX) the end repeats TAIL_REPEATS times; 0 if none."""
    names: tuple[tuple[str, int], ...] = ()
    """The most repeated field names with their counts; only names of the reader's fixed
    list are counted (anything else is not named)."""

    def log_text(self) -> str:
        return (
            f"chars={self.chars} ws_outside={self.ws_outside} "
            f"newlines_outside={self.newlines_outside} longest_ws_run={self.longest_ws_run} "
            f"rows={self.rows} boxes={self.boxes} distinct_boxes={self.distinct_boxes} "
            f"unicode_escapes={self.unicode_escapes} tail_period={self.tail_period} "
            f"names={','.join(f'{name}:{count}' for name, count in self.names) or '-'}"
        )


def answer_shape(content: str) -> AnswerShape:
    """Shape counts of ``content``; works on a cut-off (unparseable) answer too."""

    ws_outside = newlines_outside = longest = run = 0
    in_string = escaped = False
    for char in content:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char.isspace():
            ws_outside += 1
            newlines_outside += char == "\n"
            run += 1
            longest = max(longest, run)
            continue
        run = 0
        if char == '"':
            in_string = True
    boxes = [_SPACE.sub("", match) for match in _BOX.findall(content)]
    return AnswerShape(
        chars=len(content),
        ws_outside=ws_outside,
        newlines_outside=newlines_outside,
        longest_ws_run=longest,
        rows=len(_ROW_KEY.findall(content)),
        boxes=len(boxes),
        distinct_boxes=len(set(boxes)),
        unicode_escapes=content.count("\\u"),
        tail_period=_tail_period(content),
        names=tuple(
            Counter(name for name in _NAME.findall(content) if name in FIELD_KINDS).most_common(
                NAMES_LOGGED
            )
        ),
    )


def _tail_period(content: str) -> int:
    for period in range(1, TAIL_PERIOD_MAX + 1):
        span = period * TAIL_REPEATS
        if span > len(content):
            return 0
        if content[-span:] == content[-period:] * TAIL_REPEATS:
            return period
    return 0


__all__ = ["TAIL_PERIOD_MAX", "TAIL_REPEATS", "AnswerShape", "answer_shape"]
