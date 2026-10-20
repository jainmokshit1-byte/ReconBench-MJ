# Progress against the accuracy plan (AC-0 … AC-12)

Date: 16 Oct 2026. Mode: read-only audit. No source, config, migration or test file was changed. No test, benchmark, service, provider or model was run. Evidence comes from code, `docs/`, and benchmark reports already stored in `work/accuracy-reports/` (read with the throwaway script `work/progress_metrics.py`, which prints counts only).

**Evidence tags.** **verified**: confirmed in code (`path:line`) or by command output in this session. **inferred**: follows from verified facts but was not executed end to end. **UNKNOWN**: cannot be checked from here; the reason is given.

**Privacy.** Only counts, ratios, hashes, truth-set ids (`S01`…`S16`) and generic labels appear here.

**Repository state (verified).** `git rev-parse HEAD` = `0a0521071d301dee6e805d1be9d050e8f546c130` (branch `phase2-hosted-ci`). `git status --short` lists 107 entries (modified and untracked). Every finding describes the **working tree**. No source, test or doc file changed after 9 Oct 2026 14:36 (verified by file timestamps against the newest report `scanfix-truth-2.json`). So the state is "D61 plus nothing".

---

## 1. Headline

| | Count |
|---|---|
| Main-plan rows (AC-0 … AC-12, sub-phases split as the plan splits them) | 18 |
| DONE-VERIFIED | 9 |
| PARTIAL | 1 |
| IMPLEMENTED-GATE-OPEN | 4 |
| NOT-STARTED | 4 |
| UNVERIFIABLE (as a whole row) | 0 (single claims inside rows are tagged) |
| STALE-CLAIM (claims in the plan or README that the code or reports contradict) | 6 claims, listed in §4 |
| Scan-fix plan work items (`docs/accuracy-fix-plan-scan-extraction.md`) | 13: 3 DONE-VERIFIED, 10 NOT-STARTED |
| Owner decisions D-1 … D-10 | 6 decided, 1 owner-claimed but unverifiable here (D-4), 3 open (D-7, D-8, D-10) |

No single progress percentage is given (no weights were agreed). "Implemented" and "accuracy gate met" are kept apart. **Every accuracy target in plan §1 is still unmet. All four hard-zero metrics are met.**

| Plan §1 target | Target | Latest measured (verified, `scanfix-truth-2.json`, report `470682236b6d`, engine 0.1.7) | Met? |
|---|---|---|---|
| Header fields correct | ≥ 98% | 302/373 = 81.0% (all docs); scan-page docs 224/283 = 79.2% (attribution `baseline-scanfix-attribution.json`) | no |
| Line items correct | ≥ 95% | line recall 96/141 = 68.1%; scan-page line amount 78/111 = 70.3% | no |
| Auto-verified documents | ≥ 90% digital/QR, ≥ 60% scans | 18/51 = 35.3% (all docs) | no |
| Fields to touch per document | ≤ 2 | 4.96 | no |
| Link recall | ≥ 95% | 3/10 = 30% (0 auto, 3 suggested, 7 missed) | no |
| FALSE_SAFE / FALSE_AUTO_LINKS / FALSE_MATCHED / WRONG_VENDORS_CREATED | 0 / 0 / 0 / 0 | 0 / 0 / 0 / 0 | **yes** |
| Identity: extra documents / wrong merges | 0 extra | 8 extra (GRN 3, PO 3, invoice 2) / 0 wrong merges | no |
| Duplicate copies caught / missed / false | all caught | 0 / 2 / 0 | no |

Truth-set size (verified, report `segmentation`): 35 files, 26 truth documents, 51 parsed documents, 10 expected links, 2 complete PO+GRN+invoice sets. Every rate above can move several points with one document (plan §11).

---

## 2. Phase table (plan claim vs evidence)

Sources for the plan claim: `docs/accuracy-plan.md:3` (status line) and §9; `docs/accuracy-implementation-plan.md:227-243` (§1.1).

