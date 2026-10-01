# Phase 1 gate report

**Date:** 2026-10-01 · **Commit:** Phase 1 root commit (this report is included) · **Author/agent:** Codex

## Built

- **1.1–1.2:** Initialized the monorepo; pinned Python 3.12 and Node 24; checked in `uv.lock` and `web/package-lock.json`.
- **1.3–1.3a:** Added the pure `reconbench_core` package boundary, AST checks, import-linter contracts, and typed application settings.
- **1.4–1.5:** Added `.env.example`, local-only generated credentials, isolated named Compose volumes, PostgreSQL 16, source-built local MinIO, API health/readiness, and loopback ports.
- **1.6:** Added GitHub Actions stages for lint/types, smoke tests, PostgreSQL/object-store integration, golden harness, rules hash, audits, image builds, SBOM/image scans, and Gitleaks.
- **1.7–1.8:** Recorded G-01…G-28, created the gate template, and reviewed direct dependencies and licences in `docs/dependency-review.md`.
- **1.9–1.11:** Added synthetic SP-1…SP-4 spikes, SCN-01…20 fixture pairs, C1 verification, and the fail-closed golden harness.
- **1.12:** Added the six-route responsive React shell, bundled Inter and Lucide assets, semantic design tokens, and OpenAPI TypeScript generation.
- **1.13–1.14:** Added the FastAPI scaffold, RFC 9457 errors, request IDs, JSON body limit, Makefile, Dockerfiles, and local run instructions.

## Evidence for each "Done when" line

| Criterion | Evidence (test / command / file / screenshot) | Result |
|---|---|---|
| PostgreSQL 16 + MinIO start; health and readiness answer 200 | Built the API and MinIO images; ran `docker compose up -d postgres minio api`; `GET /healthz` returned `ok`; `GET /readyz` returned `ready`; Compose showed PostgreSQL and MinIO healthy. | Pass |
| `make check` lint, types and smoke checks | GNU Make is unavailable on this Windows host, so its component commands were run directly. Ruff, strict mypy, import-linter, core-purity AST checks, OpenAPI/client regeneration, pytest smoke tests, ESLint, TypeScript, and Vitest passed. | Component checks pass; literal `make check` not run |
| Purity rejects SQLAlchemy, wall-clock access, and float money/quantity use | `backend/tests/unit/test_core_purity.py` exercises forbidden imports, clocks/environment/randomness, and float usage; the core import contracts pass. | Pass |
| Env template complete; required settings fail fast; secret scan and history clean | `.env.example` covers application, database, MinIO, bootstrap, and observability variables. `test_required_settings_fail_when_unconfigured` proves blank required values fail validation. Local `.env` is ignored and excluded from Docker contexts. Pinned Gitleaks v8.29.1 detected a synthetic high-entropy probe and reported no leaks in staged files or the Phase 1 Git history. | Pass |
| CI stages exist through image build | `.github/workflows/ci.yml` wires lint/types, tests, PostgreSQL + MinIO integration, golden self-check, rules hash, dependency audits, API/web/MinIO image builds, SBOM and Trivy scans, and Gitleaks. | Wired; hosted CI has not run |
| Decision log and dependency/licence review; PyMuPDF excluded | `docs/decision-log.md` records G-01…G-28, including user-confirmed decisions and spike outcomes. `docs/dependency-review.md` records locked dependencies/licences/advisory status. PyMuPDF is absent from both lockfiles. | Pass |
| SP-1…SP-4 results and queue keep/replace decision | SP-1 passed atomic enqueue/rollback, advisory locks, stalled-job detection + explicit retry, and SIGTERM drain. SP-2 passed 36 shuffled assignments. SP-3 extracted 6/6 synthetic text fields. SP-4 detected synthetic XML/CSV profiles and preserved CSV strings. The qualified Procrastinate decision and M2 follow-up are in G-23. | Pass with recovery-policy limitation |
| Synthetic SCN-01…20 expected files and C1 numbers | `fixtures/SCN-01`…`SCN-20` each contain `input.yaml` and `expected.yaml`; `backend/tests/scenarios/test_c1_fixture.py` verifies the C1 example. SCN-19 and SCN-20 are synthetic pending cases as specified. | Pass |
| Golden harness rejects a false Matched result | `make golden` equivalent `python scripts/golden_run.py --self-check` passes the zero-case run and deliberately false-Matched self-check. | Pass |
| Web shell at 1366 px and OpenAPI client has no drift | Browser check at 1366×768 rendered Home with no horizontal overflow and a labeled primary navigation. React routes cover Home, Ingestion, Mapping, Reconciliation, and Settings. `scripts/export_openapi.py` plus TypeScript regeneration produced no diff. | Pass |

## Deviations from the plan (and why)

- PostgreSQL publishes on loopback port 15432 because 5432 was already bound and 55432 was in a Windows excluded-port range. The project uses explicitly named `reconbench_phase1_*` volumes after Compose revealed an unrelated volume with the generic name; the unrelated volume was left untouched.
- The upstream MinIO Community image was unavailable, so local Compose builds the pinned source release. The project repository is archived; this is for local parity only, and G-28 remains open for staging/production.
- GNU Make was not installed on the host. Equivalent target commands were executed directly; the literal Make target remains to be checked on a GNU Make host or CI.
- Phase 1 adds a static web shell and API scaffold only. No ReconBench schema, migrations, authentication, or business logic were started.

## Decision-log changes (G-xx opened / closed)

- Preserved user-confirmed G-03, G-04, G-08 defaults, and G-19 rules from the Implementation Plan.
- Added G-28 for MinIO source-only local use and the required supported object-store/licence decision before staging.
- Recorded SP-1…SP-4 outcomes under G-23; Procrastinate remains provisional behind `JobQueue` pending M2 load/recovery testing.
- No product-input decision was inferred from sample documents.

## New risks or open questions

- **G-07:** real Tally/CSV/PDF/GRN samples and parser acceptance remain gated; none were opened or used.
- **G-08:** actual buyer GSTINs, GRN reference practice, and IRN prevalence remain unconfirmed.
- **G-09/G-10/G-11:** deployment and AI-provider choice, legal retention/DPDP review, and volume/page-limit confirmation remain open.
- **G-23:** automatic recovery behavior and queue load remain for M2; the spike used explicit stalled-job retry.
- **G-28:** choose a supported object-store target and approve its licence before staging.
- Other product/schema decisions remain in `docs/decision-log.md`; Phase 2 is not started.

## Sign-off (human reviewer) · Date

Pending.
