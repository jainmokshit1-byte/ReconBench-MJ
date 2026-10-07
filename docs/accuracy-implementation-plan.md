# Accuracy overhaul: implementation plan for AC-6 to AC-11 (and optional AC-12)

Written 8 Oct 2026 (decision-log D43) from `docs/accuracy-plan.md`, decision-log D17–D42, `docs/ai-gemini-protocol.md` and the code as it stands at migration head `0020`. It turns the remaining phases of the accuracy plan into session-sized work packages for an engineer (or coding agent) who has **only this repository**. Every file, function, table, setting and command named here was checked in the repository on 8 Oct 2026 and is cited as `path:line`, or is marked **NEW** with its exact path and signature. Anything that could not be confirmed is marked **UNVERIFIED** with the check to run first. Owner decisions that are still open carry a default and the words **owner decision**.

No real document values appear in this plan. Examples use the synthetic fixtures in `scripts/vision_fixtures.py` or obviously invented values.

---

## 0. How to use this plan (read this first in every session)

### 0.1 Session rules

1. **One phase (or sub-phase) per session**, in the order of §2, unless the owner names another one. Read §0, §1, the phase section and §4 before touching code.
2. **Never** `git add`, `git commit`, `git push`, `git stash`, `git checkout -- <path>`, `git reset`, `git clean`, switch branches or open pull requests. The working tree holds weeks of uncommitted work (`git status` shows ~80 modified and ~60 untracked paths); never discard or reformat anything you did not change.
3. The six `docs/ReconBench_*.md` files are **read-only** source copies.
4. **`work/` is private.** It holds the owner's real samples, truth file and caches (git-ignored). You may only: run the bench (it reads `work/accuracy-samples`, `work/accuracy-truth/truth.json`, `work/accuracy-cache/**` and writes `work/accuracy-cache/**` and the report you name), and read bench report JSON files for **counts and labels only**. Never open, print, copy or summarise `work/accuracy-truth/truth.json`, sample files, cached readings, page images or answers. Never put a value seen anywhere in `work/` into code, tests, fixtures, docs, logs or your report.
5. **No real documents and no provider calls in tests or CI.** Tests use synthetic data only (`scripts/vision_fixtures.py`, reportlab PDFs, in-code models). Commands marked **OWNER-ONLY** send data to Google; never run them.
6. Never edit `.env`, `deploy/secrets/**` or any key file. `RB_AI_EXTERNAL_CALLS` stays `disabled` in every file you write.
7. Keep the architecture: the core (`backend/reconbench_core`) stays pure (no I/O, clock, randomness, environment access; checked by `scripts/check_core_purity.py`); engines/readers sit behind ports (D17); AI is reached only through the existing gate, consent, limits and ledger (`AiHookEnqueuer`, `AiRequestRunner`); AI values are suggestions unless a phase below implements a D42 rule for them (D18 → D18b).
8. Do not change behaviour that existing tests guard unless the phase section names the test and the new expectation. If an unplanned test or scenario expectation changes, **stop and report** instead of editing the expectation.
9. Stop and report (do not guess) when: an **UNVERIFIED** check fails, a command from §0.3 fails for an environmental reason (Docker not running, port in use), or an **owner decision** is needed to continue.
10. End of every session: run the phase's verification checklist (§0.6 order), add the decision-log entry (§0.5), update the status line of `docs/accuracy-plan.md` (line 3), and report exact outcomes (pass/fail counts, hashes). Never claim a check you did not run; list what was not run and why.

### 0.2 Machine facts (verified 8 Oct 2026 on the owner's Windows 11 machine)

