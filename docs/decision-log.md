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
| G-09 | Partially resolved / approval open | Gemini is the first provider (D1). External calls remain disabled by default; synthetic-only calls are limited to approved fixture digests. Real-document/provider sharing is not approved (D2); deployment target and compliance approval remain open. |
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
| G-09 | Deployment target (PRD Q9: cloud vs on-prem, may documents go to an AI provider) and first AI provider (Q10). | **Split:** Gemini is the first provider by user choice (D1), but real-document/provider sharing is not approved (D2). AI remains off by default; only the documented synthetic fixture gate may enable external calls. Deployment target/compliance approval remains open. | Product + compliance | 4.17, Phase 7 |
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

## AI Assist decisions for Tasks 4.17–4.18 (user-authorized plan, 4 Oct 2026)

These decisions implement the user-provided AI Assist plan. The PRD, TRD, App Flow, UI/UX Brief, and Backend Schema remain read-only source copies. Where the plan intentionally narrows or departs from them, the deviation is recorded here. No provider call or real business document is approved by this decision record.

| ID | Decision / status | Applied rule |
|---|---|---|
| D1 | Resolved by user-provided plan | Gemini is the first provider adapter. Keep the provider port neutral; do not add Claude or OpenAI adapters. |
| D2 | Resolved by user-provided plan; compliance remains gated | Provider choice does not approve sending real invoices, POs, or GRNs. Real-document calls are blocked pending separate approval. |
| D3 | Resolved by user-provided plan | `RB_AI_EXTERNAL_CALLS` is `disabled` by default, or `synthetic_only`, or `enabled`; `enabled` requires an approval reference. Synthetic-only mode requires a compiled allowlist of fixture digests. |
| D4 | Resolved by user-provided plan | API seals keys with X25519 + HKDF-SHA256 + AES-256-GCM using the public key; only the AI worker holds private keys and can open them. This intentionally departs from TRD-AI-6 symmetric master-key wording. |
| D5 | Resolved by user-provided plan; Phase 1 protocol gate | Use the pinned `google-genai` Interactions API through stable `v1` if Phase 1 proves it; otherwise document the beta requirement. Set `store=false`; send no tools, stream, or prior interaction state. |
| D6 | Resolved by user-provided plan | A session-only key is bound to the session that saved it, with the binding enforced in PostgreSQL. Saved encrypted keys survive logout. |
| D7 | Resolved by user-provided plan | Status must describe verified state and never say “Connected”; the best success label is “Last provider test passed at T”. This intentionally departs from the PRD/UIUX pill wording. |
| D8 | Resolved by user-provided plan | Persist every call in a request ledger. An uncertain outcome is never automatically retried; a person must acknowledge it. Do not claim exactly-once provider execution. |
| D9 | Resolved by user-provided plan | Fail closed at budget caps. Offer/enforce a USD cap only when the chosen model has a recorded price and the token bound is proven; do not invent prices or claim a guaranteed cap otherwise. |
| D10 | Resolved by user-provided plan | H2 runs after the deterministic run and writes advisory opinions to an append-only `ai_line_opinion` table. Runs never wait for H2. |
| D11 | Resolved by user-provided plan | Keep AI auto-accept disabled. This preserves SCN-20's rule that AI on/off cannot change verdicts and narrows the PRD/TRD opt-in auto-accept wording. |
| D12 | Resolved by user-provided plan | `ai_first` is explicit opt-in for H1 suggestions on every PDF; deterministic rules remain authoritative. This records the plan's narrower interpretation of PRD AI-first extraction. |
| D13 | Proposed; awaiting Product | Proposed PO/GRN H1 eligibility defaults are not a user resolution. Keep them marked proposed and verdict-neutral; do not implement them until the relevant Phase 4 decision gate. |
| D14 | Resolved by user-provided plan; behavior change to validate | Run deterministic normalization and TR-PAR-5 arithmetic QA on every correction save, including manual saves. |

Related Schema §13 #20 remains open for price-source and guaranteed-token-bound inputs: no prices are selected or inferred. Schema §13 #15 remains subject to the recorded missing UI/UX decision; D11 sets the AI auto-accept default to disabled for this implementation.

### Planned Schema additions (implemented in revision `0017`; see `docs/schema-errata.md`)

The later AI ledger migration is planned as `0017_ai_assist_ledger`; this is not authorization to modify the migration during Phase 0 or Phase 1. Proposed contents are `ai_request`, `ai_worker_heartbeat`, append-only `ai_line_opinion`, session-bound `secret.session_id` plus the supporting session composite key/FK and checks, partial unique `link.ai_call_id`, `ai_call_log.ai_request_id`, `tokens_thought` only if confirmed by the Phase 1 response contract, and revoking `reconbench_report` SELECT on `ai_cache`. The `ai_outcome` enum remains unchanged. Apply and validate those changes only in the plan's Phase 2 on disposable PostgreSQL 16.15.

## D15 — Gemini calls use a single-shot transport guard (user-authorized, 4 Oct 2026)

The public retry settings in `google-genai==2.28.0` cannot guarantee one Interactions POST and may sleep for the provider's uncapped `Retry-After`. Use a fresh SDK/HTTPX client for every provider call, with `HttpRetryOptions(attempts=1)`, `HTTPTransport(retries=0)`, and the public `HttpOptions.httpx_client` wrapped by a one-request guard. Convert 408, 409, 429, and 5xx responses to a permanent sanitized exception before they reach SDK retry handling. Classify `ConnectError` and `ConnectTimeout` as `not_sent`; classify other HTTPX transport failures as `may_have_been_sent`. Do not persist provider messages or claim exactly-once execution. Phase 5 ledger handling must release `not_sent` reservations and mark `may_have_been_sent` calls `uncertain`; it must not automatically retry uncertain calls. Phase 6 must require use of this client factory through an import-linter or runtime test. Focused tests pin the SDK to 2.28.0 so an SDK upgrade requires a fresh retry audit. This decision changes no schema or migration and does not approve sending real business documents to a provider.

**Amendment (review fixes, 4 Oct 2026).** (1) The SDK's async client path would bypass the guard and still retry once, so it fails closed: the factory supplies an async HTTPX client whose transport raises before any I/O, and provider calls use the guarded sync client (from a worker thread when the task is async). A native async guard is deferred unless the AI worker needs it. (2) `timeout_ms` is a required factory argument bounded to 1–120 seconds; production passes the configured call timeout (60 s by plan default). (3) Error mapping accepts both google.rpc-style bodies and the current string `error.code` values; unknown values are dropped and provider messages are never kept. Callers log only `safe_log_fields`. (4) The guard lives in the AI-worker-only package `reconbench_app.ai.worker`. The import boundary is enforced immediately by an import-linter contract, a source scan of all application modules, and a subprocess runtime check, rather than waiting for Phase 6. (5) Any non-HTTPX exception from the inner transport is classified `may_have_been_sent`.

## D16 — AI Assist Half 1 implementation decisions (Phases 2–7, 4 Oct 2026)

These record choices made while implementing Phases 2–7 where the plan was silent or a detail had to change. None approves real-document provider calls; `RB_AI_EXTERNAL_CALLS` still defaults to `disabled`.

| # | Decision | Reason |
|---|---|---|
| 1 | `ai_request.ai_call_log_id` is not added; `ai_call_log.ai_request_id` is the only link. | Avoids a second foreign-key cycle; the link is still one-to-one in practice. |
| 2 | `ai_request.call_kind` (`hook`, `provider_test`, `synthetic_smoke`) with `hook` required only for hooks. | Provider tests and smoke calls are budgeted and audited but are not `ai_hook` values. |
| 3 | `ai_setting.consent_fingerprint` plus a consent CHECK were added to `0017`; rows with prior consent are reset to `rules_only`. | Detecting "consent outdated" needs the fingerprint that was actually consented to. |
| 4 | A workspace without an `ai_setting` row reports version 1 (rules-only defaults); the first save writes version 2. | ETags and `If-Match` must be positive; every change still bumps the version. |
| 5 | The provider test needs a provider, model and usable key, and an open deployment gate, but not hook consent. | It sends only the compiled synthetic fixture, never business data. |
| 6 | A transport failure that provably never reached the provider (`not_sent`) moves `sending → released` and clears `sent_at`. | Truthful state; such attempts may be retried by a new request, unlike `failed` or unacknowledged `uncertain`. |
| 7 | An answer that fails parsing or grounding ends `succeeded` with `apply_outcome = discarded_invalid` and `error_code` `invalid_output`/`ungrounded`. | The provider answered (and may have billed); nothing is applied. |
| 8 | Cache hits move `queued → succeeded` with zero usage and `ai_cache_id` set; they do not count against call or token budgets. Only validated, normalised output is cached; hits are re-validated before applying. | No provider call happens. |
| 9 | Reservation waits for full windows (in-flight, per-minute, 429 cooldown) are Procrastinate retries of the *reservation* only; day/operation caps skip the request. | No provider call is retried; the queue timeout bounds the wait. |
| 10 | Token reservations are always labelled estimates (bytes + 512 input, `max_output_tokens` + `thought_reserve_tokens` output, default reserve 0 because Google documents thought tokens as inside `max_output_tokens`). A USD cap is never offered (`usd_cap_offered` is false) because no guaranteed token bound is proven. | D9; no false hard-cap claims. |
| 11 | Status is derived from settings, the gate, the AI-worker heartbeat (stale after 45 s) and the ledger. A key deleted because its session ended shows "Key expired"; an explicit removal shows "Key removed by X". | D7; truthful labels without new columns. |
| 12 | Every session revocation (logout, re-login, idle/absolute expiry, CSRF-integrity failure) deletes keys bound to that session in the same transaction; the AI worker also sweeps expired session keys. | D6. |
| 13 | The AI worker refuses to start if `GEMINI_API_KEY` or `GOOGLE_API_KEY` is set, or if its keyring is missing or invalid. | The SDK would otherwise fall back to a process-wide key. |
| 14 | The H1 field allowlists for PO and GRN (`contracts.py`) and their eligibility rules (`h1_eligibility.py`) are implemented as pure code but remain *proposed* (D13) and are not wired to any document until Product approves. | D13. |
| 15 | Phase 1 correction: the SDK derives `output_text` from `steps[].content[]` items of type `text`. The Phase 1 mock used `steps[].text`, which yields an empty `output_text`. The adapter tests now use the real shape. | Recorded in `docs/ai-gemini-protocol.md`. |

## D17 — Extraction engine: local hybrid now, external Document AI later by switch (user decision, 4 Oct 2026)

Baseline on the user's 16 sample invoices (local, read-only, 4 Oct 2026): no invoice had all six critical fields; line items were found in 6/16; every document needed verification, including digitally searchable ones. Layouts include Tally, Amazon, Ingram Micro (multi-page, item detail lines), gridded supplier tables, handwritten PO notes, stamps and signatures.

Decision: extraction runs **locally** ("local hybrid"): layout-aware OCR with word positions, grid-line and column detection, a Tally layout recognizer, arithmetic and GSTIN-checksum cross-checks, vendor layout templates learned from human corrections, and human verification. Nothing leaves the server.

The engine sits behind a pluggable port selected by `RB_EXTRACTION_ENGINE` (only `local_hybrid` exists now). The user intends to switch later to **local + external Document AI**; that will be an added adapter behind the same port, gated by consent and `RB_AI_EXTERNAL_CALLS`, with local cross-checks staying authoritative and the rule unchanged that unverified data never auto-matches.

Accuracy statement: no OCR guarantees 100% field accuracy on arbitrary scans. The guarantee this product makes is **zero false Matched**: any value not confirmed by cross-checks or a person routes to review. Field accuracy is measured against a labelled truth set and reported, not assumed. Real sample documents are used locally only (user-approved), never committed, uploaded to CI or sent to a provider.

## D18 — When an extracted value counts as verified (4–5 Oct 2026)

Applies to the layout extraction engine (D17). A value is **verified** only when one of these holds; otherwise it goes to human review, and an OCR-only value never passes the 0.70 review gate.

| Status | Rule | Confidence |
|---|---|---|
| confirmed | An arithmetic relation between independently read values holds exactly: qty × rate = subtotal; subtotal − discount + freight = line taxable; taxable + taxes = extended; lines sum to the taxable total (±₹1); taxable + CGST + SGST/IGST = grand total (±₹1 round-off); amount in words = a separately read total; GSTIN checksum valid; the same document number on another page or on a matching e-way bill; a second OCR reading (2× crop, single-line mode) agrees exactly. | 0.98 |
| corrected | A relation fixes a misreading only when the true value differs from the reading by known OCR slips (0↔8, 6↔0, 3↔8 …, at most two digits) or by leading digits hidden under a tick mark; a hidden rate is accepted only when exactly one whole quantity fits subtotal ÷ quantity. | 0.98 |
| clear | Fields no arithmetic can check (number, date, PO reference) read with every character at ≥ 93 OCR confidence and no letter/digit look-alike. | 0.85 |
| derived | Obtained only by division (e.g. qty = amount ÷ rate). | 0.60 |
| unverified | Anything else read by OCR (text-layer values: 0.90). | 0.65 |

Guards: a document number containing a letter/digit look-alike next to a digit (O/0, I/1 as in "PAPLO826") is never auto-verified, even when two copies agree, because both copies can be misread the same way and canonical matching keeps O and 0 distinct. Amount in words never confirms itself. An e-way bill whose document number differs from the invoice is ignored and reported (`ATTACHED_EWAY_BILL_OTHER_DOCUMENT`).

Measured on the user's 16 sample scans (8 invoices, local only): header fields 112/126 correct (baseline 36/126), line items 52/62 (baseline 15/62), 6 documents fully verified without review, and **zero false-safe documents** (no unflagged document with a wrong value). Adding the new core modules changed `RULES_HASH`; the golden-set self-check still passes, but the golden set has no cases yet, so the "zero golden diff" check is vacuous until Phase 4/6 golden data exists.

## D19 — Findings from the first full-stack reconciliation smoke (`make demo`, 5 Oct 2026)

`make demo` runs a synthetic month (`fixtures/demo-month/`: 7 POs, 9 GRNs, 12 invoice documents as Tally XML, CSV registers, a text PDF, two scans and a ZIP with an unreadable scan) through disposable PostgreSQL, MinIO, API and worker containers over HTTP, then compares every case and the workbook with `expected.yaml`. It now passes: 11 cases, at risk ₹4,980.00. Running it found the defects below; each fix follows the PRD/TRD text it cites. Engine version 0.1.2 → 0.1.3.

| # | Finding | Decision |
|---|---|---|
| 1 | Every case went to Needs Review, with no reason code, when the PO file had no status. Tally and most ERP PO exports carry none. | PRD: PO_NOT_OPEN fails only a closed or cancelled PO. A missing status passes `s1.po_open`; the evidence says the PO was treated as open. |
| 2 | `s4.ledger_completeness` warned on every PO dated before its first receipt (always true), turning clean matches into Matched (flags). | PRD §ledger completeness: warn only when earlier months are missing, i.e. the PO predates the first month of receipts and invoices in the pool. |
| 3 | Received quantity above the PO cap counted as billable, so SCN-04's second invoice lacked QTY_OVER_GRN. | TR-REC-7: the GRN quantity compared with billing is capped at the PO cap. |
| 4 | An unreadable scan became Unlinked [NO_PO_LINK, LOW_CONFIDENCE_DATA]. | SCN-19: Needs Review [NO_TEXT_LAYER]; the missing PO link is a symptom and is not raised. |
| 5 | A period filter on the export or case list dropped undated invoices, so an unreadable scan vanished from the month's workbook. | Undated invoices are always included. The Home `kpi_summary` SQL function still excludes them; fixing it needs a migration and is carried to 4.18 (Home KPIs = Excel Summary). |
| 6 | `GET /cases` always returned 422: the cursor context hash rejected the status-filter list. | Lists in the context are hashed. |
| 7 | CSV registers with a "Purchase Order" reference column were sniffed as POs, so GRN and invoice registers stuck in a zone-mismatch prompt. | The document's own number column ("GRN No", "Invoice No") decides the type; a PO label decides only when neither is present. |
| 8 | Tally XML lines lost their unit (`ACTUALQTY` "10 Nos"). | The unit is taken from quantity/rate measures when no unit tag exists and they agree. |
| 9 | Scans skewed by 0.3–0.6° left table borders as "|" glyphs that corrupted numbers (1000.00 read as 4000.00). | OCR pages are deskewed (projection profile, ±5°, 0.1° steps); short vertical borders are removed when they touch a horizontal rule; standalone rule glyphs are dropped; printed column borders bound table columns; Rate/Amount sub-columns under CGST/SGST/IGST are tax columns. |
| 10 | Safety: confirming the invoice number by dual-read cleared the review flag even when lines were missing or taxes unverified; a goods line without quantity, missing taxable value and unverified CGST/SGST/IGST did not flag a document; an implicit round-off of up to ₹1 let a wrong lines-sum taxable be marked verified on a total with paise. | Every review reason is listed and dual-read only clears the flag when nothing else is outstanding; those conditions now flag; a round-off applies only to whole-rupee totals and line sums must match within ₹0.02. |

Measured after these changes on the 16 local sample scans: line items 54/62 (was 52), header fields 104/126 (was 112; the lost fields are all flagged for review), FALSE_SAFE 0.

Open, for the Phase 4 exit review:
- Two files carrying the same invoice (a register row and its rescan) are processed in parallel and the last one parsed becomes the current version. A lower-quality OCR rescan can therefore replace a clean register row. Proposed: a new version replaces the current one only when its verified-field confidence is not lower, otherwise it is kept as an alternative reading.
- `kpi_summary` and undated invoices (item 5).
- Header accuracy on the samples fell from 112 to 104 because deskew changed some OCR readings; all affected fields are flagged.

## D20 — H3 top-five PO re-rank (AI Assist Half 2, Phase 9, 5 Oct 2026)

| # | Decision | Why |
|---|---|---|
| 1 | Pool = pure `rank_po_candidates(limit=5)` in `reconbench_core/mapping/links.py`: same vendor, open balance, PO dated on/before the invoice, not rejected by a person, whole-document content score on the phase-A snapshot. `resolve_links` and its top-3 candidates are unchanged (golden and scenario suites pass; `RULES_HASH` updated). | Plan Phase 9; the AI never sees a PO the rules could not consider. |
| 2 | Eligible: invoices whose resolution is `unlinked`, cite no PO reference, and have no live link. Subject = `link_resolution` row; subject version = a fingerprint of the invoice, the pool and the mapping config. The worker rebuilds the pool from the current snapshot and sends only if the snapshot hash equals the stored resolution hash and the fingerprint still matches. | Stale input never reaches the provider; unrelated uploads do not re-ask unchanged invoices. Cited-but-unresolved references stay a human problem. |
| 3 | Only document numbers, dates and line items are sent (strict redaction: GSTIN, PAN, contact and bank details). A choice must be a listed token with an exact invoice quote; `abstain` adds nothing. | Grounding and pool membership (validators.v1). |
| 4 | Apply locks the `link_resolution` row, re-checks its hash, live links, the PO's resolved vendor and any manual rejection of that PO, then inserts one header `suggested` link (`ai_suggested`, unique `ai_call_id`, confidence ≤ 0.90). No scope is marked dirty. | One link only; never supersedes an auto, suggested or confirmed link. |
| 5 | **A pending AI suggestion is excluded from reconciliation and PO-ledger queries** (`NOT (ai_suggested AND state='suggested')`). | The recon engine treats `suggested` links as active; without this an unconfirmed AI answer would change verdicts (SCN-20). |
| 6 | **Confirm** on an AI suggestion records the operator's choice as the manual header link the resolver re-proposes on every run (`method=manual`, 1.00, provenance `ai_suggested=true`), through the existing `PATCH /links/{id}`; scopes are marked like any confirm. **Unlink** is the existing manual rejection, which removes that PO from future pools. Bulk confirm skips AI suggestions. | A confirmed `content` link would otherwise be superseded on the next re-run (rules never propose it), losing the human decision. Auto-accept stays off (D11). |

Open:
- Pre-existing, outside H3: a confirmed rules `content` link becomes `suggested` again when a later re-run re-proposes it with a changed input hash, and the manual "change" action writes evidence text ("…by an operator.") that differs from the resolver's manual evidence ("…by the operator"). Both deserve a look in the Phase 4 exit review.
- The worker rebuilds the whole-workspace mapping snapshot once per H3 request (bounded by 25 calls per operation); fine at demo scale, to be measured at 10k documents.

## D21 — H1 PDF extraction suggestions (AI Assist Half 2, Phase 10, 5 Oct 2026)

| # | Decision | Why |
|---|---|---|
| 1 | Only PDF **invoices** are wired. PO/GRN allowlists and eligibility stay proposed (D13); PO/GRN corrections are not added. | D13 awaits Product; without it the PO/GRN follow-up equivalence (vendor resolution, `link_vendor`, `recompute_po`, dirty scopes) cannot be claimed, so that exit subcriterion stays **open**. |
| 2 | Subject = one immutable parser version (`document_version`), subject version = its `content_hash`. It is queued in the parse transaction (in a savepoint; a failure never blocks parsing) only when the gate is `enabled`, AI is on, consent is current, a key is usable, limits are configured and `h1_eligibility` passes (invoice critical-field gate, PDF method, usable text, ≤ 8 pages, ≤ 40,000 B). Manual versions are never eligible. | Same producer rules as H3/H4; stale input never reaches the provider. |
| 3 | Sent text = the stored page text with e-mail, phone, PAN, IFSC and account numbers replaced (`H1_CATEGORIES`); GSTINs stay because they are invoice fields. | Plan Phase 4 redaction rules. |
| 4 | **No new table.** H1 is always cacheable; the validated, normalised answer lives only in `ai_cache` (workspace-scoped, TTL). The handler writes nothing to the document; `apply_outcome = applied` means "suggestions available". | Suggestions only; the cache already gives TTL and post-expiry behaviour. |
| 5 | `GET /documents/{id}/ai-suggestions` (`data.view`) takes the latest H1 request for any version of the document and **re-grounds** the cached answer on the *current* page text every time (placeholders, page, quote, value-in-quote, conflicts). States: `not_applicable`, `pending`, `available`, `expired` (cache past TTL), `unavailable` (+ code). Offsets are mapped back to the unredacted page. | Copied page text keeps suggestions usable after a partial save; a stale or ungrounded answer is never shown. |
| 6 | The correction body gains `ai_suggestion_refs` (`ai_call_id`, scope, line_no, field). Each ref must cite the call shown by the view and a grounded suggestion whose typed value equals the saved value, otherwise 422 `ai_suggestion_ref_invalid` (`ai_suggestion_unavailable` when the answer expired). For changed fields the evidence row is `source='manual'`, confidence 1, with the suggestion's page, quote (≤ 500 chars) and `ai_call_id`; the audit event lists the refs. | The provenance claim is verified server-side; a person is still the one who saves (no auto-accept, D11). |
| 7 | D14 behaviour: every correction save runs TR-PAR-5 arithmetic (shared `arithmetic_failures` in `reconbench_core/parsers/common.py`, same ±₹1 tolerances as the parser). The result is stored in `extras.correction_qa`, the `qa` event becomes `warn`, and a `CORRECTION_QA_FAILED` document warning is opened (resolved by a later passing save). It **flags, it does not block** a verified save. | Real invoices can carry vendor arithmetic errors that a person must be able to record as read; the reconciliation engine independently raises `MATH_ERROR`, so a flagged invoice cannot be falsely Matched. `RULES_HASH` changed for the refactor only; unit, property, scenario and golden suites pass unchanged. |

Open:
- PO/GRN H1 and PO/GRN corrections (D13; equivalence to re-ingest not shown).
- Review UI (Apply to draft / Ignore, version-mismatch block) is Phase 13; `backend/openapi.json` and `web/src/api/generated.ts` were regenerated with the official scripts for the new endpoint and body field.
- Local Vitest workers fail to start in this environment (`webidl.util.markAsUncloneable`, a Node/undici mismatch); `tsc -b` passes. Re-run Vitest on the CI Node version.

## D22 — H2 uncertain-item opinion (AI Assist Half 2, Phase 11, 5 Oct 2026)

| # | Decision | Why |
|---|---|---|
| 1 | Eligible line = a `recon_line_result` of a **succeeded** run with `alignment_state='unsure'`, `line_type='goods'`, a PO line, score in the fixed band **0.60 ≤ score < 0.80**, and no spec-token conflict as computed by the core rule (`normalize_item_text` + `spec_tokens_conflict`, unchanged; `RULES_HASH` unchanged). Single-line explicit `unsure` below 0.60 is out of band. | PRD/App Flow H2 band; an AI opinion must never be able to argue past 16GB vs 8GB, so such lines are never sent. |
| 2 | One request covers up to `max_pairs` (20) eligible lines of one run: subject = `recon_line_batch`, `subject_id` = run id, `subject_version` = `start:count:fingerprint` over the run's eligible lines ordered by (invoice line, PO line). The worker rebuilds the slice from the immutable run rows and sends only if the fingerprint still matches. Up to `per_operation_calls` (25) batches per run. | Run rows and `doc_line` are append-only, so the slice is reproducible; the stable order lets an identical later run hit the cache. |
| 3 | Queued by `ReconciliationService` in its own transaction **after** the success commit; any enqueue error is logged and swallowed. Nothing waits for the AI worker. | Plan Phase 11 ("enqueue only after the run has succeeded"; D10 "runs never wait for H2"). |
| 4 | Sent text = description (≤ 300 chars), item code, HSN/SAC and unit per side, with strict redaction (GSTIN, PAN, e-mail, phone, IFSC, account). No quantities, rates or amounts. Each opinion needs an exact quote from both sides of its own pair (validators.v1). | Consent text for `h2_item`; grounding. |
| 5 | Apply appends one `ai_line_opinion` row per grounded pair (`ON CONFLICT DO NOTHING` on line result). `recon_line_result.ai_item_opinion/ai_item_reason` stay NULL; run, case and line rows are never touched. | D10: `recon_line_result` is append-only and never updated. |
| 6 | `GET /cases/{id}` adds `lines[].ai_opinion` = `{state, code}` with state `pending | available | not_applicable | unavailable`; `available` also carries `opinion`, `reason`, `model`, `created_at`. `not_applicable` codes: `NOT_ELIGIBLE`, `OUTSIDE_BAND`, `SPEC_CONFLICT`, `NOT_REQUESTED`; `unavailable` code = the request's error/skip code (e.g. `timeout`, `ungrounded`, `PROVIDER_UNAVAILABLE`) or `NO_OPINION`. | Plan Phase 11 API states. |
| 7 | The case ETag stays the case version (it is the `If-Match` for case actions), so a case with any eligible line is **never answered with 304**; cases without one keep conditional GET. | A late opinion changes the body without changing the version; a 304 would show a stale "pending". |

Open:
- Pre-existing, outside H2 (core): when an invoice has `GRN_PENDING` and is aged but its status is not `pending` (e.g. `needs_review` because of `ITEM_UNSURE`), the engine returns `aged=true`, which violates `recon_case_aged_ck` (`NOT aged OR status='exception'`) and **fails the whole run**. Found while building the H2 fixture (run date 204 days after the invoice). Needs a core rule decision in the exit review.
- Review UI for the opinion (quiet advisory line, App Flow / UIUX brief) is Task 4.18; `openapi.json` is unchanged because the case response is untyped.

## D23 — `ai_first` opt-in (AI Assist Half 2, Phase 12, 5 Oct 2026)

| # | Decision | Why |
|---|---|---|
| 1 | `ai_first` means only what D12 records: H1 suggestions are requested for **every** PDF invoice with usable text, not only those the rules could not read (`h1_eligibility` skips the `needs_verification` gate in this mode). Same caps (≤ 8 pages, ≤ 40,000 B, per-operation and daily limits), same redaction, same suggestion-only review and correction-save path. PO/GRN stay unwired (D13, D21). | Only reading compatible with SCN-20; the PRD's fuller "AI-first extraction" stays marked not met. |
| 2 | The mode has its own consent hook list (`h1_extract:all_pdfs` instead of `h1_extract:fallback`) and therefore its own fingerprint. Switching between `ai_assisted` and `ai_first` in either direction needs a fresh consent in the same save (422 `consent_required` otherwise). | Plan Phase 12 exit: consent re-required on switching modes. |
| 3 | Requests already queued under the old mode are skipped at send (`AI_OFF`) because the request's mode no longer matches the settings; nothing is back-filled for documents parsed before a switch. | No call may run under consent the person has replaced. |
| 4 | Labels: settings view adds `mode_label` and a `label` per consent hook (`MODE_LABELS`, `HOOK_LABELS` in `ai/consent.py`); `GET /documents/{id}/ai-suggestions` adds `mode` and `label` (`h1.SUGGESTION_LABELS`) from the request that produced the answer. Display only — not part of the fingerprint. | Plan Phase 12: "its own label", so a reviewer can tell AI-first suggestions from fallback ones. |

Checks: `tests/integration/test_ai_h1.py` (clean invoice not queued in `ai_assisted`, queued in `ai_first`; after a grounded-but-different and hostile answer the document, its evidence and the full extraction projection equal a rules-only workspace; labels; mode switch skips the queued request with no provider call) and `tests/integration/test_ai_settings_api.py` (switch to `ai_first` with new consent, hook labels, switch back refused without consent).

Open:
- `openapi.json` is unchanged because both responses are untyped; the label fields are for the Phase 13 review UI and the Phase 14 AI card.

## D24 — Task 4.17 review surfaces (AI Assist Half 2, Phase 13, 5 Oct 2026)

| # | Decision | Why |
|---|---|---|
| 1 | **H1 (Ingestion preview, invoices only):** `AiSuggestionsPanel` reads `GET /documents/{id}/ai-suggestions` and shows the request's label, model, and each grounded suggestion with its current value, page and quote. **Apply to draft** only fills the existing correction editor; nothing is sent until the person saves. **Ignore** hides a suggestion locally. Pending, expired and unavailable states get one quiet line; `not_applicable` shows nothing. | No auto-accept (D11); the person still saves and verifies through `PUT /documents/{id}/correction`. |
| 2 | Apply is blocked when the view's `document_version` differs from the version the preview shows, when the suggestion equals the current value, or when its line is not in the draft. | A suggestion read for another version must not reach a draft (plan Phase 13 exit). |
| 3 | A save sends `ai_suggestion_refs` only for applied values the draft **still holds** (PO/GRN refs: still in the list). A value edited after Apply is saved as a plain manual correction. | The server rejects a ref whose value differs (D21 #6); the UI never claims AI provenance for a value the person changed. |
| 4 | **H3 (Mapping):** the vendor-documents payload adds `links[].ai_suggested` (one column added to the existing query; the response is untyped, so `openapi.json` is unchanged). A pending AI link shows an "AI-suggested" tag and "Not used in reconciliation until a person confirms it"; its Unlink button reads **Reject** and calls the same `PATCH /links/{id}` `unlink`. A confirmed one reads "Confirmed from AI suggestion". | D20 #5–6: Confirm and Reject are the existing link workflow. |
| 5 | **H4 (Mapping merge banner):** `ai_opinion` / `ai_reason` (already returned) are shown as "AI opinion · advisory" next to the rule evidence; the merge buttons and the PAN-conflict block are unchanged. | H4 never merges or decides. |
| 6 | **H2 (case panel):** `lines[].ai_opinion` is shown under the line's item result as a quiet advisory line (pending, available with reason, unavailable with code); `not_applicable` shows nothing. | D22 #6 states; the run and case result are untouched. |
| 7 | **External-sharing banner** on Ingestion, Mapping and Reconciliation whenever the mode is not `rules_only`, built from `GET /ai/settings` (`data.view`). The text follows the deployment gate: `disabled` → "no document data leaves ReconBench"; `synthetic_only` → "uploaded documents are not sent"; `enabled` → "redacted and sent to Google Gemini (model)". | Honest disclosure (D3, D7); no banner in rules-only mode. |
| 8 | PO/GRN per-type editors are **not** added. | PO/GRN H1 and corrections stay open (D13, D21 #1). |

Checks: `web/src/components/AiReviewSurfaces.test.tsx` (Apply fills only the draft with no write request; edited value drops its ref; version mismatch blocks Apply; Ignore/show; pending and not-applicable states; banner on/off/disabled; H2 states) and a new case in `MappingPage.test.tsx` (AI tag, banner, H4 opinion, Reject → `unlink`). Vitest 23/23, `tsc -b`, eslint, money-math check and `vite build` pass under Node 24.19 (the CI version; local Node 20 cannot start jsdom 30, which explains the D21 note). `make openapi` regeneration leaves `openapi.json`, `generated.ts` and `docs/schemas` byte-identical. `test_ai_h3.py` asserts the new `ai_suggested` field; H1–H4, ingestion and Task 4.11–4.13 integration suites pass on a disposable PostgreSQL 16.15.

Open:
- PO/GRN review editors (D13, D21).
- The Home AI Assist card and settings UI are Phase 14 (Task 4.18).

## D25 — Home AI Assist card (AI Assist Half 2, Phase 14 / Task 4.18 AI part, 5 Oct 2026)

| # | Decision | Why |
|---|---|---|
| 1 | **New read-only endpoint `GET /ai/consent-preview?mode=&model=`** (`ai.manage`, `Cache-Control: no-store`) returns the consent text, per-hook label and data-sent list, and the fingerprint for settings that are not saved yet. It writes nothing; `rules_only` and a non-Gemini model id are 422. | The fingerprint covers server-side prompt, schema and redaction versions, so the browser cannot compute it, and `GET /ai/settings` lists hooks only for the saved mode. Without the preview a workspace could not go from rules-only to AI on in one consented save. |
| 2 | **Home card** (`AiAssistCard.tsx`) replaces the static "Rules only" panel: it shows the server status label (never "Connected", D7) and mode for everyone with `data.view`; managers (`ai.manage`) open an **AI Assist settings** dialog (portal to `body`). Viewers see "Only managers can change AI Assist." | One place for setup, as planned for Task 4.18. |
| 3 | Dialog fields: mode (rules-only / AI Assist / AI-first); provider fixed to Google Gemini (disabled select); model text field checked against the server pattern (catches a key pasted into it); **key field `type=password`, `autocomplete=off`, write-only, cleared from component state the moment Save is pressed whatever the outcome**, never put in storage or URL; storage choice with session-expiry wording; consent from the preview with the per-hook data list and a required checkbox unless matching consent is on record; withdraw consent. | Phase 14 work list. |
| 4 | Limits, usage and cost show only what the server knows: calls/tokens today against daily limits, "Not available" cost with the server's notice when no price is configured, the "no USD cap offered" notice, a "limits not configured" notice, and the deployment gate notice (`disabled` / `synthetic_only`). | Honest availability notices (D7, Phase 6 limits). |
| 5 | **Test provider** queues `POST /ai/test/provider` and polls `GET /ai/requests/{id}` (1 s, up to 45 polls), then reloads the status label; it is disabled while the form has unsaved changes, without a key or model, or when external calls are disabled. **Remove key** needs a second confirm click. **Uncertain calls** from `GET /ai/usage` are listed with an Acknowledge button and the "may have been billed; one new attempt" wording. A 412 reloads settings and asks the person to review. | Phase 14 work list; the AI worker alone calls the provider. |
| 6 | Settings page AI panel is renamed "Offline guardrail test" and points to the Home card; the offline self-test is unchanged. | Plan: copy pointer only. |

Checks: `tests/integration/test_ai_settings_api.py` adds the preview test (fingerprint equals the service fingerprint, data list equals `DATA_SENT`, no version bump, the preview fingerprint is accepted by `PUT`, bad inputs are 422 without echoing a key) and the route-capability entry; the suite passes 20/20 on a disposable PostgreSQL 16.15. `e2e/ai-assist-card.spec.mjs` (mocked API that, like the server, never echoes the key): save with consent and a session key, provider test, acknowledge, remove key; the key appears in exactly one request (the `PUT` body), in no URL and no response body; it is absent from the input after save, `localStorage`, `sessionStorage`, `document.cookie`, context cookies, storage state and the page DOM; a viewer sees status but no Manage button. Full Playwright 11/11, Vitest 23/23, `tsc -b`, eslint, money-math check and `vite build` pass under Node 24.19. `openapi.json` and `generated.ts` regenerated for the new endpoint.

Open:
- Whether a real server never returns the key over HTTP is covered by the integration test `test_key_is_sealed_write_only_and_never_echoed`; the Playwright network test runs against mocks.
- Rest of Task 4.18 (Home KPI `kpi_summary` undated-invoice migration, tracker, settings polish) and the Phase 15–16 verification and synthetic smoke test.


## D26 — Home KPI undated invoices and the G-12 tracker (Task 4.18, 5 Oct 2026)

| # | Decision | Why |
|---|---|---|
| 1 | **Revision `0018_kpi_summary_undated`** replaces `kpi_summary` so the period is `invoice_date IS NULL OR invoice_date BETWEEN from AND to`. Signature, owner and grants are unchanged; only the routine hash in `schema_contract.json` changed (recorded in `docs/schema-errata.md`). | D19 #5: the export always includes undated invoices, so Home showed fewer invoices than the Excel Summary for the same period. The plan's "Home KPIs equal the Excel Summary" needs the same rule. |
| 2 | **New read-only endpoint `GET /workflow-status?from=&to=`** (`data.view`, `Cache-Control: no-store`) returns the four tracker steps with `complete` and their counts, `next_step` (first incomplete step, or null), the latest succeeded `run_id` with cases in the period, and any `active_run_id`. Completion follows G-12: **Ingest** ≥ 1 parsed document and no file queued/parsing; **Map** ≥ 1 document, zero unassigned documents and zero current *suggested* links; **Reconcile** a succeeded run with cases in the period and no stale current case in the period; **Export** a ready export of that run. The period uses the KPI rule (undated invoices count). | The browser cannot work out staleness or suggested links itself. One server query keeps the rule in one place. |
| 3 | **Home tracker** shows each step as done / current / upcoming with a short reason ("2 unassigned · 1 suggested link", "3 stale cases — re-run"). The *Next step* button follows `next_step`, except "View run progress" while a run is queued or running. Review and Export open the latest run. | Plan 4.18 Home tracker with *Next step* (G-12). It replaces the old guess based only on the invoice count. |

Checks: `tests/integration/test_kpi_summary.py` (undated invoices count in every period; dated invoices outside the period stay out); `test_ingestion.py` now uses the same rule in its independent KPI and export SQL, and checks `/workflow-status` on an empty workspace (`next_step = ingest`) and after the export (run, Ingest/Reconcile/Export complete, Map matches the independent unassigned count, 422 for a reversed range). The migration-safety check (0017 copy → 0018), drift, catalog, index and decision checks pass on a disposable PostgreSQL 16.15. `test_ai_assist_migration.py` now checks for a single head instead of head `0017`, and `test_phase4_task_411_413.py` accepts `0018`. Playwright 12/12, including a new tracker journey; Vitest 23/23; `tsc -b`, eslint, money-math check and `vite build` pass. `openapi.json` and `generated.ts` regenerated.

Open:
- Settings polish for Task 4.18 (scoped tolerance table, synonym tags, UOM, buyer entities/GSTINs, GST slabs, payment terms against the plan list), then the Phase 15–16 verification and synthetic smoke test.
- `make demo` was not re-run in this change; the next demo run should confirm Home KPIs equal its workbook Summary, including the unreadable scan.

## D27 — Settings polish and Schema §5.1 tolerance overrides (Task 4.18, 5 Oct 2026)

| # | Decision | Why |
|---|---|---|
| 1 | **`engine.overrides` now reaches the engine.** `reconciliation_config` folds `overrides.vendors` into each rule's `by_vendor` and `overrides.items` into `by_item` (no vendor) or a new core field `by_vendor_item` (key `"<vendor uuid>\|<item key>"`). Resolution is vendor-scoped item → item → vendor → global. A vendor id is followed through `merged_into`: the value is applied to every id in the merge family, and an override set on an id itself is applied last so a relative's override never replaces it. Item keys use the engine's casefolded item key. Engine version 0.1.3 → 0.1.4; `RULES_HASH` updated. | Defect found while polishing Settings: Settings and Schema §5.1 write `engine.overrides`, but the engine only read the non-contract `tolerances.<check>.by_vendor/by_item`. Overrides saved from Settings were silently ignored. TR-REC-11 requires item, then vendor, then global, with merged vendors followed. |
| 2 | **Config validation tightened at save.** Override tolerances must name one of the six checks and set at least one; item codes cannot be blank; one override per vendor and per (item, vendor) scope; buyer GSTINs must pass the core format and check-character test. Existing seeded configs remain valid. | UI/UX §8.8: "Never let a bad value save silently." The server is authoritative; the browser mirrors these rules with zod and the same base-36 GSTIN check. |
| 3 | **`GET /config` (and the 409 `current.config`) carries `defaults`**: the Schema §10.2 seed body. | Settings shows each selected default beside the current value with a reset, without hard-coding numbers in the browser. |
| 4 | **Settings page rebuilt from JSON textareas to structured editors**: tolerance table with scope column, units and a live one-line example, plus an *Add override* form (vendor, item, or item for one vendor); policies with worked examples for limit mode and payable mode, DATE_ORDER level, Pending ageing and the plan's thresholds; tag editor per synonym group; UOM aliases and per-item conversions; buyer entities with live GSTIN validation; effective-dated GST slabs; payment-terms table; searchable alias table showing where each alias came from. A sticky save bar counts unsaved changes and fields to fix and offers *Discard*. A 409 opens a dialog listing what the other user changed (leaf-level diff) and loads their version on request. A 422 lists the rejected field locations. After saving, the note links to Reconciliation for the re-run. Users and roles stay P1. | Plan 4.18 Settings list and UI/UX §8.8. |
| 5 | **Payment-terms defaults are a reference list only.** The payment due date is still read from the terms printed on the PO; the configured list is not applied to it. The page says so. | The source documents say the list "feeds the payment due date" but do not say how a default is chosen for a PO without terms. No finance rule was invented; open below. |

Checks: `tests/unit/test_config_overrides.py` (fold with merge family and item scope, own-id precedence, five invalid-override cases, buyer GSTIN check digit, `defaults` in the response) and a new vendor-scoped precedence test in `test_phase4_reconciliation.py`; backend unit + parser suites 491 passed. `tests/integration/test_configuration.py` now checks `defaults` and 422 locations for an unknown override check and a bad buyer GSTIN. On a disposable PostgreSQL 16.15: `test_configuration.py`, `test_phase4_task_411_413.py` (pipeline/CLI parity, 5,000-invoice bound) and `test_ingestion.py` pass. Web: new `SettingsPage.test.tsx` (defaults and reset, GSTIN blocking, one versioned body with override + synonym + UOM and `If-Match`, 409 diff dialog, 422 locations); Vitest 28/28; Playwright 12/12 with the Settings journey moved off JSON editors; `tsc -b`, eslint, money-math check and `vite build` pass. Core purity and rules-hash checks pass; `openapi.json`, `generated.ts` and `docs/schemas` regenerated. Ruff format also reformatted `reconbench_core/parsers/xml.py` (formatting only). Full integration folder in one process: 128 passed, 15 skipped; `test_phase4_task_411_413.py` errors there only because it refuses to clone a source database that other modules still hold sessions on (it passes when run on its own, above), and `test_infrastructure_ready.py` needs the Phase 1 S3 credentials that this run did not set.

Open:
- Payment-terms defaults are not wired to the due date (needs a Product rule: for example, a per-vendor default term when the PO has none).
- `make demo` re-run comparing Home KPIs with the workbook Summary, then the Phase 15–16 verification and synthetic smoke test.

## D28 — `make demo` re-run: Home KPIs equal the workbook Summary (Task 4.18, 5 Oct 2026)

`make demo` now also reads `GET /kpis` for the export period (1–30 Sep 2026) and compares it with the workbook Summary sheet, read by row label: Invoices, Matched %, Pending, Exceptions, Unlinked and At-risk ₹ (`_check_home_kpis` in `scripts/demo_month.py`). It also checks Summary At-risk against the cases.

| # | Finding | Decision |
|---|---|---|
| 1 | D26 confirmed: Home Invoices = 11 = Summary Invoices, including the unreadable scan (undated, Needs Review). | No change. |
| 2 | Home Exceptions 3 ≠ Summary Exceptions 4. The workbook counted Needs Review inside Exceptions; `kpi_summary` counts effective status `exception` only. | Schema `v_run_summary` (the Summary sheet's source) keeps `needs_review`, `exception` and `duplicate` apart, and App Flow defines Exceptions as current cases by effective status. The Summary sheet now writes **Exceptions = `exception` only** and adds **Needs Review** and **Duplicate** rows after Unlinked. The ₹ rows move from B9–B11 to B11–B13. No migration; Home unchanged. |

Result: `DEMO PASSED: 11 cases match expected.yaml`, at risk ₹4,980.00, Home KPIs equal the Summary. Checks: new `test_summary_counts_needs_review_apart_from_exceptions` in `tests/unit/test_export_workbook.py` (3 passed); `tests/integration/test_ingestion.py` now also asserts Summary Pending/Exceptions/Unlinked equal `/kpis` (2 passed on a disposable PostgreSQL 16.15).

Open:
- The Vendor Summary sheet's Exceptions column still counts Exception + Needs Review. The spec does not say how it splits; left as is for the Phase 4 exit review.

## D29 — Phase 15 full offline verification (AI Assist Half 2 / Task 4.17–4.18, 5 Oct 2026)

Every check in the Half 2 §6 matrix and the CI workflow was run locally on Windows (Python from `backend/.venv`, Node 24, disposable PostgreSQL 16.15 containers). No real documents and no provider calls.

| Check | Result |
|---|---|
| ruff | 1 error (E501 in `tests/integration/test_configuration.py`, added in D27) → fixed; then pass |
| mypy `--strict`, import-linter (3 contracts kept), core purity, rules hash `142c6dbb…` | pass |
| JSON Schema + OpenAPI + `generated.ts` regeneration | no drift |
| eslint + money-math check, `tsc -b`, `vite build` | pass |
| pytest unit + property + scenarios + parsers | 493 passed |
| Vitest | 28/28 |
| Golden harness `--self-check` | pass (false Matched detected) |
| CLI smoke bundle, run twice | byte-identical (`852b43ca…`); Linux/Windows comparison stays a hosted-CI check |
| `check_engine_version.py` vs `master` / `origin/phase2-hosted-ci` | pass (0.0.0 / 0.1.1 → 0.1.4) |
| pip-audit, `npm audit --audit-level=high` | 0 vulnerabilities |
| Fresh 16.15: `upgrade 0017` → migration safety (clone 0017 → 0018) → head → schema drift, catalog, indexes, decisions, seed idempotence, Procrastinate schema, table count | pass; 45 + 4 tables |
| Integration, CI groups (phase2/readyz/configuration/ingestion/ai migration/ai runner; phase4 4.11–4.13; ai settings) | 45 + 3 + 20 passed |
| Integration not in CI: `test_ai_h1..h4`, `test_kpi_summary` | passed (63 incl. auth skips) |
| `test_authentication.py` on its own fresh cluster (`RB_PHASE3_TEST_DATABASE_URL`) | 15 passed |
| `test_infrastructure_ready.py` | not run: needs MinIO + S3 credentials (CI compose step) |
| Playwright | 11/12; the 10,000-row scroll test fails its frame budget on this laptop (p95 66.7 ms, then 50.1 ms vs ≤ 50 ms); passes its virtualization assertions. Threshold not changed. |

§6 matrix → evidence: SCN-20/property #8 `test_scn_20_hostile_stub_cannot_change_engine_result`, hostile/invalid tests in H1–H4, H1 `test_ai_first_…rules_output_unchanged`, H2 `…run_rows_are_unchanged`; consent/revocation, uncertain-once acknowledgement, concurrent caps, cache isolation + TTL, key redaction in logs/SDK errors, synthetic gate (`test_gate_synthetic_only_accepts_only_exact_fixture_payloads`, `test_synthetic_only_worker_refuses_a_business_pair`), boundary (`test_ai_worker_boundary.py`, `test_general_worker_never_consumes_the_ai_queue`, `test_ai_task_declarations_can_be_deferred_but_never_executed`), H4 PAN exclusion, authorization matrix (`test_authorization.py`).

Fixes made:
1. **Hosted CI would fail at migration safety**: the schema job built `0016`, but the head is `0018` and the script requires head − 1. Reproduced locally (`expected 0017, found ['0016']`); `ci.yml` now builds `0017`.
2. **H1–H4 and KPI integration tests were never run in hosted CI**: new step *Run AI Assist hook and Home KPI checks* after the AI settings step (same order passed locally).

Open:
- `test_authentication.py` is not in hosted CI (it needs a fresh single-workspace cluster; roles are cluster-wide so a second database in the same cluster cannot be migrated).
- 10,000-row Playwright frame budget is hardware-sensitive locally; confirm on hosted CI.
- `check_engine_version.py` prints `→` and needs `PYTHONIOENCODING=utf-8` on a Windows console (Linux CI unaffected).
- Hosted CI status not observed this session (nothing pushed, per the owner's rule).
- Phase 16 synthetic smoke is owner-run (owner's key, `RB_AI_EXTERNAL_CALLS=synthetic_only`).

## D30 — Phase 16 controlled synthetic smoke (owner-run, 6 Oct 2026)

Run by the owner on the local Compose stack with `RB_AI_EXTERNAL_CALLS=synthetic_only`, the owner's Gemini key and model `gemini-3.5-flash-lite`. Evidence read from `ai_request`, `secret`, `ai_worker_heartbeat` and the API access log.

| Step | Result |
|---|---|
| Banner and provider test | Banner reads "sends only built-in synthetic test fixtures"; four provider tests `succeeded`/`applied` (subject `fixture`); usage 3/200 calls and 111 tokens at the time of the screenshot, cost "Not available" (no price file) |
| Real uploaded PDF (mode AI-first) | File parsed normally; **no `ai_request` row created**. `Enqueuer.ready()` returns nothing unless the gate is `enabled`, so business subjects are never queued under `synthetic_only`; the worker-side `SYNTHETIC_ONLY` refusal is the second layer (offline test). 0 business-subject requests sent |
| AI worker stopped | Provider test queued at 01:06:15, picked up 46 s after `start` (01:07:01), `succeeded` 01:07:03, exactly once; heartbeat resumed |
| Remove key | `DELETE /ai/key` 200; `secret` rows 0; no request created afterwards |
| Session-only key, logout, login | Key saved for the session, test `succeeded` 01:09:30; after logout `secret` rows 0 and no further request |

Not run live, because only one synthetic fixture exists (`provider_test.v1` in `ai/fixtures.py`): H1–H4 on fixtures (including an injection line), a late H2 opinion, and H3 confirm → stale case → re-run. These remain covered only by the stubbed integration tests (D20–D22).

Also done for the run (local tooling): Compose gained a one-shot `setup` service (AI keys if missing, `alembic upgrade head`, queue schema once) so `docker compose up -d` alone starts a working stack; keys now live in `deploy/secrets/worker/` (private, ai-worker only) and `deploy/secrets/api/` (public). The local database had been at `0016`, so the AI worker heartbeat failed until `0017`/`0018` were applied.

Open:
- Synthetic fixtures for H1–H4 (and an injection fixture), so the hooks can be smoke-tested against the real provider without business data.
- Restore `RB_AI_EXTERNAL_CALLS` to `disabled` in `.env` after the smoke test.

## D31 — AI-first real-provider findings: worker gate and Gemini `maxItems` (6 Oct 2026)

During the owner's one-time AI-first accuracy test (real invoices to Gemini, owner-approved, `RB_AI_EXTERNAL_CALLS=enabled`), two defects stopped every business hook. Neither showed up in Phase 16, which only ran the provider test fixture.

| # | Finding | Fix |
|---|---|---|
| 1 | The Compose `worker` service had no `RB_AI_EXTERNAL_CALLS` / `_APPROVAL_REF`, so ingestion, mapping and reconciliation jobs always saw the gate as `disabled` and never queued H1, H3 or H4 (0 requests after uploads). | `compose.yaml` passes both variables to `worker`, the same as `api` and `ai-worker`. |
| 2 | Every H1 call returned HTTP 400 `invalid_request`. An owner-run probe with synthetic text isolated it: the H1 schema with array `maxItems` → 400; the same schema without `maxItems` → 200; without `additionalProperties` → still 400. H2's schema had the same keyword. | `maxItems` removed from the H1 and H2 provider schemas; the caps (200 suggestions, 20 opinions) stay enforced by `H1Output`/`H2Output` validation. Schema versions `h1.schema.v2`, `h2.schema.v2` (consent fingerprint changes, so the owner re-consents once). New test `test_provider_schemas_avoid_array_max_items_that_gemini_rejects`. |

Checks: AI unit tests 210 passed; on a disposable PostgreSQL 16.15 `test_ai_assist_migration.py` + `test_ai_runner.py` 40, `test_ai_settings_api.py` 20, `test_ai_h1..h4.py` 62 passed.

Open:
- Provider error messages are never logged by design, so a schema rejection is only visible as `INVALID_REQUEST`; consider an offline schema lint against the provider's supported JSON-Schema subset.
- The AI-first accuracy comparison itself (rules-only baseline vs H1 suggestions vs ground truth) is still to run after rebuilding the images.

## D32 — AC-0 accuracy benchmark and rules-only baseline (6 Oct 2026)

`scripts/accuracy_bench.py` (`make accuracy-bench`) measures the whole chain on the owner's local truth set: the real parsers, then vendor creation and resolution, linking, duplicates and the reconciliation engine, in memory as one fresh workspace. It never reads the database and runs with `--network none`, so no provider call is possible. Plan §8.1 has the run instructions, the truth v2 rules and the metric definitions.

| Decision | Choice |
|---|---|
| Where it runs | In the worker image, with the working tree mounted, so OCR is the same Tesseract/OCRmyPDF as the app and the code is the current tree |
| What it reuses | The app's own rules, not copies. Three small refactors make them callable without a database, with no behaviour change: `mapping_config_from_body`, `vendor_config_from_body` and `ingestion.document_natural_key`. Vendor anchor creation is database-bound, so the bench mirrors `_ensure_gstin_anchors`; AC-4 moves that rule into the core and the bench then calls it |
| Reader cache | Keyed by file bytes, parser hints, extraction code hash, engine version and OCR tool versions. Re-runs are deterministic, and only an extraction change re-reads files |
| Truth v2 | Absent key = unknown (not scored); `null` = known absent. `po_refs` are printed PO references only. A bundle file is a source of several truth documents |
| Gate | Exit 1 when any plan §1 hard metric is non-zero |

Baseline on 35 files (26 truth documents: 10 invoices with 2–4 copies each, 8 POs, 8 GRNs; only 2 complete PO+GRN+invoice sets):

| Measure | Baseline |
|---|---|
| Parsed documents / missed (bundle PO and GRN pages) | 45 / 6 |
| Header fields correct | 251/353 (71.1%); invoices 188/271, POs 33/42, GRNs 30/40 |
| Line items found | 94/128; spurious 36 |
| Auto-verified documents | 5/45 (XML POs/GRNs 0/12, all flagged) |
| Fields to touch per invoice | 8.07 |
| **False-safe documents** | **3** (one invoice, three copies: a sales-order number or address digits taken as the PO reference on an unflagged document) |
| **False auto-links** | **1** (the bundle read as one invoice is linked to a PO from its own PO pages) |
| **False Matched** | **0** (no case is Matched at all) |
| **Wrong auto-created vendors** | **6** of 13 (buyer GSTIN 1, GSTIN as the name 5, label as the name 1) |
| Links found | 5/10, all suggestions; both complete sets unlinked (4 links) and one GRN→PO suggestion lost when the bundle invoice used up that PO's balance |
| Duplicates | 4 extra system documents from copies; 2 flagged duplicate, 2 missed, 0 false |
| Recon status agreement | 7/10 invoices |
| Trusted-but-wrong fields | 5 (for example the buyer's checksum-valid GSTIN read as the supplier GSTIN at verified confidence) |

Determinism: two runs from the cache and one run with fresh OCR (`--refresh`, about 160 s for 35 files) give the same report hash.

Checks: new `tests/unit/test_accuracy_bench.py` (6 synthetic tests); unit + property + scenario suites 454 passed; ruff, `mypy --strict`, import contracts and the rules hash unchanged; on a disposable PostgreSQL 16.15, `test_phase4_task_411_413.py`, `test_ingestion.py` and `test_ai_h4.py` 23 passed (refactored services).

Open:
- XML POs/GRNs are all flagged for verification even though every scored value is correct; check why in AC-1.
- The truth set is small (D-7): two complete sets, no XML item descriptions labelled, and a few values the owner should confirm (noted in the local truth file).
- `buyer_entities` (D-4) is still empty in the app Settings; the bench uses the value in the local truth file.

## D33 — AC-1 deterministic guards: party roles, Tally labels, GST checks, XML items (6 Oct 2026)

Every parser's output (XML, CSV, legacy PDF and the layout path) now passes `reconbench_core/parsers/parties.py::guard_result` before it leaves the core. `ParserHints` carries `buyer_entities` and `gst_rates`, built from the active config by `party_hints_from_config` in `ingestion._parser_hints` and in the bench.

| Decision | Choice |
|---|---|
| Buyer-entity guard | A configured buyer GSTIN is never the supplier. The layout reader treats it as the buyer; the shared guard moves any that slips through to `buyer_gstin` and flags the document (`VENDOR_GSTIN_IS_BUYER_ENTITY`). `_ensure_gstin_anchors` (and the bench mirror) never creates or extends a vendor from it |
| Supplier name | Read from the "for <Company>" signatory line, else the supplier block ("Supplier (Bill from)", "Sold By", "Bill & Shipped From", "Seller"; same row or the line under it, merging a slanted line). Rejected: labels, text holding a GSTIN, the buyer's own name (also with an OCR-dropped word), dates, tax/quantity words and running lower-case text (`VENDOR_NAME_REJECTED`). The hard-coded buyer name in `extract.py` is gone |
| Tally PO/GRN labels | Document number by type: PO "Voucher No."/"Order No."; GRN "Receipt Note No."/"GRN No."/"Voucher No.". A number split at its separator ("X/26- 27/08-42") is joined. A month with a lost letter ("12- Au -26") is read but stays unverified |
| PO references | GRN: "Reference No. & Date", "PO No.", "Order No.". Invoice: explicit PO labels first; a bare "Order No." is read only after them, never after a seller word (Sales/SO/Our), and is capped below the review gate. Address digits (pure numbers under 5 digits) and marketplace order numbers are never PO references; marketplace numbers go to `extras.marketplace_order_no`. An unverified PO reference now puts the document in review; dual-read may confirm it (not a capped one) |
| GST structure | Solver: an inter-state document (supplier state ≠ buyer state) prefers IGST, an intra-state one CGST + SGST; the slab bonus uses the total rate. Guard: IGST on intra-state, CGST/SGST on inter-state, unequal CGST/SGST, or a total rate outside the configured slabs flags the document with a warning |
| Amount in words | Trailing noise ("E & O E") no longer voids the reading; noise between number words still does |
| ₹ glyph | Only when the page prints "₹"/"%" before amounts: a leading 1/2/3/4/7 may be the sign; the shorter value is a candidate that wins only through arithmetic or the amount in words (status corrected) |
| Tax rows in the item body | "Input IGST @ 18%" / "Less: Rounding Off" rows are summary rows even with a rate in the rate column |
| GSTIN repair | At most 2 look-alike swaps (a PO number plus a date could otherwise become a checksum-valid "GSTIN") |
| Tally DBC XML | The line's item name is the first narration row (the stock item, often a category, stays in `extras.dbc_stock_item`); a part code becomes `item_code`; "SN:" lists and serial rows go to `extras.serials`. `ITEM_UNSURE` only when a line has no item name; a voucher is unflagged only when its lines sum to its taxable amount and its ledger amounts to its gross (`DBC_TOTALS_UNRECONCILED` otherwise) |

Bench (`make accuracy-bench`, same 35 files and truth; before = `before-ac1.json` after the 6 Oct truth fixes, after = `after-ac1.json`):

| Measure | Before | After |
|---|---|---|
| Header fields correct | 251/354 (70.9%) | 270/345 (78.3%); POs 40/41, GRNs 37/39, invoices 193/265 |
| Line items found | 94/129 | 94/129 (1 spurious tax line fewer) |
| Auto-verified documents | 5/45 | 17/45 (XML POs/GRNs 12/12) |
| Fields to touch per document | 5.76 | 5.11 (PO 0.50, GRN 0.25) |
| **False-safe** | **3** | **0** |
| **False auto-links** | **1** | **1** (the bundle invoice → PO from its own PO page; AC-3) |
| **False Matched** | **0** | **0** |
| **Wrong auto-created vendors** | **6** (buyer GSTIN 1, GSTIN name 5, label 1) | **4** (GSTIN as the name only; AC-4) |
| Links found | 5/10 | 6/10 |

The bench's name comparison now keeps word spaces so legal suffixes normalise ("PVT LTD" = "PRIVATE LIMITED"); this also exposed two names that only matched by accident before (an OCR typo and a dropped word).

Checks: new `tests/unit/test_accuracy_guards.py` (23 synthetic tests) and one bench test updated plus one added; unit + parser suites 522 passed, 1 failed (below); golden self-check, ruff, `mypy --strict`, import contracts pass; on a disposable PostgreSQL 16.15, `test_ingestion.py`, `test_phase4_task_411_413.py`, `test_configuration.py`, `test_ai_h3.py`, `test_ai_h4.py` 41 passed.

Open:
- Pre-existing, unrelated: Hypothesis finds `canonicalize_po_number("0/0")` is not idempotent ("00" → "0") in `test_phase4_normalize.py`. PO canonicalisation feeds linking, so fix it before AC-9.
- The legacy text-layer parser still reads amount in words for invoices only; Tally PO/GRN scans now go through the layout path.
- Amazon-style invoices still give no lines (the ₹ sign is read as 2/4/% inside the table) and Ingram line text is garbled; these need AC-5 vision.
- `buyer_entities` (D-4) is set in the app (config seq 2), so the earlier Settings save problem no longer reproduces.

## D34 — AC-2 e-invoice signed QR as an independent header reader (6 Oct 2026)

Invoice PDFs are now checked for the e-invoice portal QR (a JWT signed RS256 by the IRP). After the normal extraction (legacy, layout or OCR path), `pdf_sandbox.parse_pdf_isolated` runs a separate limited child (`_qr_child_main`, 30 s CPU / 45 s wall) that renders the first 5 pages at 300 DPI and decodes QR codes with zxing-cpp (`reconbench_app/extraction/qr_decode.py`). The pure core module `reconbench_core/extraction/qr.py` parses the payload (seller/buyer GSTIN, number, type, date, value, item count, main HSN, IRN) and merges it via `apply_einvoice`. Other QR codes (e-way bill, URLs) are ignored.

Owner decisions (6 Oct 2026):

| Case | Rule |
|---|---|
| Signature verified with a key from `RB_EINVOICE_PUBLIC_KEY_FILE` | L0: QR value used, confidence 1.0, verified |
| Unsigned, reading equal | L1: reading kept, confidence 0.98 (`confirmed`) |
| Unsigned, equal up to letter/digit look-alikes (O/0, I/1, …) | L1: **QR spelling** used, verified (`corrected`); the other reading kept in `extras.einvoice.other_readings` |
| Unsigned, different reading or none | QR value prefilled at 0.65 and listed in `review_fields` (`conflict` / `prefilled`) |
| Key | Setting added; the owner downloads the portal key later. Until then QR values are at most L1 |

Guards: the QR is not used (document flagged) when its seller GSTIN is a buyer entity, its buyer GSTIN is not a configured buyer entity (when entities are set), or its type is not `INV`; QRs of different invoices in one file are not used (`EINVOICE_QR_AMBIGUOUS`, bundles are AC-3). An item count different from the lines read puts `lines` in review. IRN goes to `InvoiceHeader.irn` and the QR details to `extras.einvoice` (candidate duplicate key for AC-8). A configured key file without a usable RSA key fails startup. The review flag clears with the same rule as the dual read. New dependency zxing-cpp 3.1.1 (Apache-2.0, wheels, no system library; `docs/dependency-review.md`); worker image rebuilt. `ENGINE_VERSION` unchanged (reconciliation contract untouched).

Probe on the local samples: the signed QR decoded on 11 of 12 copies of the 4 e-invoices; all 11 matched the truth on number, date, both GSTINs, total and item count. One re-scan's QR is damaged and does not decode at any DPI/filter, so that copy stays OCR-only.

Bench (`after-ac1.json` → `after-ac2.json`):

| Measure | After AC-1 | After AC-2 |
|---|---|---|
| Header fields correct | 270/345 (78.3%) | 280/345 (81.2%) |
| Auto-verified documents | 17/45 | 18/45 |
| Fields to touch per document | 5.11 | 4.89 |
| QR fields on 11 invoices | — | 45 confirmed, 6 corrected (look-alike document numbers), 2 conflict (an OCR total that was wrong → QR value prefilled for review), 13 prefilled (IRN, buyer GSTIN) |
| **False-safe / false Matched** | **0 / 0** | **0 / 0** |
| **False auto-links / wrong vendors** | **1 / 4** | **1 / 4** (unchanged; AC-3 / AC-4) |

Trusted-but-wrong fields stay at 4 (bundles and one Tally scan, none from the QR). Most QR invoices stay in review because of their line items (Ingram garble; AC-5).

Checks: new `tests/unit/test_einvoice_qr.py` (34 synthetic tests incl. a sandbox end-to-end PDF with a reportlab QR, warnings as errors); unit + parser + property + scenario suites 557 passed, 1 failed (the pre-existing `canonicalize_po_number("0/0")` idempotence failure, open since D33); ruff, `mypy --strict`, import contracts pass. Integration tests against PostgreSQL were not run this phase.

## D35 — AC-3 page split and classify; bundle files ask before they split (7 Oct 2026)

A PDF can hold several documents (a bundle of PO, GRN and invoice pages, or two invoices scanned together). Until now the layout path read every file as one document of its upload zone, so a 6-page bundle in the invoice zone became one invoice with mixed lines and the PO page's number as its PO reference, and it was auto-linked to that PO.

**Page types and boundaries** (pure core `reconbench_core/extraction/pages.py`):
- Each page is classified by its printed title in the top 40% of the page: invoice (TAX INVOICE, BILL OF SUPPLY, CASH MEMO, …), PO (PURCHASE ORDER, a short "PO …" heading), GRN (RECEIPT NOTE, GOODS RECEIPT/RECEIVED NOTE, …), e-way bill (the existing large-title rule, moved here), or continuation (no title). A larger font wins, then the earlier row.
- A title phrase is a label, not a title, when a number-label word or a value follows it or a reference word precedes it ("Invoice To", "Invoice No.", "Receipt Note No.", "Against Purchase Order", "PO 4500…").
- Grouping: a new document starts where the type changes. Pages of the same type split only on hard evidence: two **text-layer** pages with different document numbers (look-alike folded), or "Page 1 of N" after a finished run. Same-type OCR pages never split on a number alone, because multi-page scans repeat their title and OCR reads their number differently per page. E-way bills and untitled pages stay with the document before them; leading untitled pages join the first titled one; a file without any title is one document of the zone type, as before.
- On the 33 local PDFs the grouping is exact: both bundles split correctly (5 and 3 documents), and every multi-page invoice (2 to 5 pages, titles repeated on each page) stays one document.

**Pipeline**: `layout_parse` extracts each group separately with its own type and marks each document with `extras.page_split = {pages, group, groups, state}` and locator `layout:pA-B`. A file that gives exactly one document of the zone type is unchanged (no marker, same output). The sandbox now always prefers a split layout result over the one-document text parser, and a digital PDF whose text layer names another type, or a title on more than one page, is routed to the layout path (`split_only`). The e-invoice QR goes to the invoice on the QR's own page.

**Owner decision D-9 (7 Oct 2026): suggest the split, a person confirms.**
- A split (or a single document of another type) gives the file warning `PAGE_SPLIT_SUGGESTED` with the groups (pages, type, number, document id), `mismatch_state = suggested`, and the question on the file row. The documents are created at once so they can be seen, but every one is **held**: `needs_verification`, never auto-linked (any automatic link with an unconfirmed split source, target PO or via-GRN becomes a suggestion: "file split not confirmed"), and an invoice is gated to review in reconciliation (`s1.extraction_gate`, LOW_CONFIDENCE_DATA).
- `POST /api/v1/files/{id}/page-split` (`accept_split` | `keep_one`, If-Match, audit `file.page_split.resolve`) answers it and re-queues the file. `document_version` is append-only, so the answer is carried by re-reading: `ParserHints.page_split` is `confirmed` (state `confirmed`, no warning) or `off` (one document of the zone, the old behaviour). After the re-read, documents only the earlier reading produced go back to their other sources or are withdrawn (`_settle_documents`, shared with file removal).
- A split document never replaces a current XML/CSV/manual version of the same document (`structured_version_preserved`); bundle pages printed from the XML records stay evidence. AC-8 generalises this guard.
- `document_text` stores only each document's own pages. Ingestion UI: "PAGE SPLIT SUGGESTED" chip with the proposal, buttons "Split into documents" / "Keep as one INVOICE".

Bench (`after-ac2.json` → `after-ac3.json`, report d50141864d1d, stable on re-run):

| Measure | After AC-2 | After AC-3 |
|---|---|---|
| Parsed documents / missed / spurious | 45 / 6 / 0 | 51 / **0** / 0 |
| Header fields correct | 280/345 (81.2%) | 302/373 (81.0%) |
| Auto-verified documents | 18/45 | 18/51 (the 8 bundle documents are held for the split answer) |
| Trusted-but-wrong fields | 4 | 1 |
| Links found (auto + suggested) | 6/10 | 7/10 (S14 GRN→PO no longer lost to the bundle) |
| Extra documents / wrong recon status | 4 / 3 | 2 / 2 (the bundle no longer collides with the genuine invoice) |
| **False-safe / false Matched** | **0 / 0** | **0 / 0** |
| **False auto-links** | **1** | **0** |
| **Wrong vendors** | **4** | **4** (GSTIN-as-name; AC-4) |

Every non-bundle document's outcome is identical to AC-2. PO/GRN header rates fall only because the bundle's printed-record pages are now scored as documents (no amounts on those pages); they never replace the XML versions. "Duplicates caught" goes 2 → 0 because the two AC-2 "catches" were the mixed bundle invoice colliding with the genuine one; the two real duplicates (re-scans with a misread number or vendor) remain AC-8. A cold full run once timed out the 12 s text-parser child on 2 files under 35-file parallel OCR load; those 2 read cleanly alone.

Checks: new `tests/unit/test_extraction_pages.py` (29), split tests added to links, reconciliation and QR suites, new `tests/integration/test_page_split.py` (keep and accept flows, XML guard, stale-document withdrawal, If-Match), web test for the split buttons; unit + property + scenario + parser suites 590 passed, 1 failed (the pre-existing `canonicalize_po_number("0/0")` idempotence failure); ruff, `mypy --strict`, import contracts, golden self-check, web lint/typecheck and 29 web tests pass. On a disposable PostgreSQL 16.15 the integration suite gives 131 passed, 15 skipped; 4 need the S3 test credentials (`RB_PHASE4_TEST_S3_*`) and were not run.

Open:
- Same-type scanned documents in one file split only on "Page 1 of N"; two different scanned invoices without page numbers still read as one (they stay flagged by arithmetic/QR conflicts). AC-5 vision can add a boundary signal.
- The running app needs `docker compose up -d --build` to pick up AC-3.

## D36 — AC-4 vendor hygiene: verified-only vendor creation, name trust, cleanup (7 Oct 2026)

Before AC-4 any checksum-valid supplier GSTIN created a vendor, and a document without a usable name gave the vendor the GSTIN itself as its name. OCR misspellings of a name were learned as aliases. A supplier without a GSTIN could not be created at all, because the Mapping page could only assign documents to existing vendors.

**Owner decisions (7 Oct 2026):** D-5: on a marketplace invoice the **seller GSTIN printed on the invoice is the vendor**, not the marketplace (this was already the extraction behaviour; now it is the rule). An OCR-read name is kept as a **provisional** name and replaced by a verified or manual name later. A supplier without a GSTIN is created **only by a person**.

**Rule** (pure core `reconbench_core/mapping/vendor.py::plan_vendor_anchors`, used by the app and the bench):
- A vendor is created only from a **verified** supplier GSTIN that is checksum-valid and not a buyer entity. Verified means a digital source (`xml_profile`, `csv_mapping`, `manual`), a person's correction, or a rule value at the verified level (≥ 0.98, as D18). AI-read values stay suggestions (D11/D21) until AC-6. Other documents stay **vendor pending**: resolution can still match them to an existing vendor or suggest one, but it creates nothing.
- New `vendor.name_status` (`manual` > `verified` > `provisional` > `pending`, migration 0019). A vendor without a usable name is `pending` with an empty `canonical_name`; the UI shows "(name pending)". A name is never a GSTIN, a label, line text or the buyer's own name (`anchor_document` applies the AC-1 `name_problem`/`is_buyer_name` guards). Resolution only **upgrades** a name to a better status, picks the most common spelling within the best status (the result does not depend on document order), and never replaces a `manual` name.
- Aliases are learned only from **verified** names, so OCR misspellings no longer become aliases.
- GSTINs sharing a PAN go to one vendor. When several vendors already hold that PAN, nothing is created (`pan_ambiguous`) and a person decides. Before AC-4 a third vendor was created in that case.
- Merge: a survivor whose name is weaker (for example pending) takes the source's name. A GSTIN or label "name" is never kept as an alias.

**API/UI:** `POST /api/v1/vendors` (a person creates a vendor: name, optional GSTIN/PAN; checksum, buyer GSTIN, duplicate GSTIN/PAN and name guards; audit `vendor.create`) and `PATCH /api/v1/vendors/{id}` (rename, If-Match, status `manual`, the old readable name stays an alias; audit `vendor.rename`). `GET /vendors` returns `name_status` and `version`. Mapping page: "Name pending" / "Check name" badges, a rename button on the vendor detail, and "Create vendor" in the Unassigned view.

**Cleanup** (`python -m reconbench_app.vendor_cleanup --actor-email … [--apply]`, run in the api container, dry run by default). It lists vendors with a junk name (GSTIN, label, buyer name; manual names are skipped), junk aliases, or a buyer-GSTIN registration or PAN. With `--apply` it repairs them in one transaction with an audit event `vendor.cleanup` per vendor:
- a junk name becomes `pending`;
- junk aliases are removed;
- buyer registrations and the buyer PAN are removed, and the vendor's unlocked documents are unassigned;
- a vendor left with no anchor and no references is deleted. One that is still referenced (a manual assignment or a reconciliation case) is kept and reported for a person to merge.

It then runs vendor resolution and link mapping once. The live database was not touched in this session; the owner runs it.

Bench (`after-ac3.json` → `after-ac4.json`, report 771a665fd04c):

| Measure | After AC-3 | After AC-4 |
|---|---|---|
| Vendors created | 12 | 12 (4 pending, 3 provisional, 5 verified) |
| Learned aliases | 1 (an OCR misspelling) | 0 |
| Documents with the correct vendor / pending | 26 / 2 | 26 / 2 |
| Links, extraction, recon | — | identical |
| **Wrong vendors** | **4** (GSTIN-as-name) | **0** |
| **False-safe / false auto-links / false Matched** | 0 / 0 / 0 | 0 / 0 / 0 |

GATE: PASS (first time all four hard metrics are 0). The bench's vendor simulation now calls the same `plan_vendor_anchors` + `anchor_document` + `field_verified` as the app; a pending (empty) name is reported in `name_status`, not counted as junk.

Checks: new `tests/unit/test_ac4_vendor_anchors.py` (24) and `tests/integration/test_ac4_vendor_hygiene.py` (6: create, upgrade, manual name kept, alias, rename conflict, cleanup); `test_accuracy_bench.py` updated (a GSTIN no longer names a vendor); `test_phase4_task_411_413.py` accepts revision 0019. Unit + property + scenario + parser suites 602 passed; on a disposable PostgreSQL 16.15 the integration suite passes except `test_infrastructure_ready` (needs the S3 test credentials, not run) with 15 skipped; schema drift and catalog contract (enums 37 → 38, `vendor_name_status` added), table count, rules hash (updated, vendor planner is mapping-level), ruff, `mypy --strict`, import contracts, core purity, golden self-check, web lint, typecheck and 29 web tests pass.

Open:
- The running app needs `docker compose up -d --build` (migration 0019 runs on start), then the cleanup dry run and `--apply`.
- The four pending vendors get a name from a person (rename), from AC-5 vision, or from a QR/XML document of the same GSTIN.
- AC-8 re-scan identity still makes 2 extra documents.

## D37 — AC-5 vision reader: masked page images, suggestions only (7 Oct 2026)

A third, independent reader (plan §3, reader C) reads **masked page images** of PDF invoices, POs and GRNs and returns every value as printed, with its page and box. In AC-5 its values are **suggestions only**: nothing reaches a document, vendor, link or reconciliation case except through a person's correction save.

**Owner decisions (7 Oct 2026).** D-1: yes, masked page images may go to Gemini (paid tier, `store=false`); the key comes through the existing AI settings flow (Home AI Assist card → sealed `secret`, opened only by the AI worker), no new key path. D-2 stays with AC-6. D-3: yes, PO/GRN PDFs get the vision reader too (closes D13 for this reader); XML stays the preferred PO/GRN source. D-8 (cost cap) waits for the owner-run probe.

| Decision | Choice |
|---|---|
| Hook and switch | New hook `h6_vision`. A workspace turns page images on with a checkbox in the same consent flow (`ai_setting.vision_enabled`, off by default); the vision reader then **replaces H1** for that workspace (never both). While it is off, the consent fingerprint is byte-for-byte the old one, so existing consent stays valid. `RB_VISION_READER` (`vision_gemini` default, or `off`) is the deployment switch and the D17 port: a local model can be added later as another reader |
| Where pages are made | The general worker renders and masks each page in a limited sandbox child (`extraction/vision_pages.py`): digital pages use their text-layer word boxes; scanned or rotated pages get the same orientation and deskew as the OCR reader, so OCR words, masks and reader boxes share one coordinate space. Long side 2048 px, JPEG q90, deterministic (same page = same hash) |
| Masking | `ai/redaction.py::image_mask_regions` on word boxes: e-mail, UPI id, phone/mobile/fax, bank account (after its label, same row or below), IFSC, PAN, and the signing area above "Authorised Signatory"/"Signature" (not on item-table headings). GSTINs, names, addresses, numbers, dates, amounts and handwriting stay visible. Best effort: what OCR cannot read is not masked; the consent text says so |
| How images reach the AI worker | Table `document_page_image` (migration `0020`): hash, geometry, mask boxes and the masked bytes. The bytes are transient (cleared once no live vision request uses the version); the AI worker still has no object-store access |
| What is sent | Images only, never the OCR text, so the reading stays independent of OCR (needed for AC-6's two-reader confirmation). Schema `vision.schema.v1`: document kind, page kinds, typed fields (identity, parties with roles, typed references: PO, GRN, sales order, marketplace order, quotation, other; totals), one box per field, line rows with one box per row, and handwritten/stamped marks in `annotations` only |
| Checks | `reconbench_core/extraction/vision.py`: page sent, box valid, not inside a mask; values normalised by the OCR reader's own rules (`accept_doc_no/date/po`, decimal parsing, GSTIN checksum). Hard rejects (plan §7): buyer GSTIN as supplier, invalid checksum, tax type against the state rule, totals or line quantity × rate that break arithmetic the OCR reading satisfies, address/marketplace/sales-order numbers as PO references, two values for one field; label or buyer names. An answer with readings and nothing left is `ungrounded`. The checked reading becomes an unverified document (source AI, confidence 0.65) passed through the AC-1 guard |
| Cache, limits, ledger | Cache key = existing `ai_cache` key over the payload digest, which now includes the page image hashes (text-only digests unchanged). Limits: `h6_vision` max 8 pages, 16,384 output tokens, 12 MB images, 1,300 input tokens per image (an estimate), 120 s timeout; optional `per_month_calls`/`per_month_tokens` (off until D-8). Same gate, consent, key, reservation, one-attempt transport and ledger as H1–H4 |
| Review | `GET /documents/{id}/ai-suggestions` shows the latest H1 or vision answer, re-checked; vision items carry `box` and the marks list; PO/GRN readings are read-only (`correctable=false`) until AC-7 adds their corrections. A correction citing a vision suggestion records page, as-printed text and box as evidence |
| SDK shape | The pinned SDK sends a content list as one `user_input` step (text, `Page N`, image parts); recorded in `docs/ai-gemini-protocol.md`. Live acceptance, the `box_2d` order and tokens per page are for the probe |

**Bench** (`before-ac5.json` → `after-ac5.json`): the main report hash is `771a665fd04c` both times (identical to `after-ac4.json`), so vision changes no rules reading, vendor, link or case; hard metrics stay **0/0/0/0**. The vision block (own hash) covers 39 PDF documents, 67 masked pages; masked regions: phone 123, PAN 72, signature 28, e-mail 14, bank account 7, UPI 5, IFSC 3. A counts-only check of the label words on masked rows found every PAN mask on a PAN-labelled row and no mask on an invoice- or order-number row. **No reading is scored yet**: there are no recorded answers until the owner runs the probe and `make accuracy-bench-vision` (vision alone per field, OCR × vision agreement for AC-6, handwritten-reference and marketplace-order recall, page kinds).

**Owner steps.** `docker compose up -d --build` (migration 0020); `make vision-probe` (dry run: look at the masked synthetic pages in `work/vision-probe/`); `PROBE_KEY=… make vision-probe PROBE_ARGS="--send"` (schema acceptance, box order, tokens per page at medium/high/ultra_high, latency; prices only if given); then `BENCH_GEMINI_KEY=… make accuracy-bench-vision BENCH_ARGS="--max-provider-pages 120"`; then tick "Read page images" in the AI Assist card and consent.

Checks: new `tests/unit/test_vision_core.py` (22), `test_ai_vision.py` (43), `test_accuracy_vision.py` (4), `tests/integration/test_ai_vision.py` (11: prepare → request → recorded answer → view, document/vendor/links unchanged, bytes purged, provenance, GRN read-only, H1 kept when off, nothing prepared when the gate, mode or reader is closed, render failure, month cap, stale answer) and a settings test for the vision consent; web test for the vision panel. Unit + parser + property + scenario suites 669 passed, 1 failed (the pre-existing `canonicalize_po_number("0/0")` idempotence failure); golden self-check, rules hash (updated: new core module), engine version (no rule path changed), core purity, ruff, `mypy --strict` (core and the new app modules), import contracts, web lint/typecheck and 30 web tests pass. On disposable PostgreSQL 16.15 the integration suite passes (Phase 4 acceptance run separately, as in CI) except `test_infrastructure_ready` (needs the S3 test credentials); migration safety (0019 → 0020), schema drift, catalog, index and decision checks pass; contract regenerated (46 tables, 154 foreign keys, 250 ACL entries).

Open:
- D-8 cost cap and the model choice wait for the probe numbers; `image_tokens_estimate` should be set from them.
- Gemini's live acceptance of the vision schema and the `box_2d` order are unproven until the probe runs.
- AC-6: crop re-read, trust levels and when a vision value may confirm (D-2); AC-7: PO/GRN corrections and box highlights on the PDF.
- The core `AIHook` enum mirrors only the `0001` labels (as for `vendor_name_status`).

## D38 — AC-5b vision follow-up: line rows, failure reasons, handwritten-reference recall (7 Oct 2026)

**What the first owner runs showed (counts only).** Probe: the live API accepts the vision body; about 1,100 input tokens per page at `high`. Bench on `gemini-3.5-flash-lite`: vision-alone header 141/166 (84.9%), OCR × vision `agree_wrong` 0, but lines 1/77: the model returned line rows without description or amount (63 rows dropped as `empty_line`), so the bench could not match them. 3 calls failed `INVALID_REQUEST` and 9 `invalid_output` (interaction not `completed`); the bench could not say why, because the app adapter keeps provider messages out on purpose (D15). On `gemini-3.8-flash` nothing was read: 28 `rate_limited`, 8 `PROVIDER_UNAVAILABLE`, 3 `timeout`. Vision values stay suggestions only (D-2 is AC-6's); hard metrics are untouched (vision feeds no vendor, link or case).

| Decision | Choice |
|---|---|
| Schema `vision.schema.v2` | `description` is a **required** key of every line row (provider schema and the strict `VisionLineItem`); a row without the key rejects the answer. An empty string is allowed for a row that has no printed description, so one blank row does not throw away the whole reading |
| Prompt `vision.prompt.v2` | Rule 6: copy each row's description, amount and quantity (on a GRN the received/accepted/rejected quantity) as printed in that row, plus rate, HSN/SAC and unit when printed; never take a value from another row or the totals; leave out blank cells instead of empty strings; list each row once and stop after the last item row |
| Core check | A line row is kept when it has a description, an amount or any quantity (a GRN row has no amount); otherwise `empty_line` as before. Rules hash updated; no engine rule path changed |
| Consent | The vision consent fingerprint includes the schema and prompt versions, so a workspace that ticked "Read page images" must consent again after the rebuild; with vision off the fingerprint is unchanged (tested) |
| Limits | `h6_vision.image_tokens_estimate` 1,300 → **1,200** (probe ≈ 1,100). `max_output_tokens` stays 16,384 until the next bench shows whether answers are cut by the cap (rule below) |
| Failure reasons (bench, probe) | New `scripts/vision_diagnostics.py`: an inner transport under the app's single-attempt guard reads the one response and passes it on unchanged (the adapter behaves as in the app). It keeps Google's `error.status`/`error.code`/`error.message` for HTTP errors (the request holds only the prompt and masked images, never document text; the message is still cut to 300 characters, long digit runs and key-shaped strings replaced), and for a finished interaction its `status`, input/output/thought tokens and, when not `completed`, counts from the partial answer: rows and distinct rows (a repeating model), never the text. The bench report gets a `failures` list (grouped, with `at_output_cap` and `loop_suspected`); the probe shows the same and no longer sends a failed body a second time |
| Output cap rule | A line row with its box costs on the order of 100–150 output tokens, so 16,384 holds 100+ rows. From the bench's `failures` and `output_tokens_per_call`: (a) incomplete **at** the cap with `loop_suspected` → the model repeats rows; a larger cap only costs more (prompt v2 asks for each row once; otherwise a different model); (b) incomplete at the cap with distinct rows ≈ rows, or any answer `over_16384` in a run with a larger cap → genuinely long: raise `h6_vision.max_output_tokens` to 32,768 (only the reservation grows; billing and the day/month budgets use the tokens actually used); (c) incomplete well below the cap → read `status`/`errors`. Pages-per-call (several requests per version) is a runner change and is kept for the case where one call cannot hold one document even at 32,768 |
| Bench options | `--vision-max-output-tokens N` (default: the app limit) to separate (a) from (b) in one run; `--vision-pause S` between calls for low per-minute quotas; new counts `line_cells` (rows with description / amount / quantity / rate), `handwritten_refs` (typed / other annotation / printed field / missed), `output_tokens_per_call` (max, over 4k/8k/16k) |
| Handwritten refs | Bug fixed: truth v2 keeps `annotation_refs` and `marketplace_order_no` beside the header, but the vision block looked inside the header, so handwritten recall was always 0/0 and the marketplace list was compared as one string. `TruthDocument` now carries both. Matching folds to letters and digits with zeros kept (the document-number canonical form drops them: a handwritten "04-077" must be found in "PO/26-27/04-077" (synthetic example)); a partial match needs at least 5 characters on the shorter side |
| Truth (local only) | The owner's 2026-10-06 handwritten references were added to `work/accuracy-truth/truth.json` as `annotation_refs` for three more invoices (one with a PO and a GRN ref); with the two already there, 5 truth invoices carry handwritten refs (18 checks over their copies). Values stay in `work/` and memory, never in the repo |

Checks: unit + parser + scenario suites 678 passed; the vision tests (83 with the bench tests) cover description required / empty allowed, the GRN quantity-only row, the consent fingerprint with vision on and off, Google's error text kept without the key or long digit runs, an incomplete answer reported as status + tokens + loop counts with no row text, the truth loader, partial-ref matching and the output-token spread. Golden self-check, core purity, rules hash (updated), `ruff check`, `mypy --strict` (core and the five touched AI modules) pass. Not run here: the integration suite and `make accuracy-bench` (Docker Desktop was not running); the integration assertion now expects `vision.schema.v2`. Because the truth file changed, the main `report_hash` changes through `meta.truth_sha256` only; no rules reading, vendor, link or case changes.

Owner steps: `MSYS_NO_PATHCONV=1 BENCH_GEMINI_KEY=… RB_BENCH_VISION_MODEL=gemini-3.5-flash-lite make accuracy-bench-vision BENCH_ARGS="--max-provider-pages 120 --vision-max-output-tokens 32768 --out work/accuracy-reports/after-ac5b.json"` (every vision answer is re-sent once: the v2 prompt and schema change every cache key). For the app: `docker compose up -d --build`, then consent again if page images are ticked.

Open:
- D-8 cost cap: proposal after this bench run, from measured tokens per document and the owner's price and monthly volume.
- The output cap (rule above) and the `INVALID_REQUEST` cause: from the bench's `failures` block.
- `gemini-3.8-flash` needs a quota that allows the run (`--vision-pause`), or stays out of scope.

## D39 — Free-tier Gemini key for vision **testing** (owner decision, 8 Oct 2026)

The owner has only Gemini free-tier keys. Owner decision: masked page images of the real sample documents may be sent on the **free tier for testing** (bench and probe). This narrows D-1, which assumed the paid tier with `store=false`: on the free tier Google's terms allow submitted content to be used to improve its products. Production use of page images on the free tier is **not** decided; it comes back with D-8.

AC-5b bench results so far (counts only): `gemini-3.5-flash-lite` read 15 of 39 documents; 16 answers ran to the 32,768-token cap with no line rows (degenerate output, not long documents, so the cap stays 16,384), 2 were blocked by Google's recitation filter (HTTP 400), 9 failed strict parsing (mostly `uom` longer than 40 characters); vision-alone header 76/100, lines 1/43 (amount present in only 9 of 46 rows), handwritten refs 4/5, marketplace orders 4/4. `gemini-3.8-flash` read nothing: free-tier quota (20 requests per day) and HTTP 503 "high demand". The bench got `--max-provider-calls N` (most informative documents first: handwritten-ref truth, then truth lines) and no longer sends a second copy with the same request key.

## D40 — Vision schema v3: amount and quantity required, scored cells first (8 Oct 2026)

A free-tier `gemini-3.8-flash` run read 3 requests (6 documents with copies): header 35/46, OCR × vision agreement on a wrong value 0, handwritten refs 3/6, but again no line amount (0 of 6 rows); 18 calls got HTTP 503 "high demand". A counts-only look at all recorded answers showed the cause is the contract, not one model: across 109 flash-lite rows, `uom` was filled in 96 (more than `description`, 53) while `amount` was filled in 10, and over-long `uom` was the commonest strict-parse failure. Optional cells were skipped and other columns pushed into the unit cell.

`vision.schema.v3` / `vision.prompt.v3`: `quantity` and `amount` are required keys of every line row like `description` (an empty string when the row prints none); line properties are ordered description, quantity, rate, amount, uom, then the rest, because the model writes keys in schema order; the prompt names the amount column (Amount, Total, Value, Taxable Value) and says `uom` is only the unit word. A `uom` longer than 500 characters no longer rejects the whole answer; the core check drops a unit cell longer than 20 characters as `bad_cell`. Consent with page images on must be given again (versions are in the fingerprint).

Second review before the owner's last free-tier quota (still v3, nothing was sent with it yet): the amount is the row's value **before tax** (a GST row can print both Taxable Value and a tax-inclusive Total; the truth and the quantity × rate check use the former); each field name at most once except references and `other_charge` (22 duplicate fields were dropped in the 3.8-flash run, and the degenerate answers never reached `lines`); `box_2d` is exactly four integers (the array has no length bound because Gemini rejects `maxItems`); label and title short. Payload 5,875 of 16,000 bytes. Bench: the diagnostics now say where a cut answer ran away (last section, objects vs distinct objects, longest integer array, longest string, unterminated tail; counts only); `send` stops early when the rest cannot succeed (daily quota, bad key, quota exhausted, the same non-recitation `INVALID_REQUEST` twice, five provider-unavailable/timeouts in a row), waits 60 s after a per-minute limit and 30 s after a 503, and missing documents are always sent most informative first. Offline Docker checks (`--network none`): the cached run reproduces the main block and the send path stops after five connection failures. Rules hash updated; unit + parser + scenario suites 681 passed, ruff and mypy clean.

## D41 — Vision v3 result on `gemini-3.5-flash-lite` (free tier) and a lenient box parse (8 Oct 2026)

One key's project returned HTTP 403 "project has been denied access" (an account-level block by Google, not a request error); the D40 guard stopped after that one call. With the owner's other key: 29 requests (39 documents with copies), no provider failure, no degenerate answer (largest answer 4,399 output tokens; before D40, 16 of 39 ran to the cap). Two answers had a line `box_2d` longer than four integers, which rejected the whole answer; the strict model now accepts 1–16 integers and the core check drops only that item (`bad_box`, 8 items). The provider schema is unchanged, so the recorded answers were re-checked offline: `work/accuracy-reports/after-d41-lite.json`.

Vision alone (counts, suggestions only): 39/39 documents read; header 271/313 (86.6%, was 76.0%); lines 80/123 (65.0%, was 1/43; every row now has description and amount); handwritten refs 6/18 typed + 1 as another annotation (weak: hints only, AC-9); marketplace orders 5/5. OCR × vision over scored header fields: agree-correct 211, **agree-wrong 1**, vision right where OCR wrong or empty 60 (19 + 41), OCR right where vision wrong 5, both wrong 2. Tokens per document ≈ 2,630 input and 2,080 output (sums over documents, copies counted). Main report and hard metrics unchanged (0/0/0/0). The one agree-wrong field means two readers agreeing is not proof on its own: AC-6 must keep the deterministic validators and crop re-read in front of any automatic confirmation (D-2).

## D42 — D-2 decided: when a machine-read value may be verified without a person (8 Oct 2026)

The owner asked for a recommendation and delegated the choice. **Decision: yes, but only under all of the following, per field; otherwise the value stays a suggestion and a person verifies it.**

1. **Independent agreement.** Two independent readers give the same normalised value: OCR/text layer, signed e-invoice QR, XML, or the vision reader. Two answers from the same model (or a re-ask of it) never count as two readers. A verified signed QR value is L0 on its own.
2. **Every deterministic validator passes** for that field and the document: GSTIN checksum and the buyer-entity guard, GST state/slab and tax type, totals and line arithmetic, reference format (`accept_doc_no/po`), date sanity.
3. **The crop re-read agrees.** A local OCR re-read of the reader's box gives the same value (AC-6); a box in a mask, off the page or empty fails.
4. **Never automatic:** handwritten or stamped marks (annotations), new vendor names, any field after a person's correction (the person's value wins), and anything on a page-split document not yet confirmed (D-9).
5. **Gate by evidence.** Automatic confirmation is switched on per field type only after the accuracy bench shows 0 false-safe and 0 agree-wrong-confirmed for that field type on the truth set; it ships behind a workspace setting that is off by default. The 1 agree-wrong field in D41 is exactly the case rules 2–3 must catch.

This replaces the "suggestion only" rule of D11/D21 for fields that meet all five rules (D18 → D18b in AC-6).

## D44 — AC-6a consensus core and shadow bench (8 Oct 2026)

AC-6a adds a pure per-field consensus engine and a shadow benchmark. It classifies each reading without replacing extracted values; automatic confirmation and reopening remain caller-controlled. The shadow policy measured the upper bound with crop checks disabled, while D-2 still requires a matching local crop reread before any later confirmation.

| Trust level | Rule |
|---|---|
| L0 | Manual or structured values, and signed QR values, are authoritative. |
| L1 | Existing verified rule values, or two independent agreeing readers after deterministic validators pass (and crop agreement when required). |
| L2 | Vision-only values, a single reader, fields excluded from automatic confirmation, or a failed/missing crop or non-hard validator. |
| L3 | Reader disagreement, or vision disagreeing with an already verified field; reopening remains policy-controlled. |
| Rejected | A hard validator fails, including GSTIN checksum, buyer-entity or GST-type guards. |

Deterministic validators: `doc_no_shape`, `lookalike`, `date_window`, `gstin_checksum`, `buyer_entity`, `gst_type`, `gst_structure`, `totals_arithmetic`, `non_negative`, `reference_shape`, `line_arithmetic`, `positive`, and `split_unconfirmed`. Never-automatic fields stay excluded. The cache link finding B3 is resolved as designed: `ai_request.ai_cache_id` marks a cache hit; a fresh answer is found through the workspace-scoped `cache_key` shared with `ai_cache`.

Bench (`before-ac6b.json`, the AC-6a shadow report `31cb1ec49644`):

| Field type | L1 | Correct | Wrong | Gate |
|---|---:|---:|---:|---|
| amount | 79 | 0 | 0 | no_evidence |
| doc_date | 29 | 2 | 0 | pass |
| doc_no | 25 | 1 | 0 | pass |
| gstin | 62 | 2 | 0 | pass |
| line_amount | 86 | 5 | 0 | pass |
| line_quantity | 63 | 0 | 0 | no_evidence |
| line_rate | 63 | 0 | 0 | no_evidence |
| reference | 30 | 2 | 0 | pass |

Reopened fields: 13 total, 1 true OCR-wrong catch and 12 OCR-right false alarms; shadow false-safe: 0. The hard metrics were all zero. The main report hash remained `31cb1ec49644`; the consensus block is counts and labels only.

Checks: the AC-6a offline suite and static checks passed; shadow bench passed with 35 cache hits. The AC-6b session reran the combined offline suite (721 passed) and the final benchmark twice; see D45 for the complete app and migration checks.

Open:
- AC-6c still needs the workspace setting and verify-loop behavior before any field type can be automatically confirmed.

## D45 — AC-6b crop reread, provenance and dark consensus job (8 Oct 2026)

AC-6b queues a dark consensus job only after a vision answer is accepted. The job rereads only the masked page crops in the isolated local OCR process, stores per-field reader/crop/validator provenance in an append-only decision table, and records a run summary. It does not write document values, mark fields verified or reopen them; transient page bytes and the cached answer remain available only until the run finishes or fails, then are purged.

| Decision | Choice |
|---|---|
| Consensus activation | Follow-up job on the parse queue; use the stored answer by `cache_key`, with no new provider call. |
| Crop check | Missing, masked, empty, unavailable and disagreeing rereads cannot satisfy D-2; a crop is local and runs in the PDF sandbox child. |
| Application | `auto_verify=False`, `reopen=False`; provenance is recorded without changing the document/version. |
| Provenance | `consensus_run` tracks queued/succeeded/stale/failed status; `consensus_decision` is append-only and inaccessible to the report role. |

Bench (`before-ac6b.json` → `after-ac6b.json`, report `3fe1aca30bf7`; repeat `after-ac6b-repeat.json` matched):

| Measure | Before | After |
|---|---:|---:|
| False-safe / false auto-links / false Matched / wrong vendors | 0 / 0 / 0 / 0 | 0 / 0 / 0 / 0 |
| Consensus hash | `b69f7a772db5` | `b69f7a772db5` |
| Vision hash | `f57033ecdc94` | `f57033ecdc94` |
| Shadow false-safe | 0 | 0 |
| Reopened (true catches / false alarms) | 1 / 12 | 1 / 12 |

All field-type consensus counts and gates matched the AC-6a shadow report. Main extraction, document, vision and consensus sections are identical to the before report; the main report hash changed because the crop reread additions changed only the reader-source fingerprint in `meta` (the cold run read 35 files, and the repeat used all 35 cache entries). The hard gate passed.

Migration `0021_consensus` adds 2 tables, one append-only trigger, and the required workspace-first indexes. Schema contract checks report 48 application tables, 38 enums, 25 triggers, 161 foreign keys, 256 explicit ACL entries and 7 default privileges; the queued schema has 48 application tables plus 4 Procrastinate tables.

Checks: offline unit/property/scenario/parser suites 721 passed; AI vision and AI runner integration 36 passed; migration acceptance 3 passed; migration-safety clone 0020 → 0021, Alembic drift, catalog, indexes, decisions, seeding, queue schema and table-count checks passed; ruff, format, mypy, import contracts, core purity, rules hash, engine version and golden self-check passed. No provider calls. Disposable PostgreSQL containers were stopped after the checks.

Follow-up:
- AC-6c's opt-in verification setting and targeted verify loop are completed in D46; the workspace toggle still defaults off.

## D46 — AC-6c gated verify loop and workspace consent (8 Oct 2026)

AC-6c adds an opt-in workspace allowlist for the five field types whose D42 benchmark gates pass, then applies only consensus outcomes that also pass independent-reader, validator, crop, hand-mark and current-consent checks. Eligible changes append a same-value derived document version with evidence provenance and link it to the consensus run; stale consent cannot apply, and field values are never edited. The masked-crop reread allows at most two rounds in the isolated local OCR child, while empty selection preserves the legacy consent fingerprint byte-for-byte.

| Decision | Choice |
|---|---|
| Field allowlist | `doc_date`, `doc_no`, `gstin`, `line_amount`, `reference`; empty by default |
| Consent | Existing `consent.v1` text/hash when off; selection uses field-specific `consent.v2` and must match at commit |
| Applied version | Append a derived immutable version with unchanged values; retain evidence and source provenance; no page-image bytes |
| Schema | Migration `0022_auto_verify` adds the allowlist and consensus result-version link; implementation-plan deviation because no existing persistence slot represented either |

Offline benchmark (`after-ac6c.json`, report `baf82b8de05a`; 35 files, 51 parsed documents; hard gate passed, all four hard counters 0):

| D42 field type | L1 candidates | Correct | Wrong | Gate |
|---|---:|---:|---:|---|
| `doc_date` | 29 | 2 | 0 | pass |
| `doc_no` | 25 | 1 | 0 | pass |
| `gstin` | 62 | 2 | 0 | pass |
| `line_amount` | 86 | 5 | 0 | pass |
| `reference` | 30 | 2 | 0 | pass |

Checks: offline unit/property/scenario/parser suites 724 passed; integration groups 45 + 3 + 22 + 76 + 9 passed; migration safety cloned `0021` and upgraded to `0022`; fresh upgrade, Alembic drift, catalog, indexes, decisions, seed, queue schema and table-count checks passed (48 application tables, 162 foreign keys; queued count 48 + 4); Ruff, format, strict mypy, import contracts, core purity, rules hash, engine version, golden self-check, OpenAPI generation, web typecheck/lint and 30 web tests passed. No provider calls. Not run: browser E2E, owner-only provider benchmark, authentication tests requiring a separate cluster, and infrastructure-ready tests requiring S3 credentials.

Next: AC-7a verify-screen backend.

## D47 — AC-6 re-audit: failure-directed local crop evidence (8 Oct 2026)

The requested AC-6 re-audit found that `crop_needs` only selected D42 candidates missing a crop check. That skipped targeted rereads for arithmetic/hard-validator failures and reader disagreements (which return before validator checks). It also used line OCR mode for line-cell boxes, allowing a short row serial to be parsed as the field value. The selection now includes those failed checks with a checked vision box, and their crop plus each attempt is persisted as evidence. A crop cannot clear a failed validator or a reader conflict, so neither path becomes L1/auto-verified from the reread alone. Line-cell boxes use block parsing, which ignores a leading short integer; summary-total boxes retain block mode.

Provider scope: the owner reaffirmed **D45 local-only**. Consensus reuses the accepted, stored vision answer and does not issue an additional vision-provider crop call. `docs/accuracy-plan.md` §3.3 now records this phase boundary. This keeps each additional read within the isolated local OCR path and existing workspace page-image consent.

Checks: focused consensus crop/core tests 40 passed; full offline unit/property/scenario/parser suite 727 passed; AC-5/AC-6 vision integration tests 13 passed on a fresh disposable PostgreSQL database migrated through `0022`; Ruff, format, strict core mypy (36 files), import contracts, core purity, rules hash, engine-version and golden self-check passed. No migration or API change was made. No live provider call or real document was used.

Offline bench ran twice with `--network none`: both report hashes `baf82b8de05a5e0d467ec827f53d076ac22de72d783621388dc6908da6b7bc33`, vision hashes `f57033ecdc9493b655f5e1f7f493c8f091d0f2f102abfddde0b775e92399c80e`, and consensus hashes `b69f7a772db5779a78ae49e4a35bdc5231d7fdbad8f9b6d81d2040e3792df184` matched. Hard gate passed; false-safe, false-auto-link, false-matched and wrong-vendor counts were all 0. The benchmark's upper-bound shadow block does not require crops, so its consensus hash is expected to remain unchanged by the application reread fix.

## D48 — AC-7a backend: invoice correction refresh (8 Oct 2026)

AC-7a has no endpoint-by-endpoint contract in the implementation plan, so this session implemented only its explicit B8 backend scope. Any changed invoice header, line, or line removal now queues the existing `resolve_vendors` job in the correction transaction; that job continues through the existing vendor-resolution and link-mapping chain even when vendor name and GSTIN did not change. The endpoint contract and schema are unchanged.

| Decision | Choice |
|---|---|
| AC-7a scope | Fix B8 for existing invoice corrections only; do not add undocumented endpoints or PO/GRN correction behavior. |
| Existing backend surfaces | Document details expose the current version, verification metadata and field evidence; existing raw-file and AI-suggestion routes provide source/PDF evidence; split confirmation and vendor assignment already have routes. Keep those contracts as-is. |
| PO/GRN corrections | Remain open pending Product decision D13/D21. D21 says follow-up equivalence for vendor resolution, `link_vendor`, `recompute_po`, and dirty scopes cannot yet be claimed. |
| Provider calls | None; `RB_AI_EXTERNAL_CALLS=disabled`. D45's local-only verify-loop boundary is unchanged. |

Bench (`after-ac6c.json` → `after-ac7a.json`, report `baf82b8de05a`; same report hash as the D47 baseline):

| Measure | Before | After |
|---|---:|---:|
| Parsed / missed / spurious documents | 51 / 0 / 0 | 51 / 0 / 0 |
| Header correct; lines found / expected; auto-verified | 302/373; 96/141; 18/51 | 302/373; 96/141; 18/51 |
| Vision cached documents / pages | 39 / 67 | 39 / 67 |
| **False-safe / false auto-links / false Matched / wrong vendors** | 0 / 0 / 0 / 0 | 0 / 0 / 0 / 0 |

The offline bench used `--network none`; reader cache hits/read files were 35/0 and the gate passed. It emitted only aggregate counts and labels.

Checks: full offline unit/property/scenario/parser suite 727 passed; `test_ingestion.py` 2, `test_ai_h1.py` 14, `test_ai_vision.py` 13, `test_page_split.py` 3 and `test_ac4_vendor_hygiene.py` 6 passed on disposable PostgreSQL at port 15499, migrated through `0022`; Ruff, format, strict core mypy (36 files), import contracts, core purity, rules hash, engine version, golden self-check and `git diff --check` passed. `ingestion.py` strict mypy remains at its recorded baseline of 33 errors. No migration or API contract change was made.

Not run: migration-safety checks (no migration); OpenAPI generation and web checks/E2E (no contract or UI change; AC-7b); authentication and infrastructure-ready tests (separate cluster / S3 credentials, unrelated to this correction path). No provider test was run.

Open:
- PO/GRN correction behavior still needs Product's D13/D21 decision. D21 specifically leaves editor/correction support open because the corresponding re-resolution and dirty-scope behavior has not been decided or proven.
- No additional AC-7-specific backend route contract is written for field selection/crop highlighting or local truth export; define that contract before changing backend behavior. AC-7b UI remains unstarted, and this slice does not establish the complete verify-screen acceptance criteria.

## D49 — PO/GRN manual correction and save (8 Oct 2026)

The owner decided PO and GRN documents must have an edit-and-save option. This resolves D48's open Product decision for manual correction support only. It does not change D13/D21's H1 eligibility or PO/GRN extraction allowlists; vision readings remain suggestions until a person saves a correction.

| Decision | Choice |
|---|---|
| Route | Extend the existing `PUT /documents/{id}/correction`; keep If-Match and append-only `document_version` behavior. The separate manual-invoice route retains its invoice-only request body. |
| Editable headers | Shared `DocumentHeader` values; PO adds `revision`, `status`, `ship_to`, `payment_terms`, and `delivery_date`; GRN adds `po_refs`, `challan_no`, and `supplier_invoice_no`. The allowlists follow the canonical header models and persisted columns. |
| Editable lines | Type-specific canonical `DocLine` values. Internal normalized columns and `extras` are not correction inputs. Changed values receive manual field evidence and an audit record. Existing invoice arithmetic QA remains invoice-only. |
| Follow-up | Changed corrections queue the existing vendor-resolution/link-mapping chain. A PO correction increments its mapping input sequence and marks its PO ledger scope dirty for recompute. A GRN correction marks currently linked deterministic PO scopes dirty; the existing mapping job handles changed links, pending recomputes, and GRN rechecks. |
| AI boundary | No new provider call or H1 wiring. D45's local-only verify-loop decision remains unchanged; `RB_AI_EXTERNAL_CALLS=disabled`. |
| Remaining AC-7a scope | The high-level plan mentions exporting corrections into local truth, but no backend endpoint, format, destination, or inclusion contract is written. AC-7a stays partial until that contract is defined. AC-7b remains unstarted. |

Synthetic integration: `test_ingestion.py` 3 passed and `test_ai_vision.py` 13 passed after the final correction-body split; the related integration batch (`test_ingestion.py`, `test_ai_h1.py`, `test_ai_vision.py`, `test_page_split.py`, `test_ac4_vendor_hygiene.py`) passed 39 tests after updating the PO/GRN `correctable` expectation. Full offline unit/property/scenario/parser suite: 727 passed. No provider call or real document was used in tests.

Offline benchmark (`--network none`, `after-ac7a.json`): report hash `baf82b8de05a`; parsed/missed/spurious 51/0/0; hard metrics false-safe/false-auto-link/false-matched/wrong-vendor 0/0/0/0; gate passed; 35 cache hits and 0 source files read. The cached vision block had 39 missing readings after the code fingerprint changed; nothing was sent to a provider. Only aggregate counts and labels were inspected.

Static checks: Ruff and formatting passed; strict core mypy passed for 36 files; import contracts, core purity, rules hash, engine version, golden self-check, OpenAPI export, generated API bindings, web typecheck, and `git diff --check` passed. Strict mypy on the edited app modules still reports the existing 33 `ingestion.py` errors and one existing API upload stream typing error; no new correction-field error was reported. No migration was added.

Not run: migration-safety checks (no migration), UI tests/E2E (AC-7b is out of scope), authentication tests (separate database cluster), and infrastructure-ready tests (S3 credentials). The disposable PostgreSQL integration cluster used only port 15499 and was stopped after checks.

## D50 — AC-7 verify workspace and local truth correction export (8 Oct 2026)

The owner asked to complete the remaining AC-7 work after deciding PO/GRN edit-and-save in D49. The plan states the verify-screen behavior at a high level but gives no endpoint-by-endpoint contract for local truth export. This entry records the narrow implementation contract used to complete that planned path; it does not extend PO/GRN H1 eligibility or authorize new provider behavior.

| Decision | Choice |
|---|---|
| Review flow | The preview opens on documents needing verification and uses persisted review fields or non-manual evidence to choose visible fields. “Show all fields” remains available. Saved verified documents leave the pending list. |
| Source evidence | A cited field action selects its evidence page; the PDF viewer moves to that page and overlays a bounded box for stored PDF-point or normalized vision coordinates. Missing page evidence is called out. |
| Conflicts and keyboard use | Current and suggested values have separate actions. Choosing a suggestion only changes the draft; the existing versioned correction save persists it. Controls remain keyboard-focusable. |
| Split and vendor-pending | Existing batch page-split confirmation remains the split surface. Unassigned documents link to the existing mapping flow. No new split or vendor API was added. |
| Export patch | The browser downloads `reconbench-accuracy-corrections/v1`. It includes only saved, verified, manually evidenced values supported by the benchmark contract: the 11 scored header fields and description/quantity/rate/amount line fields. AI suggestions without a saved manual correction are not exported. |
| Truth mapping | `scripts/merge_accuracy_corrections.py` requires an explicit application-document ID → Truth v2 document ID mapping. For line fields, each application line number must also map to a 1-based Truth line-array position, or explicitly to `append`; no line correspondence is guessed. |
| Truth destination | The merger validates document type and allowed fields, rejects missing/ambiguous mappings, and writes a new output path. It refuses an output equal to the source or an already-existing output. The source Truth v2 file is left unchanged. |
| API/schema | No new route, database field, or migration was needed. The existing correction endpoint remains the save path. OpenAPI, generated bindings, and published result schemas regenerate byte-identically. |
| AI boundary | D45's local-only decision remains. `RB_AI_EXTERNAL_CALLS=disabled`; no provider call is made by the verify UI, patch merger, tests, or offline benchmark. |

The mapping JSON shape is `{"<app-document-id>": {"truth_document_id": "<truth-document-id>", "lines": {"1": 1, "2": "append"}}}`. Line-map values are 1-based positions in the selected Truth document's `lines` array, or the literal `append` for a new line. Run the merger with `--truth <existing-truth-v2.json> --corrections <downloaded-patch.json> --mapping <explicit-mapping.json> --output <new-truth-v2.json>`; the output path must not already exist.

The local export is a field-value correction patch. It does not encode deleted Truth lines; those still need a deliberate Truth edit. The plan's “under 30 seconds” owner usability target was not human-timed in this synthetic-only session, so this implementation does not claim that measurement.

Checks: the synthetic merger suite passes 9/9; full offline unit/property/scenario/parser suite 736 passed; relevant integration batch (`test_ingestion.py`, `test_ai_h1.py`, `test_ai_vision.py`, `test_page_split.py`, `test_ac4_vendor_hygiene.py`) 39 passed on a disposable PostgreSQL instance bound only to `127.0.0.1:15499`, which was stopped after the run. Web typecheck, lint/money-safety, full Vitest 37/37, and production build passed; Playwright 13/13 passed with mocked APIs. Ruff, formatting for the new Python files, strict core mypy (36 files), import contracts (3 kept), core purity, rules hash, engine-version check, synthetic golden self-check, OpenAPI/schema generation, and `git diff --check` passed.

Offline benchmark (`--network none`, report `after-ac7-20261008-final-review.json`): parsed/missed/spurious 51/0/0; hard counters false-safe/false-auto-link/false-matched/wrong-vendor 0/0/0/0; gate passed; cache hits/files read 35/0. Only aggregate counts and labels were read from the report.

Not run: migration-safety checks because AC-7 adds no migration; provider-specific tests because provider calls are prohibited here; authentication tests (separate database cluster) and infrastructure-ready tests (S3 credentials). The plan's human timing target remains owner validation. The build reports its existing large PDF-related chunk warning; the build itself passes.

## D51 — AC-8a core identity and duplicate rules, plus aged fix (8 Oct 2026)

Core duplicate suspicion now uses invoice grand total within ₹1 and the configured date window (`duplicate_suspect_days`, default 3), followed by either an invoice number equal after look-alike folding and existing canonicalization, or an identical complete multiset of line amounts. The supplier/vendor anchor is not required, so the fingerprint can still catch a copy with a misread vendor; a suspected duplicate remains in Review and continues consuming balance. An aged `GRN_PENDING` case is promoted to `exception` even when another review reason would otherwise leave it in `needs_review`, satisfying `recon_case_aged_ck` while retaining its reason codes. Exact duplicate blocking remains unchanged.

| Decision | Choice |
|---|---|
| Line fingerprint | Compare sorted, complete line-amount multisets; amount uses the printed amount or quantity × rate. Incomplete or empty line sets do not provide this signal. |
| Date window | Reuse `duplicate_suspect_days` (default 3); do not introduce a second window. The prior edit-distance setting remains in the config contract but does not control this fingerprint rule. |
| App identity and re-scan policy | Deferred to AC-8b. No page hashes, migration, or overwrite/conflict behavior was added; D19's open lower-quality re-scan policy remains unresolved. |
| Aged `GRN_PENDING` | Any such case with `aged=true` has status `exception`; existing reason codes remain available for review context. |

Offline benchmark (`--network none`, `before-ac8a-session-20261008.json` → `after-ac8a-session-20261008.json`, report `5d86b53363eb`):

| Measure | Before | After |
|---|---:|---:|
| Parsed / missed / spurious | 51 / 0 / 0 | 51 / 0 / 0 |
| System documents / extra / wrong merges | 28 / 2 / 0 | 28 / 2 / 0 |
| Duplicates caught / missed / false | 0 / 2 / 0 | 0 / 2 / 0 |
| **False-safe / false auto-links / false Matched / wrong vendors** | **0 / 0 / 0 / 0** | **0 / 0 / 0 / 0** |

The hard gate passed. Benchmark outcomes are unchanged; engine version is `0.1.5`. The version change caused 0 cache hits and 35 source files read. Only aggregate counts and labels were used; no provider calls ran.

Checks: focused AC-8a unit/property tests 35 passed; final unit/property/scenario/parser suite 738 passed in 86.17s; Ruff clean, changed Python files formatted, strict core mypy 36 files with no issues, import contracts 3 kept / 0 broken, core purity passed, rules hash `b39481831e08c92eaab1f3934ba27316f677f6d0b4dc943ccec4d998e709183c` verified, engine version check passed (`0.1.4` → `0.1.5`), and golden self-check passed (0 stored cases; synthetic harness check passed). No migration safety or PostgreSQL integration checks were run because this slice changes only pure core and synthetic tests; no web checks or E2E were run because app/API/UI code is unchanged. Provider tests were not run because provider calls are prohibited.

Open:
- The offline benchmark still reports 2 missed duplicate copies (0 caught, 0 false). AC-8b remains not started; re-evaluate the end-to-end identity/duplicate metric after app identity integration.
- D19's re-scan overwrite/conflict decision remains open and is not resolved by AC-8a.

## D52 — AC-8b exact document identity and re-scan guard (8 Oct 2026)

The owner approved an exact re-scan rule: a match requires identical ordered 64-bit page dHashes and an identical content fingerprint (document type, date, total, complete sorted line-amount multiset, and supplier PAN only when verified); if that pair cannot be built, only exact source SHA plus source-page locator matches. D19's proposal is adopted: compare the minimum confidence among verified fields, persist a lower-confidence parse as an alternative version, and leave the current pointer unchanged. Migration `0023_document_identity` persists this evidence per immutable version and backfills prior versions with source SHA and locator only; no image bytes or extracted page text are stored.

| Decision | Choice |
|---|---|
| Verified natural key | Use verified document number, date and supplier identity for the document key; it does not bypass the exact-evidence rescan match. On key collision with non-matching evidence, create a disambiguated key rather than merging. |
| Re-scan match | Exact ordered page dHashes plus exact content fingerprint. If either cannot be built, fall back to exact file SHA plus source-page locator. |
| Content fingerprint | Document type, date, grand total and complete sorted line-amount multiset; derive a line amount from printed amount or quantity × rate. Include supplier PAN only when its GSTIN is verified. |
| D19 current-version guard | Score is the minimum confidence among verified fields. A lower score or no verified fields is stored as an alternative and cannot replace the current version; equal or higher can become current. |
| Page hashes | Render in the bounded PDF child and retain only the 64-bit dHash per page in page order. Parser version is `phase4-parser-5`. |
| AC-8 status | Implementation and migration are in place, but phase remains open because the benchmark's zero-extra-document target is not met. AC-9 remains not started. |

Offline benchmark (`--network none`, `after-ac8b-20261008.json`, report `1623bc6dcae4`; engine `0.1.5`): files read / parsed / missed / spurious `35 / 51 / 0 / 0`; system documents / extra / wrong merges `42 / 16 / 0`; duplicates caught / missed / false `8 / 2 / 0`; hard metrics false-safe / false-auto-links / false-Matched / wrong vendors `0 / 0 / 0 / 0`; gate passed. Compared with D51's `28 / 2 / 0` identity counts and `0 / 2 / 0` duplicate counts, the exact rule avoids false merges but leaves 16 extra documents. Only aggregate counts and labels were read from the report.

Checks: offline unit/property/scenario/parser suite 745 passed in 95.20s; document-identity unit tests 7 passed; AC-8b rescan integration 1 passed; Phase 4.11/4.13 head acceptance 3 passed; synthetic legacy identity backfill passed. The broader `test_ingestion.py` run had 3 passed and 1 failed at its existing order-invariance assertion: one synthetic case had `DUP_SUSPECTED` and the other did not; its AC-8a behavior and expectation were left unchanged. Migration safety cloned `0022` and upgraded to `0023`; fresh upgrade, Alembic drift, catalog, index, decision, seed, queue schema and table-count checks passed (49 application tables, 27 triggers, 164 foreign keys, 258 explicit ACL entries). Ruff passed; edited Python format check passed; strict core mypy passed (36 files); new `document_identity.py` has 0 strict errors; `ingestion.py` remains at its baseline 33 errors and `pdf_sandbox.py` at its baseline 9; import contracts 3 kept / 0 broken, core purity, rules hash `13e26fe7…`, engine-version check, golden self-check and `git diff --check` passed. No provider calls or real-document fixtures were used.

Not run: API/OpenAPI, web, UI E2E and other unrelated integration groups because AC-8b adds no API or UI contract; provider-specific tests because provider calls are prohibited; authentication and infrastructure-ready suites because they require a separate cluster and S3 credentials.

Open:
- The approved exact fingerprint rule leaves 16 extra documents in the benchmark (duplicates caught 8, missed 2, false 0). Keep AC-8b open and resolve this measured gap before treating its zero-extra-document target as met; do not weaken the approved rule implicitly.

## D53 — AC-8b exact-match follow-up (8 Oct 2026)

End-to-end review found that the database SHA+source-locator fallback could select an existing document of another type, while the deterministic rescan key includes document type. The app lookup now scopes all identity candidates to the parsed document type, with a synthetic regression test covering a type change at the same source SHA and locator. Synthetic integration coverage now also proves that the isolated PDF page hash reaches the immutable identity row. Two stale integration-test assumptions were corrected: a shell document is located through its current version's source file, and the ingestion-order comparison checks the shared financial fields plus the expected single `DUP_SUSPECTED` signal. The benchmark cache fingerprint now includes `document_identity.py`, the module that implements page dHash. The approved hash/content equality and D19 confidence rules are unchanged.

| Decision | Choice |
|---|---|
| SHA+locator fallback | Require the same workspace and document type in addition to exact source SHA and locator. |
| AC-8 closure | Keep AC-8 open until the benchmark reaches the written zero-extra-document target and duplicate misses are resolved. |

Offline benchmark (`after-ac8b-20261008.json` → `after-ac8b-followup-final-20261008.json`, report `9a3ada7f26ef`; 0 cache hits, all 35 files read in 185.2 seconds):

| Measure | Before | After |
|---|---:|---:|
| System documents / extra / wrong merges | 42 / 16 / 0 | 42 / 16 / 0 |
| Duplicates caught / missed / false | 8 / 2 / 0 | 8 / 2 / 0 |
| **False-safe / false auto-links / false Matched / wrong vendors** | 0 / 0 / 0 / 0 | 0 / 0 / 0 / 0 |

Checks: focused AC-8b PostgreSQL regression 1 passed (3 deselected); full `test_ingestion.py` 4 passed, including synthetic PDF hash persistence; offline unit/property/scenario/parser suite 745 passed in 97.41s; Ruff and formatting passed; migration 0023 upgrade, seed and queue schema passed on disposable port 15499. Strict app mypy reported 34 errors in `ingestion.py` (the plan records 33 as its baseline; none point into the changed identity-query lines). No real documents were used or opened; provider calls were disabled. The benchmark ran with `--network none`; only aggregate output was read.

Open:
- Benchmark identity remains 16 extra documents and duplicate detection misses 2; the plan's AC-8 exit criteria are unmet. Meeting zero extras may require improving how exact evidence is produced or an explicit owner decision to revise the approved exact-match policy. Do not silently relax it.

## D54 — AC-8 identity completion follow-up (8 Oct 2026)

At the owner's request to finish AC-8, a bounded recovery path was evaluated against the offline benchmark. The implementation now groups a re-scan when its complete content fingerprint is exact and each ordered page dHash is within 16 bits, or when document type, canonical document number, date and supplier identity all match as verified fields. Natural-key recovery runs only when the evidence query has no candidate; multiple evidence candidates remain ambiguous and are not merged. Incomplete evidence still requires exact source SHA plus locator. The D19 current-version guard is unchanged: lower-confidence parses remain alternatives.

The approved exact-dHash policy alone produced 42 system documents / 16 extra / 0 wrong merges and 8 duplicate copies caught / 2 missed / 0 false. A 4-bit bound did not change counts; 16 bits reduced this to 40 / 14 / 0; 32 bits added no further reduction. Adding exact fully verified natural-key recovery produced the best measured result: 34 system documents / 8 extra / 0 wrong merges, with extras by type `GRN 3 / PO 3 / invoice 2`; duplicate copies caught / missed / false were `0 / 2 / 0`. Hard metrics stayed `FALSE_SAFE 0 / FALSE_AUTO_LINKS 0 / FALSE_MATCHED 0 / WRONG_VENDORS_CREATED 0`; benchmark gate passed. This revises D52's exact-evidence-only rescan policy, while preserving its verified-field and D19 confidence guards.

Final offline run used `--network none`, report `after-ac8-verified-key-breakdown-20261008.json`, hash `fbd207468f84`; 35 files read, 51 parsed, 0 missed and 0 spurious. Only aggregate counts and labels were read. No real samples, truth contents, cache contents, images or cached readings were opened, and provider calls stayed disabled.

Checks: synthetic identity/benchmark unit tests 18 passed; full `test_ingestion.py` 4 passed on disposable PostgreSQL 16.15 at `127.0.0.1:15499`, with migration 0023, seed and queue schema setup successful; the container was stopped afterward. Offline unit/property/scenario/parser suite: 748 passed, with the single known B1 Hypothesis counterexample (`canonicalize_po_number("0/0")` is not idempotent) assigned to AC-9a; it was not changed. Ruff and touched-file formatting passed; strict core mypy passed (36 files); `ingestion.py` reported 33 pre-existing strict errors (baseline 33); import contracts 3 kept / 0 broken; core purity, rules hash, engine-version, synthetic golden self-check and `git diff --check` passed. No schema changed in this follow-up, so PG-SAFETY was not rerun. API, web, E2E, authentication, infrastructure-ready and provider-specific checks were not run because AC-8 changes no API/UI contract, those suites need separate services/credentials, or external calls are prohibited.

Open:
- The written AC-8 zero-extra-document target and duplicate-miss target remain unmet. The 8 extra documents have no unique fully verified identity under this policy, and the benchmark still has 2 missed duplicate copies. AC-8 remains open pending the owner's choice to accept and carry this measured gap to AC-11 or require further matching changes; AC-9 has not started.

## D55 — AC-8 final lookup-order and scoring audit (8 Oct 2026)

The final app audit found that bounded page-hash recovery could be tried before exact source SHA + locator recovery for legacy identity rows without captured page hashes. The app and synthetic benchmark now try exact content/page-hash equality first, exact same-type/source proof for legacy rows second, then bounded page-hash recovery. Benchmark scoring also counts `DUP_SUSPECTED` as a duplicate review flag, matching the AC-8 criterion that real duplicates be flagged; a synthetic regression test covers the score. No identity thresholds or D19 rules changed.

Offline benchmark (`after-ac8-source-order-20261008.json`, report hash `fbd207468f84`; 35 files, 51 parsed; `--network none`; only aggregate counts and labels read):

| Measure | Result |
|---|---:|
| System documents / extra / wrong merges | 34 / 8 / 0 |
| Extra documents by type | GRN 3 / PO 3 / invoice 2 |
| Duplicate copies caught / missed / false | 0 / 2 / 0 |
| False-safe / false auto-links / false Matched / wrong vendors | 0 / 0 / 0 / 0 |

Checks: focused synthetic unit/property suite 54 passed; full offline unit/property/scenario/parser suite 749 passed and 1 failed on the pre-existing B1 Hypothesis counterexample assigned to AC-9a (`canonicalize_po_number("0/0")`); synthetic PostgreSQL ingestion integration 4 passed on disposable port 15499. Ruff passed; formatting passed for all 4 touched Python files; strict core mypy passed (36 files); `ingestion.py` strict mypy remains at its 33-error baseline; import contracts 3 kept / 0 broken; core purity, rules hash `13e26fe7…`, engine-version check, synthetic golden self-check and `git diff --check` passed. Golden self-check had no cases (`unverified_no_cases`); its synthetic false-Matched detector passed. No provider calls or real-document fixtures were used. The PostgreSQL container was stopped after the check.

Open:
- Source-proof precedence is fixed, but benchmark identity and duplicate aggregates are unchanged: 8 extra documents and 2 duplicate misses. The authorized evidence rules do not identify these cases safely from the permitted aggregate-only report. AC-8 stays open because its zero-extra and duplicate-flag targets are unmet; AC-9 has not started. No AC-8c, migration, or later-phase work was started.

## D56 — AC-9a scope audit and offline baseline (8 Oct 2026)

Before code changes, the core matching entry point, app/CLI/benchmark callers, alignment rules, reference trust model and synthetic matching tests were traced. No AC-9a code was changed: the repo has no default GRN-to-invoice date window, no weights for the newly listed vendor/amount/strong-token/anchor signals, and no explicit verified flag in the pure `FieldValue`/`MappingDocument` model for deciding which reference anchors may auto-link. Existing `step3_min_score=0.80`, `step3_min_lead=0.15` and the six legacy Step 3 weights are documented; the existing `total_vs_expected` tolerance (1% or INR 10 in OR mode) belongs to reconciliation and is not passed into `MappingConfig`. AC-8b remains open; its unresolved identity behavior is not used by the current pure `resolve_links` path.

| Decision | Choice |
|---|---|
| Session scope | AC-9a only, as explicitly directed; the dependency table lists AC-8a + AC-6b while the diagram also places AC-8b before AC-9a. No AC-8b status or policy was changed. |
| D-6 | Preserve suggestion-only policy for new multi-signal matches; no new auto-link rule. |
| AC-9a tuning | Await owner decisions on the date-window default, new-signal scoring/tolerance policy, and the core evidence that qualifies an anchor for auto-link. |

Bench (`before-ac9a-session-20261008.json`, `--network none`; aggregate counts only):

| Measure | Result |
|---|---:|
| Expected / found auto / found suggested / missed links | 10 / 1 / 6 / 3 |
| Link recall / false suggestions / false auto-links | 7/10 (70%) / 0 / 0 |
| **False-safe / false auto-links / false Matched / wrong vendors** | **0 / 0 / 0 / 0** |
| Hard-metric gate | PASS |

Checks: baseline offline benchmark passed; no static checks or tests run because matching implementation is paused pending the required owner decisions. AC-8b's documented truth coverage remains two complete sets, so this baseline does not establish the written 95% link-recall target.

Open:
- Set the AC-9 matching date window, amount tolerance and scoring treatment for added signals, and verified-anchor evidence rule before implementing matching v2.
- AC-8b remains open; AC-9b has not started.

## D57 — AC-9a matching v2 core (8 Oct 2026)

Implemented the pure matching core and persisted the provenance needed to distinguish extracted references from verified references. Same-vendor identity is a gate, not a score; reference, part/model and amount use 40/35/25 weights normalized over available signals, with description as a lower-credit line-identity fallback. The expected amount is GRN accepted quantity × PO rate; 1% OR ₹10 grants full amount credit (the ₹10 floor only when the full invoice is covered), and score decays continuously outside that band. Quantity, price and date remain separate checks. Period-wide maximum-weight assignment respects open quantity and value ceilings; fully billed lines remain candidates and fail the open-quantity check.

Date checks use invoice-to-PO proximity: 0–7 days normal, 8–15 review/lower confidence, and over 15 strong mismatch/manual verification. An invoice dated before its PO is flagged; invoice-versus-GRN order is not flagged. Date proximity and open GRN quantity are tie-breakers only inside the existing 0.15 lead margin. Verified OCR/ERP references require exact normalized same-vendor open PO identity (or an existing same-vendor GRN→PO relation), an independent part/model or full-credit amount signal, and confidence ≥ 0.85; this reuses D42's clear-reference floor. User-entered values are verified independent of confidence; handwritten references are never verified from confidence alone. New multi-signal links stay suggestions (D-6). B1 canonicalization is now idempotent; B7's unverified OCR reference alone stays a suggestion. AC-8b remains open and AC-9b was not started.

Invoice↔GRN matching covers same-vendor invoice number to GRN supplier-invoice number or challan, plus invoice challan to GRN challan. The GRN's own PO link remains the path to a PO; the invoice's GRN number is corroboration only.

| Decision | Choice |
|---|---|
| Signal roles | Same vendor is a candidate gate; reference/marketplace order, part/model and amount are the only weighted signals (40/35/25); amount never retrieves candidates. |
| Tolerance and checks | Existing 1% OR ₹10 full-credit band; ₹10 floor only for full-invoice coverage; continuous amount decay; quantity and price variances shown separately and sum to amount delta. |
| Date policy | 0–7 / 8–15 / >15 bands as above; invoice-before-PO is a red flag; no invoice-before-GRN flag; dates and open GRN quantity only break close ties. |
| Anchor trust | Persist `origin`, nullable machine confidence, verification state, verifier and time in migration `0024_matching_provenance`; manual/user verification is confidence-independent; system-check is exact, same-vendor, open and independently corroborated. |
| D-6 | Only verified deterministic anchors may auto-link. Multi-signal matches remain suggestions; no new auto-link threshold. |
| Scope | AC-9a core only. AC-8b was not marked complete or changed; AC-9b, AC-10 and AC-11 were not started. |

Bench (`after-ac9a-20261008-final.json`, `--network none`; report `07703f906e1b`, engine `0.1.6`; aggregate counts only):

| Measure | Result |
|---|---:|
| Expected / found auto / found suggested / missed links | 10 / 0 / 3 / 7 |
| Link recall / false suggestions / false auto-links | 3/10 (30%) / 0 / 0 |
| **False-safe / false auto-links / false Matched / wrong vendors** | **0 / 0 / 0 / 0** |
| Identity extra / wrong merges | 8 / 0 |
| Duplicate copies caught / missed / false | 0 / 2 / 0 |
| Hard-metric gate | PASS; written ≥95% link-recall target remains unmet |

Checks: focused synthetic mapping/normalization/identity suite 100 passed; full offline unit/property/scenario/parser suite 772 passed; ingestion integration 4 passed and `test_phase4_task_411_413.py` 3 passed on disposable PostgreSQL 16.15 at `127.0.0.1:15499`. PG-SAFETY at `0024` passed migration safety, drift, catalog, index, decision, seed, queue-schema and table-count checks; schema contract counts remain 49 application tables, 38 enums, 27 triggers, 164 foreign keys, 258 explicit ACL entries and 7 default privileges. Other integration groups previously passed: 47, 22 and 76 tests. Group 5 had 7 passed and 2 `test_page_split.py` failures: `MultipleResultsFound` in `_current()` with duplicate PO rows, an existing AC-8b identity/split issue outside this session's scope. Ruff and touched-file formatting passed; strict core mypy passed (36 files); explicit app mypy has only the existing 33 `ingestion.py` baseline errors; import contracts 3 kept / 0 broken; purity passed; rules hash `ebc72906a13fe072ee07d288963a1b4156981c72e33e6d7a288439396290c2b0` verified; engine bump `0.1.4 → 0.1.6` verified; golden self-check passed with 0 cases (`unverified_no_cases`); `git diff --check` passed. Tests used `RB_AI_EXTERNAL_CALLS=disabled`; no provider calls or private benchmark cases were inspected. Web/API/E2E checks were not run because AC-9a changes no UI/API contract. Authentication and infrastructure-ready checks were not run because they require a separate database cluster and S3 credentials.

Open:
- The written ≥95% link-recall target is unmet (30%). Only aggregate benchmark counts were consulted; do not infer private benchmark cases. No case-directed tuning was made.
- AC-8b remains open; AC-9b has not started.
- One earlier schema-drift command accidentally used the configured default database URL before the disposable-URL guard was corrected. It was read-only and ran no migration or DDL/DML; all later PostgreSQL checks used disposable ports 15497/15499.

## D58 — AC-9a direct invoice↔GRN anchors (9 Oct 2026)

Core review found that `_via_grn_resolution` recognized the written same-vendor invoice-number↔GRN supplier-invoice/challan anchors, but discarded them unless part or amount also matched, and required a GRN→PO relation even to emit an invoice↔GRN edge. Direct identifier anchors now produce an explainable invoice↔GRN edge on their own; an invoice→PO edge is inherited only when that GRN already has a GRN→PO relation, while normal PO content matching still runs if it does not. Unverified identifiers remain suggestions; verified direct identifier pairs may auto-link under D-6. An invoice's GRN-number reference remains corroboration only and still needs an independent part or amount signal. The amount/date/identity thresholds and AC-8b behavior are unchanged.

| Decision | Choice |
|---|---|
| Direct invoice↔GRN identifiers | Same-vendor supplier-invoice number or challan equality is sufficient for an invoice↔GRN edge; verified matching fields can auto-link under the existing D-6 rule. Reuse a GRN→PO relation only when deriving invoice→PO; otherwise retain PO content suggestions. |
| Invoice GRN-number reference | Corroboration only; never creates an edge without independent part or amount evidence. |
| Scope | AC-9a pure core only; no app/API/UI or schema change. AC-8b remains open; AC-9b, AC-10 and AC-11 were not started. |

Bench (`after-ac9a-grn-anchor-final-20261009.json`, `--network none`; report `9cd8137fd7c1`, engine `0.1.7`; aggregate counts only):

| Measure | Result |
|---|---:|
| Files read / parsed / missed / spurious | 35 / 51 / 0 / 0 |
| Expected / found auto / found suggested / missed links | 10 / 0 / 3 / 7 |
| Link recall / false suggestions / false auto-links | 3/10 (30%) / 0 / 0 |
| **False-safe / false auto-links / false Matched / wrong vendors** | **0 / 0 / 0 / 0** |
| Hard-metric gate | PASS; written ≥95% link-recall target remains unmet |

Checks: focused synthetic mapping suite 48 passed; full offline unit/property/scenario/parser suite 774 passed in 95.90s with `RB_AI_EXTERNAL_CALLS=disabled`. Ruff check and changed-file formatting passed; strict core mypy passed (36 files); import contracts 3 kept / 0 broken; core purity passed; rules hash `8a1b2970cfbd07b2c4ec56501766c102f506c8583d535313d9db2f98fdeaaf1a` verified; engine version check passed (`0.1.4 → 0.1.7`); golden self-check passed with 0 cases (`unverified_no_cases`) and its synthetic false-Matched detector passed. The final `git diff --check` passed (only repository line-ending warnings). No migration or app/database change was made, so PostgreSQL and app integration groups were not rerun. No provider calls or private benchmark cases were inspected.

Open:
- Link recall remains 30% against the written ≥95% target. The permitted aggregate-only benchmark output does not identify which candidate-generation evidence is missing, so no further case-directed tuning was made.
- AC-8b remains open; AC-9b has not started.

## D59 — Scan fix WI-1, WI-3 and WI-2 steps 1/3/5 (9 Oct 2026)

Plan: `docs/accuracy-fix-plan-scan-extraction.md`. Upload and the bench now build parser hints through one function (`parser_hints_from_config`); the bench takes `--buyer-entities {truth,config,none}` (default `truth`), `--einvoice-key-file`, defaults to `--workers 1`, and counts sandbox timeouts (never cached). `RB_EXTRACTION_ENGINE` is an app setting checked at startup. `scripts/accuracy_attribution.py` (`make accuracy-attribution REPORT=…`) replaces the `work/diag-scan` prototypes and reads only the cache. `scripts/measure_extraction.py` is deprecated (exit 2; deletion is DP-3).

| Decision | Choice |
|---|---|
| Party role without buyer entities | One unlabelled GSTIN, or an unlabelled vendor beside another unlabelled GSTIN: vendor GSTIN `UNVERIFIED`, capped at 0.60 (layout and core text paths). A labelled Bill-To GSTIN establishes the vendor by elimination; with configured entities the old checksum rule is unchanged. |
| Empty buyer entities | `BUYER_ENTITIES_NOT_CONFIGURED` warning and `vendor_gstin` in `review_fields` for PDF documents that read a vendor GSTIN; Settings shows a non-blocking readiness note linking to Buyer entities. |
| Tests | `make test` now includes `backend/tests/parsers`; `tests/support/layouts.py` builds synthetic layouts; `tests/scenarios/test_hard_zero_guards.py` pins the hard-zero rules. |

Frozen baseline (`baseline-scanfix.json`, fresh worker-image reads, `--buyer-entities truth`): report `280fdebc96af`, engine `0.1.7`, extraction code `2ccc292e18ba`, truth `f25da17cbc5f`, timeouts 0. Segmentation, extraction, vendors, links, recon, hard and per-document blocks are identical to `after-ac9a-grn-anchor-final-20261009.json`, so the §0.4 table stands: header 302/373, lines 96/141, links 3/10, hard 0/0/0/0. Attribution (`baseline-scanfix-attribution.json`): scan-page docs 33, header 224/283 (A 9 / B 45 / C 0 / D 0 / E 5), line amount 78/111. Rules hash updated to `88fdcadf15b8…`.

Open:
- The `--buyer-entities none` / `config` runs (WI-1 (d), WI-3 bench acceptance) are not run yet.
- Guard found a gap: consensus `rules_readings` counts a `FieldSource.AI` value as an OCR reading, so with auto-verify on and no crop required a vision-written value verifies against its own vision reading. The guard is `xfail(strict=True)` until consensus is fixed.

## D60 — WI-3: vendor name without buyer entities (9 Oct 2026)

Cause: with `--buyer-entities none`, the layout reader took a PO or GRN's "for <Company>" line (the issuer, so the buyer) as the vendor name. A verified supplier GSTIN on one PO then created a vendor and named it after the buyer (anchor uses any non-empty name as provisional, so a confidence cap alone could not fix it).

| Decision | Choice |
|---|---|
| PO/GRN, empty entities | Only a name under a supplier label is used. If only the issuer line is found, the vendor name is left empty, `vendor_name` goes in `review_fields`, and the warning `VENDOR_NAME_ROLE_UNESTABLISHED` is added (`parties.withhold_vendor_name`). The vendor stays name-pending. |
| Core text parser | An unlabelled invoice heading tied to a vendor GSTIN whose role is not established is withheld the same way. |
| Configured entities | Unchanged. |
| Leftover C (accepted) | S10-GRN: the buyer's own GSTIN is read as the vendor GSTIN, capped at 0.60 and listed for review (D59 rule); no vendor is created. Left for WI-5 or a follow-up. |

Bench `none` (`scanfix-none-2.json`): hard 0/0/0/1 → 0/0/0/0; document vendor correct 31 / pending 2 / wrong 1 → 32 / 2 / 0; attribution C 4 → 1. Bench `truth` (`scanfix-truth-2.json`, report `470682236b6d`): segmentation, extraction, vendors, links, recon, hard and per-document blocks identical to `baseline-scanfix.json`. Tests 819 passed, 1 xfail; rules hash `43b153f776ec…`.

## D61 — WI-2 steps 2/4/6: CI Tesseract, scan images, golden cases (9 Oct 2026)

| Decision | Choice |
|---|---|
| CI | `scaffold-gates` installs `tesseract-ocr tesseract-ocr-eng` before `make test`, so OCR tests run in CI. The local skip remains (`scan_images.tesseract_with_english`). |
| Scan images | New `backend/tests/support/scan_images.py` renders a synthetic invoice (`SYN-INV-0001`, checksum-valid made-up GSTIN) with `skew_deg`, `rotate`, `dpi`, `jpeg_quality`, `noise`, `tiles`, `text_layer` (none/poor/good). The old `_scanned_invoice_pdf()` now calls it. |
| Scan tests | `tests/parsers/test_scan_images.py`, 7 cases: skew 1.5°, 150 DPI, rotated 90°, 4 strips and a garbled text layer read all 5 header fields correctly. JPEG q60/q40 with noise must give correct values or none (never a wrong one), and stay in review. |
| Golden | `golden_run.py` accepts a compact form (synthetic XML documents parsed by the core parser, labelled links by key). A case is verified when status, link and expected reason codes match. A mismatch now fails the gate. `golden/SYN-SCN-01` (Matched) and `SYN-SCN-04` (Exception, QTY_OVER_PO + QTY_OVER_GRN) are both verified (`gate_status: evaluated`). |

Seen, not fixed: on the q60 + 0.3% noise page, the taxable value and the GSTIN are lost (input to WI-8). A tiled page is OCR'd but not flagged `scanned`. Core unchanged, so no bench run. Tests 826 passed, 1 xfail; rules hash `43b153f776ec…`.

## D62 — AC-L0 part 1: WI-G2, WI-G1, WI-BE1 (16 Oct 2026)

Plan: `docs/plan-llm-extraction-call.md` §3. Owner decision §0.6 applies: Gemini and vLLM are separate providers; one request goes to exactly one provider; no answer is combined with another. No network, provider or vLLM call was made.

| Decision | Choice |
|---|---|
| WI-G2 import guard | `httpx`, `openai`, `requests` added to `forbidden_modules` of the contract "Only the AI worker may load the Gemini SDK or AI worker code". No module outside the worker imported them. The `openai` SDK is not added. |
| WI-G1 self-confirmation | `consensus.rules_readings`: a value whose source is `ai` becomes a `vision` reading, never `ocr`/`text_layer`/`structured`. So a vision value plus a second vision reading (any provider) is one reader kind → `single`/L2, never L1. A QR confirmation of such a value still counts as an independent reader. `crop_needs` now takes the first vision reading that has a page, so crops still use the check's geometry. The D59 strict xfail is removed and passes. `CONSENSUS_VERSION` unchanged (only AI-sourced fields can lose L1). |
| WI-BE1 provider port | `ProviderRequest.thinking_level` is `minimal/low/medium/high/None` (default `low`), new `options` mapping (Gemini ignores it); `ProviderResult` gains `latency_ms`, `finish_reason`, `model_reported`, `reasoning_present` (Gemini fills `model_reported` only). New `ai/worker/providers.py` `build_providers()` → `{"gemini"}` only. `AiRequestRunner(providers=…)` picks by `ai_request.provider`; a name not registered is skipped with `PROVIDER_NOT_CONFIGURED` before any reservation. `VisionReaderInfo` gains `prompt_variant`, `schema_dialect`, `box_order`, `billing` (`vision_gemini`: `gemini`, `gemini`, `yx`, true; nothing reads them yet). |
| Golden | `tests/unit/test_ai_provider_golden.py`, written before the refactor: SHA-256 and length of the exact Gemini request body for a synthetic two-page vision payload, plus its payload digest and cache key. Unchanged after the refactor. |

Checks: offline suites 835 passed, 0 xfailed (826 + former xfail + 8 new). Integration on disposable PG: `test_ai_runner` 26 passed (2 new: dispatch by name, unregistered provider skip), `test_ai_settings_api` 22 passed, H1–H4 + vision 74 passed, 1 failed: `test_ai_h1::test_ai_first_suggests_for_a_clean_pdf…` (rules-only clean PDF now `needs_verification`); it fails the same way with the WI-G1 change taken out, and that path never calls consensus or the runner, so it was already failing before this work (open). Ruff, mypy strict, lint-imports (3 kept), core purity pass. Rules hash `dca98ea94e39…`. Engine version check vs HEAD passes (0.1.4 → 0.1.7, bump already in the working tree). Bench not run (needs approval).

## D63 — AC-L0 part 2: WI-BE5 (provenance, cache key v2) and WI-BE4 (schema/prompt v4, standalone checks) (16 Oct 2026)

Plan: `docs/plan-llm-extraction-call.md` §3. §0.6 applies: one request, one provider; no answer is combined with another. No network, provider or vLLM call was made. Synthetic data only.

| Decision | Choice |
|---|---|
| Migration `0025_ai_reader_params` | `ai_request.reader_params jsonb NOT NULL DEFAULT '{}'` (object check); `ai_cache.provider ai_provider`, `schema_version text` (1–64), `reader_params jsonb` (object check), all nullable. No backfill. Errata entry added; CI previous schema `0024`; Phase 4.11/4.13 revision set includes `0025`. |
| `reader_params` | `ai/vision.reader_params(reader, reader_pass)`: reader, render version, max side, JPEG quality, mask version, media resolution, max pixels, thinking, sampling profile, prompt variant, pass. `VisionReaderInfo` gains `thinking`, `sampling_profile`, `max_pixels` (`vision_gemini`: low, provider_default, none). The enqueuer stores them on vision requests; text hooks keep `{}`. |
| Cache key v2 | `cache_key_v2` adds provider and a reader-params digest; `request_cache_key` picks v2 for `h6_vision` and keeps v1 for H1–H4 (their caches stay valid). The runner and the bench both use it. New cache rows record provider, schema version and (vision) reader params; `None` is written as SQL `NULL`, not JSON `null`. |
| Golden keys (synthetic two-page payload) | v1 `5f1c6d16e9c7…` (unchanged, v3 payload) → v2 of the same v3 payload `848fa3cb6b47…` (BE5 alone; Gemini body hash `acd3bdd9a676…` unchanged through BE5) → v2 of the v4 payload `42cdab004640…` (after BE4). |
| Bench | `accuracy_vision` uses the v2 key. `--vision-cache-compat v1` (read-only, default dir `work/accuracy-cache/vision-v1`) also finds answers recorded under the v1 key, only while the reader is Gemini with the pre-v2 parameters; the v1 key is computed from a frozen copy of the v3 prompt and schema. The 72 cached answers were copied to `vision-v1` (counts only). |
| Schema `vision.schema.v4` | v3 plus top-level `unreadable: [{name, page, box_2d}]` (no value). Two dialects from one definition: `gemini` (as before, no `maxItems`/`anyOf`) and `openai_json_schema` (every key required, `additionalProperties: false`, optional keys nullable via `anyOf`). A unit test keeps both in step with the Pydantic models; `unreadable` is optional in the model so v3 answers still parse. |
| Prompts | `vision.prompt.v4.gemini` = v3 text + rule 9 (unreadable). `vision.prompt.v4.qwen` = shorter rules, explicit `box_2d` coordinate rule, one made-up example (`SYN-INV-0001`, checksum-valid synthetic GSTIN). `PROMPT_VERSIONS` no longer holds `h6_vision`: `VISION_PROMPT_VERSIONS` per reader and `prompt_version(hook, reader=…)`. A reader returning `xy` boxes is swapped to `yx` before checks. |
| Consent | With vision on, the fingerprint covers the reader's prompt version and variant (re-consent: the vision prompt changed). With vision off it is byte-identical to the legacy formula (test). |
| Standalone checks (core) | `check_reading` marks, never drops: `totals_arithmetic`, `lines_vs_taxable` (±₹1), `line_arithmetic` (q × r), `date_window` (D42, only with `as_of`; the app passes today, the bench none). Counts in `VisionCheck.check_failed`; `unreadable` entries placed and counted (unknown name, bad page/box dropped). The OCR-anchored drop rules are unchanged. The date window moved to `extraction/date_window.py`, shared with consensus (same rule). |
| Gemini body | Changes in BE4 as the plan specifies (rule 9 and the `unreadable` schema): new golden `7ede12081bc9…` (8,121 bytes). The frozen v3 builder still reproduces the D62 body `acd3bdd9a676…` and the v1 key (test). |

Checks: offline suites 873 passed, 0 xfailed (835 + 38 new). Integration on disposable PG: `test_ai_runner` + `test_ai_assist_migration` 43 passed (new 0025 test included), `test_ai_settings_api` 22 passed, H1–H4 + vision 74 passed, 1 failed (`test_ai_h1::test_ai_first_suggests_for_a_clean_pdf…`, already failing before this work, D62). PG-SAFETY on `127.0.0.1:15497`: migration safety 0024→0025, drift, catalog, indexes, decisions, seed, queue schema, table count pass; contract counts unchanged (49 tables, 38 enums, 27 triggers, 164 FKs, 258 ACLs, 7 default privileges). Ruff, mypy strict, lint-imports (3 kept), core purity pass; OpenAPI export unchanged. Rules hash `24a1348f4fa4…`; engine version check vs HEAD passes (0.1.7 already in the tree). Not run: benches (need approval), so the `--vision-cache-compat v1` replay of `after-d41-lite.json` (header 271/313, lines 80/123) is still to be confirmed by the owner.

## D64 — AC-L0 part 3: WI-BE2 `vision_openai_compat` provider for vLLM (Qwen3.5-9B) (16 Oct 2026)

Plan: `docs/plan-llm-extraction-call.md` §0 and WI-BE2. §0.6 applies: Gemini and vLLM are separate alternatives; a request goes to exactly one provider and no answer is combined with another provider's. No network, provider or vLLM call was made; every HTTP exchange in tests is `httpx.MockTransport` with synthetic data.

| Decision | Choice |
|---|---|
| Provider | New `ai/worker/openai_compat.py` `OpenAICompatProvider` (`name="vllm"`, `external=False`), plain `httpx` (no SDK): `SingleAttemptTransport(HTTPTransport(retries=0))`, `follow_redirects=False`, `trust_env=False`, timeouts connect 5 / read = request / write 30 / pool 5 s, request timeout 1–300 s. `POST {base_url}/chat/completions` (base URL already ends in `/v1`). Every body: `chat_template_kwargs {"enable_thinking": false}` (options cannot override it), sampling profile `qwen_card_nonthinking` (temperature 0.7, top_p 0.8, top_k 20, min_p 0, presence 1.5, repetition 1.0; `_pp0` arm available; Gemini's `provider_default` maps to the card), `seed 20261016`, `response_format json_schema` (`vision_reading`, strict) with the v4 `openai_json_schema` dialect, `stream false`. More than one image → `INVALID_REQUEST`, not sent. `Authorization: Bearer` only when a key is given. |
| Response | `choices[0].message.content` must be a JSON text; `usage.prompt_tokens/completion_tokens` recorded; `reasoning_content`/`reasoning` → `reasoning_present` + counter; content starting with "Thinking Process" or `<think>` → `invalid_output` and `thinking_leak` counter; `finish_reason=length` → `invalid_output`, detail `at_output_cap`. Counters are in-process plus a warning log line (not persisted). |
| Errors (`map_openai_compat_error`) | 400 `INVALID_REQUEST` (300-char diagnostic, digit runs ≥ 6 masked, kept on `ProviderFailure.detail` and logged; never shown), 401/403 `bad_key`, 404 `MODEL_UNAVAILABLE`, 408/504 `timeout`, 429 `rate_limited` / 503 `PROVIDER_UNAVAILABLE` (30 s backoff), other 5xx `error`, other 4xx `INVALID_REQUEST`, 3xx `error` (redirect not followed). Connect refused/timeout → `PROVIDER_UNAVAILABLE` not sent (released); read timeout → `timeout` responded; nothing is "uncertain" (nothing billed). |
| Health | `health()` = `GET {base_url}/models` (5 s): ok, latency, model ids, `max_model_len` per model; `context_window(model)` fetches it once and caches it. Heartbeat and `last_health_*` columns are WI-BE3. |
| Endpoint policy (SSRF) | New pure `ai/endpoint_policy.py` (stdlib only; added to the import-linter "no HTTP client" contract): `normalize_base_url` (http/https, no user info/query/fragment/`.`/`..`/empty segments/percent-escapes/non-ASCII, lowercase host, default port dropped, no trailing slash), `parse_allowlist`, `check_allowed` (exact origin + path prefix). Checked by the gate and again by the provider before every request and health call (no I/O when it fails). The API-side check on save comes with the URL field in WI-BE3. |
| Gate | `DeploymentGate` gains `vllm_mode` (`RB_AI_VLLM_CALLS`, default `disabled`, same three modes) and `vllm_endpoints` (`RB_AI_VLLM_ENDPOINTS`, comma list; required when the mode is not `disabled`). `refusal(..., external=True, endpoint=None)`: external calls unchanged; a local call needs its mode (`LOCAL_CALLS_DISABLED`, synthetic-fixture rules for `synthetic_only`) and an allowlisted endpoint (`ENDPOINT_NOT_ALLOWED`). No approval reference for the company server. |
| Registry | `vision_openai_compat`: provider `vllm`, external false, billing false, prompt variant `qwen` (`vision.prompt.v4.qwen`), dialect `openai_json_schema`, box order `yx` (to confirm in the WI-OPS1 image smoke test), thinking `off`, sampling `qwen_card_nonthinking`, new field `pages_per_call=1` (not in `reader_params`, so the Gemini cache key is unchanged). `build_providers` registers the vLLM provider only when `RB_AI_VLLM_BASE_URL` is set and `RB_AI_VLLM_CALLS` is not `disabled`; a base URL outside the allowlist stops the worker at startup. |
| Limits and runner | `AiLimits.h6_vision_local` (used when the provider is `external=False`): 1 page per call, max output 4,096, image estimate 3,500 tokens, timeout 300 s, `max_concurrency` 1, `backoff_seconds` 30. Only `*_local` kinds may exceed the 120 s Gemini cap (validator). Per call the runner computes `max_tokens = max_model_len − ceil(prompt text bytes / 3) − image tokens − 256` (the schema is enforced by decoding, not sent) and skips with `CONTEXT_TOO_SMALL` before any reservation when it is below 1,024. `ledger.reserve` gains `max_concurrency` (in-flight per provider across workspaces, advisory lock) and `backoff_seconds` (defer `PROVIDER_BACKOFF` after the provider's last `rate_limited`/`PROVIDER_UNAVAILABLE`). Vision requests pass `reader_params` as `options`; Gemini ignores them. |
| One page per call | `Prepared.parts` + `merge_outputs`: the vision handler builds one payload per page for a reader with `pages_per_call=1`; the runner sends them in order (lease = timeout × pages, usage summed, any page failure fails the request). Answers are merged by the new pure core `merge_page_readings` (each page's items kept; an item naming another page, or a boolean page, is dropped and counted; `doc_kind` = first named kind) and the merged answer is parsed strictly and checked exactly like a Gemini answer. Digest, cache key and input limits stay on the whole-document payload. |
| Not done (deferred, with reason) | `RB_VISION_READER=all` and its default: consent fingerprint, settings API and enqueuer still assume one reader, and the `ai_provider` enum has no `vllm` value, so no ledger row can name the vLLM provider yet → WI-BE3 (migration, settings, key-optional path; the runner still requires a sealed workspace key). Image long side 1,600 px (the render spec is shared; images go at the existing 2,048 px render; `mm_processor_kwargs` not sent, unverified on vLLM 0.23.0) → WI-OPS1. The deferred 60 s retry after a refused connection (it is released and starts the 30 s backoff) → WI-OPS2. The stdlib mock server: the owner asked for `httpx.MockTransport` only; the integration test covers prepare → request → answer → cache → consensus enqueue with it. |
| Gemini | Golden body `7ede12081bc9…` unchanged (`test_ai_provider_golden.py` untouched and passing). `test_vision_v4` now pins both readers' prompt versions. |

Checks: offline suites 951 passed, 0 xfailed (873 + 78 new in `tests/unit/test_ai_openai_compat.py`). Integration on disposable PG (`127.0.0.1:15499`, stopped afterwards): `test_ai_runner` 32 passed (6 new), `test_ai_vision` 14 passed (1 new, vLLM reader through the real runner), `test_ai_settings_api` 22 passed; `test_ai_h1` 13 passed, 1 failed (`test_ai_first_suggests_for_a_clean_pdf…`, already failing before this work, D62). Ruff, mypy strict (core; touched app files also clean under strict), lint-imports (3 kept), core purity pass. Rules hash `24a1348f4fa4…` → `1e40652fe689…` (only `merge_page_readings` added; verified). Engine version check vs HEAD passes. Not run: benches and any vLLM call (need approval; record the real-invoice approval of 16 Oct here before the first real-page call).

## D65 — AC-L0 part 4: WI-BE3 settings model, API, migration, consent, audit for separate providers (16 Oct 2026)

Plan: `docs/plan-llm-extraction-call.md` §0 and WI-BE3, with the D64 deferrals. §0.6 applies: Gemini and the company vLLM server are separate alternatives; a workspace has at most one vision reader (`single`) or one shadow reader (`shadow`); there is no `dual_agree`, no `secondary_reader` value (always `null` in the API) and no merging of two providers' answers. No network, provider or vLLM call was made; all HTTP in tests is `httpx.MockTransport` with synthetic data.

| Decision | Choice |
|---|---|
| Migration `0026_ai_providers` | `ALTER TYPE ai_provider ADD VALUE IF NOT EXISTS 'vllm'` in `autocommit_block()`; enums `ai_reader_strategy (off, shadow, single)` and `ai_apply_mode (suggest, merge)`; table `ai_provider_config` as specified (UNIQUE `(workspace_id, provider)`, `base_url` only for `vllm`, `limits` object, `last_test_*`, `last_health_*`, `version`), owner `reconbench_migrator`, default grants, `REVOKE ALL … FROM reconbench_report` (as for the other AI tables); `ai_setting` gains `reader_strategy`, `primary_reader`, `apply_mode` with CHECK `(reader_strategy = 'off') = (primary_reader IS NULL)`. Backfill: every Gemini row gets a Gemini card (`enabled = mode <> 'rules_only'`, model) and `single`+`gemini` when `vision_enabled`, else `off`. Fixed-forward; no other data rewritten. |
| Deviations from the plan text (with reason) | (1) The Gemini card does **not** copy `key_secret_id`: the Gemini key stays on `ai_setting` (text hooks read it there), so one secret is never referenced by two rows that the key sweeps must keep in sync; CHECK `provider = 'vllm' OR key_secret_id IS NULL`. (2) `vision_enabled` is kept equal to `reader_strategy = 'single'` (not `<> 'off'`): in `shadow` the H1 text hook must keep running and nothing from the shadow reader is shown, so the old flag must not switch H1 to H6. No CHECK ties the two columns (older fixtures write only `vision_enabled`); a row with only the old flag reads as `single`+`gemini`. (3) Gemini `enabled` follows `mode` (turning Gemini off while AI is on is refused with `gemini_required`), because text hooks H1–H4 keep using Gemini. |
| Runtime resolution | `SettingsSnapshot` carries strategy, primary reader, apply mode and the vLLM card. Text hooks target `ai_setting.provider/model`; the vision hook targets the primary reader (vLLM → card model). `request_blocker` checks the request against that target, refuses vision when the strategy is not `single`, and checks the key per provider. `AiHookEnqueuer` uses the provider's own gate (`RB_AI_VLLM_CALLS=enabled` + allowlisted card URL for vLLM) and takes provider, model, prompt version and reader params from that provider's reader (`reader_for_provider`; Gemini keeps exactly the configured Gemini reader). The worker vision handler picks the reader from the request's provider. Shadow requests are not enqueued yet (WI-BE6). |
| Key-optional vLLM | `load_provider_key`: Gemini key required as before; vLLM without a stored key is called without `Authorization` (runner passes an empty key); a stored vLLM key is sealed like Gemini's (write-only, session or saved), ends with its session, and is removed with `remove_key`. Session/expiry sweeps also detach card keys and bump the settings version. |
| Per-workspace URL + SSRF | `PUT /ai/settings` `providers[vllm].base_url` and `consent-preview` `vllm_base_url` go through `endpoint_policy.check_allowed` against `RB_AI_VLLM_ENDPOINTS` (now also read by the API process) → 422 `endpoint_not_allowed`; the normalised URL is stored. The worker calls the workspace URL via `OpenAICompatProvider.for_base_url` (same allowlist and transport, cached per URL, rechecked before every request). Tested: metadata IP, user info, query, longer/shorter path, IPv6 literal, other scheme or port, `..`, `file:`; uppercase host and `:80` normalise to the allowlisted URL. Redirects are never followed (D64). |
| Consent | `fingerprint_for(...)` adds `vision_reader` (provider, model, base URL; qwen prompt version) for `single`+`vllm`, `reader_strategy`/`shadow_reader` for `shadow`, and `apply_mode` when not `suggest`. Legacy `off` and `single`+`gemini` give byte-identical fingerprints (unit tests; migration test: migrated workspaces stay `consent_current`). Consent text gains one sentence for the company server or shadow; `DATA_DESTINATIONS` per provider. A new server URL or model needs consent again. |
| API | `GET /ai/settings` adds `providers[2]` (key only as `{set, hint, storage, expires_at}`), `reader_strategy`, `primary_reader`, `secondary_reader: null`, `apply_mode`, `vllm_calls`, `vllm_endpoints_allowed`. `PUT` accepts `providers`, `reader_strategy`, `primary_reader`, `apply_mode`; `merge` → 422 `merge_not_enabled` (no deployment flag yet, WI-BE7); local model regex `^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$`; per-card `limits` restricted to `max_concurrency` 1–4 and `timeout_seconds` 1–300 (stored, not yet applied). `POST /ai/test/provider` takes an optional `{provider, kind}`; the vLLM text test queues the same synthetic fixture (keyless unless a key is stored) and stores the result on the card; `kind=image` → 422 `image_test_not_available`. `GET /ai/usage?provider=` filters calls, tokens, uncertain requests and USD, and adds latency p50/p95 today. Audit: `ai.provider_config_save` (before/after without key, base URL as origin only), `ai.reader_strategy_change`, `ai.apply_mode_change`; `ai.test_requested` now names the provider. `ai.manage` stays manager-only (P0 maps every session to manager; the capability table is tested). |
| Health heartbeat | `sweeps.provider_health`: every third heartbeat the AI worker calls `GET /models` once per enabled vLLM card URL (without a key) and writes `last_health_at/ok`; a URL no longer allowlisted is marked unhealthy without I/O. A server that requires a key shows unhealthy here (`bad_key`) and is proven by the provider test instead. |
| Gemini | Golden body `7ede12081bc9…` unchanged (`test_ai_provider_golden.py` untouched, passing). The status ladder stays Gemini-only and now ignores provider tests of other providers. |
| Not done (deferred) | Synthetic page-image test fixture and per-step results in `GET /ai/requests/{id}` (need a reviewed image fixture + digest) → WI-OPS1. Shadow execution and storage → WI-BE6. `merge` flag → WI-BE7. Replacing the hand-written `web/src/api/ai.ts` interfaces with generated types, and the UI cards → WI-FE1/FE3 (`generated.ts` regenerated; web typecheck passes). Dropping `vision_enabled` → a later migration. Applying per-card `limits` → WI-OPS2. |

Checks: offline suites 973 passed, 0 xfailed (951 + 22 new in `tests/unit/test_ai_provider_settings.py`). Disposable PostgreSQL 16.15 at `127.0.0.1:15499`, plus a second clean instance at `15498` to regenerate the catalog contract without queue tables; both stopped. Migration safety (0025 clone with a legacy vision row → 0026) passed; schema drift (autogenerate: no drift; contract regenerated: 50 tables, 40 enums, 27 triggers, 167 FKs, 262 ACLs, 7 default privileges), catalog, indexes, decisions and table count (50 + 4 Procrastinate) passed. Integration: `test_ai_settings_api` 54 passed (22 + 32 new), `test_ai_runner` 32 passed, `test_ai_vision` 14 passed (the D64 vLLM test now runs as provider `vllm`, keyless, at the workspace URL), new `test_ai_providers_migration` 4 passed (clone rewound to 0025, legacy rows, real upgrade, backfill, unchanged fingerprints, constraints, owner/grants), `test_ai_assist_migration` 17 passed, H1–H4 61 passed plus the known `test_ai_h1::test_ai_first_suggests_for_a_clean_pdf…` failure (D62). Ruff, mypy `--strict` (37 files; touched app files clean under strict), lint-imports 3 kept, core purity, rules hash `1e40652fe689…` unchanged, engine version vs HEAD passes; OpenAPI and `generated.ts` regenerated and stable on a second run (only WI-BE3 additions); web typecheck passes. Note: `schema_contract.json` was first overwritten from a database that already had the queue schema; it was regenerated from a clean head database and the drift check passes against it. Nothing committed; no `docs/ReconBench_*.md` edited.

## D66 — AC-L0 part 5: WI-BE6 shadow mode and comparison store, one reader only (16 Oct 2026)

Plan: `docs/plan-llm-extraction-call.md` §0 and WI-BE6, with the D65 deferrals. §0.6 applies: shadow uses exactly one reader, the workspace's `primary_reader` (Gemini **or** the company vLLM server). There is no secondary reader, no LLM×LLM agreement and no dual-reader report; the plan text about "(and secondary)" and "For dual readers" is out of scope. No network, Gemini or vLLM call was made; tests use `ScriptedProvider` and `httpx.MockTransport` with synthetic documents.

| Decision | Choice |
|---|---|
| Migration `0027_reader_comparison` | `ai_request.shadow BOOLEAN NOT NULL DEFAULT false` with CHECK `NOT shadow OR hook = 'h6_vision'` (existing rows are not shadow). Table `reader_comparison` as specified (`id`, `workspace_id`, `document_version_id`, `ai_request_id`, `provider`, `model`, `prompt_version`, `schema_version`, `reader_params_digest` (64 hex), `created_at`, `counts` object, `labels` array), UNIQUE `(workspace_id, ai_request_id)` (one row per request; a re-apply is a no-op), index `(workspace_id, created_at)`, trigger `reader_comparison_append_only` (`forbid_mutation()`), `REVOKE UPDATE, DELETE, TRUNCATE … FROM reconbench_app`, `REVOKE ALL … FROM reconbench_report` (as `consensus_decision`). Fixed-forward. |
| Why a column on `ai_request` | The review view must tell a shadow request from the workspace reader's request after settings change; the request's own row is the only durable place. Not in `request_key` (shadow and single already differ by settings version and consent fingerprint) and not in `reader_params` (that would change the vision cache key; a shadow answer may be reused by `single` for the same provider/model/prompt/params/images and vice versa). |
| Enqueue (D65 deferral) | `AiHookEnqueuer.ready(h6_vision)` accepts `single` or `shadow`. In `shadow` the consented hook list stays H1's (`vision_enabled` stays false, H1 keeps running), so the vision hook is gated on H1 being consented; same eligibility, gate, endpoint allowlist and key checks as `single`. The request is created with `shadow = true` (also for a recorded render-failure skip). |
| Worker | `request_blocker`: a shadow request runs only while the strategy is `shadow`, a non-shadow vision request only while it is `single` (else `AI_OFF`, checked before sending and before applying). `VisionReadHandler.apply` for a shadow request: when current, write one `reader_comparison` row, purge the version's page bytes, return `applied`; no consensus enqueue, no suggestion. Stale → `discarded_stale` + purge, as before. Failed or invalid answers write no row (the failure is on `ai_request`; the idle sweep clears the bytes). |
| Comparison (labels only) | `reconbench_app/ai/shadow.py` `compare(rules, check)`: the rules document is rebuilt as the consensus job does (`document_from_rows`); reader values come from the core `vision_readings` (same line alignment); fields are the comparable consensus fields (`HEADER_FIELD_TYPES`, `LINE_FIELD_TYPES`); agreement uses the core `values_agree`. Labels: `agree_ocr`, `disagree_ocr`, `llm_only`, `ocr_only`, `unreadable` (reader marked it unreadable and gave no value). `counts`: `fields_total`, one count per label, `check_failed_by_reason`, `dropped_by_reason`, `rows`. Field keys (`header:doc_no`, `line:3:amount`) and reason codes only; no value, quote or box is stored. No core change (rules hash unchanged). |
| Review view | `GET /documents/{id}/ai-suggestions` ignores shadow requests: the latest H1 or workspace-reader request is shown as before; when the document has only shadow requests (e.g. a PO/GRN, which H1 does not read) it returns `state = not_applicable`, `code = SHADOW`. |
| Report | `GET /ai/shadow-report?since=YYYY-MM-DD` (`ai.manage`) and `python -m reconbench_app.ai.shadow_report --workspace <uuid> [--since YYYY-MM-DD]` return the same body: `reader_strategy`, `shadow_reader`, `since`, and per provider `comparisons`, `totals`, per field group (`line:N:x` → `line:x`) the five label counts, merged check-failed/dropped reasons, shadow request states, `failures_by_code` (error or skip code), latency p50/p95 (cache hits excluded). No document id, value or line number. OpenAPI and `generated.ts` gain only this endpoint. |
| Unchanged | Consent: `consent.py` and `fingerprint_for` untouched; the D65 shadow fingerprints are now pinned in a unit test (`cb7d2ee1…` for shadow+vLLM, `bad2261b…` for shadow+Gemini). Gemini golden `7ede1208…` unchanged (`test_ai_provider_golden.py` untouched, passing). |
| Deviation (with reason) | The plan names `document_link`; the link tables are `link` and `link_current`, so the integration test counts both (plus `consensus_decision`). |
| Not done (deferred) | Bench run with shadow (hard metrics are unaffected by construction: nothing is written to documents; the owner's bench run stays the acceptance step). UI for the report → WI-FE1/FE2. LLM×LLM arms stay offline-bench only (§0.6). |

Checks: offline suites 981 passed, 0 xfailed (973 + 8 new in `tests/unit/test_ai_shadow.py`). PG-SAFETY on a fresh PostgreSQL 16.15 cluster at `127.0.0.1:15497`: migration safety (clone at 0026 → 0027) passed; autogenerate no drift; `schema_contract.json` regenerated at head **before** the seed and queue schema (no `procrastinate` schema or tables present) → 51 tables, 40 enums, 28 triggers, 170 FKs, 264 explicit ACLs, 7 default privileges; drift, catalog, index and decision checks passed; after seed + queue schema the table count is 51 + 4 Procrastinate. Integration on a second fresh cluster at `127.0.0.1:15499`: new `test_ai_shadow` 4 passed (0 rows changed in `document_version`, `doc_line`, `consensus_run`, `consensus_decision`, `vendor`, `link`, `link_current`; no consensus enqueue; page bytes purged; row, API report and CLI output contain none of the synthetic answer's value strings nor document ids; report role cannot read, app role cannot update/delete, UPDATE raises; GRN-only document → `not_applicable/SHADOW`; switching to `single` drops a queued shadow request with `AI_OFF` and no call; vLLM shadow calls only the workspace URL), `test_ai_runner` 32, `test_ai_vision` 14, `test_ai_settings_api` 54, `test_ai_providers_migration` 4 (rewind now also removes the 0027 objects; head `0027`), `test_ai_assist_migration` 17 passed; H1–H4 61 passed plus the known `test_ai_h1::test_ai_first_suggests_for_a_clean_pdf…` failure (D62). Both containers stopped. Ruff (app, core, tests, scripts), mypy `--strict` (37 files; touched app files clean; `ingestion.py` at its 33-error baseline), lint-imports 3 kept, core purity, rules hash `1e40652fe689…` unchanged, engine version vs HEAD passes; OpenAPI and `generated.ts` regenerated and stable on a second run; web typecheck passes. Nothing committed; no `docs/ReconBench_*.md` edited.

## D67 — AC-L1 part 1: WI-EV1 evaluation harness, one vision provider per run (16 Oct 2026)

Plan: `docs/plan-llm-extraction-call.md` §0 and WI-EV1, with D66. §0.6 applies: each bench run reads with exactly one vision provider (Gemini **or** the company vLLM server); two arms are compared by putting their reports side by side, never by combining answers. Out of scope and not built: the `llm_x_llm` and `llm_x_llm_x_ocr` policies, `--second-vision-report`, every LLM×LLM metric, WI-BE7's `merge_readings` and WI-BE8 crops. No bench was run on real samples, no Gemini or vLLM call was made, nothing in `work/` was read; tests use synthetic truth, recorded answers, `ScriptedProvider`-style callables and `httpx.MockTransport`.

| Decision | Choice |
|---|---|
| Arm | `scripts/accuracy_vision.py` `VisionArm(provider, thinking, sampling, strategy, base_url, repeats)`. `reader()` is the app's reader for that provider (`PROVIDER_READERS`) with only the overridden fields replaced, so `reader_params`, prompt variant, schema dialect and the v2 cache key come from the app code. `DEFAULT_ARM.reader()` **is** `vision_reader("vision_gemini")`: default keys, legacy keys and answers are the ones recorded before. |
| Flags (`scripts/accuracy_bench.py`) | `--vision-provider {gemini,vllm}` (default gemini); `--vision-base-url` (vLLM only); `--vision-model` (default `RB_BENCH_VISION_MODEL` or `gemini-3.8-flash` for Gemini, `qwen3.5:9b` for vLLM); `--vision-thinking {off,minimal,low,medium,high}`; `--vision-sampling {provider_default,qwen_card_nonthinking,qwen_card_nonthinking_pp0}`; `--vision-strategy {full,crops}`; `--repeat N` (1..10); `--agreement-policy {single,llm_x_ocr}`; `--vision-primary-sim`; `--vision-eval`. All arm checks run in `run()` before the truth file is read. |
| Refusals (`ArmRefused`, nothing read or sent) | `--vision-primary-sim` (WI-BE7 `merge_readings` does not exist); `--vision-strategy crops` (WI-BE8); `--vision-base-url` not on `RB_AI_VLLM_ENDPOINTS` (normalised and matched by the app's `endpoint_policy`, origin and path) or given with Gemini; vLLM with thinking other than `off` (§0.5) or `provider_default` sampling (it would alias `qwen_card_nonthinking` under another key); Gemini with `off` or a Qwen sampling profile; a `qwen…` model with Gemini or a `gemini…` model with vLLM (mixing guard); `--repeat` outside 1..10; vLLM sending without `--vision-base-url`. `llm_x_llm`, `llm_x_llm_x_ocr` and `--second-vision-report` are not argparse choices (rejected by the parser). |
| Repeats | `repeat_key(key, 0) == key`; repeat `i ≥ 1` is `sha256({"key": key, "repeat": i})`. Repeat 0 is the vision block's reading (so it reuses any recorded answer); repeats 1..N-1 are read into `repeat_validations`, their tokens into `VisionRun.repeat_usage` (the vision block's `usage` stays repeat 0 only). The page budget counts every missing request (`pages × missing repeats`). |
| vLLM sending | `send(..., arm)` builds no Gemini client (test: `GeminiProvider.__init__` patched to raise) and binds the app's `OpenAICompatProvider` to the one checked URL (`frozenset({base_url})`; the adapter re-checks before every request). As in the AI worker: one page per call (`page_prompts`), per-page answers merged with `merge_page_answers` (an unparsable page is recorded as an empty answer → `invalid_output`), `reader_params` sent as `options`, answer budget `min(h6_vision_local cap, local_output_budget(max_model_len …))` from `GET {base}/models`, `CONTEXT_TOO_SMALL` skips with no POST, timeout `h6_vision_local` (300 s). No key unless `BENCH_VLLM_KEY` is set; `BENCH_GEMINI_KEY` is never read in vLLM mode. Failure entries keep only fixed adapter details (`thinking_leak`, `at_output_cap`, `not_json`, …), never server text. Gemini sending is unchanged (same request; `thinking_level` = reader `low` = the old default; Gemini ignores `options`). Recorded answers of a non-default arm also store `provider`, `calls`, `repeat`. |
| `vision_eval` block | New `scripts/accuracy_vision_eval.py`, written as `report["vision_eval"]` with its own `vision_eval_hash` only when a WI-EV1 flag differs from the default, `--agreement-policy llm_x_ocr` or `--vision-eval` is given. Counts only (no value, file id or truth id). Contents: `arm`, `model`, `agreement_policy`; `accuracy` by subset (`all_pdf`, `scan_page`, `scan_type:{digital,scan,mixed}` from the rules reader's page OCR flags) × reader (`llm`, and `ocr` = Tesseract + rules on the same documents) × `header`, `header:<field type>` and `line:<amount,quantity,rate,description,hsn_sac,uom>` (a cell is scored only when truth has it; rows aligned as the bench aligns them); `auto_accept` per field type (`single`: LLM value that passed every standalone check; `llm_x_ocr`: and equal to the OCR value) with precision, coverage and Wilson 95% lower bound; `false_safe` (`rules`, `simulated_flags_kept`, `simulated_review_cleared`, `review_cleared_documents`); `unreadable_to_null` (listed vs silently omitted); `wrong_when_answered` (all and critical); `not_in_ocr_text`; `agree_wrong_llm_x_ocr`; `tokens` (all repeats); `cost` (USD only from `RB_AI_PRICE_FILE`, else `null`); `latency_ms` p50/p95 (nearest rank); `failures_by_code` (`call:`, `answer:`, `document:`); `repeat_variance` (fields whose normalised value or line-row count changes across repeats). |
| False-safe simulation (stand-in until WI-BE7) | The rules document with the policy's accepted LLM header values put in; lines stay the rules'. `flags_kept` keeps the rules review flag; `review_cleared` clears it when the policy accepted every critical field the truth has (the case where auto-accept would let a document through). Both are reported because `flags_kept` alone is almost always 0 and `review_cleared` also exposes the rules' line misses (the synthetic invoice shows 1). WI-EV2 rule (b) should read `simulated_review_cleared`. `--vision-primary-sim` will replace this with `merge_readings` when WI-BE7 lands. |
| Make | `accuracy-bench-vllm`: worker image with network on (company VPN through the host), passes only `RB_AI_VLLM_ENDPOINTS` and `RB_AI_PRICE_FILE` (no Gemini key), runs `--vision provider --allow-provider --vision-provider vllm $(BENCH_ARGS)`. With the default `--max-provider-pages 0` nothing is sent. Gemini arms keep `accuracy-bench-vision`. A price file must sit under `work/` and be named by its container path (`/app/work/…`). |
| Unchanged | `backend/reconbench_core`, `backend/reconbench_app`, migrations and APIs untouched (no OpenAPI/types drift run, no PG integration run). Rules hash `1e40652fe689…`, Gemini golden `7ede1208…` (`test_ai_provider_golden.py` untouched, passing), D65/D66 shadow consent fingerprints (`consent.py` untouched, pin test passing). Synthetic default-bench guard (1 synthetic invoice, a recorded answer, scratch dirs only): `report_hash` `c728aec0…`, `vision_hash` `a9d9f53c…`, `consensus_hash` `c9392395…` identical before and after, whole report identical except `timing`; with `--vision-eval` the three hashes are still identical. |
| Deviations (with reason) | Plan flag `--agreement-policy` keeps only `single`/`llm_x_ocr` (§0.6). `--vision-strategy` accepts `crops` in argparse so the refusal names WI-BE8. Gemini's thinking `off` is refused (the adapter has no off level). |
| Not done (deferred) | Any real-sample run (owner, WI-EV2). `--vision-primary-sim` (WI-BE7), crops (WI-BE8). Thinking-token and reasoning counters from vLLM are not in `vision_eval` (the server reports none; `thinking_leak` shows as `call:invalid_output` with detail in `failures`). |

Checks: offline suites 1008 passed, 0 xfailed (981 + 27 new). New `tests/unit/test_accuracy_vision_eval.py` (27): default arm = app reader and same keys; repeat/thinking/provider change the key; 10 refusal cases; URL normalisation; `--vision-primary-sim`/crops refused before any read and removed policies rejected by the parser; vLLM via `httpx.MockTransport` calls only `{base}/models` and `{base}/chat/completions`, thinking off, one image per call, no `Authorization`, no Gemini client, repeats stored and checked; `CONTEXT_TOO_SMALL` sends no POST; no base URL refuses; `thinking_leak` keeps its code only; every `vision_eval` metric on synthetic truth (header/line cells, unreadable listed vs omitted, wrong-when-answered, not-in-OCR-text, LLM×OCR agree-wrong, both policies, a check-failed total never accepted, both false-safe simulations, repeat variance, latency p50/p95, tokens, cost from a synthetic price file, failures by code, no values in the block); end-to-end bench: default report has no `vision_eval` and `--vision-eval --agreement-policy llm_x_ocr` keeps `report_hash`, `vision_hash`, `consensus_hash` and the vision block identical. Ruff (app, core, tests, scripts) clean; mypy `--strict` (37 files) clean; lint-imports 3 kept; core purity; rules hash `1e40652fe689…` verified; engine version vs HEAD passes. Nothing committed; no `docs/ReconBench_*.md` edited.

## D68 — WI-EV2 E5 smoke test approval: 5 documents on the company vLLM server (16 Oct 2026)

Owner approval in chat, 16 Oct 2026: "lets only smoke test 5 documents" (the owner delegated vLLM budget choices to Claude because the server is the company's own; Gemini is not included). Scope: arm E5 only (Qwen3.5-9B as served, `qwen3.5:9b`, thinking off, `qwen_card_nonthinking`, full pages, one page per call, 1 read, no repeats) at `http://43.242.226.63/vllm/v1` over the company VPN. Dataset: the `work/` truth set; the bench picks the 5 most informative PDF documents (`--max-provider-calls 5`). Budget: at most 15 masked page images (`--max-provider-pages 15`; nothing is sent if the 5 documents need more), concurrency 1, 300 s timeout per call. Pre-checks: `GET /models` from host and from the worker container → HTTP 200, `qwen3.5:9b`, `max_model_len` 8192. Output: counts only (report in `work/accuracy-reports/ev2-e5-smoke.json`). No full E5/E7 arm and no Gemini call are approved.

Result (counts only, `work/accuracy-reports/ev2-e5-smoke.json`): 5 distinct requests answered (`sent` 5; 9 bench documents incl. copies, 14 pages budgeted), 1 call failed `invalid_output`/`at_output_cap` (a 2-page document, one page used the whole 3,550-token budget; not recorded). Hard metrics 0/0/0/0, gate PASS. On the same 9 documents (all scan-page): header LLM 72/80 vs OCR 62/80; line amounts 25/25 vs 17/25; wrong-when-answered 1/73 (critical 0/39); not-in-OCR-text 0/73; LLM×OCR agree-wrong 0 (60 agree-correct, 6 disagree); auto-accept (`single`) precision 100% in every field type, Wilson 95% lower bounds 0.44–0.88 (small n); false-safe simulated 0 (both variants); handwritten refs 0/12, marketplace order 0/4; 7 null answers, all silently omitted (0 listed `unreadable`); latency per document p50 88 s, p95 134 s; tokens in 53,348 / out 27,765; 3 answers over 4,096 output tokens (max 5,468). Not decision-grade (5 documents, chosen as the most informative, not random). Follow-ups for the owner: dense pages hit the per-call output budget (WI-BE8 crops or a smaller image, WI-OPS1); p95 is above the 120 s/document local target; the prompt does not get Qwen to list unreadable fields or handwritten refs.

## D69 — WI-EV2 prep: one request per truth invoice; failed requests named by ID (16 Oct 2026)

The D68 smoke test spent its 5 calls on only 3 truth invoices (other files and bundle pages of the same invoice took slots). No server call in this item; offline only.

| Decision | Choice |
|---|---|
| Selection (`scripts/accuracy_bench.py` `_priority_subset`) | With `--max-provider-calls N`, each of the N requests is a different truth document: another file or bundle page of a truth document already chosen, or of one that already has a recorded answer (`covered`, from the targets not missing), takes no slot and is not sent. Exact copies (same request key) still ride along free and reuse the answer. Documents without truth each count as their own. Without a limit nothing changes. Order unchanged (handwritten refs, lines, other truth, no truth). |
| Failure IDs (`scripts/accuracy_vision.py` `send`) | Each failed or skipped request in `failures` now carries `document` (`<file_id>#<index>`) and `truth_id`: truth-file IDs only, never a value or file name. Only provider runs have failures, so default reports are unchanged. |
| Dry run (no network, `--max-provider-pages 0`) | `--vision-provider vllm --max-provider-calls 15`: 15 new invoices not covered by D68, 20 bench documents counting free copies, at most 34 page images; nothing sent. |

Checks: offline suites 1009 passed, 0 xfailed (+1 selection test; two failure-entry assertions updated); ruff clean; synthetic default-bench guard unchanged (`report_hash` `c728aec0…`, `vision_hash` `a9d9f53c…`, `consensus_hash` `c9392395…`, whole report identical except `timing`). Core, app, rules hash and goldens untouched. Next (needs the owner's go): E5 on these 15 invoices, `--max-provider-calls 15 --max-provider-pages 34`, 1 read, about 25–35 min on the shared server.

Owner go in chat, 16 Oct 2026: "Go: run E5 on the 15 new invoices, --max-provider-calls 15 --max-provider-pages 34, 1 read." Same arm and server as D68 (Qwen3.5-9B as served, thinking off, `qwen_card_nonthinking`, full pages, one page per call, concurrency 1); budget 15 distinct requests, at most 34 masked page images; report `work/accuracy-reports/ev2-e5-15.json` (counts and truth-file IDs only).

## D70 — WI-EV2 E5 result on 15 more invoices (16 Oct 2026)

Run per the D69 go (`work/accuracy-reports/ev2-e5-15.json`, counts and truth-file IDs only). `sent` 15, no call failed, hard metrics 0/0/0/0, gate PASS. With the D68 answers the report scores 24 bench documents = 20 distinct requests over 18 distinct truth documents (copies counted again; not random, not decision-grade).

| Metric (LLM vs OCR, same documents) | Result |
|---|---|
| Headers, scan pages | 132/154 vs 115/154 |
| Headers, digital PDF pages (bundle POs/GRNs) | 14/30 vs 18/30 |
| Line cells, scan pages: amount / qty / rate / description | 44/50 vs 38/50; 38/44 vs 34/44; 42/48 vs 34/48; 23/47 vs 31/47 |
| Wrong when answered | 1/147 (critical 0/78) |
| Auto-accept (`single`) | precision 100% in every field type; Wilson 95% lower bounds 0.80–0.93 (reference 0.57, n=5) |
| False-safe simulated / LLM×OCR agree-wrong | 0 / 0 |
| Not in OCR text | 6/147 (upper bound on invention) |
| Unreadable → null | 37 nulls, all silently omitted |
| Handwritten refs / marketplace order | 0/14 / 0/5 |
| Latency | 17–67 s per page; per document p50 41 s, p95 244 s (a 5-page invoice) |
| Tokens | in 181,679 / out 82,226 |
| Unusable answers | 3 truth invoices (S01-INV 2 pages, S08-INV 5 pages, S10-INV 1 page; 5 bench documents with copies): every page answered as JSON, but at least one page failed the strict v4 parse, so the merge recorded an empty answer (`invalid_output`). The raw page answers were not kept, so the failing schema rule is unknown. |

Against the WI-EV2 rule on scans: (a) headers +11 points and line amounts +12, rates +17, quantities +9 (pass except quantities); (b) pass; (c) 0.7% (pass); (d) p95 244 s > 120 s (fail); (f) not measured (1 read). Follow-ups: keep per-page raw answers and the strict-parse error location (no values) when a merge fails, then re-read the 3 invoices; smaller images / crops for latency and dense pages; prompt work for `unreadable`, handwritten refs and descriptions.

## D71 — Keep page answers on a failed merge; re-read the 3 unusable invoices (16 Oct 2026)

Owner go in chat, 16 Oct 2026: "Yes: keep per-page answers and the parse error when a merge fails, then re-read only the 3 failed invoices (S01, S08, S10-INV)."

| Decision | Choice |
|---|---|
| Failed merge record (`scripts/accuracy_vision.py` `_merged`) | When a per-page answer fails the strict v4 parse, the recorded answer stays empty (still `invalid_output`) and now also keeps `page_outputs` (raw page answers, `work/` cache only) and `merge_error.pages[]` = page + `parse_errors`. |
| `parse_errors(text)` | The pydantic errors behind `InvalidOutput` as `location: type` patterns with list positions as `*` (`lines.*.hsn_sac: string_type`), `include_input=False`: no value is ever kept or reported. A non-schema failure gives its fixed reason. |
| Report | `vision_eval.invalid_answer_rules`: pattern → pages (counts only). |
| `--vision-retry-invalid` | Sends again only requests whose recorded answer fails the strict parse (`unusable_keys`); no new document is added; each key is sent once per run (copies reuse it) and the record is overwritten. The page count includes the retried requests. |
| Re-read budget | 3 truth invoices (S01-INV 2 pages, S08-INV 5, S10-INV 1): 3 requests, 8 page calls (the bench counter shows 15 because it also counts 2 free copies), `--max-provider-calls 3 --max-provider-pages 15`, 1 read, same arm and server as D68. |

Checks: offline suites 1011 passed, 0 xfailed (+2 tests); ruff clean; synthetic default-bench guard unchanged (`c728aec0…`, `a9d9f53c…`, `c9392395…`, report identical except `timing`); offline dry run (`--network none`, budget 0) selected exactly those 5 bench documents.

Re-read result (`work/accuracy-reports/ev2-e5-retry.json`): `sent` 3, hard metrics 0/0/0/0, gate PASS; all 3 answers are **still** unusable, with the same rules broken (`invalid_answer_rules`): `fields.*.text: string_too_short` on 4 pages across all three invoices (an empty value where the model should have listed the field as unreadable; one page has it twice, 5 counted in total), and `lines.*.description: string_too_long` on 1 page of S08-INV (a description over 500 characters). In S08-INV only 2 of its 5 pages break a rule, yet the whole document is lost. Cause: the `openai_json_schema` dialect sent to vLLM carries no `minLength`/`maxLength` (`contracts._string` emits only `type`), so the server's structured output cannot prevent them, and the strict local parse rejects the whole answer for one bad item. Other numbers unchanged from D70 (the 3 invoices still have no usable answer). Owner decision needed: (A) send `minLength`/`maxLength` in the vLLM dialect (vLLM cache keys change; Gemini golden unaffected; needs a server check that 0.23 enforces them), and/or (B) a per-item tolerant vision parse that drops only the bad items (counted in `dropped`, never turned into values) instead of the whole answer.

## D72 — Owner focus: vLLM (Qwen3.5-9B) accuracy; deterministic OCR/rules work frozen (16 Oct 2026)

Owner decision in chat, 16 Oct 2026: "freeze deterministic OCR/rules work (WI-D1, WI-D2); OCR + rules stay only as the independent second reader (D42), fallback and the not-in-OCR-text check. Focus on vLLM (Qwen3.5-9B) accuracy. Latency is out of scope until accuracy is decided. Order: (1) answer-format root fix, (2) Qwen prompt v4.qwen improvements (unreadable list, handwritten refs, descriptions), (3) repeats x3, (4) bigger truth set (owner), (5) WI-BE7 merge."

| Decision | Choice |
|---|---|
| Frozen | WI-D1 (FP WI-4…WI-7 scan rules) and WI-D2 (forced OCR, >20-page warning, UTF-8 decode). No new deterministic extraction work until the owner reopens it. |
| OCR + rules keep | The independent second reader for D42, the fallback when no LLM answer is usable, and the not-in-OCR-text check. |
| Focus | Accuracy of the company vLLM arm (Qwen3.5-9B, §0.5). WI-EV2 criterion (d) latency is not judged until accuracy is decided. |
| Order | (1) answer-format root fix (D71 options A and B, D73), (2) `vision.prompt.v4.qwen` improvements (unreadable list, handwritten refs, descriptions), (3) repeats ×3, (4) bigger truth set (owner), (5) WI-BE7 merge. §0.6 still binds: Gemini and vLLM are separate providers. |

## D73 — Answer-format root fix: per-item tolerant vision parse (B) + length limits in the vLLM schema (A) (16 Oct 2026)

Step (1) of D72, both D71 options. No server call and no Gemini call in this item; the re-score below was offline (`docker run --network none`, `--max-provider-pages 0`) from the `page_outputs` D71 kept in `work/accuracy-cache/vision`.

| Decision | Choice |
|---|---|
| (B) Tolerant parse (`reconbench_app/ai/contracts.py` `parse_output` → `_vision_items`) | For `h6_vision` only: each `fields`, `lines`, `annotations` and `unreadable` item is checked against its own item model. An item that breaks a rule is left out and counted as `schema:<location>: <type>` (list positions `*`, first broken rule per item, no value kept). It is never repaired, truncated or turned into a value. A field whose **only** problem is an empty `text` (`fields.*.text: string_too_short`) also becomes an `unreadable` entry with its `name`, `page`, `box_2d` (still counted under that reason); an empty text together with another broken rule is only dropped; an empty annotation text is only dropped. The top level stays strict and rejects the whole answer: not JSON, duplicate keys, not an object, missing or extra top-level keys, wrong top-level types, `doc_kind`, every `pages` item, list caps (`fields` 400, `lines` 500, `annotations` 60, `unreadable` 200), byte limit. Text hooks H1–H4 and `provider_test` are unchanged (strict). |
| Same rule for both providers | One code path: Gemini answers go straight to `parse_output`; vLLM per-page answers go through `merge_page_answers`, which now checks each page's top level with the same parse and merges the page's items **as answered** (off-page items still dropped by the core merge), so the parse of the merged answer is the single place a bad item is dropped and counted. The counts ride on `VisionOutput.schema_dropped` (a private attribute: not in `model_dump`, not in stored answers) and `validate_vision` adds them to `VisionCheck.dropped`, so they appear in the bench `vision.dropped`, the evidence `dropped` and shadow `dropped_by_reason`. Re-validation from a stored, already-cleaned answer (`ingestion`, `consensus`) has nothing left to drop. |
| (A) vLLM schema limits (`_openai_dialect`) | Every free-text string in the `openai_json_schema` dialect now carries `minLength`/`maxLength` read from the pydantic field limits (e.g. `fields.text` 1–500, `lines.description` ≤500, `lines.rate` ≤60, `pages.title` ≤200), inside the nullable `anyOf` where the key is optional. Enum strings get none (the enum bounds them). Array caps (`maxItems`) are not added. The `gemini` dialect is not touched: byte-identical, golden `7ede1208…` test passing. |
| Cache keys | (A) changes the vLLM response schema, which is part of `payload_digest`, so **every vLLM cache key changes**: offline dry run with the new schema shows all 39 vLLM bench documents `missing` (67 pages). All Qwen answers need a fresh read before they count again. Gemini keys are unchanged. Whether vLLM 0.23's structured output enforces the length keywords is not yet verified; the next read shows it (expected `schema:` drops = 0). |
| Bench (`scripts/accuracy_vision.py`) | `stored_answer`: a record whose `output_text` is empty but has `page_outputs` (D71 failed merge) is merged again offline with the current rule; `check_answers` and `unusable_keys` use it, so `--vision-retry-invalid` no longer resends a record that now merges. The old `merge_error` counts in `invalid_answer_rules` only when the answer is still `invalid_output`. `parse_errors` uses the shared `contracts.error_patterns` and now reports only whole-answer (top-level) failures. |
| Unchanged | `backend/reconbench_core` (rules hash `1e40652fe689…` verified, engine version check vs HEAD passes, `check_reading`/`merge_page_readings` untouched), Gemini dialect + golden, D65/D66 consent fingerprints (pin tests passing), APIs (no OpenAPI/types drift run: no API changed), migrations. |

Offline re-score of the 3 unusable invoices (`work/accuracy-reports/ev2-e5-rescore.json`, old schema keys, counts only; before = `ev2-e5-retry.json`):

| Count | Before (D71) | After |
|---|---|---|
| Vision outcomes (39 PDF bench documents) | read 24, `invalid_output` 5, missing 10 | read 29, `invalid_output` 0, missing 10 |
| S01-INV, S08-INV, S10-INV (5 bench documents with copies) | 0 usable | 5 usable (all three invoices) |
| `invalid_answer_rules` | `fields.*.text: string_too_short` 5, `lines.*.description: string_too_long` 2 | none |
| Schema drops (bench documents, copies counted again) | — | `schema:fields.*.text: string_too_short` 9 (each now an `unreadable` entry), `schema:lines.*.description: string_too_long` 2 |
| Headers, scan pages: LLM / OCR | 146/184 / 133/184 | 178/228 / 173/228 (the 5 added documents: LLM 32/44, OCR 40/44; LLM wrong +2) |
| Line amount / qty / rate / description, scan pages, LLM | 44/62, 38/56, 42/60, 23/47 | 57/87, 50/80, 54/84, 24/72 (added: 13/25, 12/24, 12/24, 1/25) |
| Same cells, OCR | 38/62, 34/56, 34/60, 31/47 | 62/87, 42/80, 42/84, 37/72 |
| Hard metrics | 0/0/0/0 | 0/0/0/0 |
| Hashes | `report_hash` `09115aa2…`, `vision_hash` `70de3f02…`, `consensus_hash` `2c3461f9…` | `report_hash` `09115aa2…` (same), `vision_hash` `136df320…`, `consensus_hash` `397c676c…` |

`vision_hash` (and the bench consensus block that uses the vision readings) changed only because the 5 previously rejected answers are now partly usable. On these three invoices Qwen is behind OCR (headers 32/44 vs 40/44, line amounts 13/25 vs 24/25); S08-INV alone has 5 of their 8 pages, and the set is small and not random: not decision-grade.

Checks: offline suites 1040 passed, 0 failed, 0 xfailed (1011 + 28 in new `tests/unit/test_vision_tolerant_parse.py` + 1 offline re-merge test; 2 tests in `test_ai_vision.py` and 2 in `test_accuracy_vision_eval.py` changed from "one bad item rejects the answer" to "the item is dropped and counted"); ruff (app, core, tests, scripts) clean; mypy `--strict` (37 files) clean; lint-imports 3 kept; core purity; rules hash; engine version vs HEAD; PG integration (disposable `postgres:16.15` on 15499, migrated to 0027 + queue schema, stopped afterwards) `test_ai_vision`, `test_ai_runner`, `test_ai_shadow` 50 passed. Synthetic default-bench guard (1 synthetic invoice, recorded Gemini answer, scratch dirs; run with and without this change): `report_hash` `d054b29d…`, `vision_hash` `822e366e…`, `consensus_hash` `cca27c5e…` identical before/after and with `--vision-eval`, `dropped` `{}`. (These differ from the `c728aec0…` set quoted in D67–D71 because the guard was rebuilt from the `_bench` test helper; both sides of this comparison use the same setup.) Nothing committed; no `docs/ReconBench_*.md` edited.

Next (needs the owner's go): a fresh read of every vLLM bench document under the new schema. Proposed budget (vLLM budget is Claude's call per the owner; accuracy first, latency ignored): all 39 PDF bench documents, at most 70 page images (67 needed, copies reuse answers), 1 read, same arm and server as D68 (Qwen3.5-9B `qwen3.5:9b`, thinking off, `qwen_card_nonthinking`, full pages, one page per call, concurrency 1, 300 s timeout). Approval line: "Go: fresh E5 read of all vLLM bench documents under the D73 schema, --max-provider-pages 70, 1 read." Command (Git Bash, VPN on, `RB_AI_VLLM_ENDPOINTS=http://43.242.226.63/vllm/v1` exported):
`MSYS_NO_PATHCONV=1 make accuracy-bench-vllm BENCH_ARGS="--vision-base-url http://43.242.226.63/vllm/v1 --max-provider-pages 70 --vision-eval --workers 1 --out /app/work/accuracy-reports/ev2-e5-d73.json"`

## D74 — Fresh E5 read under the D73 schema: approval (16 Oct 2026)

Owner go in chat, 16 Oct 2026: "Go: fresh E5 read of all vLLM bench documents under the D73 schema, --max-provider-pages 70, 1 read." Scope: arm E5 only (Qwen3.5-9B as served, `qwen3.5:9b`, thinking off, `qwen_card_nonthinking`, full pages, one page per call, concurrency 1, 300 s timeout) at `http://43.242.226.63/vllm/v1` over the company VPN. Dataset: all 39 PDF bench documents of the `work/` truth set (67 page images needed, copies reuse answers), at most 70 page images, 1 read, no repeats, no Gemini call. Output: counts and truth-file IDs only, `work/accuracy-reports/ev2-e5-d73.json`.

Result (`work/accuracy-reports/ev2-e5-d73.json`, counts only): all 39 PDF bench documents read (67 pages), 0 failed calls, 0 `invalid_output`, `invalid_answer_rules` none, **0 `schema:` drops** (vLLM 0.23 structured output enforced the D73 length limits on every answer), hard metrics 0/0/0/0, `report_hash` `09115aa2…` unchanged, `vision_hash` `b06453ed…`. All 39 documents are scan-page documents in this set (scan_page = all_pdf).

| Metric (LLM vs OCR, same 39 documents) | Result |
|---|---|
| Headers | 255/313 vs 242/313 (wrong 1 vs 24) |
| Line amount / qty / rate / description | 90/123 vs 94/123; 79/114 vs 65/114; 85/118 vs 67/118; 47/108 vs 59/108 |
| Wrong when answered | 1/256 (critical 0/137) |
| Auto-accept (`single`) | precision 100% in every field type (amount 89/89, doc_date 35/35, doc_no 27/27, gstin 53/53, reference 12/12, vendor_name 32/32) |
| LLM×OCR | 205 agree-correct, 21 disagree, 0 agree-wrong |
| Not in OCR text | 11/256 |
| False-safe simulated | `flags_kept` 0; **`review_cleared` 1** (one document where every critical truth field was accepted while the rules' lines are wrong; D67 stand-in until WI-BE7) |
| Unreadable → null | 57 nulls, all silently omitted (0 listed) |
| Handwritten refs / marketplace order | 1/18 found (17 missed) / 0/5 |
| Tokens / latency | in 257,747, out 114,562; per document p50 62 s, p95 253 s (not judged, D72) |

Compared with D70 (fewer documents, earlier schema): the vLLM arm now beats OCR on headers, quantities and rates, is slightly behind on line amounts (−4) and clearly behind on descriptions (−12). Next per D72: step (2) `vision.prompt.v4.qwen` (unreadable list, handwritten refs, descriptions); the `review_cleared` case should be examined then.

## D75 — D72 step (2): Qwen prompt `vision.prompt.v5.qwen` from an offline diagnosis of the D74 answers (16 Oct 2026)

No server call and no Gemini call. Part 1 read the D74 cached answers (`work/accuracy-cache/vision`) and the truth file offline (`docker run --network none`, `--max-provider-pages 0`, the D74 arm and keys) with a counts-only script (`work/diag_d75.py`, git-ignored). Counts are bench documents (copies counted again, as in D74); no value or file name was printed.

**Part 1 (a) — the 57 null header answers.** Every one has a truth value (printed); 51 of 57 also occur in the OCR text (folded match), 6 do not (4 vendor names, 1 grand total, 1 IGST). 0 were listed `unreadable`. By field: `doc_no` 12, `grand_total` 9, `taxable_total` 8, `vendor_name` 7, `buyer_gstin` 5, `doc_date` 4, `cgst` 3, `sgst` 3, `po_refs` 3, `igst` 2, `vendor_gstin` 1. What actually happened:

| Cause | Count | Detail |
|---|---|---|
| Returned under the right name, removed by the checks | 28 | `taxable_total` 7, `vendor_name` 7, `doc_no` 5, `buyer_gstin` 4, `cgst` 2, `sgst` 2, `po_refs` 1; 24 had the right value among the items. In 24 of the 28 the per-page answers (one page per call) gave several same-name items with different values, which the `conflict` rule drops together |
| Returned under another name | 18 | `doc_no` as `po_ref` on 4 POs and as `grn_ref` on 3 GRNs; `grand_total` as `taxable_total` 8 (4 PO, 3 GRN, 1 invoice); `igst` as `taxable_total` 1; `po_refs` as `other_ref` 1; `vendor_gstin` as `grn_ref` 1 |
| Only inside a line | 4 | `cgst`, `sgst`, `igst`, `taxable_total` 1 each |
| Not returned anywhere | 7 | `doc_date` 4, `buyer_gstin` 1, `grand_total` 1, `po_refs` 1 |

So only 7 of the 57 "silently omitted" values were really left out; the rest were read but lost to naming (18), line placement (4) or the cross-page conflict rule (28).

**(b) Descriptions (108 truth cells; LLM 47 correct).** Wrong 40: extra text merged in 34 (truth text at the start in 22; the extra part is ≥70% of the answer in 16; it holds the row's HSN code as a token in 10, always with an HSN/SAC word, the row's quantity in 6, a part/model/code word in 6, only other 4+-digit codes in 3), two rows joined 3, different text 3 (similarity <0.1 in 2, 0.3 in 1); cut short 0; wrong row 0. Missing 21: all because the row was not aligned (no LLM row with that amount), 12 in documents where the LLM returned fewer rows than truth and 9 where it returned as many or more; in none of the 21 did another LLM row carry the right description; "row found, no description" 0.

**(c) Handwritten refs (18 in 13 documents; 1 found, typed `po_number`).** The 17 misses: 7 in documents whose answer had no annotation at all, 10 in documents whose answer had annotations that do not contain the ref (7 of the 13 documents had ≥1 annotation; their 11 annotations: handwritten/date 4, handwritten/note 4, handwritten/po_number 2, stamp/invoice_number 1, all 11 accepted). Typed as something else: 0; returned as a printed field or inside a line: 0. None of the 17 appears in the OCR text (handwritten only). Best similarity of any returned annotation or field text to the ref: 0.3–0.5 in 15, 0.6 in 1, 0.8 in 1 (one probable misread). Patterns: `DD-DD/DD-DDD` 8, `DD-DDD` 6, `LL/DD-DD/DD-DDD` 1, `LLL/LLLL/DD-DD/DD-DDD` 1, `LLLLLLLLLL/DD-DD/DD-DD` 1. In short: the ref is not returned at all.

**(d) Marketplace orders (5, pattern `DDD-DDDDDDD-DDDDDDD`).** All printed (in the OCR text) and all returned with their value: as `po_ref` 4, as `sales_order_ref` 1; the label was returned every time and contains "ORDER", never "PO"/"Purchase"; `marketplace_order_ref` was never used. Cause: rule 5 of `v4.qwen` never names `marketplace_order_ref` (the Gemini rule 5 does).

**(e) The simulated `review_cleared` document: S08-INV** (invoice, 5 pages). Critical truth fields `doc_no`, `doc_date`, `vendor_gstin`, `grand_total`, `po_refs`: all 5 accepted under `single` (every standalone check passed) and all 5 correct; `buyer_gstin`, `cgst`, `sgst`, `vendor_name` also accepted and correct. The rules reader flags the document (alone it is not false-safe); the D67 stand-in clears the flag because every critical field was accepted, and the document is then false-safe only through the **rules' lines** kept in the simulation: 10 rows for 10 truth rows, 5 matching and 5 spurious (3 truth amounts are not among the rules' rows; 2 rows have the amount but another quantity or rate), and the rules' Σ line amounts ≠ the truth taxable value. The LLM lines are not right either (9 rows, 6 matching, 3 spurious, Σ ≠ taxable), and no standalone line-sum check fired because the LLM document has no `taxable_total`, so there was nothing to compare. Consequence for WI-BE7 (not changed now): a merged document must keep its review flag unless Σ lines = taxable holds, and a missing taxable value must never clear it.

**Part 2 — changes.**

| Decision | Choice |
|---|---|
| Prompt `vision.prompt.v5.qwen` (`reconbench_app/ai/prompts.py`: `VISION_SYSTEM_QWEN_V5`, `QWEN_EXAMPLE_V5`, variant `qwen_v5`) | v4.qwen rules 1–4 kept word for word; new or rewritten: **5** `doc_no` is the document's own number (PO number on a PO, GRN number on a GRN), from (a), 7 renamed; `po_ref` only for the buyer's PO; on a marketplace invoice (Amazon, Flipkart and the like) the Order Number / Order ID is `marketplace_order_ref`, never `po_ref` or `sales_order_ref`, from (d). **6** `grand_total` is the final total also without tax; totals only from the totals block, never page subtotals or carried-forward amounts, from (a): 9 renamed and the cross-page conflicts. **7** description = the full item text of that one row, wrapped lines joined, never the quantity/rate/amount/HSN-SAC/unit columns or another row's text, from (b). **8** every handwritten or stamped mark goes in `annotations` even when it repeats a printed number, each with `kind` and one of the schema's `looks_like` values (`po_number`, `grn_number`, `invoice_number`, `date`, `amount`, `initials_or_signature`, `note`, `unknown`; the schema has no `po_ref`/`grn_ref` value, and the vLLM schema is enforced by decoding, not shown to the model, so the list is in the text), from (c). **9** unreadable: a printed value that cannot be read with certainty is not guessed, gets no value and is listed as `{name, page, box_2d}`; never left out silently; one inline example (`po_ref`, the same entry as in the example answer). Rules 5 (own number) and 6 go beyond the four requested items; they come straight from (a) and cost about 90 tokens. The example answer is the v4 one plus one handwritten `SYN-PO-0042` `po_number` annotation; made-up `SYN-…` values only. |
| Size | System text 3,640 bytes (v4.qwen 2,569). Estimated answer budget on a full page (8192 context, 3,500 image tokens, 3 bytes/token, 256 margin): **3,193** tokens (v4.qwen 3,550; floor 1,024; cap 4,096). |
| Reader | `vision_openai_compat.prompt_variant = "qwen_v5"`, so `VISION_PROMPT_VERSIONS["vision_openai_compat"] = "vision.prompt.v5.qwen"`; the bench vLLM arm gets it through `PROVIDER_READERS`. Variant `qwen` (v4.qwen) stays available and byte-identical. Gemini reader, prompt, schema (both dialects) and golden `7ede1208…` untouched. |
| Cache keys | Every vLLM key changes (prompt version, `reader_params.prompt_variant`, payload digest). Offline dry run (`--network none`, budget 0): 39 vLLM bench documents `missing`, **67 page images** needed, nothing sent. Gemini keys unchanged (synthetic key pinned). |
| Consent | vLLM fingerprints change because they cover the vLLM reader's prompt version (WI-BE4: a new prompt needs consent again, as after D38/D40): `single`+vLLM → `9212c85d…`; **shadow+vLLM `cb7d2ee1…` → `e4eef8f4…`** (the D66 pin is updated; keeping it would let a workspace's consent stand for a prompt it never saw). Unchanged and tested with the vLLM reader patched back to v4.qwen: legacy, `off`, `single`+Gemini, shadow+Gemini `bad2261b…`. Workspaces that consented to vLLM `single` or `shadow` must consent again before the worker sends under v5. `consent.py` untouched. |
| Unchanged | `backend/reconbench_core` (rules hash `1e40652fe689…` verified), migrations, APIs (no OpenAPI change), bench scripts. |

Tests (synthetic only): new `tests/unit/test_vision_prompt_v5.py` (19): each new rule in the v5 text and the kept v4 rules; the v5 example is synthetic and parses (all three document types); v4.qwen and Gemini system texts byte-identical per document type and both payloads (text + schema) pinned; schema version; variant and reader version maps; vLLM reader and bench vLLM arm pick v5, default arm is still the Gemini reader; vLLM cache key differs from the v4.qwen key, Gemini key pinned; answer budget ≥ 1,024 (and ≥ 3,000) for invoice/PO/GRN; only the vLLM consent fingerprints change. Updated: `test_vision_v4.py` (variant map), `test_ai_openai_compat.py` (reader variant), `test_ai_shadow.py` (shadow+vLLM pin, renamed `…_are_pinned`).

Checks: offline suites **1059 passed, 0 xfailed** (1040 + 19); ruff (app, core, tests, scripts) clean; mypy `--strict` (37 files) clean; lint-imports 3 kept; core purity; rules hash verified; engine version vs HEAD passes; Gemini golden passing; synthetic default-bench guard `report_hash` `d054b29d…`, `vision_hash` `822e366e…`, `consensus_hash` `cca27c5e…` unchanged, also with `--vision-eval`, `dropped` `{}`; PG integration (disposable `postgres:16.15` on 15499, migrated to 0027 + queue schema, stopped afterwards) `test_ai_vision`, `test_ai_runner`, `test_ai_shadow` **50 passed** (the known `test_ai_h1` failure is in a group not run). Nothing committed; no `docs/ReconBench_*.md` edited.

Next (needs the owner's go): a fresh read with v5.qwen. Budget (Claude's call per the owner; accuracy first, latency ignored): all 39 PDF bench documents, at most 70 page images (67 needed), 1 read, same arm and server as D68 (Qwen3.5-9B `qwen3.5:9b`, thinking off, `qwen_card_nonthinking`, full pages, one page per call, concurrency 1, 300 s timeout), no Gemini call. Approval line: "Go: fresh E5 read of all vLLM bench documents with vision.prompt.v5.qwen, --max-provider-pages 70, 1 read." Command (Git Bash, VPN on, `RB_AI_VLLM_ENDPOINTS=http://43.242.226.63/vllm/v1` exported):
`MSYS_NO_PATHCONV=1 make accuracy-bench-vllm BENCH_ARGS="--vision-base-url http://43.242.226.63/vllm/v1 --max-provider-pages 70 --vision-eval --workers 1 --out /app/work/accuracy-reports/ev2-e5-v5.json"`

Owner go in chat, 17 Oct 2026: "Go: fresh E5 read of all vLLM bench documents with vision.prompt.v5.qwen, --max-provider-pages 70, 1 read." Scope: arm E5 only (Qwen3.5-9B `qwen3.5:9b`, thinking off, `qwen_card_nonthinking`, full pages, one page per call, concurrency 1, 300 s timeout) at `http://43.242.226.63/vllm/v1` over the company VPN; all 39 PDF bench documents of the `work/` truth set, at most 70 masked page images, 1 read, no repeats, no Gemini call. Output: counts and truth-file IDs only, `work/accuracy-reports/ev2-e5-v5.json`.

Result (`work/accuracy-reports/ev2-e5-v5.json`, counts and truth-file IDs only): 67 page images in budget, `sent` 26 requests (copies reuse answers), 4 calls failed: `invalid_output`/`at_output_cap` 2 (a page of a 5-page S08-INV copy and the 1-page S09-PO used the whole 3,193/3,192-token answer budget) and `PROVIDER_UNAVAILABLE` 2 (copies of S08-INV and S05-INV; the shared server). 36 of 39 bench documents read; unread: PO-T2#0 (S09-PO), RESCAN-1#0 (S08-INV), RESCAN-2#0 (S05-INV). 0 `schema:` drops, `invalid_answer_rules` none, hard metrics 0/0/0/0, gate PASS, `report_hash` `09115aa2…` unchanged, `vision_hash` `34df2857…`. Not retried: a retry would go over the approved 70 pages and the two cap failures need a bigger answer budget anyway.

Because v5 read 36 documents, the D74 answers were re-scored offline on the **same 36** (the D74 arm with the reader patched back to `qwen`; `work/diag_d75.py` and a per-document outcome script, counts only). Before/after (LLM unless stated):

| Metric | D74 v4.qwen, 39 docs | v4.qwen, same 36 | v5.qwen, 36 |
|---|---|---|---|
| Headers LLM / OCR | 255/313 / 242/313 | 235/286 / 218/286 | **229/286** / 218/286 |
| Line amount / qty / rate / description | 90/123, 79/114, 85/118, 47/108 | 83/109, 72/100, 78/104, 47/95 | **76/109, 68/100, 69/104, 39/95** |
| Same cells, OCR | 94/123, 65/114, 67/118, 59/108 | 83/109, 58/100, 60/104, 58/95 | same |
| Wrong when answered (critical) | 1/256 (0/137) | 1/236 (0) | **6/235 (4/136)** |
| Nulls / listed unreadable / silently omitted | 57 / 0 / 57 | 50 / 0 / 50 | 51 / 0 / 51 |
| Nulls by cause: conflict-dropped / other name / in a line / not returned | 28 / 18 / 4 / 7 | 24 / 16 / 4 / 6 | 20 / **4** / **14** / **13** |
| Description wrong: extra merged / two rows / other; missing (not aligned) | 34 / 3 / 3; 21 | 30 / 2 / 2; 14 | 33 / 0 / 2; 21 |
| Handwritten refs found | 1/18 | 1/18 (11 annotations returned) | **4/18** (+1 as another annotation; 53 returned) |
| Marketplace order found | 0/5 | 0/5 | **5/5** |
| Auto-accept precision (`single`) | 100% every type | 100% every type | reference **8/11**, doc_date **32/33**, others 100% |
| Not in OCR text / LLM×OCR agree-wrong | 11/256 / 0 | — | 9/235 / 0 |
| Simulated `review_cleared` / `flags_kept` | 1 (S08-INV) / 0 | 0 / 0 | **8** / 0 |
| `schema:` drops / `invalid_output` / `at_output_cap` | 0 / 0 / 0 | — | 0 / 2 / 2 |
| Tokens in / out | 257,747 / 114,562 | — | 238,669 / 102,789 (8 answers over 4,096, max 9,907, document sums) |
| Hard metrics | 0/0/0/0 | — | 0/0/0/0 |

What changed, by rule (same 36 documents):

- **Worked:** rule 5 marketplace (0 → 5/5, all as `marketplace_order_ref`); rule 5 own number and rule 6 final total (nulls under another name 16 → 4; `doc_no` nulls 10 → 6, `grand_total` 9 → 2); rule 8 annotations (every one of the 13 documents with handwritten refs now returns annotations; refs found 1 → 4; the 13 still missed are not returned, 0 in OCR text).
- **Hurt:** CGST/SGST/taxable nulls 11 → 25 (`cgst` 3 → 8, `sgst` 3 → 8, `taxable_total` 5 → 9), now put inside line rows or left out; most likely rule 6's "only from the totals block, never page subtotals" (tax summary tables are not read as totals). Line cells fell (amount −7, rate −9, quantity −4) and descriptions fell (47 → 39): the rule 7 wording did not stop merged text (30 → 33) and rows not aligned rose 14 → 21.
- **New wrong values, accepted under `single`:** `po_refs` 3 (S01-INV, S02-INV ×2): the right PO plus an extra PO-like number read from page 2 of a 2-page invoice (one page per call); `doc_date` 1 (S03-INV, one marketplace copy): the "Order Date" (3 days before the invoice date) taken as `doc_date`, a side effect of the marketplace emphasis. These end the 100% auto-accept precision for `reference` and `doc_date`.
- **`review_cleared` 8:** 3 from the accepted wrong `po_refs` (S01-INV, S02-INV ×2: a real auto-accept risk), 5 from the D67 stand-in keeping the rules' lines (S05-INV ×2, S14-GRN, S15-GRN, S16-GRN: these GRNs now have every critical field because `doc_no` is read, so the rules' line misses show).
- **Budget:** the longer prompt cut the per-page answer budget 3,550 → 3,193; 2 pages hit it (S08-INV was read under v4).
- One read with sampling on: part of every difference may be run-to-run noise (not measured until repeats, D72 step 3), but the 4 new accepted wrong values have specific causes.

Verdict: **v5.qwen is not better than v4.qwen overall** (headers −6, every line cell lower, 4 accepted critical errors where v4 had 0), although three of its rules clearly work. The vLLM reader still points at v5.qwen in the code; v4.qwen is intact. Proposed next step (owner decision): a `v6.qwen` = v4.qwen plus only the rules that worked (marketplace order field, own `doc_no`, `grand_total` without tax, handwritten marks in annotations with `looks_like`), plus "doc_date is the invoice date, never the order date" and "po_ref only from a field labelled as the buyer's PO"; without the totals-block sentence and the description wording; keep it short to restore the answer budget. Then read v4 and v6 three times each (repeats) so the difference can be told from noise. Separately for WI-BE7: never auto-accept a `po_refs` set that grew across pages, and never clear review without Σ lines = taxable.


## D76 — Qwen prompt `vision.prompt.v6.qwen` (v4 + what worked in D75) and the repeats ×3 proposal (17 Oct 2026)

Owner go in chat, 17 Oct 2026: "Yes: write vision.prompt.v6.qwen = v4.qwen plus only the rules that worked, plus the order-date and po_ref guards, point the vLLM reader at it, and propose the repeats x3 comparison of v4 vs v6 with its page budget." No server call and no Gemini call in this item.

| Decision | Choice |
|---|---|
| Prompt `vision.prompt.v6.qwen` (`prompts.py`: `VISION_SYSTEM_QWEN_V6`, variant `qwen_v6`) | v4.qwen with its 9 rules; rules 1, 2, 3, 6 (lines), 8 (unreadable) and 9 kept word for word. Added from D75 because they worked: rule 4 continues "`doc_no` and `doc_date` are this document's own number and date (on a purchase order its PO number, on a goods receipt its GRN number), never an order, due or delivery date. `grand_total` is the final total, also when there is no tax" (own number + final total; the date clause is the **order-date guard** for the S03-INV error); rule 5 "po_ref only for a number **labelled** as the buyer's purchase order (PO No., Buyer's Order No.)" (the **po_ref guard**, Gemini's v3 wording) + the marketplace sentence (`marketplace_order_ref`, never `po_ref`); rule 7 is v5's rule 8 (every handwritten or stamped mark in `annotations` with `kind` and `looks_like`). Left out because they hurt or did nothing: the totals-block sentence, the description wording, v5's longer unreadable rule. Example answer: the v5 one (with the synthetic handwritten `SYN-PO-0042` annotation). |
| Size | System text 3,288 bytes (v4 2,569, v5 3,640); full-page answer budget **3,311** tokens (v4 3,550, v5 3,193). Two D75 pages used the whole 3,193; dense pages may still hit 3,311 (the real fix is WI-BE8 crops or a smaller image). |
| Reader | `vision_openai_compat.prompt_variant = "qwen_v6"` → `vision.prompt.v6.qwen`. Variants `qwen` (v4) and `qwen_v5` stay available and byte-identical (pinned). Gemini reader, prompt, schema and golden `7ede1208…` untouched. |
| Bench (`scripts/accuracy_vision.py`, `accuracy_bench.py`) | New `VisionArm.prompt_variant` / `--vision-prompt-variant {gemini,qwen,qwen_v5,qwen_v6}` (default: the reader's own). The request key's prompt version now comes from the variant actually sent (`VISION_PROMPT_VARIANTS[reader.prompt_variant]`, identical to `prompt_version(HOOK, reader=name)` for every app reader), so `--vision-prompt-variant qwen` finds the D74 answers again: offline check, all 39 documents `read`, headers 255/313 as in D74. Refused before any read: a Qwen variant with Gemini, `gemini` with vLLM, an unknown name. `vision_eval.arm` and the printed summary now show `prompt_version`. |
| Cache keys | Every vLLM key changes again (v6). Gemini keys unchanged. |
| Consent | vLLM fingerprints follow the prompt version (as D75): `single`+vLLM `32824ed7…`, shadow+vLLM `7ee9e281…` (v4 `cb7d2ee1…`, v5 `e4eef8f4…`/`9212c85d…`, all pinned per variant). Legacy, `off`, `single`+Gemini and shadow+Gemini `bad2261b…` identical under every variant. Workspaces with vLLM consent must consent again. |
| Unchanged | `backend/reconbench_core` (rules hash `1e40652fe689…`), migrations, APIs. |

Tests (synthetic): new `tests/unit/test_vision_prompt_v6.py` (20): v6 = v4 rules word for word where kept plus each added rule and guard; the v5 rules that hurt are absent; example synthetic and parses (3 doc types); v5 system texts pinned; answer budget v5 < v6 < v4 and ≥ 3,300; variant and reader maps; vLLM keys for v4/v5 pinned and v6 different, Gemini key pinned; the bench vLLM arm reads v6 and with `prompt_variant="qwen"` rebuilds the D74 key; refusals; only the vLLM fingerprints follow the variant, pinned per variant. `test_vision_prompt_v5.py` keeps only the v5 text tests (12; reader/key/consent tests moved to v6). Updated: `test_vision_v4.py`, `test_ai_openai_compat.py`, `test_ai_shadow.py` (shadow+vLLM pin → `7ee9e281…`).

Checks: offline suites **1072 passed, 0 xfailed**; ruff (app, core, tests, scripts) clean; mypy `--strict` (37 files) clean; lint-imports 3 kept; core purity; rules hash verified; engine version vs HEAD passes; synthetic default-bench guard `d054b29d…` / `822e366e…` / `cca27c5e…` unchanged (also with `--vision-eval`); PG integration (disposable `postgres:16.15`, 0027 + queue schema, stopped) `test_ai_vision`, `test_ai_runner`, `test_ai_shadow` 50 passed. Offline dry runs (`--network none`, budget 0): v6 `--repeat 3` needs **201** page images (39 documents × 3 reads); v4 `--vision-prompt-variant qwen --repeat 3` needs **134** (repeat 0 is the D74 read, already recorded). Nothing committed; no `docs/ReconBench_*.md` edited.

**Proposal: repeats ×3, v4.qwen vs v6.qwen** (D72 step 3; vLLM budget is Claude's call per the owner; accuracy first, latency ignored). Two bench runs, one provider and one prompt each (§0.6), same arm and server as D68 (Qwen3.5-9B `qwen3.5:9b`, thinking off, `qwen_card_nonthinking`, full pages, one page per call, concurrency 1, 300 s timeout), all 39 PDF bench documents, no Gemini call:

| Run | Reads | Pages needed / budget | Report |
|---|---|---|---|
| A: v6.qwen | 3 new | 201 / `--max-provider-pages 210` | `work/accuracy-reports/ev2-e5-v6-r3.json` |
| B: v4.qwen | D74 + 2 new | 134 / `--max-provider-pages 140` | `work/accuracy-reports/ev2-e5-v4-r3.json` |

Total at most 350 page images (335 needed), about 4 hours on the shared server at the D75 pace. Failed calls are not retried inside this budget. Approval line: "Go: repeats x3 of v6.qwen and v4.qwen on all vLLM bench documents, --max-provider-pages 210 for v6 and 140 for v4." Commands (Git Bash, VPN on, `RB_AI_VLLM_ENDPOINTS=http://43.242.226.63/vllm/v1` exported), run one after the other:
`MSYS_NO_PATHCONV=1 make accuracy-bench-vllm BENCH_ARGS="--vision-base-url http://43.242.226.63/vllm/v1 --repeat 3 --max-provider-pages 210 --vision-eval --workers 1 --out /app/work/accuracy-reports/ev2-e5-v6-r3.json"`
`MSYS_NO_PATHCONV=1 make accuracy-bench-vllm BENCH_ARGS="--vision-base-url http://43.242.226.63/vllm/v1 --vision-prompt-variant qwen --repeat 3 --max-provider-pages 140 --vision-eval --workers 1 --out /app/work/accuracy-reports/ev2-e5-v4-r3.json"`

Scoring: the bench scores repeat 0 and reports `repeat_variance`; each repeat is then scored offline on the documents every read answered (counts only, extending `work/diag_cmp.py` to `repeat_validations`). Proposed decision rule: v6 replaces v4 only if, over the 3 reads, its accepted critical errors are not above v4's (v4 so far: 0) and its mean headers and mean line cells are not below v4's by more than v4's own spread across its 3 reads. Otherwise the vLLM reader goes back to `qwen`.

Owner go in chat, 17 Oct 2026: "Go: repeats x3 of v6.qwen and v4.qwen on all vLLM bench documents, --max-provider-pages 210 for v6 and 140 for v4." Scope: runs A and B above exactly (arm E5, `http://43.242.226.63/vllm/v1` over the company VPN, all 39 PDF bench documents, v6 at most 210 and v4 at most 140 masked page images, no retries, no Gemini call); counts and truth-file IDs only.

Result (counts and truth-file IDs only). Run A `work/accuracy-reports/ev2-e5-v6-r3.json`: 201 page images in budget, `sent` 84, 6 calls failed (`at_output_cap` 3: S10-PO, 1 page, all 3 reads at the 3,310-token budget; `PROVIDER_UNAVAILABLE` 3: S02-INV ×2, S03-INV ×1), 38 of 39 documents read in repeat 0 (PO-T1#0 unread). Run B `work/accuracy-reports/ev2-e5-v4-r3.json`: 134 in budget, `sent` 54, 6 calls failed, all `at_output_cap` at 3,550 (S06-INV ×4 on two copies, S08-INV ×2), 39 of 39 read in repeat 0 (the D74 answers). Both: hard metrics 0/0/0/0, gate PASS, `report_hash` `09115aa2…`, 0 `schema:` drops; `vision_hash` v6 `69aa89fc…`, v4 `4b929e11…`. No retries.

**The server returns identical answers to identical requests.** Byte comparison of the recorded answers: v6, 28 of 28 requests that all 3 reads answered are identical in all 3 reads (`repeat_variance` 0/290); v4, reads 1 and 2 (both 17 Oct) identical in 27 of 27, while the D74 read (16 Oct) differs from them in 22 of 27. This holds even though every request carries the `qwen_card_nonthinking` sampling settings (temperature 0.7, top-p 0.8, top-k 20). So repeats sent in one run measure no sampling noise on this server; the only spread seen is from one day to the next (v4 D74 vs 17 Oct: headers 227 vs 222 on the same 35 documents, every line cell unchanged). The cause (a fixed seed, a cache in front of the server, or a config change on the shared server) is unknown and is the owner's/server's matter (WI-OPS1).

Comparison on the 35 documents every read of both arms answered (counts; "17 Oct" = v4 reads 1 and 2, which are identical; v6 reads 0–2 are identical):

| Metric | v4.qwen D74 read (16 Oct) | v4.qwen, 17 Oct | v6.qwen, 17 Oct |
|---|---|---|---|
| Headers LLM (OCR 213/281) | 227/281 | 222/281 | 222/281 |
| Line amount / qty / rate / description | 81/110, 70/101, 76/105, 45/96 | same | **77/110, 69/101, 72/105, 44/96** |
| Wrong header values | 1 (taxable, S03-INV) | 1 (taxable, S03-INV) | 1 (`doc_date`, S03-INV) |
| Accepted wrong values (`single`) | 0 | 0 | **1** (`doc_date`, S03-INV: the order-date guard did not prevent it) |
| Handwritten refs found | 1/16 | 0/16 | **6/16** |
| Marketplace order found | 0/5 | 0/5 | **5/5** |
| Simulated `review_cleared` | 0 | 0 | 4 (S14-GRN, S14-PO, S15-GRN, S16-GRN: all from the stand-in keeping the rules' lines; v6 now reads every critical field of these documents) |
| Spread across reads | — | 0 (identical) | 0 (identical) |

Header fields, v4 (17 Oct) → v6 on the same documents: gains `doc_no` 9 / losses 4, `grand_total` 6/2, `doc_date` 4 / 0 (+1 now wrong), `po_refs` 3/2, `taxable_total` 2/3 (+1 wrong → missing), `vendor_gstin` 1/0; losses only: `cgst` 6, `sgst` 6, `buyer_gstin` 1. The CGST/SGST loss also appeared under v5 without being tied to the totals-block sentence, so its cause lies in what v5 and v6 share (own-number/final-total sentence, annotations rule, longer example); not isolated.

Whole-run blocks (repeat 0, every document read; not the same document sets): v6 38 documents, headers 244/309 vs OCR 239/309, wrong 3/247 (critical 1/144), auto-accept precision `amount` 74/76 and `doc_date` 37/38, all other types 100%, not in OCR text 13/247, agree-wrong 0; v4 39 documents, 255/313 vs 242/313, wrong 1/256 (critical 0/137), precision 100% every type, not in OCR text 11/256, agree-wrong 0. Tokens in/out: v6 799,539 / 361,746; v4 702,749 / 318,912.

Verdict under the D76 rule: **v4.qwen stays** (v6 adds an accepted critical error and lowers amount, rate and quantity cells; headers equal). v6's marketplace and handwritten gains are real and repeatable but come with the CGST/SGST and line losses. The vLLM reader still points at `qwen_v6` in the code until the owner decides.

What this changes for the plan: (1) on this server one read per variant is enough within a day, and a fair comparison needs the control (v4) re-read **the same day** as the candidate; (2) the next prompt step should change one rule at a time (single-rule variants on v4, each 67 pages plus one same-day v4 control), starting with the marketplace sentence and the annotations rule, which carried the gains; (3) `at_output_cap` hits dense pages under every prompt (S06-INV, S08-INV, S09/S10-PO), so WI-BE8 crops or a smaller image remain needed regardless of the wording.

## D77 — v7.qwen (v4 + the marketplace rule), same-day control read, and cross-page conflict resolution (20 Oct 2026)

Owner go in chat, 20 Oct 2026, replying to the proposal "do 1 and 2 next … step 1 needs about 134 pages (v4.1 plus a same-day v4 control)": "i agree do". Scope of the server reads: arm E5 (Qwen3.5-9B `qwen3.5:9b`, thinking off, `qwen_card_nonthinking`, full pages, one page per call, concurrency 1, 300 s timeout) at `http://43.242.226.63/vllm/v1` over the company VPN, all 39 PDF bench documents, no Gemini call, no retries: (a) v4.qwen control, `--vision-prompt-variant qwen --repeat 4` (only repeat 3 is new: 67 pages), at most 70 page images, `work/accuracy-reports/ev2-e5-v4-ctl.json`; (b) v7.qwen, 1 read, at most 70 page images, `work/accuracy-reports/ev2-e5-v7.json`. Both on the same day, back to back (D76: the server answers identical requests identically within a day, so the control must be read the same day). Counts and truth-file IDs only.

First control attempt: nothing was sent. With `--repeat 4` the bench also counted the repeat 1–2 keys that failed at the output cap on 17 Oct (85 page images > 70), so it stopped before any call; the VPN was not the cause (server reachable from host and container). New bench option `--send-from-repeat K` (`VisionArm.send_from_repeat`, refused unless 0 ≤ K < repeats; not part of any key): repeats before K are scored from the cache, never sent. Control rerun after v7 with `--vision-prompt-variant qwen --repeat 4 --send-from-repeat 3`: 67 page images (dry run), within the 70 approved.

| Decision | Choice |
|---|---|
| Prompt `vision.prompt.v7.qwen` (`prompts.py`: `VISION_SYSTEM_QWEN_V7`, variant `qwen_v7`) | v4.qwen with exactly one rule line changed: rule 5 gains "On a marketplace invoice (Amazon, Flipkart and the like) the Order Number or Order ID is marketplace_order_ref, never po_ref or sales_order_ref." v4's own example; answer budget 3,502 (v4 3,550). Kept as a variant; **the vLLM reader is back on `qwen` (v4.qwen)**, see the result. |
| Cross-page conflicts (`reconbench_core/extraction/vision.py` `check_reading`, `_resolve_conflict`, `_ocr_agrees`) | A single-valued field read with different values no longer always loses every value. Keep the one value the rules reader (OCR) also read; otherwise, when the values come from different pages, the first page's value (totals: the last page's) if that page gave exactly one value; otherwise drop all, as before (so a same-page conflict without OCR agreement is still dropped). The kept value is marked `check_failed: page_conflict` (counted in `check_failed`), so it is a visible suggestion only: excluded from `single`/`llm_x_ocr` auto-accept (`_passed_fields`) and, new, skipped by the consensus job (`reconbench_core/extraction/consensus.py` `vision_readings`), so it can never help verify a field. Other values count under `dropped.conflict` as before. Same rule for both providers (one code path); Gemini answers with cross-page conflicts change the same way. |
| Bench | `--send-from-repeat K` (above). `vision_eval.arm` shows `send_from_repeat`. |
| Rules hash | `1e40652fe689…` → **`1e9af32f2514…`** (reviewed: `check_reading` conflict resolution + consensus skip). Engine version check vs HEAD passes. |
| Consent / keys | Back on v4.qwen, so the vLLM keys are the D74 ones again and the vLLM consent fingerprints are the D65/D66 values (shadow+vLLM `cb7d2ee1…`), pinned per variant in `test_vision_prompt_v6.py` (v5/v6/v7 values kept). Gemini and legacy unchanged throughout. |

**Step 2 result (offline, no server call; same answers scored with the old and the new conflict rule).**

| On the D74 v4.qwen answers (39 documents) | Old rule | New rule |
|---|---|---|
| Headers correct / wrong | 255/313 / 1 | **275/313** / 3 |
| Changes | — | correct now: `vendor_name` 7, `doc_no` 5, `taxable_total` 3, `buyer_gstin` 2, `cgst` 2, `sgst` 2; wrong now: `taxable_total` 2 (both marked `page_conflict`); lost: `grand_total` 1 (correct → missing) |
| Accepted wrong values (`single`) / simulated `review_cleared` | 0 / 1 | 0 / 1 |
| Line cells | unchanged | unchanged |
| Values marked `page_conflict` | — | 23 |

The 17 Oct v4 reads (36 documents) move the same way: 225 → 245 correct, wrong 1 → 3. Headers are now 275/313 for Qwen vs 242/313 for OCR on the same documents.

**Step 1 result (server reads).** v7 (`ev2-e5-v7.json`): `sent` 26, 5 calls failed (`PROVIDER_UNAVAILABLE` 3, `at_output_cap` 2: S08-INV, S02-INV), 35 of 39 documents read, hard metrics 0, gate PASS. v4 control (`ev2-e5-v4-ctl.json`): the server became unavailable during the run: `sent` 14, `PROVIDER_UNAVAILABLE` 7, `stopped:provider_down` 12 (the bench stops after repeated failures), `at_output_cap` 2 (S06-INV copies); 18 documents got the new read; hard metrics 0. No retries. Where the control was read, it equals the 17 Oct v4 read on all 17 comparable documents (header outcomes identical), so the server state had not changed since 17 Oct and the 17 Oct v4 read is a valid same-state control for the rest.

| v7 vs v4 (both scored with the new conflict rule) | 17 docs read by both today | 32 docs, v4 = 17 Oct read |
|---|---|---|
| Headers correct: v4 / v7 | 102/125 / 82/129 | 207/245 / 188/249 |
| Wrong: v4 / v7 | 1 / 8 | 3 / 10 |
| Accepted wrong (`single`): v4 / v7 | 0 / **4** | 0 / **4** |
| Line cells | identical | identical |
| Marketplace order found: v4 / v7 | 0/5 / **5/5** | 0/5 / 5/5 |

24 of the 36 changed header outcomes are on S03-INV (the Amazon invoice, 4 bench copies): with the marketplace sentence the model reads the order number but also returns a `buyer_gstin` the invoice does not show (truth has none; 4 accepted wrong values) and loses `vendor_gstin`, `doc_no`, totals and the PO reference there. Verdict: **v7 is not better than v4; the reader is back on v4.qwen.** One prompt sentence moves the whole reading of the page it targets, so marketplace order IDs are better taken without touching the prompt: a deterministic post-check that renames a `po_ref`/`sales_order_ref` whose label contains "Order" and whose value has the marketplace pattern (`DDD-DDDDDDD-DDDDDDD`) to `marketplace_order_ref` (proposal only; D72 froze deterministic OCR/rules work, so the owner decides).

Tests (synthetic): new `tests/unit/test_vision_conflicts.py` (10: first/last page choice, OCR agreement wins, same-page conflict still dropped, two values on the chosen page dropped, duplicates, no conflict → no mark, other marks kept, consensus never uses a resolved value), `tests/unit/test_vision_prompt_v7.py` (one line changed, v4 example, budget, reader back on v4), `test_accuracy_vision_eval.py` +1 (`--send-from-repeat` sends only later repeats, same keys, refusals); pins updated in `test_vision_prompt_v6.py`, `test_vision_v4.py`, `test_ai_openai_compat.py`, `test_ai_shadow.py` (back to `cb7d2ee1…`).

Checks: offline suites 1090 passed, 0 xfailed (also re-run after the reader went back to v4: 1090 passed); ruff, mypy `--strict` (37 files), lint-imports 3 kept, core purity, rules hash `1e9af32f…`, engine version vs HEAD; synthetic default-bench guard `d054b29d…` / `822e366e…` / `cca27c5e…` unchanged (also with `--vision-eval`); PG integration (disposable `postgres:16.15`, 0027 + queue schema, stopped) `test_ai_vision`, `test_ai_runner`, `test_ai_shadow` 50 passed. Nothing committed; no `docs/ReconBench_*.md` edited.

**Remaining work in the LLM plan (`docs/plan-llm-extraction-call.md`), as of D77.**

| Phase | Done | Remaining |
|---|---|---|
| AC-L0 provider layer | WI-G1, WI-G2, WI-BE1, WI-BE5 (D62–D63) | WI-EV0 (offline re-score of stored Gemini answers, never run) |
| AC-L1 local provider, settings, shadow | WI-BE2, WI-BE3, WI-BE4, WI-BE6 (D63–D66) | WI-FE1 (AI settings UI with two provider cards), WI-FE3 (frontend tests), WI-OPS1 (written server checklist and smoke test; only ad-hoc checks so far; the server's own availability is a known issue), WI-OPS2 (health, concurrency, fallback in operation) |
| AC-L2 evaluation and decision | WI-EV1 (D67); WI-EV2 E5 arm runs (D68–D77); prompt work D75–D77 (v4.qwen stays); repeats (D76: the server is deterministic per state) | WI-BE8 cropped two-pass reading (needed: dense pages hit the output cap under every prompt); Gemini arms E1–E4 (need the owner's Gemini approval); decision matrix and DP-L1 auto-accept thresholds (need a bigger truth set) |
| AC-L3 LLM values on scans | — | WI-BE7 merge with OCR per field (never clear review without Σ lines = taxable; never auto-accept a `po_refs` set that grew across pages; `page_conflict` values stay suggestions), WI-FE2 review UI provenance, WI-FE3 rest, WI-G3 regression guards and rollback switches |
| Deterministic | — | WI-D1, WI-D2 frozen (D72); WI-D3 parser-version guard, WI-D4 link recall, WI-D6 page-split defect not started; WI-D5 truth set v3 (owner-led, D72 step 4) |
| D72 order | (1) answer format D73, (2) prompt D75–D77, (3) repeats D76 | (4) bigger truth set (owner), (5) WI-BE7 |

DoD §7 is not met yet (headers ≥ 98%: Qwen 275/313 = 87.9% with the conflict fix; lines ≥ 95%; links ≥ 95%). Suggested next order: WI-BE8 crops (recovers documents lost to the output cap) and the marketplace post-check, then WI-BE7, with WI-D5 (owner) in parallel.

## D78 — Plan consolidation: `docs/accuracy-plan.md` rewritten Qwen-first (20 Oct 2026)

Stage 2 of the owner's two-plan consolidation. `docs/accuracy-plan.md` was rewritten in place and now replaces `accuracy-implementation-plan.md`, `plan-llm-extraction-call.md`, `accuracy-fix-plan-scan-extraction.md`, `progress-accuracy-plan.md` and `audit-llm-extraction.md`, which stay on disk until Stage 3. `docs/ReconBench_*.md` are untouched (main plan blob `c6a96759…` equals HEAD). No code, config, container, database, migration, dataset or truth change; no model call and no benchmark. Evidence checks beyond file reads: image and container metadata, a directory/`RULES_HASH`/variable-name listing inside the three running app containers, and read-only SQL counts on the live database.

| Decision | Choice |
|---|---|
| Owner decisions of 20 Oct | O-1 two-tier truth (Tier H about 80 invoices header-only, Tier S 8–10 complete sets; about 5 sets indicative only; labelling protocol and dev/held-out split in WI-D5). O-2 auto-accept only when Qwen and OCR agree or a deterministic validator confirms, plus the lower-bound threshold, 0 critical wrong and a shadow period; DP-L1 arithmetic corrected (a 98% lower bound needs 189 error-free values, not 100). O-3 D-8 gates Gemini only. O-4 D72 lifted for WI-V2 only. O-5 version rule. O-6 Qwen down: rules + OCR with a visible flag, no automatic Gemini failover. O-7 `audit-llm-extraction.md` retired after merging. O-8 live rebuild only inside AC-INT1 (full backup, tested restore, dry-run 0024→0027 on a copy). O-9 carried items keep defaults. |
| Version rule (O-5, recorded here) | `ENGINE_VERSION` for recon-path rules; `PARSER_VERSION` for OCR/rules extraction; `CONSENSUS_VERSION` (or a new check version) for vision-check and consensus logic; guards compare with the last release tag, not HEAD (HEAD is 0.1.4, the tree 0.1.7, so a HEAD comparison is vacuous). D77's change to vision conflict resolution and the consensus skip is covered retroactively by a `consensus.v2` bump in WI-D3. No release tag exists yet; the owner creates the first at AC-INT1b. |
| Recoverability | Option A′ (docs-only commit with explicit pathspecs, tag `pre-plan-merge-20261020`) and Option B (copy to `work/plan-archive-20261020/`). Option B was done in Stage 2 for the eight files below (SHA-256 list in the folder) because rewriting `accuracy-plan.md` replaced an uncommitted version; five of the plan documents are untracked in git. Nothing is committed yet. |
| D43 | Cited by the retired implementation plan (lines 3 and 178) but no D43 entry exists (D42 → D44). Treated as a numbering gap; the number is not reused; old entries are not edited. |
| AC-L labels | D62–D67 headings ("AC-L0 part 1–5", "AC-L1 part 1") do not match the phase membership in the retired LLM plan (BE2/BE3/BE4/BE6 in AC-L1, EV1 in AC-L2). The new plan uses the plan's labels and refers to work by task ID (§13.0). |
| New findings recorded in the plan | The running images (built 16 Oct 04:41) contain none of D62–D77 and the live database is at 0024; the live app is Gemini `ai_first` (`gemini-3.5-flash-lite`), suggestions only, with 0 `consensus_run` rows (WI-INT0); Gemini cannot be switched off while Qwen vision is on because H1–H4 are bound to Gemini (WI-BE9); `test_ai_h1::test_ai_first_suggests_for_a_clean_pdf…` is still failing (WI-T1); the engine-version guard is vacuous against HEAD; four critical fields (doc no., date, GSTIN, refs) and vendor name cannot be proven ≥ 95% at the present sample size. |
| New tasks | WI-EV3, WI-T1, WI-INT0, AC-INT1a/b, WI-BE9, AC-LIVE-S/M, WI-V1, WI-V2, WI-Q1/Q2/Q3, WI-OPS3, WI-BE7a/b/c, WI-QR1, AC-7c; WI-D2b/D2c (fix-plan WI-8 steps 1, 2, 6, frozen). Fix-plan WI-3 steps 4–5 verified done (`SettingsPage.tsx:240`; live config has 1 buyer entity). |
| Rule recorded | RULE-DESC: a line description is never auto-accepted until WI-Q1 shows a measured fix on held-out data. |

Verification of the plan file: every ID-bearing item of the six merged documents (AC-x, WI-x, D-x, DP-x, DP-Lx, R1–R12, B1–B12, C1–C6, P1–P10, M1–M6, I1–I6, E0–E9) appears in §13 (scripted check, no gaps); all relative links resolve; all report files cited exist; every cited decision ID exists except D43 (explained) and D78 (this entry). Baseline numbers were re-read from `ev2-e5-v4-ctl.json` (headers 275/313) and `scanfix-truth-2.json` (302/373); all 13 per-field figures quoted in the owner's brief match `ev2-e5-d73.json` exactly and the D77-rule values are in the plan.

Not done (by design): Stage 3 removal and reference mapping, the docs-only commit and tag, the README and `gates/phase-4.md` pointers, any engineering.

Archived (byte for byte) in `work/plan-archive-20261020/`: `accuracy-plan.md` (previous version), `accuracy-implementation-plan.md`, `plan-llm-extraction-call.md`, `accuracy-fix-plan-scan-extraction.md`, `progress-accuracy-plan.md`, `audit-llm-extraction.md`, `diagnosis-scan-extraction.md`, `decision-log.md` (before this entry).

## D79 — Plan consolidation, Stage 3: removal, narrowed O-10, DP-L1 (A) (20 Oct 2026)

Owner answers to D78 and the Stage 3 cleanup. `docs/accuracy-plan.md` is now the only accuracy plan beside `docs/ReconBench_Implementation_Plan.md` (blob `c6a96759…`, unchanged). No code, config, container, database, migration, dataset or truth change; no model call.

| Decision | Choice |
|---|---|
| DP-L1 | Option (A) for first enablement: Wilson lower bound ≥ 95% with n ≥ 73 error-free values per field type, 0 critical wrong, plus the O-11 circuit breaker. Option (B) (≥ 98%, n ≥ 189 error-free) stays the target for wider auto-accept. |
| O-10 narrowed (replaces the broader reading in D78) | The validator-only path (no OCR agreement) is allowed only for (i) supplier GSTIN: checksum passes **and** it is not the buyer entity's GSTIN **and** it equals the vendor-master GSTIN of that vendor, otherwise agreement-only; (ii) amounts: the full chain closes within rounding (Σ line amounts = taxable **and** taxable + tax = grand total), partial closure is agreement-only. It amends D42 rule 1 for these two cases only; D42 rules 2, 3 and 5 still apply. Recorded in plan §2.3, §2.4, §4.4, §9.1. |
| Accepted as proposed | O-11 (shadow ≥ 14 days and ≥ 100 comparisons; circuit breaker), O-12 (Tier H quotas; PH1 collects the first 25 invoices, the rest staged without a date), O-13 (`vision_only` mode), O-14 (shadow-only back-fill; AC-LIVE-S may turn off Gemini page-image reading), O-15 (first release tag at AC-INT1b), O-16 (held-out policy). O-17 (AC-8b residual) is decided after PH4. |
| PH2 interim targets, pre-registered before any PH2 run | Dev headers ≥ 92% (≥ 288/313), scan-page line amount ≥ 85% (≥ 105/123), quantity and rate ≥ 80%, 0 documents lost to the output cap, accepted-wrong 0. Plan §6. |
| Decision-log proof | Current `decision-log.md` minus the appended D78 block equals the pre-session copy byte for byte (SHA-256 `1ab5d3c8…9af6b`, 273,808 bytes); the pre-session file has 1,487 LF and 114 CRLF line endings, and those are unchanged; the D78 and D79 blocks use LF. `git diff --stat` against HEAD shows only insertions, because D32–D77 written by earlier sessions were uncommitted. |
| Recoverability | Tarball `~/reconbench-archive/plan-archive-20261020.tar.gz` (outside the working tree; SHA-256 `f17a257c…97965`). Snapshot commit `d916ec6` (explicit pathspecs: the five documents to be removed, `accuracy-plan.md`, `decision-log.md`) tagged `pre-plan-merge-20261020`. This entry's commit removes the five documents. The previous version of `accuracy-plan.md` (before the rewrite) exists only in the archive folder and the tarball. |
| Removed | `accuracy-implementation-plan.md`, `plan-llm-extraction-call.md`, `accuracy-fix-plan-scan-extraction.md`, `progress-accuracy-plan.md`, `audit-llm-extraction.md`. Old→new mapping: `docs/accuracy-plan.md` §13 (row per old ID) and §14 (file → section). Old log entries are not edited: D62–D67 and D77 cite `docs/plan-llm-extraction-call.md`; read those citations through §13.5 (task IDs are unchanged); the D43 gap and the AC-L label mismatch are explained in §13.0. |
| Pointers added | One paragraph in `README.md`; a pointer section at the end of `docs/gates/phase-4.md`. |
| Not rewritten (reason) | About 25 code comments and docstrings that say "LLM plan WI-xx" or "scan fix plan WI-n" (backend, scripts, Makefile, migrations) are unchanged: those files carry other sessions' uncommitted work and the instruction was to commit nothing else. The IDs resolve through plan §13.0. `docs/diagnosis-scan-extraction.md` (untracked evidence) is unchanged; its "fix plan WI-n" citations resolve through §13.3. `CODEBASE_CONTEXT.md` is noted as stale in plan §10.4. |

Uncommitted entries in the working tree: 159 before Stage 3, 152 after the snapshot commit (seven paths committed), 152 after this commit (the removals and edits to tracked files are committed; no other path touched).

## D80 — Change of requirement: both LLM providers, connected and selected from the frontend (20 Oct 2026)

Owner change of requirement, after D79. The product must support **both** Qwen (internal vLLM, OpenAI-compatible) and Gemini (Google); an admin connects each from Settings with credentials, a successful connection test makes it active and selectable, users choose the provider for extraction and for AI assist, and **Gemini is not turned off**. Docs only: `docs/accuracy-plan.md` amended (new §2.6, §4.14, §4.15, §7.1a, §13.8, phase PH2b, new owner decisions) and this entry. No code, setting, environment, container, database, model call or benchmark; code comments are left as they are. Nothing is committed yet.

Existing code was read before designing (reuse map in plan §4.15.1): `ai_provider_config` (D65), key sealing in `ai/key_seal.py` (public key in the API, private keyring only in the ai-worker's mounted file, AES-256-GCM with row-bound additional data), the settings and test endpoints, `ai/endpoint_policy.py` and the gate, consent fingerprints, `sweeps.provider_health`, the audit events and the `ai.manage` capability. Findings that shape the plan: (1) `resolve_effective_role` maps every persisted user to `manager` (`authorization.py:93-99`), so "admin only" is not enforced today; (2) the endpoint allowlist is environment-only; (3) the Gemini key sits on `ai_setting` and `auto_verify_field_types` is one workspace-wide list that was set before any measurement; (4) `reader_strategy`/`primary_reader` cannot express "Gemini live and Qwen in shadow"; (5) text hooks H1–H4 and `HOOKS_BY_MODE` are bound to Gemini; (6) `RB_VISION_READER` still defaults to `vision_gemini`; (7) Gemini health is not checked.

| Decision | Choice |
|---|---|
| Superseded (not edited in old entries) | O-13 (`vision_only` / Gemini off): replaced by provider selection; WI-BE9 reworked as provider-agnostic hooks (WI-BE9a/b/c). AC-LIVE-S no longer turns off Gemini: Qwen shadows while Gemini stays the live default. At AC-LIVE-M the default provider is an owner decision from the side-by-side numbers. O-6 rewritten: no silent failover; flagged rules + OCR; manual switch; optional fallback setting off by default. O-14 applies per provider. O-3 amended: D-8 caps required per provider before its merge; the Gemini monthly cap is a required field at connect. |
| Connect from the frontend | Settings → AI providers, one card per provider; status Not connected / Connected / Error with last-checked time and reason; Connected only after a successful server-side test; disconnect and rotate (rotate = test then swap); disconnecting the active provider sends new documents to rules + OCR with a visible flag; in-flight jobs finish or fail cleanly; periodic health check for both providers; Gemini egress notice accepted at connect. No environment edit or rebuild to connect, switch or disconnect. |
| Security | Credentials sealed at rest with a server-side key (existing mechanism), write-only, masked to the last 4, never in logs, errors, reports, exports, audit rows or decision-log entries; admin-only with audit; base URL through the allowlist with loopback and metadata refused and https or approved internal hosts only; a deployment kill switch that overrides the UI. Mapping: environment = kill switch (`RB_AI_EXTERNAL_CALLS`, `RB_AI_VLLM_CALLS`; both `disabled` = global off) + allowlist ceiling (`RB_AI_VLLM_ENDPOINTS`); database = per-workspace connections. |
| Choosing | Workspace defaults for extraction and for AI assist, per-upload override, re-run with the other provider (new reading, never overwrites), provenance (provider, model, prompt version) on the document page, review screen and Excel export (additive `Provenance` sheet). Scope: page-image extraction and H1–H4. One live provider and at most one shadow provider per document version; no merge, vote or in-app comparison. |
| Safety contract per provider | Same validators, OCR-agreement rules and narrowed O-10; DP-L1 numbers, auto-verify list, circuit breaker and `apply_mode` per provider and field type; a provider without measured numbers gives suggestions only; hard zeros for both. |
| Evaluation | Gemini arms E1–E4 move from PH6 into PH2; WI-EV2 and WI-EV3 report Qwen and Gemini side by side on the same truth set and splits; a counts-only measurement import feeds a short "measured better per field" note in Settings (the user decides). Gemini has no measurement under the current prompt and schema (stored numbers are v3, D41), so it gives suggestions only until PH2/PH4 produce its own. |
| New tasks | WI-PV1 (connection model, encrypted credentials, per-provider settings; one migration), WI-PV2 (connect/test/disconnect/rotate/health), WI-PV3 (admin permission and audit), WI-PV4 (allowlist), WI-PV5 (registry, defaults, per-upload override, live + shadow), WI-PV6 (re-run), WI-PV7 (cost cap and usage), WI-PV8 (safety test suite), WI-EXP1 (provenance and export), WI-BE9a/b/c, WI-FE1a/b (replacing WI-FE1). Extended: WI-EV2, WI-EV3, WI-OPS2, WI-FE2, WI-FE3, WI-G3, WI-BE7a/c, AC-INT1a/b, AC-LIVE-S/M. New phase **PH2b** (provider platform) before the deployment ladder; Gemini arms in PH2. |
| New order and Stage 4 queue | WI-PV1 ∥ WI-D5; then WI-BE8 and the Gemini arms E1–E4 → side-by-side WI-EV2; then WI-BE7a (provider-agnostic); then WI-PV2 … PV8, WI-BE9a, WI-EXP1, WI-FE1a/b, WI-FE3 (all before AC-INT1); WI-T1 and WI-INT0 alongside. |
| Owner decisions proposed (defaults, not yet confirmed) | O-18 who may connect: the `ai.manage` capability enforced from the stored role (departs from the P0 policy); O-19 allowlist: environment ceiling + admin-editable entries inside it; O-20 Qwen URL outside the company VPN: no; O-21 fallback to the other provider: off; O-22 default provider after AC-LIVE-M: from measured numbers; O-23 additive `Provenance` sheet: yes, after checking the export contract; O-24 Gemini monthly cap required at connect: yes. |
| Risks added | RT-22 … RT-31 in plan §10.1 (credential leakage, unenforced admin role, SSRF through editable entries, disconnect mid-run, silent data change on switching, auto-verify without numbers, silent failover, live + shadow coexistence, misleading comparison note, export contract). |

Traceability: plan §13.8 lists every superseded part; §13.3 (WI-12, DP-1) and §13.5 (WI-EV2, §0.6, DP-L5, §6) rows were updated in place; the PH numbering of D78/D79 is unchanged (PH2b inserted). Checks re-run: every ID of the six merged documents is still in §13 (scripted, no gaps); relative links resolve; cited reports exist; cited decision IDs exist (D43 explained in §13.0, D80 is this entry); table shapes consistent. Not run: anything that needs code, a database or a server.

## D81 — D80 decisions accepted; Gemini cost caps and bench budget set (20 Oct 2026)

Owner answers to D80. Docs only; no code, setting, environment, container, database, model call or benchmark. `docs/ReconBench_*.md` untouched.

| Decision | Choice |
|---|---|
| O-18 accepted, with a recorded departure from the P0 policy | `ai.manage` is enforced from the stored role, the bootstrap user first. The main plan's P0 policy (and `resolve_effective_role`, `authorization.py:93-99`) maps every persisted user to `manager`; this decision departs from it for `ai.manage` only. The departure is recorded here; the main plan is not edited. Implemented in WI-PV3. |
| O-19, O-20, O-21, O-22, O-23 accepted | Allowlist = environment ceiling plus admin-editable workspace entries inside it, audited; no Qwen URL outside the company VPN; fallback to the other provider off (if ever enabled: consent and a flag on every affected document, never silent); default provider after AC-LIVE-M = higher lower bound on critical fields with 0 critical wrong, within 2 points the owner chooses; additive trailing `Provenance` sheet after checking the export contract. |
| Per-provider auto-verify | Each provider's list starts empty (the live workspace-wide list of five types stops being read for acceptance). |
| O-24 / D-8 Gemini caps | Live monthly cap per workspace ₹2,500 (about US$30); warn at 80% (₹2,000); at 100% new Gemini calls stop and documents fall back to rules + OCR with a visible flag, never a silent switch to Qwen; reset on the 1st of each month (IST); usage = API token counts × the pinned model's configured price, stored per call; pinned model `gemini-3.5-flash-lite` (any other Gemini model needs the owner's approval and a cap re-check); a single document estimated above ₹20 is flagged. Prices and the INR/USD rate live in configuration. |
| Price verification | **Not done.** The official pricing page (ai.google.dev/gemini-api/docs/pricing) could not be read on 20 Oct: the fetch tool refused the domain and the built-in browser navigation was denied. The working assumption (US$0.30 / 1M input, US$2.50 / 1M output, about US$0.006–0.010 per page) is recorded in the plan as an assumption. It must be verified and recorded with its date before cap enforcement is enabled. |
| Gemini bench arms E1–E4 | Approved; total budget ₹2,000 (about US$23) for all Gemini bench runs; estimate and log each run first; stop and ask if one run is estimated above ₹500 or the total would pass ₹2,000; log actual spend from returned token counts; stop at the cap. E3 and E4 name `gemini-3.8-flash` (not the pinned model) and need separate approval. Key: entered by the owner through the connect flow, never in chat or files. Open question recorded in WI-EV2: the offline bench reads `BENCH_GEMINI_KEY` and cannot use a key sealed for the ai-worker. |
| Confirmations added to the plan | WI-PV8 and WI-G3 cover: the secret never in API responses, logs, errors, exports, reports or audit events; a non-admin refused; allowlist/SSRF; disconnect mid-run safe; switching provider never altering readings; the cap stopping calls at 100% with the fallback flag shown. The Gemini health check makes no billable call, or (if no free models call exists in the pinned SDK) runs at most once per hour and is counted against the cap. Rotate = test the new key first, swap only on success, the old key stays active until then. |

Stage 4 was authorised to start with WI-PV1 (test database only, no container rebuild, no model call), stopping after WI-PV1 for review.

## D82 — WI-PV1 provider connection model (migration 0028) and WI-D5 labelling preparation (20 Oct 2026)

Stage 4 started with the owner's authorisation (D81): WI-PV1 on disposable databases only, no container rebuild, no model call, no change to the live database or the running stack. Stopped after WI-PV1 for review.

| Decision | Choice |
|---|---|
| Migration `0028_provider_connections` | `ai_provider_config`: connection state (`connected_at`, `last_error_code/at`, `key_rotated_at`), consent accepted at connect (all-or-none), `monthly_cost_cap_inr` and `doc_cost_flag_inr` (`d_amount`), `cap_warn_pct` (1–100, default 80), per-provider `auto_verify_field_types` (same five values, default empty), per-card `apply_mode` (default `suggest`), breaker state (`closed`/`open`). `ai_setting`: `extraction_provider`, `assist_provider`, `shadow_provider`, `allow_provider_fallback` (false). `batch.provider_override`. New append-only `ai_endpoint_allow` and `provider_measurement` (counts only; mutation guard, no `UPDATE/DELETE/TRUNCATE` for the app role, no access for the report role). Backfill keeps today's providers and gives every Gemini card the owner's 2,500 / 80 / 20 (D81); every per-provider auto-verify list starts empty. Fixed-forward; nothing applied to the live database. |
| Scope choices (with reasons) | (1) The rule "only a `vllm` card may reference a key" is dropped, but the Gemini key is **not** moved: `ai_setting.key_secret_id` stays in use until WI-PV2 moves it, so one secret is never referenced twice (D65's reason). (2) No application code reads the new columns, so the per-provider auto-verify lists are data only; the code that decides auto-verification still reads the workspace-wide list. The switch to the per-provider list, which makes "starts empty" effective, is part of WI-PV5 (acceptance added to the plan). (3) No API, OpenAPI or web change: the connect, test, disconnect, rotate and selection behaviour is WI-PV2 … PV7. (4) The cap is in rupees (the owner's unit); the INR/USD rate and the model price are configuration for WI-PV7 and remain unverified (D81). |
| Other files touched | `db/metadata.py` (mirror), `db/schema_contract.json` (regenerated from a clean head database before seed and queue schema: 53 tables, 40 enums, 30 triggers, 175 foreign keys, 268 explicit ACLs, 7 default privileges), `docs/schema-errata.md` (revision 0028), `.github/workflows/ci.yml` (previous-schema revision `0024` → `0027`, the head − 1 that the safety check requires; it had not been updated since D63), `test_ai_providers_migration.py` (head `0028`, rewinds 0028 first), `test_phase4_task_411_413.py` (accepted revision set now `0015`–`0028`, upgrade when not at `0028`; it previously refused any database above `0025`). |
| Tests | New `test_ai_provider_connections_migration.py` (10): clone rewound to `0027`, legacy workspaces seeded with made-up names, upgrade run; backfill of providers, cap values and empty lists, legacy settings untouched, consent fingerprints byte-identical for migrated workspaces, 15 constraint violations rejected and valid values accepted, the key check gone, mutation guard and grants on both new tables, `batch.provider_override`, no new column able to hold key material. New `test_truth_tools.py` (6). |
| WI-D5 preparation | `scripts/truth_tally.py` (counts only) and `scripts/truth_agreement.py` (labeller A vs B, counts only, `--min-critical`), both unit-tested on synthetic data; the private protocol `work/accuracy-truth/tier-h/PROTOCOL.md` and label template `labels-template.csv` (made-up example row only). **Finding:** the tally of the real truth file shows **26 documents: 10 invoices, 8 POs, 8 GRNs** in 16 sets and 35 files (33 PDF, 2 XML), 2 complete sets, 10 expected links; header presence: doc no./date/supplier GSTIN/vendor name 26, grand total 24, printed PO refs 12, buyer GSTIN 8, taxable/CGST/SGST/IGST 10. The earlier "about 18 invoices" was wrong, and the per-field n quoted from the bench (e.g. 39) counts copies and re-scans of the same document, so the lower bounds in plan §3.2 are optimistic. Plan §3.2/§3.4/§3.7 corrected. |

Checks (disposable PostgreSQL 16.15 at `127.0.0.1:15497`, stopped afterwards; nothing near port 15432): migration safety cloned `0027` and upgraded the copy to `0028`; Alembic autogenerate reports no metadata drift; schema drift, catalog, index and decision checks pass; seed and queue schema applied; table count 53 + 4 Procrastinate passes. Offline suites **1096 passed** (1090 + 6 new), 0 failed. Integration on the disposable database, run from the repository root: group 1 (database, readyz, configuration, ingestion, assist migration, runner) 56 passed; group 2 (`test_phase4_task_411_413`) 3 passed; group 3 (`test_ai_settings_api`) 54 passed; group 4 (H1–H4, vision, KPI, shadow, providers migration, connections migration) 94 passed, 1 failed; group 5 (page split, vendor hygiene) 7 passed, 2 failed. **The 3 failures are the known pre-existing ones, unchanged by this work:** `test_ai_h1::test_ai_first_suggests_for_a_clean_pdf…` (`assert True is False` at line 858: the rules-only clean PDF is `needs_verification`; open since D62, WI-T1) and `test_page_split` ×2 (`MultipleResultsFound`; D57, WI-D6). Ruff (app, core, tests, scripts), mypy `--strict` (37 core files; `metadata.py` and the two new scripts clean), lint-imports (3 kept), core purity, rules hash `1e9af32f…` (unchanged: no core file touched), engine version check vs HEAD, golden self-check pass. Not run: OpenAPI regeneration and web checks (no API or web change), benchmarks, anything against the live stack. Nothing committed.

Open: WI-PV2 next (connect, test, disconnect, rotate, health, the move of the Gemini key to its card); the Gemini price and INR/USD rate remain unverified; the owner's review of WI-PV1.

## D83 — Before WI-PV2: WI-PV1 patch, migration check, deployment gate, unique-document evidence, Gemini free tier (20 Oct 2026)

New session on the owner's instruction (tasks 1–5, then a stop for the owner's go). Plan edits only plus read-only checks on a disposable database; no application code changed, nothing committed, stashed or reverted, no model call, no benchmark, nothing against the live stack or the running containers.

| Item | Result |
|---|---|
| WI-PV1 patch | `work/patches/wi-pv1.patch` (git-ignored, 1,085 lines) holds only the WI-PV1 hunks of the six shared files (`db/metadata.py`, `db/schema_contract.json`, `ci.yml`, `schema-errata.md`, `test_phase4_task_411_413.py`, `test_ai_providers_migration.py`) plus the two new files (`0028_provider_connections.py`, `test_ai_provider_connections_migration.py`); the file list is in `work/patches/wi-pv1-files.md`. The pre-PV1 state of the six files was rebuilt by reversing only the PV1 hunks and checked independently: the rebuilt `metadata.py` shows no drift against a disposable database at `0027` (51 tables, Alembic compare), and the rebuilt contract is the one regenerated from that database (its differences from the working file are exactly the handoff's hunk list). Reverse apply on a copy of the current files gives the pre files byte for byte; forward apply on the pre files gives the current files byte for byte. **Two spots are reconstructed from surrounding code and cannot be proven:** the text of the old `ai_provider_config_key_ck` (re-inserted in the pre state of `metadata.py`; Alembic does not compare check constraints) and one comment word in `test_ai_providers_migration.py` ("first" for "next"). Not PV1 and not in the patch: the plan and decision-log edits, and the WI-D5 files (`scripts/truth_tally.py`, `scripts/truth_agreement.py`, `test_truth_tools.py`). |
| Migration collision | `alembic heads` on a fresh disposable PostgreSQL 16.15 (`127.0.0.1:15497`, upgraded from empty): `0028 (head)`; `alembic current` `0028 (head)`; no branch point, no duplicate revision id in the tree. No ref (the two branches, the remote branch, the tag) holds a migration after the committed ones: `0021`–`0028` exist only as untracked files in this one worktree; no other worktree, no stash. No second `0028`. |
| Deployment gate (AC-INT1) | No rebuild, no migration of the live database and no container swap before **WI-PV5** is done and verified, i.e. before auto-verification reads the per-provider lists (which start empty). Reason: the code reads the workspace-wide list (live: five types) until then. Added to AC-INT1a/b, WI-PV5 and the PH3 entry. |
| Unit of evidence (O-26) | Every per-field `n`, Wilson bound and gate counts **unique documents**; copies and re-scans share the document's split (WI-D5 loader rejects a mixed document; audit of the 26 existing entries before the freeze); WI-EV3 prints `n_unique` and `n_copies` and refuses claims on `n_copies`; strict aggregation across copies (correct only if every copy read is correct) is **my proposal**. **Counts (truth file, counts only; the with-copies side reproduces the 313 headers and 123 line rows of the stored report exactly):** the 39 dev PDFs are 20 unique documents (10 invoices with 29 PDFs, 10 POs/GRNs with 10 PDFs); unique header values 136 (was 313); line rows 47 (was 123). **Gates restated (percentages unchanged):** headers ≥ 92% = ≥ 126/136 (was ≥ 288/313); accepted-wrong 0 = 0 in 136, rule-of-three ≤ 2.2% (was ≤ 1.0%); line amount ≥ 85% = ≥ 40/47 (was ≥ 105/123); quantity ≥ 80% = ≥ 36/44; rate ≥ 80% = ≥ 36/45; marketplace 2/2 documents (was 5/5 reads); handwritten over 5 documents (was 18 reads). One wrong value now costs 0.74 points on headers (was 0.32) and about 2.1 points on a line cell (was 0.8). **Max possible 95% lower bound on unique dev documents:** doc no./date/vendor 83.9% (10 invoices: 72.3%), GSTIN 86.2%, totals 92.3%, line amount 92.4%, printed refs 61.0%; so **no dev field can reach 95%** and §3.4's "totals, lines and HSN reach n ≥ 73" holds only with copies. PH4: Tier H n is unique invoices; at the rates in 10 invoices (very uncertain) printed PO refs would need about 183 and buyer GSTIN about 122 invoices for n ≥ 73 unless collection is targeted. Observed Qwen/OCR correct counts on the unique basis are **not recomputed**: the stored report keeps per-copy results only as totals; an offline re-score of the stored answers (`--network none`, no model call) is requested and waits for the owner's go. |
| Gemini free tier (O-25) | Gemini runs on the free tier (`gemini-3.5-flash-lite`, no billing): cost ₹0, so the O-24 cap (₹2,500 / 80% / ₹20) stays built but **dormant** and starts when a card is declared paid; the API cannot tell the tier, so the admin declares it at connect. No price verification for now (still required before a card is declared paid). **Rate limits replace cost:** 429 `RESOURCE_EXHAUSTED` with backoff; per-minute versus daily classified from the error body (layout documented, **unverified here**; the first live 429 confirms it); daily reset at midnight Pacific computed from the zone (about 12:30–13:30 IST); on a used-up daily quota documents take rules + OCR with a visible "Gemini quota reached" flag and **nothing switches to Qwen**; quota status on the Gemini card; limits read from errors, headers or configuration, never hard-coded. **Data use:** on the free tier Google may use prompts and responses to improve its products and human reviewers may read them; the free-tier consent notice at connect says so (part of the fingerprint); **O-25 default: free-tier Gemini only for test/sample documents; real company documents use Qwen or a paid tier.** **Bench E1/E2:** budget in requests, paced inside the daily limit and spread over days, drift recorded per run date, a separate free-tier project and key supplied by the owner through `BENCH_GEMINI_KEY` for the run only (never `.env`, history, files or logs; the bench never reads the sealed app key). **E3/E4 (`gemini-3.8-flash`) are not approved.** The ₹2,000 bench budget and `--max-spend-inr` apply only to a billed key. |

Plan sections changed (all in `docs/accuracy-plan.md`): §0 status row, §2.2, §2.5, §3.2a (new), §3.4, §3.7, §4.15.9 (new), §5.1, §5.4, §6 (PH2, PH3, PH4), WI-D5, WI-EV3, WI-PV2, WI-PV5, WI-PV7 (reworked: quota handling, tier declaration, cap dormant), WI-PV8 (items 11, 12, 14, 15), WI-FE1a, WI-EV2, AC-INT1a/b, §9.1 (O-24 and bench rows annotated; O-25, O-26 added), D-8 and DP-L5 rows. Appended only; no earlier decision-log entry edited.

**Pending owner confirmations (O-25):** (a) the tier of the already-sealed live Gemini key (D-1 says paid; D39 allows free only for tests): if it is free, real documents on the live stack fall under the data-use warning; (b) whether the masked dev-set samples (real invoices) count as "sample documents" for E1/E2. **Pending go:** the offline re-score on the unique basis; WI-PV2.

## D84 — Owner answers after D83: live Gemini key is free tier (O-25 changed), unique-document re-score, targeted collection, O-18 placement (20 Oct 2026)

Owner's answers to the D83 questions, and the work they released. No code changed in this entry (WI-PV2 is D85); no model call, no network (the re-score ran in a container with `--network none`); nothing committed; the live stack, its database and containers untouched.

| Item | Record |
|---|---|
| Live Gemini key tier | The owner checked in Google AI Studio: the key sealed in the live app is **free tier**. **D-1's "paid" assumption was wrong.** Real documents on the live stack have been sent to Gemini under free-tier data-use terms (Google may use prompts and responses to improve its products, and human reviewers may read them). |
| O-25 changed | **Owner decision: free-tier data use is accepted for real company documents, including live invoices; no restriction to test/sample documents** (replaces the D83 default). The free-tier consent notice at connect stays; it states the data use explicitly and is part of the consent fingerprint. The live stack is **not** changed by this decision (migration of the card to tier = free happens only inside WI-PV2's migration 0029 on disposable databases, and reaches the live DB only in AC-INT1b). Plan: O-25 row, §3.6 (new row), §4.15.9 data-use bullet, D-1 row, DP-L5 (closed), WI-EV2. |
| E1/E2 bench data | The masked dev set goes to the **free bench key** as planned: budget in requests, paced inside the daily limit, separate free-tier project and key so the app's quota is not used, `BENCH_GEMINI_KEY` exported by the owner in their shell for the run only, the bench never reads the sealed app key. E3/E4 stay unapproved. |
| Copy rule | "Correct only if every copy read is correct" accepted by the owner as the conservative choice (O-26 stands as written in D83). |
| Offline re-score (approved) | Stored answers only: `accuracy_bench.py --vision cached --vision-provider vllm --vision-prompt-variant qwen --repeat 4 --send-from-repeat 3 --vision-eval` in the `reconbench-worker` image with `--network none`, source mounted read-only, wrapped by `work/tools/rescore_unique_driver.py` (captures per-document outcomes from the bench's own scoring functions; writes counts only). **Reproduces the stored report exactly** (Qwen 275/313, OCR 242/313, tokens 791,102 / 359,804). Unique-document results (strict rule), also in plan §3.2b: Qwen **headers 101/136 (74.3%)**, LB 66.3% (with copies 275/313); doc no. 13/20, date 16/20, vendor name 20/20, GSTIN 22/24, totals 27/46, printed refs 3/6; line amount 27/47, quantity 23/44, rate 25/45, description 13/32, HSN 13/23. OCR headers 96/136 (62.5%). Invoices only: Qwen doc no./date/vendor 10/10, headers 74/86. Of the 35 unique Qwen headers not correct, 33 are missing and 2 wrong. Auto-accepted (policy `single`) 101 unique values, 0 wrong (rule-of-three ≤ 3.0%; replaces D83's "0 in 136" reading). **Against the PH2 interim gates restated in D83:** headers 101/136 vs ≥ 126/136 (25 short), line amount 27/47 vs ≥ 40/47, quantity 23/44 vs ≥ 36/44, rate 25/45 vs ≥ 36/45. The gate percentages were set on the with-copies basis and are far on the unique basis; reported, not changed (the owner may re-register the interim gates). |
| WI-D5 targeted collection | Added as step 8 and a §5.1 bullet: Tier H batches deliberately include invoices with a printed PO ref and with a printed buyer GSTIN until each reaches 73 unique documents (instead of about 183 and 122 random invoices); per-field unique `n` is recomputed and logged after every batch with `truth_tally.py` (to be extended to print presence per document type and tier); targeted batches are labelled and never used for presence rates. |
| Admin enforcement (O-18) | Built by **WI-PV3**, a PH2b task; AC-INT1b depends on PH2b exit, and the deployment gate now names WI-PV3 beside WI-PV5, so it lands before the first live change (AC-INT1a changes nothing live). **Until then every logged-in user can change AI settings, and the new WI-PV2 endpoints (`ai.manage`) are equally open.** |
| Housekeeping | Plan §0 status row and the stale memory note are updated in D85; the container `rb-verify-pg-refresh-20261003` is left alone. |

## D85 — WI-PV2 connect, test, rotate, disconnect and health; Gemini key moved to its card (migration 0029) (20 Oct 2026)

Authorised by the owner after D84 ("WI-PV2: GO"): disposable databases only (a fresh PostgreSQL 16.15 cluster at `127.0.0.1:15497`, stopped afterwards), no live database or container touched, no model call (every provider answer in the tests is scripted or mocked), nothing committed. Stopped after WI-PV2 for review.

| Decision | Choice |
|---|---|
| Migration `0029_provider_key_move` | `ai_provider_config.key_tier` (`free`/`paid`, NOT NULL default `free`; a company-server card must be `free`) and `pending_key_secret_id` (rotation candidate, FK to `secret`, never the live key). **The Gemini key moves from `ai_setting.key_secret_id` to its card by moving the pointer only**: the `secret` row (ciphertext, nonce, key id, hint) is not copied or re-encrypted. The old pointer is cleared only after the migration verified, for every moved key, that the card points at a secret of the same workspace that still holds ciphertext and nonce; a mismatch, or a card that already holds a different key, raises and rolls the whole migration back (the message names counts, never key material). The live Gemini card migrates as tier = free (owner, D84), and a card whose legacy test passed takes that time as `connected_at`. **`downgrade()` is real** (the owner asked for a tested path): it refuses when both locations hold a key, moves the pointer back with the same verify-then-clear order, deletes pending candidates and drops the columns. Contract after: 53 tables, 40 enums, 30 triggers, 176 foreign keys, 268 ACLs, 7 default privileges. |
| Reading stays dual | `load_settings` reads the card's key and falls back to `ai_setting.key_secret_id` for one release (WI-BE9a), so a workspace not yet moved still works; writes go to the card and clear the old pointer. |
| Connection state | Derived, never a stored label: **Not connected** until a provider test passes (`connected_at`); **Connected**; **Error** with a generic reason code (`bad_key`, `MODEL_UNAVAILABLE`, `PROVIDER_UNAVAILABLE`, `timeout`, … anything else is `error`; never provider text). Any change to key, model or server resets the card; a failed health check turns Connected into Error and a passing one back, within one cycle; health never makes a never-tested card Connected. |
| Endpoints (both providers, `ai.manage`) | `GET /ai/providers/{provider}/connect-notice` (text, data-use flag, fingerprint; the free-tier notice states that Google may use prompts and responses to improve its products and human reviewers may read them); `POST …/connect` (202: seals the key on receipt, records the accepted notice with who and when, declared tier, queues the server-side test); `POST …/rotate` (202: **test then swap**: the candidate waits in `pending_key_secret_id`, the old key stays active, the worker tests the candidate and swaps on success; a failed test removes the candidate and changes nothing else); `POST …/disconnect` (deletes every key of the card, records who and when, moves the settings version so a request in flight is discarded, never applied). `GET /ai/settings` shows per provider `connection`, `tier`, `consent`, `health`. New audit actions `ai.provider_connect`, `ai.provider_disconnect`, `ai.provider_rotate` (booleans, tier, model, origin; never a key). The API never calls a provider. |
| Health | Company server: the existing `GET /models` sweep now also updates the state. Gemini: a **synthetic test request at most once per hour per tested card** (`gemini_health`), because a non-billed models call is **unverified**; it is a ledger row (`provider_health:` key), so it counts in the day's requests; a quota error during health is a state change, not a broken connection. Candidates no test settled within an hour are dropped by the sweep. |
| Consent | `connect` requires the notice fingerprint (tier, model, server in it); wrong fingerprint → 409, nothing saved. **The card-level consent is recorded but is not yet a send gate**: the workspace AI consent still authorises sends; per-provider enforcement is WI-PV5 / WI-PV8 item 9. A paid declaration is refused until a price entry exists for the model (`price_not_configured`). |
| Not done / deferred | Admin check (WI-PV3: until it lands every logged-in user can call these endpoints, D84); UI (WI-FE1a/b); a *skipped* test leaves the card Not connected rather than Error; the day's request count for the Gemini quota must read `ai_request` (tests and health have no `ai_call_log` row); quota state storage is open (WI-PV7); connecting Gemini with another model while AI is on is refused (`model_change_via_settings`) so the workspace consent is asked through the settings route. |

Tests (new): `test_ai_provider_key_move_migration.py` **4** (clone rewound to 0028 with a dummy sealed key sealed by a throwaway keyring: the pointer moves, the secret row is byte-identical, the worker keyring still opens it, vLLM key untouched, tier free everywhere, `connected_at` from the legacy test, constraints; a conflicting card aborts and rolls back with nothing changed; downgrade moves the key back, deletes the candidate and the move is repeatable; refusal when both locations hold a key; **the dummy key value appears in no migration output, log or error**), `test_ai_provider_connections_api.py` **22** (Connected only after a passing test; Error with reason and recovery; reset on any change; rotate with a bad key keeps the old key and connection, with a good key swaps atomically and deletes the old secret; disconnect between queue and run sends nothing, disconnect during a call discards the answer with no partial rows; Gemini health at most hourly and flipping both ways; company-server health; both providers through the same endpoints; SSRF refusal; notice and consent rules; the legacy key location still works; connecting while AI is on keeps the workspace consent; no key in any response, audit row, request row or setting), `test_ai_provider_connection_state.py` **4**. Existing migration tests updated for head 0029 (`test_ai_providers_migration`, `test_ai_provider_connections_migration`, `test_phase4_task_411_413`); CI previous-schema revision now `0028`.

Checks: migration safety cloned `0028` and upgraded the copy to `0029`; Alembic no metadata drift; schema drift, catalog, index and decision checks pass (run before seed and queue schema, as required); table count 53 + 4 Procrastinate. Ruff clean (app, core, tests, scripts); mypy `--strict` 37 core files and the ten touched app modules clean; lint-imports 3 kept; core purity, rules hash `1e9af32f…`, engine version and golden self-check pass; web `tsc -b` passes after regenerating `openapi.json` and `generated.ts` (additions only). **Full suite on the disposable database:** offline **1100 passed**; integration group 1 56 passed; group 2 (`test_phase4_task_411_413`) 3 passed; group 3 (`test_ai_settings_api`, unchanged by the key move) 54 passed; group 4 98 passed + 1 failed; group 5 7 passed + 2 failed; connections API 22 passed. **The 3 known failures, unchanged and not caused by this work (same signatures as D82):** `test_ai_h1::test_ai_first_suggests_for_a_clean_pdf…` (`assert True is False`, line 858; WI-T1) and `test_page_split` ×2 (`MultipleResultsFound`; WI-D6). One failure appeared during the work and was fixed in the same session: `test_authorization` (the public `engine` property of the new service had no declaration; now private).

Patch: `work/patches/wi-pv2.patch` (26 files: 19 changed with only WI-PV2 hunks, 7 new) and `work/patches/wi-pv2-files.md`; reverse and forward apply on copies are byte-exact. Plan: WI-PV2 card marked done with an "as built" and "not done" list, the tier storage sentence in WI-PV7, §0 status rows and a Stage 4 progress paragraph. Memory note `project_plan_consolidation` rewritten (it was stale).

Open: the owner's review of WI-PV1 and WI-PV2; next WI-PV3 (admin enforcement, O-18), which also closes the "any user can use these endpoints" gap before any deployment.

## D86 — Full-pool linking arm (WI-D4f), native Tally import task (WI-IMP1), approvals and coverage of the August dumps (21 Oct 2026)

Records what the owner approved in chat on 21 Oct 2026 (the handoff of 21 Oct could not find these approvals in its own record; they are in the task message of this session) and two task cards that were proposed but never written. Docs only: no code, no import, no model call, no commit, live stack untouched. The data: `work/accuracy-intake/po/PO.xml` (251 vouchers, `Purchase Order 25-26`, 23,140,306 bytes) and `grn/GRN.xml` (250 vouchers, `Receipt Note 25-26`, 23,383,678 bytes), full August 2026 Tally exports (UTF-16, native "Import Data" format; every voucher holds the invalid character reference `&#4;`; the app parser rejects both whole files with `XML_PARSE_ERROR`; sizes are near the 26,214,400-byte upload limit). Intake finding: the 15 PDFs the owner also placed in `work/accuracy-intake/inovices/` (sic) are **byte-identical** (SHA-256) to files already in `work/accuracy-samples/invoice/` (INV-01 … INV-08 and RESCAN-1 … RESCAN-7) and already in `truth.json` as S01 … S08. They add **no new unique invoice**; only the two dumps are new data.

| Decision | Choice |
|---|---|
| Approvals (owner, 21 Oct) | (1) The **split approach for the bench**: each voucher is read out of the file and wrapped in the Tally envelope (declaring `xmlns:UDF="TallyUDF"`), byte-exact except that the `&#4;` references are removed (and stray control characters in the masters are ignored); the removals are counted and logged, no value changes. (2) The **GRN `po_refs` layout mapping** in the disposable workspace only: saved-layout hint `custom_xpaths={"po_refs": "REFERENCE"}` (sets `po_refs` on 250/250 GRNs; 6 GRNs have a line `ORDERNO` different from `REFERENCE`, i.e. more than one PO). Neither approval changes product code. |
| **WI-D4f Full-pool linking arm** (new, §7.7) | A **separate bench path and report** (the default bench output and its hash do not change): a disposable PostgreSQL database; the August pool is ingested through the split approach above; one **primary** PDF per invoice (owner, 21 Oct) is added with its reading; the matcher (`MappingService.resolve_workspace`) runs over the whole pool. Two arms: **A rules + OCR** (stored readings where they exist; for invoices without a stored reading the offline rules + OCR path only, local Tesseract, `--network none`) and **B Qwen stored** (stored v4 answers plus the D77 vision-alone document, **labelled as a harness substitution** because the app merge WI-BE7 is not built; no new Qwen read without the owner's approval and a request count first). Metrics per truth invoice: correct PO and GRN as **auto / suggested / missed**; false links (auto and suggested); ambiguous candidates; precision and recall; runtime; and how many correct links were possible **only** from the handwritten numbers. **Hard zero: 0 false auto-links.** The pool is **private, a dev-set tool only and never held-out evidence**. It is described by data (the `pool` block of `truth.json`: files, hashes, layout hints, coverage), so further dumps (July POs, service vouchers) are added by editing data, not code. **Service-purchase exports (SERVICEPUR vouchers, SER/GRN receipts) are needed for service invoices**; none is in these dumps. Status: **approved, not built**; the run itself waits for the owner's go. |
| **WI-IMP1 Native Tally import** (new, §7.2; product task, **before AC-INT1, not built now, no approval to build**) | Import native Tally "Import Data" exports: multi-voucher `ENVELOPE` files; the `&#4;` marker and stray control characters handled safely and **logged**, never silently changing a value; masters ignored cleanly; a file-size limit that fits real exports (these dumps are about 22 MB against the 25 MB limit); Receipt Note `REFERENCE` / line `ORDERNO` mapped to `po_refs` by default, with a multi-PO GRN flagged; PO and GRN totals parsed where Tally provides them (today neither a grand nor a taxable total is parsed from these vouchers); follows the version rule O-5. |
| Coverage of the dumps | **August 2026 only** (vouchers dated 1-31 Aug; 0 cancelled, deleted or optional; 0 duplicate voucher numbers). S08's PO (`PO/26-27/07-077`) is a **July** PO and is not in the dump (a GRN cites it); S01 is a **service voucher** (servicepur) and is not in the dump. So the pool can prove links for August goods invoices only. |
| Copies rule (owner, 21 Oct) | One **primary** PDF per invoice for linking and extraction metrics; **all copies (the 7 re-scan pairs) only for the duplicate-detection test** (AC-8b measurement, STEP 3b). Consistent with O-26. |
| Collection note | This batch added **no new unique invoice**. Tier H still needs about 80 distinct invoices (§5.1). The dumps cover August, so **new August invoices from vendors that appear in these dumps** are the most useful next collection (they can be linked as well as read). |

Open: STEP 3 (full-pool linking, duplicate detection on the 7 re-scan pairs, partial delivery S05, stray page S05 page 3) needs the owner's go; nothing in WI-IMP1 starts without a separate approval.

## D87 — S06 truth correction, Tier S pool links and typed handwritten references (21 Oct 2026)

Owner answers of 21 Oct to the STEP 2 report (all rows of the link table checked against the dumps by document number, vendor GSTIN, date, totals and line items). Private file `work/accuracy-truth/truth.json` edited (backup `truth.json.bak-20261021-pre-D87` beside it); no code, no model call (the re-scores read stored answers in a throwaway container with `--network none`), no commit, live stack untouched.

| Decision | Choice |
|---|---|
| **S06 correction** | The handwritten PO on the Phoenyx invoice (ARMU-41045-26-27) is **`PO/26-27/08-081`** and the handwritten GRN is `08-072`. `truth.json` had **`08-087`**, a misreading; reason: on a zoomed crop and by the owner's check the number is 08-081; the dump PO 08-081 has the same vendor, date (12 Aug), total and single line, while PO 08-087 belongs to another vendor (dated 11 Aug). 19 of the 22 stored vision answers for this paper (Gemini flash-lite 5 of 5, Gemini 3.8 1 of 1, Qwen 13 of 16; the other 3 Qwen answers recorded no annotation) read 08-081; none read 08-087, so **every stored report scored this reference as missed**. Old value kept in the S06 note and here. |
| **Re-score of stored reports** | All 71 reports in `work/accuracy-reports` carry `truth_sha256` `f25da17c…` (the old truth); 51 of them have a non-zero handwritten-reference count and are **stale for that one metric**. Re-scored offline (same driver and container as D84, cached answers): **Qwen v4 control** (`--vision-prompt-variant qwen --repeat 4 --send-from-repeat 3`): handwritten refs **1/18 → 2/18** with the S06 fix alone, → **9/57** on the new truth (typed 9, annotation_other 15, printed_field 2, missed 31); **Gemini 3.5 flash-lite** (`--vision-cache-compat v1`; reproduces 6/18 and 273/313 on the old truth): **6/18 → 9/18**, → **24/57** on the new truth. Headers (Qwen 275/313, Gemini 273/313), lines, links and every hard zero are unchanged (`po_refs` and header values were not edited; hard zero 0 in every run). The other 49 stale reports (Gemini flash-lite repeats of the same answers, Gemini 3.8, Qwen v5/v6/v7/D73/v4-r3 variants) were **not** re-run; read their handwritten line only as "old truth". Outputs: `rescore-d87-20261021*.json`, `rescore-d87-s06only*.json`, `rescore-d87-gemini-lite-{old,s06only,new}*.json`. The scored form of a handwritten reference is the short identifying part (`08-081`, as S03 already had): a stored Qwen answer read `26-27108-081` (the slash as "1"), which would miss the long form. S05 and S08 also print the same PO (`printed_field` outcome is not a rule-7 breach there); WI-Q2 must define the match rule. |
| **Tier S links added** (owner-confirmed, S02 … S08) | Invoice → PO **6** (S02 08-047, S03 08-118, S04 08-227, S05 08-222, S06 08-081, S07 08-136); invoice → GRN **7** (the six plus S08 GRN 08-119); GRN → PO **6**, plus **1** GRN (08-119) that cites the external July PO `PO/26-27/07-077`. Each GRN's `REFERENCE` is its PO. Stored in the invoice documents as `pool_links` (voucher number, in pool or not, basis printed/handwritten) and `tier: "S"`, and counted in the new top-level `pool` block (files and SHA-256 of the two dumps, coverage, exclusions, loading rules, the copy rule, expected counts). The loader and the dev bench ignore these fields; the pool files are **not** truth files, because the bench would parse 22 MB dumps and change the 51 parsed documents. **Complete PO + GRN + invoice sets with the pool: 8** (S02 … S07 plus S09, S10); `truth_tally.py` still prints 2 because it counts only truth documents. S08 has GRN and invoice only; S01 has none. |
| **Handwritten references as truth fields** (WI-Q2) | New `handwritten_refs` per invoice (`type` po / grn / service_po / unverified, the text as written, the voucher): **16 typed references on 8 invoices** (S01 … S08, one PO-type and one GRN-type each; S01's are the service PO and the unverified one). The flat `annotation_refs` list the bench scores holds 19 strings on 10 documents (was 5 documents: `truth_tally` `documents_with_annotation_refs` 5 → 10). |
| **S01 (Dell 1610357353)** | Owner gave two alternatives and did not choose; recorded as **option (b)**: handwritten `servicepur/26-27/08-31` (service PO) and `08-031` typed **`unverified`** (probably the service GRN `SER/GRN-25-26/08-031`), excluded from link metrics until the owner confirms in one line. Either way neither is linkable: service vouchers (SERVICEPUR, SER/GRN) are not in these dumps (WI-D4f, D86). |
| **Expected-behaviour notes kept** (STEP 3) | **S05 (76ID2600960905):** partial delivery (PO 5 lines, GRN 4, invoice 3; the 2 Warranty Pack lines are undelivered and must show as pending, not as a mismatch and not as fully matched); the GRN has a COURIER CHARGES line not on the PO; invoice line amounts are the PO/GRN amounts plus freight loaded on each line (the freight equals the courier line within 2 paise); **page 3 of every copy (original and re-scan) is the e-way bill of another invoice** (76ID2600960902, another recipient) and must not be read into this invoice. S08: GRN 11 lines against 10; freight loaded as in S05. S03: a zero-value shipping line the PO and GRN lack. |

Facts verified on 21 Oct (read-only): 8 unique invoices in the 15 files; 7 re-scan pairs (1..→S08, 2→S05, 3→S02, 4→S07, 5→S06, 6→S01, 7→S04; S03 has no re-scan); the Prime, Phoenyx and Ingram 959957 e-way-bill pages carry their own invoice numbers, only S05 page 3 is foreign. `truth.json` SHA-256 `f25da17c…56cd` → `7486a670…3d4` (CRLF kept, 1,649 lines).

Plan sections changed (D86 and D87, `docs/accuracy-plan.md`, CRLF kept, 1,414 to 1,438 lines): §0 (date and last-decision row; a paragraph on the new truth and linking data), §3.2 (handwritten row), §3.4 (update bullet), §6 (PH2 row: handwritten count basis; the gate is not re-registered), §7.1 (WI-D5 progress), §7.2 (new card WI-IMP1), §7.7 (new card WI-D4f), §8 (two diagram lines).

Open: the one-line confirmation for S01 `08-031`; STEP 3 on the owner's go; the 49 stale reports stay as they are unless a metric needs them.

## D88 — Follow-up to D87: S01 stays unverified, type-aware reference match rule, WI-EV2 note, STEP 3 go (21 Oct 2026)

Owner, 21 Oct, after accepting the D86/D87 records. Docs only in this entry; D86 and D87 are not rewritten.

| Decision | Choice |
|---|---|
| S01 `08-031` | Stays option (b), `unverified`, excluded from link metrics; the owner confirms later. |
| **Reference match rule** (WI-Q2, WI-D4f) | Short-form scoring (for example `08-081`) is fine for **reading** accuracy. For **linking** a reference is **type-aware**: a PO-type reference may link only to a PO, a GRN-type reference only to a GRN. Both `PO/26-27/08-081` and `GRN/BCSL/25-26/08-081` can exist, so **a short number alone never auto-links**; if the type is unclear it stays a suggestion. Common misreads are normalised before matching (for example `26-27108-081` to `26-27/08-081`) and **each normalisation is logged**. |
| STEP 3a test case | Report every invoice where a short number matches both a PO and a GRN in the pool, and what the matcher did. |
| WI-EV2 note (no decision) | On stored answers (D87 truth) Gemini 3.5 flash-lite reads handwritten references 24/57 against Qwen v4 9/57 with similar header scores; a per-field provider choice for handwritten references is a candidate row for the side-by-side matrix after E1/E2 and WI-Q2. No setting is changed. |
| STEP 3 | **Go** (owner, 21 Oct): full-pool linking (one primary copy per invoice, arms A and B), duplicate detection on the 7 re-scan pairs, S05 partial delivery, S05 stray page. Disposable databases only, no model call, no commit; report per invoice with the hard zero (0 false auto-links) first, then stop. |

Plan sections changed (`docs/accuracy-plan.md`, CRLF kept): §7.3 WI-EV2 (note) and WI-Q2 (match rule), §7.7 WI-D4f (match rule, test case).

## D89 — WI-D4f full-pool linking results, harness-only sign-normalised diagnostic, follow-up cards, detour closed (21 Oct 2026)

Owner go on 21 Oct for STEP 3 (D88) and, after the first report, for a sign-normalised re-run as a bench-only diagnostic, then the STEP 4 records. Everything ran on the disposable database `rb-d4f-pg` (PostgreSQL 16.15, port 15496, tmpfs; migrated to head `0029`; stopped at the end); no model call (arm B reads stored Qwen v4 answers), no live database or container touched, no commit, no change to app code. Harness and reports are private: `work/tools/d4f_readings_driver.py` (readings captured offline in the `reconbench-worker` image with `--network none`), `work/tools/d4f_pool_run.py`, `work/tools/d4f_grn_trace.py`; reports `work/accuracy-intake/d4f/d4f-arm{A,B}-20261021.json` (harness v1), `d4f-armB-signnorm-20261021.json` (v1, sign-normalised) and `d4f-v2-{baseline,signnorm}-arm{A,B}.json` (harness v2).

| Item | Result |
|---|---|
| Method | The pool is ingested voucher by voucher through `IngestionService._persist_parse` (split approach and GRN layout hint of D86: 251/251 POs and 250/250 GRNs parsed, document number = voucher number on all; 5,311 and 5,017 `&#4;` references removed inside the vouchers); 8 primary scans (INV-01 … INV-08) as arm A (stored rules + OCR ParseResult) or arm B (the stored Qwen v4 vision-alone document substituted, labelled harness substitution); vendors by `VendorResolutionService`, links by `MappingService.resolve_workspace`, cases by `ReconciliationService.evaluate_run`, PO ledger by `LedgerRebuildService`. **Harness v1** ran one mapping pass; **harness v2** emulates the worker sequence (`link_vendor` mapping, then `recompute_po`/`drain_dirty_scopes`, then mapping again) until the links stop changing (converged after pass 3). |
| **Hard zero** | **0 false auto-links and 0 false suggestions** in every arm run (v1 A, v1 B, v1 B sign-normalised, and v2 A and B both with and without normalisation; the v1 sign-normalised arm A was stopped and superseded by v2). |
| Invoice links | **1 of 13 expected found** (S05 to PO 08-222, suggested, reverse; the printed PO), in both arms and both harness versions; precision 1/1, recall 1/13. Per invoice: S01 unlinked (service voucher, correct); S02 missed (arm A: 7 candidates, CONTENT_AMBIGUOUS; the correct PO is not the top candidate); S03, S04, S07 missed (no candidate); S05 PO suggested, GRN missed; S06 missed (v2: correct PO scores 0.77, amount check pass, part 0.61, reference 0, below threshold); S08 GRN missed (its PO is July, outside the pool). |
| GRN to PO | Truth GRNs: v1 6/6 suggested (reverse); **v2 4 auto/labeled (S02, S03, S04, S06) + 2 suggested (S05, S07)**. Pool, v2: **191 auto/labeled + 35 suggested, all 226 agree with the Tally `REFERENCE`**; 24 GRNs unlinked, 23 of them cite POs outside the August dump. The 191 auto links unlocked **0** invoice links. |
| Why GRN to PO needs a second pass | The GRN `po_refs` value becomes system-checked (verified) only with an independent part match (Tally stock items carry no part numbers) or the amount signal; the amount signal uses the PO ledger's received quantity, which is 0 before the first ledger rebuild. The ledger counts suggested non-AI links, so on the second pass the GRN's own receipt supports its own reference check (in effect GRN qty × PO rate against GRN qty × GRN rate). Correct in this pool, but partly self-supporting: recorded in WI-D4 for a design decision. |
| **Correction** | My first report said the negative Tally line amounts blocked the GRN reference check. **That was wrong**: the sign-normalised diagnostic changed nothing, and the cause was the single mapping pass of harness v1 plus the ledger dependence above. |
| Sign-normalised diagnostic | Labelled "harness sign-normalisation, not app behaviour"; first-run results kept as the baseline. The harness turned 455 PO and 456 GRN negative line amounts into positive magnitudes after parsing (logged per field: only `line.amount` was negative). **Every link, case and ledger figure is identical** with and without it (v1 arm B; v2 arms A and B). GRN to PO links that became auto/labeled because of it: **0**; invoice links it unlocked: **0**. |
| Handwritten numbers | 12 of the 13 expected numbers exist only in handwriting; **0** links came from them (OCR reads none; Qwen v4 typed one reference, S08's July PO, and wrote S04's PO `08-227` and GRN `08-210` only as untyped notes). |
| D88 type-aware test | One invoice: S05's `08-222` names both PO/26-27/08-222 and GRN/BCSL/25-26/08-222 (a GRN of another PO); the matcher linked only the PO, as a suggestion. |
| Duplicate detection (7 re-scan pairs, arm A) | v1: **5/7** caught (the re-scan became a version of the original document). v2: **5/7** caught, 2 second invoice documents, 0 links from them. The misses: S07 (RESCAN-4) made a second invoice document and the reconciliation then marked a case `duplicate`; S04 (RESCAN-7) made a second document because OCR did not read its invoice number, and the original only gets `DUP_SUSPECTED`. To AC-8b. |
| After the re-scans | S05's current version becomes its re-scan, which reads the printed PO cleanly (the original OCR read was garbled): S05 gets **3 auto/labeled line links to the correct PO 08-222**, the only auto invoice links in any run; still 0 false. |
| S05 partial delivery | The 2 undelivered PO lines (Warranty Pack) show ordered 1, received 0, billed 0, open 1: pending, not a mismatch, not fully matched (case `needs_review`). The 3 delivered invoice lines do not align (freight loaded onto line amounts: WI-R2), and PO line 4 shows received 2 against ordered 1 (a courier-charge GRN line aligned to it: WI-R1). |
| S05 stray page | Rules + OCR take no value from page 3 and raise `ATTACHED_EWAY_BILL_OTHER_DOCUMENT` on both copies. The stored Qwen v4 answer classifies page 3 as an e-way bill but on the original copy takes the **taxable total from page 3** (the other invoice's value; unverified, not auto-accepted); on the re-scan copy it is read correctly from page 2. Page 3's text is stored with the invoice version (text only). To WI-BE7a as guard MG-11. |
| Runtime | Pool ingest about 42 s (59 s with normalisation), vendor resolution about 5 s, **mapping 18.6 min per pass** (v1, two runs in parallel); v2 about 24 + 8 + 8 min plus about 12 min of ledger rebuild per arm (four runs in parallel on 12 logical CPUs). To WI-PERF1. |

Owner decisions recorded (21 Oct, chat):

| Decision | Choice |
|---|---|
| WI-IMP1 | **Sign normalisation of Tally amounts is a required item**, logged per field; the D89 evidence shows it is a data-correctness requirement, not the linking fix. |
| New cards (not to be built now) | **WI-R1** PO-ledger over-receipt from charge lines (test required); **WI-R2** freight-loaded invoice lines (align on quantity and rate and on amount net of freight); **WI-PERF1** matcher runtime (profile; proposed target under 2 minutes per pass for one real month, the pool run as the benchmark; the owner sets the target); **WI-BE7a guard MG-11** (never take a value from a page classified as another document's; S05 page 3 as the test; MG-10 already existed); **WI-Q2 output-type fix** (handwritten PO/GRN numbers as typed references, S04 evidence); **AC-8b** pool evidence (5/7 and the two misses). |
| WI-EV2 note | The Gemini 24/57 against Qwen 9/57 handwritten-reference note is already in WI-EV2 (D88); nothing added. |
| Detour closed | WI-D4f is closed; work returns to the plan's order. **Owner-proposed order:** WI-IMP1 sign normalisation, then WI-Q2 and WI-BE8 in parallel, then WI-PV3, then WI-R1, WI-R2, WI-PERF1, WI-BE7. **Engineer's recommendation (differs; the owner chooses):** (1) WI-PV3 alongside WI-Q2 (different files: `ai/settings_service.py` against the vision prompt and output; WI-PV3 is a deployment gate and any logged-in user can change AI settings today); (2) WI-D4 with the D89 evidence (GRN reference check, content scoring), then WI-PERF1 (same file area, `mapping/links.py`); (3) WI-BE8 after WI-Q2 (both touch `extraction/vision.py`; §8 one writer per file area, so not in parallel); (4) WI-IMP1 with sign normalisation before AC-INT1 (measured zero effect on links, so not first); (5) WI-BE7a with MG-11, WI-R1, WI-R2. |

Plan sections changed (`docs/accuracy-plan.md`, CRLF kept, 1,442 to 1,472 lines): §0 (date and last-decision row; detour-closed paragraph with both orders), §3.4 (pool-linking bullet), §4.10 (guard MG-11), §7.2 (WI-IMP1 required item), §7.3 (WI-Q2 output-type fix), §7.6 (WI-BE7a guard), §7.7 (WI-D4 evidence; WI-D4f status and results; AC-8b evidence; new cards WI-R1, WI-R2, WI-PERF1), §8 (one diagram line).

Open: the owner's choice of the next order; the WI-D4 design decision on self-supporting GRN reference checks; the WI-PERF1 target; S01 `08-031` confirmation.

## D90 — Chosen order after D89, no self-support in GRN reference checks, WI-PERF1 targets, WI-PV3 go, WI-Q2 design go (21 Oct 2026)

Owner, 21 Oct, after D89. Docs only in this entry.

| Decision | Choice |
|---|---|
| **Chosen order** | The engineer's D89 recommendation is accepted: **WI-PV3 ∥ WI-Q2 → WI-D4 → WI-PERF1 → WI-BE8 → WI-IMP1** (sign normalisation as data correctness, before AC-INT1) **→ WI-BE7a with MG-11 → WI-R1, WI-R2**. Recorded in §0. |
| **WI-D4: no self-support** | A GRN's own link (suggested or otherwise) must never count as evidence in its own reference check. Ledger support used by a check may come only from other, already verified or auto links, or from independent evidence. After the change the v2 pool is re-run on a disposable database and the report states how many of the 191 auto GRN to PO links stay auto, whether any outcome changes, and the hard zero. Part of WI-D4; not built now. |
| **WI-PERF1 targets** | One mapping pass under 2 minutes; the full worker sequence (mapping, ledger rebuild, mapping) under 5 minutes for one real month, the D86 pool as the benchmark; no change to link results allowed. |
| S01 `08-031` | Still pending from the owner; stays `unverified`. |
| **WI-PV3 go** | Build now: `ai.manage` enforced from the stored role on every provider and AI-settings write endpoint, the bootstrap user first, a last-admin lock, audit rows, no other P1 check switched on, the card's tests. Disposable databases only, no live database or container, no commit, own hunks only; save `work/patches/wi-pv3.patch` and a file list. |
| **WI-Q2 design go** | Design only, in parallel with WI-PV3, no model call: typed `po_ref`/`grn_ref` output, a margin/header crop for handwriting, the D88 type-aware match rule and misread normalisation; truth = the 16 typed handwritten references on 8 invoices, scored on unique documents; the experiment plan for both providers on stored answers first, then new reads, with a request-count estimate per provider before any model call (each run approved separately). Success metric: handwritten-reference reads, and invoice links unlocked in the v2 pool with 0 false auto-links. |

Plan sections changed (`docs/accuracy-plan.md`, CRLF kept): §0 (date and last-decision row; chosen-order paragraph), §7.7 (WI-D4 no-self-support bullet; WI-PERF1 target).

## D91 — WI-PV3 admin enforcement and last-manager lock (migration 0030); WI-Q2 design (21 Oct 2026)

Authorised by the owner in D90 ("GO for WI-PV3 now"; WI-Q2 design only, in parallel). Disposable databases only (fresh PostgreSQL 16.15 clusters at `127.0.0.1:15497` and, for `test_authentication.py`, `15498`; both stopped afterwards); no live database or container touched; no model call; nothing committed. Patch `work/patches/wi-pv3.patch` (17 files: 14 changed, 3 new; only WI-PV3 hunks, forward apply onto the snapshots byte-exact, reverse check passes) and `work/patches/wi-pv3-files.md`.

| Decision / item | Choice |
|---|---|
| Enforcement | `ai.manage` is checked against the stored role (`app_user.role = 'manager'` and `is_active`) on every AI-settings and provider write: settings update, key removal, provider test, request acknowledge, connect, rotate, disconnect, and the offline `POST /ai/test`. Refusal: 403 `ai_manage_required`. Every other capability keeps the P0 policy (`resolve_effective_role` unchanged; no other P1 check switched on). Read routes stay open. |
| Read-only view | Login and session payloads omit `ai.manage` for a user whose stored role lacks it; the existing Settings and AI cards already gate on it. The displayed role stays the P0 role. |
| Audit | Each refusal writes `ai.manage_denied` (actor, the stored role as the row's actor role, what was attempted, whether the user is active; never a value from the request) in its own transaction, so it survives the 403. The existing events (`ai.settings_save`, `ai.provider_config_save`, `ai.reader_strategy_change`, `ai.apply_mode_change`, `ai.consent_change`, `ai.key_remove`, `ai.test_requested`, `ai.request_acknowledged`, `ai.provider_connect`, `ai.provider_rotate`, `ai.provider_disconnect`) already cover every existing write; the card's other events belong to endpoints that do not exist yet (WI-PV4 … PV7). |
| Bootstrap user first | The seed stores the bootstrap user as `manager`. |
| **Last-manager lock** (migration `0030_last_manager_lock`) | Deferred constraint trigger `app_user_last_manager` (function `app_user_last_manager_guard()`): at COMMIT, a transaction that demoted, deactivated, moved or deleted an active manager fails with `check_violation` (`last_manager`) if the workspace is left without one. Multi-row demotion refused; a swap inside one transaction allowed; removing a whole workspace (users, then the workspace) allowed; a workspace that never had a manager not blocked; a per-workspace advisory lock serialises concurrent demotions. First built as a row-level AFTER trigger; changed to the deferred form because removing a whole workspace (the `test_ai_runner.py` fixture does) must stay possible. Real `downgrade()`. Contract: 53 tables, 40 enums, **31 triggers, 38 routines**, 176 FKs, 268 ACLs, 7 default privileges (only the routine and the trigger are new). CI previous-schema revision 0028 -> 0029. |
| Deployment note | Every existing user's stored role defaults to `manager`, so the enforcement changes nothing on a database until non-admin users get another role; there is no role-management path yet. Setting the live roles is an owner step inside AC-INT1b, after backup. |
| Tests (disposable PG 16.15) | Offline suites **1103 passed** (1100 + 3 new unit tests). Integration: group 1 (database, readyz, configuration, ingestion, assist migration, runner) **56 passed**; `test_phase4_task_411_413` **3 passed** (source-revision allowlist extended to 0030); `test_ai_settings_api` **54 passed**; `test_ai_provider_connections_api` **22 passed**; group 4 (h1–h4, vision, kpi, shadow, three migration tests) **98 passed + 1 known failure** (`test_ai_h1` …`leaves_the_rules_output_unchanged`, WI-T1); page split + vendor hygiene **7 passed + 2 known failures** (WI-D6); new `test_ai_admin_enforcement.py` **10 passed**; `test_authentication.py` **15 passed** (its own fresh cluster). Known failures unchanged and not caused by this work. |
| Static | ruff clean (app, core, tests, scripts); mypy `--strict` on the 7 touched app modules: 1 error in `api/auth.py:155` that also exists in the committed file (not this work); lint-imports 3 kept; core purity; rules hash `1e9af32f…`; engine version; golden self-check; migration safety (0029 -> 0030); drift, catalog, index, decision and table-count checks pass; `openapi.json` re-exported byte-identical. Web unit tests and `tsc` not run (no web change). |
| **WI-Q2 design** (written in the WI-Q2 card) | Typed `ref_type`/`ref_text` annotations into `extras.annotation_refs` objects (never fields, MG-9); deterministic, logged misread normalisation with pattern and date checks; a page-1 header band crop in a new `vision_bands.py` module (WI-BE8 reuses it; Q2 and BE8 stay sequential); D88 type-aware matching with every handwriting-based link a suggestion; truth = 16 typed references, unique documents, strict copy rule (27 copies, 55 pages). **Baselines on stored answers:** typed references Qwen v4 **1/16**, Gemini 3.5 flash-lite (prompt v3) **6/16**; with untyped notes 4/16 and 11/16; v2-pool links from handwriting 0/12. **Request counts:** Q2.0 offline 0; Q2.1 prompt variant on the dev set Qwen 67 / Gemini 39; Q2.1c same-day control Qwen 67 / Gemini 39; Q2.2 band crop Qwen 39 / Gemini 39; totals **Qwen 173, Gemini 117** (reduced S01 … S08 plan: 137 / 81, no regression check). Each run needs the owner's approval. |

Plan sections changed (`docs/accuracy-plan.md`, CRLF kept): §0 (date and last-decision row; Stage 4 progress D91), §7.1a (WI-PV3 status, as built, not built, deployment note), §7.3 (WI-Q2 design).

Open: the owner's review of WI-PV3 (and of WI-PV1/PV2); approval of WI-Q2 step Q2.0 (offline) and, later, of each model-call step with its request count; S01 `08-031`.

## D92 — WI-PV3 accepted; WI-PV3b recorded; WI-Q2 built and measured (Qwen); full-page Q2 prompt not adopted; handwriting links blocked by corroboration (21 Oct 2026)

Owner, 21 Oct: WI-PV3 accepted; WI-PV3b to be recorded (not built); WI-Q2 approved in order (build; Q2.0 offline; Q2.1, Q2.1c, Q2.2 on the whole dev set with Qwen, 173 requests, and Gemini, 117 requests on the separate free bench key); records; a fresh handoff. No commit; disposable databases only (`rb-safety-pg` 15497 for tests, `rb-q2-pg` 15496 for the pool, both stopped at the end); no live database or container touched.

| Item | Result |
|---|---|
| **WI-PV3b** (recorded, not built) | A management CLI command for an admin to set one user's role and active flag; runs through the last-manager lock; audit row `user.role_change`; no other P1 check; tests; placed before AC-INT1b, which uses it after the backup to set the live roles (the owner's list). |
| **WI-Q2 built** (`work/patches/wi-q2.patch`, 16 files: 10 changed, 6 new; own hunks only, forward apply byte-exact, reverse check passes) | Core `extraction/handwritten_refs.py` (typed references `po`/`grn`/`service_po`/`other`; logged normalisation rules with the text before and after; pattern, financial-year and month checks; `handwritten_refs.v1`); `vision_document` adds `typed_refs` beside the annotations, never as a field (MG-9); type carried by `looks_like` (new value `service_po_number`) rather than new `ref_type`/`ref_text` keys; `mapping/links.py`: type-aware matching (PO-type only to POs, GRN-type only to GRNs, whole number or `MM-NNN`), a handwritten-GRN hint path that needs part or amount corroboration and is always a suggestion, typed references kept out of reverse matching; prompt variants `vision.prompt.v4.q2.qwen` and `.gemini` (the current v4 prompts plus one typing rule) with `vision.schema.v5`, every v4 request and cache key unchanged; app `ai/vision_bands.py` (top 35 % of the upright page 1, masks moved, reusable by WI-BE8); bench `--vision-strategy band`. `ENGINE_VERSION` 0.1.7 → 0.1.8, `RULES_HASH` `70d529d1…`; the vision-check version is left to WI-D3. Consensus hints were not changed (consensus is not live). |
| Tests | Offline suites **1135 passed** (1103 + 32 new: 19 handwritten refs, 5 typed links, 5 prompts/schema/arms, 3 band; 2 existing variant lists extended). Integration (disposable PG 16.15): group 1 56; `test_phase4_task_411_413` 3; `test_ai_settings_api` 54; `test_ai_provider_connections_api` 22; `test_ai_admin_enforcement` 10; group 4 98 + 1 known failure; page split + vendor hygiene 7 + 2 known failures (unchanged, not this work). ruff and mypy `--strict` clean on the touched files; lint-imports 3 kept; core purity; rules hash; engine version; golden self-check; `openapi.json` re-exported byte-identical. |
| **Requests** | Qwen (company server, concurrency 1): Q2.1 **43**, Q2.1c **44**, Q2.2 **29** = **116** recorded (approved 173; fewer because copies with identical requests are answered once; calls inside a failed multi-page document before its failing call are not recorded, at most a few more). Failures: Q2.1 three answers at the output cap (two copies of the 3-page S05 invoice, INV-05 and INV-12, and the S09 PO) and one `PROVIDER_UNAVAILABLE` (RESCAN-1; not retried, the run continued); Q2.1c one at the cap (RESCAN-1); Q2.2 none. **Gemini: skipped** — `BENCH_GEMINI_KEY` was not set in the session that ran the bench; 0 Gemini requests. |
| **Handwritten references** (unique documents, strict copy rule, 16 refs on 8 invoices; S01 `08-031` at any type) | Typed correctly: stored v4 answers + new code (Q2.0) **3/16** (4/16 with notes; 9 of 26 refs normalised); stored Gemini v3 answers + new code **6/16** (13/16; 21 of 45); Q2.1 `v4.q2` full pages **12/16** (8/16 if an unread copy counts as a miss; 13/16 with notes); Q2.2 band **11/16** (11/16; 13/16; 14 of 50 normalised); **v4 full pages + band 12/16** (12/16; 13/16; 23 of 76 normalised; no typed reference on a document without handwriting in truth). |
| **Field guard** | Same-day control: today's v4 read equals the 16 Oct read on **722/725** field outcomes (headers 270/273). On the 29 copies read by both, `v4.q2` against same-day v4: headers **217 vs 229**/246, taxable 17 vs 23 (wrong 4 vs 1), CGST/SGST 21 vs 23, line rate **64 vs 78**/89, amount 79 vs 83/94, quantity 70 vs 72, HSN 38 vs 42, **accepted wrong 3 vs 0**. The full-page `v4.q2` prompt fails the WI-Q2 guard and is **not adopted**; the app readers keep v4. |
| **v2 pool** (disposable DB, worker sequence, arm B only: arm A has no handwriting) | Stored v4 + typed refs: 1/13 found (0 auto, 1 suggested), false auto 0, false suggested 0, links resting on handwriting 0 correct / 0 false. Stored Gemini + typed refs: 1/13 found (0 auto, 1 suggested), false auto 0, false suggested 0, links resting on handwriting 0 correct / 0 false. `v4.q2` + band: 1/13 found (0 auto, 1 suggested), false auto 0, false suggested 0, links resting on handwriting 0 correct / 0 false. **v4 + band: 1/13 found (0 auto, 1 suggested), false auto 0, false suggested 0, links resting on handwriting 0 correct / 0 false**. Hard zero held in every run. Why nothing unlocks: typed PO references name the right POs (S03, S04, S06, S07), but a handwritten reference counts only with same-vendor amount corroboration, and that needs aligned lines; the invoice descriptions do not align with the generic Tally stock-item names (0 aligned rows), so the reference signal stays 0. Recorded in WI-D4 as a candidate change for the owner (header-level amount corroboration for typed references, still a suggestion; PO/GRN totals need WI-IMP1). |
| Runtime note | The first mapping pass of the Q2.0 pool runs took about 2.9 h each (two runs in parallel) against about 24 min before: more WI-PERF1 evidence. |

Plan sections changed (`docs/accuracy-plan.md`, CRLF kept): §0 (date and last-decision row; Stage 4 progress D92), §3.2 (handwritten row), §7.1a (new card WI-PV3b), §7.2 (AC-INT1b roles step), §7.3 (WI-EV2 note; WI-Q2 title, as built, results, recommendation), §7.7 (WI-D4 handwritten corroboration), §7.8 (WI-D3 version note), §8 (one diagram line).

Next (owner's order): WI-D4 (no self-support; handwritten corroboration), then WI-PERF1, then WI-BE8. Open: the Gemini Q2 runs (the bench key must be exported in the shell that runs the bench); S01 `08-031`.


## D93 — Queue run of 21–22 Oct; task 1: WI-Q2 shape adopted (v4 full pages + header band, Qwen); full-page Q2 prompt rejected (22 Oct 2026)

Owner, 21 Oct: a queue of seven tasks with pre-approved budgets (Qwen 300 requests, Gemini 150 on `gemini-3.5-flash-lite` with the separate bench key), run without stopping between tasks; order for this run: WI-Q2 shape, line-alignment check, WI-IMP1 parser part, WI-D4, Gemini side by side, **WI-BE8 before WI-PERF1** (33 of 35 header misses are missing values), WI-PERF1. Disposable databases only (`rb-q3-pg`, port 15496); no commit; live stack untouched.

| Item | Result |
|---|---|
| Environment | Docker 29.8.1 up; Qwen server health 200 (no model call). **Gemini bench key file: one `.txt` in the Desktop folder named for the key, 0 bytes** → every Gemini run of this queue (task 5) is skipped; no Gemini request. |
| **Adopted shape** | App Qwen reader `vision_openai_compat`: full pages with `vision.prompt.v4.qwen` (unchanged) plus, for invoices only (`BAND_DOC_TYPES`), one call on the page-1 header band (top 35 %, `vision.band.v1`) with `vision.prompt.v4.q2.qwen` / schema v5. The band answer contributes **annotations only** (moved to page 1, vertical box edges rescaled, same type+text not added twice); its fields and lines are discarded; a band answer that does not parse is ignored and logged (`vision_band_ignored`). |
| Version (O-5) | `reader_params` gains `band` {prompt variant, top, version} for that reader only: its cache key changes; the Gemini reader's params and every bench arm's request key are unchanged (bench arms clear `band_prompt_variant`; the band stays the separate `--vision-strategy band` arm). No core change: `RULES_HASH` and `ENGINE_VERSION` unchanged. |
| Rejected | Full-page `v4.q2` prompt (D92 guard: headers 217 vs 229/246, rate 64 vs 78, 3 accepted-wrong): kept only as a bench variant; added to plan §12. |
| Not changed | Gemini reader (one call per document, no band; not measured). Live containers carry no D62+ code: no live change. |
| Tests | Offline 1141 + 2 updated v6-key tests = **1143 passed** (6 new in `test_vision_band_default.py`). Integration (disposable PG 16.15, 14 files): **211 passed, 3 known failures** (WI-T1 `test_ai_h1`; `test_page_split` ×2, WI-D6); `test_ai_vision` vLLM test and `test_ai_shadow` company-server test updated for the band call (GET, POST page, POST band; usage summed). ruff, ruff format, mypy `--strict` clean on touched files. |
| Patch | `work/patches/t1-q2-band-default.patch` (9 files, own hunks only; reverse check passes). |

Plan changed (CRLF kept): §0 (date row; queue-run paragraph; task 1 paragraph), §7.3 WI-Q2 (adopted line), §12 (row for the full-page `v4.q2` prompt).

Next: task 2 (offline line-alignment check), then task 3 (WI-IMP1 parser part).

## D94 — Queue task 2: line-alignment check (offline); Tally line description comes from STOCKITEMNAME, BASICUSERDESCRIPTION aligns (22 Oct 2026)

Offline, no model call, no code change. Question from the queue: does the Tally parser take the line description from `STOCKITEMNAME` or from `BASICUSERDESCRIPTION`, and how many truth invoice lines align with their PO/GRN lines each way?

| Item | Result |
|---|---|
| Parser today | `reconbench_core/parsers/xml.py` maps `description` through the synonym `STOCK ITEM NAME` (tag `STOCKITEMNAME`); `BASICUSERDESCRIPTION` is not mapped (it is not even kept in line extras as a known value). |
| Method | Truth invoice lines of S02–S08 against every inventory line of the truth PO and GRN voucher in the August dumps; app scorer (`normalize_item_text`, token set/sort ratio), seeded thresholds aligned ≥ 0.80, reviewable ≥ 0.60; numeric = quantity equal, rate within 0.01, \|amount\| within ₹1. Truth values, not reads. |
| (a) `STOCKITEMNAME` | PO **0/19** aligned (4 reviewable); GRN **0/29** (4) |
| (b) `BASICUSERDESCRIPTION` | PO **16/19** (18 reviewable); GRN **16/29** (23) |
| (c) qty + rate + amount | PO **14/19**; GRN **15/29** (S05 0/3 and S08 1/10: freight inside the invoice line amount, WI-R2) |
| (b) reviewable or (c) | PO 18/19; GRN 24/29 |
| Pool coverage | 5 of 455 PO lines and 11 of 456 GRN lines have no `BASICUSERDESCRIPTION`. S08's PO is a July PO (10 lines without a PO in the pool); S01 is a service voucher (excluded). |

Conclusion: WI-IMP1 should take the line description from `BASICUSERDESCRIPTION` (all lines of the list, joined), keep `STOCKITEMNAME` as the item name, and fall back to it when the list is empty. Tool `work/tools/t2_align.py`; counts `work/accuracy-intake/t2/align.json` (private). No patch (no product code). Tests not run (no code change).

Plan changed (CRLF kept): §0 (date row; task 2 paragraph), §7.2 WI-IMP1 (evidence line). Next: task 3 (WI-IMP1 parser part).

## D95 — Queue task 3: WI-IMP1 parser part (native Tally export); two reconciliation defects fixed; v2 pool re-run (22 Oct 2026)

Owner's queue (D93) authorised the WI-IMP1 parser part: line description from `BASICUSERDESCRIPTION`, PO/GRN totals from the party ledger, sign normalisation, `REFERENCE`/line `ORDERNO` → `po_refs` with multi-PO GRNs flagged, `&#4;` and control characters handled and logged, multi-voucher envelopes, masters ignored. Disposable databases only (`rb-q3-pg` 15496 tests; `rb-q3-pool` 15495 pool); no model call.

| Item | Result |
|---|---|
| Built | New `reconbench_core/parsers/tally.py` (`sanitize`, `voucher_facts`, `tally_native.v1`); `parsers/xml.py`: a file that fails the strict parse is retried once without the characters XML 1.0 forbids (files that parse today are read unchanged), each removal logged on its voucher (`XML_INVALID_CHARACTERS_REMOVED`; warning only when the tag is read for a field in its own scope, else info; removals outside vouchers in one file-level note); native voucher enrichment: description = joined `BASICUSERDESCRIPTION` lines (stock item name in `extras.stock_item_name`; fallback to the item name), full line GST rate (IGST head, else CGST + SGST; the first `GSTRATE` was the CGST half rate), debit amounts (`ISDEEMEDPOSITIVE` Yes, negative) stored as magnitudes with one `TALLY_SIGN_NORMALISED` log per voucher listing each changed field and its original text, PO/GRN totals from party + tax + round-off ledgers only when they close within ₹1 (`TALLY_TOTALS_FROM_LEDGERS`; else voucher total only + `TALLY_TOTALS_NOT_CLOSED`), Receipt Note → GRN, GRN `po_refs` = `REFERENCE` + line `ORDERNO` (a value without a digit is never a reference: Tally writes `&#4; Not Applicable`), line `po_ref` = `ORDERNO`, `TALLY_GRN_MULTIPLE_PO_REFS` when more than one. `PARSER_VERSION` phase4-parser-6 → 7. The upload size limit was **not** changed (dumps 23.1/23.4 MB < 25 MB). |
| Found and fixed while measuring | (1) `reconciliation_service` loaded every current link into the snapshot; an invoice-to-GRN link (via-GRN or handwritten-GRN path, reachable once GRNs carry totals) failed the snapshot check and crashed the run → the snapshot now reads only `inv_po`/`grn_po` edges. (2) `recon/engine.py` kept an at-risk amount on a blocked case; a partly linked invoice (status `unlinked`) violated `recon_case_atrisk_ck` → blocked cases (duplicate, unlinked) carry `at_risk` 0 and their value as `blocked_value`. Both covered by tests (`test_recon_blocked_at_risk.py` fails on the old engine). |
| Dumps | Whole-file parse 251/251 POs, 250/250 GRNs; 501/501 totals close; **1** GRN names two POs (D86 counted 6: five were `Not Applicable` markers in `ORDERNO`); with the seeded workspace synonyms every removal note is info (no marker sat in a field that is read). Against the old split parse: same documents, lines, quantities and rates; amounts positive; arithmetic QA failures 248 → 14 (PO), 247 → 15 (GRN). |
| Dev bench (stored answers, `--network none`) | Unique-document re-score **identical** to `rescore-d87-20261021.json` (439/439 counts; Qwen headers 275/313, OCR 242/313, line amount 27/47, quantity 23/44, rate 25/45); bench `hard`, `links`, `recon`, `extraction` blocks identical; the only report change is the arm label `schema_version` (D92 code). Reports `work/accuracy-reports/t3-rescore*.json`. |
| v2 pool, native import (no split, no layout hint) | **Arm B (Qwen stored v4 + band typed refs, harness substitution):** invoice links **10/13 found (0 auto, 10 suggested)**: PO 6/6, GRN 4/7 (S04, S06, S07, S08; S02, S03, S05 missed); links resting on handwriting 12 rows, all correct; **false auto 0, false suggested 0**. **Arm A (rules + OCR):** **4/13 (0 auto, 4 suggested)** (POs of S04–S07); false 0. Truth GRN→PO: 5 auto + 1 suggested (S04); S08 has no PO in the pool. Pool GRN→PO: **217 auto** (header) + 9 suggested, 24 unlinked, all agree with `REFERENCE` (D89: 191 + 35). Re-scans (arm A): **5/7** caught (as D89). Reconciliation: 8 cases (4 unlinked, 4 needs review); after re-scans 10 (6 unlinked, 3 needs review, 1 duplicate). Arm B's and arm A's last reconciliation step were completed with the fixed services on the same databases (`work/tools/q3_resume_recon.py`; links unchanged). Mapping 585 + 311 + 389 s (three passes). Reports `work/accuracy-intake/q3/t3-pool-arm{A,B}.json`. |
| Tests | Offline (with tasks 4 and 6 code) **1178 passed** (16 new parser tests, 2 recon tests). Integration on disposable PG: ingestion group (`test_ingestion`, `test_phase4_task_411_413`, `test_ac4_vendor_hygiene`, `test_page_split`, `test_kpi_summary`) 15 passed + 2 known `test_page_split` failures. ruff, format, mypy `--strict` on touched files; lint-imports 3 kept; core purity; rules hash; golden self-check. |
| Patches | `work/patches/t3-imp1-tally-native.patch` (7 files, own hunks); final `RULES_HASH` in `q3-rules-hash.patch`. |

Plan changed (CRLF kept): §0 (date row; task 3 paragraph), §7.2 WI-IMP1 (status, as built, results), §7.7 AC-8b (pool evidence). Next: task 4 (WI-D4).

## D96 — Queue task 4: WI-D4 no self-support and header-level handwritten references (suggestions only); v2 pool re-run (22 Oct 2026)

Owner's queue (D93): implement D90's no-self-support rule and let typed handwritten references create suggestions (never auto) under four conditions; re-run the pool. Disposable databases only (`rb-q3-pool2` 15494); no model call.

| Item | Result |
|---|---|
| No self-support | `MappingPO.grn_support_by_source` (from `ledger_entry`, per source document; only sources whose current GRN→PO link is auto or confirmed, plus opening balances); every content and reference check uses `_grn_support(po, source)`, which leaves out the source's own receipt; a suggested link supports nothing. Independent evidence added: `_direct_grn_po_amount`, the GRN's own lines against the PO's lines (every goods line aligned; GRN quantities at PO rates equal the GRN amounts within tolerance), in the header and line `po_ref` system checks. |
| Handwritten references | `_handwritten_header_links`: a typed PO reference → `inv_po` suggestion, a typed GRN reference → `inv_grn` suggestion, only when the type matches the target, the vendor GSTIN on the invoice equals the target's, the invoice grand total equals the target's total within ₹1 (for a PO also the total of a GRN that cites it: partial delivery) and no other document of that type has the same number and vendor; when the handwritten GRN's `po_refs` names the handwritten PO, both links record it as extra evidence. Never auto. |
| Version | `ENGINE_VERSION` 0.1.8 → 0.1.9 (recon-path rules, O-5); `RULES_HASH` in `q3-rules-hash.patch`. |
| v2 pool (native import; `work/accuracy-intake/q3/t4-pool-arm{A,B}.json`) | **Arm B (Qwen stored v4 + band typed refs, harness substitution): 11/13 found (0 auto, 11 suggested)** — PO 6/6, GRN 5/7 (S02, S03 GRNs missed); links resting on handwriting 10 rows, all correct; **false auto 0, false suggested 0**. **Arm A (rules + OCR): 4/13 (0 auto, 4 suggested)**, false 0. Truth GRN→PO **6/6 auto** (S04 now auto). Pool GRN→PO **217 auto** + 9 suggested, 24 unlinked, all agree with `REFERENCE`. **Against the self-supported run of the same data (D95), 216 of 217 auto GRN→PO links stay auto**; GRN 08-133 becomes a suggestion and GRN 08-210 (S04's) becomes auto. (The D89 count of 191 was on the old split import with generic item names; not re-run under this rule.) Re-scans 5/7; reconciliation after re-scans 10 cases (6 unlinked, 3 needs review, 1 duplicate; last step completed with the D95 engine fix). Mapping converges in 2 passes: 401 + 293 s (arm A), 332 + 297 s (arm B), against three passes before. |
| Dev bench | Bench `links`, `hard`, `recon` blocks identical to the D95 re-score (engine 0.1.9 report `work/accuracy-intake/q3/be8-control.json`). |
| Tests | 9 new unit tests (`test_d4_matching.py`: GSTIN, ₹1, uniqueness, partial delivery, type rule, cross evidence, own receipt excluded, opening balance and other sources counted, direct line evidence); mapping and reconciliation suites 123 + 48 passed; offline **1178 passed**. Integration (16 files): **213 passed, 3 known failures** (WI-T1 `test_ai_h1`; `test_page_split` ×2, WI-D6). ruff, format, mypy `--strict` on touched files. |
| Patch | `work/patches/t4-d4-matching.patch` (4 files). |

Plan changed (CRLF kept): §0 (date row; task 4 paragraph), §7.7 WI-D4 (status, as built, results). Next: task 5 skipped (Gemini key file empty); task 6 (WI-BE8) in progress.

## D97 — Queue task 5 skipped (Gemini key file empty); task 6: WI-BE8 cropped two-pass reading measured on Qwen and rejected (bench variant kept); BE8 ran before WI-PERF1 (22 Oct 2026)

Task 5 (Gemini side by side, about 117 requests) was **skipped**: the only `.txt` file in the Desktop folder named for the key is 0 bytes; no Gemini request was made and WI-EV2 E1/E2 stay open. Task 6 ran under the owner's order change (WI-BE8 before WI-PERF1: 33 of 35 header misses are missing values) on the company Qwen server; every other part offline (`--network none`).

| Item | Result |
|---|---|
| Built (bench only) | `reconbench_app/ai/vision_crops.py` (three overlapping horizontal crops 0–400/300–700/600–1000 per flagged page, masks moved into crop coordinates, answers moved back to the page; header fields and annotations added once so the core D77 rule decides conflicts; line rows deduplicated by amount/quantity/rate, a crop row overlapping another row with different values removes both, rows at a cut edge dropped; `unreadable` and page kinds of a crop not used); bench `--vision-strategy crops` (pass 2 merged) and `crops-control` (pass 1 alone, same pipeline), crops sent only with `--vision-crops-send`; the Q2 band module reused in spirit (new crop function, `header_band` untouched). Simpler than the §4.5 design (no layout-driven table bands, no repeated heading strip). |
| Pass 1 / flags | Pass 1 = the same-day v4 full-page read of 21 Oct (Q2.1c repeat 4; 0 requests). Flagged 34 pages: 5 lost to the cap (one document), 29 with line amounts not closing against the taxable total; `fewer_rows` never fired. |
| Requests | Estimate logged before sending: 90 (dry run), then 102 (34 pages × 3; two documents had been re-read); the first send was capped at 100 and sent nothing; second run (cap 110): 72 answered + 6 `PROVIDER_UNAVAILABLE`; one retry of those 6 (cap 10): 6 answered. **Qwen total 84** (copies with the same request key answered once). 0 at the answer cap. Gemini 0. |
| Result (dev set, unique documents, strict rule; `work/accuracy-intake/q3/be8-{control,crops}-rescore.json`) | Control → crops: headers **101/136 → 99/137** (wrong 2 → 4), header amounts 27 → 24/46, line amount 28/47 → 28/47, quantity 24/44 → 24/44, rate 26/45 → 26/45 (with copies each +8), references 3 → 4/6, HSN 13 → 14/23 (wrong 1 → 2), **accepted-wrong 0 → 1** (101 accepted), documents lost to the cap **1 → 0**. OCR headers 97/136 and 96/136 (the arms see different copies). |
| Verdict | Fails the guard (fields fell; accepted-wrong not 0): **not adopted**; the app reader stays v4 full pages + band (D93). Kept as a bench variant; recorded in plan §12. The gain is the cap case only; the open-totals trigger added 190 header items that created conflicts. Candidate for a second try (owner decides): crops only for pages lost to the cap, header items not merged. |
| Same-day control vs D84 baseline | The 21 Oct v4 read scores headers 101/136, line amount 28/47, quantity 24/44, rate 26/45 (D84, 16 Oct read: 101, 27, 23, 25). |
| Tests | 9 unit tests (`test_vision_crops.py`); bench arm tests updated (crops allowed for vLLM, refused for Gemini); bench-eval suite 32 passed; offline 1178 passed. |
| Patch | `work/patches/t6-be8-crops.patch` (5 files). |

Plan changed (CRLF kept): §0 (date row; tasks 5 and 6), §7.3 WI-BE8 (status, as built, result), WI-EV2 (task 5 note), §12 (row). Next: task 7 (WI-PERF1).

## D98 — Queue task 7: WI-PERF1 matcher and ledger runtime (targets met, identical outputs); queue run closed (22 Oct 2026)

Last task of the owner's queue (D93); disposable databases only (`rb-q3-pool2` 15494, `rb-q3-pool` 15495); no model call.

| Item | Result |
|---|---|
| Profile | One mapping pass on the converged task 4 workspace: 1,053 s under cProfile, 1,048 s of it in `_reverse_matches` → `_po_matches` (each text window against each PO, both canonicalised: about 98 million `canonicalize_po_number` calls). Ledger drain on a fresh workspace: 362 s for 251 POs; each PO rebuild reloaded all current documents (125,751 document builds) and recomputed the governing POs (about 68,000 queries). |
| Built | `mapping/links.py`: `_po_index` (POs by canonical number, snapshot order kept) for reverse matching; `_canonical_text` and `_without_leading_label` memoised (`lru_cache`, pure functions of the text). `ledger_service.py`: `_CurrentSnapshot` (current documents and governing POs) loaded once per `drain_dirty_scopes` batch and reused for each PO; `rebuild_po` alone unchanged; documents now read once before the per-PO locks of a batch (ledger writes change neither documents nor governance; a concurrent document change marks its scope dirty again). No version bump: outputs identical (part of 0.1.9, unreleased). |
| Result (D86 pool, native import, both arms) | Pass 1: mapping **26 s** (was 401 s in task 4, 585 s in task 3), ledger **5 s** (was 442 s); pass 2 mapping 18 s, ledger 2 s; converged. **Full worker sequence ≈ 49 s** (target < 5 min); one pass < 2 min. Whole arm A including ingest (116 s), re-scans and reconciliation: 215 s (task 4: about 2,800 s). **Links 595/595 (arm A) and 589/589 (arm B), ledger rows 455/455 identical** to the task 4 run (compared by document numbers, link type, level, line, state, method, confidence; ledger by PO line figures). Arm results unchanged: B 11/13 (0 auto), A 4/13, false 0, re-scans 5/7. |
| Tests | Offline **1178 passed**; integration (16 files) **213 passed, 3 known failures** (WI-T1; `test_page_split` ×2, WI-D6); ledger/mapping integration files 43 passed; ruff, format, mypy `--strict` on touched files; lint-imports 3 kept; core purity; rules hash `5fa08179…`; golden self-check. OpenAPI not regenerated (no API change). |
| Patch | `work/patches/t7-perf1.patch` (2 files); final `RULES_HASH` in `q3-rules-hash.patch`. |

**Queue summary (D93–D98).** Adopted: v4 full pages + header band for the Qwen reader (D93); native Tally import (D95); no self-support and header-level handwritten suggestions (D96); matcher/ledger speed-up (D98). Rejected: full-page `v4.q2` prompt (D93), WI-BE8 crops as the default (D97). Skipped: Gemini side by side (key file empty). Qwen requests 84 of 300 (BE8 only); Gemini 0 of 150. Invoice links in the v2 pool rose from 1/13 to **11/13 (all suggestions)** with the Qwen readings and 4/13 with rules + OCR; false links 0 throughout. **PH2 gate (unique documents, same-day Qwen v4 read):** headers 101/136 (gate ≥ 126), line amount 28/47 (≥ 40), quantity 24/44 (≥ 36), rate 26/45 (≥ 36), documents lost to the cap 1 (gate 0), accepted-wrong 0 (met) — **not met**. Handwritten references 12/16 (v4 + band, D92 measurement; not re-measured).

Plan changed (CRLF kept): §0 (date row; task 7 paragraph; next), §7.7 WI-PERF1 (status, profile, as built, result). Next (owner decides): WI-BE7a with MG-11, then WI-R1/WI-R2; open decisions listed in the session report.

## D99 — Live deploy for the 22 Oct demo (AC-INT1 expedited); queue fan-out fix; app rehearsal on a disposable stack (21 Oct 2026)

Owner decision, 21 Oct: no separate demo stack; all built work goes to the **live** app tonight from `phase2-hosted-ci` because it is demoed on 22 Oct; the live app is not used for business decisions, so short downtime is acceptable. Safety first: full backup, tested restore, dry-run migration on a copy and a ready rollback before any live change. This **expedites AC-INT1a/b for the demo** and waives the D83/D84 deployment gate (WI-PV5 not built) on two conditions: nothing from an LLM is auto-accepted on live, and the workspace auto-verify list is cleared before any upload. The login file was not present, so no one signed in to live in this session: the AI settings steps and the live smoke test are an owner checklist (`work/demo/setup-checklist.md`), rehearsed on a disposable stack.

| Item | Result |
|---|---|
| Backup (21 Oct 14:39 UTC, live at 0024, 40 documents, all already withdrawn) | `C:\Users\jainm\reconbench-archive\`: `live-db-20261022.dump` (pg_dump -Fc, 676,319 B, SHA-256 `de7ad0c4daf6…`), `live-globals-20261022.sql` (`a4bde2a4b010…`), `live-minio-20261022.tar` (85,207,040 B, `67e59b9a70d8…`), `.env` copy (`601c4f011c33…`), `deploy/secrets` copy; images `reconbench-{api,worker,ai-worker,setup,web,minio}:pre-20261022`; rollback runbook in `work/handoff-20261022-live.md` §2 |
| Restore test (disposable `rb-live-copy-pg`) | all 54 tables' row counts identical to live |
| Dry-run migration on the copy (setup script verbatim, candidate image) | 0024 → 0030 in 7 s; Alembic autogenerate no drift; catalog contract differs only in 31 extension-routine owner names (environment); phase-2 catalog, index, table-count checks pass; Gemini key moved to its card and still unseals; Excel export builds (5 sheets, 6 cases) |
| Consent after migration | not current under the new code (old code: current) — **by design**: the fingerprint covers the vision prompt version/variant, changed since the 15 Oct consent. Effect: no document AI request and no auto-verification until an admin re-saves AI settings with consent |
| Re-processing on start | none (0 waiting jobs; no periodic re-parse) |
| Code (commit `8b972df`, tag `live-20261022`, local) | snapshot of all work since `8fd9c3c` (192 files; scan: 0 keys/tokens; test keys all synthetic; no new truth GSTIN or party string); `compose.yaml` forwards `RB_AI_VLLM_CALLS`/`RB_AI_VLLM_ENDPOINTS` (api, worker, ai-worker) and `RB_AI_VLLM_BASE_URL` (ai-worker); an empty base URL counts as unset; `.env` gains the three vLLM entries (Qwen server of §3.5); Gemini settings untouched. Offline 1178 passed (+1) |
| Live deploy (≈ 14:58 UTC) | `docker compose up -d --build`; setup 0024 → 0030; api healthy/ready; web 200; rules hash `5fa08179…` in all containers; data row counts unchanged; the Gemini card became Connected through the app's hourly synthetic health check (2 requests tonight) |
| **Found: ledger job fan-out** | importing the August PO export queued **31,196** `recompute_po` jobs (every mapping step re-queued one per dirty PO; ≈ 0.8 jobs/s on the single worker ≈ 11 h, blocking every other queue). Fix (commit `15ab05a`, tag `live-20261022-fix1`): per-PO and per-workspace queueing locks on `recompute_po` / `drain_dirty_scopes` (indexed check for a waiting job; insert inside a savepoint). Same results; ≈ 500 jobs, ≈ 10 min. Offline 1182 passed (3 new); integration `test_ingestion` + `test_phase4_task_411_413` 7 passed. Redeployed to live (setup no-op, data unchanged) |
| Owner scripts (tested on the disposable stack) | `work/demo/live_setup.py setup`: clears the auto-verify list + re-consents (Gemini settings), tests Gemini, connects Qwen (Connected), sets the page reader to Qwen; `reader gemini|vllm` switches; no new wiring was needed (`primary_reader` exists since D65) |
| App rehearsal (disposable stack, same code and files, fresh DB) | S07 Qwen reading **reaches the document page**: 42 suggestions (9 header fields, 33 line values, all with page boxes; 30 equal the parsed value), provider vllm / qwen3.5:9b; both truth handwritten references typed correctly (+1 extra PO-typed note). PO.xml 251, GRN.xml 250 imported natively; Qwen read 7/8 invoices (S06: `invalid_output`, rules + OCR kept); GRN→PO header **217 auto + 9 suggested** (= D96); invoice links **4/13, all suggested, 0 false** (= bench rules arm); reconciliation 8 cases (4 needs review, 4 unlinked; = D95/D96 arm A); S05 needs review (`GRN_OVER_PO`, `TOTAL_MISMATCH`, `MATH_ERROR`, …; WI-R1/WI-R2) |
| **Difference from the bench** | the bench's 11/13 used Qwen values and typed references inside the matcher (harness substitution); in the app a reading in suggest mode never enters the document, so the matcher sees rules + OCR values only. Not wired tonight (it would put LLM output into matching without review); needs WI-BE7 (merge with guards) or reviewer-accepted values |
| Requests | Qwen 27 (all HTTP 200; 1 answer invalid; 0 retries) of 150; Gemini 2 (live health checks) of 150; Gemini bench (step H) not run |
| Hard zeros | false links 0; values auto-accepted 0; nothing from an LLM applied as a value on live (live has no new document) |

Provisional decisions (owner to confirm): no SQL change to live settings (the list is cleared by the owner's first step; until then consent is not current, so nothing is sent or auto-verified); the hourly Gemini health check is accepted on live; the queue fix was deployed to live; the linking gap is reported, not wired; plan and decision-log records left uncommitted.

Next: owner runs `work/demo/setup-checklist.md` (E then F, ≥ 1 h before the demo); then WI-BE7a with MG-11 (the path from 4/13 to the bench's 11/13 in the app), WI-PV5, WI-FE1a/b.

## D100 — Demo workflow on live (fix2): one-click confirm, confirmations kept, engine 0.1.10, run view, AI providers screen (22 Oct 2026)

Owner brief, night of 21–22 Oct: make the target workflow (Settings → upload → Mapping one-click confirm → Reconciliation) work on live by clicking, for the 22 Oct demo; fixes general only, each with a test; no rule, tolerance or threshold loosened; 0 false Matched, 0 false links, 0 LLM values accepted without a person; all testing on throwaway copies of live (fresh read-only dump, copy-only test user, Qwen on, Gemini gate off); web changes developed outside `web/` and applied in one step after the gate. Commit `1f821c8`, tag `live-20261022-fix2` (local, no push); live rebuilt (api, worker, ai-worker) and `web/src` patched at ≈ 01:10 IST.

| Fix | What changed | Test |
|---|---|---|
| F1 Mapping crash | PO dropdown read `event.currentTarget` inside a state updater (TypeError → blank page); same pattern in the CSV dialog; error boundary around routed pages (message + Reload) | web test chooses a PO and the row stays |
| F2 One-click invoice confirm | `POST /documents/{id}/links/confirm` (listed link id + version, one PO, one transaction, each link through the per-link confirm path and audited; stale version 409, foreign PO / repeated link / AI-suggested link 422, nothing confirmed); document listing returns `line_nos`; row shows the PO, "n of m lines linked", lines needing attention, line links | integration (409/422 confirm nothing; audit rows = links); web tests |
| F7 Confirmations kept | `_persist_resolution` kept only *manual* confirmed links: any later mapping pass for that document turned a person's confirmed content link back into a suggestion (seen on the copy: the owner's confirm reverted after other uploads). A confirmed link now stays while the resolver proposes the same pairing (document, line, PO, link type); a changed pairing is superseded as before | integration re-run assertion fails on the old code, passes now |
| F6 Engine: confirmed link | a person-confirmed link below 0.95 was still flagged LINK_INFERRED and low confidence (spec: confirming resolves LINK_INFERRED); suggested/auto links unchanged | 3 unit tests |
| F8 Engine: GRN double count | a GRN with a header link and line links to the same PO was added to the PO ledger once per link → false GRN_OVER_PO on every linked demo case; each GRN line now counts once per PO (line link first, else header) | 2 unit tests (fails on old code; a real over-receipt is still flagged) |
| F3 Run UX | reconciliation jobs queued with priority 10 (ledger/mapping/ingest default 0); run view lists that run's cases (`GET /cases?run_id=`) for all dates; progress panel as before | unit (priority), integration (run filter) |
| F5 Settings | AI providers panel: Qwen and Gemini cards (state, model, server, Test connection), page-reader switch with the consent text for the new destination, text-assist line; banner names page reader and text assist separately; Home card consent preview uses the saved reader (it asked for consent and would fail while the reader is Qwen) | web tests |
| F4 Accept a suggestion | already present (Use suggested → draft → save; `document.correct` audit with `ai_call_id`; mapping re-queued); no change | rehearsal |

ENGINE_VERSION 0.1.9 → 0.1.10; rules hash `ffa8e9cb2b87…`; OpenAPI + web types regenerated (additive).

| Rehearsal 2 = gate (fresh copy of live, final code, clicks) | Result |
|---|---|
| Matched | 2 invoices (Phoenyx; Om Shakti after the reviewer took the suggested invoice number); both = truth PO and GRN, expected = invoice taxable, variance 0 |
| Needs review | Ingram partial delivery (ITEM_UNSURE, LOW_CONFIDENCE_DATA, MATH_ERROR taxable misread, TOTAL_MISMATCH freight in line amounts; PO lines 2–3 stay open); Vijayshree (TOTAL_MISMATCH = exactly the lump-sum line 7, LOW_CONFIDENCE_DATA two lines at 0.65 OCR) |
| Unlinked | Prime (handwritten PO only), Amazon (0 lines parsed); blind S08 (July PO not in the app): extraction correct, Unlinked NO_PO_LINK |
| Hard zeros | wrong links 0, false Matched 0, AI values accepted without a person 0, consensus runs 0, Gemini calls 0 |
| Timing | run 4–6 s, including one while 99 ledger jobs waited |
| Literal scan (non-test diff) | 0 truth literals, 0 GSTIN-shaped strings |
| Requests | Qwen ≈ 40 HTTP calls across both copies (19 counted in rehearsal 2; rehearsal 1 logs lost on container restarts) of 120; Gemini 0 |

Checks: offline suite 1188 passed; integration `test_ingestion` + `test_phase4_task_411_413` 7 passed (disposable PG); web tsc, eslint, money-math, vitest 40/40; ruff/format/mypy --strict on touched files; rules hash, engine version, core purity. Live after apply: healthz ok, readyz ready, rules hash in all three containers, Alembic 0030, reader vllm/single, auto-verify list empty, apply mode suggest, queue empty, login page renders (0 page errors). Not run: Gemini anything; live login.

Provisional decisions (owner to confirm): F7 and F8 counted as real bugs (not loosening); uploads right after a correction wait behind ≈ 100 ledger jobs (≈ 10 min) because ingest keeps default priority (mapping reads ledger support, so reordering was not safe tonight); an accidental `git stash` on `mapping_service.py` was popped immediately (nothing lost).

Open items (proposals, not built): P1 lump-sum line rule (amount-based payable when invoice and PO line both have no quantity); P2 "Save and mark reviewed" marks only PO/GRN refs user-verified, so LOW_CONFIDENCE_DATA stays after a person's review; P3 `consensus_enqueue_failed TypeError` in the AI worker (advisory, auto-verify empty); P4 ingest/parse priority over ledger jobs; deferred by the owner: handwritten-reference link hints, WI-BE7, bundle-PDF OCR artefacts, "PO document is not current" job failures, hourly Gemini health check. The branch exists on origin (`origin/phase2-hosted-ci`, HEAD now 6 commits ahead; no push tonight).

## D101 — Read-only terminal trace tool (22 Oct 2026)

Built `reconbench_tools.trace` (`doc`, `run`, and `follow` modes) directly from the trace tool plan.
1. **Security & Read-Only Guarantees:** Connects with `default_transaction_read_only=on`, `statement_timeout=3000ms`, `lock_timeout=500ms`, `application_name=reconbench_trace`. Verifies `SHOW default_transaction_read_only` returns `on` on connect, else exits code 2. Every query runs inside `SET TRANSACTION READ ONLY`. Pure `SELECT` queries bounded with `LIMIT` and scoped by `workspace_id`.
2. **Features:**
   - `doc <invoice number | document id>`: traces document ingestion status, AI reader metadata/suggestions/typed handwritten refs, header field comparisons against stored extraction, line items, active links, current PO ledger, and 3-way reconciliation match.
   - `run <latest | run id>`: shows run summary and per-invoice case statuses and reason codes.
   - `follow [--interval 3]`: polls queue counts (`procrastinate_jobs`) and recent ingestion/parsing/linking/recon events.
   - Common flags: `--mask` (redacts GSTINs, vendor names, monetary amounts), `--no-color`, `--workspace-id`, `--db-url`.
3. **Validation & Isolation:** Unit tests (`test_trace_format.py`, 16 tests) and integration tests (`test_trace_readonly.py`, 3 tests) passed against disposable PostgreSQL (port 15499) with zero writes permitted. Verified live against read-only session on port 15432. No existing application, worker, or configuration files were modified.

## D102 — WI-TRC2 pipeline log watcher and read-only login (23 Oct 2026)
- Added idle, read-only pipeline-log watcher container (WI-TRC2) for live terminal visibility.
- Configured 4 environment toggles: RB_PIPELINE_LOG (default off), RB_PIPELINE_LOG_VALUES (masked), RB_PIPELINE_LOG_INTERVAL (3s), RB_PIPELINE_LOG_ASCII (0).
- Owner decision departs from main plan Phase 7.6: show mode displays vendor names and amounts in terminal logs; GSTIN is masked to last 4 in both modes; log rotation set to 10 MB x 3.
- Created dedicated PostgreSQL login reconbench_logreader (NOSUPERUSER, default_transaction_read_only=on, statement_timeout=3s, idle timeout 30s).
- Granted SELECT on 26 tables plus app_user(id, display_name); blocked from secret, session, rate_limit_event, idempotency_key, and password columns.
- Both pipeline_log watcher and trace tool now wire to reconbench_logreader instead of superuser.
- Pre-deployment binary database backup saved to C:\Users\jainm\reconbench-archive\live-db-20261023-1538.dump (3.49 MB, 705 items).
- Pre-pilot findings flagged: api/worker/ai-worker run as superuser reconbench (main plan 2.7), and backend image runs as root (main plan 7.1).
- Fixed trace.py AttributeError on unversioned documents (line 196/224) and verified with automated unit tests.
- Zero AI requests made during implementation and deployment verification.

## D103 — Post-demo decisions (owner, 26 Oct 2026, final): PD plan approved as revised; Task C defined; batch PD-B1 queued

Source: the owner's decisions of 26 Oct on the post-demo audit (`work/postdemo-audit-20261026.md`, PD-AUDIT). Where they differ from the audit's proposed defaults PD-D1 … PD-D10, these win. The plan is `docs/accuracy-plan.md` §15 (appended with this entry). The main plan stays read-only and unchanged.

**Decisions.**
1. **Digital invoices are always read by rules + text layer.** Never an LLM call, whatever any setting says.
2. **Scanned invoices: one switch in Settings**, "Scanned invoices: read with rules / read with AI", **default rules**. No per-upload choice. Digital vs scanned stays automatic (per page); a PDF with any scanned page counts as scanned.
3. **AI reading runs only on scanned invoices with the switch on AI.** Never on digital invoices, PO files or GRN files.
4. **Handwritten values the AI reads** (PO/GRN numbers and other values) are used in mapping and 3-way matching **only after one click by a person**: references become **link suggestions** (WI-D4 rule plus the self-support guard); other values become **suggested field values**. Never automatic. This changes MG-9 for person-confirmed values.
5. **vLLM can be connected from the frontend** on Home and Settings (URL from the allowlist + model): one shared component, admin only, Connected only after a server-side test.
6. **One active AI provider** (Gemini or vLLM) for every AI call; the inactive one gets 0 requests. On live: no shadow provider, no per-upload provider override, no provider fallback, no re-run with the other provider.
7. **Mapping and reconciliation make 0 AI calls**: H2, H3, H4 off (code kept), proven by a test.
8. **Answers to the audit's §11:** Q1 Task A → decision 1 (no setting); Task B → decision 6; Task C = "links appear fast" (below). Q2 default rules. Q3 AI reading failed → "Waiting for reading"; nothing becomes current until a person picks Retry or "Read with rules". Q4 dropped (digital is never read by AI; PD-2g retired). Q5 PO/GRN always rules. Q6 yes (H2–H4 off). Q7 see decision 4. Q8 accepted; "Verify all" only where the AI and OCR values agree. Q9 the vLLM URL is visible to admins only; others see "Company server". Q10 taxable vs expected taxable, with the grand total in its own column. Q11 AI reading uses vLLM; Gemini may be the active provider for reading only after the owner re-confirms O-25 for this use. Q12 both a timed Undo and a permanent "Link again". Q13 accepted (method/provenance column in the export). Q14 accepted: AI values may become current before PH4, only after PD-2a and PD-2b, with the switch defaulting to rules until PD-5c measures. Photo uploads (JPG/PNG): open, not in scope.
9. **No duplicate ingestion.** The same invoice, PO or GRN is never ingested twice. The exact same file (same bytes) already uploaded → not ingested again. A PO or GRN with the same number → no second document: unchanged → skipped; changed → a new version of the same document (an amendment). An invoice with the same vendor, invoice number and financial year → no second document; a person may choose "Replace with this file" (a new version). An uncertain match (e.g. a re-scan whose number was read differently) is held as "Possible duplicate of …": not mapped or reconciled until a person chooses Discard or Keep. Deleted (withdrawn) documents never count. The same number from a different vendor is not a duplicate. Nothing is dropped silently: every skipped or held file shows the reason and a link to the original, and is audited.

**Task C (definition) = "links appear fast" (card PD-0b-3).** Measured on live, 23 Oct: every upload, correction or link action queues about 255–261 `recompute_po` jobs on the mapping queue (≈ every PO); a correction at 16:12:07 got its re-mapped links only at 16:15:59, behind those jobs. Fix the cause, not the order: scope the ledger recompute to the POs whose inputs changed (the POs the document is linked to now and before the change, its vendor's candidate POs, POs whose GRN or link inputs changed), preferably identical by construction (skip a PO whose input fingerprint is unchanged). No queue-jumping (owner decision P4, 22 Oct). Nothing may read a ledger row older than the latest change to its inputs; a full-recompute command stays as the safety net. Target: links visible ≤ about 60 s after an upload or a correction; links, ledger rows and reconciliation outcomes identical to the full recompute.

**Departures** (recorded rule → decided rule; cards in `accuracy-plan.md` §15):

| ID | Recorded rule (source) | Decided rule | Card |
|---|---|---|---|
| DEP-1 | Main plan Step 4.17: any AI failure falls back to rules; the run completes on rules | AI reading failed → the document is "Waiting for reading"; nothing becomes current until a person picks Retry or "Read with rules" (audited); the run never blocks | PD-2f |
| DEP-2 | Main plan 4.17 SCN-20 / property #8: with any stub output, maths and verdicts equal the rules-only run | holds for everything read by rules (all digital invoices, PO/GRN files, scanned invoices with the switch on rules); for AI-read scanned invoices: nothing Matched or auto-linked without a person's click | PD-3b |
| DEP-3 | Main plan 4.17: H1 is a fallback; hooks never change a verdict; AI off by default | for scanned invoices with the switch on AI the reader is the primary extractor and its values change verdicts after a person verifies them; the switch defaults to rules (AI stays off by default); H1 never runs | PD-2c, PD-2e |
| DEP-4 | Main plan 4.17 builds H1–H4 as product hooks | H2, H3, H4 off (code kept, proven by a test); H1 not run | PD-3a |
| DEP-5 | accuracy-plan §4.3 `arb.v1`: OCR leads every field until held-out confirmation | AI-read scanned invoices: the LLM leads (`arb.llm`), OCR is a visible cross-check; digital invoices unchanged (rules + text layer) | PD-2e |
| DEP-6 | §4.10 / §6 PH4: no LLM value in a document version before PH4; D83 "no deploy before WI-PV5" | accepted (Q14): AI values may become current before PH4, only after PD-2a and PD-2b; no auto-verify before PH4; switch default rules until PD-5c | PD-2a, PD-2b, PD-2e |
| DEP-7 | D80 / §4.15: separate `extraction_provider` and `assist_provider` | one active provider for every AI call | PD-1a |
| DEP-8 | D80 / §4.15.5: per-upload provider override (`batch.provider_override`) | no per-upload provider choice and no per-upload method choice (decision 2) | PD-1a, PD-2c |
| DEP-9 | D80 / §4.14, WI-PV5, WI-BE6, AC-LIVE-S: live + shadow provider | no shadow on live; `reader_strategy = shadow` refused | PD-1a |
| DEP-10 | D80 / WI-PV6: re-run with the other provider | not on live; switching providers affects new readings only | PD-1a |
| DEP-11 | O-21 / §1: fallback to the other provider (`allow_provider_fallback`) | no provider fallback; the setting is ignored | PD-1a |
| DEP-12 | O-6 (D80): provider down → the OCR version stays current with a visible flag | "Waiting for reading" (Q3); the rules result is not current until a person chooses it | PD-2f |
| DEP-13 | Task A (22 Oct queue) | decision 1: digital invoices always rules + text layer, no setting; PD-2d: 0 LLM calls for digital invoices, PO/GRN files and scanned invoices with the switch on rules | PD-2d |
| DEP-14 | Task B (22 Oct queue); D99/D100: handwritten-reference link hints deferred ("would put LLM output into matching without review") | Task B → decision 6 (PD-1a); handwritten references from an AI reading become link suggestions (WI-D4 + self-support guard) and count only after a person's click | PD-1a, PD-2h |
| MG-9 | §4.10 / WI-Q2: handwritten marks stay annotations, never fields | person-confirmed handwritten values: references → link suggestions; other values → suggested field values; both count in mapping and 3-way matching only after a person's click; never automatic | PD-2h |
| PD-D1 (audit draft) | method chosen per upload in the upload dialog | replaced by one Settings switch for scanned invoices (decision 2); the method actually used is stored per version and survives a person's save (F9) | PD-2c |
| PD-2g (audit draft) | measure image vs text-layer input for digital PDFs in AI mode | retired: digital invoices are never read by AI (decision 1, Q4 dropped) | — |
| AC-8b | re-scan guard: a re-scan of a known invoice must not become a second document (dev evidence 5/7 caught on the bench) | extended by decision 9 to every upload: exact-file hash, PO/GRN identity with amendments, invoice identity (vendor + canonical number + financial year), "Possible duplicate of …" hold with Discard / Keep, within-upload duplicates, a database guard against simultaneous uploads | PD-0b-4 |

Not departures (checked): the hard zeros (0 false Matched, 0 false links, 0 AI values accepted without a person's click) stay; D42 auto-verify stays empty; O-25 stays (Gemini reading needs the owner's re-confirmation); the main plan text is not edited.

**Order (accuracy-plan §15.1):** (1) batch **PD-B1** = PD-0b-1, PD-0b-2, PD-0b-3, PD-0b-4, PD-4a, PD-4b; (2) PD-1a + PD-1d + PD-3a (they change the consent fingerprint: deploy on a day without a demo, then the owner re-consents); (3) PD-2a + PD-2b + PD-4d; (4) PD-1b + PD-1c; (5) PD-2c + PD-2d; (6) PD-2e + PD-2f + PD-2h; (7) PD-5.

**Batch PD-B1 rules (owner brief, 26 Oct):** live deploy pre-approved after each task's gate (two rebuilds at most: deploy 1 = PD-0b-1 + PD-0b-2 + PD-0b-3; deploy 2 = PD-0b-4 + the PD-4b backend, with the web changes of PD-4a, PD-4b and PD-0b-4 applied in one step); 0 AI requests; no consent or live-setting change; uploads only on throwaway copies of live; additive migrations only; at most two fix-and-retry rounds per failed gate, then the task is parked.

## D104 — Batch 3 live: PD-2a + PD-2b + PD-4d (28 Oct 2026)
- PD-2a: `FieldOrigin.AI`; values read by AI are stored with origin `ai` and are never system-checked. Migration 0032 only widens the `field_evidence.origin` check (no row changed).
- PD-2b: the data gate counts a person-verified value as confidence 1; any key value read by AI (origin or source `ai`) that no person verified makes the case Needs review with the new reason `AI_VALUE_UNVERIFIED` (migration 0033 adds it to every workspace). "Save and mark reviewed" verifies only the key values shown on the screen, never a value read by AI. Engine 0.1.10 → 0.1.12; rules hash d401c4ba0d74bc4cc04b48a61cd61f204f09ee4a83c5d9f6dc943bec9f3e03f0.
- PD-4d: the document page lists each value read by AI beside the OCR reading of the same file, with its page. "Verify" per value and "Verify all" only where the two readings agree (Q8); the server checks the same rule again and refuses a stale screen; each verification is audited (`document.verify_ai_values`) and the value itself never changes.
- Checks: two independent runs (advisor and owner PC) of unit, integration and web tests, golden run (0 false Matched) and migration safety; planted bugs caught: PD-2b 9/9, PD-4d backend 8/8, PD-4d web 4/4. Remaining integration failures also fail on the old code (unchanged).
- Dry run on a throwaway copy of live: 4 cases, 0 verdict changes, nothing newly Matched. Values read by AI before this batch carry source `ai`; the gate looks at origin or source, so they also need a person's click. Live holds no value with source `ai` today.
- Deploy: fresh backup `live-db-20261029-0049-b3.dump` (3.7 MB, 717 items); migration 0031 → 0033 through the setup service; api, worker and ai-worker swapped (rollback tags `:pre-b3`); web served directly from the folder; 0 AI requests; AI settings and consent fingerprint unchanged; owner check OK. Commit 48b081a, tag live-20261029-b3, pushed.
- Notes: the read-only login reconbench_logreader cannot read `reason_code` (the first deploy run stopped on it before any change); deploy reports must mask the company AI server address.
- Hard zeros unchanged: 0 false Matched, 0 false links, 0 AI values accepted without a person's click.

## D105 — PD-1b built; one real capability check on the company vLLM server (29 Oct 2026)
- PD-1b (step 4 of plan §15.1), built and tested, not committed or deployed: the company server's Connect/Test queues two logged synthetic requests, the text test (proves the strict JSON schema; `GET /models` gives the context window) and the new image test (`provider_image_test.v1`, a generated PNG of "472"; the answer must name the number). The card stores `supports_json_schema`, `supports_vision`, `context_window` (additive migration 0034; NULL = not checked; any server, model or key change resets them). AI reading with the company server is refused (`provider_capability_missing`) until both are true. A failed image test never changes the connection state or the workspace status. Tests: unit 1402 passed, scenarios/property 47 passed, AI integration groups passed on a disposable cluster (15499); known failure WI-T1 unchanged.
- Owner go in chat, 29 Oct 2026, replying to "Run the one-time real check against the company vLLM (2 synthetic requests)?": "Yes, run it". Scope: the company vLLM server (the allowlisted `RB_AI_VLLM_ENDPOINTS` address, masked here), model `qwen3.5:9b`, from this PC over the company VPN with the PD-1b adapter and fixtures: 1 `GET /models` + 2 `POST /chat/completions` (text fixture, then image fixture), 32 output tokens each, thinking off, one attempt each, no retry, no Gemini call, no live database or setting written. Only synthetic fixtures are sent.

- Result (29 Oct 2026, exactly the budget above: 1 `GET /models` + 2 completions, no retry, 0 Gemini requests, nothing written to live): `GET /models` ok in 656 ms, serves `qwen3.5:9b` with `max_model_len` 8192. Text test PASS (`{"status":"ok"}`, finish `stop`, 43 tokens in / 12 out, 813 ms, no reasoning text). Image test PASS (answer `{"number": "472"}`, finish `stop`, 128 tokens in / 9 out, 1125 ms, no reasoning text). So on the live company server a PD-1b Test would store `supports_json_schema = true`, `supports_vision = true`, `context_window = 8192`. The synthetic image costs about 128 input tokens; the `synthetic_image` limit keeps its conservative 1,000-token estimate.

## D106 — PD-1b + PD-1c live (29 Oct 2026)
- PD-1b: the company server card stores what its Test proved (reads page images, structured answers, context window). AI reading can be switched on only after both checks pass. The workspace connection state still comes from the text test only.
- PD-1c: one shared provider component on Home and Settings. A card says Connected only after the server-side test; the key field is write-only; only managers (`ai.manage`) can change anything.
- Masking: `GET /ai/settings` hides the company server address from anyone without `ai.manage`: `base_url` is null, the allowlist is empty, and the destination and consent texts name no host. Fingerprints are unchanged. Managers still see the address.
- Checks: vitest 60 passed, unit 1333, scenarios 43, AI integration 82, mypy 80 (same as HEAD). An independent run on another machine matched.
- Deploy: backup `live-db-20261029-b4.dump` (3.7 MB), tested restore with identical row counts, migration 0033 -> 0034 through the setup service, api, worker and ai-worker swapped (rollback tags `:pre-b4`). AI settings version 22 and the consent fingerprint unchanged, 0 new AI requests. Commit a90ee42, tag live-20261029-b4.
- Owner check: Home page and the company server Test connection both worked on live.

## D107 — PD-2c + PD-2d built: Settings switch for scanned invoices, 0 LLM calls outside AI reading (29 Oct 2026)
- Step 5 of plan §15.1, built and tested, not committed or deployed. Owner choices in chat (29 Oct): H1 is never queued but stays in the consent hook list (no fingerprint change, no re-consent); the switch may be on AI only with the company server active and both PD-1b capabilities proven (Gemini waits for O-25, Q11); PD-4c limited to the method chip and the export column; integration tests on a disposable PostgreSQL in Docker.
- PD-2c: `ai_setting.scanned_invoice_reader` ("Scanned invoices: read with rules / read with AI", default rules, `ai.manage` only, audited as `ai.scanned_invoice_reader_change`). Setting AI is refused (`scanned_ai_not_available`) unless AI is on with current consent, the company server is the page reader, its card is enabled and its Test proved page images and structured answers. An update that takes this away (e.g. consent withdrawn) puts the switch back to rules, audited. Settings shows the switch; the document page shows "Read by rules / Read by AI"; the Invoice Recon sheet gets a last column "Reading Method" (no column moves).
- Method per version: `document_version.reading_method` (`rules`/`ai`); a person's save and a consensus version copy it, so it survives a correction (F9). A check ties it to `extraction_method` (AI methods always `ai`, parser methods always `rules`, `manual` either). Until PD-2e every new version is `rules`.
- Scanned = the per-page scan check, stored as `document_text.scanned`, not `extraction_method = pdf_ocr`: an OCR assist on a digital text-layer page is `pdf_ocr` but stays digital (decision 1; found in review). Existing pages stay `false`.
- PD-2d: the vision reader takes only invoices with a scanned page, the switch on AI and the capable company server (`vision_targets` and again in the worker before a call); PO/GRN files, digital invoices and scans with the switch on rules make 0 `ai_request` rows. H1 is no longer queued by ingestion (code kept). Rules extraction code is unchanged.
- Migration 0035 (additive): the three columns and two checks; the `reading_method` backfill for existing `ai_first`/`ai_fallback` rows pauses `document_version_append_only` for that statement, as 0024 did. CI previous-schema step moves to 0034.
- Checks: offline suite (unit, parsers, property, scenarios) 1460 passed, 22 skipped (need a database); migration safety (clone 0034 → 0035), no drift, catalog/index/table-count checks, idempotent seed on a disposable `postgres:16.15-bookworm` at 127.0.0.1:15499; integration: new PD-2c/PD-2d 9, vision 14, H1 15 of 16, AI settings API 57, migration groups 4/10/4/4/3, PD-4d 8, ingestion/page split/duplicates 17 of 19; scenarios 43 on a fresh database. The 3 failures (H1 clean-PDF rules-only baseline, two page-split tests) also fail on HEAD e9573e9 on a fresh database. Web: typecheck, lint, vitest 64. mypy strict core clean, app 80 (same as HEAD); import contracts 3 kept; core purity; rules hash and engine version unchanged. No provider call; no live database or setting touched.
- Deploy notes: after the migration the switch is rules everywhere, so page-image reading stops on live until a manager turns it on; check that no H1 request is still queued before the swap (a queued one would still run).
- Open: provider name on the method chip and per-field origin (PD-4c rest, with PD-2e); in `ai_assisted` mode a scanned invoice that needs no verification is still skipped (`NOT_NEEDED`), to settle in PD-2e.

## D108 — PD-2c + PD-2d live (29 Oct 2026)
- Owner go in chat, 29 Oct 2026: "lets make these part to live".
- Before: live at 0034; 0 AI requests queued or running (so no H1 request left to run); no queue job waiting; 0 AI-read versions; AI settings version 22.
- Deploy: backup `live-db-20261029-b5.dump` (3.7 MB) in the archive folder, test restore on a throwaway container with identical row counts in all 59 tables; rollback tags `:pre-b5` on setup, api, worker and ai-worker; images rebuilt from 41b5546; migration 0034 -> 0035 through the setup service; api, worker and ai-worker swapped; web served from the folder as before. Commit 41b5546, local tag live-20261029-b5.
- After: revision 0035; the switch is "read with rules"; AI settings version 22 and the consent fingerprint unchanged; all 1114 versions `reading_method = rules`; 113 AI requests before and after (0 new); api healthy, no errors in the first logs.
- Effect: page-image reading, which ran on every PDF under the old settings, now runs only on scanned invoices once a manager sets the switch to "read with AI" (company server with a passing Test).
- Owner check pending: Settings shows the "Scanned invoices" panel; a document shows "Read by rules".

## D109 — Step 6 built: PD-2e + PD-2f + PD-2h + PD-3b (29 Oct 2026)
- Step 6 of plan §15.1, built and tested, not committed or deployed; no real provider call. Plan and review: `docs/claude/plan-step6.md`. Owner choices in chat (29 Oct): D1 with the switch on AI every scanned invoice is read (the `NOT_NEEDED` skip is gone); D2 the provider is derived from the AI values' calls (no new column); D3 Gemini still waits for O-25; D4 a correction is refused while an invoice waits for its reading; D5/D6 a scanned invoice the reader can't take, or one still waiting when the switch goes back to rules, shows "AI reading failed" and a person picks (never silently rules); D7 per-field origin in the export moves to step 7.
- P3 root cause (proven by a test that reproduced the live `TypeError`): the ai-worker deferred the consensus job through Procrastinate's async connector on a sync connection. Fixed with a sync job manager built before the app opens (as `jobs.py` does); a failed enqueue now fails the reading visibly. Until now no consensus run had ever been queued on live.
- PD-2f: `document.reading_state` (`ready`/`waiting`/`failed`) + `reading_failure_code` (additive migration 0036). A scanned invoice with the switch on AI waits from its parse transaction; every final AI request state (skip, release, finish, lease sweep), a page or merge failure, and a stall sweep fail it with the stated reason. Mapping, the ledger, reconciliation (one shared loader) and vendor resolution use `ready` documents only. Retry (switch still on AI, a fresh attempt to the active provider, never the cached answer) and "Read with rules" are audited (`document.reading_retry`, `document.reading_use_rules`).
- PD-2e: pure `reconbench_core/extraction/merge.py:merge_readings`, policy `arb.llm`: the AI value leads, OCR stays beside it (`LLM_DISAGREEMENT` per field in `extras.cross_check`, OCR header values in `extras.ocr_reading`); MG-2/3/4/5/7/10/11 and V-HSN (WI-V1) applied. The general worker's consensus run appends the `ai_first`/`ai` version (AI values with origin AI, their `ai_call_id`, unverified, confidence ≤ 0.69) and marks the document ready in one transaction; a person who chose "Read with rules" meanwhile keeps that choice. `LLM_DISAGREEMENT` is not a new ReasonCode (verification reasons must be ReasonCodes; AI_VALUE_UNVERIFIED already holds the case).
- PD-2h: a handwritten-reference hint rests on the invoice total only if it was read by rules, verified by a person, or the OCR reading agrees within ₹1. Handwritten PO/GRN numbers are also "Use suggested" values; handwritten dates and amounts stay notes (no unambiguous target field).
- Found in the plan review (R1): a person-verified PO reference could make an auto link although another value on the invoice was read by AI and unchecked. Now no link from a document with an unverified AI value is auto, whatever the method.
- PD-3b: hypothesis property, 500 stub answers (valid, invalid, hostile) through `check_reading` → `merge_readings` → `resolve_links` → `evaluate` with a zero confidence gate: no auto link, no Matched. Planted removal of the PD-2b gate is caught.
- UI (inert until the backend ships): "Waiting for reading" / "AI reading failed: <plain reason>" with Retry and Read with rules; the edit button hides meanwhile; chip "Read by AI · Company server".
- Engine 0.1.12 → 0.1.13 (mapping/links.py); rules hash bb92f8699f20c82fe0e74d8e239e96541c3ddde3acabbc4979927df62f2c1a35. HOOKS_BY_MODE and consent texts unchanged (no re-consent).
- Checks: offline 1495 passed + the authorization test fixed after the run, 22 skipped; web vitest 70, tsc and lint clean; mypy app 80 (= HEAD), core strict clean; import contracts, purity, rules hash, engine version, migration safety (0035 copy → 0036) and schema drift pass; integration files on a disposable DB pass except the KI-003 failures (unchanged). Not run: `test_phase4_task_411_413`, Playwright, `make accuracy-bench`, a live dry run.
