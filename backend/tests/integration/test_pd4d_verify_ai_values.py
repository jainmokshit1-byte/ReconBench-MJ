"""Disposable-database tests for PD-4d: a person verifies values read by AI.

The AI reading itself arrives with PD-2e, so each test builds it directly: the uploaded
invoice is copied as a rules (OCR) reading and then as an AI reading of the same file.
Every invoice here is synthetic and nothing touches a live database.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from reconbench_app.api.application import create_app
from reconbench_app.authentication import COOKIE_NAME, AuthContext, AuthenticationCheck
from reconbench_app.authorization import ActorContext
from reconbench_app.db.ids import new_id
from reconbench_app.db.metadata import metadata
from reconbench_app.ingestion import BatchVersionConflict, IngestionFailure, IngestionService
from reconbench_app.settings import Settings
from sqlalchemy.engine import Engine
from tests.integration.test_pd_no_duplicates import _invoice, _setup, _upload_invoice
from tests.integration.test_phase4_task_411_413 import scratch_engine

__all__ = ["scratch_engine"]

# The AI reading differs from the OCR reading in this value only.
_DIFFERENT = "line 1 rate"


def _copy_version(
    engine: Engine, document_id: UUID, *, method: str, ai: bool, changed: str | None = None
) -> None:
    """Add a new current version of the document: a copy read with ``method``."""

    document = metadata.tables["document"]
    version = metadata.tables["document_version"]
    line = metadata.tables["doc_line"]
    evidence = metadata.tables["field_evidence"]
    with engine.begin() as connection:
        current = connection.execute(
            sa.select(document.c.current_version_id).where(document.c.id == document_id)
        ).scalar_one()
        old = dict(
            connection.execute(sa.select(version).where(version.c.id == current)).mappings().one()
        )
        new_version_id = new_id()
        copy = {**old, "id": new_version_id, "seq": int(old["seq"]) + 1}
        copy["extraction_method"] = method
        copy["reading_method"] = "ai" if method in {"ai_first", "ai_fallback"} else "rules"
        copy.pop("created_at", None)
        connection.execute(sa.insert(version).values(**copy))
        line_ids: dict[UUID, tuple[UUID, int]] = {}
        for row in connection.execute(
            sa.select(line).where(line.c.document_version_id == current)
        ).mappings():
            new_line_id = new_id()
            line_ids[row["id"]] = (new_line_id, int(row["line_no"]))
            line_copy = {**dict(row), "id": new_line_id, "document_version_id": new_version_id}
            line_copy.pop("created_at", None)
            connection.execute(sa.insert(line).values(**line_copy))
        rows = connection.execute(
            sa.select(evidence)
            .where(
                sa.or_(
                    evidence.c.document_version_id == current,
                    evidence.c.doc_line_id.in_(list(line_ids)),
                )
            )
            .order_by(evidence.c.created_at, evidence.c.id)
        ).mappings()
        latest: dict[tuple[Any, Any, str], dict[str, Any]] = {}
        for row in rows:
            latest[(row["document_version_id"], row["doc_line_id"], row["field_name"])] = dict(row)
        for row in latest.values():
            owner = row["doc_line_id"]
            name = (
                row["field_name"]
                if owner is None
                else f"line {line_ids[owner][1]} {row['field_name']}"
            )
            row.update(
                id=new_id(),
                document_version_id=None if owner is not None else new_version_id,
                doc_line_id=None if owner is None else line_ids[owner][0],
                verification="none",
                verified_by=None,
                verified_at=None,
            )
            if ai:
                row.update(origin="ai", source="ai", confidence=1)
                if name == changed:
                    row["value_text"] = f"{row['value_text']}9"
            else:
                row.update(origin="ocr", source="rule")
            row.pop("created_at", None)
            connection.execute(sa.insert(evidence).values(**row))
        connection.execute(
            sa.update(document)
            .where(document.c.id == document_id)
            .values(current_version_id=new_version_id)
        )


def _ai_read_invoice(
    engine: Engine, label: str, tmp_path: Any, *, ocr_reading: bool = True
) -> tuple[ActorContext, IngestionService, UUID]:
    actor, ingestion, batch_id = _setup(engine, label, tmp_path)
    _upload_invoice(
        ingestion, actor, batch_id, f"{label}.csv", _invoice("INV-PD4D-1", date(2026, 9, 5)), 10
    )
    document = metadata.tables["document"]
    with engine.connect() as connection:
        document_id = connection.execute(
            sa.select(document.c.id).where(
                document.c.workspace_id == actor.workspace_id,
                document.c.doc_no == "INV-PD4D-1",
            )
        ).scalar_one()
    if ocr_reading:
        _copy_version(engine, document_id, method="pdf_ocr", ai=False)
    _copy_version(engine, document_id, method="ai_first", ai=True, changed=_DIFFERENT)
    return actor, ingestion, document_id


def _details(ingestion: IngestionService, actor: ActorContext, document_id: UUID) -> Any:
    details = ingestion.get_document(actor.workspace_id, document_id)
    assert details is not None
    return details


def _by_label(details: Any) -> dict[str, dict[str, Any]]:
    return {item["label"]: item for item in details["ai_values"]}


def _audit(engine: Engine, document_id: UUID) -> list[dict[str, Any]]:
    audit = metadata.tables["audit_log"]
    with engine.connect() as connection:
        return [
            dict(row)
            for row in connection.execute(
                sa.select(audit.c.after_state)
                .where(
                    audit.c.action == "document.verify_ai_values",
                    audit.c.target_id == str(document_id),
                )
                .order_by(audit.c.occurred_at, audit.c.id)
            ).mappings()
        ]


def _open_invoice_scopes(engine: Engine, document_id: UUID) -> int:
    scopes = metadata.tables["dirty_scope"]
    with engine.connect() as connection:
        return int(
            connection.scalar(
                sa.select(sa.func.count())
                .select_from(scopes)
                .where(
                    scopes.c.scope_kind == "invoice",
                    scopes.c.scope_id == document_id,
                    scopes.c.processed_at.is_(None),
                )
            )
            or 0
        )


def test_each_value_read_by_ai_is_listed_beside_its_ocr_reading(
    scratch_engine: Engine, tmp_path: Any
) -> None:
    actor, ingestion, document_id = _ai_read_invoice(scratch_engine, "pd4d-list", tmp_path)

    values = _by_label(_details(ingestion, actor, document_id))

    assert {"doc_no", "grand_total", "line 1 quantity", _DIFFERENT} <= set(values)
    assert not any(item["verified"] for item in values.values())
    assert values[_DIFFERENT]["agrees"] is False
    assert values[_DIFFERENT]["value_text"] != values[_DIFFERENT]["ocr_value_text"]
    assert all(
        item["agrees"] and item["value_text"] == item["ocr_value_text"]
        for label, item in values.items()
        if label != _DIFFERENT and item["value_text"] is not None
    )


def test_a_rules_read_invoice_lists_no_ai_values(scratch_engine: Engine, tmp_path: Any) -> None:
    actor, ingestion, batch_id = _setup(scratch_engine, "pd4d-rules", tmp_path)
    _upload_invoice(
        ingestion, actor, batch_id, "rules.csv", _invoice("INV-PD4D-2", date(2026, 9, 5)), 10
    )
    document = metadata.tables["document"]
    with scratch_engine.connect() as connection:
        document_id = connection.execute(
            sa.select(document.c.id).where(
                document.c.workspace_id == actor.workspace_id,
                document.c.doc_no == "INV-PD4D-2",
            )
        ).scalar_one()

    assert _details(ingestion, actor, document_id)["ai_values"] == []


def test_verify_marks_only_the_chosen_value_and_is_audited(
    scratch_engine: Engine, tmp_path: Any
) -> None:
    actor, ingestion, document_id = _ai_read_invoice(scratch_engine, "pd4d-one", tmp_path)
    before = _details(ingestion, actor, document_id)
    chosen = _by_label(before)[_DIFFERENT]

    after = ingestion.verify_ai_values(
        actor,
        document_id,
        expected_version=int(before["document"]["version"]),
        values=[(chosen["line_id"], chosen["field_name"])],
    )

    assert after is not None
    values = _by_label(after)
    assert {label for label, item in values.items() if item["verified"]} == {_DIFFERENT}
    assert int(after["document"]["version"]) == int(before["document"]["version"]) + 1
    assert after["version"]["id"] == before["version"]["id"]
    rows = [
        row
        for row in after["field_evidence"]
        if row["doc_line_id"] == chosen["line_id"] and row["field_name"] == "rate"
    ]
    assert sorted(row["verification"] for row in rows) == ["none", "user"]
    assert {row["value_text"] for row in rows} == {chosen["value_text"]}
    assert next(row for row in rows if row["verification"] == "user")["verified_by"] == str(
        actor.actor_id
    )
    assert [
        item["after_state"]["verified_fields"] for item in _audit(scratch_engine, document_id)
    ] == [[_DIFFERENT]]
    assert _open_invoice_scopes(scratch_engine, document_id) == 1


def test_verify_all_covers_only_the_values_that_agree_with_ocr(
    scratch_engine: Engine, tmp_path: Any
) -> None:
    actor, ingestion, document_id = _ai_read_invoice(scratch_engine, "pd4d-all", tmp_path)
    before = _details(ingestion, actor, document_id)
    agreeing = {label for label, item in _by_label(before).items() if item["agrees"]}
    assert agreeing and _DIFFERENT not in agreeing

    after = ingestion.verify_ai_values(
        actor,
        document_id,
        expected_version=int(before["document"]["version"]),
        all_agreeing=True,
    )

    assert after is not None
    values = _by_label(after)
    assert {label for label, item in values.items() if item["verified"]} == agreeing
    assert values[_DIFFERENT]["verified"] is False
    audit = _audit(scratch_engine, document_id)
    assert audit[-1]["after_state"]["verified_fields"] == sorted(agreeing)
    assert audit[-1]["after_state"]["verify_all_agreeing"] is True

    # Nothing else agrees: "Verify all" refuses, the one that differs needs its own Verify.
    with pytest.raises(IngestionFailure) as refused:
        ingestion.verify_ai_values(
            actor,
            document_id,
            expected_version=int(after["document"]["version"]),
            all_agreeing=True,
        )
    assert refused.value.code == "AI_VALUE_NONE_TO_VERIFY"
    item = values[_DIFFERENT]
    last = ingestion.verify_ai_values(
        actor,
        document_id,
        expected_version=int(after["document"]["version"]),
        values=[(item["line_id"], item["field_name"])],
    )
    assert last is not None
    assert all(value["verified"] for value in last["ai_values"])


def test_without_an_ocr_reading_verify_all_verifies_nothing(
    scratch_engine: Engine, tmp_path: Any
) -> None:
    actor, ingestion, document_id = _ai_read_invoice(
        scratch_engine, "pd4d-no-ocr", tmp_path, ocr_reading=False
    )
    before = _details(ingestion, actor, document_id)
    assert all(
        item["ocr_value_text"] is None and item["agrees"] is False for item in before["ai_values"]
    )

    with pytest.raises(IngestionFailure) as refused:
        ingestion.verify_ai_values(
            actor,
            document_id,
            expected_version=int(before["document"]["version"]),
            all_agreeing=True,
        )

    assert refused.value.code == "AI_VALUE_NONE_TO_VERIFY"
    assert not any(
        item["verified"] for item in _details(ingestion, actor, document_id)["ai_values"]
    )


def test_a_value_the_ocr_reading_took_from_ai_is_no_cross_check(
    scratch_engine: Engine, tmp_path: Any
) -> None:
    actor, ingestion, document_id = _ai_read_invoice(scratch_engine, "pd4d-ai-in-ocr", tmp_path)
    version = metadata.tables["document_version"]
    evidence = metadata.tables["field_evidence"]
    with scratch_engine.begin() as connection:
        ocr_version = connection.execute(
            sa.select(version.c.id).where(
                version.c.document_id == document_id, version.c.extraction_method == "pdf_ocr"
            )
        ).scalar_one()
        row = dict(
            connection.execute(
                sa.select(evidence)
                .where(
                    evidence.c.document_version_id == ocr_version,
                    evidence.c.field_name == "doc_no",
                )
                .order_by(evidence.c.created_at.desc(), evidence.c.id.desc())
                .limit(1)
            )
            .mappings()
            .one()
        )
        row.update(id=new_id(), origin="ai", source="ai")
        row.pop("created_at")
        connection.execute(sa.insert(evidence).values(**row))

    values = _by_label(_details(ingestion, actor, document_id))

    assert values["doc_no"]["ocr_value_text"] is None
    assert values["doc_no"]["agrees"] is False
    assert values["grand_total"]["agrees"] is True


def test_verify_refuses_a_stale_screen_and_a_value_not_waiting(
    scratch_engine: Engine, tmp_path: Any
) -> None:
    actor, ingestion, document_id = _ai_read_invoice(scratch_engine, "pd4d-refuse", tmp_path)
    before = _details(ingestion, actor, document_id)
    version = int(before["document"]["version"])
    chosen = _by_label(before)["doc_no"]
    key = (chosen["line_id"], chosen["field_name"])

    with pytest.raises(BatchVersionConflict):
        ingestion.verify_ai_values(actor, document_id, expected_version=version - 1, values=[key])
    with pytest.raises(IngestionFailure) as unknown:
        ingestion.verify_ai_values(
            actor, document_id, expected_version=version, values=[(None, "no_such_field")]
        )
    assert unknown.value.code == "AI_VALUE_NOT_WAITING"

    ingestion.verify_ai_values(actor, document_id, expected_version=version, values=[key])
    with pytest.raises(IngestionFailure) as again:
        ingestion.verify_ai_values(actor, document_id, expected_version=version + 1, values=[key])
    assert again.value.code == "AI_VALUE_NOT_WAITING"
    assert len(_audit(scratch_engine, document_id)) == 1


def test_the_verify_endpoint_needs_the_loaded_version_and_one_clear_choice(
    scratch_engine: Engine, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RB_AI_EXTERNAL_CALLS", "disabled")
    actor, ingestion, document_id = _ai_read_invoice(scratch_engine, "pd4d-api", tmp_path)
    settings = Settings(
        _env_file=None,
        environment="ci",
        database_url=scratch_engine.url.render_as_string(hide_password=False),
        s3_endpoint_url="http://127.0.0.1:1",
        s3_region="us-east-1",
        s3_bucket="synthetic-pd4d",
        s3_api_access_key="synthetic-access",
        s3_api_secret_key="synthetic-secret",
        public_base_url="https://example.invalid",
        argon2_time_cost=1,
        argon2_memory_cost_kib=8192,
        argon2_parallelism=1,
    )
    app = create_app(settings)
    app.state.ingestion_service = ingestion
    context = AuthContext(
        session_id=new_id(),
        workspace_id=actor.workspace_id,
        user_id=actor.actor_id,
        display_name="Synthetic Reviewer",
        role="manager",
        csrf_token="synthetic-csrf",
    )
    app.state.auth_service.authenticate = lambda *args, **kwargs: AuthenticationCheck(context, True)
    url = f"/api/v1/documents/{document_id}/verify-ai-values"
    csrf = {"X-CSRF-Token": "synthetic-csrf"}
    with TestClient(app, base_url="https://testserver") as client:
        client.cookies.set(COOKIE_NAME, "synthetic-session", path="/api/v1")
        loaded = client.get(f"/api/v1/documents/{document_id}")
        assert loaded.status_code == 200, loaded.text
        etag = loaded.headers["ETag"]
        assert {item["label"] for item in loaded.json()["ai_values"]} >= {"doc_no", _DIFFERENT}

        missing = client.post(url, json={"all_agreeing": True}, headers=csrf)
        assert missing.status_code == 428, missing.text
        both = client.post(
            url,
            json={"all_agreeing": True, "values": [{"field_name": "doc_no"}]},
            headers={**csrf, "If-Match": etag},
        )
        assert both.status_code == 422, both.text
        stale = client.post(url, json={"all_agreeing": True}, headers={**csrf, "If-Match": '"1"'})
        assert stale.status_code == 409, stale.text

        done = client.post(url, json={"all_agreeing": True}, headers={**csrf, "If-Match": etag})
        assert done.status_code == 200, done.text
        assert done.headers["ETag"] != etag
        values = {item["label"]: item for item in done.json()["ai_values"]}
        assert values["doc_no"]["verified"] and not values[_DIFFERENT]["verified"]

        nothing = client.post(
            url, json={"all_agreeing": True}, headers={**csrf, "If-Match": done.headers["ETag"]}
        )
        assert nothing.status_code == 422, nothing.text
        assert nothing.json()["code"] == "ai_value_none_to_verify"
