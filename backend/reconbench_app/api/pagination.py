"""Validated keyset pagination for business-key ordered SQLAlchemy queries."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, TypeVar
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session
from sqlalchemy.sql import Select
from sqlalchemy.sql.elements import ColumnElement

from reconbench_app.json_serialization import to_jsonable

_MAX_CURSOR_LENGTH = 4096
_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200
T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class KeysetField:
    """A non-null business sort field; mark only the final unique business key."""

    key: str
    column: ColumnElement[Any]
    descending: bool = False
    unique: bool = False

    def __post_init__(self) -> None:
        column_name = getattr(self.column, "name", self.key)
        if (
            self.key == "id"
            or self.key.endswith("_id")
            or column_name == "id"
            or column_name.endswith("_id")
        ):
            raise ValueError("keyset ordering cannot depend on an identifier column")
        if getattr(self.column, "nullable", False):
            raise ValueError("keyset ordering fields must be non-null")
        try:
            python_type = self.column.type.python_type  # type: ignore[attr-defined]
        except (AttributeError, NotImplementedError):
            python_type = None
        if python_type is UUID:
            raise ValueError("keyset ordering cannot depend on UUID identifiers")


@dataclass(frozen=True, slots=True)
class KeysetPage[T]:
    items: list[T]
    next_cursor: str | None
    total_count: int | None
    limit: int

    def response_body(self) -> dict[str, Any]:
        """Serialize the shared list shape, omitting counts unless requested."""

        body: dict[str, Any] = {
            "items": to_jsonable(self.items),
            "next_cursor": self.next_cursor,
            "limit": self.limit,
        }
        if self.total_count is not None:
            body["total_count"] = self.total_count
        return body


def paginate_keyset(
    session: Session,
    statement: Select[Any],
    *,
    fields: Sequence[KeysetField],
    cursor: str | None = None,
    limit: int = _DEFAULT_LIMIT,
    count: bool = False,
    context: Mapping[str, Any] | None = None,
) -> KeysetPage[RowMapping]:
    """Fetch a stable page using explicit business keys and lexicographic seeking.

    The final field must be unique within the server-filtered result set. Pass the
    normalized filter/sort scope in ``context`` so a cursor cannot be reused with a
    different query. Identifier fields are rejected to prevent UUID tie-breaking.
    """

    if not 1 <= limit <= _MAX_LIMIT:
        raise ValueError(f"limit must be between 1 and {_MAX_LIMIT}")
    if not fields or not fields[-1].unique:
        raise ValueError("the final keyset field must be a unique business key")
    if any(field.unique for field in fields[:-1]):
        raise ValueError("only the final keyset field may be the unique tie-break")
    keys = tuple(field.key for field in fields)
    if len(set(keys)) != len(keys):
        raise ValueError("keyset field names must be unique")

    context_hash = _context_hash(context or {})
    base_statement = statement.order_by(None).limit(None).offset(None)
    total_count = None
    if count:
        count_statement = sa.select(sa.func.count()).select_from(base_statement.subquery())
        total_count = int(session.scalar(count_statement) or 0)

    query = base_statement
    if cursor is not None:
        values = _decode_cursor(cursor, fields=fields, context_hash=context_hash)
        query = query.where(_seek_predicate(fields, values))

    order_by = [field.column.desc() if field.descending else field.column.asc() for field in fields]
    rows = list(session.execute(query.order_by(*order_by).limit(limit + 1)).mappings())
    has_more = len(rows) > limit
    items = rows[:limit]
    next_cursor = None
    if has_more and items:
        last = items[-1]
        missing = [field.key for field in fields if field.key not in last]
        if missing:
            raise ValueError(f"keyset fields must be included in the selected rows: {missing}")
        next_cursor = _encode_cursor(
            fields=fields,
            values=[last[field.key] for field in fields],
            context_hash=context_hash,
        )
    return KeysetPage(items, next_cursor, total_count, limit)


def _seek_predicate(fields: Sequence[KeysetField], values: Sequence[Any]) -> ColumnElement[bool]:
    alternatives: list[ColumnElement[bool]] = []
    for index, field in enumerate(fields):
        equal_prefix = [fields[prefix].column == values[prefix] for prefix in range(index)]
        comparator = (
            field.column < values[index] if field.descending else field.column > values[index]
        )
        alternatives.append(sa.and_(*equal_prefix, comparator))
    return sa.or_(*alternatives)


def _encode_cursor(
    *, fields: Sequence[KeysetField], values: Sequence[Any], context_hash: str
) -> str:
    payload = {
        "v": 1,
        "f": [field.key for field in fields],
        "c": context_hash,
        "k": [_cursor_value(value) for value in values],
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str, *, fields: Sequence[KeysetField], context_hash: str) -> list[Any]:
    if not cursor or len(cursor) > _MAX_CURSOR_LENGTH:
        raise ValueError("invalid keyset cursor length")
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        payload = json.loads(raw)
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid keyset cursor") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("v") != 1
        or payload.get("f") != [field.key for field in fields]
        or payload.get("c") != context_hash
        or not isinstance(payload.get("k"), list)
        or len(payload["k"]) != len(fields)
    ):
        raise ValueError("keyset cursor does not match this query")
    return [
        _parse_cursor_value(field, value) for field, value in zip(fields, payload["k"], strict=True)
    ]


def _cursor_value(value: Any) -> Any:
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, UUID):
        raise ValueError("keyset cursors cannot contain UUID identifiers")
    if isinstance(value, str | int | bool) or value is None:
        return value
    raise ValueError(f"unsupported keyset cursor value: {type(value).__name__}")


def _parse_cursor_value(field: KeysetField, value: Any) -> Any:
    python_type = field.column.type.python_type  # type: ignore[attr-defined]
    if value is None:
        raise ValueError("keyset cursor values must be non-null")
    try:
        if python_type is Decimal:
            return Decimal(str(value))
        if python_type is datetime:
            return datetime.fromisoformat(str(value))
        if python_type is date:
            return date.fromisoformat(str(value))
        if python_type is UUID:
            raise ValueError("keyset ordering cannot depend on UUID identifiers")
        if python_type in (str, int, float, bool):
            return python_type(value)
    except (TypeError, ValueError, ArithmeticError) as exc:
        raise ValueError(f"invalid value for keyset field {field.key}") from exc
    raise ValueError(f"unsupported keyset field type for {field.key}")


def _context_hash(context: Mapping[str, Any]) -> str:
    normalized = {str(key): _context_value(value) for key, value in context.items()}
    raw = json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _context_value(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    # Filter scopes such as a status list only feed the hash, never the seek.
    if isinstance(value, list | tuple | set | frozenset):
        items = [_context_value(item) for item in value]
        return sorted(items, key=str) if isinstance(value, set | frozenset) else items
    return _cursor_value(value)


__all__ = ["KeysetField", "KeysetPage", "paginate_keyset"]
