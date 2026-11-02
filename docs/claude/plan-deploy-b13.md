# Deploy plan b13 — KI-010 / KI-011 mapping fix (2 Nov 2026)

Status: **executed 2 Nov 2026 (D118).** Gate passed; deployed as live-20261102-b13; the live mapping pass changed exactly 1 link.

## What ships
| Part | Change | Live effect |
|---|---|---|
| Backend (api, worker, ai-worker images) | `mapping_service.py` `PriorLinkDecisions`; `links.py` line-only rejections + evidence note; `link_type` in the vendor documents list; ENGINE 0.1.13 → 0.1.14; RULES_HASH updated | Next mapping pass: a person's line Remove blocks only that line's PO. Invoice 267 is expected to get a **suggested** link to PO/26-27/08-136 |
| Web (`web/src`, served live from the folder) | `work/patches/mapping-grn-link-web.patch` (3 files) | GRN link shown as "GRN …", never as the PO; "No PO link yet" when only a GRN link exists |
| Database | none (Alembic stays 0036, seed data unchanged) | — |

Order-safe: the old web ignores `link_type`; the new web treats a missing `link_type` as a PO link (old behaviour). Backend and web can go in either order.

## Gate before the deploy (offline / throwaway only)
1. Full offline suite: `backend/.venv/Scripts/pytest.exe backend/tests/unit backend/tests/parsers backend/tests/property backend/tests/scenarios -q` (~6 min). Pass = no new failures.
2. Integration on a disposable postgres:16.15 (127.0.0.1:15499, recipe in `handoff-step6.md`): `test_ingestion.py` (done: 4 passed) and `test_phase4_task_411_413.py` (mapping orchestration).
3. Golden harness: `scripts/golden_run.py --self-check` (0 false Matched).
4. Web: already done in a scratch copy (tsc, eslint, vitest 74 passed).

## Deploy steps (live)
1. Pre-checks (read-only): queue empty (`todo`/`doing` = 0); Alembic 0036; record `ai_request` count, link count and max `link.created_at`; scanned-invoice switch position.
2. Commit on `phase2-hosted-ci` (owner's go needed) and local tag `live-20261102-b13`. Not pushed.
3. Backup: `pg_dump -Fc` → `C:\Users\jainm\reconbench-archive\live-db-20261102-b13.dump` + roles; test restore on a throwaway container, row counts identical in every table, container removed.
4. Rollback tags: `docker tag reconbench-{setup,api,worker,ai-worker}:latest …:pre-b13`.
5. Build: `docker compose build setup api worker ai-worker`.
6. Setup: `docker compose run --rm setup` (expect revision 0036, no migration).
7. Swap: `docker compose up -d api worker ai-worker` (seconds of API downtime).
8. Container check: `ENGINE_VERSION = "0.1.14"` and `rejected_line_po_pairs` present in the worker and api images; `/readyz` 200; no errors in the first logs.
9. Web: `git apply work/patches/mapping-grn-link-web.patch` (the live web container picks it up immediately); load the Mapping page once.
10. One mapping pass on live (owner's go needed; it writes link and resolution rows): run `MappingService.resolve_workspace` once inside the worker container. Expected, from the replay on the 1 Nov backup: **exactly 1 link change** (invoice 267 → PO/26-27/08-136, suggested, evidence ends "line 3 to this PO was removed by a person earlier"). Without this step the link appears only at the next natural mapping pass (any upload, correction or vendor assignment).
11. Post-checks: link diff = 1 (stop and report if different); 0 new auto links; `ai_request` unchanged; queue drains; switch position unchanged.
12. Records: decision-log entry, KNOWN_ISSUES (KI-010/KI-011 → "deployed, seen on live" after the owner's UI check), PROJECT_STATUS.

## Stop conditions
Any gate failure; queue not empty at step 1; restore row counts differ; setup changes the revision; the mapping pass changes more than the one expected link.

## Rollback
1. `docker tag reconbench-{api,worker,ai-worker,setup}:pre-b13 …:latest` and `docker compose up -d api worker ai-worker`.
2. Web: `git apply -R work/patches/mapping-grn-link-web.patch`.
3. Data: links are append-only; the new suggested link row stays in history and the old code supersedes it at its next pass. Restore the b13 dump only if something else went wrong.

## Owner's UI check afterwards
Mapping → Vijayshree Telecom → Invoice tab → 267: "PO PO/26-27/08-136 · Suggested", separate "GRN GRN/BCSL/25-26/08-124" line, Evidence note about line 3, line 3 "Removed by MJ · Link again". Confirming is the owner's decision.

## Open point (not part of this deploy)
The scanned-invoice switch is still **"Read with AI"** (settings v45, set during the 1 Nov test). Any scanned upload sends requests to the company AI server. Set it back to rules in Settings if no AI test is planned.
