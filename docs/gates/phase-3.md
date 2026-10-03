# Phase 3 gate evidence

**Status:** Tasks 3.1–3.10 are implemented and every Phase 3 Done-when criterion has local evidence below. **Local Phase 3 gate: passed.** The repository-root Ruff scope initially reported five unused `# noqa: E402` directives; I removed them and formatted the catalog checker, after which the full Ruff check and formatting check passed. Phase 4 work is now underway. Capability declarations and P0 role resolution are present; P1 role enforcement remains off. Hosted CI and manual browser testing were not run or claimed.

## Inspection and decisions

- Re-inspected the current application, bootstrap seed, SQLAlchemy metadata, migrations, and Phase 2 gate before implementation.
- The existing `app_user`, `session`, and `rate_limit_event` tables represent the required password hash, hashed session/CSRF tokens, expiry/revocation, account lock, and login-attempt data. **No migration was required or created; migration revisions were not changed.**
- Existing config seq 1 is immutable and may predate the newly approved throttle keys. New seeds include them; runtime validation supplies the same approved defaults for older config versions. The seed preserves existing valid canonical hashes.
- G-29 in `docs/decision-log.md` records the user-authorized independent email/IP rolling-window policy, expiry, generic responses, and socket-peer rule. The chosen values are in the checked-in `DEFAULT_CONFIG` and validated by `AuthSecurityConfig`.
- No real PO, GRN, invoice, or other sample files were opened or used.

## Task evidence

| Task / acceptance item | Status | Evidence |
|---|---|---|
| 3.1 Argon2id hash/verify; dummy hash on unknown email; identical generic bad-credential response | Pass | Unit Argon2id verification and PostgreSQL API test comparing unknown-email and wrong-password 401 bodies and timing. |
| 3.2 SHA-256-only session token storage, new token and old-session revocation at login, cookie flags, idle/absolute expiry | Pass | PostgreSQL API test inspects stored token digest, verifies login rotation and expiry with a fake clock; automated and local cookie flags tested. |
| 3.3 CSRF token delivery on login and `/auth/me`; unsafe requests rejected with `CSRF_FAILED` | Pass | PostgreSQL API integration test. |
| 3.4 Login/logout/me, independent account and IP throttles, `rate_limit_event` records, automatic expiry and generic responses | Pass | PostgreSQL integration tests cover both buckets, rolling-window edge, 15-minute expiry, success reset semantics, same 401/429 body, ignored arbitrary `X-Forwarded-For`, and concurrent attempts. |
| 3.5 Capability matrix, declarations and route/service audit | Pass | `authorization.py` contains the full Plan §3.5 matrix. P0 resolves every persisted or client-supplied role to manager; the app returns manager capabilities. All routes and public service methods require a named capability or an allowlisted public marker. Unit test proves the audit rejects an injected undeclared route and service entry point. P1 role enforcement remains off. |
| 3.6 Security headers and middleware | Pass | ASGI middleware sets CSP `default-src 'self'; frame-ancestors 'none'`, `X-Content-Type-Options: nosniff`, and `Referrer-Policy: same-origin` on responses including errors. Tests check closed CORS and absence of app-level HSTS. HSTS remains a Phase 7 proxy setting; file-response CSP sandbox remains Task 4.8. |
| 3.7 Actor context and audit helper | Pass | Authenticated requests expose `ActorContext` with workspace, DB-authenticated actor ID, and P0 manager role. `write_audit_event` participates in the caller transaction and omits trigger-owned sequence/time/hash fields. PostgreSQL integration test verifies trigger-generated chain values and that `reconbench_app` cannot update audit rows. No schema change was needed. |
| 3.8 Shared API conventions | Pass | Shared Problem Details/error mapping, keyset pagination, If-Match/409 current state, idempotency, decimal/date serialization, conditional GET, and 202 job response foundations are covered by focused tests and PostgreSQL idempotency integration tests. |
| 3.9 Login screen | Pass | Accessible login form uses existing login API, generic 401 text, and coarse Retry-After information when supplied. Vitest covers the failure and success paths. |
| 3.10 Session-aware web flow | Pass | `/auth/me` bootstrap, protected route guard, deep-link preservation, expired-session notice, CSRF logout, history/back guard, capability-disabled reason, and P0 manager access are covered in Vitest. |
| Full Phase 3 “Done when” criteria | Pass | Each of the ten criteria in the Plan has local test, integration, generated-contract, or prior Tasks 3.1–3.7 evidence below. Manual browser testing was explicitly deferred; no manual-test claim is made. |

