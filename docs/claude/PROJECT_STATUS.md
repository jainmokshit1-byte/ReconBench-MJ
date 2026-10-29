# Project status
Last verified: 2026-10-29, commit 41b5546 live as live-20261029-b5 (D108); CODEBASE_CONTEXT.md untracked

## Components
Status: working-verified | implemented-untested | partial | stub | broken | not-found | unknown
Unit-level "working-verified" = offline suite passed this session; not proven on real invoices.
- Ingestion (upload, sniff, ZIP, dedupe by hash) — implemented-untested here (DB integration tests skipped) — reconbench_app/ingestion.py, tests/integration/test_ingestion.py
- Digital vs scanned PDF routing — working-verified (unit) — local_hybrid.py:is_scanned; tests/parsers/test_phase4_pdf.py, test_scan_images.py
- Rules extraction (PDF/XML/CSV/Tally) — working-verified (unit); accuracy target not met — tests/parsers/*; docs/accuracy-plan.md §0
- AI vision reading (Gemini / vLLM) — implemented, suggestions only; live calls not run — reconbench_app/ai/worker/; tests/unit/test_ai_vision.py
- Scanned-invoice switch + 0 LLM calls outside AI reading (PD-2c/PD-2d, D107) — working-verified (unit + integration on disposable DB); live 29 Oct (D108) — ai/vision.py, ai/settings_service.py, migration 0035; tests/integration/test_pd2c_pd2d_reading.py
- Validation (arithmetic, GSTIN, confidence) — working-verified (unit) — reconbench_core/extraction/verify.py; tests/property
- Vendor + link mapping — working-verified (unit) — tests/unit/test_phase4_mapping_links.py, test_phase4_vendor_mapping.py
- Line alignment — working-verified (unit) — tests/unit/test_phase4_alignment.py
- Reconciliation engine — working-verified (unit) — tests/unit/test_phase4_reconciliation.py, tests/scenarios/test_hard_zero_guards.py
- Ledger, review, export — implemented-untested here — ledger_service.py, api/review.py, export_service.py; tests/unit/test_export_workbook.py
- Web app — vitest 64 passed, lint + typecheck OK (2026-10-29, Node v24.19.0 from nvm, not on PATH) — web/

## Tests
- `backend/.venv/Scripts/pytest.exe backend/tests/{unit,parsers,property,scenarios} -q` — 1460 passed, 0 failed, 22 skipped (all need RB_PHASE2_TEST_DATABASE_URL), 296 s, 2026-10-29 (with D107 changes)
- `pytest backend/tests/integration` — affected groups run on disposable postgres:16.15 at 127.0.0.1:15499 (D107); 3 failures also fail on HEAD (KNOWN_ISSUES); test_infrastructure_ready needs MinIO/S3; test_phase4_task_411_413 needs a database with no other sessions
- Web: `npx tsc -b`, `npm run lint`, `npx vitest run` passed with PATH=/c/Users/jainm/AppData/Local/nvm/v24.19.0; Playwright e2e not run
- `make accuracy-bench` — not run: needs Docker worker image and owner's local truth set

## Active task
- PD-2c + PD-2d (step 5): live 29 Oct 2026 (D107, D108, commit 41b5546, tag live-20261029-b5); owner check pending

## Next steps (max 5, highest value first)
1. Owner check of step 5 on live (Settings "Scanned invoices" panel, "Read by rules" chip); decide when to set the switch to AI.
2. Step 6: PD-2e + PD-2f + PD-2h (settle `NOT_NEEDED` skip in ai_assisted; provider name on method chip).
3. Real-sample end-to-end test (extraction → mapping → reconciliation) — owner's stated next step before the Phase 4 exit gate.
4. Decide on KI-001 (internal server address in tracked files).
5. Check the 20-page sandbox cap and scan misdetection suspicions (KNOWN_ISSUES) with synthetic PDFs — scan misdetection now decides which invoices AI may read.
