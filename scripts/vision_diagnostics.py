"""Why a vision call failed, for the owner's bench and probe runs (AC-5b); no document values.

The app's adapter keeps provider messages out of logs and the ledger on purpose
(D15), so a failed bench call only said ``INVALID_REQUEST`` or
``invalid_output``. For the owner's own runs, :class:`DiagnosticTransport` sits
under the app's single-attempt guard, reads the one response the SDK receives,
and hands it on unchanged (the adapter behaves exactly as in the app). It keeps:

* an HTTP error: the status code, Google's ``error.status``/``error.code`` and
  ``error.message``. The request carries only the prompt and the masked page
  images, never document text, so the message cannot quote a document value;
  it is still cut to 300 characters with long digit runs replaced;
* a finished interaction: its ``status`` and token usage, and when it is not
  ``completed``, *counts* taken from the partial answer: characters, line rows
  and distinct line rows (a model that repeats rows until the output limit).
  The partial text itself is never stored or printed.
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

MESSAGE_LIMIT = 300
_LONG_DIGITS = re.compile(r"\d{6,}")
_KEY_SHAPE = re.compile(r"AIza[0-9A-Za-z_\-]{10,}")
_FLAT_OBJECT = re.compile(r"\{[^{}]*\}")
_BOX = re.compile(r'"box_2d"\s*:\s*\[[^\]]*\]')
_SKIPPED_HEADERS = {"content-encoding", "content-length", "transfer-encoding"}


def safe_message(message: str) -> str:
    text = " ".join(message.split())
    text = _KEY_SHAPE.sub("[key]", text)
    text = _LONG_DIGITS.sub("#", text)
    return text[:MESSAGE_LIMIT]


def _short(value: object) -> str | None:
    if isinstance(value, (str, int)) and not isinstance(value, bool):
        return str(value)[:64]
    return None


def _output_text(interaction: dict[str, Any]) -> str:
    parts: list[str] = []
    for step in interaction.get("steps") or []:
        if not isinstance(step, dict) or step.get("type") != "model_output":
            continue
        for item in step.get("content") or []:
            if isinstance(item, dict) and item.get("type") == "text":
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
    return "".join(parts)


_SECTION = re.compile(r'"(pages|fields|lines|annotations)"\s*:')
_INT_ARRAY = re.compile(r"\[\s*-?\d+(?:\s*,\s*-?\d+)*")
_STRING = re.compile(r'"((?:[^"\\]|\\.)*)"')


def row_counts(text: str) -> dict[str, Any]:
    """Where a (possibly cut) answer went: counts and section names only, never text.

    ``last_section`` is the last top-level list the answer opened; objects are the
    flat ``{...}`` items (pages, fields, rows, notes) and how many differ once boxes
    are ignored; ``longest_int_array`` / ``longest_string`` / ``tail_chars`` (the
    unterminated value at the end) show a run-away box or string.
    """

    rows = [item for item in _FLAT_OBJECT.findall(text) if '"description"' in item]
    objects = _FLAT_OBJECT.findall(text)
    sections = _SECTION.findall(text)
    arrays = [match.group(0).count(",") + 1 for match in _INT_ARRAY.finditer(text)]
    strings = [len(match.group(1)) for match in _STRING.finditer(text)]
    tail = text[text.rfind('"') + 1 :] if text.count('"') % 2 else ""
    last_close = max(text.rfind("}"), text.rfind("]"))
    return {
        "partial_chars": len(text),
        "partial_rows": len(rows),
        "distinct_rows": len({_BOX.sub("", " ".join(item.split())) for item in rows}),
        "objects": len(objects),
        "distinct_objects": len({_BOX.sub("", " ".join(item.split())) for item in objects}),
        "last_section": sections[-1] if sections else None,
        "longest_int_array": max(arrays, default=0),
        "longest_string": max(strings, default=0),
        "tail_chars": len(tail) if tail else len(text) - last_close - 1,
    }


def describe(status_code: int, body: bytes) -> dict[str, Any]:
    """Counts and Google's own error text for one response; never document values."""

    try:
        parsed: Any = json.loads(body)
    except (UnicodeDecodeError, ValueError):
        parsed = None
    info: dict[str, Any] = {"http": status_code}
    if status_code >= 400:
        error = parsed.get("error") if isinstance(parsed, dict) else None
        if isinstance(error, dict):
            for key in ("status", "code"):
                if (value := _short(error.get(key))) is not None:
                    info[f"error_{key}"] = value
            message = error.get("message")
            if isinstance(message, str):
                info["error_message"] = safe_message(message)
        return info
    if not isinstance(parsed, dict):
        return info
    if (status := _short(parsed.get("status"))) is not None:
        info["status"] = status
    usage = parsed.get("usage")
    if isinstance(usage, dict):
        for key in ("total_input_tokens", "total_output_tokens", "total_thought_tokens"):
            value = usage.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                info[key.removeprefix("total_")] = value
    errors = parsed.get("errors")
    if isinstance(errors, list) and errors:
        info["errors"] = [
            {
                key: text
                for key, text in (
                    ("code", _short(item.get("code"))),
                    ("message", safe_message(str(item.get("message") or ""))),
                )
                if text
            }
            for item in errors[:3]
            if isinstance(item, dict)
        ]
    if info.get("status") != "completed":
        info.update(row_counts(_output_text(parsed)))
    return info


def looks_like_a_loop(info: dict[str, Any]) -> bool:
    rows, distinct = info.get("partial_rows", 0), info.get("distinct_rows", 0)
    objects, distinct_objects = info.get("objects", 0), info.get("distinct_objects", 0)
    return bool(
        (rows >= 20 and distinct * 2 <= rows)
        or (objects >= 40 and distinct_objects * 2 <= objects)
        or info.get("longest_int_array", 0) > 8
        or info.get("longest_string", 0) > 2_000
        or info.get("tail_chars", 0) > 2_000
    )


def at_output_cap(info: dict[str, Any], max_output_tokens: int) -> bool | None:
    output = info.get("output_tokens")
    if not isinstance(output, int):
        return None
    used = output + int(info.get("thought_tokens") or 0)
    return used >= max_output_tokens * 0.95


class DiagnosticTransport(httpx.BaseTransport):
    """Pass one response through unchanged and remember :func:`describe` of it."""

    def __init__(self, inner: httpx.BaseTransport | None = None) -> None:
        self._inner = inner if inner is not None else httpx.HTTPTransport(retries=0)
        self.info: dict[str, Any] = {}

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        response = self._inner.handle_request(request)
        try:
            body = response.read()
        finally:
            response.close()
        self.info = describe(response.status_code, body)
        headers = [
            (name, value)
            for name, value in response.headers.multi_items()
            if name.lower() not in _SKIPPED_HEADERS
        ]
        return httpx.Response(response.status_code, headers=headers, content=body, request=request)

    def close(self) -> None:
        self._inner.close()


__all__ = [
    "MESSAGE_LIMIT",
    "DiagnosticTransport",
    "at_output_cap",
    "describe",
    "looks_like_a_loop",
    "row_counts",
    "safe_message",
]
