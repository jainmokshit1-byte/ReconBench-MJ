# Plan: step 6 (PD-2e + PD-2f + PD-2h, with PD-3b)

Written 29 Oct 2026. Specs: `docs/accuracy-plan.md` §15.2 (PD-2e, PD-2f, PD-2h, PD-3b), §4.10 (MG guards), D103. Status: **approved by the owner in chat, 29 Oct 2026**: D1 every scanned invoice, D4 refuse the save, D5/D6 failed + a person picks, implement all of §3 (D2, D3, D7 as recommended); no commits, no deploy, no real provider calls.

## 0. What the code does today (verified by reading, 29 Oct)
- Parse → OCR version (rules) is current at once → mapping runs on it. With the switch on AI, `_queue_vision_reading` (`ingestion.py:2556`) queues page prep → `h6_vision` `ai_request` → ai-worker `VisionReadHandler.apply` (`ai/worker/hooks/vision.py:148`) → `consensus_enqueue` → general-worker `ConsensusService._process` (`ai/consensus.py:408`): crop checks + `decide` (D42); appends a version only if a field was auto-verified (never: MG-10, opt-ins empty).
- **P3 root cause (hypothesis, strong):** the ai-worker's `defer_consensus` (`ai/worker/main.py:52`) defers through procrastinate's *async* `PsycopgConnector`. Once the worker's async pool is open, `get_sync_connector()` returns the async connector itself, whose `execute_query_all_with_connection` runs async cursor code on our *sync* psycopg connection → `TypeError`. `jobs.py:228` avoids this by building `JobManager(connector.get_sync_connector())` before the app opens. So **no consensus run has ever been queued on live**. To be proven by a failing test first.
- `vision_document` (`reconbench_core/extraction/vision.py:962`) already turns a checked reading into a `Document`, incl. `typed_refs` / `annotation_refs` in `extras`. It does not drop `check_failed` / `page_conflict` values; the merge must.
- `consensus.rules_readings` / `vision_readings` / `values_agree` (core) already pair OCR and LLM readings per field: the merge reuses them.
- WI-D4 hint rule: `links.py:_handwritten_header_links` (~L1300). Uses the invoice `grand_total` with no check of where it came from (the self-support gap PD-2h closes).
- The document page already lists AI values beside the OCR reading (PD-4d) and has "Use suggested" for AI suggestions (`AiSuggestionsPanel.tsx`, `_h1_review` covers vision answers).

## 1. Owner decisions needed before building
| # | Question | Recommended default |
|---|---|---|
| D1 | `ai_assisted` mode: `vision_eligibility` skips scanned invoices whose verification is `NOT_NEEDED`. With the switch on AI, read **every** scanned invoice? | **Yes**: switch on AI ⇒ every scanned invoice is read by AI (principle 2/3). The `NOT_NEEDED` skip applies only when not reading for the switch. |
| D2 | Provider name on the chip: store per version, or derive? | **Derive** from the AI fields' `ai_call_id` → call log provider. No new column. Shown as "Read by AI · Company server" (no URL, Q9). |
| D3 | Gemini as reader | Stays blocked until O-25 is re-confirmed (no change). |
| D4 | A person edits a document while it is "Waiting for reading" | **Refuse** the correction save (`READING_PENDING`, 409) with the text "Choose Retry or Read with rules first". Otherwise the AI answer would land on a stale version and the doc would wait forever. |
| D5 | Scanned invoice with the switch on AI that the reader can't take (too many pages, over the per-upload call cap, page prep failed) | It goes straight to **"AI reading failed: <reason>"** (waiting), never silently rules. |
| D6 | Switch flipped back to rules while documents wait | They keep waiting; **Retry is refused** (switch is on rules), **Read with rules** works. No automatic change. |
| D7 | Per-field origin in the Excel export (rest of PD-4c) | **Defer** to step 7; this step adds only the provider to the chip. |

## 2. Design

