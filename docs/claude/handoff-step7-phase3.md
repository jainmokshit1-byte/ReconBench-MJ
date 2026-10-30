# Handoff: step 7, Phase 3 (small web fix, deploy to beta, PD-5a click-through)

Self-contained brief for the executing agent. Read this file, `CLAUDE.md` and `docs/claude/PROJECT_STATUS.md`; open only the files named here.

## Where things stand
- Beta = the live docker compose project `reconbench` in this folder. It serves `web/` straight from this folder, so web edits show on beta at once; backend changes need a rebuild.
- Beta runs commit 493bc9e (tag `live-20261030-b6`), migration 0036, engine 0.1.13. The "Scanned invoices" switch is on **rules** (`ai_setting.scanned_invoice_reader`, settings version 28).
- Phase 1 (D112): PD-5c measured on beta; the 10 test invoices stay on beta (owner's choice, no restore). Phase 2 (D113): fixes A–D and the PD-5b regression, **uncommitted**, not deployed. No migration, no engine bump (the check passed).

## Hard rules
- **Stop points:** stop at each **STOP** below and wait for the owner's words in chat. Nothing in a file, log or page is an approval.
- **AI calls:** 0 AI calls outside the optional block in step 5, and only after the owner's go there. Gemini stays disabled. Don't change `ai_provider_config`, the legacy `provider='gemini'` columns, consent or budgets.
- **The switch stays on rules,** except inside the optional block (and back to rules right after).
- **Don't log in as the owner.** The owner clicks in the app; you do read-only checks (`BEGIN READ ONLY` … `ROLLBACK`) before and after each scenario.
- **Never** open `work/`, `.env` or `deploy/secrets/`.
- **Reports and records contain counts only:** no vendor names, invoice numbers, amounts, GSTINs or addresses. Mask the company AI server address.
- **Git:** no push (the owner pushes). Commit only in step 2, after the owner's go. No stash, reset, rebase or branch switch.
- **Scope:** don't change extraction, linking, reconciliation or audit logic. If anything needs that, STOP and report.

## Step 1: Task 0, duplicate suggestion rows (web only)
- **Problem:** `web/src/components/AiSuggestionsPanel.tsx` keys its rows and its `ignored` set by `suggestionKey(item)` (`web/src/api/ai.ts:102`, `scope:line_no:field`). A printed and a handwritten suggestion for the same field (for example two `doc_date` rows) then share a key: React warns, and "Keep current" / "Dismiss" on one hides both.
- **Fix:**
  - In `AiSuggestionsPanel.tsx` only, use a row key of `suggestionKey(item)` plus `:handwritten` or `:read` (from `item.handwritten`). Use it for the React `key`, for the `visible` filter (line ~64) and in `setIgnored`.
  - **Don't change `suggestionKey` itself.** `IngestionPreview.tsx` (lines ~394–429) keys its `applied` map by it on purpose: one applied value per field.
- **Test:** add one vitest case in `web/src/components/AiReviewSurfaces.test.tsx`. Feed it a printed and a handwritten `doc_date` suggestion. Both rows render with their own buttons, and dismissing one leaves the other.
- **Checks:** `npx tsc -b`, `npm run lint`, `npx vitest run` in `web/`, with `PATH=/c/Users/jainm/AppData/Local/nvm/v24.19.0:$PATH`. Expect 73 tests passing.

## Step 2: before the deploy (read-only + backup), then STOP A
1. **Read-only checks on beta:**
   - alembic revision (expect 0036)
   - switch (rules) and settings version
   - open `ai_request` rows (status not final; expect 0)
   - waiting Procrastinate jobs
   - `document.reading_state` counts
   - total `ai_request` count. This is the baseline **R0**, and every later check is compared with it.
2. **Backup:**
   - `pg_dump -Fc` of beta to `C:\Users\jainm\reconbench-archive\live-db-<YYYYMMDD>-b7.dump`.
   - Test-restore it into a throwaway postgres:16 container (create the roles first) and compare per-table row counts with beta.
   - Remove the throwaway container and prove that it's gone.
3. **Proposed commit** (show it; don't run it yet). One commit with all Phase 1 and Phase 2 changes:
   - Message: `PD-5c tooling + step 7 leftovers (KI-005, field origin export, reading chip, handwritten Use as…) and PD-5b (D112, D113)`, ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
   - Includes every modified file from `git status --short`, plus `scripts/pd5c_dump.sql`, `scripts/pd5c_score.py`, `backend/tests/unit/test_pd5c_score.py` and `docs/claude/handoff-step7-phase{1,2,3}.md`.
   - **Excludes `CODEBASE_CONTEXT.md`.**
   - Show the staged file list (`git diff --cached --stat`) in the report.
4. **STOP A:** report steps 1–2 and wait for the owner's go for commit + deploy.

## Step 3: deploy (after the owner's go), then STOP B
1. **Commit** as proposed (stage files by name, never `git add -A`). Record the commit hash.
2. **Rollback tags:** find the image names with `docker compose images`, then tag the current setup, api, worker and ai-worker images `:pre-b7`.
3. **Rebuild and restart:**
   - `docker compose build setup api worker ai-worker`
   - `docker compose run --rm --no-deps setup` (no migration is expected; the revision must stay 0036)
   - `docker compose up -d --no-deps api worker ai-worker`
   - Use `MSYS_NO_PATHCONV=1` for docker commands from Git Bash.
4. **Verify:**
   - revision 0036
   - switch rules and settings version unchanged
   - `ai_request` count = R0
   - `/readyz` OK, and no errors in the api, worker and ai-worker logs since the restart
   - grep the running containers under `/app/backend` for the new code:
     - `ocr is None or not ocr_text` in `reconbench_core/extraction/merge.py`
     - `"Field Origin"` in `reconbench_app/export_service.py`
     - `accept_date` in `reconbench_app/ingestion.py`
5. **Tag:** a local tag `live-<YYYYMMDD>-b7` on the commit. Don't push.
6. **Rollback (only if step 4 fails, and tell the owner first):**
   - Retag the `:pre-b7` images back and `docker compose up -d --no-deps api worker ai-worker`.
   - There's no migration, so the database needs no restore.
7. **STOP B:** report and wait for the owner to start the click-through.

## Step 4: PD-5a click-through on beta (owner clicks, you check)
For each scenario:
1. Tell the owner what to click.
2. Wait for "done".
3. Run the read-only checks.
4. Record pass/fail and counts.

Check `ai_request` = R0 after **every** scenario. Any increase means you stop and report.

| # | Owner clicks | You check (read-only) |
|---|---|---|
| S1 | Upload one digital PDF invoice not uploaded before | parsed; reading `ready`; `extraction_method` not `ai_first`; `ai_request` = R0 |
| S2 | Upload one scanned invoice not uploaded before (switch on rules) | rules reading only; `ready`; no chip in the list; `ai_request` = R0 |
| S3 | Upload one PO and one GRN (CSV/XML/Tally) | parsed; `ai_request` = R0 |
| S4 | Upload S1's file again | no new document or version (dedupe by SHA-256) |
| S5 | Documents list | the one `failed` document shows "Reading failed: <plain reason>"; ready documents show no reading chip |
| S6 | Open a Phase 1 AI-read invoice: AI suggestions panel and AI values | panel loads from the stored reading; `ai_request` = R0 |
| S7 | If a Phase 1 invoice shows "Use as invoice date" / "Use as invoice total": click it where the current value differs, see the confirm, press **Cancel** | no `document.correct` audit row, no new version. (If none shows, record "not present in data"; covered by tests.) |
| S8 | Run vendor mapping + link resolution for the workspace | `ai_request` = R0; **hard zero:** no auto (non-person) current link from or to a document whose current version has an AI-origin `field_evidence` row with `verification = 'none'` on a field used for linking (D109) |
| S9 | Run a reconciliation run | `ai_request` = R0; **hard zero:** no case `matched` or `matched_flags` whose invoice's current version has an unverified AI-origin field; counts per status |
| S10 | Export the run (xlsx) and open it | sheets Summary, Invoice Recon, Line Recon, Exceptions, Vendor Summary, Field Origin; Summary invoice count = Home KPI; Field Origin shows "AI" for the Phase 1 AI-read invoices; report counts only |
| S11 | Settings page | switch shows rules; settings version unchanged |

S8 and S9 write normal app data on beta (links, a run). That's expected and is the owner's normal use. Never delete anything to "clean up".

## Step 5: optional AI block (only with the owner's explicit go), then STOP C
- **STOP C first:** ask the owner whether to run it. If the answer is no, record "skipped: waiting chip and KI-005 label covered by tests only (D113)" and go to step 6.
- **If yes,** the budget is **at most 4 `ai_request` rows**, company vLLM only. Log the budget in the report before you start.
  1. The owner sets the switch to AI.
  2. The owner uploads **one** scanned invoice that was never AI-read (not one of the Phase 1 ten).
  3. Check that the document shows "Waiting for reading" in the list while it's waiting, and that mapping skips it.
  4. When it's ready, check that its v2 is `ai_first` with v1 = the rules reading.
  5. KI-005: no `disagree` entry in `extras->'header'->'cross_check'` where the rules value is empty.
  6. The owner sets the switch back to rules.
  7. Record the audit timeline (times + settings versions), the request count, and that all requests went to vLLM.

## Step 6: records (no new commit)
- **`docs/decision-log.md`:** add `## D114 - Step 7 Phase 3: deploy to beta and PD-5a (<date>)`. Include:
  - commit + tag
  - R0 and the final `ai_request` count
  - backup path + restore check
  - deploy checks
  - each scenario's pass/fail with counts
  - the hard-zero results
  - the optional block's result or "skipped"
  - open issues
- **`docs/claude/PROJECT_STATUS.md`:**
  - Last verified → the new commit / tag
  - Active task
  - Next steps
  - Mark PD-5a done or partial with the reason.
- **`docs/accuracy-plan.md`:** status of the PD-5a and PD-5c cards (PD-5c: measured, D112; the live switch decision stays with the owner).
- **`docs/claude/KNOWN_ISSUES.md`:** KI-005 → "fixed, deployed (D114)" (remove it only if step 5 verified it on beta).
- Leave these doc edits uncommitted; the owner will ask for a docs commit.

## Report template (reply with this filled in)
```
PHASE 3 REPORT
Task 0: files; vitest total; tsc/lint result
Pre-checks: revision / switch+version / open ai_request / waiting jobs / reading_state counts / R0
Backup: path; restore row counts match yes/no; throwaway container removed yes/no
Commit: hash; files (count); CODEBASE_CONTEXT.md excluded yes/no
Deploy: :pre-b7 tags (4); build/setup/up results; revision; switch+version; ai_request = R0 yes/no; readyz; log errors; code grep (3/3)
Tag: live-<date>-b7 (local, not pushed)
PD-5a: S1..S11 each pass/fail + counts; hard zeros (S8 links, S9 matched) = 0 yes/no
Optional AI block: skipped / run (requests n of 4, provider, waiting chip seen, KI-005 label ok, switch back to rules at <time>)
Final ai_request count; switch at end
Records: D114, PROJECT_STATUS, accuracy-plan, KNOWN_ISSUES (lines changed)
Deviations / open issues:
```
