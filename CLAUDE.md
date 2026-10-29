# ReconBench — Claude instructions

## Purpose
- Auditable, invoice-centred three-way reconciliation (purchase order ↔ goods-received note ↔ supplier invoice) for Indian GST invoices.
- Python 3.12 backend (FastAPI API, Procrastinate workers, separate AI worker) on PostgreSQL 16 + S3/MinIO; React/Vite web app in `web/`.
- `backend/reconbench_core` is a pure, Decimal-only rules library; `backend/reconbench_app` holds DB, storage, queues, API and AI adapters.

## Pipeline map
- Upload → `reconbench_app/api/ingestion.py` (`POST /api/v1/files`, raw bytes + `X-File-Name`) → `ingestion.py:IngestionService` → sniff, size/ZIP limits, store by SHA-256, queue ingest/parse (`jobs.py`)
- Parse dispatch → `ingestion.py:IngestionService.parse_file` → PDF via `pdf_sandbox.py:parse_pdf_isolated`; XML/CSV via `reconbench_core/parsers/__init__.py:parse` (`xml.py:parse_xml`, `csv.py:parse_csv`)
- Digital vs scanned → `reconbench_app/extraction/local_hybrid.py:is_scanned` (any image ≥ `SCANNED_IMAGE_COVERAGE`=0.5 of page area) → text layer (pdfplumber) or 300-DPI render + Tesseract OCR, per page
- Layout/rules extraction → `reconbench_app/extraction/pipeline.py:layout_parse` → `reconbench_core/extraction/extract.py:extract_document` (+ `table.py`, `header.py`, `values.py`)
- Validation → `reconbench_core/extraction/verify.py` (line/header arithmetic, confusable digits), GSTIN checksum, confidence gate
- AI reading (scanned only, optional) → `reconbench_app/extraction/vision.py` + `reconbench_app/ai/worker/` (Gemini `gemini.py`, vLLM `openai_compat.py`) → suggestions needing a person's check
- Vendor mapping → `reconbench_core/mapping/vendor.py:resolve_vendors` via `reconbench_app/vendor_resolution.py:VendorResolutionService`
- Document links → `reconbench_core/mapping/links.py:resolve_links` via `reconbench_app/mapping_service.py:MappingService.resolve_workspace`
- Line alignment → `reconbench_core/align/__init__.py:align_lines` (RapidFuzz + Hungarian assignment)
- PO ledger → `reconbench_app/ledger_service.py:LedgerRebuildService`
- Reconciliation → `reconbench_app/reconciliation_service.py:ReconciliationService.evaluate_run` → pure `reconbench_core/recon/engine.py:evaluate`; statuses in `reconbench_core/schema/__init__.py:CaseStatus`
- Review / export → `reconbench_app/api/review.py`; `reconbench_app/export_service.py:ExportService.build_export` (xlsx to object store)
- Offline engine CLI → `python -m reconbench_core.cli run <bundle> --config … --run-date … --out …`

## Commands (run from repo root)
- Offline Python tests: `backend/.venv/Scripts/pytest.exe backend/tests/unit backend/tests/parsers backend/tests/property backend/tests/scenarios -q` — ran OK (see PROJECT_STATUS)
- Same via Make: `make test` (also runs web vitest; needs Node) — from config, not run
- Lint/types: `make check-python` (ruff, `mypy --strict` on core, `lint-imports`); `make check-static` adds web lint/typecheck, purity, rules hash, OpenAPI diff — from config, not run
- Integration tests: `pytest backend/tests/integration` — need a disposable PostgreSQL (`RB_PHASE2_TEST_DATABASE_URL`) and MinIO — from config, not run
- Stack: `make up`, `make down`, `make migrate`, `make queue-schema`, `make web`, `make demo` — from config, not run
- Golden harness: `make golden`; engine CLI smoke: see CI `engine-reproducibility` — from config, not run
- Accuracy bench: `make accuracy-bench` (Docker worker image, no network, reads git-ignored `work/`) — from config, not run; provider variants are owner-only

