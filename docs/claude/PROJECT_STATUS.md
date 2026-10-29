# Project status
Last verified: 2026-10-29, commit a90ee42 (uncommitted changes present: .claude/, CODEBASE_CONTEXT.md, CLAUDE.md, docs/claude/)

## Components
Status: working-verified | implemented-untested | partial | stub | broken | not-found | unknown
Unit-level "working-verified" = offline suite passed this session; not proven on real invoices.
- Ingestion (upload, sniff, ZIP, dedupe by hash) — implemented-untested here (DB integration tests skipped) — reconbench_app/ingestion.py, tests/integration/test_ingestion.py
- Digital vs scanned PDF routing — working-verified (unit) — local_hybrid.py:is_scanned; tests/parsers/test_phase4_pdf.py, test_scan_images.py
- Rules extraction (PDF/XML/CSV/Tally) — working-verified (unit); accuracy target not met — tests/parsers/*; docs/accuracy-plan.md §0
- AI vision reading (Gemini / vLLM) — implemented, suggestions only; live calls not run — reconbench_app/ai/worker/; tests/unit/test_ai_vision.py
- Validation (arithmetic, GSTIN, confidence) — working-verified (unit) — reconbench_core/extraction/verify.py; tests/property
- Vendor + link mapping — working-verified (unit) — tests/unit/test_phase4_mapping_links.py, test_phase4_vendor_mapping.py
- Line alignment — working-verified (unit) — tests/unit/test_phase4_alignment.py
- Reconciliation engine — working-verified (unit) — tests/unit/test_phase4_reconciliation.py, tests/scenarios/test_hard_zero_guards.py
- Ledger, review, export — implemented-untested here — ledger_service.py, api/review.py, export_service.py; tests/unit/test_export_workbook.py
- Web app — unknown (not run: Node not on PATH) — web/

## Tests
- `backend/.venv/Scripts/pytest.exe backend/tests/{unit,parsers,property,scenarios} -q` — 1427 passed, 0 failed, 22 skipped (all need RB_PHASE2_TEST_DATABASE_URL), 327 s, 2026-10-29
- `pytest backend/tests/integration` — not run: needs disposable PostgreSQL + MinIO
- `make check-static`, web vitest, Playwright e2e — not run: Node not on PATH
- `make accuracy-bench` — not run: needs Docker worker image and owner's local truth set

## Active task
- none (memory setup done 2026-10-29)

## Next steps (max 5, highest value first)
1. Real-sample end-to-end test (extraction → mapping → reconciliation) — owner's stated next step before the Phase 4 exit gate.
2. Decide on KI-001 (internal server address in tracked files).
3. Check the 20-page sandbox cap and scan misdetection suspicions (KNOWN_ISSUES) with synthetic PDFs.
4. Run `make check-static` and web tests once Node is available.
