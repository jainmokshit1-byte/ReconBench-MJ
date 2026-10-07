# Phase 1 dependency and licence review

Python runtime dependencies were re-reviewed against the refreshed lockfile on 2026-10-04; `pip-audit --skip-editable` reported no known vulnerabilities in locked third-party packages. The web lockfile review and `npm audit --audit-level=high` result are from 2026-10-01 (zero vulnerabilities). “Active” means the package resolved from its registry and its upstream project was accepting releases or maintenance at review time; it is not a support guarantee. The Python audit intentionally skips this repository's editable `reconbench-app` distribution; there is no released application package to audit yet. Re-run both audits in CI and before release.

## Python runtime

| Library | Locked version | Licence | Maintenance at review | Known advisories | Decision |
|---|---:|---|---|---|---|
| Alembic | 1.20.0 | MIT | Active | None reported by `pip-audit` | Keep |
| boto3, botocore | 1.43.106 | Apache-2.0 | Active | None reported by `pip-audit` | Keep |
| charset-normalizer | 3.5.2 | MIT | Active | None reported by `pip-audit` | Keep |
| cryptography | 50.0.2 | Apache-2.0 OR BSD-3-Clause | Active | None reported by `pip-audit` | Add as a direct dependency for the planned AI key-sealing protocol; see `docs/ai-gemini-protocol.md` |
| FastAPI | 0.142.2 | MIT | Active | None reported by `pip-audit` | Keep |
| google-genai | 2.28.0 | Apache-2.0 | Active; v2.28.0 released 2026-10-02 | None reported by `pip-audit` | Keep pinned for Gemini Interactions. The public retry setting alone is insufficient; the one-shot public HTTPX transport guard and locked-version regression tests now pass (see `docs/ai-gemini-protocol.md`). |
| lxml | 6.1.3 | BSD-3-Clause | Active | None reported by `pip-audit` | Keep |
| OpenTelemetry API, SDK, OTLP HTTP exporter | 1.45.0 | Apache-2.0 | Active | None reported by `pip-audit` | Keep; exporter remains optional at runtime |
| OCRmyPDF | 17.13.0 | MPL-2.0 | Active upstream on 2026-10-02 | Not re-audited after addition | Add for scanned-PDF text layers; no OCRmyPDF source modifications |
| pdfplumber | 0.11.10 | MIT | Active | None reported by `pip-audit` | Keep; synthetic-only baseline in SP-3 |
| Prometheus client | 0.26.0 | Apache-2.0 AND BSD-2-Clause | Active | None reported by `pip-audit` | Keep |
| Procrastinate | 3.10.0 | MIT | Active; upstream requests additional maintainers | None reported by `pip-audit` | Keep behind `JobQueue`; see SP-1 qualification |
| psycopg[binary] | 3.3.6 | LGPL-3.0-only | Active | None reported by `pip-audit` | Keep; preserve licence and bundled-binary notices in distributions |
| Pydantic, pydantic-settings | 2.13.5, 2.15.0 | MIT | Active | None reported by `pip-audit` | Keep |
| pypdfium2 | 5.13.0 | BSD-3-Clause; bundled PDFium and third-party notices also apply | Active | None reported by `pip-audit` | Keep; ship upstream notices |
| python-dotenv | 1.2.4 | BSD-3-Clause | Active | None reported by `pip-audit` | Keep |
| RapidFuzz | 3.14.6 | MIT | Active | None reported by `pip-audit` | Keep |
| SciPy | 1.18.1 | BSD-3-Clause; bundled numerical libraries have separate notices | Active | None reported by `pip-audit` | Keep; validate bundled notices in image SBOM |
| Sentry SDK | 2.71.0 | MIT | Active | None reported by `pip-audit` | Keep; disabled unless configured |
| SQLAlchemy | 2.0.54 | MIT | Active | None reported by `pip-audit` | Keep; pinned below 2.1 per TRD |
| structlog | 26.1.0 | MIT OR Apache-2.0 | Active | None reported by `pip-audit` | Keep |
| Uvicorn | 0.54.0 | BSD-3-Clause | Active | None reported by `pip-audit` | Keep |
| zxing-cpp | 3.1.1 | Apache-2.0 (bundled zxing-cpp C++ library, Apache-2.0) | Active; v3.1.1 released 2026-07-29 | Not re-audited after addition | Add (AC-2, 6 Oct 2026) to decode e-invoice QR codes from rendered PDF pages inside the PDF sandbox; prebuilt abi3 wheels for Linux x86_64/aarch64 and Windows, no system library. Alternatives rejected: pyzbar needs the system `libzbar`; OpenCV is a much larger dependency for one decoder |
| XlsxWriter | 3.2.9 | BSD-2-Clause | Active | None reported by `pip-audit` | Keep |

## Python development tools

| Library | Locked version | Licence | Maintenance at review | Known advisories | Decision |
|---|---:|---|---|---|---|
| Hypothesis | 6.168.3 | MPL-2.0 | Active | None reported by `pip-audit` | Keep; dev only |
| httpx2 | 2.13.1 | BSD-3-Clause | Active | None reported by refreshed `pip-audit` | Keep; dev only, required by Starlette's integration-test client |
| import-linter | 2.15 | BSD-2-Clause | Active | None reported by `pip-audit` | Keep; dev only |
| mypy | 2.3.1 | MIT | Active | None reported by `pip-audit` | Keep; dev only |
| pip-audit | 2.10.1 | Apache-2.0 (includes ISC-licensed reused examples) | Active | None reported by `pip-audit` | Keep; dev only |
| pytest, pytest-cov | 9.1.1, 7.1.0 | MIT | Active | None reported by `pip-audit` | Keep; dev only |
| PyYAML | 6.0.3 | MIT | Active | None reported by `pip-audit` | Keep; dev only |
| ReportLab | 5.0.1 | BSD-style licence | Active | None reported by `pip-audit` | Keep; used only to generate synthetic SP-3 PDFs |
| Ruff | 0.16.9 | MIT | Active | None reported by `pip-audit` | Keep; dev only |

