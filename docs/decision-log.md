# ReconBench decision log

This register copies every Phase 1 item G-01 through G-27 from `ReconBench_Implementation_Plan.md` §2. The plan's user-confirmed decisions are authoritative. `docs/ReconBench_*.md` are read-only source copies.

## Current status

| ID | Status | Applied decision / current state |
|---|---|---|
| G-01 | Applied | Follow the TRD stack: PostgreSQL only; Procrastinate behind a port; pdfplumber + pypdfium2; no PyMuPDF. |
| G-02 | Applied | Use `GET /api/v1/reason-codes` when the endpoint is added. |
| G-03 | Resolved by user | P0 GST slabs are 5/12/18/28 with null effective-date boundaries; no 40% seed. |
| G-04 | Resolved by user | A billed quantity above the PO cap also receives `QTY_OVER_GRN` as specified in Appendix C1 / TR-REC-6. |
| G-05 | Open | TRD field keys are the default; the missing All Fields Reference still needs reconciliation. |
| G-06 | Open | Draft reason-code copy waits for Product/Finance review. |
| G-07 | Open / gated | Use synthetic fixtures only. Real parser profiles, samples, and the extraction gate remain untouched. |
| G-08 | Partially resolved | User defaults recorded in the source note; buyer GSTINs, GRN reference practice, and IRN prevalence remain open. |
| G-09 | Open | Compose is the local development shape; provider and deployment approvals remain open. |
| G-10 | Open | Retention and DPDP review are not inputs to Phase 1. |
| G-11 | Open | Size initially for the TRD estimate; Finance/AP confirmation remains open. |
| G-12 | Open | Use the plan's four-step tracker completion proposal in Phase 4. |
| G-13 | Open | Proposed assign-vendor and alias endpoints wait for Phase 4. |
| G-14 | Open | Use effective status for the Matched KPI proposal in Phase 4. |
| G-15 | Open | Use the plan's ledger completeness proposal in Phase 4. |
| G-16 | Open | Period selector scoping follows the plan proposal in Phase 4. |
| G-17 | Open | Apply the listed screen-flow defaults in the relevant Phase 4 step. |
| G-18 | Resolved for engine; API follows in 4.7 | `DUP_SUSPECTED`, `ITEM_UNSURE`, and `NO_TEXT_LAYER` are present as proposed Review-level reason codes. The API catalogue is next in 4.7. |
| G-19 | Resolved by user | Failed invoices consume balance except Duplicate/Rejected; store GST beside at-risk; show Duplicate/Unlinked in blocked value. |
| G-20 | Open | Reject manual cross-PAN vendor merges until Finance/Product decides. |
| G-21 | Default recorded | P0 bootstrap account only; user creation/reset flows remain outside this phase. |
| G-22 | Resolved (Task 2.1) | Schema §§3–8 DDL and privilege blocks executed in order on a blank, disposable PostgreSQL 16.15 database with no DDL errors. The final catalog contains 41 tables, 4 views, 37 functions, and the 4 declared ReconBench roles. No DDL correction was needed; scope notes and evidence are in `docs/schema-errata.md`. |
| G-23 | Provisional / follow-up required | SP-1 through SP-4 outcomes are recorded below. Queue commit/rollback, lock exclusion, stalled-job detection plus explicit retry, and SIGTERM drain passed. Automatic killed-worker retry was not proven; Procrastinate stays behind `JobQueue` pending the M2 load test and a recovery supervisor. |
| G-24 | In review | Direct dependencies and licenses are recorded in `docs/dependency-review.md`; MinIO has an added upstream-maintenance caveat in G-28. |
| G-25 | Resolved (Task 4.6) | `rules_hash` covers the versioned check registry and `body.engine` only; changes to `body.app` do not change it. |
| G-26 | Default recorded | Login required and one role in P0; Phase 3 owns authentication. |
| G-27 | Open | The proposed error dashboard is a Phase 4 product addition. |
| G-28 | Open / Phase 7 gate | The plan names MinIO for local Compose. Its upstream community repository was archived on 25 Apr 2026 and releases are no longer maintained. Keep the named dependency local to Phase 1; before deployment, decide whether to build from source or replace it with an actively maintained S3-compatible service. Owner: Tech lead / Product. |
| G-29 | Resolved by user (2026-10-01) | P0 login throttling uses independent normalized-email and source-IP rolling buckets, with 10 failures / 15 minutes and 30 failures / 15 minutes respectively; each lock/throttle expires after 15 minutes. A success clears only that account's consecutive-failure state. See the policy below. |
| G-30 | Resolved by user (2026-10-02); implementation present, validation pending | Scanned invoices are a required input, so OCR is promoted from P1/v1.1 to a pre-pilot Phase 4 deliverable under PDF extraction (Task 4.9b). Use the TRD's OCRmyPDF + Tesseract starting point, lower OCR confidence, preserve evidence, and require the same extraction/arithmetic gates. Unreadable or low-confidence critical fields remain Needs Review. |

