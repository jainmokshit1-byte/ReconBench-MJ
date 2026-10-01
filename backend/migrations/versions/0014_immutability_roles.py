"""8. Immutability guards and database roles migration, sourced from Backend Schema §8."""

# ruff: noqa: E501 -- preserve the validated SQL source verbatim.

from __future__ import annotations

from reconbench_app.db.migration_sql import execute_sql_block

revision = "0014"
down_revision = '0013'
branch_labels = None
depends_on = None

SQL_BLOCKS = (
    # Backend Schema source line 1952
    r"""CREATE FUNCTION forbid_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION '% on % is not allowed: rows are append-only', TG_OP, TG_TABLE_NAME
    USING ERRCODE = 'restrict_violation';
END $$;

DO $$
DECLARE t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY['document_version','doc_line','field_evidence','link','config_version',
                           'recon_case','recon_line_result','run_manifest','case_action','audit_log']
  LOOP
    EXECUTE format('CREATE TRIGGER %I BEFORE UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION forbid_mutation()',
                   t || '_append_only', t);
  END LOOP;
END $$;
""",

    # Backend Schema source line 1973
    r"""CREATE ROLE reconbench_migrator  NOLOGIN;   -- owns the objects; used by Alembic only
CREATE ROLE reconbench_app       NOLOGIN;   -- API and workers
CREATE ROLE reconbench_report    NOLOGIN;   -- read-only reporting
CREATE ROLE reconbench_retention NOLOGIN;   -- P1 retention job

GRANT USAGE ON SCHEMA public TO reconbench_app, reconbench_report, reconbench_retention;

-- Application: normal reads and writes ...
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO reconbench_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO reconbench_app;
-- ... except on the append-only tables (the application role cannot rewrite history).
REVOKE UPDATE, DELETE, TRUNCATE ON document_version, doc_line, field_evidence, link, config_version,
       recon_case, recon_line_result, run_manifest, case_action, audit_log FROM reconbench_app;

-- Reporting: read-only, and never credentials or encrypted keys.
GRANT SELECT ON ALL TABLES IN SCHEMA public TO reconbench_report;
REVOKE ALL ON secret, session, rate_limit_event FROM reconbench_report;
REVOKE SELECT ON app_user FROM reconbench_report;
GRANT SELECT (id, workspace_id, email, display_name, role, is_active, created_at) ON app_user TO reconbench_report;

-- Retention (P1): removes purged content only, and writes an audit row per deletion.
GRANT SELECT, DELETE ON document_text, ai_cache TO reconbench_retention;
GRANT SELECT ON source_file TO reconbench_retention;
GRANT UPDATE (object_purged_at) ON source_file TO reconbench_retention;
GRANT SELECT, INSERT ON audit_log TO reconbench_retention;
GRANT USAGE ON SEQUENCE audit_log_seq TO reconbench_retention;
""",
)


def upgrade() -> None:
    for sql in SQL_BLOCKS:
        execute_sql_block(sql)


def downgrade() -> None:
    raise NotImplementedError("ReconBench migrations are fixed-forward; redeploy the prior image")