## Web dependencies

| Libraries | Locked versions | Licence | Maintenance at review | Known advisories | Decision |
|---|---|---|---|---|---|
| `@fontsource-variable/inter` | 5.3.0 | OFL-1.1 | Active | None reported by npm audit | Keep; bundled locally |
| TanStack Query, Table, Virtual | 5.104.0, 9.2.4, 3.14.13 | MIT | Active | None reported by npm audit | Keep |
| `lucide-react` | 1.49.0 | ISC | Active | None reported by npm audit | Keep; bundled locally |
| `openapi-fetch` | 0.17.0 | MIT | Active | None reported by npm audit | Keep |
| `pdfjs-dist` | 6.3.289 | Apache-2.0 | Active | None reported by npm audit | Keep |
| React, React DOM | 19.3.0 | MIT | Active | None reported by npm audit | Keep |
| React Hook Form | 7.89.0 | MIT | Active | None reported by npm audit | Keep |
| React Router | 8.4.0 | MIT | Active | None reported by npm audit | Keep |
| Zod | 4.6.5 | MIT | Active | None reported by npm audit | Keep |
| `@eslint/js`, ESLint | 10.0.1, 10.11.0 | MIT | Active | None reported by npm audit | Keep; dev only |
| Tailwind Vite plugin, Tailwind CSS | 4.3.3 | MIT | Active | None reported by npm audit | Keep |
| Testing Library DOM, React, and types | 7.0.1, 16.3.3, 19.3.0 | MIT | Active | None reported by npm audit | Keep; dev only |
| React DOM types | 19.2.3 | MIT | Active | None reported by npm audit | Keep; dev only |
| Vite React plugin | 6.1.1 | MIT | Active | None reported by npm audit | Keep; dev only |
| Playwright | 1.63.0 | Apache-2.0 | Active | None reported by npm audit | Keep; dev only |
| React hooks and refresh ESLint plugins | 7.1.1, 0.5.7 | MIT | Active | None reported by npm audit | Keep; dev only |
| jsdom | 30.1.1 | MIT | Active | None reported by npm audit | Keep; dev only |
| openapi-typescript | 7.13.0 | MIT | Active | None reported by npm audit | Keep; dev only |
| TypeScript | 5.9.3 | Apache-2.0 | Active | None reported by npm audit | Keep; dev only |
| typescript-eslint | 8.71.0 | MIT | Active | None reported by npm audit | Keep; dev only |
| Vitest | 5.0.3 | MIT | Active | None reported by npm audit | Keep; dev only |

## Local infrastructure images

| Component | Version/source | Licence and maintenance | Known advisories | Decision |
|---|---|---|---|---|
| PostgreSQL | 16.15 Bookworm image | PostgreSQL licence; supported major line | Used by Compose and the Phase 2 CI service; not included in the Trivy image loop | Keep for local and CI |
| Tesseract OCR | Debian Trixie `tesseract-ocr` plus English and Hindi trained-data packages | Apache-2.0 for Tesseract; Debian language/font data carry their own notices | Not reviewed independently after addition | Install only in the backend worker image for OCR; default language is English and Hindi is opt-in |
| MinIO Community | Source-built `RELEASE.2025-10-15T17-29-55Z` | AGPL-3.0; upstream repository archived 2026-04-25, community edition is source-only and no longer maintained | Not covered by Python/npm lockfile audits; Trivy scans the built image | Keep only to satisfy local Phase 1 parity with the TRD; do not use for staging/production without an explicit, current licence and support decision (G-28) |

PyMuPDF is absent from both lockfiles. No package in Phase 1 uses SQLite. The API image uses Python 3.12 slim on Debian Trixie; the web build uses Node 24 and Nginx Alpine. Syft inventories the source and built application images in CI so operating-system and Go dependencies are included in supply-chain evidence.

## Phase 1 CI image-scan policy

CI blocks on fixable HIGH/CRITICAL findings in the API and web images. Trivy's `--ignore-unfixed` excludes findings for which the vendor has not released a fix; those findings still need review before deployment under the Phase 7 security gate. The MinIO Community image is scanned and its findings are reported, but it does not block Phase 1 CI while G-28 restricts it to local development and its upstream is archived. Before staging, replace it with a supported object store and make its scan blocking. The PostgreSQL service image is not included in the current Trivy image loop.

## Secret scanning

CI scans full Git history with the official Gitleaks CLI container pinned to v8.29.1 and its pulled OCI digest. A synthetic high-entropy probe was detected locally. The v8.30.1 release has an upstream report of default-rule false negatives, so Phase 1 pins the preceding release and requires review before upgrading ([issue #2170](https://github.com/gitleaks/gitleaks/issues/2170)).

## Review evidence and limits

- `uv run --project backend --group dev pip-audit --skip-editable` → no known vulnerabilities.
- Python audit of the updated environment on 2026-10-04: `backend/.venv/Scripts/python.exe -m pip_audit --skip-editable` → no known vulnerabilities; editable `reconbench-app` skipped.
- `npm audit --prefix web --audit-level=high` → zero vulnerabilities.
- `deploy/minio.Dockerfile` builds the last documented MinIO Community source release because the selected Docker Hub image could not be pulled. This archived source build is a local development dependency only.
- `scripts/spikes/` uses synthetic fixtures only. SP-3 does not estimate real vendor PDF accuracy, and SP-4 does not validate real Tally/CSV variants; both remain gated by G-07.
