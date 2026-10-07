"""Revision 0017 checks; requires a disposable, migrated PostgreSQL 16 database."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from reconbench_app.db.ids import new_id
from reconbench_app.db.metadata import metadata
from sqlalchemy.engine import Connection, Engine, make_url
from sqlalchemy.exc import DBAPIError, IntegrityError

_NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)


def _hash(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    dsn = os.environ.get("RB_PHASE2_TEST_DATABASE_URL", "").strip()
    if not dsn:
        pytest.skip("RB_PHASE2_TEST_DATABASE_URL is required for disposable PostgreSQL tests")
    url = make_url(dsn)
    if not url.database or not url.database.startswith("reconbench_phase2_"):
        raise RuntimeError(
            "database checks require a disposable database named reconbench_phase2_*"
        )
    created = sa.create_engine(dsn)
    with created.connect() as connection:
        if int(connection.scalar(sa.text("SHOW server_version_num"))) // 10_000 != 16:
            raise RuntimeError("revision 0017 checks require PostgreSQL 16")
        heads = set(ScriptDirectory.from_config(Config("backend/alembic.ini")).get_heads())
        assert len(heads) == 1  # 0017 objects are checked at the current single head
        assert set(MigrationContext.configure(connection).get_current_heads()) == heads
    yield created
    created.dispose()


@pytest.fixture(scope="module")
def seed(engine: Engine) -> tuple[UUID, UUID]:
    with engine.connect() as connection:
        row = connection.execute(
            sa.text("SELECT workspace_id, id FROM app_user ORDER BY created_at, id LIMIT 1")
        ).first()
    if row is None:
        pytest.skip("run scripts/seed_database.py before the revision 0017 checks")
    return UUID(str(row[0])), UUID(str(row[1]))


@pytest.fixture
def connection(engine: Engine) -> Iterator[Connection]:
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            yield connection
        finally:
            transaction.rollback()


def _request(workspace_id: UUID, **overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "id": new_id(),
        "workspace_id": workspace_id,
        "call_kind": "hook",
        "hook": "h4_vendor",
        "mode": "ai_assisted",
        "subject_type": "vendor_merge_suggestion",
        "subject_id": new_id(),
        "subject_version": "1",
        "operation_key": "merge-review",
        "settings_version": 1,
        "consent_fingerprint": _hash("consent"),
        "provider": "gemini",
        "model": "gemini-3.8-flash",
        "prompt_version": "h4.v1",
        "schema_version": "h4.v1",
        "redaction_version": "redaction.v1",
        "request_key": _hash("request"),
    }
    values.update(overrides)
    return values


def _insert_request(connection: Connection, values: dict[str, Any]) -> None:
    connection.execute(sa.insert(metadata.tables["ai_request"]).values(**values))


def _rejects(connection: Connection, values: dict[str, Any]) -> None:
    savepoint = connection.begin_nested()
    with pytest.raises(IntegrityError):
        _insert_request(connection, values)
    savepoint.rollback()


def test_new_relations_have_migration_role_owner_and_expected_grants(engine: Engine) -> None:
    def privilege(role: str, table: str, operation: str) -> bool:
        with engine.connect() as connection:
            return bool(
                connection.scalar(
                    sa.text("SELECT has_table_privilege(:role, :table, :operation)"),
                    {"role": role, "table": f"public.{table}", "operation": operation},
                )
            )

    with engine.connect() as connection:
        owners = dict(
            connection.execute(
                sa.text(
                    "SELECT relname, pg_get_userbyid(relowner) FROM pg_class "
                    "WHERE relnamespace = 'public'::regnamespace "
                    "AND relname IN ('ai_request', 'ai_worker_heartbeat', 'ai_line_opinion')"
                )
            ).all()
        )
    assert owners == dict.fromkeys(
        ("ai_request", "ai_worker_heartbeat", "ai_line_opinion"), "reconbench_migrator"
    )

    for table in ("ai_request", "ai_worker_heartbeat"):
        for operation in ("SELECT", "INSERT", "UPDATE", "DELETE"):
            assert privilege("reconbench_app", table, operation)
        assert privilege("reconbench_report", table, "SELECT")
        assert not privilege("reconbench_retention", table, "SELECT")

    assert privilege("reconbench_app", "ai_line_opinion", "SELECT")
    assert privilege("reconbench_app", "ai_line_opinion", "INSERT")
    for operation in ("UPDATE", "DELETE", "TRUNCATE"):
        assert not privilege("reconbench_app", "ai_line_opinion", operation)
    assert not privilege("reconbench_report", "ai_line_opinion", "SELECT")

    assert not privilege("reconbench_report", "ai_cache", "SELECT")
    assert privilege("reconbench_retention", "ai_cache", "SELECT")
    assert privilege("reconbench_retention", "ai_cache", "DELETE")
    assert not privilege("reconbench_report", "secret", "SELECT")


@pytest.mark.parametrize(
    ("role", "statement"),
    [
        ("reconbench_report", "SELECT response FROM public.ai_cache WHERE false"),
        ("reconbench_report", "SELECT reason FROM public.ai_line_opinion WHERE false"),
        ("reconbench_app", "UPDATE public.ai_line_opinion SET reason = reason WHERE false"),
        ("reconbench_app", "DELETE FROM public.ai_line_opinion WHERE false"),
        ("reconbench_app", "TRUNCATE public.ai_line_opinion"),
    ],
)
def test_group_roles_cannot_bypass_new_restrictions(
    engine: Engine, role: str, statement: str
) -> None:
    with engine.connect() as connection:
        transaction = connection.begin()
        connection.exec_driver_sql(f"SET LOCAL ROLE {role}")
        with pytest.raises(DBAPIError):
            connection.exec_driver_sql(statement)
        transaction.rollback()


def test_ai_line_opinion_is_append_only_even_for_the_owner(
    connection: Connection, seed: tuple[UUID, UUID]
) -> None:
    workspace_id, _user_id = seed
    opinion_id = new_id()
    # Referenced rows are irrelevant to the trigger; replica mode skips FK checks
    # for this one synthetic row, then normal trigger firing is restored.
    connection.exec_driver_sql("SET LOCAL session_replication_role = replica")
    connection.execute(
        sa.insert(metadata.tables["ai_line_opinion"]).values(
            id=opinion_id,
            workspace_id=workspace_id,
            run_id=new_id(),
            recon_line_result_id=new_id(),
            invoice_line_id=new_id(),
            po_doc_line_id=new_id(),
            ai_request_id=new_id(),
            ai_call_id=new_id(),
            opinion="same",
            reason="Both lines describe the same synthetic part.",
            model="gemini-3.8-flash",
            prompt_version="h2.v1",
        )
    )
    connection.exec_driver_sql("SET LOCAL session_replication_role = origin")
    for statement in (
        "UPDATE public.ai_line_opinion SET reason = 'changed' WHERE id = :id",
        "DELETE FROM public.ai_line_opinion WHERE id = :id",
    ):
        savepoint = connection.begin_nested()
        with pytest.raises(DBAPIError, match="append-only"):
            connection.execute(sa.text(statement), {"id": opinion_id})
        savepoint.rollback()
    savepoint = connection.begin_nested()
    with pytest.raises(DBAPIError, match="append-only"):
        connection.exec_driver_sql("TRUNCATE public.ai_line_opinion")
    savepoint.rollback()


def test_ai_line_opinion_reason_and_line_constraints(
    connection: Connection, seed: tuple[UUID, UUID]
) -> None:
    workspace_id, _user_id = seed
    connection.exec_driver_sql("SET LOCAL session_replication_role = replica")
    line_id = new_id()
    base = {
        "workspace_id": workspace_id,
        "run_id": new_id(),
        "invoice_line_id": line_id,
        "po_doc_line_id": new_id(),
        "ai_request_id": new_id(),
        "ai_call_id": new_id(),
        "opinion": "unsure",
        "model": "m",
        "prompt_version": "h2.v1",
    }
    table = metadata.tables["ai_line_opinion"]
    for overrides in (
        {"reason": "x" * 301},
        {"reason": ""},
        {"reason": "ok", "po_doc_line_id": line_id},
    ):
        savepoint = connection.begin_nested()
        with pytest.raises(IntegrityError):
            connection.execute(
                sa.insert(table).values(
                    id=new_id(), recon_line_result_id=new_id(), **{**base, **overrides}
                )
            )
        savepoint.rollback()
    result_id = new_id()
    connection.execute(
        sa.insert(table).values(
            id=new_id(), recon_line_result_id=result_id, reason="x" * 300, **base
        )
    )
    savepoint = connection.begin_nested()
    with pytest.raises(IntegrityError):
        connection.execute(
            sa.insert(table).values(id=new_id(), recon_line_result_id=result_id, reason="b", **base)
        )
    savepoint.rollback()


def test_ai_request_state_constraints(connection: Connection, seed: tuple[UUID, UUID]) -> None:
    workspace_id, user_id = seed
    _insert_request(connection, _request(workspace_id))
    invalid: list[dict[str, Any]] = [
        {"call_kind": "provider_test"},
        {"hook": None},
        {"hook": "h5_explain"},
        {"mode": "rules_only"},
        {"consent_fingerprint": None},
        {"subject_type": "fixture"},
        {"subject_type": "invoice_text"},
        {"attempt_no": 0},
        {"request_state": "connected"},
        {"apply_outcome": "auto_accepted"},
        {"request_state": "reserved"},
        {"request_state": "sending", "reserved_at": _NOW},
        {"request_state": "succeeded", "reserved_at": _NOW, "sent_at": _NOW},
        {"request_state": "skipped", "finished_at": _NOW},
        {
            "request_state": "failed",
            "reserved_at": _NOW,
            "sent_at": _NOW,
            "finished_at": _NOW,
        },
        {"apply_outcome": "applied"},
        {"reserved_input_tokens": -1},
        {"usage_known": True},
        {"reserved_usd": 1},
        {"acknowledged_by": user_id, "acknowledged_at": _NOW},
        {"acknowledged_at": _NOW},
        {"error_code": "e" * 65},
        {"model": ""},
    ]
    for index, overrides in enumerate(invalid):
        _rejects(
            connection,
            _request(workspace_id, request_key=_hash(f"invalid-{index}"), **overrides),
        )

    _insert_request(
        connection,
        _request(
            workspace_id,
            request_key=_hash("fixture"),
            call_kind="synthetic_smoke",
            hook=None,
            mode="rules_only",
            consent_fingerprint=None,
            subject_type="fixture",
            subject_id=None,
            subject_version="fixture-sha",
            requested_by=user_id,
            request_state="skipped",
            skip_code="EXTERNAL_CALLS_DISABLED",
            finished_at=_NOW,
            apply_outcome="not_applicable",
        ),
    )
    _insert_request(
        connection,
        _request(
            workspace_id,
            request_key=_hash("done"),
            request_state="succeeded",
            reserved_at=_NOW,
            sent_at=_NOW,
            finished_at=_NOW,
            usage_known=True,
            tokens_input=10,
            tokens_output=3,
            tokens_thought=2,
            reserved_usd=0.5,
            actual_usd=0.01,
            price_version="prices.v1",
            apply_outcome="applied",
        ),
    )


def test_one_live_attempt_per_request_until_uncertainty_is_acknowledged(
    connection: Connection, seed: tuple[UUID, UUID]
) -> None:
    workspace_id, user_id = seed
    key = _hash("live")
    first = _request(workspace_id, request_key=key)
    _insert_request(connection, first)
    _rejects(connection, _request(workspace_id, request_key=key, attempt_no=2))
    _rejects(connection, _request(workspace_id, request_key=key, attempt_no=1))

    requests = metadata.tables["ai_request"]
    connection.execute(
        sa.update(requests)
        .where(requests.c.id == first["id"])
        .values(
            request_state="uncertain",
            reserved_at=_NOW,
            sent_at=_NOW,
            finished_at=_NOW + timedelta(minutes=2),
            error_code="LEASE_EXPIRED",
        )
    )
    _rejects(connection, _request(workspace_id, request_key=key, attempt_no=2))
    connection.execute(
        sa.update(requests)
        .where(requests.c.id == first["id"])
        .values(acknowledged_by=user_id, acknowledged_at=_NOW + timedelta(minutes=5))
    )
    _insert_request(connection, _request(workspace_id, request_key=key, attempt_no=2))
    _rejects(connection, _request(workspace_id, request_key=key, attempt_no=3))


def test_ai_call_log_links_to_request_and_records_thought_tokens(
    connection: Connection, seed: tuple[UUID, UUID]
) -> None:
    workspace_id, _user_id = seed
    request = _request(workspace_id, request_key=_hash("call-log"))
    _insert_request(connection, request)
    call_log = metadata.tables["ai_call_log"]
    values = {
        "workspace_id": workspace_id,
        "hook": "h4_vendor",
        "provider": "gemini",
        "model": "gemini-3.8-flash",
        "prompt_version": "h4.v1",
        "input_hash": _hash("input"),
        "outcome": "ok",
        "redacted": True,
    }
    connection.execute(
        sa.insert(call_log).values(
            id=new_id(), ai_request_id=request["id"], tokens_thought=7, **values
        )
    )
    for overrides in ({"ai_request_id": new_id()}, {"tokens_thought": -1}):
        savepoint = connection.begin_nested()
        with pytest.raises(IntegrityError):
            connection.execute(sa.insert(call_log).values(id=new_id(), **values, **overrides))
        savepoint.rollback()


def test_session_only_keys_are_bound_to_the_saving_users_session(
    connection: Connection, seed: tuple[UUID, UUID]
) -> None:
    workspace_id, user_id = seed
    other_user = new_id()
    connection.execute(
        sa.insert(metadata.tables["app_user"]).values(
            id=other_user,
            workspace_id=workspace_id,
            email=f"other-{other_user}@example.invalid",
            display_name="Synthetic other user",
            password_hash="not-a-real-hash",
        )
    )
    session_id = new_id()
    connection.execute(
        sa.insert(metadata.tables["session"]).values(
            id=session_id,
            workspace_id=workspace_id,
            user_id=user_id,
            token_hash=_hash(f"token-{session_id}"),
            csrf_hash=_hash(f"csrf-{session_id}"),
            idle_expires_at=_NOW + timedelta(minutes=30),
            abs_expires_at=_NOW + timedelta(hours=12),
        )
    )
    secret = metadata.tables["secret"]
    base = {
        "workspace_id": workspace_id,
        "purpose": "ai_api_key",
        "ciphertext": b"sealed",
        "nonce": b"n" * 12,
        "key_id": "k1",
    }
    connection.execute(sa.insert(secret).values(id=new_id(), created_by=user_id, **base))
    connection.execute(
        sa.insert(secret).values(
            id=new_id(),
            created_by=user_id,
            session_id=session_id,
            expires_at=_NOW + timedelta(hours=12),
            **base,
        )
    )
    invalid: list[dict[str, Any]] = [
        {"created_by": None},
        {"created_by": user_id, "session_id": session_id},
        {"created_by": user_id, "expires_at": _NOW},
        {"created_by": other_user, "session_id": session_id, "expires_at": _NOW},
        {"created_by": user_id, "session_id": new_id(), "expires_at": _NOW},
    ]
    for overrides in invalid:
        savepoint = connection.begin_nested()
        with pytest.raises(IntegrityError):
            connection.execute(sa.insert(secret).values(id=new_id(), **base, **overrides))
        savepoint.rollback()


def test_heartbeat_is_a_single_ai_row(connection: Connection) -> None:
    heartbeat = metadata.tables["ai_worker_heartbeat"]
    connection.execute(sa.insert(heartbeat).values(worker_role="ai", instance="ai-worker-1"))
    for values in (
        {"worker_role": "ai", "instance": "ai-worker-2"},
        {"worker_role": "ingest", "instance": "w"},
        {"worker_role": "ai", "instance": ""},
    ):
        savepoint = connection.begin_nested()
        with pytest.raises(IntegrityError):
            connection.execute(sa.insert(heartbeat).values(**values))
        savepoint.rollback()


def test_ai_link_uniqueness_index_is_partial_and_unique(engine: Engine) -> None:
    with engine.connect() as connection:
        definition = connection.scalar(
            sa.text("SELECT indexdef FROM pg_indexes WHERE indexname = 'link_ai_call_uq'")
        )
    assert definition == (
        "CREATE UNIQUE INDEX link_ai_call_uq ON public.link USING btree (ai_call_id) "
        "WHERE (ai_call_id IS NOT NULL)"
    )


def test_revision_refuses_to_bind_pre_existing_secrets(
    connection: Connection, seed: tuple[UUID, UUID]
) -> None:
    from importlib import import_module

    revision = import_module("migrations.versions.0017_ai_assist_ledger")
    workspace_id, user_id = seed
    connection.exec_driver_sql(revision._SECRET_MUST_BE_EMPTY)
    connection.execute(
        sa.insert(metadata.tables["secret"]).values(
            id=new_id(),
            workspace_id=workspace_id,
            purpose="ai_api_key",
            ciphertext=b"legacy",
            nonce=b"n" * 12,
            key_id="legacy",
            created_by=user_id,
        )
    )
    savepoint = connection.begin_nested()
    with pytest.raises(DBAPIError, match="requires an empty secret table"):
        connection.exec_driver_sql(revision._SECRET_MUST_BE_EMPTY)
    savepoint.rollback()


def test_consent_requires_a_recorded_fingerprint(
    connection: Connection, seed: tuple[UUID, UUID]
) -> None:
    workspace_id, user_id = seed
    settings = metadata.tables["ai_setting"]
    connection.execute(sa.delete(settings).where(settings.c.workspace_id == workspace_id))
    consented = {
        "workspace_id": workspace_id,
        "mode": "ai_assisted",
        "provider": "gemini",
        "model": "gemini-3.8-flash",
        "consent_given": True,
        "consent_by": user_id,
        "consent_at": _NOW,
        "consent_text_version": "consent.v1",
    }
    for overrides in (
        {},
        {"consent_fingerprint": _hash("fp"), "consent_by": None},
        {"consent_fingerprint": _hash("fp"), "consent_text_version": None},
        {"consent_fingerprint": _hash("fp"), "consent_given": False},
    ):
        savepoint = connection.begin_nested()
        with pytest.raises(IntegrityError):
            connection.execute(
                sa.insert(settings).values(id=new_id(), **{**consented, **overrides})
            )
        savepoint.rollback()
    connection.execute(
        sa.insert(settings).values(id=new_id(), consent_fingerprint=_hash("fp"), **consented)
    )
