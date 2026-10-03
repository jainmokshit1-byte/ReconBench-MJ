# ReconBench

ReconBench is an auditable, invoice-centred three-way reconciliation workbench for purchase orders, goods-received notes, and supplier invoices.

## Current implementation scope

The repository contains the Phase 1 scaffold, Phase 2 database foundation, Phase 3 authentication, and a local Phase 4 implementation on synthetic fixtures. Phase 4 includes canonical parsing and matching, ingestion and Mapping workflows, and PO ledger rebuilds; persisted reconciliation runs, review actions, exports, and later tasks remain unfinished. The six source documents are preserved in [`docs/`](docs/); those copies are read-only. Local acceptance fixtures are synthetic and derived from the PRD examples. The `golden/` directory intentionally contains no real or customer documents.

### Prerequisites

- Python 3.12 (the checked-in `.python-version` pins the minor release), `uv`
- Node.js 24 and npm
- Docker Engine / Docker Desktop with Compose v2
- GNU Make

### First run

```sh
make up
make check
```

`make up` creates an ignored `.env` with randomly generated local credentials if needed, then binds services to loopback ports. Compose replaces the non-secret `RB_S3_API_*` placeholders with those local credentials. PostgreSQL uses host port `15432` to avoid a common local database port conflict; MinIO uses `9000`/`9001` and the API uses `8000`. Those credentials are only for local development; configure independent credentials before any non-local environment. `make down` stops containers; `make clean-local` also removes only the ReconBench Phase 1 named data volumes.

The local Compose stack uses PostgreSQL 16 and MinIO as required by the Phase 1 plan. MinIO's upstream community repository is archived; see [`docs/dependency-review.md`](docs/dependency-review.md) and decision G-28 before using an object store for a deployed environment.

The worker image includes OCRmyPDF and Tesseract for scanned PDFs. Scanned pages are OCR-processed in the isolated PDF worker; the uploaded PDF stays unchanged, and OCR text uses lower confidence with the normal field and arithmetic checks. The default OCR language is English (`RB_PDF_OCR_LANGUAGES=eng`); set `eng+hin` to also recognize Hindi. Non-Docker worker setups need OCRmyPDF and the matching Tesseract language data installed separately. Real vendor scans are still required to measure extraction accuracy.

### Useful commands

| Command | Purpose |
|---|---|
| `make up` | Build and start PostgreSQL, MinIO, and the API scaffold |
| `make down` | Stop local services, retaining volumes |
| `make check` | Lint and type-check Python/TypeScript; run scaffold smoke tests |
| `make test` | Run the Python and web test suites |
| `make test-e2e` | Run Chromium browser workflows against controlled API fixtures |
| `make golden` | Run the empty golden-set harness and its false-Matched self-check |
| `make openapi` | Regenerate the OpenAPI snapshot and TypeScript types |
| `make migrate` | Apply ReconBench Alembic revisions through head |
| `make schema-drift` | Confirm Alembic head, compare metadata, and check catalog-only schema objects |
| `make queue-schema` | Apply the separately locked Procrastinate 3.10.0 schema (once, to an empty queue schema) |
| `make web` | Run the static web shell on Vite's local development server |
| `reconbench rebuild-ledgers` | Rebuild every current PO ledger and process outstanding dirty scopes |
| `reconbench drain-workers` | Run a one-shot queue worker until it catches up, then exit |

`make test-e2e` needs the locked npm dependencies and a Playwright Chromium install (`npx playwright install chromium`). Its API responses are deterministic synthetic fixtures for browser workflow coverage; use the backend PostgreSQL/MinIO integration tests for service and storage verification.

The queue library owns its tables. Apply `make migrate` first and `make queue-schema` as a separate step; the queue schema is pinned by `backend/uv.lock` and is not embedded in ReconBench Alembic revisions. Alembic uses `RB_MIGRATOR_DATABASE_URL`; with `RB_ENV=local`, it may fall back to `RB_DATABASE_URL` or derive the host DSN from the local Compose `POSTGRES_*` values. Non-local runs require a dedicated migrator URL.

`make schema-drift` requires the ReconBench database to be at Alembic head and uses the same migrator DSN. It checks the application schema before the separate queue schema is applied; Procrastinate-owned tables are not part of ReconBench metadata. The static metadata and the catalog-only checks are documented in [`docs/schema-drift-check.md`](docs/schema-drift-check.md).

The API exposes liveness at `/healthz`, readiness at `/readyz`, and the versioned API base at `/api/v1`. Readiness reports success only when PostgreSQL and the object store respond.

The `reconbench` CLI is installed with the backend package. `drain-workers` starts a short-lived worker which exits after it catches up; it does **not** signal or stop other worker processes. To stop deployed workers gracefully, send SIGTERM through the process supervisor (for example, `docker compose stop -t 300 <worker-service>`); that supervisor action is not performed by the CLI.
