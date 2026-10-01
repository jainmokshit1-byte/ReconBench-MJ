# Schema DDL validation and errata

**Date:** 2026-10-01
**Task:** Phase 2, Task 2.1 (G-22)
**Source:** `docs/ReconBench_Backend_Schema.md`, §§3–8
**Database:** Fresh disposable `postgres:16.15-bookworm`; PostgreSQL 16.15; database `reconbench_schema_scratch`

## Validation result

The schema-definition and privilege SQL blocks from §§3–8 were executed in source order against a blank database using `psql -X -v ON_ERROR_STOP=1`. The final run exited successfully. The catalog contains 41 public base tables, 4 views, and 37 public functions; the `pg_trgm` extension and all 4 declared `reconbench_*` roles were created. The scratch instance was isolated from the Phase 1 Compose database and discarded after validation. No real sample files were opened or used.

## Errata

| Location | Finding | Fix / disposition |
|---|---|---|
| Schema §§3–8 | No PostgreSQL DDL or privilege-block errors were found in the final ordered run. | No source DDL correction was needed. |
| Schema §4.8, line 1261; §4.11, line 1524 | These fenced SQL blocks begin with parameterized runtime `INSERT` statements (`$1`, `$2`, etc.), not schema DDL. Passing them to `psql` as standalone DDL produced “there is no parameter $1”. | Excluded these two application DML examples from the DDL run. They are not schema defects and remain unchanged. |

No migrations, seed scripts, or application behavior were started as part of Task 2.1. The user-confirmed product defaults in the Implementation Plan remain unchanged.
