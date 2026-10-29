---
name: recon-check
description: Run ReconBench's pre-merge checks on a backend change - ruff, mypy --strict, import contracts, core purity, rules hash, engine-version bump, and focused pytest for the touched area. Use after editing backend/ or scripts/ Python, or when asked to "check", "verify" or "run the checks" on a change.
---

# recon-check

Run from the repo root on Windows; use the venv executables directly (`uv run` may sync the environment).

## 1. Scope
- `git status --short` and `git diff --stat` to list the changed Python files.
- If nothing under `backend/` or `scripts/` changed, say so and stop.

## 2. Static checks (always, in this order; stop and report on the first failure)
1. `backend/.venv/Scripts/ruff.exe check backend/reconbench_core backend/reconbench_app backend/tests scripts`
2. `cd backend && .venv/Scripts/mypy.exe --strict` (source of truth for type errors; LSP diagnostics are hints)
3. `cd backend && .venv/Scripts/lint-imports.exe` (core layer and purity contracts)
4. `backend/.venv/Scripts/python.exe scripts/check_core_purity.py`
5. `backend/.venv/Scripts/python.exe scripts/check_rules_hash.py`
6. If any rule path changed (`backend/reconbench_core/{align,mapping,normalize,recon,schema}`, `backend/reconbench_app/db/seed_data.py`): confirm `ENGINE_VERSION` in `backend/reconbench_core/version.py` was raised; `scripts/check_engine_version.py --base-ref <base>` checks this against git.

## 3. Focused tests (default)
- Map each changed module to its tests (Grep `backend/tests` for the module name; e.g. `recon/engine.py` → `tests/unit/test_phase4_reconciliation.py`, `tests/scenarios/test_hard_zero_guards.py`).
- Run only those: `backend/.venv/Scripts/pytest.exe <test files> -q`.
- Always include `backend/tests/scenarios/test_hard_zero_guards.py` when core rules, mapping or extraction changed.

## 4. Full offline suite (only when the user asks for it)
- `backend/.venv/Scripts/pytest.exe backend/tests/unit backend/tests/parsers backend/tests/property backend/tests/scenarios -q` (about 5-6 minutes; run in the background).
- Integration tests need a disposable PostgreSQL (`RB_PHASE2_TEST_DATABASE_URL`) and MinIO; never point them at a live or shared database.

## 5. Not covered here
- API changes: `make openapi`, then check `git diff -- backend/openapi.json web/src/api/generated.ts docs/schemas`.
- Web lint/tests need Node. Never run the accuracy bench or any provider call without the owner's go.

## Report
Pass/fail per step with the command, failing test names, and what was skipped and why. Update docs/claude/PROJECT_STATUS.md Tests only after a full-suite run.
