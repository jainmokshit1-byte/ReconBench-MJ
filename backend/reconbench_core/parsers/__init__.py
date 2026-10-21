"""Pure parser contract for XML and CSV source files."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from reconbench_core.parsers.csv import parse_csv
from reconbench_core.parsers.pdf import parse_pdf
from reconbench_core.parsers.types import (
    PageImageHash,
    PageText,
    ParseResult,
    ParserHints,
    ParseWarning,
)
from reconbench_core.parsers.xml import parse_xml
from reconbench_core.schema import FileFormat


def parse(content: bytes, hints: ParserHints | Mapping[str, Any] | None = None) -> ParseResult:
    """Parse bytes into canonical documents; malformed/untrusted input returns failure data."""

    fallback = hashlib.sha256(b"reconbench:parse-failure").hexdigest()
    try:
        parser_hints = (
            hints if isinstance(hints, ParserHints) else ParserHints.model_validate(hints or {})
        )
        selected_format = parser_hints.file_format
        if selected_format is None:
            prefix = content.removeprefix(b"\xef\xbb\xbf").lstrip()[:1]
            selected_format = FileFormat.XML if prefix == b"<" else FileFormat.CSV
        if selected_format is FileFormat.XML:
            return parse_xml(content, parser_hints)
        if selected_format is FileFormat.CSV:
            return parse_csv(content, parser_hints)
        if selected_format is FileFormat.PDF:
            return parse_pdf(content, parser_hints)
        return ParseResult(
            success=False,
            file_format=selected_format,
            layout_signature=fallback,
            reason_code="UNSUPPORTED_FORMAT",
            warnings=(
                ParseWarning(
                    code="UNSUPPORTED_FORMAT",
                    message="This parser does not support the selected file format.",
                ),
            ),
        )
    except Exception:
        return ParseResult(
            success=False,
            file_format=None,
            layout_signature=fallback,
            reason_code="PARSE_FAILED",
            warnings=(
                ParseWarning(
                    code="PARSE_FAILED",
                    message="The source file could not be parsed safely.",
                ),
            ),
        )


__all__ = ["PageImageHash", "PageText", "ParseResult", "ParseWarning", "ParserHints", "parse"]
