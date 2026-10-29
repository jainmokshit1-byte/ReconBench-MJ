"""Revision 0026 (separate AI providers, WI-BE3): backfill and constraints.

Clones the disposable database, rewinds the clone to revision 0025 by removing the
0027 and 0026 objects (the ``vllm`` enum value cannot be removed and is harmless: the
migration adds it with ``IF NOT EXISTS``), seeds legacy Gemini settings and runs
the real upgrade on the clone. The source database is never changed.
"""

from __future__ import annotations

import os
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
from reconbench_app.ai.prompts import prompt_version
from reconbench_app.ai.runtime_checks import load_settings
from reconbench_app.db.ids import new_id
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import IntegrityError

ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"
MODEL = "gemini-3.8-flash"

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
    # 0028 (provider connections, WI-PV1) is removed next; the upgrade re-applies it.
    "DROP TABLE provider_measurement",
    "DROP TABLE ai_endpoint_allow",
    "ALTER TABLE batch DROP COLUMN provider_override",
    "ALTER TABLE ai_setting DROP COLUMN extraction_provider, DROP COLUMN assist_provider, "
    "DROP COLUMN shadow_provider, DROP COLUMN allow_provider_fallback",
    # (the new ai_provider_config columns go with that table below)
    # 0027 (shadow comparison store, WI-BE6) is removed next; the upgrade re-applies it.
    "DROP TABLE reader_comparison",
    "ALTER TABLE ai_request DROP CONSTRAINT ai_request_shadow_ck, DROP COLUMN shadow",
    "DROP TABLE ai_provider_config",
    "ALTER TABLE ai_setting DROP CONSTRAINT ai_setting_reader_strategy_ck",
    "ALTER TABLE ai_setting DROP COLUMN reader_strategy, DROP COLUMN primary_reader, "
    "DROP COLUMN apply_mode",
    "DROP TYPE ai_reader_strategy",
    "DROP TYPE ai_apply_mode",
    "UPDATE alembic_version SET version_num = '0025'",
)


