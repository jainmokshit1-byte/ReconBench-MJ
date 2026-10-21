"""Shadow reader comparison (LLM plan WI-BE6): measure one reader without effect.

With ``reader_strategy = shadow`` the workspace's one reader (``primary_reader``,
Gemini *or* the company vLLM server, never both: plan §0.6) reads page images in
the background while H1 keeps running. A checked answer is compared with the
stored rules reading of the same version and one append-only
``reader_comparison`` row is written: counts and per-field labels, never a value.
Nothing here writes a document, a line, a consensus run, a vendor or a link, and
the review view does not show a shadow answer.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Any, Literal, cast
from uuid import UUID

import sqlalchemy as sa
from reconbench_core.extraction.consensus import (
    HEADER_FIELD_TYPES,
    LINE_FIELD_TYPES,
    LINE_VALUE_KINDS,
    VALUE_KINDS,
    ConsensusValue,
    FieldKey,
    values_agree,
    vision_readings,
)
from reconbench_core.extraction.vision import (
    VISION_HEADER_FIELDS,
    VISION_REF_FIELDS,
    VISION_TYPE_FIELDS,
    VisionCheck,
)
from reconbench_core.schema import Document
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.engine import Connection

from reconbench_app.ai.cache import reader_params_digest
from reconbench_app.ai.consensus import document_from_rows
from reconbench_app.ai.runtime_checks import load_settings
from reconbench_app.db.ids import new_id
from reconbench_app.db.metadata import metadata

COMPARISONS = metadata.tables["reader_comparison"]
REQUESTS = metadata.tables["ai_request"]
LOGS = metadata.tables["ai_call_log"]
DOCUMENTS = metadata.tables["document"]
VERSIONS = metadata.tables["document_version"]
LINES = metadata.tables["doc_line"]
EVIDENCE = metadata.tables["field_evidence"]

SHADOW_CODE = "SHADOW"
"""``ai-suggestions`` state ``not_applicable`` code for a document read only in shadow."""

Label = Literal["agree_ocr", "disagree_ocr", "llm_only", "ocr_only", "unreadable"]
LABELS: tuple[Label, ...] = ("agree_ocr", "disagree_ocr", "llm_only", "ocr_only", "unreadable")


@dataclass(frozen=True, slots=True)
class Comparison:
    """What one shadow answer agreed with; field keys and labels only, never values."""

    counts: dict[str, Any]
    labels: list[dict[str, str]]


def _plain(value: object) -> ConsensusValue | None:
    if isinstance(value, (str, Decimal, date)):
        return value
    if isinstance(value, (list, tuple)):
        items = tuple(str(item) for item in value if item is not None)
        return items or None
    if value is None:
        return None
    return str(value)


def _rules_values(document: Document) -> dict[FieldKey, ConsensusValue]:
    values: dict[FieldKey, ConsensusValue] = {}
    for name in HEADER_FIELD_TYPES:
        field_value = getattr(document.header, name, None)
        plain = _plain(getattr(field_value, "value", None))
        if plain is not None:
            values[FieldKey("header", name)] = plain
    for line in document.lines:
        for name in LINE_FIELD_TYPES:
            plain = _plain(getattr(getattr(line, name, None), "value", None))
            if plain is not None:
                values[FieldKey("line", name, line.line_no)] = plain
    return values


def _unreadable_keys(document: Document, check: VisionCheck) -> set[FieldKey]:
    doc_type = document.header.doc_type
    names = {
        **VISION_HEADER_FIELDS,
        **VISION_TYPE_FIELDS[doc_type],
        **VISION_REF_FIELDS[doc_type],
    }
    return {
        FieldKey("header", names[item.name])
        for item in check.unreadable
        if item.name in names and names[item.name] in HEADER_FIELD_TYPES
    }


def _kind(key: FieldKey) -> str:
    if key.scope == "header":
        return VALUE_KINDS.get(key.name, "text")
    return LINE_VALUE_KINDS.get(key.name, "number")


def compare(rules: Document, check: VisionCheck) -> Comparison:
    """Label every comparable field the rules or the reader has (pure)."""

    rules_values = _rules_values(rules)
    readings, _unpaired = vision_readings(rules, check)
    reader_values = {
        key: reading.value
        for key, reading in readings.items()
        if (key.scope == "header" and key.name in HEADER_FIELD_TYPES)
        or (key.scope == "line" and key.name in LINE_FIELD_TYPES)
    }
    unreadable = _unreadable_keys(rules, check)
    labels: list[dict[str, str]] = []
    for key in sorted(rules_values.keys() | reader_values.keys() | unreadable):
        ours, theirs = rules_values.get(key), reader_values.get(key)
        label: Label
        if theirs is not None and ours is not None:
            label = "agree_ocr" if values_agree(_kind(key), theirs, ours) else "disagree_ocr"
        elif theirs is not None:
            label = "llm_only"
        elif key in unreadable:
            label = "unreadable"
        else:
            label = "ocr_only"
        labels.append({"field_key": key.text(), "label": label})
    tally = Counter(item["label"] for item in labels)
    counts: dict[str, Any] = {
        "fields_total": len(labels),
        **{label: tally.get(label, 0) for label in LABELS},
        "check_failed_by_reason": {str(k): int(v) for k, v in sorted(check.check_failed.items())},
        "dropped_by_reason": {str(k): int(v) for k, v in sorted(check.dropped.items())},
        "rows": len(check.lines),
    }
    return Comparison(counts=counts, labels=labels)


def rules_document(connection: Connection, workspace_id: UUID, version_id: UUID) -> Document | None:
    """The stored rules reading of one version (the same rebuild the consensus job uses)."""

    version_row = (
        connection.execute(
            sa.select(VERSIONS).where(
                VERSIONS.c.workspace_id == workspace_id, VERSIONS.c.id == version_id
            )
        )
        .mappings()
        .one_or_none()
    )
    if version_row is None:
        return None
    document_row = (
        connection.execute(
            sa.select(DOCUMENTS).where(
                DOCUMENTS.c.workspace_id == workspace_id,
                DOCUMENTS.c.id == version_row["document_id"],
            )
        )
        .mappings()
        .one()
    )
    line_rows = (
        connection.execute(
            sa.select(LINES)
            .where(LINES.c.workspace_id == workspace_id, LINES.c.document_version_id == version_id)
            .order_by(LINES.c.line_no)
        )
        .mappings()
        .all()
    )
    line_ids = [row["id"] for row in line_rows]
    evidence_rows = (
        connection.execute(
            sa.select(EVIDENCE).where(
                EVIDENCE.c.workspace_id == workspace_id,
                sa.or_(
                    EVIDENCE.c.document_version_id == version_id,
                    EVIDENCE.c.doc_line_id.in_(line_ids) if line_ids else sa.false(),
                ),
            )
        )
        .mappings()
        .all()
    )
    return document_from_rows(document_row, version_row, line_rows, evidence_rows)


def record(
    connection: Connection,
    request: Mapping[str, Any],
    version_id: UUID,
    check: VisionCheck,
    *,
    now: datetime,
) -> bool:
    """Write the one comparison row of a shadow request; ``False`` when it already exists."""

    workspace_id = cast(UUID, request["workspace_id"])
    rules = rules_document(connection, workspace_id, version_id)
    if rules is None:
        return False
    comparison = compare(rules, check)
    inserted = connection.execute(
        postgres_insert(COMPARISONS)
        .values(
            id=new_id(),
            workspace_id=workspace_id,
            document_version_id=version_id,
            ai_request_id=request["id"],
            provider=request["provider"],
            model=request["model"],
            prompt_version=request["prompt_version"],
            schema_version=request["schema_version"],
            reader_params_digest=reader_params_digest(request["reader_params"] or {}),
            counts=comparison.counts,
            labels=comparison.labels,
            created_at=now,
        )
        .on_conflict_do_nothing(index_elements=["workspace_id", "ai_request_id"])
        .returning(COMPARISONS.c.id)
    ).scalar_one_or_none()
    return inserted is not None


# --------------------------------------------------------------------------- report


def _field_group(field_key: str) -> str:
    """``line:3:amount`` → ``line:amount``: the report aggregates lines by field."""

    parts = field_key.split(":")
    return f"line:{parts[2]}" if len(parts) == 3 and parts[0] == "line" else field_key


def report(
    connection: Connection, workspace_id: UUID, *, since: datetime | None = None
) -> dict[str, Any]:
    """Aggregates only: per provider and field, per-provider latency and failure codes.

    Never a value, a document id or a field key with a line number.
    """

    window = [COMPARISONS.c.workspace_id == workspace_id]
    request_window = [REQUESTS.c.workspace_id == workspace_id, REQUESTS.c.shadow.is_(True)]
    if since is not None:
        window.append(COMPARISONS.c.created_at >= since)
        request_window.append(REQUESTS.c.created_at >= since)
    providers: dict[str, dict[str, Any]] = {}

    def entry(provider: str) -> dict[str, Any]:
        return providers.setdefault(
            provider,
            {
                "provider": provider,
                "comparisons": 0,
                "totals": {"fields_total": 0, **{label: 0 for label in LABELS}, "rows": 0},
                "fields": {},
                "check_failed_by_reason": Counter(),
                "dropped_by_reason": Counter(),
                "requests": {},
                "failures_by_code": {},
                "latency_ms_p50": None,
                "latency_ms_p95": None,
            },
        )

    for row in connection.execute(
        sa.select(COMPARISONS.c.provider, COMPARISONS.c.counts, COMPARISONS.c.labels).where(*window)
    ).mappings():
        item = entry(str(row["provider"]))
        item["comparisons"] += 1
        counts = row["counts"] if isinstance(row["counts"], Mapping) else {}
        for name in item["totals"]:
            item["totals"][name] += int(counts.get(name, 0) or 0)
        for name in ("check_failed_by_reason", "dropped_by_reason"):
            reasons = counts.get(name)
            if isinstance(reasons, Mapping):
                item[name].update({str(k): int(v) for k, v in reasons.items()})
        for label_row in row["labels"] if isinstance(row["labels"], Sequence) else ():
            if not isinstance(label_row, Mapping):
                continue
            label = label_row.get("label")
            if label not in LABELS:
                continue
            group = item["fields"].setdefault(
                _field_group(str(label_row.get("field_key"))), {name: 0 for name in LABELS}
            )
            group[label] += 1

    for provider, state, code, count in connection.execute(
        sa.select(
            REQUESTS.c.provider,
            REQUESTS.c.request_state,
            sa.func.coalesce(REQUESTS.c.error_code, REQUESTS.c.skip_code),
            sa.func.count(),
        )
        .where(*request_window)
        .group_by(
            REQUESTS.c.provider,
            REQUESTS.c.request_state,
            sa.func.coalesce(REQUESTS.c.error_code, REQUESTS.c.skip_code),
        )
    ):
        item = entry(str(provider))
        item["requests"][str(state)] = item["requests"].get(str(state), 0) + int(count)
        if state in {"failed", "uncertain", "skipped"} and code is not None:
            item["failures_by_code"][str(code)] = item["failures_by_code"].get(str(code), 0) + int(
                count
            )

    for provider, p50, p95 in connection.execute(
        sa.select(
            LOGS.c.provider,
            sa.func.percentile_cont(0.5).within_group(LOGS.c.latency_ms),
            sa.func.percentile_cont(0.95).within_group(LOGS.c.latency_ms),
        )
        .select_from(LOGS.join(REQUESTS, REQUESTS.c.id == LOGS.c.ai_request_id))
        .where(*request_window, LOGS.c.outcome != "cache_hit", LOGS.c.latency_ms.is_not(None))
        .group_by(LOGS.c.provider)
    ):
        item = entry(str(provider))
        item["latency_ms_p50"] = None if p50 is None else int(p50)
        item["latency_ms_p95"] = None if p95 is None else int(p95)

    result = []
    for name in sorted(providers):
        item = providers[name]
        result.append(
            {
                **item,
                "fields": [
                    {"field": field, **tally} for field, tally in sorted(item["fields"].items())
                ],
                "check_failed_by_reason": dict(sorted(item["check_failed_by_reason"].items())),
                "dropped_by_reason": dict(sorted(item["dropped_by_reason"].items())),
                "requests": dict(sorted(item["requests"].items())),
                "failures_by_code": dict(sorted(item["failures_by_code"].items())),
            }
        )
    return {"since": since, "providers": result}


def workspace_report(
    connection: Connection, workspace_id: UUID, *, since: date | None = None
) -> dict[str, Any]:
    """The report the API and the CLI return: the aggregates plus the current strategy."""

    settings = load_settings(connection, workspace_id)
    start = datetime.combine(since, time.min, tzinfo=UTC) if since is not None else None
    return {
        "reader_strategy": settings.strategy if settings is not None else "off",
        "shadow_reader": (
            settings.vision_provider
            if settings is not None and settings.strategy == "shadow"
            else None
        ),
        **report(connection, workspace_id, since=start),
    }


__all__ = [
    "LABELS",
    "SHADOW_CODE",
    "Comparison",
    "compare",
    "record",
    "report",
    "rules_document",
    "workspace_report",
]