G-03, G-04, G-08, and G-19 reflect the user-confirmed build-decision note in the Implementation Plan dated 1 Oct 2026. G-07 remains explicitly open; no real sample files were inspected.

### M1 questions closed to documented defaults (Task 4.6)

These close the decision questions, not future application work. The user's samples remain unopened.

| TRD question | Applied decision | Implementation timing |
|---|---|---|
| OQ-1 | Exception and Needs Review invoices consume PO balance; Duplicate and Rejected do not. | Engine in 4.5 |
| OQ-2 | Follow Appendix C1 / TR-REC-6: a PO-cap shortfall is also `QTY_OVER_GRN`. | User resolved; engine in 4.5 |
| OQ-3 | A newly ingested GRN automatically re-evaluates Pending cases only. | Decision closed; worker orchestration in 4.11 |
| OQ-4 | Show GST on at-risk value beside the taxable at-risk amount, not inside it. | Engine in 4.5 |
| OQ-5 | Suspected duplicates use the Plan's date/number/value criteria, get `DUP_SUSPECTED` at Review, and continue consuming balance until a human decision. Exact duplicates are blocked. | Engine in 4.5 |
| OQ-8 | `DUP_SUSPECTED`, `ITEM_UNSURE`, and `NO_TEXT_LAYER` are Review-level proposed codes. User-facing copy still waits for G-06 review. | Core catalogue in 4.1/4.5; API in 4.7 |

### G-29 — P0 login throttling (user-authorized, 1 Oct 2026)

- `app.security.login_email_max_failures=10`, `login_email_window_minutes=15`, and `login_email_lockout_minutes=15`.
- `app.security.login_ip_max_failures=30`, `login_ip_window_minutes=15`, and `login_ip_throttle_minutes=15`.
- The email/account bucket is keyed by normalized email across source IPs. The IP bucket is keyed by source IP across submitted emails; the buckets are independent, and either one can block login. They are never combined into an IP-plus-email key.
- A successful login clears that email/account's consecutive-failure state. It does not clear the IP bucket; IP failures age out through their rolling window. Account lockouts and IP throttles expire automatically after 15 minutes; there is no permanent lockout or manual-unlock path.
- Unknown and known accounts receive the same generic 401 response, and either throttle bucket receives the same generic 429 response with a constant coarse `Retry-After: 900`. Responses do not reveal registration state or the bucket that fired.
- Use the ASGI socket peer address. Ignore `X-Forwarded-For` unless a future deployment explicitly configures a trusted proxy boundary.
- The values are in `backend/reconbench_app/db/seed_data.py` and validated by `AuthSecurityConfig`. Older immutable config versions that predate these keys use the same approved defaults at runtime; no schema change is required.

### G-30 — Scanned invoice OCR scope (user-confirmed, 2 Oct 2026)

