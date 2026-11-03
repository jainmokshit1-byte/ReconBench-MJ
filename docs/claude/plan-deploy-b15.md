# Deploy plan b15 — matching fixes + scanned PO/GRN AI reading (3 Nov 2026)

Status: **plan, not executed.** Needs the owner's go for: commits, the backup, the image swap, the one-time mapping pass (step 10) and the switch decision (below).
Rules: `CLAUDE.md`; never read `work/`, `.env`, `deploy/secrets/`; no AI provider calls by us; no real invoice data in commits, docs or report; **no push**.

## What ships
| Part | Files | Effect on live |
|---|---|---|
| Verify re-match | `reconbench_app/ingestion.py:verify_ai_values` | When the last AI value of a document is verified, `resolve_vendors` (→ mapping) is queued, so links held as Suggested only for unverified AI values can become Auto. |
| Zero-amount rows | `reconbench_core/recon/engine.py` | A row with amount 0 (e.g. Shipping 0.00) no longer fails ITEM_NOT_ON_PO. |
| PO-link reason | `engine.py` | A suggested PO link adds LINK_INFERRED to the case reasons (the check was already there). |
| AI lines missing | `reconbench_core/extraction/merge.py`, `schema/__init__.py`, `recon/registry.py`, `engine.py`, `ingestion.py:append_ai_version`, `db/seed_data.py` | An AI reading with fewer lines than the rules reading gets reason AI_LINES_MISSING, a Needs-review check `s1.ai_lines_read` and an open document warning. |
| Half GST rate | `reconbench_core/extraction/vision.py:_split_tax_rate` | An AI-read line rate equal to one of two equal CGST/SGST amounts is left out. |
| Scanned PO/GRN AI | `reconbench_app/ai/vision.py` (READ_DOC_TYPES), `ingestion.py` (verify allowed for PO/GRN, wording) | With the switch on AI, scanned POs and GRNs are read by the company vLLM server; a person verifies their values. **Each one costs one vLLM call.** |
| Target guard | `reconbench_core/mapping/links.py:resolve_links` | No link is Auto while its source, target or via document holds an unverified AI value. |
| KI-006 diagnostic | `reconbench_app/ai/answer_shape.py` | `vision_answer_shape` log adds `names=` (field-name counts only). |
| API types | `backend/openapi.json`, `docs/schemas/*`, `web/src/api/generated.ts` | New reason code in the enum. |
| Web | `web/src/pages/ReconciliationPage.tsx`, `MappingPage.tsx`, `utils/reconChecks.ts` (+ tests) | **Already live**: the live web container serves `./web` directly and was restarted on 3 Nov. |
| Records | `docs/claude/{KNOWN_ISSUES,PROJECT_STATUS,DECISIONS,plan-ki006-and-extraction-gaps}.md`, this file | — |

ENGINE_VERSION 0.1.14 → **0.1.15**; RULES_HASH updated. **No migration** (Alembic stays 0036). Live has no catalogue row for AI_LINES_MISSING; the web shows its built-in text.
Not in this deploy (leave untracked): `CODEBASE_CONTEXT.md`, `docs/claude/plan-accuracy-testing.md`, `docs/claude/handoff-recon-analysis.md`, `docs/claude/session-notes-20261101-02.md`, `docs/demo/`.

## Gate (done 3 Nov 2026 on this working tree; re-run step 1 if any file changes)
1. `check_rules_hash` OK, `check_engine_version --base-ref HEAD` 0.1.14 → 0.1.15 OK, `check_core_purity` OK, `lint-imports` 3 kept, `mypy` core clean, `mypy reconbench_app` 80 (baseline), ruff clean on touched files (pre-existing findings only in `tests/integration/test_trace_readonly.py`, untouched).
2. Offline suite: 1579 passed, 22 skipped, 0 failed.
3. Integration (throwaway postgres:16.15): 80 passed across 11 files incl. new `test_flow_three_way_match.py` (3); only KI-003 failures (`test_ai_h1` 1, `test_page_split` 2).
4. Web: `tsc -b` clean, lint clean, vitest 79 passed.
5. `golden_run.py --self-check`: passed (0 false Matched).

## Decision before deploy (owner)
- **Switch position — decided 3 Nov 2026: keep AI.** Live stays on AI (version 45). After this deploy every new scanned invoice, PO and GRN upload costs one vLLM call.

