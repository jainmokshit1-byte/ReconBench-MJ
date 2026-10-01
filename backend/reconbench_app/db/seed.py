"""Idempotent P0 bootstrap seed for the first workspace and manager account."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Engine

from reconbench_app.db.ids import new_id
from reconbench_app.db.metadata import metadata
from reconbench_app.db.seed_data import DEFAULT_CONFIG, REASON_CODES

_ARGON2ID_HASH = re.compile(
    r"^\$argon2id\$v=19\$m=\d+,t=\d+,p=\d+\$[A-Za-z0-9+/]+=*\$[A-Za-z0-9+/]+=*$"
)


@dataclass(frozen=True)
class BootstrapSettings:
    workspace_name: str
    email: str
    display_name: str
    password_hash: str

    @classmethod
    def from_environment(cls) -> BootstrapSettings:
        email = os.environ.get("RB_BOOTSTRAP_EMAIL", "").strip().lower()
        display_name = os.environ.get("RB_BOOTSTRAP_DISPLAY_NAME", "").strip()
        password_hash = os.environ.get("RB_BOOTSTRAP_PASSWORD_HASH", "").strip()
        if not email or "@" not in email:
            raise ValueError("RB_BOOTSTRAP_EMAIL must be set to the first manager's email")
        if not display_name:
            raise ValueError("RB_BOOTSTRAP_DISPLAY_NAME must be set")
        if not _ARGON2ID_HASH.fullmatch(password_hash):
            raise ValueError("RB_BOOTSTRAP_PASSWORD_HASH must be a PHC Argon2id hash")
        workspace_name = os.environ.get("RB_BOOTSTRAP_WORKSPACE_NAME", "ReconBench").strip()
        if not workspace_name:
            raise ValueError("RB_BOOTSTRAP_WORKSPACE_NAME must not be blank")
        return cls(workspace_name, email, display_name, password_hash)


def canonical_json(value: Any) -> str:
    """Canonical JSON shared by config hashing on every supported platform."""

    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def seed_database(engine: Engine, settings: BootstrapSettings) -> dict[str, int]:
    """Insert seed rows in Schema §10.1 order; repeated calls leave rows unchanged."""

    workspace = metadata.tables["workspace"]
    config_version = metadata.tables["config_version"]
    config_active = metadata.tables["config_active"]
    reason_code = metadata.tables["reason_code"]
    app_user = metadata.tables["app_user"]

    with engine.begin() as connection:
        # Seed data is application-owned data: exercise exactly the app role's
        # INSERT/SELECT grants even when the command's DSN is an admin login.
        connection.exec_driver_sql("SET LOCAL ROLE reconbench_app")
        # A single-workspace P0 bootstrap must not create a second workspace on rerun.
        connection.execute(
            sa.text(
                "SELECT pg_advisory_xact_lock(hashtextextended('reconbench.seed.bootstrap', 0))"
            )
        )
        workspace_rows = connection.execute(sa.select(workspace.c.id).limit(2)).all()
        if len(workspace_rows) > 1:
            raise RuntimeError("P0 seed expects at most one workspace; refusing an ambiguous seed")
        if workspace_rows:
            workspace_id = workspace_rows[0].id
        else:
            workspace_id = new_id()
            connection.execute(
                pg_insert(workspace)
                .values(id=workspace_id, name=settings.workspace_name)
                .on_conflict_do_nothing()
            )

        body_hash = canonical_hash(DEFAULT_CONFIG)
        engine_hash = canonical_hash(DEFAULT_CONFIG["engine"])
        config_id = new_id()
        connection.execute(
            pg_insert(config_version)
            .values(
                id=config_id,
                workspace_id=workspace_id,
                seq=1,
                parent_version_id=None,
                body=DEFAULT_CONFIG,
                engine_hash=engine_hash,
                body_hash=body_hash,
                change_note="Initial ReconBench configuration",
                created_by=None,
            )
            .on_conflict_do_nothing()
        )
        config_row = (
            connection.execute(
                sa.select(
                    config_version.c.id, config_version.c.engine_hash, config_version.c.body_hash
                ).where(config_version.c.workspace_id == workspace_id, config_version.c.seq == 1)
            )
            .mappings()
            .one_or_none()
        )
        if config_row is None:
            raise RuntimeError("config_version seq 1 was not created")
        if config_row["engine_hash"] != engine_hash or config_row["body_hash"] != body_hash:
            raise RuntimeError("existing config_version seq 1 differs from the canonical defaults")

        connection.execute(
            pg_insert(config_active)
            .values(
                id=new_id(),
                workspace_id=workspace_id,
                config_version_id=config_row["id"],
                activated_by=None,
            )
            .on_conflict_do_nothing()
        )

        for seed_row in REASON_CODES:
            connection.execute(
                pg_insert(reason_code)
                .values(id=new_id(), workspace_id=workspace_id, **seed_row)
                .on_conflict_do_nothing()
            )

        connection.execute(
            pg_insert(app_user)
            .values(
                id=new_id(),
                workspace_id=workspace_id,
                email=settings.email,
                display_name=settings.display_name,
                role="manager",
                password_hash=settings.password_hash,
            )
            .on_conflict_do_nothing()
        )

        # ai_setting is intentionally absent; the API interprets that as rules_only.
        counts = {
            "workspace": connection.scalar(
                sa.select(sa.func.count())
                .select_from(workspace)
                .where(workspace.c.id == workspace_id)
            ),
            "config_version": connection.scalar(
                sa.select(sa.func.count())
                .select_from(config_version)
                .where(
                    config_version.c.workspace_id == workspace_id,
                    config_version.c.seq == 1,
                )
            ),
            "config_active": connection.scalar(
                sa.select(sa.func.count())
                .select_from(config_active)
                .where(
                    config_active.c.workspace_id == workspace_id,
                    config_active.c.config_version_id == config_row["id"],
                )
            ),
            "reason_code": connection.scalar(
                sa.select(sa.func.count())
                .select_from(reason_code)
                .where(reason_code.c.workspace_id == workspace_id)
            ),
            "app_user": connection.scalar(
                sa.select(sa.func.count())
                .select_from(app_user)
                .where(
                    app_user.c.workspace_id == workspace_id,
                    app_user.c.email == settings.email,
                )
            ),
            "ai_setting": connection.scalar(
                sa.select(sa.func.count())
                .select_from(metadata.tables["ai_setting"])
                .where(metadata.tables["ai_setting"].c.workspace_id == workspace_id)
            ),
        }
        return {name: int(count or 0) for name, count in counts.items()}


__all__ = ["BootstrapSettings", "canonical_hash", "canonical_json", "seed_database"]
