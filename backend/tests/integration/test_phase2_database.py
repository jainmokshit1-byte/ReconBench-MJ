"""Phase 2 PostgreSQL checks; requires a named, disposable PostgreSQL 16 database."""

from __future__ import annotations

import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from uuid import UUID

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from reconbench_app.db.ids import new_id
from reconbench_app.db.metadata import metadata
from reconbench_app.db.repository import (
    ScopeNotFound,
    VersionConflict,
    apply_dirty_scope_change,
    update_versioned,
    workspace_filter,
)
from reconbench_app.db.seed import canonical_hash
from reconbench_app.db.seed_data import DEFAULT_CONFIG, REASON_CODES
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

_APPEND_ONLY = (
    "document_version",
    "doc_line",
    "field_evidence",
    "link",
    "config_version",
    "recon_case",
    "recon_line_result",
    "run_manifest",
    "case_action",
    "audit_log",
)
_ZERO_HASH = "0" * 64


@pytest.fixture(scope="session")
def phase2_engine() -> Engine:
    dsn = os.environ.get("RB_PHASE2_TEST_DATABASE_URL", "").strip()
    if not dsn:
        pytest.skip("RB_PHASE2_TEST_DATABASE_URL is required for disposable PostgreSQL tests")
    url = make_url(dsn)
    if not url.database or not url.database.startswith("reconbench_phase2_"):
        raise RuntimeError(
            "database checks require a disposable database named reconbench_phase2_*"
        )
    engine = sa.create_engine(dsn, pool_size=12, max_overflow=12)
    with engine.connect() as connection:
        version_num = int(connection.scalar(sa.text("SHOW server_version_num")))
        if version_num // 10_000 != 16:
            raise RuntimeError("Phase 2 database checks require PostgreSQL 16")
        expected_heads = set(ScriptDirectory.from_config(Config("backend/alembic.ini")).get_heads())
        current_heads = set(MigrationContext.configure(connection).get_current_heads())
        if expected_heads != current_heads:
            raise RuntimeError(
                "database must be migrated to head before integration tests: "
                f"{current_heads} != {expected_heads}"
            )
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def seed_ids(phase2_engine: Engine) -> tuple[UUID, UUID, UUID]:
    workspace = metadata.tables["workspace"]
    app_user = metadata.tables["app_user"]
    config_version = metadata.tables["config_version"]
    with phase2_engine.begin() as connection:
        workspace_id = connection.scalar(sa.select(workspace.c.id).limit(1))
        if workspace_id is None:
            workspace_id = new_id()
            connection.execute(workspace.insert().values(id=workspace_id, name="Synthetic tests"))
        user_id = connection.scalar(
            sa.select(app_user.c.id).where(app_user.c.workspace_id == workspace_id).limit(1)
        )
        if user_id is None:
            user_id = new_id()
            connection.execute(
                app_user.insert().values(
                    id=user_id,
                    workspace_id=workspace_id,
                    email=f"phase2-{user_id}@example.invalid",
                    display_name="Synthetic Test User",
                    role="manager",
                    password_hash="$argon2id$v=19$m=65536,t=3,p=4$c3ludGhldGlj$bm90LWF1dGhlbnRpY2F0ZWQ",
                )
            )
        config_id = connection.scalar(
            sa.select(config_version.c.id)
            .where(config_version.c.workspace_id == workspace_id, config_version.c.seq == 1)
            .limit(1)
        )
        if config_id is None:
            config_id = new_id()
            connection.execute(
                config_version.insert().values(
                    id=config_id,
                    workspace_id=workspace_id,
                    seq=1,
                    body={"engine": {}, "app": {}},
                    engine_hash=_ZERO_HASH,
                    body_hash=_ZERO_HASH,
                )
            )
    return workspace_id, user_id, config_id


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _add_document(connection: sa.Connection, workspace_id: UUID, doc_type: str) -> UUID:
    table = metadata.tables["document"]
    document_id = new_id()
    connection.execute(
        table.insert().values(
            id=document_id,
            workspace_id=workspace_id,
            doc_type=doc_type,
            natural_key=_digest(str(document_id)),
        )
    )
    return document_id


