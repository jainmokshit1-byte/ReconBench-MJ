# Phase 2 database roles and bootstrap seed

## PostgreSQL group roles

Migration `0015` transfers the ReconBench application tables, views, enum/domain
types, audit sequence, ReconBench routines, and `alembic_version` to
`reconbench_migrator`. The role is `NOLOGIN`; the deployment's migration login
must be granted membership separately. Alembic switches to that role when it
exists. On a clean database, revisions `0001`–`0014` run first and `0015`
switches the connection after creating and assigning the role.

The migration role has `USAGE, CREATE` on `public`. Its default privileges grant
new application tables to `reconbench_app` for normal CRUD and to
`reconbench_report` for reads. New sequences grant usage and select to the app
role. A future append-only table must also be added to the explicit revoke list
and both trigger lists; default ACLs do not grant `TRUNCATE`.

`reconbench_app` and `reconbench_report` are also `NOLOGIN` group roles. The
application role cannot update, delete, or truncate the ten append-only tables.
The report role cannot read `secret`, `session`, `rate_limit_event`, or the
`app_user.password_hash` column. `reconbench_retention` remains P1; its narrow
grants are defined, but no login or retention worker is provisioned in Phase 2.

Create separate login roles through the deployment's secret-management process
and grant the required group roles. In particular, do not use the PostgreSQL
bootstrap superuser as the API login in a deployed environment.

## Bootstrap variables

Run `scripts/seed_database.py` only after Alembic reaches head. It connects with
`RB_DATABASE_URL`, then switches to `reconbench_app`. Required values are:

- `RB_BOOTSTRAP_EMAIL`: first manager email; normalized to lowercase.
- `RB_BOOTSTRAP_DISPLAY_NAME`: first manager display name.
- `RB_BOOTSTRAP_PASSWORD_HASH`: a precomputed Argon2id PHC password hash; raw
  passwords are not accepted or stored by the seed command.
- `RB_BOOTSTRAP_WORKSPACE_NAME`: optional; defaults to `ReconBench`.

The seed uses the exact default body and reason-code draft rows from Backend
Schema §§10.2–10.3. It inserts in the documented order, uses conflict-ignore
inserts, and does not create an `ai_setting`; absent AI settings mean
`rules_only`. It hashes canonical UTF-8 JSON using sorted keys and compact
separators. Re-running it against the same single-workspace database is safe.
