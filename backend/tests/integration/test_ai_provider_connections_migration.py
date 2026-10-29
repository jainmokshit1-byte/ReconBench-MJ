"""Revision 0028 (provider connections, WI-PV1): backfill, constraints, grants.

Clones the disposable database, rewinds the clone to revision 0027 by removing the 0028
objects, seeds legacy workspaces (made-up names and settings) and runs the real upgrade
on the clone. The source database is never changed and nothing here touches a live
database.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from reconbench_app.ai.consent import consent_fingerprint, hooks_for_mode
from reconbench_app.ai.runtime_checks import load_settings
from reconbench_app.db.ids import new_id
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import IntegrityError

ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"
MODEL = "gemini-3.5-flash-lite"
NEW_CONFIG_COLUMNS = (
    "connected_at",
    "last_error_code",
    "last_error_at",
    "key_rotated_at",
    "consent_fingerprint",
    "consent_accepted_by",
    "consent_accepted_at",
    "monthly_cost_cap_inr",
    "cap_warn_pct",
    "doc_cost_flag_inr",
    "auto_verify_field_types",
    "apply_mode",
    "breaker_state",
    "breaker_changed_at",
)

_REWIND = (
    # 0034 (provider capabilities, PD-1b) is removed first; the upgrade re-applies it.
    "ALTER TABLE ai_provider_config DROP CONSTRAINT ai_provider_config_context_window_ck, "
    "DROP COLUMN context_window, DROP COLUMN supports_json_schema, DROP COLUMN supports_vision",
    # 0033 (AI_VALUE_UNVERIFIED reason, PD-2b) is removed next; the upgrade re-applies it.
    "DELETE FROM reason_code WHERE code = 'AI_VALUE_UNVERIFIED'",
    # 0032 (AI field origin, PD-2a) is removed next; the upgrade re-applies it.
    "ALTER TABLE field_evidence DROP CONSTRAINT field_evidence_origin_ck, "
    "ADD CONSTRAINT field_evidence_origin_ck "
    "CHECK (origin IS NULL OR origin IN ('ocr', 'manual', 'erp_import'))",
    # 0031 (duplicate holds, PD-0b-4) is removed next; the upgrade re-applies it.
    "DROP TABLE duplicate_hold",
    # 0030 (last-manager lock, WI-PV3) is removed next; the upgrade re-applies it.
    "DROP TRIGGER app_user_last_manager ON app_user",
    "DROP FUNCTION app_user_last_manager_guard()",
    # 0029 (Gemini key move, key tier; WI-PV2) is removed next; the upgrade re-applies it.
    "ALTER TABLE ai_provider_config DROP CONSTRAINT ai_provider_config_pending_key_ck, "
    "DROP CONSTRAINT ai_provider_config_key_tier_provider_ck, "
    "DROP CONSTRAINT ai_provider_config_key_tier_ck, "
    "DROP COLUMN pending_key_secret_id, DROP COLUMN key_tier",
    "DROP TABLE provider_measurement",
    "DROP TABLE ai_endpoint_allow",
    "ALTER TABLE batch DROP COLUMN provider_override",
    "ALTER TABLE ai_setting DROP COLUMN extraction_provider, DROP COLUMN assist_provider, "
    "DROP COLUMN shadow_provider, DROP COLUMN allow_provider_fallback",
    "ALTER TABLE ai_provider_config "
    + ", ".join(f"DROP COLUMN {column}" for column in NEW_CONFIG_COLUMNS),
    "ALTER TABLE ai_provider_config ADD CONSTRAINT ai_provider_config_key_ck "
    "CHECK (provider = 'vllm' OR key_secret_id IS NULL)",
    "UPDATE alembic_version SET version_num = '0027'",
)

# label -> (mode, vision_enabled, reader_strategy, primary_reader, auto_verify)
_WORKSPACES = {
    "gemini_single": ("ai_first", True, "single", "gemini", ["doc_no"]),
    "gemini_assisted": ("ai_assisted", False, "off", None, []),
    "rules_only": ("rules_only", False, "off", None, []),
    "shadow_vllm": ("ai_first", False, "shadow", "vllm", []),
    "single_vllm": ("ai_assisted", True, "single", "vllm", []),
    "no_settings": None,
}


@pytest.fixture(scope="module")
def migrated() -> Iterator[tuple[Engine, dict[str, UUID], UUID]]:
    source = os.environ.get("RB_PHASE2_TEST_DATABASE_URL", "").strip()
    if not source:
        pytest.skip("RB_PHASE2_TEST_DATABASE_URL is required for disposable PostgreSQL tests")
    source_url = make_url(source)
    source_name = source_url.database or ""
    if not source_name.startswith("reconbench_phase2_"):
        raise RuntimeError("migration checks require a disposable reconbench_phase2_* database")
    clone_name = f"reconbench_phase2_m28_{uuid4().hex[:10]}"
    admin = sa.create_engine(source_url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.exec_driver_sql(f'CREATE DATABASE "{clone_name}" TEMPLATE "{source_name}"')
    clone = source_url.set(database=clone_name).render_as_string(hide_password=False)
    engine = sa.create_engine(clone)
    previous = os.environ.get("RB_MIGRATOR_DATABASE_URL")
    try:
        with engine.begin() as connection:
            heads = set(MigrationContext.configure(connection).get_current_heads())
            assert heads == {"0034"}, heads
            for statement in _REWIND:
                connection.exec_driver_sql(statement)
            user_id = connection.scalar(sa.text("SELECT id FROM app_user LIMIT 1"))
            workspaces: dict[str, UUID] = {}
            for label, spec in _WORKSPACES.items():
                workspace_id = new_id()
                workspaces[label] = workspace_id
                connection.execute(
                    sa.text("INSERT INTO workspace (id, name) VALUES (:id, :name)"),
                    {"id": workspace_id, "name": f"Synthetic 0028 {label}"},
                )
                if spec is None:
                    continue
                mode, vision, strategy, primary, auto_verify = spec
                consented = mode != "rules_only"
                connection.execute(
                    sa.text(
                        "INSERT INTO ai_setting (id, workspace_id, mode, provider, model, "
                        "consent_given, consent_fingerprint, consent_by, consent_at, "
                        "consent_text_version, vision_enabled, version, "
                        "auto_verify_field_types, reader_strategy, primary_reader) VALUES "
                        "(:id, :w, CAST(:mode AS ai_mode), 'gemini', :model, :given, :fp, :by, "
                        ":at, :text, :vision, 4, CAST(:auto AS text[]), "
                        "CAST(:strategy AS ai_reader_strategy), CAST(:primary AS ai_provider))"
                    ),
                    {
                        "id": new_id(),
                        "w": workspace_id,
                        "mode": mode,
                        "model": MODEL,
                        "given": consented,
                        "fp": (
                            consent_fingerprint(
                                provider="gemini",
                                model=MODEL,
                                mode=mode,
                                vision=vision,
                                auto_verify_field_types=tuple(auto_verify),
                            )
                            if consented
                            else None
                        ),
                        "by": user_id if consented else None,
                        "at": datetime(2026, 10, 1, tzinfo=UTC) if consented else None,
                        "text": "consent.v2" if auto_verify else "consent.v1",
                        "vision": vision,
                        "auto": "{" + ",".join(auto_verify) + "}",
                        "strategy": strategy,
                        "primary": primary,
                    },
                )
                # The 0026 backfill gave every Gemini workspace a card; vLLM cards exist
                # where the workspace had configured one.
                connection.execute(
                    sa.text(
                        "INSERT INTO ai_provider_config (id, workspace_id, provider, enabled, "
                        "model, updated_by) VALUES (gen_random_uuid(), :w, 'gemini', :enabled, "
                        ":model, :u)"
                    ),
                    {"w": workspace_id, "enabled": consented, "model": MODEL, "u": user_id},
                )
                if primary == "vllm":
                    connection.execute(
                        sa.text(
                            "INSERT INTO ai_provider_config (id, workspace_id, provider, enabled, "
                            "model, base_url, updated_by) VALUES (gen_random_uuid(), :w, 'vllm', "
                            "true, 'qwen3.5:9b', 'http://vllm.example.test/vllm/v1', :u)"
                        ),
                        {"w": workspace_id, "u": user_id},
                    )
        os.environ["RB_MIGRATOR_DATABASE_URL"] = clone
        command.upgrade(Config(str(ALEMBIC_INI)), "head")
        yield engine, workspaces, UUID(str(user_id))
    finally:
        if previous is None:
            os.environ.pop("RB_MIGRATOR_DATABASE_URL", None)
        else:
            os.environ["RB_MIGRATOR_DATABASE_URL"] = previous
        engine.dispose()
        with admin.connect() as connection:
            connection.exec_driver_sql(f'DROP DATABASE IF EXISTS "{clone_name}" WITH (FORCE)')
        admin.dispose()


def test_backfill_keeps_today_s_providers(
    migrated: tuple[Engine, dict[str, UUID], UUID],
) -> None:
    engine, workspaces, _user = migrated
    with engine.connect() as connection:
        rows = {
            label: connection.execute(
                sa.text(
                    "SELECT extraction_provider, assist_provider, shadow_provider, "
                    "allow_provider_fallback FROM ai_setting WHERE workspace_id = :w"
                ),
                {"w": workspace_id},
            ).one_or_none()
            for label, workspace_id in workspaces.items()
        }
    expected = {
        "gemini_single": ("gemini", "gemini", None, False),
        "gemini_assisted": ("gemini", "gemini", None, False),
        "rules_only": (None, None, None, False),
        # A reader that ran in shadow stays the shadow provider; H1 text stays on Gemini.
        "shadow_vllm": ("gemini", "gemini", "vllm", False),
        "single_vllm": ("vllm", "gemini", None, False),
    }
    for label, values in expected.items():
        assert tuple(rows[label] or ()) == values, label
    assert rows["no_settings"] is None


def test_cards_get_the_owner_cap_values_and_empty_lists(
    migrated: tuple[Engine, dict[str, UUID], UUID],
) -> None:
    engine, workspaces, _user = migrated
    with engine.connect() as connection:
        cards = connection.execute(
            sa.text(
                "SELECT provider::text, monthly_cost_cap_inr, cap_warn_pct, doc_cost_flag_inr, "
                "auto_verify_field_types, apply_mode::text, breaker_state, connected_at, "
                "last_error_code, key_rotated_at, consent_fingerprint, consent_accepted_by "
                "FROM ai_provider_config WHERE workspace_id = ANY(:w)"
            ),
            {"w": list(workspaces.values())},
        ).all()
    gemini = [row for row in cards if row[0] == "gemini"]
    vllm = [row for row in cards if row[0] == "vllm"]
    assert len(gemini) == 5 and len(vllm) == 2
    for row in gemini:
        assert (row[1], row[2], row[3]) == (2500, 80, 20)
    for row in vllm:
        assert (row[1], row[2], row[3]) == (None, 80, None)
    for row in cards:
        # Per-provider auto-verify starts empty; nothing is connected or consented yet.
        assert tuple(row[4]) == () and row[5] == "suggest" and row[6] == "closed"
        assert row[7:] == (None, None, None, None, None)


def test_legacy_workspace_settings_are_not_touched(
    migrated: tuple[Engine, dict[str, UUID], UUID],
) -> None:
    engine, workspaces, _user = migrated
    with engine.connect() as connection:
        legacy = connection.execute(
            sa.text(
                "SELECT auto_verify_field_types, reader_strategy::text, primary_reader::text, "
                "apply_mode::text, vision_enabled, version FROM ai_setting WHERE workspace_id = :w"
            ),
            {"w": workspaces["gemini_single"]},
        ).one()
    assert (tuple(legacy[0]), legacy[1], legacy[2], legacy[3], legacy[4], legacy[5]) == (
        ("doc_no",),
        "single",
        "gemini",
        "suggest",
        True,
        4,
    )


@pytest.mark.parametrize(("label", "vision"), [("gemini_assisted", False), ("gemini_single", True)])
def test_migrated_workspaces_keep_their_consent(
    migrated: tuple[Engine, dict[str, UUID], UUID], label: str, vision: bool
) -> None:
    engine, workspaces, _user = migrated
    with engine.connect() as connection:
        settings = load_settings(connection, workspaces[label])
    assert settings is not None
    mode = "ai_first" if vision else "ai_assisted"
    assert settings.consent_current is True
    assert settings.hooks == hooks_for_mode(mode, vision=vision)
    assert settings.target("h2_item") == ("gemini", MODEL)


def test_new_constraints_hold(migrated: tuple[Engine, dict[str, UUID], UUID]) -> None:
    engine, workspaces, user_id = migrated
    target = workspaces["gemini_assisted"]
    statements = (
        "UPDATE ai_provider_config SET monthly_cost_cap_inr = -1 WHERE workspace_id = :w",
        "UPDATE ai_provider_config SET doc_cost_flag_inr = -0.01 WHERE workspace_id = :w",
        "UPDATE ai_provider_config SET cap_warn_pct = 0 WHERE workspace_id = :w",
        "UPDATE ai_provider_config SET cap_warn_pct = 101 WHERE workspace_id = :w",
        (
            "UPDATE ai_provider_config SET auto_verify_field_types = '{description}' "
            "WHERE workspace_id = :w"
        ),
        "UPDATE ai_provider_config SET breaker_state = 'half_open' WHERE workspace_id = :w",
        "UPDATE ai_provider_config SET last_error_code = '' WHERE workspace_id = :w",
        # Consent is recorded as a whole or not at all.
        "UPDATE ai_provider_config SET consent_fingerprint = 'abc' WHERE workspace_id = :w",
        ("UPDATE ai_provider_config SET consent_accepted_at = now() WHERE workspace_id = :w"),
        "UPDATE batch SET provider_override = 'not_a_provider'",
        (
            "INSERT INTO ai_endpoint_allow (id, workspace_id, url, action) "
            "VALUES (gen_random_uuid(), :w, 'http://x/v1', 'change')"
        ),
        (
            "INSERT INTO ai_endpoint_allow (id, workspace_id, url, action) "
            "VALUES (gen_random_uuid(), :w, '', 'add')"
        ),
        (
            "INSERT INTO provider_measurement (id, workspace_id, provider, field_type, split, "
            "n, correct, wrong, report_hash, measured_on) VALUES (gen_random_uuid(), :w, "
            "'gemini', 'doc_no', 'heldout', 5, 4, 3, 'abcdef123456', current_date)"
        ),
        (
            "INSERT INTO provider_measurement (id, workspace_id, provider, field_type, split, "
            "n, correct, wrong, report_hash, measured_on) VALUES (gen_random_uuid(), :w, "
            "'gemini', 'doc_no', 'test', 5, 4, 1, 'abcdef123456', current_date)"
        ),
        (
            "INSERT INTO provider_measurement (id, workspace_id, provider, field_type, split, "
            "n, correct, wrong, report_hash, measured_on) VALUES (gen_random_uuid(), :w, "
            "'gemini', 'doc_no', 'dev', 5, 4, 1, 'not-a-hash', current_date)"
        ),
    )
    for statement in statements:
        with (
            engine.connect() as connection,
            connection.begin(),
            pytest.raises((IntegrityError, sa.exc.DataError)),
        ):
            connection.execute(sa.text(statement), {"w": target})
    # Valid values are accepted (and rolled back).
    with engine.connect() as connection, connection.begin() as transaction:
        connection.execute(
            sa.text(
                "UPDATE ai_provider_config SET monthly_cost_cap_inr = 3000, cap_warn_pct = 75, "
                "auto_verify_field_types = '{gstin,doc_no}', breaker_state = 'open', "
                "consent_fingerprint = 'abc', consent_accepted_at = now(), "
                "consent_accepted_by = :u, connected_at = now(), last_error_code = 'bad_key' "
                "WHERE workspace_id = :w AND provider = 'gemini'"
            ),
            {"w": target, "u": user_id},
        )
        connection.execute(
            sa.text(
                "INSERT INTO ai_endpoint_allow (id, workspace_id, url, action, added_by) "
                "VALUES (gen_random_uuid(), :w, 'http://vllm.example.test/vllm/v1', 'add', :u)"
            ),
            {"w": target, "u": user_id},
        )
        connection.execute(
            sa.text(
                "INSERT INTO provider_measurement (id, workspace_id, provider, field_type, "
                "split, n, correct, wrong, lower_bound, report_hash, epoch, measured_on, "
                "imported_by) VALUES (gen_random_uuid(), :w, 'gemini', 'doc_no', 'heldout', "
                "80, 80, 0, 0.9542, 'abcdef123456', 'epoch-1', current_date, :u)"
            ),
            {"w": target, "u": user_id},
        )
        transaction.rollback()


def test_the_gemini_key_may_now_sit_on_its_card(
    migrated: tuple[Engine, dict[str, UUID], UUID],
) -> None:
    engine, _workspaces, _user = migrated
    with engine.connect() as connection:
        names = set(
            connection.scalars(
                sa.text(
                    "SELECT conname FROM pg_constraint "
                    "WHERE conrelid = 'ai_provider_config'::regclass"
                )
            )
        )
    # The 0026 rule "only a vllm card may reference a key" is gone; WI-PV2 moves the key.
    assert "ai_provider_config_key_ck" not in names
    assert "ai_provider_config_base_url_ck" in names


def test_new_tables_are_append_only_with_the_right_grants(
    migrated: tuple[Engine, dict[str, UUID], UUID],
) -> None:
    engine, workspaces, user_id = migrated
    target = workspaces["gemini_assisted"]
    with engine.connect() as connection:
        for table in ("ai_endpoint_allow", "provider_measurement"):
            owner, app_insert, app_update, app_delete, report = connection.execute(
                sa.text(
                    "SELECT pg_get_userbyid(c.relowner), "
                    "has_table_privilege('reconbench_app', c.oid, 'INSERT'), "
                    "has_table_privilege('reconbench_app', c.oid, 'UPDATE'), "
                    "has_table_privilege('reconbench_app', c.oid, 'DELETE'), "
                    "has_table_privilege('reconbench_report', c.oid, 'SELECT') "
                    "FROM pg_class c WHERE c.relname = :t"
                ),
                {"t": table},
            ).one()
            assert owner == "reconbench_migrator", table
            assert (app_insert, app_update, app_delete, report) == (True, False, False, False), (
                table
            )
    # The mutation guard also stops the owner: rows are never changed or removed.
    with engine.connect() as connection, connection.begin() as transaction:
        connection.execute(
            sa.text(
                "INSERT INTO ai_endpoint_allow (id, workspace_id, url, action, added_by) "
                "VALUES (gen_random_uuid(), :w, 'http://vllm.example.test/vllm/v1', 'add', :u)"
            ),
            {"w": target, "u": user_id},
        )
        for statement in (
            "UPDATE ai_endpoint_allow SET action = 'remove'",
            "DELETE FROM ai_endpoint_allow",
        ):
            with pytest.raises(sa.exc.DBAPIError), connection.begin_nested():
                connection.execute(sa.text(statement))
        transaction.rollback()


def test_batch_override_defaults_to_none_and_takes_a_provider(
    migrated: tuple[Engine, dict[str, UUID], UUID],
) -> None:
    engine, workspaces, user_id = migrated
    with engine.connect() as connection, connection.begin() as transaction:
        connection.execute(
            sa.text("INSERT INTO batch (id, workspace_id, created_by) VALUES (:id, :w, :u)"),
            {"id": new_id(), "w": workspaces["gemini_assisted"], "u": user_id},
        )
        value = connection.scalar(
            sa.text("SELECT provider_override FROM batch WHERE workspace_id = :w"),
            {"w": workspaces["gemini_assisted"]},
        )
        assert value is None
        connection.execute(
            sa.text("UPDATE batch SET provider_override = 'vllm' WHERE workspace_id = :w"),
            {"w": workspaces["gemini_assisted"]},
        )
        transaction.rollback()


def test_no_new_column_can_hold_key_material(
    migrated: tuple[Engine, dict[str, UUID], UUID],
) -> None:
    engine, _workspaces, _user = migrated
    pattern = re.compile(r"(api_?key|secret|token|password|credential)", re.IGNORECASE)
    with engine.connect() as connection:
        new_columns = connection.execute(
            sa.text(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND ("
                " (table_name = 'ai_provider_config' AND column_name = ANY(:config)) "
                " OR table_name IN ('ai_endpoint_allow', 'provider_measurement') "
                " OR (table_name = 'ai_setting' AND column_name IN "
                "('extraction_provider', 'assist_provider', 'shadow_provider', "
                "'allow_provider_fallback')) "
                " OR (table_name = 'batch' AND column_name = 'provider_override'))"
            ),
            {"config": list(NEW_CONFIG_COLUMNS)},
        ).all()
    assert len(new_columns) >= 14 + 7 + 12
    assert [row for row in new_columns if pattern.search(row[1])] == []