| Phase | Plan's own claim | Exit-gate metric (plan §9) | Evidence | Class | Remaining work (size) |
|---|---|---|---|---|---|
| **AC-0** benchmark + baseline | done (D32) | baseline recorded; bench re-runs deterministically | `scripts/accuracy_bench.py`; `make accuracy-bench`; `tests/unit/test_accuracy_bench.py`; `work/accuracy-reports/baseline-ac0.json` (hash `dfce849a3b1e`). D47 records two runs with identical hash `baf82b8de05a…`. All verified (files exist; hashes read from reports). | DONE-VERIFIED | Truth-set growth is D-7 / scan-fix WI-11 (L) |
| **AC-1** deterministic guards | done (D33) | header up; false-safe 0; buyer GSTIN never a vendor | `reconbench_core/parsers/parties.py:396 guard_result`, `:375 party_hints_from_config`, `:293 withhold_vendor_name`. Header 251/353 → 302/373; FALSE_SAFE 3 → 0; WRONG_VENDORS 6 → 0 (`baseline-ac0.json` vs `scanfix-truth-2.json`, verified). With `--buyer-entities none` one stage-C miss remains (buyer GSTIN read as vendor GSTIN, capped 0.60 and flagged, no vendor created; `scanfix-none-2-attribution.json`, D60). | DONE-VERIFIED | The C=1 leftover (S) is assigned to scan-fix WI-5 |
| **AC-2** e-invoice QR | done (D34) | QR invoices get L0/L1 header fields | `reconbench_core/extraction/qr.py:151 parse_einvoice_qr`, `:401 apply_einvoice`. Bench QR block: 11 docs, fields confirmed 45 / prefilled 13 / corrected 6 / conflict 2, **signed 0** (verified). | DONE-VERIFIED (L1 path) | L0 needs the NIC public key: owner supplies `RB_EINVOICE_PUBLIC_KEY_FILE` (S). Whether the app has it: UNKNOWN (env not readable here) |
| **AC-3** page split + classify | done (D35) | a bundle gives separate documents; no document mixes pages | `reconbench_core/extraction/pages.py:164 classify_page`; `POST /files/{file_id}/page-split` (`api/ingestion.py:574`). Missed documents 6 → 0, spurious 0 (verified). **But** D57 records 2 failing integration tests in `test_page_split.py` (`MultipleResultsFound` in `_current()` with duplicate PO rows). No later entry says they were fixed, and no source changed after D61 (verified by timestamps). | PARTIAL | Fix the `_current()` duplicate-row failure (S–M); re-run `test_page_split.py` on disposable PG |
| **AC-4** vendor hygiene | done (D36) | 0 junk vendors after re-ingesting the real set | `reconbench_app/vendor_cleanup.py` exists; migration `0019_vendor_name_status.py`. Bench WRONG_VENDORS_CREATED 0 in every report since AC-4; `junk_reasons {}` (verified). The app-side re-ingest and the owner's one-time cleanup run: UNKNOWN (needs DB access). | DONE-VERIFIED (bench) | None in code; owner confirms the cleanup run |
| **AC-5 / 5b** vision reader (Gemini H6) | done (D37–D41) | bench with cached responses; vision alone measured per field | `reconbench_app/ai/vision.py`, `ai/worker/hooks/vision.py`, `reconbench_core/extraction/vision.py:534 check_reading`, `:789 vision_document`; migration `0020`. `after-d41-lite.json`: 39/39 docs read, header 271/313 (86.6%), lines 80/123 (65.0%), handwritten refs 6/18, agree-wrong 1 (verified). | DONE-VERIFIED | D-8 cost cap open (S, owner). The newest reports have an **empty** vision block (see §4, claim C6) |
| **AC-6a** consensus core + shadow bench | done (D44) | (AC-6 exit, below) | `reconbench_core/extraction/consensus.py` (`decide` `:721`, `rules_readings` `:434`); `tests/unit/test_consensus_core.py`, `test_accuracy_consensus.py` (verified present). Shadow block in `after-ac6c.json` (verified). | DONE-VERIFIED (sub-scope) | — |
| **AC-6b** crop reread + provenance | done (D45) | (AC-6 exit) | `reconbench_app/ai/consensus.py:372 ConsensusService`, `:292 _crop_checks`; migration `0021_consensus.py` (untracked file, verified present). | DONE-VERIFIED (sub-scope) | — |
| **AC-6c** opt-in verify loop | done (D46/D47) | **auto-verified rate up**, false-safe 0, suggestion-only replaced for L0/L1 fields | Migration `0022_auto_verify.py`; `ai/consent.py:25` five field types; `ai/consensus.py:802 _append_verified_version`. Bench auto-verified is **18/51 before and after** AC-6 (verified: `after-ac5.json` and `scanfix-truth-2.json`), because the bench measures only the shadow upper bound. The per-field gates "pass" on 1–5 scored L1 values each (doc_date 2, doc_no 1, gstin 2, line_amount 5, reference 2; `after-ac6c.json`, verified). A known false-safe path is open: `tests/scenarios/test_hard_zero_guards.py:255` is `xfail(strict=True)` because `rules_readings` counts an AI-sourced value as an OCR reading (D59). | IMPLEMENTED-GATE-OPEN | Fix the D59 consensus gap (S); measure auto-verified in the app path (M); grow scored evidence per field type (depends on WI-11) |
| **AC-7a** verify-screen backend | done (D48–D50) | corrections reach the bench | `scripts/merge_accuracy_corrections.py`, `tests/unit/test_merge_accuracy_corrections.py` (9 tests per D50), PO/GRN correction via `PUT /documents/{id}/correction` (D49). | DONE-VERIFIED (sub-scope) | — |
| **AC-7b** verify-screen UI | implemented (D50) | owner verifies a document in < 30 s | `web/src/components/VerifyDocument.test.tsx`, `web/e2e/verify-screen.spec.mjs`, `web/src/utils/pdfEvidence.ts` (untracked, verified present). The 30-second target was never timed (D50 says so). | IMPLEMENTED-GATE-OPEN | Owner timing session (S). UNVERIFIABLE from code |
| **AC-8a** identity/duplicate core | done (D51) | (AC-8 exit) | Core duplicate rule and aged fix; `tests/property/test_reconciliation_duplicates.py` (verified present). | DONE-VERIFIED (sub-scope) | — |
| **AC-8b** app identity + re-scan guard | in progress (D52–D55) | **re-scans make 0 new documents; real duplicates flagged** | `reconbench_app/document_identity.py` (`difference_hash` `:33`, `verified_field_confidence` `:83`); migration `0023`. Bench: 8 extra docs, 0 wrong merges, duplicates 0/2 (verified). | IMPLEMENTED-GATE-OPEN | Close 8 extras and 2 missed duplicates, or the owner accepts and carries the gap to AC-11 (M–L) |
| **AC-9a** matching v2 core | implemented; gate open (D57–D58) | **link recall ≥ 95%**, false auto-links 0 | `reconbench_core/mapping/links.py:2593 resolve_links`, `:804 _via_grn_resolution`, max-weight assignment at `:1743`; migration `0024`. Recall **7/10 → 3/10** across AC-9a with identical extraction (`before-ac9a-session-20261008.json` vs `after-ac9a-grn-anchor-final-20261009.json`: both header 302/373, lines 96/141; verified). False auto-links 0. | IMPLEMENTED-GATE-OPEN | Scan-fix WI-10 regression trace and fix (M); then truth growth |
| **AC-9b** matching v2 app/UI (one-click confirm) | not started | (AC-9 exit) | No score explanation in `mapping_service.py` (grep for `explanation`/`signals`: 0 hits, verified). A generic "Confirm" button for auto/suggested links already exists (`web/src/pages/MappingPage.tsx:244`), reusable. | NOT-STARTED | M |
| **AC-10** learning loop (vendor profile) | not started | repeat-vendor documents need fewer touches | No `vendor_profile` in `backend/` (grep: 0 hits, verified). | NOT-STARTED | L |
| **AC-11** real-set E2E + exit gate | not started | hard metrics 0; targets met or gap reported | Nothing to check yet. | NOT-STARTED | L (needs D-7 truth) |
| **AC-12** GSTR-2B (optional) | not started | — | No GSTR-2B importer (grep `gstr` in app/core: only a label check in `extract.py:889`, verified). | NOT-STARTED | M (optional) |