## Commands and results

All database checks used named disposable PostgreSQL 16 databases. The final auth run used the isolated PostgreSQL 16.15 container `reconbench-phase3-auth-isolated-20261001`, port 41632, database `reconbench_phase2_auth_isolated`; it started with one empty database and no pre-existing PostgreSQL roles. No Phase 1 Compose or production database was used.

| Command | Result |
|---|---|
| `.\.tools\uv\Scripts\uv.exe run --project backend pytest backend/tests/unit/test_authentication.py -q` | **8 passed**. |
| `$env:RB_PHASE3_TEST_DATABASE_URL='postgresql+psycopg://postgres:phase3-isolated-synthetic@127.0.0.1:41632/reconbench_phase2_auth_isolated'; .\.tools\uv\Scripts\uv.exe run --project backend pytest backend/tests/integration/test_authentication.py -q` | **11 passed** on PostgreSQL 16.15 migrated to Alembic head. |
| `$env:RB_MIGRATOR_DATABASE_URL='postgresql+psycopg://postgres:phase3-isolated-synthetic@127.0.0.1:41632/reconbench_phase2_auth_isolated'; .\.tools\uv\Scripts\uv.exe run --project backend alembic -c backend/alembic.ini upgrade head` | **Passed**, revisions 0001–0015 on the clean disposable database. This was test setup only; no migration file changed. |
| `$env:RB_PHASE2_TEST_DATABASE_URL='postgresql+psycopg://postgres:phase3-local-ephemeral@127.0.0.1:41631/reconbench_phase2_auth'; .\.tools\uv\Scripts\uv.exe run --project backend pytest backend/tests/integration/test_phase2_database.py -q` | **1 passed**. |
| Same `RB_PHASE2_TEST_DATABASE_URL`; `.\.tools\uv\Scripts\uv.exe run --project backend pytest backend/tests/integration/test_phase2_readyz.py -q` | **1 passed**. |
| `.\.tools\uv\Scripts\uv.exe run --project backend ruff check backend` | **Passed**. |
| `.\.tools\uv\Scripts\uv.exe run --project backend ruff format --check` on the eight changed Python files | **Passed**, 8 files already formatted. |
| `.\.tools\uv\Scripts\uv.exe run --project backend mypy backend/reconbench_core` | **Passed**, 8 source files. |
| `.\.tools\uv\Scripts\uv.exe run --project backend mypy --strict` on `authentication.py`, `api/auth.py`, `settings.py`, `db/seed.py`, and `db/seed_data.py` | **Passed**, 5 source files. |
| `git diff --check` | **Passed**. |