- The source PRD and Implementation Plan placed scanned-PDF OCR in P1 / v1.1. Because the user's intended invoice input is scanned PDFs, OCR is promoted to a required deliverable before the full-month reconciliation demo/pilot.
- Implement it as an extension to **Phase 4, Task 4.9 (PDF extraction), tracked as Task 4.9b**. It belongs in document ingestion before mapping/reconciliation; Phase 5 is UI polish, and optional AI assist in 4.17 is not OCR.
- Follow the TRD starting design: OCRmyPDF with Tesseract, then send the resulting text through the existing PDF extraction, normalization, and arithmetic-QA pipeline. Keep the original scan and source-page evidence; record OCR extraction provenance and the lower OCR confidence.
- Never promote a scan to Matched solely because OCR returned text. Missing/low-confidence critical fields or failed arithmetic QA must remain Needs Verification / Needs Review. OCR runs inside the existing resource-limited PDF child-process boundary.
- The local code path now runs OCRmyPDF + Tesseract in a separately bounded child process, stores per-page OCR provenance, and records `pdf_ocr` on document versions that used OCR. Runtime/system-package verification and real accuracy validation remain pending. G-07's representative scanned samples and hand-labelled truth set remain necessary to validate languages/layouts, tune confidence, and report measured extraction accuracy.

## Phase 1 spike outcomes (2026-10-01)

All spike inputs were synthetic. SP-1 ran in the local Docker PostgreSQL service against a uniquely named disposable database, which the spike dropped afterward; it created no ReconBench schema or migration.

| Spike | Result | Decision / limitation |
|---|---|---|
| SP-1 Procrastinate lifecycle | Passed atomic data-row/job commit and rollback, PostgreSQL advisory-lock exclusion, stalled-job detection followed by explicit retry and successful completion, and SIGTERM drain. | Keep Procrastinate behind `JobQueue` provisionally. A hard-killed worker's job was detected and manually retried by `JobManager`; this did not demonstrate automatic lease re-queue. The M2 load and recovery-policy test remains open under G-23. |
| SP-2 assignment determinism | Passed across 36 shuffled input permutations after sorting and quantizing scores to four decimals. | Keep SciPy's assignment approach; retain deterministic input ordering. |
| SP-3 PDF baseline | Synthetic text-layer fixture: six of six critical fields extracted. | This is only a synthetic parser smoke check. Real vendor PDFs, scanned documents, and the ≥97% extraction gate remain blocked by G-07. |
| SP-4 XML/CSV profiles | Synthetic Tally-style XML and CSV header fixtures detected; CSV values remain strings. | No claim about actual Tally/CSV variants; G-07 remains open. |

Local command for SP-1: `docker compose exec api /app/backend/.venv/bin/python -m scripts.spikes.sp1_procrastinate` after copying the synthetic `scripts/` directory into the disposable API container. SP-2 through SP-4 are runnable with the `make spikes` target when a disposable PostgreSQL DSN is available for SP-1.