## Conventions & pitfalls
- `reconbench_core` must not import `reconbench_app`, DB, HTTP or LLM SDKs, read env/clock/random, or use floats: enforced by import-linter contracts (`backend/pyproject.toml`) and `scripts/check_core_purity.py`.
- mypy --strict is the source of truth for type errors; LSP diagnostics are hints.
- Pyright's 3 errors in `reconbench_app/extraction/local_hybrid.py` (`pdfplumber.page` attribute ×2, pypdfium2 `render(scale=float)`) are not reported by mypy (it doesn't check `render` because pypdfium2 is imported as untyped; why it misses the two `pdfplumber.page` errors is unknown) and are not confirmed bugs; don't change code for them unless the owner asks.
- Changing rule paths (`reconbench_core/{align,mapping,normalize,recon,schema}`, `reconbench_app/db/seed_data.py`) requires bumping `ENGINE_VERSION` in `backend/reconbench_core/version.py` (CI `scripts/check_engine_version.py`) and keeping `scripts/check_rules_hash.py` green.
- API changes: regenerate `backend/openapi.json`, `web/src/api/generated.ts` and `docs/schemas` (`make openapi`); CI fails on a diff.
- Schema changes: new Alembic revision in `backend/migrations`; CI tests the migration on a copy of the previous revision (`scripts/check_migration_safety.py`). The Procrastinate queue schema is separate (`make queue-schema`).
- The six `docs/ReconBench_*.md` source documents are read-only. Authoritative project history: `docs/decision-log.md` (D-numbers) and `docs/accuracy-plan.md` (status §0, post-demo plan §15).
- `work/`, `golden/**/inputs|documents`, `deploy/secrets/` and `.env` are git-ignored and may hold real invoices, truth data or keys: never open, copy or quote them.
- Hard-zero rule for every change: no false Matched, no false auto-links, no auto-accepted wrong values (README; `backend/tests/scenarios/test_hard_zero_guards.py`).
- Windows host: use `backend/.venv/Scripts/*.exe`; Node is not on this machine's PATH, so web checks need Docker (`make web-docker`) or a Node install.
- Do not call Gemini or the company vLLM server without the owner's go; provider calls are budgeted per run.

## Project memory
- Current status (auto-loaded): @docs/claude/PROJECT_STATUS.md
- Decisions (read on demand): docs/claude/DECISIONS.md
- Known issues (read on demand): docs/claude/KNOWN_ISSUES.md

## Working rules
Priority: correctness > safety > token savings. Never skip necessary verification to save tokens.

### Starting a task
- CLAUDE.md and PROJECT_STATUS.md are already in context; don't re-read them just for context.
- Simple question: answer directly, without exploring the repo.
- Before a code task: `git status --short`, `git log --oneline -5`, then `git diff <Last verified commit> --stat -- <paths the task touches>`; re-check anything changed before trusting memory about it. If that commit no longer exists, treat memory about those paths as stale.
- Open DECISIONS.md or KNOWN_ISSUES.md only when the task touches a decision or a known issue.
- Don't ask for anything these files or the repo can answer. Ask only about blockers, risky trade-offs, or the approvals under Safety.

### While working
- Workflow: understand → inspect only relevant context → find the root cause with evidence → smallest correct change → verify → update memory if needed → summarize.
- Complex task: a 3–5 line plan first.
- Search (Grep/Glob or symbol lookup) before reading; read only needed parts of large files; never re-read an unchanged file or repeat a tool call whose result you already have.
- Don't open PDFs, images, binaries, lockfiles, generated output, vendored code, virtualenvs or data/fixture files unless the task needs them.
- Filter command output; never paste long logs.
- Focused tests first; the full suite only when the change is broad.
- Use a subagent only when its output would be large (full test run, log scan, wide search).
- Follow existing conventions. Don't touch unrelated code or change architecture or business logic without asking.
- Prefer built-in tools, installed skills/plugins and existing dependencies. Suggest a new skill or plugin only for a verified need, with its exact `name@marketplace` from a catalog you actually read, the reason, and its always-on token cost if known. Never invent names or install commands.

### Accuracy
- Label claims: verified (cite path + function, or the command run) / hypothesis / unknown.
- Never invent test results, metrics, paths, APIs or completed features. "Done" requires a passing test or a command run in this session.
- README text, comments, docstrings and file names are claims, not proof. Memory files are a starting point; re-check the code when correctness depends on it.

### Safety
- Commit only when asked. Ask before: installing or upgrading anything, adding dependencies, editing lockfiles, changing settings/permissions/global config, deleting files, pushing, switching branches, stash/reset/rebase/force operations, or reverting the user's changes. If an install is approved, use the documented method (plugins: local scope unless told otherwise), confirm it works, and record it in DECISIONS.md.
- Never write secrets, internal hostnames/URLs or real invoice/customer data (names, amounts, tax IDs, bank details, addresses) into code, docs, memory files, commits or replies. Use placeholders.

### Keeping memory current (meaningful tasks only; skip trivial edits)
- Edit only the affected lines: PROJECT_STATUS (component status, test results, active task, next steps, Last verified), DECISIONS (new entry), KNOWN_ISSUES (add with evidence; remove only after the fix is verified).
- Project state lives in docs/claude/ (versioned and shared), not in auto memory. If auto memory contradicts these files, say so.
- Module-specific detail goes in a path-scoped rule (`.claude/rules/*.md` with `paths:` frontmatter) or a nested CLAUDE.md, not here.

### Reply format
- End a task with: Changed · Verified (how) · Not verified · Next, in 10 lines or fewer. Don't repeat earlier explanations or code; expand only if asked or a mistake is likely.

# Compact instructions
When compacting, keep: the active task, files changed, commands run and their results, failing tests and errors, decisions made, and open questions.
