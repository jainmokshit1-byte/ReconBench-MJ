"""Default migration-role grants and statement-level append-only guards."""

from __future__ import annotations

from reconbench_app.db.migration_sql import execute_sql_block

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

_TABLES = (
    "ai_cache",
    "ai_call_log",
    "ai_setting",
    "app_user",
    "audit_log",
    "batch",
    "case_action",
    "case_current",
    "config_active",
    "config_version",
    "dirty_scope",
    "doc_line",
    "document",
    "document_text",
    "document_version",
    "document_warning",
    "export_file",
    "field_evidence",
    "idempotency_key",
    "layout_profile",
    "ledger_entry",
    "link",
    "link_current",
    "link_resolution",
    "opening_balance",
    "po_line_ledger",
    "processing_event",
    "rate_limit_event",
    "reason_code",
    "recon_case",
    "recon_line_result",
    "recon_run",
    "run_manifest",
    "secret",
    "session",
    "source_file",
    "vendor",
    "vendor_alias",
    "vendor_gstin",
    "vendor_merge_suggestion",
    "workspace",
    "alembic_version",
)
_VIEWS = ("v_case_current", "v_grni_now", "v_run_summary", "v_vendor_link_health")
_ENUMS = (
    "action_type",
    "ai_hook",
    "ai_key_storage",
    "ai_mode",
    "ai_opinion",
    "ai_outcome",
    "ai_provider",
    "alias_source",
    "alignment_state",
    "case_status",
    "doc_type",
    "export_status",
    "extraction_method",
    "field_source",
    "file_format",
    "file_status",
    "line_type",
    "link_level",
    "link_method",
    "link_state",
    "link_type",
    "mismatch_state",
    "overlay_type",
    "po_status",
    "processing_stage",
    "reason_level",
    "resolution_outcome",
    "run_status",
    "run_trigger",
    "scope_type",
    "stage_outcome",
    "suggestion_state",
    "upload_zone",
    "user_role",
    "vendor_anchor",
    "vendor_rule",
    "warn_level",
)
_DOMAINS = ("d_amount", "d_hash", "d_qty", "d_score", "d_taxrate")
_FUNCTIONS = (
    "ALTER FUNCTION public.audit_log_chain() OWNER TO reconbench_migrator;",
    "ALTER FUNCTION public.case_action_apply() OWNER TO reconbench_migrator;",
    "ALTER FUNCTION public.case_overlay(uuid) OWNER TO reconbench_migrator;",
    "ALTER FUNCTION public.forbid_mutation() OWNER TO reconbench_migrator;",
    "ALTER FUNCTION public.kpi_summary(uuid, date, date) OWNER TO reconbench_migrator;",
    "ALTER FUNCTION public.severity_rank(case_status) OWNER TO reconbench_migrator;",
)


def _ownership_sql() -> str:
    def array(values: tuple[str, ...]) -> str:
        return "ARRAY[" + ", ".join("'" + value + "'" for value in values) + "]"

    return f"""DO $$
DECLARE object_name TEXT;
BEGIN
  FOREACH object_name IN ARRAY {array(_TABLES)} LOOP
    EXECUTE format('ALTER TABLE public.%I OWNER TO reconbench_migrator', object_name);
  END LOOP;
  FOREACH object_name IN ARRAY {array(_VIEWS)} LOOP
    EXECUTE format('ALTER VIEW public.%I OWNER TO reconbench_migrator', object_name);
  END LOOP;
  FOREACH object_name IN ARRAY {array(_ENUMS)} LOOP
    EXECUTE format('ALTER TYPE public.%I OWNER TO reconbench_migrator', object_name);
  END LOOP;
  FOREACH object_name IN ARRAY {array(_DOMAINS)} LOOP
    EXECUTE format('ALTER DOMAIN public.%I OWNER TO reconbench_migrator', object_name);
  END LOOP;
  ALTER SEQUENCE public.audit_log_seq OWNER TO reconbench_migrator;
  {" ".join(_FUNCTIONS)}
END $$;"""


_ROLE_SQL = r"""GRANT USAGE, CREATE ON SCHEMA public TO reconbench_migrator;
ALTER DEFAULT PRIVILEGES FOR ROLE reconbench_migrator IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO reconbench_app;
ALTER DEFAULT PRIVILEGES FOR ROLE reconbench_migrator IN SCHEMA public
  GRANT SELECT ON TABLES TO reconbench_report;
ALTER DEFAULT PRIVILEGES FOR ROLE reconbench_migrator IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO reconbench_app;
"""

_TRUNCATE_SQL = r"""DO $$
DECLARE table_name TEXT;
BEGIN
  FOREACH table_name IN ARRAY ARRAY['document_version','doc_line','field_evidence','link',
    'config_version','recon_case','recon_line_result','run_manifest','case_action','audit_log']
  LOOP
    EXECUTE format(
      'CREATE TRIGGER %I BEFORE TRUNCATE ON %I FOR EACH STATEMENT '
      'EXECUTE FUNCTION forbid_mutation()',
      table_name || '_append_only_truncate', table_name
    );
  END LOOP;
END $$;"""


def upgrade() -> None:
    execute_sql_block(_ROLE_SQL)
    execute_sql_block(_ownership_sql())
    execute_sql_block(_TRUNCATE_SQL)
    # A clean install starts earlier revisions as the deployment login. Switch
    # this connection after ownership is transferred so any following revision
    # in the same upgrade command also creates objects as the migration role.
    execute_sql_block("SET ROLE reconbench_migrator;")


def downgrade() -> None:
    raise NotImplementedError("ReconBench migrations are fixed-forward; redeploy the prior image")
