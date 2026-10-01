# ReconBench

ReconBench is an auditable, invoice-centred three-way reconciliation workbench for purchase orders, goods-received notes, and supplier invoices.

## Phase 1 — Project setup

This repository currently contains the project scaffold only. The matching engine, database schema and migrations, authentication, and product workflows are later phases. The six source documents are preserved in [`docs/`](docs/); those copies are read-only. Local acceptance fixtures are synthetic and derived from the PRD examples. The `golden/` directory intentionally contains no real or customer documents.

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

### Useful commands

| Command | Purpose |
|---|---|
| `make up` | Build and start PostgreSQL, MinIO, and the API scaffold |
| `make down` | Stop local services, retaining volumes |
| `make check` | Lint and type-check Python/TypeScript; run scaffold smoke tests |
| `make test` | Run the Python and web test suites |
| `make golden` | Run the empty golden-set harness and its false-Matched self-check |
| `make openapi` | Regenerate the OpenAPI snapshot and TypeScript types |
| `make migrate` | Reserved for Phase 2; intentionally reports that migrations do not exist yet |
| `make web` | Run the static web shell on Vite's local development server |

The API exposes liveness at `/healthz`, readiness at `/readyz`, and the versioned API base at `/api/v1`. Readiness reports success only when PostgreSQL and the object store respond.