@pytest.fixture(scope="module")
def migrated() -> Iterator[tuple[Engine, dict[str, UUID], UUID]]:
    source = os.environ.get("RB_PHASE2_TEST_DATABASE_URL", "").strip()
    if not source:
        pytest.skip("RB_PHASE2_TEST_DATABASE_URL is required for disposable PostgreSQL tests")
    source_url = make_url(source)
    source_name = source_url.database or ""
    if not source_name.startswith("reconbench_phase2_"):
        raise RuntimeError("migration checks require a disposable reconbench_phase2_* database")
    clone_name = f"reconbench_phase2_m26_{uuid4().hex[:10]}"
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
            for label, mode, vision in (
                ("vision_on", "ai_first", True),
                ("vision_off", "ai_assisted", False),
                ("rules_only", "rules_only", False),
                ("no_settings", None, False),
            ):
                workspace_id = new_id()
                workspaces[label] = workspace_id
                connection.execute(
                    sa.text("INSERT INTO workspace (id, name) VALUES (:id, :name)"),
                    {"id": workspace_id, "name": f"Synthetic 0026 {label}"},
                )
                if mode is None:
                    continue
                consented = mode != "rules_only"
                connection.execute(
                    sa.text(
                        "INSERT INTO ai_setting (id, workspace_id, mode, provider, model, "
                        "consent_given, consent_fingerprint, consent_by, consent_at, "
                        "consent_text_version, vision_enabled, version) VALUES (:id, :w, "
                        "CAST(:mode AS ai_mode), 'gemini', :model, :given, :fp, :by, :at, "
                        ":text, :vision, 4)"
                    ),
                    {
                        "id": new_id(),
                        "w": workspace_id,
                        "mode": mode,
                        "model": MODEL,
                        "given": consented,
                        "fp": (
                            consent_fingerprint(
                                provider="gemini", model=MODEL, mode=mode, vision=vision
                            )
                            if consented
                            else None
                        ),
                        "by": user_id if consented else None,
                        "at": datetime(2026, 10, 1, tzinfo=UTC) if consented else None,
                        "text": "consent.v1" if consented else None,
                        "vision": vision,
                    },
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


def test_backfill_maps_legacy_settings_to_single_gemini_or_off(
    migrated: tuple[Engine, dict[str, UUID], UUID],
) -> None:
    engine, workspaces, _user = migrated
    with engine.connect() as connection:
        rows = {
            label: connection.execute(
                sa.text(
                    "SELECT s.reader_strategy, s.primary_reader, s.apply_mode, s.vision_enabled, "
                    "s.version, c.enabled, c.model, c.base_url, c.key_secret_id "
                    "FROM ai_setting s LEFT JOIN ai_provider_config c "
                    "ON c.workspace_id = s.workspace_id AND c.provider = 'gemini' "
                    "WHERE s.workspace_id = :w"
                ),
                {"w": workspace_id},
            ).one_or_none()
            for label, workspace_id in workspaces.items()
        }
        configs = connection.scalar(
            sa.text("SELECT count(*) FROM ai_provider_config WHERE workspace_id = ANY(:w)"),
            {"w": list(workspaces.values())},
        )
    assert tuple(rows["vision_on"] or ()) == (
        "single",
        "gemini",
        "suggest",
        True,
        4,
        True,
        MODEL,
        None,
        None,
    )
    assert tuple(rows["vision_off"] or ()) == (
        "off",
        None,
        "suggest",
        False,
        4,
        True,
        MODEL,
        None,
        None,
    )
    assert tuple(rows["rules_only"] or ())[:6] == ("off", None, "suggest", False, 4, False)
    assert rows["no_settings"] is None
    # One Gemini card per Gemini workspace; no vLLM card is invented.
    assert configs == 3


@pytest.mark.parametrize(("label", "vision"), [("vision_on", True), ("vision_off", False)])
def test_migrated_workspaces_keep_consent_and_send_the_same_requests(
    migrated: tuple[Engine, dict[str, UUID], UUID], label: str, vision: bool
) -> None:
    engine, workspaces, _user = migrated
    with engine.connect() as connection:
        settings = load_settings(connection, workspaces[label])
    assert settings is not None
    mode = "ai_first" if vision else "ai_assisted"
    # Byte-identical fingerprint: the stored legacy consent is still current.
    assert settings.consent_current is True
    assert settings.expected_fingerprint == consent_fingerprint(
        provider="gemini", model=MODEL, mode=mode, vision=vision
    )
    assert settings.hooks == hooks_for_mode(mode, vision=vision)
    assert settings.target("h6_vision") == ("gemini", MODEL)
    assert settings.target("h2_item") == ("gemini", MODEL)
    assert prompt_version("h6_vision", reader="vision_gemini") == prompt_version("h6_vision")


def test_new_constraints_hold(migrated: tuple[Engine, dict[str, UUID], UUID]) -> None:
    engine, workspaces, user_id = migrated
    statements = (
        (
            "UPDATE ai_setting SET reader_strategy = 'single', primary_reader = NULL "
            "WHERE workspace_id = :w"
        ),
        "UPDATE ai_setting SET primary_reader = 'vllm' WHERE workspace_id = :w",
        "UPDATE ai_setting SET apply_mode = 'dual_agree' WHERE workspace_id = :w",
        "UPDATE ai_provider_config SET base_url = 'http://x/v1' WHERE workspace_id = :w",
        (
            "INSERT INTO ai_provider_config (id, workspace_id, provider) "
            "VALUES (gen_random_uuid(), :w, 'gemini')"
        ),
        (
            "INSERT INTO ai_provider_config (id, workspace_id, provider, limits) "
            "VALUES (gen_random_uuid(), :w, 'vllm', '[]')"
        ),
    )
    for statement in statements:
        with (
            engine.connect() as connection,
            connection.begin(),
            pytest.raises((IntegrityError, sa.exc.DataError)),
        ):
            connection.execute(sa.text(statement), {"w": workspaces["vision_off"]})
    with engine.connect() as connection, connection.begin() as transaction:
        connection.execute(
            sa.text(
                "INSERT INTO ai_provider_config (id, workspace_id, provider, enabled, model, "
                "base_url, updated_by) VALUES (gen_random_uuid(), :w, 'vllm', true, "
                "'qwen3.5:9b', 'http://vllm.example.test/vllm/v1', :u)"
            ),
            {"w": workspaces["vision_off"], "u": user_id},
        )
        owner, report = connection.execute(
            sa.text(
                "SELECT pg_get_userbyid(relowner), "
                "has_table_privilege('reconbench_report', 'public.ai_provider_config', 'SELECT') "
                "FROM pg_class WHERE relname = 'ai_provider_config'"
            )
        ).one()
        transaction.rollback()
    assert owner == "reconbench_migrator" and report is False
