"""``ai.manage`` from the stored role (accuracy plan WI-PV3, decision O-18, decision-log D90).

Runs against the single seeded workspace of a disposable PostgreSQL 16 database, with the
harness of ``test_ai_settings_api``. A non-manager is refused (403) on every AI-settings
and provider write route, each refusal leaves an ``ai.manage_denied`` audit row, the read
routes stay open and the session reports no ``ai.manage`` (read-only view); the manager is
unaffected; the last active manager of a workspace cannot be demoted, deactivated or
deleted (migration 0030). No model is called; keys in this file are made up.
"""

# ruff: noqa: F811 -- the harness fixtures are imported from test_ai_settings_api

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from typing import Any
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from reconbench_app.ai.settings_service import AiSettingsError, require_ai_manage
from reconbench_app.authorization import ActorContext
from reconbench_app.db.ids import new_id
from sqlalchemy.exc import DBAPIError
from test_ai_settings_api import (  # noqa: F401  (fixtures are registered by import)
    _HASHER,
    PASSWORD,
    Harness,
    harness,
    scratch_dsn,
)

SENTINEL_KEY = "AIzaSyD-SENTINEL-pv3-denied-key-000000"
OTHER_SENTINEL = "AIzaSyD-SENTINEL-pv3-rotate-key-000000"


def _add_user(h: Harness, role: str, *, active: bool = True) -> tuple[UUID, str]:
    user_id = new_id()
    email = f"pv3-{role}-{uuid4().hex[:8]}@example.invalid"
    with h.engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO app_user (id, workspace_id, email, display_name, role, "
                "password_hash, is_active) VALUES (:id, :w, :e, :n, CAST(:r AS user_role), "
                ":h, :a)"
            ),
            {
                "id": user_id,
                "w": h.workspace_id,
                "e": email,
                "n": f"Synthetic {role}",
                "r": role,
                "h": _HASHER.hash(PASSWORD),
                "a": active,
            },
        )
    return user_id, email


def _login(h: Harness, email: str) -> dict[str, Any]:
    with h.engine.begin() as connection:
        connection.execute(
            sa.text("DELETE FROM rate_limit_event WHERE workspace_id = :w"), {"w": h.workspace_id}
        )
    response = h.client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    h.csrf = response.json()["csrf_token"]
    return dict(response.json())


def _denied_rows(h: Harness) -> list[dict[str, Any]]:
    with h.engine.connect() as connection:
        return [
            dict(row)
            for row in connection.execute(
                sa.text(
                    "SELECT actor_id, actor_role, target_type, target_id, after_state "
                    "FROM audit_log WHERE workspace_id = :w AND action = 'ai.manage_denied' "
                    "ORDER BY seq"
                ),
                {"w": h.workspace_id},
            ).mappings()
        ]


def _write_calls(h: Harness) -> list[tuple[str, Any]]:
    """Every AI-settings and provider write route, each with a well-formed body."""

    version = h.get()["version"]
    consent = {"accept": True, "fingerprint": "0" * 64}
    return [
        (
            "settings_update",
            lambda: h.client.put(
                "/api/v1/ai/settings", json={"redaction_enabled": True}, headers=h.headers(version)
            ),
        ),
        ("key_remove", lambda: h.client.delete("/api/v1/ai/key", headers=h.headers(version))),
        ("guardrail_test", lambda: h.client.post("/api/v1/ai/test", headers=h.headers())),
        (
            "provider_test",
            lambda: h.client.post(
                "/api/v1/ai/test/provider", json={"provider": "gemini"}, headers=h.headers()
            ),
        ),
        (
            "provider_connect",
            lambda: h.client.post(
                "/api/v1/ai/providers/gemini/connect",
                json={
                    "tier": "free",
                    "api_key": SENTINEL_KEY,
                    "key_storage": "saved",
                    "consent": consent,
                },
                headers=h.headers(),
            ),
        ),
        (
            "provider_rotate",
            lambda: h.client.post(
                "/api/v1/ai/providers/gemini/rotate",
                json={"api_key": OTHER_SENTINEL, "key_storage": "saved"},
                headers=h.headers(),
            ),
        ),
        (
            "provider_disconnect",
            lambda: h.client.post("/api/v1/ai/providers/gemini/disconnect", headers=h.headers()),
        ),
        (
            "request_acknowledge",
            lambda: h.client.post(
                f"/api/v1/ai/requests/{uuid4()}/acknowledge", headers=h.headers()
            ),
        ),
    ]