def _expect_constraint(connection: sa.Connection, statement: sa.Executable) -> None:
    with pytest.raises(IntegrityError), connection.begin_nested():
        connection.execute(statement)


def test_repository_constraints_roles_and_audit_chain(
    phase2_engine: Engine,
    seed_ids: tuple[UUID, UUID, UUID],
) -> None:
    workspace_id, user_id, config_id = seed_ids
    vendor_table = metadata.tables["vendor"]
    workspace_table = metadata.tables["workspace"]

    with phase2_engine.connect() as connection:
        config_version = metadata.tables["config_version"]
        seeded_config = connection.execute(
            sa.select(config_version.c.engine_hash, config_version.c.body_hash).where(
                config_version.c.workspace_id == workspace_id,
                config_version.c.seq == 1,
            )
        ).one()
        assert seeded_config.engine_hash == canonical_hash(DEFAULT_CONFIG["engine"])
        assert seeded_config.body_hash == canonical_hash(DEFAULT_CONFIG)
        assert DEFAULT_CONFIG["engine"]["tolerances"]["limit_mode"] == "or"
        assert [row["rate"] for row in DEFAULT_CONFIG["engine"]["gst_slabs"]] == [
            "5",
            "12",
            "18",
            "28",
        ]
        active_id = connection.scalar(
            sa.select(metadata.tables["config_active"].c.config_version_id).where(
                metadata.tables["config_active"].c.workspace_id == workspace_id
            )
        )
        assert active_id == config_id
        assert connection.scalar(
            sa.select(sa.func.count())
            .select_from(metadata.tables["reason_code"])
            .where(metadata.tables["reason_code"].c.workspace_id == workspace_id)
        ) == len(REASON_CODES)

    vendor_id = new_id()
    with phase2_engine.begin() as connection:
        connection.execute(
            vendor_table.insert().values(
                id=vendor_id,
                workspace_id=workspace_id,
                canonical_name="Synthetic Vendor",
                normalized_name="synthetic vendor",
                anchor="name",
            )
        )

    # Workspace isolation and optimistic concurrency are always applied in the helper.
    second_workspace = new_id()
    other_vendor = new_id()
    with phase2_engine.begin() as connection:
        connection.execute(
            workspace_table.insert().values(id=second_workspace, name="Other synthetic tenant")
        )
        connection.execute(
            vendor_table.insert().values(
                id=other_vendor,
                workspace_id=second_workspace,
                canonical_name="Other Vendor",
                normalized_name="other vendor",
                anchor="name",
            )
        )
    with Session(phase2_engine) as session:
        visible = (
            session.execute(
                workspace_filter(sa.select(vendor_table.c.id), vendor_table, workspace_id)
            )
            .scalars()
            .all()
        )
        assert vendor_id in visible and other_vendor not in visible
        session.commit()
        with session.begin():
            updated = update_versioned(
                session,
                vendor_table,
                workspace_id=workspace_id,
                row_id=vendor_id,
                if_match='"1"',
                values={"canonical_name": "Synthetic Vendor Updated"},
            )
            assert updated["version"] == 2
        with session.begin():
            with pytest.raises(VersionConflict) as conflict:
                update_versioned(
                    session,
                    vendor_table,
                    workspace_id=workspace_id,
                    row_id=vendor_id,
                    if_match='"1"',
                    values={"canonical_name": "Stale Write"},
                )
            assert conflict.value.current is not None
            assert conflict.value.current["version"] == 2
            assert conflict.value.current["canonical_name"] == "Synthetic Vendor Updated"
        with pytest.raises(VersionConflict) as wrong_workspace, session.begin():
            update_versioned(
                session,
                vendor_table,
                workspace_id=second_workspace,
                row_id=vendor_id,
                if_match='"2"',
                values={"canonical_name": "Cross Workspace"},
            )
        assert wrong_workspace.value.current is None

    def mutate_vendor(session: Session) -> str:
        session.execute(
            sa.update(vendor_table)
            .where(vendor_table.c.workspace_id == workspace_id, vendor_table.c.id == vendor_id)
            .values(canonical_name=sa.func.concat(vendor_table.c.canonical_name, "!"))
        )
        return "changed"

    for _ in range(2):
        with Session(phase2_engine) as session:
            assert (
                apply_dirty_scope_change(
                    session,
                    workspace_id=workspace_id,
                    scope_kind="vendor",
                    scope_id=vendor_id,
                    reason="synthetic_change",
                    change=mutate_vendor,
                )
                == "changed"
            )
    with phase2_engine.connect() as connection:
        assert (
            connection.scalar(
                sa.select(vendor_table.c.input_seq).where(
                    vendor_table.c.workspace_id == workspace_id, vendor_table.c.id == vendor_id
                )
            )
            == 2
        )
        dirty = metadata.tables["dirty_scope"]
        mark = connection.execute(
            sa.select(dirty.c.mark_count).where(
                dirty.c.workspace_id == workspace_id,
                dirty.c.scope_kind == "vendor",
                dirty.c.scope_id == vendor_id,
                dirty.c.processed_at.is_(None),
            )
        ).scalar_one()
        assert mark == 2

    # A failed scope bump rolls the caller's mutation back with the transaction.
    with Session(phase2_engine) as session, pytest.raises(ScopeNotFound):
        apply_dirty_scope_change(
            session,
            workspace_id=workspace_id,
            scope_kind="po",
            scope_id=vendor_id,
            reason="wrong_scope_type",
            change=mutate_vendor,
        )

    # Constraint invariants: self merge, one current link, one current case,
    # reasoned approvals, AI consent, and the case-action overlay projection.
    with phase2_engine.begin() as connection:
        self_merge_id = new_id()
        _expect_constraint(
            connection,
            vendor_table.insert().values(
                id=self_merge_id,
                workspace_id=workspace_id,
                canonical_name="Self merge",
                normalized_name="self merge",
                anchor="name",
                merged_into=self_merge_id,
            ),
        )
        invoice_id = _add_document(connection, workspace_id, "invoice")
        po_id = _add_document(connection, workspace_id, "po")
        link_table = metadata.tables["link"]
        link_id = new_id()
        connection.execute(
            link_table.insert().values(
                id=link_id,
                workspace_id=workspace_id,
                link_type="inv_po",
                level="header",
                from_document_id=invoice_id,
                to_document_id=po_id,
                version_no=1,
                method="labeled",
                confidence=0.99,
                state="auto",
            )
        )
        link_current = metadata.tables["link_current"]
        logical_link = {
            "workspace_id": workspace_id,
            "link_type": "inv_po",
            "from_document_id": invoice_id,
            "from_line_no": None,
            "to_document_id": po_id,
            "current_link_id": link_id,
        }
        connection.execute(link_current.insert().values(id=new_id(), **logical_link))
        _expect_constraint(
            connection,
            link_current.insert().values(id=new_id(), **logical_link),
        )

        source_file = metadata.tables["source_file"]
        source_file_id = new_id()
        connection.execute(
            source_file.insert().values(
                id=source_file_id,
                workspace_id=workspace_id,
                sha256=_digest(str(source_file_id)),
                display_name="synthetic-invoice.pdf",
                zone="invoice",
                zone_uploaded="invoice",
                format="pdf",
                size_bytes=8,
                object_key=f"synthetic/{source_file_id}",
            )
        )
        document_version = metadata.tables["document_version"]
        invoice_version_id = new_id()
        connection.execute(
            document_version.insert().values(
                id=invoice_version_id,
                workspace_id=workspace_id,
                document_id=invoice_id,
                seq=1,
                content_hash=_digest(str(invoice_version_id)),
                extraction_method="pdf_text",
                source_file_id=source_file_id,
                parser_version="synthetic-test",
            )
        )
        recon_run = metadata.tables["recon_run"]
        run_id = new_id()
        connection.execute(
            recon_run.insert().values(
                id=run_id,
                workspace_id=workspace_id,
                scope={},
                scope_hash=_digest("synthetic-scope"),
                run_date=date(2026, 1, 1),
                engine_version="synthetic-test",
                rules_hash=_ZERO_HASH,
                config_version_id=config_id,
            )
        )
        recon_case = metadata.tables["recon_case"]
        case_id = new_id()
        connection.execute(
            recon_case.insert().values(
                id=case_id,
                workspace_id=workspace_id,
                run_id=run_id,
                invoice_id=invoice_id,
                invoice_version_id=invoice_version_id,
                status="matched",
                inputs={},
                input_fingerprint=_digest("synthetic-case"),
            )
        )
        case_current = metadata.tables["case_current"]
        current_case_id = new_id()
        current_values = {
            "workspace_id": workspace_id,
            "invoice_id": invoice_id,
            "case_id": case_id,
            "run_id": run_id,
            "computed_status": "matched",
            "effective_status": "matched",
            "severity_rank": 7,
        }
        connection.execute(case_current.insert().values(id=current_case_id, **current_values))
        _expect_constraint(
            connection,
            case_current.insert().values(id=new_id(), **current_values),
        )
        case_action = metadata.tables["case_action"]
        action_base = {
            "workspace_id": workspace_id,
            "invoice_id": invoice_id,
            "case_id": case_id,
            "actor_id": user_id,
            "actor_role": "manager",
            "input_fingerprint": _digest("synthetic-case"),
        }
        _expect_constraint(
            connection,
            case_action.insert().values(id=new_id(), action="approve", reason=" ", **action_base),
        )
        for action, expected_overlay in (
            ("approve", "approved"),
            ("hold", "on_hold"),
            ("release", None),
        ):
            connection.execute(
                case_action.insert().values(
                    id=new_id(),
                    action=action,
                    reason="synthetic review" if action == "approve" else None,
                    **action_base,
                )
            )
            overlay, effective = connection.execute(
                sa.select(case_current.c.overlay, case_current.c.effective_status).where(
                    case_current.c.id == current_case_id
                )
            ).one()
            assert overlay == expected_overlay
            assert effective == (expected_overlay or "matched")

        ai_setting = metadata.tables["ai_setting"]
        _expect_constraint(
            connection,
            ai_setting.insert().values(
                id=new_id(), workspace_id=workspace_id, mode="ai_assisted", consent_given=False
            ),
        )

    # A future table created by the migrator inherits the intended app/report ACLs.
    with phase2_engine.begin() as connection:
        connection.exec_driver_sql("SET LOCAL ROLE reconbench_migrator")
        connection.exec_driver_sql(
            "CREATE TABLE public.phase2_default_grant_probe "
            "(id uuid primary key, workspace_id uuid not null)"
        )
        assert connection.scalar(
            sa.text(
                "SELECT has_table_privilege("
                "'reconbench_app', 'public.phase2_default_grant_probe', "
                "'SELECT,INSERT,UPDATE,DELETE')"
            )
        )
        assert (
            connection.scalar(
                sa.text(
                    "SELECT has_table_privilege("
                    "'reconbench_app', 'public.phase2_default_grant_probe', 'TRUNCATE')"
                )
            )
            is False
        )
        assert connection.scalar(
            sa.text(
                "SELECT has_table_privilege("
                "'reconbench_report', 'public.phase2_default_grant_probe', 'SELECT')"
            )
        )
        assert (
            connection.scalar(
                sa.text(
                    "SELECT has_table_privilege("
                    "'reconbench_report', 'public.phase2_default_grant_probe', 'INSERT')"
                )
            )
            is False
        )
        connection.exec_driver_sql("DROP TABLE public.phase2_default_grant_probe")

    with phase2_engine.connect() as connection:
        role_names = set(
            connection.scalars(
                sa.text(
                    "SELECT rolname FROM pg_roles WHERE rolname IN ("
                    "'reconbench_migrator', 'reconbench_app', 'reconbench_report', "
                    "'reconbench_retention')"
                )
            ).all()
        )
        assert role_names == {
            "reconbench_migrator",
            "reconbench_app",
            "reconbench_report",
            "reconbench_retention",
        }
        assert connection.scalar(
            sa.text(
                "SELECT bool_and(NOT rolcanlogin) FROM pg_roles WHERE rolname IN ("
                "'reconbench_migrator', 'reconbench_app', 'reconbench_report', "
                "'reconbench_retention')"
            )
        )
        owned_relations = {
            row["relname"]
            for row in connection.execute(
                sa.text(
                    "SELECT relation.relname, role.rolname AS owner "
                    "FROM pg_class relation "
                    "JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace "
                    "JOIN pg_roles role ON role.oid = relation.relowner "
                    "WHERE namespace.nspname = 'public' "
                    "AND relation.relkind IN ('r','p','v','m','S')"
                )
            ).mappings()
            if row["owner"] == "reconbench_migrator"
        }
        expected_owned_relations = set(metadata.tables) | {
            "alembic_version",
            "audit_log_seq",
            "v_case_current",
            "v_grni_now",
            "v_run_summary",
            "v_vendor_link_health",
        }
        assert expected_owned_relations <= owned_relations
        expected_type_names = {
            column.type.name
            for table in metadata.tables.values()
            for column in table.columns
            if isinstance(column.type, (postgresql.ENUM, postgresql.DOMAIN))
        }
        type_owners = {
            row["typname"]: row["owner"]
            for row in connection.execute(
                sa.text(
                    "SELECT type.typname, role.rolname AS owner "
                    "FROM pg_type type "
                    "JOIN pg_namespace namespace ON namespace.oid = type.typnamespace "
                    "JOIN pg_roles role ON role.oid = type.typowner "
                    "WHERE namespace.nspname = 'public' AND type.typtype IN ('e','d') "
                    "AND type.typname = ANY(:expected_type_names)"
                ),
                {"expected_type_names": sorted(expected_type_names)},
            ).mappings()
        }
        assert set(type_owners) == expected_type_names
        assert all(owner == "reconbench_migrator" for owner in type_owners.values())
        trigger_names = {
            f"{row['table_name']}.{row['trigger_name']}"
            for row in connection.execute(
                sa.text(
                    "SELECT table_class.relname AS table_name, trigger.tgname AS trigger_name "
                    "FROM pg_trigger trigger "
                    "JOIN pg_class table_class ON table_class.oid = trigger.tgrelid "
                    "JOIN pg_namespace namespace ON namespace.oid = table_class.relnamespace "
                    "WHERE namespace.nspname = 'public' AND NOT trigger.tgisinternal"
                )
            ).mappings()
        }
        expected_triggers = {
            f"{table}.{table}{suffix}"
            for table in _APPEND_ONLY
            for suffix in ("_append_only", "_append_only_truncate")
        }
        assert expected_triggers <= trigger_names
        for table_name in _APPEND_ONLY:
            assert connection.scalar(
                sa.text(
                    "SELECT has_table_privilege('reconbench_app', :table_name, 'SELECT,INSERT')"
                ).bindparams(table_name=f"public.{table_name}")
            )
            for operation in ("UPDATE", "DELETE", "TRUNCATE"):
                assert (
                    connection.scalar(
                        sa.text(
                            "SELECT has_table_privilege('reconbench_app', :table_name, :operation)"
                        ).bindparams(table_name=f"public.{table_name}", operation=operation)
                    )
                    is False
                )
        for table_name in ("secret", "session", "rate_limit_event"):
            assert (
                connection.scalar(
                    sa.text(
                        "SELECT has_table_privilege('reconbench_report', :table_name, 'SELECT')"
                    ).bindparams(table_name=f"public.{table_name}")
                )
                is False
            )
        assert (
            connection.scalar(
                sa.text(
                    "SELECT has_column_privilege("
                    "'reconbench_report', 'public.app_user', 'password_hash', 'SELECT')"
                )
            )
            is False
        )

    # Privilege checks run as the actual group roles and fail before touching rows.
    for table_name in _APPEND_ONLY:
        for statement in (
            f'UPDATE public."{table_name}" SET id = id WHERE false',
            f'DELETE FROM public."{table_name}" WHERE false',
            f'TRUNCATE public."{table_name}"',
        ):
            with phase2_engine.connect() as connection:
                transaction = connection.begin()
                connection.exec_driver_sql("SET LOCAL ROLE reconbench_app")
                with pytest.raises(DBAPIError):
                    connection.exec_driver_sql(statement)
                transaction.rollback()

    for statement in (
        "SELECT secret FROM public.secret WHERE false",
        "SELECT id FROM public.session WHERE false",
        "SELECT id FROM public.rate_limit_event WHERE false",
        "SELECT password_hash FROM public.app_user WHERE false",
    ):
        with phase2_engine.connect() as connection:
            transaction = connection.begin()
            connection.exec_driver_sql("SET LOCAL ROLE reconbench_report")
            with pytest.raises(DBAPIError):
                connection.exec_driver_sql(statement)
            transaction.rollback()

    # Seed concurrent audit rows. The workspace advisory lock must keep this
    # workspace's hash chain ordered even though seq is allocated cluster-wide
    # and can therefore have gaps when other workspaces write audit events.
    with phase2_engine.connect() as connection:
        previous = (
            connection.execute(
                sa.text(
                    "SELECT seq, row_hash FROM public.audit_log "
                    "WHERE workspace_id=:workspace ORDER BY seq DESC LIMIT 1"
                ),
                {"workspace": workspace_id},
            )
            .mappings()
            .first()
        )
        starting_seq = int(previous["seq"]) if previous else 0
        previous_hash = previous["row_hash"] if previous else None

    def add_audit_row(_: int) -> tuple[int, UUID]:
        audit = metadata.tables["audit_log"]
        row_id = new_id()
        with phase2_engine.begin() as connection:
            row = connection.execute(
                audit.insert()
                .values(
                    id=row_id,
                    workspace_id=workspace_id,
                    seq=0,
                    actor_id=user_id,
                    action="phase2.synthetic_test",
                    target_type="test",
                    occurred_at=datetime.now(UTC),
                    row_hash=_ZERO_HASH,
                )
                .returning(audit.c.seq, audit.c.id)
            ).one()
            return row.seq, row.id

    with ThreadPoolExecutor(max_workers=12) as executor:
        inserted = list(executor.map(add_audit_row, range(36)))
    assert len(inserted) == 36
    ids = [row_id for _, row_id in inserted]
    with phase2_engine.connect() as connection:
        audit_rows = (
            connection.execute(
                sa.text(
                    "SELECT seq, workspace_id, actor_id, action, target_type, target_id, "
                    "before_state::text AS before_text, after_state::text AS after_text, reason, "
                    "occurred_at, prev_hash, row_hash FROM public.audit_log "
                    "WHERE id = ANY(:ids) ORDER BY seq"
                ),
                {"ids": ids},
            )
            .mappings()
            .all()
        )
        assert len(audit_rows) == 36
    expected_previous = previous_hash
    expected_seq = starting_seq
    for row in audit_rows:
        assert row["seq"] > expected_seq
        expected_seq = row["seq"]
        assert row["prev_hash"] == expected_previous
        when = row["occurred_at"].astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        payload = "|".join(
            (
                expected_previous or "",
                str(row["seq"]),
                str(row["workspace_id"]),
                str(row["actor_id"] or ""),
                row["action"],
                row["target_type"],
                str(row["target_id"] or ""),
                row["before_text"] or "",
                row["after_text"] or "",
                row["reason"] or "",
                when,
            )
        )
        assert row["row_hash"] == hashlib.sha256(payload.encode("utf-8")).hexdigest()
        expected_previous = row["row_hash"]

    # Owners remain protected by row and statement triggers, including TRUNCATE.
    audit_id = audit_rows[0]["seq"]
    with phase2_engine.connect() as connection:
        transaction = connection.begin()
        connection.exec_driver_sql("SET LOCAL ROLE reconbench_migrator")
        for statement in (
            "UPDATE public.audit_log SET action = action WHERE seq = " + str(audit_id),
            "DELETE FROM public.audit_log WHERE seq = " + str(audit_id),
            "TRUNCATE public.audit_log",
        ):
            with pytest.raises(DBAPIError):
                connection.exec_driver_sql(statement)
            transaction.rollback()
            transaction = connection.begin()
            connection.exec_driver_sql("SET LOCAL ROLE reconbench_migrator")
        transaction.rollback()