### 2.1 Scan-fix plan (`docs/accuracy-fix-plan-scan-extraction.md`, 9 Oct)

| WI | Evidence | Class |
|---|---|---|
| WI-1 bench/upload parity, attribution, baseline freeze | `ingestion.py:294 parser_hints_from_config`; `tests/unit/test_parser_hints_parity.py`, `scripts/accuracy_attribution.py`, `baseline-scanfix.json` (`280fdebc96af`) and its attribution JSON (verified present). D59/D60 record the `none`/`truth` runs. | DONE-VERIFIED |
| WI-2 test infra + hard-zero guards | `Makefile` `test:` now includes `backend/tests/parsers` (verified, `Makefile:36`); `tests/support/scan_images.py`, `tests/parsers/test_scan_images.py`, `golden/SYN-SCN-01`, `golden/SYN-SCN-04` (verified present). | DONE-VERIFIED |
| WI-3 buyer_entities + party-role guard | D59/D60; `scanfix-none-2.json` hard 0/0/0/0 (verified); C=1 leftover accepted by the owner for WI-5. | DONE-VERIFIED |
| WI-4 doc-no / PO-ref candidates | No code change after D61 (timestamps, verified). | NOT-STARTED |
| WI-5 vendor name | same | NOT-STARTED |
| WI-6 header totals/tax | same | NOT-STARTED |
| WI-7 line tables | same | NOT-STARTED |
| WI-8 targeted OCR / force OCR on poor text layers | `local_hybrid.py:179` still `text=True` with no `encoding=` (step 5 not done); no `LAYOUT_PAGE_LIMIT` warning (grep 0 hits). | NOT-STARTED |
| WI-9 parser-version guard + re-parse CLI | `PARSER_VERSION = "phase4-parser-6"` (`ingestion.py:88`); `PARSER_HASH`, `scripts/check_parser_version.py`, `reparse_outdated.py` absent (verified). | NOT-STARTED |
| WI-10 link-recall regression | no `--link-trace` in `accuracy_bench.py` (grep 0 hits). | NOT-STARTED |
| WI-11 truth-set expansion | truth still 26 docs / 2 complete sets (report meta, verified). Owner target revised to about 5 complete sets (fix plan §3a). | NOT-STARTED |
| WI-12 gated Gemini experiment | `ProviderRequest.thinking_level` is still `Literal["low"]` (`ai/provider.py:40`); prompt still `vision.prompt.v3` (`ai/prompts.py:35`); no `--vision-thinking` / `--vision-primary-sim` flags (grep 0 hits). | NOT-STARTED |
| WI-13 vision as primary reader on scans | no `merge_primary` (grep 0 hits). | NOT-STARTED |