The auth unit and integration modules share a basename, so they were run as separate pytest commands (a combined collection fails before running tests due pytest's duplicate-module-name rule). A first attempt to reuse the Phase 2 test database for auth was rejected by the seed's intentional single-workspace P0 guard after the Phase 2 fixture inserted another synthetic workspace; auth tests then passed on the clean isolated cluster above. The project-configured mypy target remains `reconbench_core`; a broader exploratory app-factory check reports missing boto3/botocore stubs and Pydantic `BaseSettings` constructor-call warnings, so the app factory is covered by Ruff and integration tests rather than claimed as a strict-mypy pass.

## Tasks 3.5–3.7 continuation evidence

Before implementation, the current Tasks 3.1–3.4 code, Phase 2 metadata/migrations, seed flow, and the relevant Implementation Plan, Schema §4.8, TRD TR-REV-5/TR-SEC-13/TR-SEC-14, App Flow, PRD, and UI/UX sections were re-inspected. The existing `audit_log` trigger owns `seq`, `occurred_at`, `prev_hash`, and `row_hash`; the application role has append-only insert/select behavior with database guards. Those existing protections cover the helper, so no migration or DDL change was needed. Source copies `docs/ReconBench_*.md` were not modified.

The current continuation checks used only the disposable PostgreSQL 16.15 container `reconbench-phase3-357-20261002` (port 41633, database `reconbench_phase2_phase3_357`) with synthetic fixtures. Phase 1 Compose services/databases and production data were not used.

| Command | Result |
|---|---|
| `.\.tools\uv\Scripts\uv.exe run --project backend pytest backend/tests/unit/test_authentication.py backend/tests/unit/test_authorization.py -q` | **13 passed.** Covers the exact role matrix, P0 manager resolution, route/service audit including injected undeclared entries, headers/closed CORS, protected docs, and actor context. |
| `$env:RB_PHASE3_TEST_DATABASE_URL='postgresql+psycopg://postgres:phase3-357-synthetic@127.0.0.1:41633/reconbench_phase2_phase3_357'; .\.tools\uv\Scripts\uv.exe run --project backend pytest backend/tests/integration/test_authentication.py -q` | **13 passed** on PostgreSQL 16.15. Includes helper insert, trigger-owned sequence/hash chain, transaction requirement, actor values, JSON serialization, append-only permissions, readiness headers, and existing auth behavior. |
| `$env:RB_PHASE2_TEST_DATABASE_URL='postgresql+psycopg://postgres:phase3-357-synthetic@127.0.0.1:41633/reconbench_phase2_phase3_357'; .\.tools\uv\Scripts\uv.exe run --project backend pytest backend/tests/integration/test_phase2_database.py -q` | **1 passed.** Rechecked existing repository constraints, database roles, and audit-chain coverage. |
| `.\.tools\uv\Scripts\uv.exe run --project backend ruff check backend` | **Passed.** |
| `.\.tools\uv\Scripts\uv.exe run --project backend ruff format --check backend/reconbench_app/authorization.py backend/reconbench_app/audit.py backend/reconbench_app/middleware.py backend/reconbench_app/api/auth.py backend/reconbench_app/api/application.py backend/reconbench_app/authentication.py backend/reconbench_app/settings.py backend/tests/unit/test_authorization.py backend/tests/unit/test_authentication.py backend/tests/integration/test_authentication.py` | **Passed**, 10 files already formatted. |
| `.\.tools\uv\Scripts\uv.exe run --project backend mypy --strict backend/reconbench_app/authorization.py backend/reconbench_app/audit.py backend/reconbench_app/middleware.py backend/reconbench_app/api/auth.py backend/reconbench_app/authentication.py backend/reconbench_app/settings.py` | **Passed**, 6 source files. |
| `git diff --check` | **Passed** (Git emitted line-ending normalization warnings for existing working-tree files). |

An exploratory whole-backend `ruff format --check backend` is not clean because it reports 14 pre-existing migration files and `db/procrastinate_schema.py`; none was changed or reformatted. The focused changed-file format check is green. `api/application.py` remains covered by Ruff and request tests; it was not included in the strict-mypy subset because this repository reports the existing missing boto3/botocore stubs and Pydantic `BaseSettings` constructor-call warnings there.

## Tasks 3.8–3.10 continuation evidence

The current repository, Tasks 3.1–3.7 implementation, all six read-only `docs/ReconBench_*.md` sources, Phase 2 metadata/migrations, and seed/bootstrap flow were inspected before edits. The existing auth, capability, middleware, actor, audit, version-concurrency, decimal JSON, and `idempotency_key` schema were reused. No schema gap was found; no migration revision was changed. No Phase 4 route, screen, or workflow was added. No real PO, GRN, or invoice samples were opened or used.

The API foundation is reusable but intentionally is not wired to nonexistent business endpoints: Phase 4 will supply those call sites. Keyset ordering requires caller-declared non-null business fields and a unique business-key tiebreaker, rejects ID/UUID ordering, binds cursors to filter/sort context, caps page size at 200, and omits `total_count` unless `count=true`. Idempotency records the first result and operation in one transaction, replays the 202 `Location`, rejects a changed body, expires keys after 24 hours, handles concurrent same-key requests, and explicitly excludes login/logout. Problem Details keep submitted values out of errors and use stable codes; API 500s also use the shared Problem Details media type. The web routes use the existing auth API; protected content remains behind `/auth/me` and the route guard.

| Command | Result |
|---|---|
| `docker run --name reconbench-phase3-final-20261002 -e POSTGRES_PASSWORD=<redacted> -e POSTGRES_DB=postgres -p 127.0.0.1:41635:5432 -d postgres:16.15-bookworm` then `CREATE DATABASE reconbench_phase2_phase3_final` | Started a fresh disposable PostgreSQL 16.15 cluster/database. The migration chain applied successfully through 0001–0015 with `alembic upgrade head`. |
| `$env:RB_MIGRATOR_DATABASE_URL='postgresql+psycopg://postgres:phase3-final-synthetic@127.0.0.1:41635/reconbench_phase2_phase3_final'; .\.tools\uv\Scripts\uv.exe run --project backend alembic -c backend/alembic.ini upgrade head` | **Passed** on the empty disposable database. No migration source was modified. |
| `$env:RB_PHASE3_TEST_DATABASE_URL='postgresql+psycopg://postgres:phase3-final-synthetic@127.0.0.1:41635/reconbench_phase2_phase3_final'; .\.tools\uv\Scripts\uv.exe run --project backend pytest backend/tests/integration/test_authentication.py -q` | **15 passed** on PostgreSQL 16.15. Includes hash-only sessions, auth and audit checks, 24-hour idempotency replay/body mismatch/expiry, and concurrent same-key execution. |
| `.\.tools\uv\Scripts\uv.exe run --project backend pytest backend/tests/unit/test_api_conventions.py backend/tests/unit/test_authorization.py backend/tests/unit/test_authentication.py -q` | **24 passed.** Covers stable keyset pagination across insertion, count opt-in and bounds, current-state 409 and required If-Match, 304, 202/Location, RFC 9457 codes/media type, decimal string and ISO date serialization, idempotency exclusion and replay response, auth behavior, headers, and the web-facing capability/auth declarations. |
| `npm ci --prefix web` | **Passed**, 290 packages installed, zero reported vulnerabilities. |
| `npm run test --prefix web -- --run --pool=threads --maxWorkers=1` | **4 passed.** Deep-link login, generic credential error, retry information, session bootstrap/expiry, logout CSRF, protected-content history guard, and P0 manager/restricted-control rendering. These are automated jsdom tests; no manual browser test was run. |
| `npm run lint --prefix web`; `npm run typecheck --prefix web`; `npm run build --prefix web` | **Passed** (ESLint, TypeScript, and Vite production build). |
| `.\.tools\uv\Scripts\uv.exe run --project backend ruff check backend/reconbench_core backend/reconbench_app backend/tests` | **Passed** for ReconBench source and tests. |
| `.\.tools\uv\Scripts\uv.exe run --project backend mypy --strict` from `backend/`; `uv run lint-imports` from `backend/`; `python scripts/check_core_purity.py`; `python scripts/check_rules_hash.py` | **Passed:** strict mypy (8 source files), both import contracts, core purity, and rules hash. |
| `.\.tools\uv\Scripts\uv.exe run --project backend ruff format --check` on the 16 changed Phase 3 Python files | **Passed**, all 16 formatted. |
| `.\.tools\uv\Scripts\uv.exe run --project backend python scripts/export_openapi.py`; `npm run api:generate --prefix web` | **Passed.** Both generated outputs were hashed before/after a second run and remained byte-identical; auth endpoints, Problem Details, and auth response headers are in OpenAPI. |
| `git diff --check` | **Passed** (Git only reported its existing LF/CRLF normalization notices). |

The exact configured Ruff scope from `Makefile` was run from `backend/` as `ruff check reconbench_core reconbench_app tests ../scripts`; it passed. The repository-root scope `ruff check backend/reconbench_core backend/reconbench_app backend/tests scripts` also passes after removing the five stale suppressions. The Makefile's final `git diff --exit-code` generated-artifact assertion was not used as a pass claim because these intended OpenAPI/client changes are uncommitted; repeated generation is deterministic and showed no drift.

An initial combined database rerun against an already seeded disposable test database was refused by the seed's single-workspace guard. A first attempt at multiple fresh databases in that same PostgreSQL cluster hit the migration chain's cluster-global role creation (`reconbench_migrator` already exists). Verification was rerun successfully on the separate fresh cluster/database listed above. No Phase 1 Compose or production database was used.

## Remaining items

- No Phase 3 product/schema input remains unresolved. Existing P0 throttle policy remains recorded in G-29; persisted schema suffices and migrations remain unchanged.
- Tasks 3.1–3.10 Done-when criteria have local evidence, and the local Phase 3 gate passes. Hosted CI and manual browser testing were not run. Manual browser testing can now follow; the full reconciliation workflows remain Phase 4 work.
- Production proxy trust configuration remains a deployment item: the app currently uses the socket peer and ignores forwarded headers. This is not a blocker for local Phase 3 behavior.
