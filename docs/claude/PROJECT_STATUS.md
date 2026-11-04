# Project status
Last verified: 2026-11-04, live as live-20261104-b18 (re-read with rules, GRN link confirm, override, field errors; engine 0.1.17, migration 0037, D126) (pushed to origin/main; branch renamed from phase2-hosted-ci); switch on rules (version 48); 152 ai_request; KI-006 root cause found, fix deferred; step-10 record still owed

## Components
Status: working-verified | implemented-untested | partial | stub | broken | not-found | unknown
Unit-level "working-verified" = offline suite passed this session; not proven on real invoices.
- Ingestion (upload, sniff, ZIP, dedupe by hash) — working-verified on beta (PD-5a S1–S4, D114) — reconbench_app/ingestion.py, tests/integration/test_ingestion.py
- Digital vs scanned PDF routing — working-verified (unit + beta S1/S2, D114) — local_hybrid.py:is_scanned; tests/parsers/test_phase4_pdf.py, test_scan_images.py
- Rules extraction (PDF/XML/CSV/Tally) — working-verified (unit + beta, D114); accuracy target not met — tests/parsers/*; docs/accuracy-plan.md §0
- AI vision reading (Gemini / vLLM) — working-verified on beta (PD-5a D114, optional AI block: 1 request to vLLM, rules + AI versions, switch returned to rules; KI-005 not exercised) — reconbench_app/ai/worker/, ai/consensus.py, reconbench_core/extraction/merge.py; tests/unit/test_pd2e_merge.py, tests/integration/test_ai_vision.py
- Waiting for reading (PD-2f, D109; live D110, D114, D116, D117) — working-verified (beta S5 + optional AI block, D114); KI-009 fixed on beta (doc-4 stayed waiting during retry); KI-008 fixed in code and deployed; doc-4 failed at output cap (KI-006 confirmed) — reconbench_app/reading_state.py, migration 0036; tests/integration/test_pd2f_waiting_for_reading.py
- Scanned-invoice switch + 0 LLM calls outside AI reading (PD-2c/PD-2d, D107, D114) — working-verified (beta S1–S11 0 calls on rules, optional AI block 1 call, D114) — ai/vision.py, ai/settings_service.py, migration 0035; tests/integration/test_pd2c_pd2d_reading.py
- Validation (arithmetic, GSTIN, confidence) — working-verified (unit) — reconbench_core/extraction/verify.py; tests/property
- Vendor + link mapping — working-verified (unit + beta S8, D114: 0 auto links on unverified AI fields; KI-010/KI-011 fixed and live, D118) — tests/unit/test_phase4_mapping_links.py, test_phase4_vendor_mapping.py, test_q2_typed_links.py
- Line alignment — working-verified (unit) — tests/unit/test_phase4_alignment.py
- Reconciliation engine — working-verified (unit + beta S9, D114: 0 matched cases on unverified AI fields) — tests/unit/test_phase4_reconciliation.py, tests/scenarios/test_hard_zero_guards.py
- Ledger, review, export — working-verified on beta (S10, D114: 6 sheets incl. Field Origin with 80 rows) — ledger_service.py, api/review.py, export_service.py; tests/unit/test_export_workbook.py
- Web app — vitest 74 passed, lint + typecheck OK (2026-11-02, D118, Node v24.19.0 from nvm, not on PATH) — web/

## Tests
- `backend/.venv/Scripts/pytest.exe backend/tests/{unit,parsers,property,scenarios} -q` — 1605 passed, 22 skipped, 386 s, 2026-11-03 (b16; earlier b15: 1579: LINK_INFERRED, AI_LINES_MISSING, Verify re-match, zero-amount rows, split tax rate, PO/GRN AI reading; engine 0.1.15; web vitest 79 passed, tsc + lint clean)
- `pytest backend/tests/integration` — run on disposable postgres:16.15 at 127.0.0.1:15499: test_phase4_task_411_413.py (3 passed), test_ai_vision.py (15 passed), test_pd2f_waiting_for_reading.py (18 passed, b12), test_ai_runner.py (32 passed, b12), test_pd2c_pd2d_reading.py (9 passed), test_pd3a_no_ai_calls.py (3 passed), test_ingestion.py (4 passed, b13), test_phase4_task_411_413.py (3 passed, b13), test_pd_ledger_scopes.py (3 passed); 3 failures on HEAD (KNOWN_ISSUES: test_ai_h1, test_page_split); test_infrastructure_ready needs MinIO/S3 (D113)
- Web: `npx tsc -b`, `npm run lint`, `npx vitest run` (74 passed, D118); Playwright e2e not run
- Integration, 3 Nov 2026 (b15, throwaway postgres:16.15): test_flow_three_way_match (3, new: digital Matched, zero row Matched, AI invoice Matched after Verify), test_pd4d_verify_ai_values 10, test_ai_vision 16, test_pd2c_pd2d_reading 10, test_pd2f_waiting_for_reading 18, test_vendor_rematch 2, test_phase4_task_411_413 3, test_pd2b_reason_code_migration 3, test_ingestion 4, test_reupload_withdrawn 6, test_pd2b_review_marks_fields 5 — all passed; only KI-003 failures (test_ai_h1 1, test_page_split 2)
- `make accuracy-bench` — not run: needs Docker worker image and owner's local truth set

## Active task
- b18 (4 Nov, live, D126): "Read again with rules" for ready documents, "Confirm GRN link" on Mapping, "Values don't add up" override in the correction editor, field-named save errors. Owner next: §7 confirm GRN link; §8 override with a reason; §10 Document total 400; re-run Reconciliation.
- b17 (4 Nov, live, D124/D125): lump-sum amount rule (PD-3c), unread-header-totals review, line-tax totals + derived grand total, spread freight, serial check (SERIAL_DIFF), suggested fallback link, failed-QA saves not verified (+override_reason, no web control yet), export Not included / PO Balance / own GSTIN / payable incl. GST, OCR orientation warning, health page. Tests 1672 offline, 89 integration, web 83. Owner next: confirm §10 links, re-read §7, fix §8 taxable, re-run Reconciliation.
- Step 9 complete (D117): KI-008 and KI-009 fixed in code and deployed (tag live-20261031-b10); doc-4 retried twice (2 vLLM requests against a budget of 1, 0 Gemini), each 3774 in / 3386 out; KI-009 fixed on beta (doc-4 stayed waiting, then failed at the request's own finish); both requests hit the output cap on page 1 (detail=at_output_cap, KI-006 confirmed); KI-008 partly seen (Retry got its own cache_key, no cache hit), end to end not yet seen.
- Step 10 (31 Oct, after D117; record still owed, next free D-number): b11 (37cf3b4) sizes image tokens from real pixels, so doc-4's cap is 4096 (verified on beta). Doc-4 still filled 4096 (2 requests, one from the owner's re-upload). b12 (930ac02) logs `vision_answer_shape` counts per AI call. Its retry showed about 52 header fields on page 1 plus pretty-printed JSON (51% whitespace), not a loop and not rotation (KNOWN_ISSUES KI-006). Requests 129 → 132, 0 Gemini.

- b16 (3 Nov, live, D123; engine 0.1.16): Accept item match (person's PO-line pin + invoice tax_rate/po_line_ref edits); alignment ignores serial numbers as specs, tolerates a word run into a model code, and aligns contained titles when qty and rate agree exactly; split CGST/SGST rate doubled when the tax amounts prove it; per-line freight accepted in line math; computed tax skipped when no header tax was read; OCR '@' next to a digit read as 0 (OCR pages only). Re-scoring live run 01a1a129 pairs: case 7 0.69→0.80, case 9 weak lines 0.60→0.80–0.95. Open: lump-sum line (PD-3c, Sir), unread TOTAL row (KI-015), missing POs, garbled OCR lines.
- Live test of 10 real invoices (1-2 Nov, records in work/live-test-20261101/): extraction (report 06) and Mapping (report 07) analysed; KI-010/KI-011 fixed and live (D118); Reconciliation analysis next.

## Next steps (max 5, highest value first)
1. Owner's UI check of inv10 on Mapping (D118); then the Reconciliation analysis of the live test; write the step-10 record (b11, b12, root cause) and fix KI-006 when the owner picks it up.
2. Exercise KI-008 end-to-end on beta once doc-4 reading succeeds.
3. Larger PD-5c run (n=10 is too small for live switch decision).
4. Owner decides live switch from D112 / D114 / PD-5c results.
5. Address pre-existing test failures (KI-003: test_ai_h1, test_page_split).