### 2.2 Owner decisions (plan §10)

| ID | Status (evidence) |
|---|---|
| D-1 page images to Gemini | Decided 7 Oct (paid tier, `store=False`). D39 + fix plan §3a add **free-tier testing only**. Production on free tier not decided. |
| D-2 machine-verified without a person | Decided D42 (five rules); implemented as opt-in AC-6c. |
| D-3 vision for PO/GRN | Decided 7 Oct. |
| D-4 `buyer_entities` | Owner says it is filled in workspace Settings (fix plan §3a). The live DB value is UNKNOWN (no DB access in read-only mode). Seed is still empty (D59 `BUYER_ENTITIES_NOT_CONFIGURED` warning exists). |
| D-5 marketplace vendor rule | Decided (D36). |
| D-6 non-PO matching suggestion-only | Decided; enforced in AC-9a (D57). |
| D-7 truth set 15–20 sets | **Open.** Owner target revised to about 5 sets (fix plan §3a); the plan text still says 15–20. |
| D-8 vision cost cap | **Open.** `per_month_calls/tokens` default `None` (`ai/limits.py`, verified). |
| D-9 bundles: suggest split | Decided (D35). |
| D-10 GSTR-2B | **Open** (optional). |

---

## 3. Remaining work by item (S/M/L)

