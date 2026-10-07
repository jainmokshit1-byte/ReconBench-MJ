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
