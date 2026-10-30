# Project status
Last verified: 2026-10-30, commit 493bc9e live as live-20261030-b6 (D110/D112), switch on rules; Step 7 Phase 1 PD-5c measured on beta (D112); CODEBASE_CONTEXT.md untracked

## Components
Status: working-verified | implemented-untested | partial | stub | broken | not-found | unknown
Unit-level "working-verified" = offline suite passed this session; not proven on real invoices.
- Ingestion (upload, sniff, ZIP, dedupe by hash) — implemented-untested here (DB integration tests skipped) — reconbench_app/ingestion.py, tests/integration/test_ingestion.py
- Digital vs scanned PDF routing — working-verified (unit) — local_hybrid.py:is_scanned; tests/parsers/test_phase4_pdf.py, test_scan_images.py
- Rules extraction (PDF/XML/CSV/Tally) — working-verified (unit); accuracy target not met — tests/parsers/*; docs/accuracy-plan.md §0
- AI vision reading (Gemini / vLLM) — implemented; with the switch on AI the reading becomes the current version (PD-2e, D109; live D110, switch on rules); one real company-server reading on a throwaway copy of live passed (D111) — reconbench_app/ai/worker/, ai/consensus.py, reconbench_core/extraction/merge.py; tests/unit/test_pd2e_merge.py, tests/integration/test_ai_vision.py
- Waiting for reading (PD-2f, D109; live D110) — working-verified (integration, disposable DB) — reconbench_app/reading_state.py, migration 0036; tests/integration/test_pd2f_waiting_for_reading.py
- Scanned-invoice switch + 0 LLM calls outside AI reading (PD-2c/PD-2d, D107) — working-verified (unit + integration on disposable DB); live 29 Oct (D108) — ai/vision.py, ai/settings_service.py, migration 0035; tests/integration/test_pd2c_pd2d_reading.py
- Validation (arithmetic, GSTIN, confidence) — working-verified (unit) — reconbench_core/extraction/verify.py; tests/property
- Vendor + link mapping — working-verified (unit); D109: no auto link while an AI value is unverified, PD-2h self-support guard — tests/unit/test_phase4_mapping_links.py, test_phase4_vendor_mapping.py, test_q2_typed_links.py
- Line alignment — working-verified (unit) — tests/unit/test_phase4_alignment.py
- Reconciliation engine — working-verified (unit) — tests/unit/test_phase4_reconciliation.py, tests/scenarios/test_hard_zero_guards.py
- Ledger, review, export — implemented-untested here — ledger_service.py, api/review.py, export_service.py; tests/unit/test_export_workbook.py
- Web app — vitest 72 passed, lint + typecheck OK (2026-10-30, D113, Node v24.19.0 from nvm, not on PATH) — web/

## Tests
- `backend/.venv/Scripts/pytest.exe backend/tests/{unit,parsers,property,scenarios} -q` — 1502 passed, 22 skipped, 361 s, 2026-10-30 (D113)
- `pytest backend/tests/integration` — run on disposable postgres:16.15 at 127.0.0.1:15499: test_phase4_task_411_413.py (3 passed), test_ai_vision.py (15 passed), test_pd2f_waiting_for_reading.py (11 passed), test_pd2c_pd2d_reading.py (9 passed), test_pd3a_no_ai_calls.py (3 passed), test_ingestion.py (4 passed), test_pd_ledger_scopes.py (3 passed); 3 failures on HEAD (KNOWN_ISSUES: test_ai_h1, test_page_split); test_infrastructure_ready needs MinIO/S3 (D113)
- Web: `npx tsc -b`, `npm run lint`, `npx vitest run` (72 passed, D113); Playwright e2e not run
- `make accuracy-bench` — not run: needs Docker worker image and owner's local truth set

## Active task
- Step 7 Phase 2 complete (D113): Tasks A–D implemented (KI-005 empty OCR as ai_only; Field Origin sheet in export; waiting/failed chip in documents list; handwritten date/amount "Use as..." suggestions); PD-5b regression suite green.
- Next is Step 7 Phase 3: deploy with the owner's go + PD-5a click-through on beta; owner decides live switch from D112.

## Next steps (max 5, highest value first)
1. Step 7 Phase 3: deploy to beta with owner's go and run PD-5a click-through.
2. Owner decides live switch from D112 results (test invoices kept on beta, owner's choice; no restore).
3. Owner pushes 493bc9e and tag live-20261030-b6 (plus the docs once committed).
4. Decide on KI-001 (internal server address in tracked files).
5. Address pre-existing test failures (KI-003: test_ai_h1, test_page_split).