| Item | What is left | Size | Moves which metric |
|---|---|---|---|
| Scan-fix WI-4 … WI-7 (deterministic rules) | doc-no/PO-ref choice, vendor name, totals/tax, line tables | S, M, M, L | headers, lines; indirectly identity (extras) and links |
| Scan-fix WI-8 | targeted OCR, force OCR on poor/tiled text layers, >20-page warning, UTF-8 decode | M | headers/lines (A-stage misses: 9 header, 24 line cells on scans) |
| Scan-fix WI-9 | parser-version guard, re-parse CLI | S | none directly; makes fixes reach existing files |
| Scan-fix WI-10 | AC-9a link regression trace and fix | M | link recall (4 of 7 misses) |
| Scan-fix WI-11 / D-7 | truth set growth | L (owner) | makes every DoD claim measurable |
| AC-3 defect | `test_page_split.py` 2 failures | S–M | none (correctness of split flow) |
| AC-6 gap (D59) | consensus counts AI-sourced value as OCR reading | S | hard-zero guard (false-safe) |
| AC-6 exit | auto-verified rate in the app path; more scored evidence | M | auto-verified, touches/doc |
| AC-7 exit | owner 30 s timing | S (owner) | touches/doc |
| AC-8b | 8 extra docs, 2 missed duplicates | M–L | extra docs, duplicates |
| AC-9b | explanation + one-click confirm | M | link throughput (not recall) |
| AC-10 | vendor profile | L | headers/touches on repeat vendors |
| AC-11 | E2E exit gate | L | all |
| D-8 | cost caps | S (owner) | none; prerequisite for vision-primary |
| WI-12/WI-13 | LLM experiment and vision-primary | M + L | headers/lines on scans |

---

## 4. Stale claims (plan or README vs code/reports)

| # | Claim | Where | Evidence it is stale |
|---|---|---|---|
| C1 | "Today: about 89% headers / about 84% lines (D18, 8 scans)" | `accuracy-plan.md:23-24` | Current bench: headers 81.0% (all docs), 79.2% (scan-page docs); lines 68.1%. The diagnosis could not reproduce 89/84 (`diagnosis-scan-extraction.md:127`). Verified. |
| C2 | "Only the paid API tier with `store=False` is used" | `accuracy-plan.md:159` | D39 and fix plan §3a allow free-tier keys for testing. Code cannot tell the tier: it always sends `store=False` (`ai/worker/gemini.py:89` in the read output, verified). |
| C3 | D-4 "Set it in Settings before AC-1" | `accuracy-plan.md:232` | Superseded by D59 (warning when empty) and the owner's 9 Oct answer. |
| C4 | D-7 "at least 15–20 complete sets" | `accuracy-plan.md:173`, `:235` | Owner target revised to about 5 (fix plan §3a). |
| C5 | README describes only Tesseract/OCRmyPDF for scans; it does not mention the vision reader, AI-first, consensus, the accuracy bench or `RB_VISION_READER` | `README.md:29` (last modified 6 Oct) | Verified by reading README. |
| C6 | The plan treats vision as "measured per field" | `accuracy-plan.md:3`, §9 | The newest reports (`baseline-scanfix.json`, `scanfix-truth-2.json`, `scanfix-none-2.json`) have an **empty** vision block and every consensus gate `no_evidence`, because the bench default model is `gemini-3.8-flash` (`scripts/accuracy_bench.py:1894`) while the stored answers are `gemini-3.5-flash-lite`. The vision numbers exist only in `after-d41-lite.json` … `after-ac9a-grn-anchor-final-20261009.json` and in the diagnosis cache replay. Verified. |