## Deploy steps (live)
1. **Pre-checks (read-only, `PGOPTIONS=-c default_transaction_read_only=on`):** procrastinate `todo`/`doing` = 0 (stop if not); Alembic 0036; counts of `ai_request`, `link` by state, active `document` by type, `document_version`, `recon_run`; switch position; count of `document_version` whose `verification_reasons` contains anything (baseline).
2. **Commits** on `phase2-hosted-ci` (no real values in messages), attribution per the session reminder:
   - "Matching: Verify re-runs link matching; zero-amount rows need no PO line; LINK_INFERRED case reason; AI_LINES_MISSING; half GST rate left out; target-document AI guard" (backend core + app + tests + API types).
   - "Scanned POs and GRNs take the AI reading under the shared switch" (ai/vision.py, ingestion.py gate + wording, tests). If `ingestion.py` hunks cannot be split cleanly, fold into the first commit and say so.
   - "Web: case fix links, plain reason titles, link confidence label" (web/src).
   - "docs: b15 records".
   Local tag `live-20261103-b15` on the last commit. **No push** (commit 76c0f11 still needs rewording before any push).
3. **Backup:** `pg_dump -Fc` → `C:\Users\jainm\reconbench-archive\live-db-20261103-b15.dump` + roles; restore into a throwaway container (`--no-owner`), compare row counts of every public table with live (identical), remove it.
4. **Rollback tags:** `docker tag reconbench-{setup,api,worker,ai-worker}:latest …:pre-b15`.
5. **Build:** `docker compose build setup api worker ai-worker`.
6. **Setup:** `docker compose run --rm setup` → revision stays 0036.
7. **Swap:** `docker compose up -d api worker ai-worker`; `GET http://127.0.0.1:8000/readyz` = 200.
8. **Container check (api, worker, ai-worker):** `ENGINE_VERSION = "0.1.15"`; `grep -c '"resolve_vendors"' ingestion.py` ≥ 5; `grep -c AI_LINES_MISSING reconbench_core/schema/__init__.py` ≥ 1; `grep -c '"po"' reconbench_app/ai/vision.py` ≥ 2; no errors in 2 minutes of logs.
9. **Post-checks (read-only):** queue drains to 0; 0 new failed jobs; `ai_request`, `link`, `document`, `document_version`, `recon_run` unchanged (nothing runs until a person acts); switch unchanged; Alembic 0036.
10. **One-time mapping pass (owner's go, live write):** invoices verified before b15 never had their links re-matched. Run once: `docker compose exec worker python -c "from uuid import UUID; …MappingService(engine).resolve_workspace(UUID('<workspace id>'))"` (or queue `rematch_vendors` for the workspace). Then read back read-only: links changed by state (expected: some Suggested → Auto only on fully verified documents; **0 Auto on any document with unverified AI values**), 0 new `ai_request`.
11. **Records:** decision-log D122 (D119/D120 stay reserved) with gate numbers, commits, tag, backup, checks; KNOWN_ISSUES KI-014/KI-016 → "deployed, awaiting owner check"; PROJECT_STATUS.

## Stop conditions
Any gate failure; queue not empty at step 1; restore counts differ; setup changes the revision; any step-9 counter changes without a person's action; step 10 creates an Auto link on a document with unverified AI values; errors in the logs.

## Rollback
`docker tag reconbench-{api,worker,ai-worker,setup}:pre-b15 …:latest` then `docker compose up -d api worker ai-worker`. **Before rolling back** count `document_version` rows whose `verification_reasons` contain `AI_LINES_MISSING`: the old code cannot read that value (`ReasonCode(...)` raises). If > 0, remove that value from those rows first (owner's go). Web rollback: `git checkout live-20261102-b14 -- web/src` (the container serves `./web`). Restore the b15 dump only if data went wrong.

## Owner's checks afterwards (in the app)
1. **Settings:** add `shipping` (and any other zero-value row words you see) to Charge keywords.
2. **AI-read invoice (inv06 or inv09):** open it → Verify each AI value → wait about a minute → Mapping shows its PO link **Auto** → Reconciliation → Run → expect **Matched** (or only real differences).
3. **Zero-value row (inv05, inv07):** re-run Reconciliation → the shipping row no longer shows "Item not on PO".
4. **Fix links:** open any Needs-review case → "Verify document values" / "Confirm PO link on Mapping" open the right invoice.
5. **Scanned GRN or PO** (only if the switch stays on AI): upload one → it waits for reading → AI values appear → Verify works.
6. Expected limits: inv01/inv04 still Unlinked (their POs are not uploaded); inv08 needs a manual link; inv10 lump-sum rule awaits Sir; inv02 dense page still fails AI reading (KI-006).

## Report back
Gate re-run (if any), commit hashes and tag, backup + restore check, container checks, pre/post counter table, step-10 link changes, `git status --short` at the end.