@pytest.mark.parametrize("role", ["ap_executive", "auditor"])
def test_a_non_manager_is_refused_on_every_ai_write_route_and_each_refusal_is_audited(
    harness: Harness, role: str
) -> None:
    before_settings = harness.get()
    before_rows = len(_denied_rows(harness))
    user_id, email = _add_user(harness, role)
    session = _login(harness, email)
    texts: list[str] = []
    calls = _write_calls(harness)
    for attempted, call in calls:
        response = call()
        texts.append(response.text)
        assert response.status_code == 403, (attempted, response.status_code, response.text)
        assert response.json()["code"] == "ai_manage_required", attempted
    rows = _denied_rows(harness)[before_rows:]
    assert [row["after_state"]["attempted"] for row in rows] == [name for name, _ in calls]
    for row in rows:
        assert row["actor_id"] == user_id and row["target_id"] == user_id
        assert row["actor_role"] == role and row["target_type"] == "user"
        assert row["after_state"]["stored_role"] == role
    # Nothing changed, the read routes stay open, and the session shows no ai.manage.
    assert "ai.manage" not in session["capabilities"]
    assert {"files.upload", "data.view"} <= set(session["capabilities"])
    after = harness.get()
    assert after["version"] == before_settings["version"]
    with harness.engine.connect() as connection:
        haystack = list(texts)
        for table in ("audit_log", "ai_request", "ai_setting", "ai_provider_config", "secret"):
            for row in connection.execute(
                sa.text(f"SELECT * FROM {table} WHERE workspace_id = :w"),
                {"w": harness.workspace_id},
            ).mappings():
                haystack.append(json.dumps(dict(row), default=str))
    assert all(SENTINEL_KEY not in text and OTHER_SENTINEL not in text for text in haystack)


def test_the_manager_passes_the_check_and_sees_ai_manage(harness: Harness) -> None:
    before_rows = len(_denied_rows(harness))
    session = _login(harness, harness.email)
    assert "ai.manage" in session["capabilities"]
    for attempted, call in _write_calls(harness):
        response = call()
        assert response.status_code != 403 or response.json().get("code") != "ai_manage_required", (
            attempted,
            response.text,
        )
    assert len(_denied_rows(harness)) == before_rows


def test_an_inactive_manager_holds_no_ai_manage(harness: Harness) -> None:
    user_id, _ = _add_user(harness, "manager", active=False)
    actor = ActorContext(workspace_id=harness.workspace_id, actor_id=user_id, actor_role="manager")
    with pytest.raises(AiSettingsError) as refused:
        require_ai_manage(harness.engine, actor, attempted="settings_update")
    assert refused.value.status == 403 and refused.value.code == "ai_manage_required"
    assert _denied_rows(harness)[-1]["after_state"] == {
        "attempted": "settings_update",
        "stored_role": "manager",
        "is_active": False,
    }
    # The seeded (bootstrap) manager passes.
    require_ai_manage(
        harness.engine,
        ActorContext(
            workspace_id=harness.workspace_id, actor_id=harness.user_id, actor_role="manager"
        ),
        attempted="settings_update",
    )


# ---- last-manager lock (migration 0030) ---------------------------------------------------