---

## 5. Dependency map

```
D-7 / WI-11 truth growth ─────────────────────────────────────────────┐ (needed to prove any target)
WI-1,2,3 (done) ─► WI-4 ─► WI-5 ─► WI-6 ─► WI-7 ─► WI-9 bump+reparse ──┤
                └► WI-8 ──────────────────────────────────────────────┤
WI-10 (AC-9a regression) ─► AC-9b ────────────────────────────────────┤
AC-8b extras/dups ◄── better/verified extraction (WI-4..8, AC-L) ──────┤
D59 consensus gap fix ─► AC-6 exit (auto-verified) ─► AC-L3 merge ──────┤
AC-L0 provider layer ─► AC-L1 local provider+settings+shadow ─► AC-L2 eval ─► AC-L3 LLM values on scans
                                                                       └► AC-11 exit gate
AC-10 vendor profile ◄── verified corrections (AC-7) ─────────────────┘
```

| Open item | Blocked by | Blocks | Metric it is meant to move |
|---|---|---|---|
| WI-4…WI-7 | nothing (WI-1/2/3 done) | WI-9 bump; AC-8b (stable keys); AC-L2 baseline | headers, lines |
| WI-8 | nothing | — | headers/lines (A-stage) |
| WI-10 | nothing | AC-9b, AC-11 | link recall |
| AC-8b | WI-4…8 and/or AC-L (more verified fields) | AC-11 | extra docs, duplicates |
| D59 consensus gap | nothing | any auto-verify; AC-L3 | hard-zero (false-safe) |
| AC-L0…L3 (new, see the LLM plan) | D-8 for production; hardware for local | AC-11 | headers, lines (scans); handwritten hints |
| AC-10 | AC-7 corrections, truth growth | AC-11 | touches/doc |
| AC-11 | everything above + D-7 | Phase 4 exit | all |

---

## 6. The new LLM feature: what already exists

