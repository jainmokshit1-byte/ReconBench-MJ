"""4.1 Identity and access migration, sourced from Backend Schema §4.1."""

# ruff: noqa: E501 -- preserve the validated SQL source verbatim.

from __future__ import annotations

from reconbench_app.db.migration_sql import execute_sql_block

revision = "0002"
down_revision = '0001'
branch_labels = None
depends_on = None

SQL_BLOCKS = (
    # Backend Schema source line 246
    r"""CREATE TABLE workspace (
  id          UUID PRIMARY KEY,
  name        TEXT NOT NULL,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
""",

    # Backend Schema source line 257
    r"""CREATE TABLE app_user (
  id             UUID PRIMARY KEY,
  workspace_id   UUID NOT NULL REFERENCES workspace(id),
  email          TEXT NOT NULL,
  display_name   TEXT NOT NULL,
  role           user_role NOT NULL DEFAULT 'manager',
  password_hash  TEXT NOT NULL,                       -- Argon2id
  is_active      BOOLEAN NOT NULL DEFAULT true,
  failed_logins  INTEGER NOT NULL DEFAULT 0,
  locked_until   TIMESTAMPTZ,
  last_login_at  TIMESTAMPTZ,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  version        INTEGER NOT NULL DEFAULT 1,
  CONSTRAINT app_user_email_lower CHECK (email = lower(email))
);
CREATE UNIQUE INDEX app_user_email_uq ON app_user (workspace_id, email);
""",

    # Backend Schema source line 280
    r"""CREATE TABLE session (
  id               UUID PRIMARY KEY,
  workspace_id     UUID NOT NULL REFERENCES workspace(id),
  user_id          UUID NOT NULL REFERENCES app_user(id),
  token_hash       d_hash NOT NULL,                   -- SHA-256 of the cookie value
  csrf_hash        d_hash NOT NULL,                   -- SHA-256 of the X-CSRF-Token value
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  idle_expires_at  TIMESTAMPTZ NOT NULL,
  abs_expires_at   TIMESTAMPTZ NOT NULL,
  revoked_at       TIMESTAMPTZ,
  ip               INET,
  user_agent       TEXT
);
CREATE UNIQUE INDEX session_token_uq ON session (token_hash);
CREATE INDEX session_user_ix ON session (user_id) WHERE revoked_at IS NULL;
""",

    # Backend Schema source line 302
    r"""CREATE TABLE rate_limit_event (
  id            UUID PRIMARY KEY,
  workspace_id  UUID NOT NULL REFERENCES workspace(id),
  kind          TEXT NOT NULL CHECK (kind IN ('login','ai_test')),
  user_id       UUID REFERENCES app_user(id),
  email         TEXT,                                 -- as typed, lower-cased; may match no user
  ip            INET,
  succeeded     BOOLEAN NOT NULL,
  occurred_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX rate_limit_email_ix ON rate_limit_event (workspace_id, kind, email, occurred_at DESC);
CREATE INDEX rate_limit_ip_ix    ON rate_limit_event (workspace_id, kind, ip, occurred_at DESC);
""",
)


def upgrade() -> None:
    for sql in SQL_BLOCKS:
        execute_sql_block(sql)


def downgrade() -> None:
    raise NotImplementedError("ReconBench migrations are fixed-forward; redeploy the prior image")
