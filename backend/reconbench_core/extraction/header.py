"""Label-anchored header values: find a printed label, read the value beside or below it.

Handles inline labels ("Invoice No: 1610357353"), boxed cells with the value
under the label (Tally, Ingram) and footer label rows whose values sit on the
next row in the same column ("TOTAL CGST" above "621925.93").
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from reconbench_core.extraction.layout import TextLine, Word

_NORMAL = re.compile(r"[^A-Z0-9]")


def norm(text: str) -> str:
    return _NORMAL.sub("", text.upper())


@dataclass(frozen=True, slots=True)
class Candidate:
    value: Any
    raw: str
    page: int
    words: tuple[Word, ...]
    label: str
    distance: int
    repaired: bool = False

    @property
    def conf(self) -> int:
        return min((word.conf for word in self.words), default=0)

    @property
    def bbox(self) -> tuple[int, int, int, int]:
        return (
            min(word.x0 for word in self.words),
            min(word.y0 for word in self.words),
            max(word.x1 for word in self.words),
            max(word.y1 for word in self.words),
        )


@dataclass(frozen=True, slots=True)
class LabelHit:
    line: TextLine
    start: int
    end: int
    label: str

    @property
    def words(self) -> tuple[Word, ...]:
        return self.line.words[self.start : self.end]

    @property
    def x0(self) -> int:
        return self.words[0].x0

    @property
    def x1(self) -> int:
        return self.words[-1].x1

    @property
    def y1(self) -> int:
        return max(word.y1 for word in self.words)

    @property
    def cy(self) -> int:
        return self.line.cy


def _token(text: str) -> str:
    token = norm(text)
    # "DATE/TIME" often reads as one token such as DATEITIME or DATEMTIME.
    if token.startswith("DATE") and token.endswith("TIME"):
        return "DATE"
    return token


def _tokens(line: TextLine) -> list[str]:
    return [_token(word.text) for word in line.words]


def find_labels(
    lines: Sequence[TextLine],
    labels: Sequence[str],
    *,
    exclude_before: frozenset[str] = frozenset(),
) -> list[LabelHit]:
    """Occurrences of any label (space-separated words) as consecutive tokens.

    Empty tokens (pure punctuation) are skipped. A hit is dropped when the token
    right before it is in ``exclude_before`` (e.g. "DUE" before "DATE").
    """

    hits: list[LabelHit] = []
    for line in lines:
        tokens = _tokens(line)
        positions = [index for index, token in enumerate(tokens) if token]
        for label in labels:
            parts = [norm(part) for part in label.split()]
            parts = [part for part in parts if part]
            for offset in range(len(positions)):
                match_end = None
                cursor = offset
                for part in parts:
                    if cursor >= len(positions):
                        break
                    token = tokens[positions[cursor]]
                    if token == part or (
                        len(part) >= 3 and token.startswith(part) and len(token) - len(part) <= 2
                    ):
                        cursor += 1
                    else:
                        break
                else:
                    match_end = positions[cursor - 1] + 1
                if match_end is None:
                    continue
                start = positions[offset]
                previous = tokens[positions[offset - 1]] if offset > 0 else ""
                if previous in exclude_before:
                    continue
                hits.append(LabelHit(line, start, match_end, label))
    return hits


Accept = Callable[[str], Any]


def _try_tokens(words: Sequence[Word], accept: Accept, *, max_join: int) -> tuple[Any, int] | None:
    for join in range(1, max_join + 1):
        if join > len(words):
            break
        text = " ".join(word.text for word in words[:join])
        value = accept(text)
        if value is not None:
            return value, join
    return None


def values_for(
    hits: Sequence[LabelHit],
    lines: Sequence[TextLine],
    accept: Accept,
    *,
    height: int,
    max_join: int = 1,
    right: bool = True,
    below: bool = True,
    below_rows: int = 3,
    right_gap_heights: int = 30,
) -> list[Candidate]:
    """Candidates to the right of each label on its row, then in the rows beneath it."""

    found: list[Candidate] = []
    for hit in hits:
        if right:
            after = [
                word
                for word in hit.line.words[hit.end :]
                if norm(word.text) or any(char.isdigit() for char in word.text)
            ]
            after = [word for word in after if word.x0 - hit.x1 <= right_gap_heights * height]
            for index in range(min(len(after), 4)):
                result = _try_tokens(after[index:], accept, max_join=max_join)
                if result is not None:
                    value, join = result
                    used = tuple(after[index : index + join])
                    found.append(
                        Candidate(
                            value=value,
                            raw=" ".join(word.text for word in used),
                            page=hit.line.page,
                            words=used,
                            label=hit.label,
                            distance=used[0].x0 - hit.x1,
                        )
                    )
                    break
        if below:
            column_x0 = hit.x0 - 2 * height
            column_x1 = hit.x1 + 8 * height
            # Compare word centres, not row tops: a tall logo can share the value's row.
            rows = [
                line
                for line in lines
                if line.page == hit.line.page
                and hit.y1 < line.cy <= hit.y1 + below_rows * height * 2
            ]
            rows.sort(key=lambda line: line.cy)
            for line in rows[:below_rows]:
                words = [
                    word
                    for word in line.words
                    if column_x0 <= word.cx <= column_x1 and word.cy > hit.y1
                ]
                if not words:
                    continue
                result = _try_tokens(words, accept, max_join=max_join)
                if result is not None:
                    value, join = result
                    used = tuple(words[:join])
                    found.append(
                        Candidate(
                            value=value,
                            raw=" ".join(word.text for word in used),
                            page=hit.line.page,
                            words=used,
                            label=hit.label,
                            distance=10_000 + (used[0].y0 - hit.y1),
                        )
                    )
                    break
    found.sort(key=lambda candidate: candidate.distance)
    return found


def aligned_below(
    hit: LabelHit, lines: Sequence[TextLine], accept: Accept, *, height: int
) -> Candidate | None:
    """The value on the next row whose horizontal centre falls inside the label span."""

    rows = [
        line
        for line in lines
        if line.page == hit.line.page and hit.y1 < line.cy <= hit.y1 + 5 * height
    ]
    rows.sort(key=lambda line: line.cy)
    for line in rows[:4]:
        for word in line.words:
            if hit.x0 - height <= word.cx <= hit.x1 + 2 * height:
                value = accept(word.text)
                if value is not None:
                    return Candidate(
                        value, word.text, hit.line.page, (word,), hit.label, word.y0 - hit.y1
                    )
    return None


__all__ = ["Candidate", "LabelHit", "aligned_below", "find_labels", "norm", "values_for"]