| Piece | State | Evidence |
|---|---|---|
| Provider-neutral call port (`AiProvider`, `ProviderRequest`, `ProviderResult`, `ProviderFailure`) | **ALREADY EXISTS, reusable** | `reconbench_app/ai/provider.py:33-72` (D1) |
| Gemini adapter | exists; Gemini-specific | `ai/worker/gemini.py:148-198` |
| Worker runner (gate → reserve → key → send → validate → apply → cache → ledger) | exists; **hard-wired to one provider** | `ai/worker/runner.py:79-359`; `ai/worker/main.py:80` `provider=GeminiProvider()` |
| Single-attempt httpx transport guard | exists; reusable for any httpx client | `ai/worker/transport_guard.py:102 SingleAttemptTransport` |
| Deployment gate, consent fingerprint, limits, usage ledger, cache, key sealing | exist; mostly provider-neutral | `ai/gate.py`, `ai/consent.py`, `ai/limits.py`, `ai/ledger.py`, `ai/cache.py`, `ai/key_seal.py` |
| Vision reader registry (`VisionReaderInfo{provider, external}`) | **PARTIAL**: only `vision_gemini`; the `external` flag exists but nothing reads it | `extraction/vision.py:19-59` |
| Page render + masking in sandbox, transient page bytes | exists; provider-neutral | `extraction/vision_pages.py`, migration `0020` |
| Vision schema v3, prompt v3, strict parse, core checks | exist; schema dialect shaped by Gemini limits (no `maxItems`) | `ai/contracts.py`, `ai/prompts.py:35`, `reconbench_core/extraction/vision.py` |
| Consensus (D42) and crop reread | exist | `reconbench_core/extraction/consensus.py`, `ai/consensus.py` |
| Settings API (GET/PUT settings, consent preview, key delete, provider test, usage, request state) | exists; **Gemini-only** (`provider: Literal["gemini"]`, model regex `gemini-…`) | `api/ai.py:114-213`; `ai/settings_service.py:73,155` |
| Settings UI | exists as a Home-page dialog, single provider | `web/src/components/AiAssistCard.tsx` |
| Review surfaces (suggestions panel, verify screen with box overlay) | exist; show `model` only | `web/src/components/AiSuggestionsPanel.tsx:77`; `web/src/api/ai.ts:72-87` |
| Tests | Gemini adapter with `httpx.MockTransport` (`tests/unit/test_ai_gemini_adapter.py:63`), worker boundary, transport guard, vision core/app, consensus, Playwright AI card with mocked routes | verified present |
| OpenAI-compatible / vLLM provider, local endpoint allowlist, per-provider settings, reader strategy, dual-reader agreement, LLM-to-document merge, Qwen prompt variant, two-pass crops | **NEW** | grep for `openai`/`vllm`/`qwen` in app and web source: only the DB enum value `openai` (verified) |

---

## 7. Per-stage attribution of the D57/D58 misses

Stages: extraction (OCR = A, rules = B, normalisation = E), party role (C), page split/identity (D), candidate set / link / alignment (matching). Sources: `baseline-scanfix-attribution.json` (verified counts), `diagnosis-scan-extraction.md` §5.7 (link attribution, verified there by report diff; the internal cause in `links.py` is UNKNOWN).

| Miss | Count | Stage | Can a better LLM reader plausibly change it? |
|---|---|---|---|
| Scan-page header misses | 59 (A 9, B 45, C 0, D 0, E 5) | extraction | **Yes, in part.** On the 39 PDF docs, cached flash-lite was right on 60 fields where OCR was wrong or empty, and OCR was right on 31 where vision was wrong or empty (verified, §1 of the audit). Best-of-both upper bound 302/313 = 96.5% (inferred: a perfect chooser, which does not exist). |
| Scan-page line-amount misses | 33 (A 5, B 28) | extraction | Yes, in part (vision line recall 80/123 vs OCR 96/141 over different populations; per-cell vision accuracy not stored: UNKNOWN). |
| Link misses | 7 of 10 | 4 matching regression (AC-9a); 2 extraction A (handwritten PO ref + missing totals); 1 vendor identity (supplier without GSTIN, vendor pending; inferred) | **At most 2 of 7.** The 4 matching misses and the identity miss are not reader problems. Even for the 2, handwritten refs are hints only (plan §4; D42 rule 4), so a reader can at best produce a suggestion that still needs a same-vendor PO and an amount signal. Vision handwritten recall is 6/18 (weak). |
| Extra documents | 8 | identity (AC-8b) | **Indirectly, UNKNOWN share.** Natural-key recovery needs fully verified fields (D54). An LLM reader adds verified fields only through D42 (independent reader + validators + crop), so the effect depends on AC-6 being on. |
| Missed duplicate copies | 2 | identity/duplicate rule | **Indirectly, UNKNOWN.** One is inferred to involve GSTIN look-alikes beyond the 2-swap repair (A-glyph). |
| Party-role errors | 0 with entities, 1 without | party role | A reader does not fix role; the deterministic guard does (and must also guard LLM output). |

Conclusion: an LLM reader can move header and line accuracy on scans. It cannot fix 5 of 7 link misses, and it does not directly fix identity. Link recall needs WI-10 first.