def _active_managers(h: Harness) -> list[UUID]:
    with h.engine.connect() as connection:
        return list(
            connection.scalars(
                sa.text(
                    "SELECT id FROM app_user WHERE workspace_id = :w AND role = 'manager' "
                    "AND is_active ORDER BY created_at, id"
                ),
                {"w": h.workspace_id},
            )
        )


@pytest.fixture
def single_manager(harness: Harness) -> Iterator[Harness]:
    """Only the bootstrap user stays an active manager; restored afterwards."""

    others = [item for item in _active_managers(harness) if item != harness.user_id]
    with harness.engine.begin() as connection:
        for user_id in others:
            connection.execute(
                sa.text("UPDATE app_user SET is_active = false WHERE id = :id"), {"id": user_id}
            )
    try:
        yield harness
    finally:
        with harness.engine.begin() as connection:
            for user_id in others:
                connection.execute(
                    sa.text("UPDATE app_user SET is_active = true WHERE id = :id"), {"id": user_id}
                )


def _refused(h: Harness, statement: str, params: dict[str, Any]) -> None:
    with pytest.raises(DBAPIError) as raised, h.engine.begin() as connection:
        connection.execute(sa.text(statement), params)
    assert "last_manager" in str(raised.value.orig)
    assert getattr(raised.value.orig, "sqlstate", None) == "23514"


def test_the_last_active_manager_cannot_be_demoted_deactivated_or_deleted(
    single_manager: Harness,
) -> None:
    h = single_manager
    me = {"id": h.user_id}
    _refused(h, "UPDATE app_user SET role = 'ap_executive' WHERE id = :id", me)
    _refused(h, "UPDATE app_user SET is_active = false WHERE id = :id", me)
    assert _active_managers(h) == [h.user_id]
    # Changes that keep the manager active and a manager pass (login bookkeeping, names).
    with h.engine.begin() as connection:
        connection.execute(
            sa.text(
                "UPDATE app_user SET display_name = display_name, is_active = true WHERE id = :id"
            ),
            me,
        )


def test_a_second_manager_can_be_demoted_but_not_both_and_not_in_one_statement(
    single_manager: Harness,
) -> None:
    h = single_manager
    second, _ = _add_user(h, "manager")
    with h.engine.begin() as connection:
        connection.execute(
            sa.text("UPDATE app_user SET role = 'ap_executive' WHERE id = :id"), {"id": second}
        )
        connection.execute(
            sa.text("UPDATE app_user SET role = 'manager' WHERE id = :id"), {"id": second}
        )
    _refused(
        h,
        "UPDATE app_user SET role = 'auditor' WHERE workspace_id = :w AND role = 'manager'",
        {"w": h.workspace_id},
    )
    with h.engine.begin() as connection:
        connection.execute(
            sa.text("UPDATE app_user SET role = 'ap_executive' WHERE id = :id"), {"id": second}
        )
    _refused(h, "UPDATE app_user SET role = 'ap_executive' WHERE id = :id", {"id": h.user_id})
    assert _active_managers(h) == [h.user_id]


def test_two_concurrent_demotions_cannot_both_pass(single_manager: Harness) -> None:
    h = single_manager
    second, _ = _add_user(h, "manager")
    managers = (h.user_id, second)
    start = threading.Barrier(2)
    errors: dict[UUID, BaseException | None] = {}

    def demote(user_id: UUID) -> None:
        try:
            with h.engine.begin() as connection:
                connection.execute(
                    sa.text("UPDATE app_user SET role = 'ap_executive' WHERE id = :id"),
                    {"id": user_id},
                )
                start.wait(timeout=10)  # both updates are done before either commits
            errors[user_id] = None
        except BaseException as exc:  # noqa: BLE001 -- the assertion below names it
            errors[user_id] = exc

    workers = [threading.Thread(target=demote, args=(user_id,)) for user_id in managers]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=30)
        assert not worker.is_alive()
    failed = [user_id for user_id, error in errors.items() if error is not None]
    assert len(failed) == 1, errors
    error = errors[failed[0]]
    assert isinstance(error, DBAPIError) and "last_manager" in str(error.orig)
    assert _active_managers(h) == failed
    with h.engine.begin() as connection:
        connection.execute(
            sa.text("UPDATE app_user SET role = 'manager' WHERE id = :id"), {"id": h.user_id}
        )
        connection.execute(
            sa.text("UPDATE app_user SET role = 'ap_executive' WHERE id = :id"), {"id": second}
        )
    assert _active_managers(h) == [h.user_id]


