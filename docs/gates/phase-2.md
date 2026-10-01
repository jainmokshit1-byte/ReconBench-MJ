# Phase 2 gate report

**Date:** 2026-10-01 · **Environment:** local disposable PostgreSQL 16.15 containers · **Hosted CI:** not run

Phase 2 implementation and local database checks are complete. The catalog criterion now matches the validated Backend Schema and has a repeatable catalog check. The only unchecked gate item is hosted CI: its migration-copy step passed locally and is wired into the workflow, but hosted GitHub Actions has not run.

## Evidence for each "Done when" line

| Criterion | Evidence | Result |
|---|---|---|
| Empty PostgreSQL 16 migration, 41 app tables plus queue tables, second `upgrade head` is a no-op | Started from an empty scratch PostgreSQL 16.15 database, applied through 0014, upgraded to 0015, then ran `alembic upgrade head` again with no revision operations. After the separate Procrastinate 3.10.0 schema command, `scripts/check_phase2_table_count.py` reported 41 ReconBench tables + 4 queue tables (+ Alembic bookkeeping). | Pass |
| Metadata drift check is empty | `scripts/check_schema_drift.py` on 0015, before queue schema: Alembic autogenerate found no operations; the explicit catalog contract passed (41 tables, 138 foreign keys, views, routines, triggers, roles, owners, ACLs, default grants and other catalog objects). | Pass; SQLAlchemy warns it cannot sort the document/document_version FK cycle, which the explicit 138-FK check covers |
| App cannot UPDATE, DELETE or TRUNCATE the ten append-only tables; privileged `forbid_mutation` guard fires | `pytest backend/tests/unit/test_repository_conventions.py backend/tests/integration/test_phase2_database.py backend/tests/integration/test_phase2_readyz.py -q`: 14 passed against the pre-queue schema and 14 passed again after the separately applied queue schema. The database test checks all three app-role operations and privileged trigger behavior; its type-owner assertion scopes to SQLAlchemy's 42 ReconBench enums/domains. | Pass |
| Report role cannot read secret/session/rate-limit/password data | Same Phase 2 database integration test verifies those grants and restricted columns. | Pass |
| No float columns; required numeric types and text identifiers; workspace and unique-index rules | Fresh local rerun on disposable PostgreSQL 16.15: `scripts/check_phase2_catalog.py` verified no float/double columns, exact common numeric domains and text identifiers, and exact precision for the four additional Schema-defined native `NUMERIC` fields. It required `workspace_id NOT NULL` on all 40 workspace-owned ReconBench tables, excluded the global `workspace` root, and checked all 19 tenant-scoped unique indexes as workspace-first plus the ten documented uniqueness exceptions by exact index name, table, and columns. Primary-key indexes are not tenant-scoped unique indexes. See `docs/schema-drift-check.md`. | Pass |
| Seed twice has stable counts, 28 reason codes, active seq 1 and reproducible canonical hashes | Ran `scripts/seed_database.py` twice against a fresh synthetic scratch DB and compared stdout/stderr byte-for-byte: workspace 1, config_version 1, config_active 1, reason_code 28, app_user 1, ai_setting 0. Integration assertions recomputed both hashes from canonical JSON. | Pass |
| Constraint invariants and audit-chain continuity under concurrent inserts | Same Phase 2 integration run covers invariant constraints and concurrent audit inserts/hash continuity. | Pass |
| Planned indexes exist | `scripts/check_phase2_indexes.py` checked all 12 index entries from Implementation Plan §2.9, Schema §6.4 and TRD §6.4 on PostgreSQL catalog. | Pass |
| CI copies the previous schema and applies the migration | Locally, `scripts/check_migration_safety.py` cloned 0014, upgraded the copy to 0015 and removed it; it passed on PostgreSQL 16.15. The GitHub Actions Phase 2 job is wired to run this step. Hosted GitHub Actions was not run from this workspace. | Local pass; hosted result unverified |
| Schema errata and four open Schema §13 decisions are recorded | `docs/schema-errata.md` exists. `scripts/check_phase2_decisions.py` confirms the existing #12, #14, #16 and #24 rows appear once and remain explicitly unresolved; no duplicate or resolution was added. | Pass |
| `/readyz` is not ready behind head and ready at head | The readiness integration test lowers only the revision marker on a disposable, fully migrated database and confirms `GET /readyz` returns 503; it restores the current head in a `finally` block and confirms 200. | Pass |

## Phase B checks

- `python -m alembic -c backend/alembic.ini upgrade 0014` on a fresh disposable PostgreSQL 16.15 container, followed by `python scripts/check_migration_safety.py` → cloned 0014 and migrated the copy to 0015.
- `python -m alembic -c backend/alembic.ini upgrade head` on the source, followed by the same command again → first applied 0015, second was a no-op.
- `python scripts/check_schema_drift.py` → no metadata drift; catalog contract passed before the queue step.
- Fresh scratch database: `alembic upgrade head` succeeded through 0015. `python scripts/check_phase2_catalog.py` → 40 workspace-owned tables, 19 workspace-first tenant unique indexes, ten exact reviewed exceptions, numeric/text checks all passed.
- `python scripts/check_schema_drift.py` → no metadata or explicit catalog drift (41 tables, 138 foreign keys, and the recorded PostgreSQL objects); it reports the existing SQLAlchemy warning for the document/document_version FK cycle.
- `python scripts/check_phase2_indexes.py` → 12 required indexes verified.
- `python scripts/check_phase2_decisions.py` → all four requested open decisions found once.
- Repeated synthetic seed command → identical output; required row counts and 28 reason codes.
- `pytest backend/tests/unit/test_repository_conventions.py backend/tests/integration/test_phase2_database.py backend/tests/integration/test_phase2_readyz.py -q` → 14 passed on the pre-queue database.
- `procrastinate --app reconbench_app.db.procrastinate_schema.app schema --apply` → applied as the separate pinned step; `scripts/check_phase2_table_count.py` → 41 + 4 tables.
- Ruff on the changed Python files → all checks passed. `pip-audit --skip-editable` → no known vulnerabilities; the editable app package was skipped.

The queue schema remains a separate step after ReconBench drift and integration checks in CI. The app integration suite also passes after queue schema installation because it scopes its type-owner check to ReconBench metadata rather than counting library-owned types.

## Remaining gate item and limits

- Hosted CI has not run. The workflow is configured and the database-copy step passed locally, but the CI gate remains unchecked until hosted GitHub Actions passes.
- Procrastinate remains the separately pinned queue schema step. The existing M2 queue-library decision remains recorded and was not changed here.

No real sample files, Phase 1 Compose database, or production data were used. No Phase 3 work was started.