| Item | Fact |
|---|---|
| Repository root | `C:\Users\jainm\Desktop\reconbench` |
| Shell | Windows PowerShell 5.1. No `&&`/`||`; check `$LASTEXITCODE` after each native command. Do **not** set `$ErrorActionPreference = "Stop"` around native commands and do not use `2>&1` on them: stderr lines (for example Alembic's INFO log) then become terminating errors (reproduced with `alembic upgrade`). |
| Python | `backend\.venv\Scripts\python.exe` (3.12.0). `uv` is **not** on PATH, so `make` targets that call `uv run` do not work; use the commands in §0.3. Tools present in `backend\.venv\Scripts\`: `ruff.exe`, `mypy.exe`, `lint-imports.exe`, `pytest.exe`, `alembic.exe`, `procrastinate.exe` (Alembic 1.20.0, Procrastinate 3.10.0). |
| Node | Default Node is 20.20.2 (cannot load jsdom 30). Prefix PATH with `C:\Users\jainm\AppData\Local\nvm\v24.19.0` (contains `node.exe`, `npm.cmd`, `npx.cmd`) for every web command. |
| Docker | Docker Desktop 29.8 must be running. Image `reconbench-worker:latest` (Python 3.12.14, Tesseract 5.5.0) runs the bench. Host Tesseract 5.4.0 is at `C:\Program Files\Tesseract-OCR`; unit tests that need OCR skip when `shutil.which("tesseract")` is `None` (pattern: `backend/tests/unit/test_ai_vision.py:443`). |
| Real `.env` | A real `.env` exists at the repository root and the owner's Compose stack may be running (PostgreSQL on 15432, MinIO, API, workers). `migration_url()` (`backend/reconbench_app/db/migration_config.py:14-50`) and `scripts/seed_database.py:22` load `.env` for every variable you did not set, and `RB_ENV=local` falls back to `RB_DATABASE_URL`. **Always** set the disposable variables of block PG-UP (§0.3) in the same PowerShell session before any Alembic, seed or check script. Never point anything at port 15432. |
| Formatting | `ruff check` passes on the tree (CI runs only `ruff check`, `Makefile:25-28`). `ruff format --check` already fails on 19 files: `backend/reconbench_app/db/procrastinate_schema.py`, `backend/tests/integration/test_phase4_task_411_413.py`, `backend/tests/integration/test_ai_settings_api.py`, and `scripts/{check_core_purity,check_migration_safety,check_phase2_catalog,check_phase2_decisions,check_phase2_indexes,check_phase2_table_count,check_rules_hash,check_schema_drift,compare_engine_outputs,export_openapi,generate_synthetic_fixtures,golden_run,seed_database,wait_for_services}.py`, `scripts/spikes/{sp2_assignment,sp4_profiles}.py`. Run `ruff format` only on files you create or edit **that are not in this list**; when you edit a listed file, keep its existing style and do not reformat it (a whole-file reformat would hide your change in noise). |
| Test collection | Unit and integration folders cannot be collected in one pytest call (duplicate test module basenames); run them separately. Run pytest from the repository root. There is no `conftest.py`; integration fixtures live inside test modules (for example `backend/tests/integration/test_ai_vision.py:71-401`). |
| mypy scope | `backend/pyproject.toml:74-79` checks only `reconbench_core` with `--strict`. App modules must be passed explicitly. Pre-existing strict errors (`--follow-imports=silent`, per file): `reconbench_app/ingestion.py` 33, `reconbench_app/pdf_sandbox.py` 9, `reconbench_app/jobs.py` 1, `reconbench_app/api/ingestion.py` 1; 0 in `mapping_service.py`, `vendor_resolution.py`, `reconciliation_service.py`, `settings.py`, `configuration.py`, `db/metadata.py`, `ai/vision.py`, `ai/consent.py`, `ai/runtime_checks.py`, `ai/settings_service.py`, `ai/task_contracts.py`, `ai/worker/main.py`, `ai/worker/handlers.py`, `ai/worker/hooks/vision.py`, `extraction/local_hybrid.py`, `extraction/vision_pages.py`, `api/mapping.py`, `api/ai.py`. Rule: a **new** module must have 0 errors; a touched existing module must not have more errors than this baseline. |

### 0.3 Command blocks (PowerShell; run from the repository root unless a block changes directory)

Every block below was executed on this machine on 8 Oct 2026 with the stated result, except where marked **NEW** (built only from verified pieces) or **not run during planning**.

**CHK-PY** (static checks; verified: ruff "All checks passed!", mypy "no issues found in 35 source files", lint-imports "Contracts: 3 kept, 0 broken", purity passed, rules hash verified, engine version "0.1.1 → 0.1.4", golden self-check passed):
```powershell
Set-Location C:\Users\jainm\Desktop\reconbench\backend
.\.venv\Scripts\ruff.exe check reconbench_core reconbench_app tests ..\scripts
.\.venv\Scripts\ruff.exe format --check <each file you created or edited, path relative to backend\>
.\.venv\Scripts\mypy.exe --strict
.\.venv\Scripts\mypy.exe --strict --follow-imports=silent <each app module you created or edited>
.\.venv\Scripts\lint-imports.exe
Set-Location C:\Users\jainm\Desktop\reconbench
backend\.venv\Scripts\python.exe scripts\check_core_purity.py
backend\.venv\Scripts\python.exe scripts\check_rules_hash.py
$env:PYTHONIOENCODING = "utf-8"; backend\.venv\Scripts\python.exe scripts\check_engine_version.py --base-ref HEAD
backend\.venv\Scripts\python.exe scripts\golden_run.py --self-check
```

**TEST-OFFLINE** (verified: `682 passed in 86.44s`):
```powershell
backend\.venv\Scripts\python.exe -m pytest backend\tests\unit backend\tests\property backend\tests\scenarios backend\tests\parsers -q -p no:cacheprovider
```
Until AC-9a, Hypothesis may draw the known counterexample of `test_po_canonicalization_is_idempotent` (`backend/tests/unit/test_phase4_normalize.py:43-53`); report that single failure as pre-existing and do not fix it outside AC-9a.

**RULES-HASH-UPDATE** (**NEW** composition of `scripts/check_rules_hash.py:14-25`; the file is 64 hex characters plus LF):
```powershell
backend\.venv\Scripts\python.exe -c "import sys; sys.path.insert(0, 'scripts'); import check_rules_hash as r; r.HASH_FILE.write_bytes((r.calculate_rules_hash() + '\n').encode('ascii'))"
backend\.venv\Scripts\python.exe scripts\check_rules_hash.py
```

**PG-UP** (disposable integration database on `127.0.0.1:15499`; verified: migrations 0001→0020, seed counts workspace 1 / reason_code 28 / ai_setting 0, queue schema applied, `test_kpi_summary.py` 1 passed):
```powershell
Set-Location C:\Users\jainm\Desktop\reconbench
docker rm -f rb-ephemeral-pg 2>$null | Out-Null
docker run -d --rm --name rb-ephemeral-pg -e POSTGRES_PASSWORD=phase2-ephemeral-local-only -p 127.0.0.1:15499:5432 --tmpfs /var/lib/postgresql/data postgres:16.15-bookworm | Out-Null
for ($i = 0; $i -lt 60; $i++) { docker exec rb-ephemeral-pg pg_isready -U postgres -d postgres 2>$null | Out-Null; if ($LASTEXITCODE -eq 0) { break }; Start-Sleep -Seconds 1 }
Start-Sleep -Seconds 2
docker exec rb-ephemeral-pg psql -U postgres -c "CREATE DATABASE reconbench_phase2_ai" | Out-Null
$dsn = "postgresql+psycopg://postgres:phase2-ephemeral-local-only@127.0.0.1:15499/reconbench_phase2_ai"
$env:RB_ENV = "ci"; $env:RB_DATABASE_URL = $dsn; $env:RB_MIGRATOR_DATABASE_URL = $dsn; $env:RB_PHASE2_TEST_DATABASE_URL = $dsn
$env:RB_BOOTSTRAP_EMAIL = "phase2-manager@example.invalid"; $env:RB_BOOTSTRAP_DISPLAY_NAME = "Synthetic Phase 2 Manager"
$env:RB_BOOTSTRAP_PASSWORD_HASH = '$argon2id$v=19$m=65536,t=3,p=4$c3ludGhldGlj$bm90LWF1dGhlbnRpY2F0ZWQ'
$env:RB_BOOTSTRAP_WORKSPACE_NAME = "Synthetic Phase 2 Workspace"
backend\.venv\Scripts\python.exe -m alembic -c backend\alembic.ini upgrade head
backend\.venv\Scripts\python.exe scripts\seed_database.py
Push-Location backend; .\.venv\Scripts\procrastinate.exe --app reconbench_app.db.procrastinate_schema.app schema --apply; Pop-Location
```

**INT-TEST** (in the PG-UP session; run the groups separately, as CI does at `.github/workflows/ci.yml:305-333`):
```powershell
backend\.venv\Scripts\python.exe -m pytest <test files> -q -p no:cacheprovider
```
Groups: (1) `backend\tests\integration\test_phase2_database.py backend\tests\integration\test_phase2_readyz.py backend\tests\integration\test_configuration.py backend\tests\integration\test_ingestion.py backend\tests\integration\test_ai_assist_migration.py backend\tests\integration\test_ai_runner.py`; (2) `backend\tests\integration\test_phase4_task_411_413.py` alone (it clones the database and refuses while other sessions are open); (3) `backend\tests\integration\test_ai_settings_api.py`; (4) `backend\tests\integration\test_ai_h1.py backend\tests\integration\test_ai_h2.py backend\tests\integration\test_ai_h3.py backend\tests\integration\test_ai_h4.py backend\tests\integration\test_ai_vision.py backend\tests\integration\test_kpi_summary.py`; (5) `backend\tests\integration\test_page_split.py backend\tests\integration\test_ac4_vendor_hygiene.py`; (6) the phase's new integration files. Not run locally by default: `test_authentication.py` (needs its own cluster via `RB_PHASE3_TEST_DATABASE_URL`, D29) and `test_infrastructure_ready.py` (needs S3 test credentials).

**PG-DOWN**: `docker stop rb-ephemeral-pg | Out-Null` (the container was started with `--rm`).

**PG-SAFETY** (migration safety, drift, catalog, indexes, decisions, table count on a **second fresh cluster** on `127.0.0.1:15497`, because database roles are cluster-wide; verified at head 0020: "Migration safety passed: cloned 0019 and upgraded the copy to 0020", drift "no metadata drift" and "Catalog contract: all explicit checks passed", catalog/indexes/decisions/table count passed). Replace `<PREVIOUS>` with the revision before the new head:
```powershell
Set-Location C:\Users\jainm\Desktop\reconbench
docker rm -f rb-safety-pg 2>$null | Out-Null
docker run -d --rm --name rb-safety-pg -e POSTGRES_PASSWORD=phase2-ephemeral-local-only -p 127.0.0.1:15497:5432 --tmpfs /var/lib/postgresql/data postgres:16.15-bookworm | Out-Null
for ($i = 0; $i -lt 60; $i++) { docker exec rb-safety-pg pg_isready -U postgres -d postgres 2>$null | Out-Null; if ($LASTEXITCODE -eq 0) { break }; Start-Sleep -Seconds 1 }
Start-Sleep -Seconds 2
docker exec rb-safety-pg psql -U postgres -c "CREATE DATABASE reconbench_phase2_safety" | Out-Null
$dsn = "postgresql+psycopg://postgres:phase2-ephemeral-local-only@127.0.0.1:15497/reconbench_phase2_safety"
$env:RB_ENV = "ci"; $env:RB_DATABASE_URL = $dsn; $env:RB_MIGRATOR_DATABASE_URL = $dsn; $env:RB_PHASE2_TEST_DATABASE_URL = $dsn
$env:RB_BOOTSTRAP_EMAIL = "phase2-manager@example.invalid"; $env:RB_BOOTSTRAP_DISPLAY_NAME = "Synthetic Phase 2 Manager"
$env:RB_BOOTSTRAP_PASSWORD_HASH = '$argon2id$v=19$m=65536,t=3,p=4$c3ludGhldGlj$bm90LWF1dGhlbnRpY2F0ZWQ'
$env:RB_BOOTSTRAP_WORKSPACE_NAME = "Synthetic Phase 2 Workspace"
backend\.venv\Scripts\python.exe -m alembic -c backend\alembic.ini upgrade <PREVIOUS>
backend\.venv\Scripts\python.exe scripts\check_migration_safety.py
backend\.venv\Scripts\python.exe -m alembic -c backend\alembic.ini upgrade head
backend\.venv\Scripts\python.exe scripts\check_schema_drift.py
backend\.venv\Scripts\python.exe scripts\check_phase2_catalog.py
backend\.venv\Scripts\python.exe scripts\check_phase2_indexes.py
backend\.venv\Scripts\python.exe scripts\check_phase2_decisions.py
```
After a schema change, `check_schema_drift.py` reports catalog drift until you run **CONTRACT-REGEN** in this same session (database at the new head, before the seed and queue schema), then run `check_schema_drift.py` again (it must pass). Then finish:
```powershell
backend\.venv\Scripts\python.exe scripts\seed_database.py
Push-Location backend; .\.venv\Scripts\procrastinate.exe --app reconbench_app.db.procrastinate_schema.app schema --apply; Pop-Location
backend\.venv\Scripts\python.exe scripts\check_phase2_table_count.py
docker stop rb-safety-pg | Out-Null
```

**CONTRACT-REGEN** (rewrites `backend/reconbench_app/db/schema_contract.json` from the live catalog with the drift script's own `_catalog_contract`, `scripts/check_schema_drift.py:31-331`; verified: writing to a scratch path at head 0020 reproduced the committed file byte for byte). Only in the PG-SAFETY session, only after a reviewed migration (`docs/schema-drift-check.md:47`):
```powershell
backend\.venv\Scripts\python.exe -c "import json,sys; sys.path.insert(0,'scripts'); import check_schema_drift as d; from sqlalchemy import create_engine; e=create_engine(d.migration_url()); c=e.connect(); open(d.CONTRACT_FILE,'w',encoding='utf-8').write(json.dumps(d._catalog_contract(c), indent=2)+'\n'); c.close(); e.dispose()"
```

**OPENAPI** (same scripts as `make openapi`, `Makefile:84-87`; **not run during planning**):
```powershell
backend\.venv\Scripts\python.exe scripts\export_reconciliation_schemas.py
backend\.venv\Scripts\python.exe scripts\export_openapi.py
$env:PATH = "C:\Users\jainm\AppData\Local\nvm\v24.19.0;" + $env:PATH
npm run api:generate --prefix web
git diff --stat -- backend/openapi.json web/src/api/generated.ts docs/schemas
```

**WEB** (verified: typecheck 0 errors, lint and money-math check passed, Vitest 7 files / 30 tests passed):
```powershell
$env:PATH = "C:\Users\jainm\AppData\Local\nvm\v24.19.0;" + $env:PATH
Set-Location C:\Users\jainm\Desktop\reconbench\web
npm run typecheck
npm run lint
npm run test -- --run --pool=threads --maxWorkers=1
Set-Location C:\Users\jainm\Desktop\reconbench
```
**E2E** (Playwright, mocked API; **not run during planning**; the 10,000-row frame-budget test is hardware-sensitive locally, D29): in the WEB session, `npm run test:e2e` from `web\`.

**BENCH-OFFLINE** (accuracy bench in the worker image with no network; verified: `report=31cb1ec49644`, `GATE: PASS`, vision 39/39 read, "cache hits 35, read 0 file(s)"):
```powershell
Set-Location C:\Users\jainm\Desktop\reconbench
$root = (Resolve-Path .).Path
docker run --rm --network none -e RB_BENCH_VISION_MODEL=gemini-3.5-flash-lite -v "${root}\backend\reconbench_core:/app/backend/reconbench_core:ro" -v "${root}\backend\reconbench_app:/app/backend/reconbench_app:ro" -v "${root}\scripts:/app/scripts:ro" -v "${root}\work:/app/work" reconbench-worker /app/backend/.venv/bin/python /app/scripts/accuracy_bench.py --out /app/work/accuracy-reports/<report-name>.json
```
- `RB_BENCH_VISION_MODEL=gemini-3.5-flash-lite` is required: the recorded answers (D41) are keyed by model (`scripts/accuracy_vision.py:202-225`); the script default is `gemini-3.8-flash` (`scripts/accuracy_bench.py:1661-1664`), which makes every answer "missing".
- Changing anything under `backend/reconbench_core/{extraction,parsers,normalize,schema}`, `backend/reconbench_app/extraction`, `backend/reconbench_app/pdf_sandbox.py` or `ENGINE_VERSION` invalidates the reader cache (`scripts/accuracy_bench.py:160-167,339-359`): the next run re-reads all 35 files (several minutes). If a file then fails with `PDF_PROCESS_TIMEOUT` under parallel load, re-run once with `--workers 1` (D35). Never pass `--refresh` unless the phase says so.
- Report names: `before-<phase>.json` at the start of a phase (only if the code changed since the last report), `after-<phase>.json` at the end, for example `after-ac6a.json`.
- The image supplies only dependencies; the code is mounted. Rebuild (`docker compose build worker`) only if a phase adds a Python dependency (none is planned).

**BENCH-PROVIDER** (**OWNER-ONLY**, network on, free tier under D39; never run by Codex):
```powershell
$env:BENCH_GEMINI_KEY = "<owner's key>"
docker run --rm -e BENCH_GEMINI_KEY -e RB_BENCH_VISION_MODEL=gemini-3.5-flash-lite -v "${root}\backend\reconbench_core:/app/backend/reconbench_core:ro" -v "${root}\backend\reconbench_app:/app/backend/reconbench_app:ro" -v "${root}\scripts:/app/scripts:ro" -v "${root}\work:/app/work" reconbench-worker /app/backend/.venv/bin/python /app/scripts/accuracy_bench.py --vision provider --allow-provider --max-provider-pages 120 --out /app/work/accuracy-reports/<report-name>.json
```

### 0.4 Hashes, versions and fingerprints

| Item | Where | Rule |
|---|---|---|
| `RULES_HASH` | `backend/reconbench_core/RULES_HASH` (now `b724a4bb…`) | Run RULES-HASH-UPDATE after **any** change to a `.py` file under `backend/reconbench_core` (`scripts/check_rules_hash.py:14-25`). |
| `ENGINE_VERSION` | `backend/reconbench_core/version.py:3` (now `0.1.4`) | Bump the patch number once in a phase that changes files in the rule paths of `scripts/check_engine_version.py:13-20` (`backend/reconbench_app/db/seed_data.py`, core `align`, `mapping`, `normalize`, `recon`, `schema`) in a way that can change mapping or reconciliation output. Extraction- or parser-only phases do not bump it (D35, D37). The script passes against `HEAD` today (HEAD has 0.1.1); the bump is the project convention, recorded in the D-entry. A bump invalidates the bench reader cache. |
| Check registry versions | `backend/reconbench_core/recon/registry.py:22-60` | Bump a check's `version` when its semantics change (changes `rules_hash`, so earlier runs show as stale). |
| `PARSER_VERSION` | `backend/reconbench_app/ingestion.py:76` (`"phase4-parser-4"`) | Bump when the stored parser output for the same file changes (AC-8b). |
| Vision contract | `vision.schema.v3` (`backend/reconbench_app/ai/contracts.py:38`), `vision.prompt.v3` (`backend/reconbench_app/ai/prompts.py:35`), `image_mask.v1` (`backend/reconbench_app/ai/redaction.py:152`), `vision.render.v1` (`backend/reconbench_app/extraction/vision.py:47-53`) | **Must not change in any phase of this plan.** Each change invalidates every cached answer and requires consent again (D38, D40). |
| Consent fingerprint | `consent_fingerprint` (`backend/reconbench_app/ai/consent.py:110-129`) | Must stay byte-identical for every workspace whose new toggle (AC-6c) is off; a test proves it. |

### 0.5 Decision-log entries

Append at the end of `docs/decision-log.md` (after D42/D43). Number in order of execution: AC-6a takes **D44**, the next phase **D45**, and so on; never renumber. Template:

```markdown
## D<nn> — <phase id> <short title> (<day> <Mon> 2026)

<Two to four sentences: what changed and why. Counts only; no real values.>

| Decision | Choice |
|---|---|
| <topic> | <rule as implemented> |

Bench (`<before>.json` → `<after>.json`, report <first 12 of report_hash>):

| Measure | Before | After |
|---|---|---|
| <measure> | <count> | <count> |
| **False-safe / false auto-links / false Matched / wrong vendors** | 0 / 0 / 0 / 0 | 0 / 0 / 0 / 0 |

Checks: <suites with pass counts>; not run: <item and reason>.

Open:
- <item>
```

Then change line 3 of `docs/accuracy-plan.md` to name the finished phase (for example "**AC-6a done** (consensus core, shadow bench, D44)") and the next one.

### 0.6 Common exit order for every phase

1. CHK-PY. 2. TEST-OFFLINE. 3. Phase unit tests (already part of TEST-OFFLINE when they live in `backend/tests/unit`). 4. If a migration was added: PG-SAFETY with CONTRACT-REGEN. 5. If app or database code changed: PG-UP, the INT-TEST groups listed by the phase, PG-DOWN. 6. If an API changed: OPENAPI and check that only the expected paths changed. 7. If web code changed: WEB, and E2E when the phase lists it. 8. BENCH-OFFLINE (`after-<phase>.json`). 9. Docs: decision-log entry, `docs/accuracy-plan.md` status, `docs/schema-errata.md` for a migration. 10. Final report.

### 0.7 Migration checklist (any phase that adds a revision)

1. `backend/migrations/versions/00NN_<name>.py` with `revision = "00NN"`, `down_revision = "<previous>"`, SQL run through `execute_sql_block` (`backend/reconbench_app/db/migration_sql.py`), module docstring in the style of `0020_vision_pages.py:1-14`, and `downgrade()` raising `NotImplementedError("ReconBench migrations are fixed-forward; redeploy the prior image")` (`0019_vendor_name_status.py:33-34`). Migrations run as `reconbench_migrator`, so default privileges apply (`0015_role_defaults_append_only.py:126-133`): `reconbench_app` gets SELECT/INSERT/UPDATE/DELETE and `reconbench_report` SELECT on new tables.
2. Mirror every object in `backend/reconbench_app/db/metadata.py` (types, nullability, server defaults, constraint and index names), so `check_schema_drift.py` reports no Alembic drift. A new enum uses `postgresql.ENUM(..., name="<enum>", create_type=False)` and is **not** added to `DATABASE_ENUMS` (`backend/reconbench_core/schema/__init__.py:684-722`): `test_core_enums_match_all_database_enum_values` (`backend/tests/unit/test_phase4_schema.py:40-50`) compares that map with the enums of `0001_setup.py` only.
3. Workspace-owned tables have `workspace_id UUID NOT NULL REFERENCES workspace(id)`; every non-primary unique index starts with `workspace_id` (`scripts/check_phase2_catalog.py:59-99,247-282`); numeric columns use the domains `d_amount`, `d_qty`, `d_score`, `d_taxrate` (`scripts/check_phase2_catalog.py:21-38`); no float columns.
4. Append-only tables get the triggers of `0014_immutability_roles.py:16-31` and `0015_role_defaults_append_only.py:136-150` and `REVOKE UPDATE, DELETE ON <table> FROM reconbench_app`.
5. `.github/workflows/ci.yml:279` builds the previous schema: set it to `upgrade <new head − 1>` (`scripts/check_migration_safety.py:53-66` requires exactly head − 1).
6. `backend/tests/integration/test_phase4_task_411_413.py:116-120` accepts a fixed set of revisions and compares with the head: add the new head to the set and compare against it.
7. Run PG-SAFETY with CONTRACT-REGEN; report the new `object_counts` printed by `check_schema_drift.py`.
8. Add `## Revision 00NN: <title> (<date>)` to `docs/schema-errata.md` in the style of its 0020 entry (what, why, validation, new counts).

---

## 1. Current state (counts only, 8 Oct 2026)

### 1.1 Phases

| Phase | State | Evidence |
|---|---|---|
| AC-0 benchmark | done | D32; `scripts/accuracy_bench.py`, truth v2 in `work/` |
| AC-1 deterministic guards | done | D33; `backend/reconbench_core/parsers/parties.py` |
| AC-2 e-invoice QR | done | D34; `backend/reconbench_core/extraction/qr.py` |
| AC-3 page split | done | D35; `backend/reconbench_core/extraction/pages.py`, `POST /files/{id}/page-split` |
| AC-4 vendor hygiene | done | D36; migration 0019, `plan_vendor_anchors` |
| AC-5 / AC-5b vision reader | done | D37, D38, D40, D41; hook `h6_vision`, migration 0020, suggestions only |
| D-2 | decided | D42 (five rules, below) |
| AC-6 … AC-11, AC-12 | not started | this plan |

D42 (the rule every AC-6 sub-phase implements): a machine-read value may be verified without a person **per field**, only if (1) two independent readers give the same normalised value (OCR/text layer, signed e-invoice QR, XML, vision; two answers of one model never count twice; a signed QR is L0 on its own), (2) every deterministic validator passes for the field and the document, (3) a local OCR re-read of the reader's box agrees (a box in a mask, off the page or empty fails), (4) never for handwritten or stamped marks, new vendor names, a field a person corrected, or an unconfirmed page split, and (5) it is switched on per field type only after the bench shows 0 false-safe and 0 agree-wrong-confirmed for that type, behind a workspace setting that is off by default.

### 1.2 Bench (`work/accuracy-reports/after-d41-lite.json`, re-run identically during planning)

| Measure | Count |
|---|---|
| Files / truth documents / parsed documents / missed / spurious | 35 / 26 / 51 / 0 / 0 |
| Header fields correct (rules) | 302/373 (81.0%); invoices 207/263, POs 49/56, GRNs 46/54 |
| Line items found / spurious | 96/141 (68.1%) / 33 |
| Auto-verified documents | 18/51 (35.3%); invoices 6/29 |
| Fields to touch / to confirm per document | 4.96 / 1.94 |
| Trusted-but-wrong fields | 1 |
| E-invoice QR fields | 11 documents: confirmed 45, corrected 6, conflict 2, prefilled 13, signed 0 |
| Vendors | 12 created (pending 4, provisional 3, verified 5); document vendor correct 26, pending 2 |
| Links | expected 10: auto 1, suggested 6, missed 3 (one complete set's invoice→PO; the other set's GRN→PO and invoice→PO); false suggestions 0 |
| Identity / duplicates | system documents 28, extra 2, wrong merges 0; duplicates caught 0, missed 2, false 0 |
| Reconciliation | 12 cases, all unlinked; status agree 8, disagree 2 (the two complete sets) |
| **Hard metrics** | **FALSE_SAFE 0, FALSE_AUTO_LINKS 0, FALSE_MATCHED 0, WRONG_VENDORS_CREATED 0** |
| Report hashes | `report_hash` 31cb1ec49644, `vision_hash` f57033ecdc94, engine 0.1.4 |
| Vision alone (`gemini-3.5-flash-lite`, free tier) | 39/39 documents read, 67 pages; header 271/313 (86.6%); lines 80/123 (65.0%); handwritten refs 6/18 typed (+1 other annotation, 11 missed); marketplace orders 5/5; page kinds 54 correct |
| OCR × vision on scored header fields | agree-correct 211, **agree-wrong 1 (a vendor name)**, both missing 60, disagree: OCR right 5 (dates), vision right 19, both wrong 2 (taxable totals); OCR only correct 26, OCR only wrong 2 (document numbers); vision only correct 41 |
| Vision tokens | 102,470 input / 81,104 output over 39 documents (≈2,630 / 2,080 per document); largest answer 4,399 output tokens; 3 answers above 4,096, none above 8,192 |

### 1.3 Code baseline

- Offline suites: 682 passed (TEST-OFFLINE). Web: 30 tests passed; typecheck and lint pass. Static checks pass (CHK-PY), golden self-check passes; the golden set itself has no cases (`golden/` holds only `README.md`), so "zero golden diff" is vacuous (D18).
- Schema at `0020`: 46 application tables, 38 enums, 24 triggers, 154 foreign keys, 250 explicit ACL entries, 7 default privileges (`check_schema_drift.py` output).
- Append-only tables (trigger `forbid_mutation`, `0014_immutability_roles.py:16-31`): `document_version`, `doc_line`, `field_evidence`, `link`, `config_version`, `recon_case`, `recon_line_result`, `run_manifest`, `case_action`, `audit_log`. New evidence is always a new row; a changed document is always a new `document_version`.

### 1.4 Known open bugs and where each is fixed

| # | Bug | Evidence (verified) | Fixed in |
|---|---|---|---|
| B1 | `canonicalize_po_number` is not idempotent when the joined result is all digits with a leading zero: `"0/0"` → `"00"` → `"0"`, `"0-12"` → `"012"` → `"12"`; mixed results such as `"PO/0/142"` are stable | `backend/reconbench_core/normalize/text.py:143-153`; property test `backend/tests/unit/test_phase4_normalize.py:43-53` fails only when Hypothesis draws such a case | AC-9a |
| B2 | An aged GRN_PENDING invoice whose status is not `pending` keeps `aged=true`, violating `recon_case_aged_ck` (`NOT aged OR status='exception'`) and failing the whole run | `backend/reconbench_core/recon/engine.py:1745-1753`; `backend/reconbench_app/db/metadata.py:1703`; D22 | AC-8a |
| B3 | `ai_request.ai_cache_id` is NULL for answered (non-cached) requests | **By design, not a bug:** `ai_cache_id IS NOT NULL` marks a cache hit, which budgets count as zero (`backend/reconbench_app/ai/ledger.py:43-50`, `record_cache_hit` at `:412-432`). A fresh answer is linked through `ai_request.cache_key` (set at `backend/reconbench_app/ai/worker/runner.py:173-177`, indexed by `ai_request_cache_ix`, `metadata.py:3026-3032`) = `ai_cache.cache_key` within the workspace, as `_h1_review` already does (`backend/reconbench_app/ingestion.py:4156-4162`). Setting `ai_cache_id` on fresh answers would make them free in the budget. | No change; AC-6b reads answers by `cache_key`; D44 records the explanation |
| B4 | The configured payment-terms list is not applied to the due date; `_payment_due` reads only the PO's printed terms | `backend/reconbench_core/recon/engine.py:489-498`; D27 | **Owner decision** (not accuracy); carried to the AC-11 exit gate |
| B5 | Hosted CI migration safety would fail: CI builds `0017` but the head is `0020` and the check needs head − 1 | `.github/workflows/ci.yml:279`; `scripts/check_migration_safety.py:53-66` | AC-6a (set `0019`), then each migration phase (§0.7 step 5) |
| B6 | A confirmed rules `content` link becomes `suggested` again after a re-run with a changed input hash | `backend/reconbench_app/mapping_service.py:2576-2622` with `_matches` comparing `state` (`:2663-2672`); confirm keeps the rules method (`:2088-2108`); D20 | AC-9b |
| B7 | An OCR-read, unverified PO reference that equals a PO number of the same vendor creates an **auto** labeled link (confidence 0.99 ≥ 0.95) | `backend/reconbench_core/mapping/links.py:305-403` (no check of the reference's confidence) with `_confidence_state` (`:251-252`) | AC-9a |
| B8 | Corrections exist only for invoices; an invoice correction re-runs vendor resolution (and with it link mapping) only when the vendor name or GSTIN changed | `backend/reconbench_app/ingestion.py:3529-3533`, `:3983-3990`; D21 open | AC-7a |
| B9 | A lower-quality re-scan can replace a clean current version (D19 open); re-scans with a misread number or vendor become new documents (R9) | `backend/reconbench_app/ingestion.py:2319-2563`, `document_natural_key` at `:4943-4963` | AC-8b |
| B10 | The manual "change" link evidence text differs from the resolver's manual evidence text | `backend/reconbench_app/mapping_service.py:2198` vs `backend/reconbench_core/mapping/links.py:39`; D20 | AC-9b |
| B11 | Same-type scanned documents in one file split only on "Page 1 of N" | D35 open | Not planned here (§5, Q-14) |
| B12 | Vendor Summary sheet counts Needs Review inside Exceptions; auth tests not in hosted CI; 10k-row frame budget flaky locally | D28, D29 | Carried to the AC-11 exit gate (not accuracy) |

---

## 2. Order, dependencies and sessions

```
AC-6a ──► AC-6b ──► AC-6c ──────────────┐
            │                            │
            ├──► AC-7a ──► AC-7b         │
            │                            │
AC-8a ──────┴──► AC-8b ──► AC-9a ──► AC-9b ──► AC-10 ──► AC-11 (exit gate)
                                                  (AC-12 optional, after AC-6b; before or after AC-11)
```

| Order | Phase | Size | Sessions | Effort | Migration (if run in this order) | Depends on | Can run in parallel with |
|---|---|---|---|---|---|---|---|
| 1 | AC-6a consensus core + shadow bench | large | 1 | max | none | — | AC-8a |
| 2 | AC-6b crop re-read, provenance, consensus job (dark) | large | 1 (split point given) | max | `0021_consensus` | AC-6a | AC-8a |
| 3 | AC-6c verify loop, auto-verify setting, consent | medium–large | 1 | high | none | AC-6b | AC-7a, AC-8a |
| 4 | AC-7a verify screen backend | large | 1 | high | none | AC-6b | AC-6c, AC-8a |
| 5 | AC-7b verify screen UI | medium | 1 | medium | none | AC-7a | AC-8a/8b |
| 6 | AC-8a identity and duplicate rules (core) + aged fix | medium | 1 | high | none | — (bench changes merge after AC-6a) | AC-6x, AC-7x |
| 7 | AC-8b identity in the app, re-scan guard, page hashes | medium–large | 1 | high | `0022_document_identity` | AC-8a, AC-6b | AC-7b |
| 8 | AC-9a matching v2 (core) | large | 1 | max | none | AC-8a, AC-6b | — |
| 9 | AC-9b matching v2 (app, UI) | large | 1 | high | none | AC-9a, AC-8b | — |
| 10 | AC-10 learning loop | medium–large | 1 | high | `0023_vendor_profile` | AC-9b, AC-6c | — |
| 11 | AC-11 real-set E2E + Phase 4 exit gate | medium + owner runs | 1 + owner | high | none | all of the above | — |
| opt | AC-12 GSTR-2B cross-check | medium–large | 1–2 | high | next free (`0024_gstr2b` in this order) | AC-6b, **D-10** | after AC-6b |

"Parallel" means the phases touch different files and their order does not matter; they still run in separate sessions on the same working tree. **Migration numbers assume this order.** If phases run in another order, use the next free revision number (current head + 1) and keep §0.7 steps 5–6 consistent. Total: 11 sessions for AC-6a…AC-11 (plus owner runs in AC-11), 1–2 for optional AC-12.

---

## 3. Phases

### AC-6a — Consensus core (pure) and shadow scoring in the bench

**a. Goal and exit criteria**

Goal: one pure, deterministic core module that takes the rules reading of a document (including its QR merge), the checked vision reading and, optionally, crop re-read results, and decides a trust level for every header field and line cell under D42 rules 1, 2 and 4. Rule 3 (crop agreement) is an input; rule 5 (which field types may auto-verify, and whether the workspace allows it) is a policy parameter. The bench gets a shadow block that measures, per field type, what would be verified automatically and whether it would be right. Nothing in the app calls the module yet.

Exit criteria:
1. BENCH-OFFLINE → `after-ac6a.json`: `report_hash` = `31cb1ec49644` (unchanged; consensus feeds nothing in the main pipeline) and `vision_hash` = `f57033ecdc94` (unchanged), hard metrics 0/0/0/0. If the owner changed the truth file in the meantime, the main hash may change only through `meta.truth_sha256`; say so.
2. The report has a top-level `consensus` object with an `upper_bound` block (crop not required) and a `consensus_hash`; two consecutive runs give the same `consensus_hash`.
3. `consensus.upper_bound.by_field_type` lists, for each of the eight field types, `l1`, `l1_correct`, `l1_wrong`, `l1_unscored`, `l2`, `l3`, `rejected` and a `gate` value. The vendor-name field that both readers got wrong in D41 is counted under `excluded`, never as L1.
4. TEST-OFFLINE: 682 + the new tests pass. CHK-PY passes; mypy core reports 36 source files (35 + `consensus.py`).
5. The consensus block holds counts and labels only (a test asserts that no synthetic value appears in it).

**b. Preconditions**

D42 (decided). Recorded `gemini-3.5-flash-lite` answers exist in `work/accuracy-cache/vision/` (D41). Docker Desktop running. No owner decision is needed.

**c. Design**

c.1 Trust levels as implemented (extends `docs/accuracy-plan.md` §3.1):

| Level | Produced when |
|---|---|
| L0 | the value's source is a person (`FieldSource.MANUAL`), the document is structured (XML/CSV), or the e-invoice QR signature verified |
| L1 `existing` | the rules already verified it (confidence ≥ 0.98: D18 arithmetic/checksum/dual read, D34 QR agreement or look-alike correction) and the vision reading does not contradict it |
| L1 `d42` | D42: the rules value and the vision value agree, no reader disagrees, every validator passes, the crop re-read agrees (unless the policy waives the crop for the bench upper bound) |
| L2 | one reader only, a never-automatic field, a failed soft validator, or no/negative crop agreement |
| L3 | readers disagree (`conflict`), or vision disagrees with a rules-verified non-L0 value (`reopen`) |
| rejected | a hard validator fails (checksum, buyer entity as supplier, tax type against the state rule) |

A decision with `auto_verified=True` is applied only when the policy allows it (`auto_verify` and the field type in `field_types`). With the default policy nothing is applied.

c.2 Module `backend/reconbench_core/extraction/consensus.py` (**NEW**, pure; imports only `reconbench_core`).

Constants and enums (exact names and values):
```python
CONSENSUS_VERSION = "consensus.v1"
REOPEN_CONFIDENCE = Decimal("0.65")      # equals OCR_CONFIDENCE (extract.py:74): below the 0.70 review floor
DEFAULT_DATE_PAST_DAYS = 540
DEFAULT_DATE_FUTURE_DAYS = 1

class TrustLevel(StrEnum):   L0 = "l0"; L1 = "l1"; L2 = "l2"; L3 = "l3"; REJECTED = "rejected"
class ReaderKind(StrEnum):   STRUCTURED = "structured"; MANUAL = "manual"; QR_SIGNED = "qr_signed"; QR = "qr"; TEXT_LAYER = "text_layer"; OCR = "ocr"; VISION = "vision"
class FieldType(StrEnum):    DOC_NO = "doc_no"; DOC_DATE = "doc_date"; GSTIN = "gstin"; AMOUNT = "amount"; REFERENCE = "reference"; LINE_AMOUNT = "line_amount"; LINE_QUANTITY = "line_quantity"; LINE_RATE = "line_rate"
class CropOutcome(StrEnum):  AGREE = "agree"; DISAGREE = "disagree"; EMPTY = "empty"; MASKED = "masked"; UNAVAILABLE = "unavailable"
class DecisionRule(StrEnum): EXISTING = "existing"; D42 = "d42"; SINGLE = "single"; CONFLICT = "conflict"; REOPEN = "reopen"; EXCLUDED = "excluded"; VISION_ONLY = "vision_only"
```

Field maps (attribute names of `DocumentHeader`/`DocLine`, `backend/reconbench_core/schema/__init__.py:416-489`):
```python
HEADER_FIELD_TYPES: Mapping[str, FieldType] = MappingProxyType({
    "doc_no": FieldType.DOC_NO, "doc_date": FieldType.DOC_DATE,
    "vendor_gstin": FieldType.GSTIN, "buyer_gstin": FieldType.GSTIN,
    "taxable_total": FieldType.AMOUNT, "cgst": FieldType.AMOUNT, "sgst": FieldType.AMOUNT,
    "igst": FieldType.AMOUNT, "round_off": FieldType.AMOUNT, "grand_total": FieldType.AMOUNT,
    "po_refs": FieldType.REFERENCE, "grn_refs": FieldType.REFERENCE, "challan_no": FieldType.REFERENCE,
    "supplier_invoice_no": FieldType.REFERENCE, "e_way_bill_no": FieldType.REFERENCE, "irn": FieldType.REFERENCE,
})
LINE_FIELD_TYPES: Mapping[str, FieldType] = MappingProxyType({
    "amount": FieldType.LINE_AMOUNT, "rate": FieldType.LINE_RATE,
    "quantity": FieldType.LINE_QUANTITY, "received_qty": FieldType.LINE_QUANTITY,
    "accepted_qty": FieldType.LINE_QUANTITY, "rejected_qty": FieldType.LINE_QUANTITY,
})
NEVER_AUTOMATIC_HEADER = frozenset({"vendor_name", "currency", "place_of_supply", "ship_to", "payment_terms", "delivery_date", "revision", "status"})
NEVER_AUTOMATIC_LINE = frozenset({"description", "item_code", "hsn_sac", "uom", "discount", "tax_rate", "cgst_amount", "sgst_amount", "igst_amount", "po_ref", "po_line_ref", "line_type"})
VALUE_KINDS: Mapping[str, str]  # header: doc_no→"doc_no"; doc_date→"date"; vendor_gstin, buyer_gstin→"gstin";
                                # amounts→"money"; po_refs, grn_refs→"refs"; challan_no, supplier_invoice_no, e_way_bill_no, irn→"ref"
LINE_VALUE_KINDS: Mapping[str, str]  # amount→"money"; rate and all quantities→"number"
HARD_VALIDATORS = frozenset({"gstin_checksum", "buyer_entity", "gst_type"})
```

Data classes (all `@dataclass(frozen=True, slots=True)`):
```python
@dataclass(frozen=True, slots=True, order=True)
class FieldKey:
    scope: Literal["header", "line"]
    name: str
    line_no: int = 0                       # 0 for header fields
    def text(self) -> str: ...             # "header:doc_no" or "line:3:amount"
    @classmethod
    def parse(cls, text: str) -> FieldKey: ...

ConsensusValue = str | Decimal | date | tuple[str, ...]

class Reading:      reader: ReaderKind; value: ConsensusValue; page: int | None = None; box: tuple[int, int, int, int] | None = None   # box only for vision: (ymin, xmin, ymax, xmax) on 0–1000
class CropCheck:    outcome: CropOutcome; text: str = ""              # at most 200 characters of the crop's OCR text
class CropNeed:     key: FieldKey; kind: str; value: ConsensusValue; page: int; box: tuple[int, int, int, int]; mode: Literal["line", "block"]
class ConsensusPolicy:
    auto_verify: bool = False
    reopen: bool = False
    field_types: frozenset[FieldType] = frozenset()
    require_crop: bool = True
    as_of: date | None = None
    date_past_days: int = DEFAULT_DATE_PAST_DAYS
    date_future_days: int = DEFAULT_DATE_FUTURE_DAYS
class FieldDecision:
    key: FieldKey; field_type: FieldType | None; level: TrustLevel; rule: DecisionRule; reason: str
    readings: tuple[Reading, ...]; supporting: tuple[ReaderKind, ...]
    crop: CropCheck | None = None; failed_validators: tuple[str, ...] = ()
    auto_verified: bool = False; reopened: bool = False
    def to_json(self) -> dict[str, object]: ...   # Decimal→str, date→ISO, tuple→list, enums→value
class ConsensusResult:
    version: str; decisions: tuple[FieldDecision, ...]; document: Document; changed: bool
    vision_only_lines: int
    hints: Mapping[str, tuple[str, ...]]          # keys exactly "annotation_refs", "marketplace_order_refs", "page_kinds"
    def summary(self) -> dict[str, object]: ...   # {"levels": {...}, "by_field_type": {ft: {level: n}}, "auto_verified": n, "reopened": n, "vision_only_lines": n}
    def decisions_json(self) -> list[dict[str, object]]: ...
```

Public functions (exact signatures):
```python
def rules_readings(document: Document, *, structured: bool, rules_source: ReaderKind) -> dict[FieldKey, tuple[Reading, ...]]
def vision_readings(document: Document, check: VisionCheck) -> tuple[dict[FieldKey, Reading], int]
def values_agree(kind: str, left: ConsensusValue | None, right: ConsensusValue | None) -> bool
def field_validators(key: FieldKey, value: ConsensusValue, document: Document, *, hints: ParserHints, policy: ConsensusPolicy) -> tuple[str, ...]
def crop_outcome(kind: str, value: ConsensusValue, text: str, *, mode: Literal["line", "block"]) -> CropOutcome
def decide(document: Document, *, vision: VisionCheck | None, crops: Mapping[FieldKey, CropCheck] | None, hints: ParserHints, policy: ConsensusPolicy, structured: bool = False, rules_source: ReaderKind = ReaderKind.OCR) -> ConsensusResult
def crop_needs(result: ConsensusResult) -> tuple[CropNeed, ...]
```

`rules_readings`: for each header attribute in `HEADER_FIELD_TYPES` or `NEVER_AUTOMATIC_HEADER` whose `FieldValue` is not `None`, and each line cell in `LINE_FIELD_TYPES` or `NEVER_AUTOMATIC_LINE`:
- source `FieldSource.MANUAL` → `(Reading(MANUAL, value),)`;
- `structured=True` → `(Reading(STRUCTURED, value),)`;
- otherwise read the QR merge status `document.header.extras.get("einvoice", {}).get("fields", {}).get(name)` (written by `_apply`, `backend/reconbench_core/extraction/qr.py:315-329`):
  - `"signed"` → `(QR_SIGNED,)`; `"confirmed"` → `(rules_source, QR)`, both with the field value; `"corrected"` and `"prefilled"` → `(QR,)`;
  - `"conflict"` → `(QR,)` with the field value **plus** `Reading(rules_source, <other reading>)`, where the other reading is `extras["einvoice"]["other_readings"][name]` (a `str(...)` of the OCR value, `qr.py:238,267,277`) parsed per kind (`date.fromisoformat`, `Decimal`, `str`); skip it if parsing fails;
  - no status → `(rules_source,)`.
- `rules_source` is decided by the caller: `document.header.extras["source"]` (`"ocr"`/`"text_layer"`, written by the layout path at `backend/reconbench_core/extraction/extract.py:1001-1004`) when present, otherwise the caller's knowledge (app: `document_version.extraction_method == "pdf_ocr"`; bench: any `is_ocr` page).

`vision_readings`: header fields come from `check.fields` mapped with the **same** maps as `vision_document` (`backend/reconbench_core/extraction/vision.py:702-734`, used at `:787-806`): `VISION_HEADER_FIELDS` + `VISION_TYPE_FIELDS[doc_type]`; reference fields through `VISION_REF_FIELDS[doc_type]` with the value = tuple of all accepted values in order, de-duplicated; the first accepted field wins for single-valued names; box = `item.box.as_list()` as a tuple, page = `item.page`. Lines: pair `check.lines` with `document.lines` using `align_lines` (`vision.py:889-949`) on rows `{"line_no", "amount", "quantity", "rate", "description"}` built from each `DocLine`'s plain values (quantity = `quantity` or `received_qty` or `accepted_qty`); map cells with `VISION_LINE_FIELDS` (`vision.py:735-752`); on a GRN a vision `quantity` cell is read as `received_qty` when the line has no `quantity` (as `vision.py:862-863`); box = the row box. Return the number of unpaired vision rows as the second value. Hints: `annotation_refs` = texts of `check.annotations` whose `looks_like` is `po_number`, `grn_number` or `invoice_number` (as `vision.py:815-819`); `marketplace_order_refs` = values of accepted fields named `marketplace_order_ref`; `page_kinds` = `f"{page}:{kind}"` for `check.page_kinds`.

`values_agree`: `None` on either side → `False`. `"money"`: equal after `quantize(Decimal("0.01"))`; `"number"`: equal after `quantize(Decimal("0.0001"))`; `"date"`: equal dates; `"refs"`: non-empty and `frozenset(_compact(x) for x in a) == frozenset(_compact(x) for x in b)`; `"doc_no"`, `"ref"`, `"gstin"`: `_compact(a) == _compact(b)` with `_compact(v) = "".join(str(v).split()).upper()`. **No look-alike folding** (D18: folding hides exactly the error both readers can make).

`field_validators` (returns the names that fail; exact names):

| Field type | Name | Rule (existing function) |
|---|---|---|
| all | `split_unconfirmed` | fails when `split_unconfirmed(document)` (`backend/reconbench_core/extraction/pages.py:257-264`) — D42 rule 4 |
| DOC_NO | `doc_no_shape` | `accept_doc_no(value) == value` (`extract.py:233-254`) |
| DOC_NO | `lookalike` | fails when `has_lookalike(value)` (`backend/reconbench_core/extraction/values.py:424-444`); AC-10 adds the vendor-pattern exception |
| DOC_DATE | `date_window` | `policy.as_of` set and `as_of − date_past_days ≤ value ≤ as_of + date_future_days`; fails when `as_of` is `None` |
| GSTIN | `gstin_checksum` (hard) | `validate_gstin(value).is_valid` (`backend/reconbench_core/normalize/gstin.py`) |
| GSTIN | `buyer_entity` (hard) | `vendor_gstin` not in `buyer_gstins(hints.buyer_entities)` (`backend/reconbench_core/parsers/parties.py:83-84`); `buyer_gstin` in it when buyer entities are configured |
| AMOUNT | `gst_type` (hard for `cgst`, `sgst`, `igst`) | no `GST_TYPE_MISMATCH` from `gst_problems(document, state=buyer_state(document.header, hints.buyer_entities), rates=hints.gst_rates)` (`parties.py:143-219`) |
| AMOUNT | `gst_structure` | no other problem from the same call |
| AMOUNT | `totals_arithmetic` | `arithmetic_failures` (`backend/reconbench_core/parsers/common.py:712-743`) on the document's plain values reports neither `"taxable total"` nor `"grand total"` |
| AMOUNT | `non_negative` | value ≥ 0 (not checked for `round_off`) |
| REFERENCE | `reference_shape` | `po_refs`/`grn_refs`: every item satisfies `accept_po(item) == item` (`extract.py:273-288`) and not `has_lookalike(item)`; `challan_no`, `supplier_invoice_no`: `accept_doc_no(value) == value` and not `has_lookalike`; `e_way_bill_no`: exactly 12 digits; `irn`: 64 hexadecimal characters |
| LINE_* | `line_arithmetic` | `arithmetic_failures` reports no `f"line {n} amount"` for the line |
| LINE_* | `positive` | quantity > 0, rate ≥ 0, amount ≥ 0 |

`decide` (deterministic; iterate `sorted(keys)`):
```
VERIFIED = extract.VERIFIED_CONFIDENCE                      # 0.98, extract.py:72
rules = rules_readings(document, structured=structured, rules_source=rules_source)
vision_map, unpaired = vision_readings(document, vision) if vision else ({}, 0)
for key in sorted(set(rules) | set(vision_map)):
    field = FieldValue on the document for key, or None
    vis = vision_map.get(key); readings = rules.get(key, ()) + ((vis,) if vis else ())
    ftype = HEADER/LINE_FIELD_TYPES lookup (None for never-automatic names); kind = VALUE_KINDS lookup
    if field is None:                                   -> L2, VISION_ONLY, "vision_only"   (value never written)
    elif any reading is MANUAL:                         -> L0, EXISTING, "manual"
    elif any reading is STRUCTURED:                     -> L0, EXISTING, "structured"
    elif any reading is QR_SIGNED:                      -> L0, EXISTING, "signed_qr"
    elif field.confidence >= VERIFIED:
        if vis and ftype and not values_agree(kind, field.value, vis.value):
                                                        -> L3, REOPEN, "vision_disagrees_with_verified", reopened=policy.reopen
        else:                                           -> L1, EXISTING, "rules_verified"
    elif ftype is None:                                 -> L2, EXCLUDED, "never_automatic"
    else:
        supporters = [r for r in readings if values_agree(kind, field.value, r.value)]
        dissent    = [r for r in readings if not values_agree(kind, field.value, r.value)]
        if dissent:                                     -> L3, CONFLICT, "readers_disagree"
        elif len({r.reader for r in supporters}) < 2:   -> L2, SINGLE, "one_reader"
        else:
            failed = field_validators(key, field.value, document, hints=hints, policy=policy)
            crop = (crops or {}).get(key)
            if set(failed) & HARD_VALIDATORS:           -> REJECTED, D42, "hard_validator", failed_validators=failed
            elif failed:                                -> L2, D42, "validator_failed", failed_validators=failed
            elif policy.require_crop and (crop is None or crop.outcome is not CropOutcome.AGREE):
                                                        -> L2, D42, "crop_" + (crop.outcome.value if crop else "missing"), crop=crop
            else:                                       -> L1, D42, "two_readers_agree", crop=crop,
                                                           auto_verified = policy.auto_verify and ftype in policy.field_types
```
A `d42` L1 always has the vision reading plus one non-AI reader among `supporters`: two non-AI readers that agree already give confidence ≥ 0.98 (dual read, QR "confirmed"). There is at most one vision reading per key, so two answers of one model never count twice (rule 1).

Apply step (end of `decide`):
```
if no decision has auto_verified=True and no decision has reopened=True:
    return ConsensusResult(document=document, changed=False, ...)           # the same object
new header fields: auto_verified → field.model_copy(update={"confidence": VERIFIED})
                   reopened      → field.model_copy(update={"confidence": min(field.confidence, REOPEN_CONFIDENCE)})
new line cells: the same per cell (DocLine.model_copy)
listed   = list(header.extras.get("review_fields", []))
resolved = {names of auto-verified header fields}
         | ({"vendor"} if vendor_gstin was auto-verified else set())
         | {f"line {n}" for every line with an auto-verified cell for which _line_needs_review(new line) is False}
review   = [item for item in listed if item not in resolved]
         + [name for reopened header names if name not in listed]
         + [f"line {n}" for lines with a reopened cell if f"line {n}" not in listed]
other    = any(code is not ReasonCode.LOW_CONFIDENCE_DATA for code in document.reason_codes)
needs    = bool(review) or other or (document.needs_verification and not listed) or split_unconfirmed(document)
reason_codes = document.reason_codes if needs else (); if needs and not reason_codes: (ReasonCode.LOW_CONFIDENCE_DATA,)
extras   = {**header.extras, "review_fields": review,
            "consensus": {"version": CONSENSUS_VERSION,
                          "auto_verified": [key.text() ...], "reopened": [key.text() ...]}}
```
`_line_needs_review(line)` repeats the line predicate of `_apply_checks` (`extract.py:1064-1077`). The "clear only when every listed reason is resolved" rule is the one `_dual_read` (`backend/reconbench_app/extraction/pipeline.py:85-99`) and the QR merge (`qr.py:331-344`) already use.

Invariants (each has a test below): **I1** consensus never changes a value — only `confidence`, `extras.review_fields`, `extras.consensus`, `needs_verification` and `reason_codes`; **I2** L0 fields and person-entered values are never touched; **I3** with `auto_verify=False` and `reopen=False` the result's document is the input object and `changed` is `False`; **I4** a value read only by vision is never written; **I5** the result does not depend on the order of `check.fields` or `check.lines`; **I6** a never-automatic name never gets `auto_verified=True`.

`crop_needs(result)`: one `CropNeed` for each decision with `rule is D42` and `reason == "crop_missing"` whose vision reading has a page and a box; `mode="line"` for header keys and `"block"` for line keys; `value` = the document value.

`crop_outcome(kind, value, text, mode=...)`: collapse whitespace; empty → `EMPTY`. Candidates: `"money"`/`"number"`: `parse_money` / `parse_quantity` (`vision.py:348-372`) of every token and of every pair of adjacent tokens joined without a space (OCR may split a number); in `"block"` mode, ignore the first token when it is an integer of at most three digits (a row's serial number). `"date"`: values of `find_dates(text)` (`values.py:155-187`). `"gstin"`: `[r.value for r in gstins_in(text) if not r.repaired]` (`values.py:261-276`). `"doc_no"`/`"ref"`: every token, every adjacent pair joined, and the whole compacted text. `"refs"`: every item of the value must be found as for `"ref"`. Result `AGREE` when `values_agree(kind, value, candidate)` for some candidate, else `DISAGREE`. `MASKED` and `UNAVAILABLE` are set only by the caller (AC-6b).

Edge cases from the real-sample findings (generic) and how these rules handle them:
- Document numbers with O/0 or I/1 between letters and digits (and the same misreading on every re-scan): `lookalike` fails → L2 (the QR look-alike rule of D34 still verifies QR invoices).
- The rupee sign read as a leading digit: the D18 solver may already have confirmed the shorter amount (L1 `existing`); a disagreeing vision amount only re-opens it when `reopen` is on.
- The buyer's own GSTIN in the supplier slot: AC-1 already moved it (`guard_document`), `check_reading` drops a vision supplier GSTIN equal to a buyer entity, and `buyer_entity` rejects anything left.
- IGST on an intra-state invoice (or CGST/SGST across states): `gst_type` rejects that tax field and `gst_structure` blocks every other total.
- Totals taken from an odd summary row or a line amount read as CGST: `totals_arithmetic` or `gst_structure` fails → L2.
- Handwritten PO/GRN numbers: vision `annotations` → `hints` only (rule 4). Marketplace order numbers never become `po_refs` (`accept_po` rejects the pattern, `extract.py:284-285`).
- A bundle whose split is not confirmed: `split_unconfirmed` fails every field.
- Vendor names (the D41 agree-wrong case): never automatic.
- An e-invoice QR that disagrees with OCR: three readings; any dissent → L3.

c.3 Bench shadow block (`scripts/accuracy_consensus.py`, **NEW**). For each vision target with an accepted reading, call `decide(...)` with the shadow policy (`auto_verify=True`, `reopen=True`, `field_types=frozenset(FieldType)`, `as_of` = the truth file's `run_date`, `require_crop=False` for the `upper_bound` block in this phase), and score:
- per field type: `l1` = decisions with `level is L1` and `rule is D42`; for a header field present in the truth header, `normal()`/`same()` of `accuracy_bench.py:453-476` decide `l1_correct` or `l1_wrong`; absent → `l1_unscored`. A line cell is correct when some truth line has the same cell value (`quantity`/`rate`/`amount`, compared after `quantize(Decimal("0.01"))`), wrong otherwise, unscored when the truth has no lines. Also count `l2`, `l3`, `rejected`, `excluded`.
- `reopened`: `total`, `ocr_wrong` (the truth says the document value is wrong: a true catch), `ocr_right` (a false alarm), `unscored`.
- `shadow`: `extraction_summary` of `score_document(truth, file_id, result.document)` over the shadow documents; `shadow_false_safe` = documents with `false_safe`; each such document is attributed to every field type auto-verified in it.
- `gate[ft]` = `"no_evidence"` when `l1_correct + l1_wrong == 0`, `"fail"` when `l1_wrong > 0` or an attributed shadow false-safe exists, else `"pass"`.
The block contains counts and labels only.

**d. Change list**

1. **NEW** `backend/reconbench_core/extraction/consensus.py`: everything in c.2; `__all__` lists every public name.
2. **EDIT** `backend/reconbench_core/extraction/vision.py`: after `_LINE_MAP` (ends at line 752) add public read-only views and list them in `__all__` (`:952-980`); no behaviour change:
   ```python
   VISION_HEADER_FIELDS: Mapping[str, str] = MappingProxyType(_HEADER_MAP)
   VISION_TYPE_FIELDS: Mapping[DocType, Mapping[str, str]] = MappingProxyType({key: MappingProxyType(dict(value)) for key, value in _TYPE_MAP.items()})
   VISION_REF_FIELDS: Mapping[DocType, Mapping[str, str]] = MappingProxyType({key: MappingProxyType(dict(value)) for key, value in _REF_FIELDS.items()})
   VISION_LINE_FIELDS: Mapping[str, str] = MappingProxyType(_LINE_MAP)
   ```
3. RULES-HASH-UPDATE (`ENGINE_VERSION` unchanged: `extraction` is not a rule path).
4. **NEW** `scripts/accuracy_consensus.py`:
   ```python
   def shadow_policy(*, run_date: date, require_crop: bool) -> ConsensusPolicy
   def rules_source_for(target: accuracy_vision.VisionTarget) -> ReaderKind   # header extras "source" if present, else OCR when target.ocr_pages else TEXT_LAYER
   def summary(run: accuracy_vision.VisionRun, truth_by_id: Mapping[str, Any], score_document: Callable[..., Any], extraction_summary: Callable[[list[Any]], dict[str, Any]], normal: Callable[[str, Any], Any], same: Callable[[str, Any, Any], bool], *, buyer_entities: Sequence[BuyerEntityHint], run_date: date, require_crop: bool, crops: Mapping[tuple[str, int], Mapping[FieldKey, CropCheck]] | None = None) -> dict[str, Any]
   ```
   `summary` builds `ParserHints(doc_type=target.doc_type, buyer_entities=tuple(buyer_entities))` per target and returns the block of c.3 with keys exactly: `mode` (`"shadow"`), `require_crop`, `documents`, `levels`, `rules`, `by_field_type`, `reopened`, `vision_only_lines`, `shadow`, `shadow_false_safe`, `gate`.
5. **EDIT** `scripts/accuracy_vision.py`: add `ocr_pages: bool = False` to `VisionTarget` (`:73-90`) after `output_tokens`.
6. **EDIT** `scripts/accuracy_bench.py`:
   - `reader_fingerprint` (`:339-359`): replace the skip at `:347-348` with `if path.name.startswith(("vision", "consensus")):` and comment "the vision reader (AC-5) and consensus (AC-6) never change a rules reading" — so the new module does not invalidate the reader cache.
   - `run_vision` (`:1269-1350`): set `ocr_pages=any(page.is_ocr for page in outcome.result.page_texts)` on each `VisionTarget` and return `tuple[dict[str, Any], accuracy_vision.VisionRun]`; update the call at `:1533-1535` (`vision_block, vision_run = run_vision(...)`, with `vision_run = None` when vision is off).
   - `main` (`:1631-1687`): new option `--consensus` with choices `("off", "shadow")`, default `"shadow"`.
   - `run` (`:1460-1628`): after `report["vision_hash"]` (`:1602-1604`), when `arguments.consensus == "shadow"` and `vision_run is not None`, set `block = {"upper_bound": accuracy_consensus.summary(vision_run, truth.by_id(), score_document, extraction_summary, _normal, _same, buyer_entities=truth.buyer_entities_hints(), run_date=truth.run_date, require_crop=False)}`, `report["consensus"] = block`, `report["consensus_hash"] = _sha256(_canonical_json(block))`, and print it with **NEW** `_print_consensus(block)` after `_print_vision` (one line per field type: `l1`, correct, wrong, gate). `report_hash` is computed before this (`:1601`), so it does not change.
7. **EDIT** `.github/workflows/ci.yml:279`: `upgrade 0017` → `upgrade 0019` (bug B5).

**e. Tests (synthetic only)**

**NEW** `backend/tests/unit/test_consensus_core.py` (build documents with `Document.model_validate` and readings with `check_reading(VisionReading(...), pages={1: SentPage(1)}, ...)` as `backend/tests/unit/test_vision_core.py` does; invent values such as `"INV-000123"`, never values from `work/`):
1. `test_ocr_and_vision_agreement_with_crop_and_validators_is_l1` — an unverified OCR `doc_no` (0.65), the same vision value, crop `AGREE`, `as_of` set → L1 `d42`, `auto_verified` when the policy lists `doc_no`; the document's `doc_no` confidence becomes 0.98 and its value is unchanged.
2. `test_policy_off_returns_the_same_document` (I3).
3. `test_auto_verify_needs_the_field_type_in_the_policy`.
4. `test_vision_only_value_is_l2_and_never_written` (I4).
5. `test_disagreement_is_l3_with_both_readings`.
6. `test_a_dissenting_third_reader_blocks_l1` — QR status `"conflict"` with an OCR other reading; vision agrees with the QR value → L3.
7. `test_rules_verified_value_stays_l1_existing`.
8. `test_vision_disagreement_reopens_a_verified_field_only_when_policy_reopens` — confidence drops to 0.65 and the field is listed in `review_fields` only with `reopen=True`.
9. `test_manual_structured_and_signed_qr_values_are_l0_and_untouched` (I2).
10. `test_hard_validators_reject` — buyer-entity GSTIN as `vendor_gstin`; invalid checksum; IGST on an intra-state document → `rejected`.
11. `test_soft_validators_keep_l2` — totals arithmetic fails; a date outside the window; `as_of=None`; a `doc_no` that `accept_doc_no` changes.
12. `test_lookalike_document_number_is_never_auto_verified` — for example `"INVO0123"`.
13. `test_crop_missing_or_disagreeing_keeps_l2_and_require_crop_false_allows_l1`.
14. `test_never_automatic_fields_are_excluded` — agreeing vendor names stay L2 `excluded` (I6).
15. `test_split_unconfirmed_document_never_auto_verifies`.
16. `test_review_flag_clears_only_when_every_listed_reason_is_resolved` — two listed fields with one resolved → still in review; both resolved → cleared; a non-LOW_CONFIDENCE reason code keeps it in review.
17. `test_line_cells_pair_with_vision_rows_and_use_the_row_crop` — LINE_AMOUNT L1 through a row crop; an unpaired vision row counts in `vision_only_lines`.
18. `test_grn_vision_quantity_is_read_as_received_quantity`.
19. `test_values_never_change` — Hypothesis over policies, agreement flags and crop outcomes: every value of the result document equals the input (I1).
20. `test_decide_is_order_independent` — Hypothesis shuffles `check.fields` and `check.lines`; identical `decisions_json()` (I5).
21. `test_crop_outcome_candidates` — parametrised over money, number, date, gstin, doc_no and refs; the serial-number skip in `"block"` mode; `EMPTY`.
22. `test_hints_carry_annotations_and_marketplace_orders_but_no_fields`.
23. `test_crop_needs_lists_only_d42_keys_waiting_for_a_crop`.
24. `test_decisions_json_is_stable_and_serialisable` (`json.dumps` works; same output twice).

**NEW** `backend/tests/unit/test_accuracy_consensus.py` (reuse the `_target`/`_prepare`/recorded-answer pattern of `backend/tests/unit/test_accuracy_vision.py:35-142` with `synthetic_invoice_pdf()` and `recorded_response()`):
1. `test_shadow_block_counts_l1_correct_and_wrong_per_field_type` — a truth with one right and one wrong header value.
2. `test_gate_is_no_evidence_without_scored_l1_and_fail_on_any_wrong`.
3. `test_consensus_block_holds_no_document_values` — `INVOICE_NO`, `SUPPLIER_GSTIN` and `GRAND` are not in `json.dumps(block)`.
4. `test_reader_fingerprint_ignores_consensus_modules` — monkeypatch `accuracy_bench.ROOT` to a temporary tree containing the `_CODE_PATHS` folders and `_tool_version` to a constant; changing a file named `consensus.py` leaves `reader_fingerprint` unchanged, changing `extract.py` changes it.

**f. Verification (in order)**

1. CHK-PY; `ruff format --check` on: `reconbench_core\extraction\consensus.py`, `reconbench_core\extraction\vision.py`, `tests\unit\test_consensus_core.py`, `tests\unit\test_accuracy_consensus.py`, `..\scripts\accuracy_consensus.py`, `..\scripts\accuracy_bench.py`, `..\scripts\accuracy_vision.py`. Expected: all pass; mypy core "no issues found in 36 source files".
2. TEST-OFFLINE: previous 682 + about 30 new tests pass.
3. BENCH-OFFLINE `after-ac6a.json`: `report=31cb1ec49644`, `GATE: PASS`, cache hits 35, vision unchanged; the consensus lines print; run it twice and compare `consensus_hash`.
4. No PG, OPENAPI or WEB step (nothing in the app changed).

**g. Risks, rollback, must not change**

- Must not change: any parser or extraction output (the main `report_hash` proves it), the vision block (`vision_hash`), vision versions (§0.4), every existing test.
- Risk: the new module imports `parsers.common` and `parsers.parties`; an import cycle would show at test collection. Import only modules that the existing `vision.py` and `extract.py` already import, plus `parsers.common.arithmetic_failures` and `extraction.pages.split_unconfirmed`.
- Rollback: delete the new files and revert the listed edits (nothing else depends on them yet).

**h. Size**: large (≈700–900 lines of core code, ≈600 lines of tests, ≈250 lines of bench code). One session.

**i. Docs**: decision-log **D44** "AC-6a consensus core and shadow bench" with: the trust-level table as built, the validator list, the upper-bound counts per field type (`l1`, correct, wrong, gate), `reopened` (true catches vs false alarms), shadow false-safe, and the B3 explanation (`ai_cache_id` is a cache-hit marker; answers are found by `cache_key`). `docs/accuracy-plan.md` line 3: AC-6a done; next AC-6b.