def test_managers_can_be_swapped_in_one_transaction(single_manager: Harness) -> None:
    h = single_manager
    second, _ = _add_user(h, "ap_executive")
    with h.engine.begin() as connection:
        connection.execute(
            sa.text("UPDATE app_user SET role = 'ap_executive' WHERE id = :id"), {"id": h.user_id}
        )
        connection.execute(
            sa.text("UPDATE app_user SET role = 'manager' WHERE id = :id"), {"id": second}
        )
    assert _active_managers(h) == [second]
    with h.engine.begin() as connection:
        connection.execute(
            sa.text("UPDATE app_user SET role = 'manager' WHERE id = :id"), {"id": h.user_id}
        )
        connection.execute(
            sa.text("UPDATE app_user SET role = 'ap_executive' WHERE id = :id"), {"id": second}
        )


def test_a_workspace_that_never_had_a_manager_is_not_blocked(harness: Harness) -> None:
    workspace_id, user_id = new_id(), new_id()
    with harness.engine.begin() as connection:
        connection.execute(
            sa.text("INSERT INTO workspace (id, name) VALUES (:id, 'Synthetic PV3 no manager')"),
            {"id": workspace_id},
        )
        connection.execute(
            sa.text(
                "INSERT INTO app_user (id, workspace_id, email, display_name, role, "
                "password_hash) VALUES (:id, :w, :e, 'Synthetic clerk', 'ap_executive', 'x')"
            ),
            {"id": user_id, "w": workspace_id, "e": f"pv3-clerk-{uuid4().hex[:8]}@example.invalid"},
        )
    try:
        with harness.engine.begin() as connection:
            connection.execute(
                sa.text("UPDATE app_user SET role = 'auditor', is_active = false WHERE id = :id"),
                {"id": user_id},
            )
    finally:
        with harness.engine.begin() as connection:
            connection.execute(sa.text("DELETE FROM app_user WHERE id = :id"), {"id": user_id})
            connection.execute(
                sa.text("DELETE FROM workspace WHERE id = :id"), {"id": workspace_id}
            )


def test_deleting_the_only_manager_of_a_workspace_is_refused(harness: Harness) -> None:
    # A separate workspace whose single manager has no sessions or audit rows, so the
    # foreign keys do not fire first.
    workspace_id, user_id = new_id(), new_id()
    with harness.engine.begin() as connection:
        connection.execute(
            sa.text("INSERT INTO workspace (id, name) VALUES (:id, 'Synthetic PV3 delete')"),
            {"id": workspace_id},
        )
        connection.execute(
            sa.text(
                "INSERT INTO app_user (id, workspace_id, email, display_name, role, "
                "password_hash) VALUES (:id, :w, :e, 'Synthetic owner', 'manager', 'x')"
            ),
            {"id": user_id, "w": workspace_id, "e": f"pv3-owner-{uuid4().hex[:8]}@example.invalid"},
        )
    _refused(harness, "DELETE FROM app_user WHERE id = :id", {"id": user_id})
    # Removing the whole workspace (its users, then the workspace) in one transaction passes.
    with harness.engine.begin() as connection:
        connection.execute(sa.text("DELETE FROM app_user WHERE id = :id"), {"id": user_id})
        connection.execute(sa.text("DELETE FROM workspace WHERE id = :id"), {"id": workspace_id})