### 2.1 PD-2f: "Waiting for reading" (built first: it is the safety gate for PD-2e)
- **Migration 0036 (additive):** `document.reading_state` text NOT NULL default `'ready'`, check in (`ready`, `waiting`, `failed`); `document.reading_failure_code` text NULL; `reason_code` row `LLM_DISAGREEMENT` for every workspace (like 0033). No backfill (existing rows = `ready`).
- **Enter:** in the parse transaction, a scanned invoice with the switch on AI gets `reading_state='waiting'` before mapping is queued; ineligible/over-cap/enqueue failure ⇒ `failed` + code (D5).
- **Fail:** one seam in `ai/ledger.py` (the only writer of terminal `ai_request` states: `finish`/`_skip`): a terminal non-applied `h6_vision` request (not shadow) whose subject is the current version ⇒ document `failed` + plain code. Also: answer not accepted (`apply` → `not_applicable`), page prep failure, merge failure (`VISION_ANSWER_INVALID` etc.). Retry states that will be re-run (`released` with a pending job) don't fail it — verify in code.
- **Skip:** mapping, vendor resolution and reconciliation select only `reading_state='ready'` invoices (one shared SQL predicate, applied at each selection site; sites listed during build). Ledger is fed by links, so it follows.
- **Actions (audited, `files.upload` like `verify_ai_values`):** `POST /documents/{id}/reading/retry` (switch must be AI and capability still true; new `ai_request` to the active provider only; cache bypassed so a bad answer isn't replayed) and `POST /documents/{id}/reading/use-rules` (state `ready`, the existing OCR version counts, method stays `rules`, mapping queued). Stale-screen guard via document `version`.
- **UI:** document page banner "Waiting for reading" / "AI reading failed: <plain reason>" with the two buttons; status chip in the documents list. Regenerate `openapi.json`, `generated.ts`, `docs/schemas`.

### 2.2 PD-2e: AI reading becomes the current version
- **Pure core `reconbench_core/extraction/merge.py`:** `merge_readings(ocr: Document, check: VisionCheck, policy: MergePolicy) -> MergeResult` with policy `ARB_LLM` (`arb.llm`, stored as data). Per field:
  - LLM passes and agrees with OCR ⇒ LLM value, origin AI, OCR recorded as supporting.
  - LLM passes, OCR differs ⇒ LLM value leads; OCR value kept side by side in `extras["cross_check"]`; `LLM_DISAGREEMENT` added.
  - LLM missing / unreadable / `check_failed` / `page_conflict` (MG-3) / from a page of another document (MG-11) ⇒ OCR value + flag; the LLM value only as a suggestion.
  - MG-7: a field the OCR version has as manual or person-verified is never replaced.
  - MG-2: AI confidence capped below the review floor (0.69); MG-10: nothing auto-verified; MG-4: `needs_verification` stays true (AI values unverified anyway).
  - MG-5: a `po_refs` set that grew across pages is flagged. MG-6: descriptions never accepted.
  - Lines: LLM lines lead when present (aligned to OCR lines with the existing `align_lines`, cell disagreements recorded); otherwise OCR lines.
  - `typed_refs` / `annotation_refs` carried into `extras` (PD-2h).
- **WI-V1 (needed by PD-2e):** V-HSN shape check on LLM HSN/SAC values (core, beside `check_reading`). V-REFEXIST stays link-time (links are suggestions anyway).
- **Worker:** `ConsensusService._process` runs `merge_readings` when the document is `waiting`, and in the same transaction appends the version (`extraction_method='ai_first'`, `reading_method='ai'`, `derived_from` = OCR version, field evidence with origin AI + `ai_call_id`, page, box), sets `reading_state='ready'`, and queues the follow-up (mapping). Copy-template = `_append_verified_version`, refactored to share the row copying.
- **P3 fix:** build the consensus `JobManager` from the sync connector before the app opens (same as `jobs.py`), and stop swallowing the error silently: a failed enqueue marks the document `failed`.
- **Chip:** "Read by AI · Company server" (D2).
- **Engine/rules:** `ReasonCode.LLM_DISAGREEMENT` (schema) + `links.py` (PD-2h) ⇒ bump `ENGINE_VERSION` 0.1.12 → 0.1.13, refresh `RULES_HASH`.

### 2.3 PD-2h: handwritten values
- **References:** in `_handwritten_header_links`, the **self-support guard**: when the invoice `grand_total` has origin AI, it is used only if a person verified it or the OCR cross-check value is within ₹1 (`extras["cross_check"]`). Rules-read invoices: unchanged. Links stay `suggested`.
- **Other handwritten values:** shown as suggested field values through the existing "Use suggested" path (annotation → field by `looks_like`, only for field names the correction form accepts); never written into the version.

### 2.4 PD-3b: determinism property
- `backend/tests/property/test_pd3b_ai_reading.py` (hypothesis): stub reader output = arbitrary valid, invalid and hostile `VisionReading`s → `check_reading` → `merge_readings` → `resolve_links` + `evaluate`. Asserts: no link `auto`, no case `Matched`, unless the test plays a person's click (verification `user`). Existing SCN-20 / property #8 tests stay green for the rules path (unchanged code path).

## 3. Order of work (each with focused tests first)
1. P3: failing integration test (real procrastinate, opened async connector, defer inside a SQLAlchemy txn) → fix in `main.py`.
2. Migration 0036 + `reading_state` + skip predicate + Retry / Read with rules API (PD-2f) + tests: each failure path ⇒ `failed`; mapping/recon skip; actions audited; 0 requests to the other provider.
3. Core `merge.py` + V-HSN + unit tests per MG guard (MG-2…MG-11).
4. Worker integration: AI version appended, `reading_method='ai'`, origin AI, state `ready`, mapping queued; integration test with the stub transport and `scanned_invoice_pdf()`.
5. PD-2h guard in `links.py` + R1 rule (unverified AI values ⇒ only suggested links) + extend `test_q2_typed_links.py` (self-support case; hint links 100 % suggested).
6. PD-3b property test.
7. Web: banner, buttons, list chip, provider on chip; vitest. OpenAPI regen.
8. Checks: `recon-check` skill, full offline suite (background), AI + ingestion integration groups on a disposable DB, migration safety, web tsc/lint/vitest. Update PROJECT_STATUS, KNOWN_ISSUES (P3), a D109 entry.

## 4. Acceptance (hard zeros)
- Switch on rules: 0 `ai_request`, extraction output identical (PD-2d test unchanged).
- Switch on AI: a scanned invoice is never mapped or reconciled until the AI version is current or a person picks "Read with rules".
- No AI value counts without a person's click: `AI_VALUE_UNVERIFIED` stays; no auto link from AI refs; no Matched case (PD-3b).
- Every failure path ends in "AI reading failed", never silently rules; 0 requests to the inactive provider.

## 5. Review of this plan (29 Oct, against the code and the hard zeros)
- **R1, gap found (fixed in plan):** links.py can still give `auto` for paths other than references: `_confidence_state` (`links.py:410`) makes any labelled, via-GRN or content link `auto` above `link_auto_accept`; only references are blocked for AI values (PD-2a, `_has_ai_values` in the system check). Today no document holds AI values, so it never mattered; PD-2e makes it real. **Add:** a source document with any AI value a person hasn't verified gets only `suggested` links (all methods), in `_links_for_matches` and the via-GRN path. Once a person verifies every AI value, the normal rules apply (PD-2b acceptance). PD-3b proves it.
- **R2, checked OK:** the PD-2b gate (`engine.py:_unverified_ai_values`) covers every header **and line** value, so AI line values also need a person's click before Matched.
- **R3, race:** a person picks "Read with rules" while the AI request is in flight. The merge runs only if the document is still `waiting` **and** the version is still current; otherwise it records nothing current (advisory decisions only). Test it. Retry is allowed only from `failed`, never while a request is open.
- **R4, cache:** a Retry after a bad answer must not replay the cached answer: the retry request is not cacheable (decided in the handler from the request), so a fresh call is made to the active provider only.
- **R5, no re-consent:** `HOOKS_BY_MODE` and consent texts unchanged ⇒ fingerprint unchanged (check with the existing fingerprint test).
- **R6, DB role:** the ai-worker uses the same DB user as the api (`compose.yaml`), so marking `reading_state` from `ai/ledger.py` needs no new grant. Verified in compose only, not on the live roles.
- **R7, order:** `reading_state='waiting'` is written in the same parse transaction that queues mapping, so mapping can never see the OCR version as ready.
- **R8, scope risk:** this is the largest step so far (core merge, migration, worker, API, web). Build in the §3 order with one commit-ready checkpoint after each of 1, 2, 4, 6 and 7; stop and report if a step needs a design change.

## 6. Not in this step
- Deploy (needs owner go, handoff "Deploy" section). Real vLLM calls (needs one go + logged budget). PD-5. Per-field origin in export (D7).