The local object-store image was successfully built from MinIO Community `RELEASE.2025-10-15T17-29-55Z` and used only in local Compose. The upstream repository is archived; a supported object-store and licence choice is still required before staging or production (G-28). See [MinIO upstream](https://github.com/minio/minio) and the [pinned release](https://github.com/minio/minio/releases/tag/RELEASE.2025-10-15T17-29-55Z).

---
## 2. Gaps, conflicts and open decisions

Each row is specific and actionable: what the documents say, what this plan does meanwhile, who decides, and which phase it blocks. **Phase 1 copies every row into `docs/decision-log.md`.** IDs are stable references, not a sequence: G-27 sits in section C because it was added last.

 > **User-confirmed build decisions (1 Oct 2026):** Use the PRD tolerance defaults, including `limit_mode=or`; keep `both` selectable. Keep `cum_value_vs_po=0` (strict, as proposed in this Plan). Support both payable modes (`hold` and `pay_at_po_rate`), with `pay_at_po_rate` as the initial default. Seed GST slabs from the PRD (`5/12/18/28`) with no effective-date boundaries in the initial config. For quantity shortfalls, use G-04 / Appendix C1 / TR-REC-6. Use G-19's proposed balance, at-risk GST and blocked-value rules. Actual buyer GSTIN(s), whether GRNs carry invoice/challan references, IRN prevalence, and real-sample parser behavior remain open. Do not inspect or infer facts from the sample files until the user asks to use them.

### A. Conflicts between documents (resolved choices are recorded below)

| ID | Conflict | Plan default | Decides | Blocks |
|---|---|---|---|---|
| G-01 | PRD Appendix D suggests SQLite for a local MVP, RQ/Celery/Arq queues and PyMuPDF. TRD D3/D4/D9 choose PostgreSQL-only, Procrastinate, pdfplumber + pypdfium2. | Follow the TRD. No SQLite anywhere, including tests. PyMuPDF excluded unless a licence review approves it. | Tech lead | Phase 1 |
| G-02 | Reason-code endpoint is `/reasoncodes` in TRD §7.2 but `/reason-codes` in TR-API-4 (App Flow gap 8). | `GET /api/v1/reason-codes` (matches the kebab-case of `/vendor-merge-suggestions`, `/manual-recon`). Decide **before** the first OpenAPI client is generated. | Tech lead | Phase 1 |
| G-03 | GST slabs conflict: PRD §7.5 seeds 5/12/18/28; Schema §10.2 and TRD departure 9 add date boundaries and a 40% row. | **Resolved for P0 by user (1 Oct 2026):** use the PRD seed 5/12/18/28 with null date boundaries. Keep effective-date support configurable, but do not preload the TRD's extra 40% transition. | User (1 Oct 2026) | None for engine build |
| G-04 | PRD §7.5 S4 and Appendix C1 differ on classifying a shortfall beyond the PO cap. | **Resolved for P0 by user (1 Oct 2026):** follow Appendix C1 / TR-REC-6; when billed exceeds the PO cap, classify the shortfall as `QTY_OVER_GRN` as well as `QTY_OVER_PO`. | User (1 Oct 2026) | None for engine build |
| G-05 | Field names: PRD uses `po_number`, `vendor`, `unit_price`. Schema says keys follow the TRD; the *All Fields Reference* sheet was never uploaded (Schema §13 #26). | Use TRD keys. Keep one mapping table in `reconbench_core.schema`; reconcile when the sheet arrives. | Product | 4.1 |
| G-06 | The UI/UX Brief ends at §14; its Appendix A copy deck was not available. Reason-code titles, messages and actions in Schema §10.3 are drafts. | Seed the drafts. Reason text comes only from the API catalogue, never hard-coded in components. Product/Finance review before copy freeze. | Product + Finance | Phase 5 |

### B. Inputs that gate work (no document can replace these)

| ID | Missing input | Plan default meanwhile | Decides | Blocks |
|---|---|---|---|---|
| G-07 | 2–3 sample Tally-style XMLs and CSVs; ~20 invoice PDFs from ≥ 5 vendors (include scanned/ugly ones); a few GRN PDFs (PRD Q1–Q2). | Build synthetic fixtures from PRD Appendix C (task 1.10) so engine work is not blocked. Real parser profiles and the ≥ 97 % extraction gate stay **blocked** until samples arrive and are anonymised. | Finance / AP | 4.3, 4.9, 6.1 |
| G-08 | Buyer GSTIN(s) and count (Q5), whether GRNs carry invoice/challan references (Q3), and whether invoices carry IRN (Q7) still need real business/sample confirmation. | **Resolved defaults:** PRD tolerances with `limit_mode=or` (keep `both` selectable); both payable modes, initially `pay_at_po_rate`; `cum_value_vs_po=0` strict. Keep `buyer_entities` empty until actual GSTIN(s) are supplied. | User for defaults; Finance/AP for remaining facts | Engine build unblocked; buyer config and sample-specific validation pending |
| G-09 | Deployment target (PRD Q9: cloud vs on-prem, may documents go to an AI provider) and first AI provider (Q10). | Shape A (Docker Compose, single node) per TRD §10.1. Build the provider port plus a stub adapter first; add the real adapter once Q10 is answered. AI stays off. | Product + compliance | 4.17, Phase 7 |
| G-10 | Legal retention periods and DPDP applicability (OQ-9, TRD §9.3). | `app.retention` values stay `null`. Retention job is P1. | Finance + legal | Phase 7 |
| G-11 | Monthly volumes and max PDF pages (PRD Q6). | Size for ~10,000 documents/month at ~10 lines each (TRD §6.5). | Finance / AP | Phase 6 |

### C. Behaviour the documents leave undefined (Product/Finance rule needed; plan proposes a default)

| ID | Undefined behaviour | Plan default | Blocks |
|---|---|---|---|
| G-12 | What "complete" means for each step of the 4-step tracker (App Flow gap 4). | Ingest: ≥ 1 parsed document and no file Queued/Parsing. Map: zero Unassigned documents and zero *suggested* links. Reconcile: a non-stale run exists for the period. Export: an export exists from that run. | 4.18 |
| G-13 | **No endpoint exists to move a document out of *Unassigned*** (TRD §7.2 lists only merge and merge-suggestion decisions), and none for vendor aliases, although Settings shows an alias table (Schema §13 #25). | Add `POST /documents/{id}/assign-vendor` (sets `vendor_locked`) and `GET/POST/DELETE /vendor-aliases`. Both are *proposed* additions; log them. | 4.11 |
| G-14 | Overlay semantics in KPIs (Schema §13 #16: "needs a decision"). | Read literally: Matched % counts `matched` + `matched_flags` by **effective** status, so an approved case leaves it. | 4.13 |
| G-15 | How "ledger completeness" is detected (Schema §13 #12). | Set `completeness_warning` when the PO date is earlier than the earliest GRN/invoice in the pool and no opening balance exists for the line. | 4.5 |
| G-16 | Does the period selector scope only Home, or Mapping and Reconciliation too (UI/UX B1)? | Home KPIs and the default date filter on Reconciliation. Mapping shows the whole pool. | 4.18 |
| G-17 | Smaller App Flow gaps: export on a stale run (gap 7), link change inside a case (gap 5), CSV mapping dialog location (gap 3), batch name on Ingestion (gap 2), KPI click-through (gap 6). | Export warns and offers Re-run first. A link change waits for a scoped Re-run; Manual Recon is immediate. CSV mapping opens from the file row. Batch name is optional. KPI click-through is optional in P0. | 4.10–4.16 |
| G-18 | Three proposed reason codes: `DUP_SUSPECTED`, `ITEM_UNSURE`, `NO_TEXT_LAYER` (OQ-5, OQ-8). | Add all three at level *Review* (already in the 28-row catalogue with `is_proposed = true`). | 4.5 |
| G-19 | Do failed invoices consume PO balance (OQ-1)? Is GST on at-risk shown (OQ-4)? Do Duplicate/Unlinked values count as at-risk (OQ-6)? | **Accepted P0 defaults (user decision 1 Oct 2026):** failed invoices consume balance except Duplicate and Rejected; GST is stored beside `at_risk`; Duplicate/Unlinked value is separate in `blocked_value`. | No additional decision gate |
| G-20 | Manual vendor merge across **different PANs** (UI/UX B3). The schema does not block it; the PRD only says they never *auto*-merge. | Service layer rejects cross-PAN merges until decided. | 4.11 |
| G-21 | No password-change, password-reset or user-creation flow for P0 (Manage users is P1). | Bootstrap user from the seed script only. | Phase 3 |
| G-27 | **Error dashboard** (TR-OPS-3, P0: failed files by reason code and day, top failing layouts and vendors, unresolved warnings, retry actions). The TRD gives `GET /admin/errors`, but neither the App Flow sitemap nor the UI/UX Brief defines a screen for it. | Build the endpoint in 4.8 and a minimal read-only admin table in 4.18, flagged as a plan addition until Product decides. | 4.18 |

### D. Technical uncertainties (each has an early test in Phase 1 or 2)

| ID | Uncertainty | Early test |
|---|---|---|
| G-22 | **Resolved:** the schema DDL was validated on PostgreSQL 16.15 for Task 2.1. | `docs/schema-errata.md`; no DDL defect or product-default change. |
| G-23 | Procrastinate is provisional until the M2 load test; Hungarian tie-break determinism; pdfplumber accuracy; Tally XML variants. | Spikes SP-1 to SP-4 (task 1.9) |
| G-24 | Library versions and licences in TRD §4 "come from the author's knowledge". | Dependency review (task 1.8) |
| G-25 | `rules_hash` covers `engine` only, not `app` settings (Schema §13 #14); TRD says "config". | Confirm in decision log; hash test in 4.6 |
| G-26 | PRD Q8 (is login required in v1, how many roles) is still open. | Default: login required, one role, matrix in P1 (OQ-7) |

### E. Post-plan infrastructure input

| ID | Issue | Phase 1 default | Owner | Blocks |
|---|---|---|---|---|
| G-28 | The selected MinIO Community image is no longer available from the Docker Hub repository, and the upstream Community repository is archived. | The pinned 2025-10-15 source image builds and runs for local-only development. Treat it as an unmaintained local fixture; approve a maintained, supported object-store target and its licence before staging or production. | Product + Tech lead | Before staging |

---

## 3. Remaining Backend Schema §13 decisions

These inputs and proposed rules remain open after DDL validation and migration work. They do not change the user's confirmed P0 defaults or block the structural migration chain.

| Schema §13 item | Unresolved input or decision | Tracking |
|---:|---|---|
| 1 | Opening-balance upload/API flow is not defined; the P1 table remains available. | Product / Finance, Phase 6 |
| 2 | Whether identical bytes re-uploaded after removal reactivate the same `source_file` row is still a proposed behavior. | Product, Phase 4.3 |
| 3 | CSV mapping and retry behavior is a plan default for the App Flow gap, pending product acceptance. | G-17 |
| 6 | Manual vendor merges across different PANs remain blocked pending a decision. | G-20 |
| 12 | Ledger completeness detection remains a proposed rule. | G-15 |
| 14 | **Resolved in Task 4.6:** `rules_hash` includes the check registry and `body.engine`; `body.app` is excluded. | G-25 |
| 15 | Release-action semantics need the missing UI/UX B2 decision; stale-override validation is P1. | Product / Finance, Phase 5 |
| 16 | KPI/list treatment of approved, held, and rejected cases needs confirmation; the effective-status interpretation is still a proposal. | G-14 |
| 17 | The view computes staleness directly; caching it is conditional on M2 performance evidence. | G-23 |
| 20 | The schema assumes USD for AI budget caps; the budget unit and auto-accept policy need confirmation with the AI-provider decision. | G-09, Product / Compliance |
| 22 | Retention periods, DPDP applicability, and retention scope for evidence snippets await legal review. | G-10 |
| 23 | Procrastinate remains provisional until M2 automatic-recovery/load behavior is proven; its tables stay a separate pinned schema step. | G-23 |
| 24 | Reason-code titles, messages, and suggested actions await the missing copy deck and Finance review. | G-06 |
| 25 | The `/reasoncodes` route conflict and missing alias/user endpoints remain API design gaps. | G-02, G-13 |
| 26 | Reconciliation against the missing All Fields Reference remains pending. | G-05 |
