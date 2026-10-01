"""Unit coverage for workspace and version conventions used by repositories."""

from uuid import uuid4

import pytest
import sqlalchemy as sa
from reconbench_app.db.metadata import metadata
from reconbench_app.db.repository import (
    VersionConflict,
    parse_if_match,
    version_etag,
    workspace_filter,
)
from sqlalchemy.dialects import postgresql


def test_workspace_filter_adds_a_required_workspace_predicate() -> None:
    vendor = metadata.tables["vendor"]
    workspace_id = uuid4()
    statement = workspace_filter(sa.select(vendor), vendor, workspace_id)

    compiled = statement.compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "WHERE vendor.workspace_id = " in sql
    assert workspace_id in compiled.params.values()


def test_workspace_filter_rejects_unscoped_tables() -> None:
    workspace = metadata.tables["workspace"]
    with pytest.raises(ValueError, match="workspace_id"):
        workspace_filter(sa.select(workspace), workspace, uuid4())


@pytest.mark.parametrize(("header", "expected"), [("1", 1), ('"42"', 42), ('  "7" ', 7)])
def test_if_match_parses_strong_integer_etags(header: str, expected: int) -> None:
    assert parse_if_match(header) == expected


@pytest.mark.parametrize("header", ["", "*", 'W/"1"', '"x"', "0", "-1"])
def test_if_match_rejects_unsupported_or_invalid_values(header: str) -> None:
    with pytest.raises(ValueError):
        parse_if_match(header)


def test_version_etag_is_strong_and_conflict_retains_current_state() -> None:
    assert version_etag(3) == '"3"'
    conflict = VersionConflict({"id": "current", "version": 3})
    assert conflict.current == {"id": "current", "version": 3}
