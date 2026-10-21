"""AI settings, consent, key and status APIs over HTTP (Phase 7).

Runs against the single seeded workspace of a disposable PostgreSQL 16 database.
Provider calls go through the offline ``ScriptedProvider`` only.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import boto3
import httpx
import pytest
import sqlalchemy as sa
from argon2 import PasswordHasher
from fastapi import FastAPI
from fastapi.testclient import TestClient
from reconbench_app.ai import consent as ai_consent
from reconbench_app.ai.gate import DeploymentGate
from reconbench_app.ai.limits import AiLimits, LimitsState
from reconbench_app.ai.provider import ProviderFailure
from reconbench_app.ai.stub import ScriptedProvider
from reconbench_app.ai.worker.handlers import default_handlers
from reconbench_app.ai.worker.key_open import Keyring
from reconbench_app.ai.worker.keytool import KEYRING_NAME, PUBLIC_KEY_NAME, generate
from reconbench_app.ai.worker.openai_compat import OpenAICompatProvider
from reconbench_app.ai.worker.runner import AiRequestRunner
from reconbench_app.ai.worker.sweeps import provider_health
from reconbench_app.api.application import create_app
from reconbench_app.authorization import (
    _iter_routes,
    authorization_declaration,
    capabilities_for_role,
)
from reconbench_app.db.metadata import metadata
from reconbench_app.settings import Settings
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import IntegrityError

SENTINEL_KEY = "AIzaSyD-SENTINEL-api-test-key-0000000000"
PASSWORD = "Synthetic AI settings password! 123"
MODEL = "gemini-3.8-flash"
_HASHER = PasswordHasher(time_cost=1, memory_cost=8192, parallelism=1)


@dataclass
class Harness:
    app: FastAPI
    client: TestClient
    engine: Engine
    workspace_id: UUID
    user_id: UUID
    email: str
    keyring: Keyring
    csrf: str = ""

    def login(self) -> None:
        response = self.client.post(
            "/api/v1/auth/login", json={"email": self.email, "password": PASSWORD}
        )
        assert response.status_code == 200, response.text
        self.csrf = response.json()["csrf_token"]

    def headers(self, version: int | None = None) -> dict[str, str]:
        headers = {"X-CSRF-Token": self.csrf}
        if version is not None:
            headers["If-Match"] = f'"{version}"'
        return headers

    def get(self) -> dict[str, Any]:
        response = self.client.get("/api/v1/ai/settings")
        assert response.status_code == 200, response.text
        assert response.headers["etag"] == f'"{response.json()["version"]}"'
        assert response.headers["cache-control"] == "no-store"
        return dict(response.json())

    def put(self, body: dict[str, Any], *, version: int | None = None) -> Any:
        current = version if version is not None else self.get()["version"]
        return self.client.put("/api/v1/ai/settings", json=body, headers=self.headers(current))

    def gate(self, mode: str) -> None:
        service = self.app.state.ai_settings_service
        service.config = replace(service.config, gate=DeploymentGate.from_values(mode, "CMP-1"))

    def runner(self, provider: ScriptedProvider, keyring: Keyring | None = None) -> AiRequestRunner:
        provider.name = "gemini"
        return AiRequestRunner(
            engine=self.engine,
            keyring=keyring or self.keyring,
            gate=DeploymentGate.from_values("synthetic_only", None),
            limits=lambda: LimitsState(AiLimits()),
            prices=lambda: None,
            providers={provider.name: provider},
            handlers=default_handlers(),
        )

    def heartbeat(self, seconds_ago: int = 0) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                sa.text(
                    "INSERT INTO ai_worker_heartbeat (worker_role, instance, last_seen_at) "
                    "VALUES ('ai', 'test', now() - make_interval(secs => :ago)) "
                    "ON CONFLICT (worker_role) DO UPDATE SET last_seen_at = EXCLUDED.last_seen_at"
                ),
                {"ago": seconds_ago},
            )


def _reset(engine: Engine) -> tuple[UUID, UUID, str]:
    with engine.begin() as connection:
        workspaces = connection.scalars(sa.text("SELECT id FROM workspace")).all()
        if len(workspaces) != 1:
            raise RuntimeError("AI API tests need the single seeded P0 workspace")
        workspace_id = workspaces[0]
        user = connection.execute(
            sa.text(
                "SELECT id, email FROM app_user WHERE workspace_id = :w ORDER BY created_at LIMIT 1"
            ),
            {"w": workspace_id},
        ).one()
        for table in (
            "ai_call_log",
            "ai_request",
            "ai_provider_config",
            "ai_setting",
            "secret",
            "session",
        ):
            connection.execute(
                sa.text(f"DELETE FROM {table} WHERE workspace_id = :w"), {"w": workspace_id}
            )
        connection.execute(
            sa.text("DELETE FROM rate_limit_event WHERE workspace_id = :w"), {"w": workspace_id}
        )
        connection.execute(sa.text("DELETE FROM ai_worker_heartbeat"))
        connection.execute(
            sa.text(
                "UPDATE app_user SET password_hash = :hash, failed_logins = 0, "
                "locked_until = NULL, is_active = true WHERE id = :id"
            ),
            {"hash": _HASHER.hash(PASSWORD), "id": user.id},
        )
    return UUID(str(workspace_id)), UUID(str(user.id)), str(user.email)


@pytest.fixture(scope="module")
def scratch_dsn() -> Iterator[str]:
    """Clone the disposable database and keep only its first workspace.

    P0 login requires exactly one workspace, while earlier suites leave extra
    synthetic tenants behind; the clone leaves their data untouched.
    """

    source = os.environ.get("RB_PHASE2_TEST_DATABASE_URL", "").strip()
    if not source:
        pytest.skip("RB_PHASE2_TEST_DATABASE_URL is required for disposable PostgreSQL tests")
    source_url = make_url(source)
    source_name = source_url.database or ""
    if not source_name.startswith("reconbench_phase2_"):
        raise RuntimeError("AI API tests require a disposable reconbench_phase2_* database")
    scratch_name = f"reconbench_phase2_aiapi_{uuid4().hex[:12]}"
    admin = sa.create_engine(source_url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            active = connection.scalar(
                sa.text("SELECT count(*) FROM pg_stat_activity WHERE datname = :name"),
                {"name": source_name},
            )
            if active:
                raise RuntimeError(
                    "the disposable source database has active sessions; run this file alone"
                )
            connection.exec_driver_sql(f'CREATE DATABASE "{scratch_name}" TEMPLATE "{source_name}"')
        scratch = source_url.set(database=scratch_name).render_as_string(hide_password=False)
        engine = sa.create_engine(scratch)
        try:
            with engine.begin() as connection:
                keep = connection.scalar(
                    sa.text("SELECT id FROM workspace ORDER BY created_at, id LIMIT 1")
                )
                connection.exec_driver_sql("SET LOCAL session_replication_role = replica")
                for table in metadata.sorted_tables:
                    if "workspace_id" in table.c:
                        connection.execute(sa.delete(table).where(table.c.workspace_id != keep))
                connection.execute(sa.text("DELETE FROM workspace WHERE id <> :k"), {"k": keep})
        finally:
            engine.dispose()
        yield scratch
    finally:
        with admin.connect() as connection:
            connection.exec_driver_sql(f'DROP DATABASE IF EXISTS "{scratch_name}" WITH (FORCE)')
        admin.dispose()


@pytest.fixture
def harness(scratch_dsn: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Harness]:
    dsn = scratch_dsn
    engine = sa.create_engine(dsn)
    workspace_id, user_id, email = _reset(engine)
    generate(tmp_path, "k1")

    class FakeObjectStore:
        def list_buckets(self) -> dict[str, list[object]]:
            return {"Buckets": []}

    monkeypatch.setattr(boto3, "client", lambda *args, **kwargs: FakeObjectStore())
    settings = Settings(
        _env_file=None,
        environment="ci",
        database_url=dsn,
        s3_endpoint_url="http://127.0.0.1:1",
        s3_region="us-east-1",
        s3_bucket="synthetic-test",
        s3_api_access_key="synthetic-access",
        s3_api_secret_key="synthetic-secret",
        public_base_url="https://example.invalid",
        argon2_time_cost=1,
        argon2_memory_cost_kib=8192,
        argon2_parallelism=1,
        ai_seal_public_key_file=tmp_path / PUBLIC_KEY_NAME,
        ai_external_calls="enabled",
        ai_external_calls_approval_ref="CMP-TEST-1",
    )
    app = create_app(settings)
    with TestClient(app, base_url="https://testserver", client=("198.51.100.9", 1)) as client:
        harness = Harness(
            app,
            client,
            engine,
            workspace_id,
            user_id,
            email,
            Keyring.load(tmp_path / KEYRING_NAME),
        )
        harness.login()
        yield harness
    _reset(engine)
    engine.dispose()


def _consent(h: Harness, mode: str = "ai_assisted", model: str = MODEL) -> dict[str, Any]:
    return {
        "accept": True,
        "fingerprint": ai_consent.consent_fingerprint(provider="gemini", model=model, mode=mode),
    }


def _enable(h: Harness, *, storage: str = "saved") -> dict[str, Any]:
    response = h.put(
        {
            "mode": "ai_assisted",
            "provider": "gemini",
            "model": MODEL,
            "api_key": SENTINEL_KEY,
            "key_storage": storage,
            "consent": _consent(h),
        }
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


def _status(h: Harness) -> str:
    return str(h.get()["status"]["code"])


def test_defaults_are_off_and_writes_need_if_match(harness: Harness) -> None:
    view = harness.get()
    assert view["version"] == 1 and view["mode"] == "rules_only"
    assert view["status"] == {"code": "OFF", "label": "Off: rules only"}
    assert view["key"]["set"] is False and view["auto_accept_ai_links"] is False
    missing = harness.client.put(
        "/api/v1/ai/settings", json={"model": MODEL}, headers=harness.headers()
    )
    assert missing.status_code == 428
    stale = harness.put({"model": MODEL}, version=7)
    assert stale.status_code == 412 and stale.json()["code"] == "version_conflict"


def test_key_is_sealed_write_only_and_never_echoed(harness: Harness) -> None:
    view = _enable(harness)
    assert view["key"] == {
        "set": True,
        "hint": "****" + SENTINEL_KEY[-4:],
        "storage": "saved",
        "expires_at": None,
    }
    assert view["version"] == 2 and view["consent"]["current"] is True
    bodies = [json.dumps(view), json.dumps(harness.get())]
    with harness.engine.connect() as connection:
        for table in ("secret", "ai_setting", "audit_log", "ai_request"):
            rows = connection.execute(
                sa.text(f"SELECT * FROM {table} WHERE workspace_id = :w"),
                {"w": harness.workspace_id},
            ).all()
            bodies.extend(repr(tuple(row)) for row in rows)
        bodies.extend(
            str(args) for args in connection.scalars(sa.text("SELECT args FROM procrastinate_jobs"))
        )
    for body in bodies:
        assert SENTINEL_KEY not in body
        assert SENTINEL_KEY.encode() not in body.encode()


@pytest.mark.parametrize(
    "body",
    [
        {"model": SENTINEL_KEY},
        {"model": "gemini-3.8-flash", "api_key": SENTINEL_KEY + " !"},
        {"api_key": "gemini-3.8-flash-looks-like-a-model"},
        {"api_key": SENTINEL_KEY, "unknown_field": SENTINEL_KEY},
    ],
)
def test_invalid_bodies_never_echo_the_key(harness: Harness, body: dict[str, Any]) -> None:
    response = harness.put(body)
    assert response.status_code == 422
    assert SENTINEL_KEY not in response.text
    assert harness.get()["key"]["set"] is False


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ({"auto_accept_ai_links": True}, "auto_accept_not_available"),
        ({"redaction_enabled": False}, "redaction_required"),
        ({"model": MODEL, "budget_cap_usd": "5"}, "usd_cap_not_available"),
        ({"mode": "ai_assisted", "provider": "gemini", "model": MODEL}, "consent_required"),
    ],
)
def test_unsupported_or_unconsented_settings_are_rejected(
    harness: Harness, body: dict[str, Any], code: str
) -> None:
    response = harness.put(body)
    assert response.status_code == 422
    assert response.json()["code"] == code


def test_consent_follows_the_fingerprint_at_service_and_database_level(
    harness: Harness,
) -> None:
    with harness.engine.connect() as connection:
        first_seq = int(connection.scalar(sa.text("SELECT coalesce(max(seq), 0) FROM audit_log")))
    wrong = harness.put(
        {
            "mode": "ai_assisted",
            "provider": "gemini",
            "model": MODEL,
            "consent": _consent(harness, mode="ai_first"),
        }
    )
    assert wrong.status_code == 409 and wrong.json()["code"] == "consent_mismatch"
    _enable(harness)
    changed = harness.put({"model": "gemini-3.8-pro"})
    assert changed.status_code == 422 and changed.json()["code"] == "consent_required"
    switched = harness.put(
        {"model": "gemini-3.8-pro", "consent": _consent(harness, model="gemini-3.8-pro")}
    )
    assert switched.status_code == 200 and switched.json()["consent"]["current"] is True
    mode_change = harness.put({"mode": "ai_first"})
    assert mode_change.status_code == 422
    assisted_fingerprint = switched.json()["consent"]["required_fingerprint"]
    ai_first = harness.put(
        {
            "mode": "ai_first",
            "consent": _consent(harness, mode="ai_first", model="gemini-3.8-pro"),
        }
    )
    assert ai_first.status_code == 200, ai_first.text
    body = ai_first.json()
    assert body["consent"]["current"] is True
    assert body["consent"]["required_fingerprint"] != assisted_fingerprint
    assert body["mode_label"] == ai_consent.MODE_LABELS["ai_first"]
    assert [(item["hook"], item["label"]) for item in body["consent"]["hooks"]] == [
        (hook, ai_consent.HOOK_LABELS[hook]) for hook in ai_consent.HOOKS_BY_MODE["ai_first"]
    ]
    back = harness.put({"mode": "ai_assisted"})
    assert back.status_code == 422 and back.json()["code"] == "consent_required"
    withdrawn = harness.put({"withdraw_consent": True})
    assert withdrawn.status_code == 200
    assert withdrawn.json()["mode"] == "rules_only"
    assert withdrawn.json()["consent"]["given"] is False
    with harness.engine.begin() as connection, pytest.raises(IntegrityError):
        connection.execute(
            sa.text(
                "UPDATE ai_setting SET mode = 'ai_assisted', consent_given = true "
                "WHERE workspace_id = :w"
            ),
            {"w": harness.workspace_id},
        )
    with harness.engine.connect() as connection:
        actions = connection.scalars(
            sa.text(
                "SELECT action FROM audit_log WHERE workspace_id = :w AND action LIKE 'ai.%' "
                "AND seq > :first ORDER BY seq"
            ),
            {"w": harness.workspace_id, "first": first_seq},
        ).all()
    assert actions.count("ai.consent_change") == 4


def test_consent_preview_lets_the_card_turn_ai_on_from_rules_only(harness: Harness) -> None:
    before = harness.get()
    assert before["mode"] == "rules_only" and before["consent"]["hooks"] == []
    response = harness.client.get(
        "/api/v1/ai/consent-preview", params={"mode": "ai_assisted", "model": MODEL}
    )
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    preview = response.json()
    assert preview["fingerprint"] == _consent(harness)["fingerprint"]
    assert preview["mode_label"] == ai_consent.MODE_LABELS["ai_assisted"]
    assert MODEL in preview["text"]
    assert [(item["hook"], item["data_sent"]) for item in preview["hooks"]] == [
        (hook, ai_consent.DATA_SENT[hook]) for hook in ai_consent.HOOKS_BY_MODE["ai_assisted"]
    ]
    assert harness.get()["version"] == before["version"]
    saved = harness.put(
        {
            "mode": "ai_assisted",
            "provider": "gemini",
            "model": MODEL,
            "api_key": SENTINEL_KEY,
            "key_storage": "session",
            "consent": {"accept": True, "fingerprint": preview["fingerprint"]},
        }
    )
    assert saved.status_code == 200 and saved.json()["consent"]["current"] is True
    for params in (
        {"mode": "rules_only", "model": MODEL},
        {"mode": "ai_assisted", "model": SENTINEL_KEY},
        {"mode": "ai_assisted"},
    ):
        bad = harness.client.get("/api/v1/ai/consent-preview", params=params)
        assert bad.status_code == 422
        assert SENTINEL_KEY not in bad.text


def test_auto_verify_field_selection_is_opt_in_and_reconsented(harness: Harness) -> None:
    _enable(harness)
    current = harness.get()
    assert current["auto_verify"]["field_types"] == []
    assert [item["type"] for item in current["auto_verify"]["available_field_types"]] == [
        "doc_date",
        "doc_no",
        "gstin",
        "line_amount",
        "reference",
    ]
    params = [
        ("mode", "ai_assisted"),
        ("model", MODEL),
        ("auto_verify_field_types", "doc_no"),
        ("auto_verify_field_types", "gstin"),
    ]
    preview_response = harness.client.get("/api/v1/ai/consent-preview", params=params)
    assert preview_response.status_code == 200, preview_response.text
    preview = preview_response.json()
    assert preview["text_version"] == "consent.v2"
    assert preview["auto_verify_field_types"] == ["doc_no", "gstin"]
    assert "document number" in preview["text"] and "GSTIN" in preview["text"]

    missing_consent = harness.put({"auto_verify_field_types": ["doc_no", "gstin"]})
    assert missing_consent.status_code == 422
    assert missing_consent.json()["code"] == "consent_required"
    saved = harness.put(
        {
            "auto_verify_field_types": ["doc_no", "gstin"],
            "consent": {"accept": True, "fingerprint": preview["fingerprint"]},
        }
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["auto_verify"]["field_types"] == ["doc_no", "gstin"]
    assert saved.json()["consent"]["current"] is True
    assert saved.json()["consent"]["text_version"] == "consent.v2"

    changed_again = harness.put({"auto_verify_field_types": ["doc_no"]})
    assert changed_again.status_code == 422
    assert changed_again.json()["code"] == "consent_required"
    invalid = harness.put({"auto_verify_field_types": ["amount"]})
    assert invalid.status_code == 422


def test_status_ladder_never_says_connected(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: list[str] = []
    harness.gate("disabled")
    _enable(harness)
    seen.append(_status(harness))
    harness.gate("synthetic_only")
    seen.append(_status(harness))
    harness.gate("enabled")
    seen.append(_status(harness))  # no heartbeat yet
    harness.heartbeat(seconds_ago=120)
    seen.append(_status(harness))
    harness.heartbeat()
    seen.append(_status(harness))

    test = harness.client.post("/api/v1/ai/test/provider", headers=harness.headers())
    assert test.status_code == 202, test.text
    request_id = UUID(test.json()["request_id"])
    with harness.engine.connect() as connection:
        args = connection.scalar(
            sa.text("SELECT args FROM procrastinate_jobs WHERE queue_name = 'ai' ORDER BY id DESC")
        )
    assert args == {"ai_request_id": str(request_id)}
    assert harness.runner(ScriptedProvider(['{"status": "ok"}'])).run(request_id).state == (
        "succeeded"
    )
    passed = harness.get()["status"]
    assert passed["code"] == "TEST_PASSED" and passed["at"]
    seen.append(passed["code"])

    harness.put({"key_storage": "saved"})
    seen.append(_status(harness))

    failing = harness.client.post("/api/v1/ai/test/provider", headers=harness.headers())
    harness.runner(ScriptedProvider([ProviderFailure("bad_key", delivery="responded")])).run(
        UUID(failing.json()["request_id"])
    )
    error = harness.get()["status"]
    assert error["code"] == "PROVIDER_ERROR" and error["label"] == (
        "Provider error: the key was rejected"
    )
    seen.append(error["code"])

    generate(tmp_path / "other", "k1")
    unreadable = harness.client.post("/api/v1/ai/test/provider", headers=harness.headers())
    harness.runner(
        ScriptedProvider(['{"status": "ok"}']), Keyring.load(tmp_path / "other" / KEYRING_NAME)
    ).run(UUID(unreadable.json()["request_id"]))
    seen.append(_status(harness))

    service = harness.app.state.ai_settings_service
    service.config = replace(service.config, limits_file=tmp_path / "missing-limits.json")
    harness.put({"api_key": SENTINEL_KEY})
    seen.append(_status(harness))
    service.config = replace(service.config, limits_file=None)

    monkeypatch.setattr(ai_consent, "CONSENT_TEXT_VERSION", "consent.v2")
    seen.append(_status(harness))
    monkeypatch.undo()

    removed = harness.client.delete(
        "/api/v1/ai/key", headers=harness.headers(harness.get()["version"])
    )
    assert removed.status_code == 200
    status = harness.get()["status"]
    assert status["by"] and status["at"]
    seen.append(status["code"])

    assert seen == [
        "EXTERNAL_CALLS_DISABLED",
        "EXTERNAL_CALLS_SYNTHETIC_ONLY",
        "AI_WORKER_UNAVAILABLE",
        "AI_WORKER_UNAVAILABLE",
        "NOT_TESTED",
        "TEST_PASSED",
        "NOT_TESTED",
        "PROVIDER_ERROR",
        "KEY_UNREADABLE",
        "LIMITS_NOT_CONFIGURED",
        "CONSENT_OUTDATED",
        "KEY_REMOVED",
    ]
    assert all("connect" not in code.lower() for code in seen)


def _secret_count(h: Harness) -> int:
    with h.engine.connect() as connection:
        return int(
            connection.scalar(
                sa.text("SELECT count(*) FROM secret WHERE workspace_id = :w"),
                {"w": h.workspace_id},
            )
        )


def test_session_key_is_deleted_on_logout_and_saved_key_survives(harness: Harness) -> None:
    harness.heartbeat()
    view = _enable(harness, storage="session")
    assert view["key"]["storage"] == "session" and view["key"]["expires_at"]
    assert harness.client.post("/api/v1/auth/logout", headers=harness.headers()).status_code == 204
    assert _secret_count(harness) == 0
    harness.login()
    assert harness.get()["status"]["code"] == "KEY_EXPIRED"

    saved = harness.put({"api_key": SENTINEL_KEY, "key_storage": "saved"})
    assert saved.status_code == 200
    assert harness.client.post("/api/v1/auth/logout", headers=harness.headers()).status_code == 204
    assert _secret_count(harness) == 1
    harness.login()
    assert harness.get()["key"]["set"] is True


@pytest.mark.parametrize("ending", ["relogin", "idle", "absolute"])
def test_relogin_and_expiry_make_a_session_key_unusable(harness: Harness, ending: str) -> None:
    _enable(harness, storage="session")
    assert _secret_count(harness) == 1
    if ending == "relogin":
        harness.login()
    else:
        column = "idle_expires_at" if ending == "idle" else "abs_expires_at"
        with harness.engine.begin() as connection:
            connection.execute(
                sa.text(
                    f"UPDATE session SET {column} = now() - interval '1 minute' "
                    "WHERE workspace_id = :w AND revoked_at IS NULL"
                ),
                {"w": harness.workspace_id},
            )
        assert harness.client.get("/api/v1/auth/me").status_code == 401
    assert _secret_count(harness) == 0


def test_uncertain_requests_are_acknowledged_once_and_audited(harness: Harness) -> None:
    harness.heartbeat()
    _enable(harness)
    test = harness.client.post("/api/v1/ai/test/provider", headers=harness.headers())
    request_id = UUID(test.json()["request_id"])
    harness.runner(
        ScriptedProvider([ProviderFailure("timeout", delivery="may_have_been_sent")])
    ).run(request_id)
    usage = harness.client.get("/api/v1/ai/usage").json()
    assert [row["id"] for row in usage["uncertain_requests"]] == [str(request_id)]
    assert usage["usd_available"] is False and usage["usd_today"] is None
    assert harness.client.get(f"/api/v1/ai/requests/{request_id}").json()["request_state"] == (
        "uncertain"
    )
    first = harness.client.post(
        f"/api/v1/ai/requests/{request_id}/acknowledge", headers=harness.headers()
    )
    assert first.status_code == 200
    again = harness.client.post(
        f"/api/v1/ai/requests/{request_id}/acknowledge", headers=harness.headers()
    )
    assert again.status_code == 409
    with harness.engine.connect() as connection:
        assert (
            connection.scalar(
                sa.text(
                    "SELECT count(*) FROM audit_log WHERE action = 'ai.request_acknowledged' "
                    "AND target_id = :id"
                ),
                {"id": request_id},
            )
            == 1
        )


def test_offline_self_test_is_unchanged(harness: Harness) -> None:
    response = harness.client.post("/api/v1/ai/test", headers=harness.headers())
    assert response.status_code == 200
    assert response.json()["external_provider_called"] is False


def test_ai_routes_declare_their_capabilities(harness: Harness) -> None:
    expected = {
        ("GET", "/api/v1/ai/settings"): "data.view",
        ("PUT", "/api/v1/ai/settings"): "ai.manage",
        ("DELETE", "/api/v1/ai/key"): "ai.manage",
        ("POST", "/api/v1/ai/test"): "ai.manage",
        ("POST", "/api/v1/ai/test/provider"): "ai.manage",
        ("GET", "/api/v1/ai/usage"): "ai.manage",
        ("GET", "/api/v1/ai/consent-preview"): "ai.manage",
        ("GET", "/api/v1/ai/requests/{request_id}"): "ai.manage",
        ("POST", "/api/v1/ai/requests/{request_id}/acknowledge"): "ai.manage",
    }
    found: dict[tuple[str, str], str | None] = {}
    for route, full_path in _iter_routes(harness.app.routes):
        for method in getattr(route, "methods", set()) or set():
            key = (method, full_path)
            if key in expected:
                declaration = authorization_declaration(route.endpoint)  # type: ignore[attr-defined]
                found[key] = declaration.capability if declaration else None
    assert found == expected


def test_page_images_need_their_own_consent_and_a_server_reader(harness: Harness) -> None:
    view = _enable(harness)
    assert view["vision"] == {"enabled": False, "available": True, "reader": "vision_gemini"}
    assert [item["hook"] for item in view["consent"]["hooks"]] == list(
        ai_consent.HOOKS_BY_MODE["ai_assisted"]
    )
    # Turning page images on changes what is sent, so the old consent does not cover it.
    refused = harness.put({"vision": True})
    assert refused.status_code == 422 and refused.json()["code"] == "consent_required"
    params = {"mode": "ai_assisted", "model": MODEL, "vision": "true"}
    preview = harness.client.get("/api/v1/ai/consent-preview", params=params).json()
    assert preview["vision"] is True
    assert preview["hooks"][0]["hook"] == "h6_vision:fallback"
    assert "blacked out" in preview["hooks"][0]["data_sent"]
    saved = harness.put(
        {"vision": True, "consent": {"accept": True, "fingerprint": preview["fingerprint"]}}
    )
    assert saved.status_code == 200, saved.text
    on = saved.json()
    assert on["vision"]["enabled"] is True and on["consent"]["current"] is True
    assert on["consent"]["hooks"][0]["hook"] == "h6_vision:fallback"
    # A server with page images off refuses them whatever the workspace chose.
    service = harness.app.state.ai_settings_service
    service.config = replace(service.config, vision_reader="off")
    assert harness.get()["vision"]["available"] is False
    closed = harness.client.get("/api/v1/ai/consent-preview", params=params)
    assert closed.status_code == 422 and closed.json()["code"] == "vision_unavailable"


# -- WI-BE3: separate providers (Gemini and the company vLLM server) -------------------------

VLLM_URL = "http://vllm.example.test/vllm/v1"
VLLM_MODEL = "qwen3.5:9b"
LOCAL_SENTINEL_KEY = "local-SENTINEL-vllm-key-000000"


def _vllm_gate(h: Harness, mode: str = "enabled", endpoints: str = VLLM_URL) -> None:
    service = h.app.state.ai_settings_service
    service.config = replace(
        service.config,
        gate=DeploymentGate.from_values(
            "enabled", "CMP-1", vllm_mode=mode, vllm_endpoints=endpoints
        ),
    )


def _vllm_card(**overrides: Any) -> dict[str, Any]:
    return {
        "provider": "vllm",
        "enabled": True,
        "model": VLLM_MODEL,
        "base_url": VLLM_URL,
        **overrides,
    }


def _audit(h: Harness, action: str) -> list[Any]:
    with h.engine.connect() as connection:
        return list(
            connection.execute(
                sa.text(
                    "SELECT before_state, after_state FROM audit_log "
                    "WHERE workspace_id = :w AND action = :a ORDER BY seq"
                ),
                {"w": h.workspace_id, "a": action},
            ).mappings()
        )


def _vllm_preview(h: Harness) -> dict[str, Any]:
    response = h.client.get(
        "/api/v1/ai/consent-preview",
        params={
            "mode": "ai_assisted",
            "model": MODEL,
            "reader_strategy": "single",
            "primary_reader": "vllm",
            "vllm_model": VLLM_MODEL,
            "vllm_base_url": VLLM_URL,
        },
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


def test_legacy_settings_keep_their_fingerprint_and_get_a_gemini_card(harness: Harness) -> None:
    view = _enable(harness)
    # A legacy save is unchanged: same fingerprint, Gemini card mirrors the text settings.
    assert view["consent"]["required_fingerprint"] == ai_consent.consent_fingerprint(
        provider="gemini", model=MODEL, mode="ai_assisted"
    )
    assert (view["reader_strategy"], view["primary_reader"], view["secondary_reader"]) == (
        "off",
        None,
        None,
    )
    assert view["apply_mode"] == "suggest" and view["vllm_calls"] == "disabled"
    gemini, vllm = view["providers"]
    assert (gemini["provider"], gemini["enabled"], gemini["model"]) == ("gemini", True, MODEL)
    assert gemini["key"]["set"] is True and gemini["external"] is True
    assert "Google Gemini API" in gemini["data_destination"]
    assert (vllm["provider"], vllm["enabled"], vllm["base_url"]) == ("vllm", False, None)
    # The legacy "vision" flag is single + Gemini with the legacy fingerprint.
    legacy = ai_consent.consent_fingerprint(
        provider="gemini", model=MODEL, mode="ai_assisted", vision=True
    )
    saved = harness.put({"vision": True, "consent": {"accept": True, "fingerprint": legacy}})
    assert saved.status_code == 200, saved.text
    on = saved.json()
    assert (on["reader_strategy"], on["primary_reader"]) == ("single", "gemini")
    assert on["vision"]["enabled"] is True and on["consent"]["current"] is True
    with harness.engine.connect() as connection:
        row = connection.execute(
            sa.text(
                "SELECT vision_enabled, reader_strategy, primary_reader FROM ai_setting "
                "WHERE workspace_id = :w"
            ),
            {"w": harness.workspace_id},
        ).one()
    assert tuple(row) == (True, "single", "gemini")
    change = _audit(harness, "ai.reader_strategy_change")[-1]
    assert change["after_state"] == {"reader_strategy": "single", "primary_reader": "gemini"}


@pytest.mark.parametrize(
    "base_url",
    [
        "http://169.254.169.254/latest",
        "http://user@vllm.example.test/vllm/v1",
        "http://vllm.example.test/vllm/v1?y=1",
        "http://vllm.example.test/vllm/v1/x",
        "http://vllm.example.test/vllm",
        "http://[::1]/vllm/v1",
        "https://vllm.example.test/vllm/v1",
        "http://vllm.example.test:8000/vllm/v1",
        "http://vllm.example.test/vllm/../v1",
        "file:///etc/passwd",
    ],
)
def test_company_server_url_must_match_the_allowlist_exactly(
    harness: Harness, base_url: str
) -> None:
    _vllm_gate(harness)
    response = harness.put({"providers": [_vllm_card(base_url=base_url)]})
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "endpoint_not_allowed"
    preview = harness.client.get(
        "/api/v1/ai/consent-preview",
        params={
            "mode": "ai_assisted",
            "model": MODEL,
            "reader_strategy": "single",
            "primary_reader": "vllm",
            "vllm_model": VLLM_MODEL,
            "vllm_base_url": base_url,
        },
    )
    assert preview.status_code == 422 and preview.json()["code"] == "endpoint_not_allowed"
    with harness.engine.connect() as connection:
        assert not connection.scalar(
            sa.text("SELECT count(*) FROM ai_provider_config WHERE workspace_id = :w"),
            {"w": harness.workspace_id},
        )


def test_company_server_url_is_stored_normalised_and_audited_by_origin(harness: Harness) -> None:
    _vllm_gate(harness)
    response = harness.put(
        {"providers": [_vllm_card(base_url="HTTP://VLLM.Example.TEST:80/vllm/v1/")]}
    )
    assert response.status_code == 200, response.text
    vllm = response.json()["providers"][1]
    assert (vllm["enabled"], vllm["model"], vllm["base_url"]) == (True, VLLM_MODEL, VLLM_URL)
    assert vllm["key"]["set"] is False and vllm["external"] is False
    assert VLLM_URL in vllm["data_destination"]
    saved = _audit(harness, "ai.provider_config_save")[-1]
    assert saved["before_state"] is None
    assert saved["after_state"]["base_url_origin"] == "http://vllm.example.test"
    assert "base_url" not in saved["after_state"] and saved["after_state"]["key_set"] is False
    # With no allowlist, a URL cannot be (re)submitted; other fields still save.
    _vllm_gate(harness, mode="disabled", endpoints="")
    model_only = harness.put({"providers": [{"provider": "vllm", "model": "qwen3.5:4b"}]})
    assert model_only.status_code == 200, model_only.text
    moved = harness.put({"providers": [_vllm_card()]})
    assert moved.status_code == 422 and moved.json()["code"] == "endpoint_not_allowed"


@pytest.mark.parametrize(
    "card",
    [
        {"provider": "vllm", "model": "bad model with spaces"},
        {"provider": "vllm", "model": "-leading-dash"},
        {"provider": "gemini", "base_url": VLLM_URL},
        {"provider": "gemini", "model": VLLM_MODEL},
        {"provider": "vllm", "api_key": "short"},
        {"provider": "vllm", "limits": {"max_concurrency": 9}},
        {"provider": "vllm", "limits": {"anything": 1}},
        {"provider": "claude"},
    ],
)
def test_invalid_provider_cards_are_rejected(harness: Harness, card: dict[str, Any]) -> None:
    _vllm_gate(harness)
    response = harness.put({"providers": [card]})
    assert response.status_code == 422
    assert LOCAL_SENTINEL_KEY not in response.text


def test_company_server_key_is_optional_write_only_and_removable(harness: Harness) -> None:
    _vllm_gate(harness)
    keyless = harness.put({"providers": [_vllm_card()]})
    assert keyless.status_code == 200 and keyless.json()["providers"][1]["key"]["set"] is False
    keyed = harness.put({"providers": [_vllm_card(api_key=LOCAL_SENTINEL_KEY)]})
    assert keyed.status_code == 200, keyed.text
    key = keyed.json()["providers"][1]["key"]
    assert key == {
        "set": True,
        "hint": "****" + LOCAL_SENTINEL_KEY[-4:],
        "storage": "saved",
        "expires_at": None,
    }
    bodies = [keyed.text, json.dumps(harness.get())]
    with harness.engine.connect() as connection:
        for table in ("secret", "ai_setting", "ai_provider_config", "audit_log"):
            rows = connection.execute(
                sa.text(f"SELECT * FROM {table} WHERE workspace_id = :w"),
                {"w": harness.workspace_id},
            ).all()
            bodies.extend(repr(tuple(row)) for row in rows)
    for body in bodies:
        assert LOCAL_SENTINEL_KEY not in body
    assert _secret_count(harness) == 1
    removed = harness.put({"providers": [{"provider": "vllm", "remove_key": True}]})
    assert removed.status_code == 200 and removed.json()["providers"][1]["key"]["set"] is False
    assert _secret_count(harness) == 0


def test_session_company_server_key_ends_with_the_session(harness: Harness) -> None:
    _vllm_gate(harness)
    saved = harness.put(
        {"providers": [_vllm_card(api_key=LOCAL_SENTINEL_KEY, key_storage="session")]}
    )
    assert saved.status_code == 200 and saved.json()["providers"][1]["key"]["storage"] == "session"
    version = saved.json()["version"]
    assert harness.client.post("/api/v1/auth/logout", headers=harness.headers()).status_code == 204
    assert _secret_count(harness) == 0
    harness.login()
    view = harness.get()
    assert view["providers"][1]["key"]["set"] is False and view["version"] > version


def test_switching_the_reader_to_the_company_server_needs_new_consent(harness: Harness) -> None:
    _vllm_gate(harness)
    _enable(harness)
    card = harness.put({"providers": [_vllm_card()]})
    assert card.status_code == 200 and card.json()["consent"]["current"] is True
    refused = harness.put({"reader_strategy": "single", "primary_reader": "vllm"})
    assert refused.status_code == 422 and refused.json()["code"] == "consent_required"
    preview = _vllm_preview(harness)
    assert preview["hooks"][0]["hook"] == "h6_vision:fallback"
    assert "company vLLM server" in preview["text"] and VLLM_URL in preview["text"]
    assert preview["providers"][1]["data_destination"].startswith("Company vLLM server at")
    assert preview["fingerprint"] != ai_consent.consent_fingerprint(
        provider="gemini", model=MODEL, mode="ai_assisted", vision=True
    )
    saved = harness.put(
        {
            "reader_strategy": "single",
            "primary_reader": "vllm",
            "consent": {"accept": True, "fingerprint": preview["fingerprint"]},
        }
    )
    assert saved.status_code == 200, saved.text
    on = saved.json()
    assert (on["reader_strategy"], on["primary_reader"]) == ("single", "vllm")
    assert on["consent"]["current"] is True and on["vision"]["enabled"] is True
    change = _audit(harness, "ai.reader_strategy_change")[-1]
    assert change["before_state"] == {"reader_strategy": "off", "primary_reader": None}
    # A new model on the server is a new data destination: consent is outdated again.
    moved = harness.put({"providers": [_vllm_card(model="qwen3.5:4b")]})
    assert moved.status_code == 422 and moved.json()["code"] == "consent_required"
    # Disabling the server card while it is the reader is refused.
    disabled = harness.put({"providers": [_vllm_card(enabled=False)]})
    assert disabled.status_code == 422 and disabled.json()["code"] == "provider_not_configured"


@pytest.mark.parametrize(
    ("body", "status", "code"),
    [
        ({"apply_mode": "merge"}, 422, "merge_not_enabled"),
        ({"reader_strategy": "single", "primary_reader": "vllm"}, 422, "provider_not_configured"),
        ({"reader_strategy": "off", "primary_reader": "gemini"}, 422, "invalid_reader_strategy"),
        ({"reader_strategy": "dual_agree", "primary_reader": "gemini"}, 422, None),
        ({"secondary_reader": "vllm"}, 422, None),
        ({"providers": [_vllm_card(), _vllm_card()]}, 422, None),
    ],
)
def test_unsupported_strategies_are_refused(
    harness: Harness, body: dict[str, Any], status: int, code: str | None
) -> None:
    _vllm_gate(harness)
    _enable(harness)
    response = harness.put(body)
    assert response.status_code == status, response.text
    if code is not None:
        assert response.json()["code"] == code
    assert harness.get()["apply_mode"] == "suggest"


def test_only_managers_hold_ai_manage() -> None:
    # P0 maps every session to manager; the capability table is what later roles get.
    assert "ai.manage" in capabilities_for_role("manager")
    assert "ai.manage" not in capabilities_for_role("ap_executive")
    assert "ai.manage" not in capabilities_for_role("auditor")


def test_company_server_test_is_keyless_synthetic_and_recorded_on_its_card(
    harness: Harness,
) -> None:
    harness.heartbeat()
    _vllm_gate(harness, mode="synthetic_only")
    _enable(harness)
    harness.put({"providers": [_vllm_card()]})
    image = harness.client.post(
        "/api/v1/ai/test/provider",
        json={"provider": "vllm", "kind": "image"},
        headers=harness.headers(),
    )
    assert image.status_code == 422 and image.json()["code"] == "image_test_not_available"
    queued = harness.client.post(
        "/api/v1/ai/test/provider", json={"provider": "vllm"}, headers=harness.headers()
    )
    assert queued.status_code == 202, queued.text
    request_id = UUID(queued.json()["request_id"])
    with harness.engine.connect() as connection:
        row = connection.execute(
            sa.text("SELECT provider, model, call_kind FROM ai_request WHERE id = :id"),
            {"id": request_id},
        ).one()
    assert tuple(row) == ("vllm", VLLM_MODEL, "provider_test")
    requested = _audit(harness, "ai.test_requested")[-1]
    assert requested["after_state"]["provider"] == "vllm"

    sent: list[httpx.Request] = []

    def server(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": VLLM_MODEL, "max_model_len": 8192}]})
        return httpx.Response(
            200,
            json={
                "model": VLLM_MODEL,
                "choices": [{"message": {"content": '{"status":"ok"}'}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 40, "completion_tokens": 4},
            },
        )

    provider = OpenAICompatProvider(
        VLLM_URL, frozenset({VLLM_URL}), inner_transport=httpx.MockTransport(server)
    )
    runner = AiRequestRunner(
        engine=harness.engine,
        keyring=harness.keyring,
        gate=DeploymentGate.from_values(
            "disabled", None, vllm_mode="synthetic_only", vllm_endpoints=VLLM_URL
        ),
        limits=lambda: LimitsState(AiLimits()),
        prices=lambda: None,
        providers={"vllm": provider},
        handlers=default_handlers(),
    )
    result = runner.run(request_id)
    assert (result.state, result.apply_outcome) == ("succeeded", "applied"), result
    assert all("authorization" not in request.headers for request in sent)
    view = harness.get()
    assert view["providers"][1]["last_test"]["ok"] is True
    # The Gemini status ladder is not satisfied by a company-server test.
    assert view["status"]["code"] == "NOT_TESTED"
    usage = harness.client.get("/api/v1/ai/usage", params={"provider": "vllm"})
    assert usage.status_code == 200 and usage.json()["provider"] == "vllm"
    assert usage.json()["usd_today"] is None
    assert harness.client.get("/api/v1/ai/usage", params={"provider": "x"}).status_code == 422


def test_health_heartbeat_marks_each_enabled_server_card(harness: Harness) -> None:
    _vllm_gate(harness)
    harness.put({"providers": [_vllm_card()]})
    calls: list[str] = []

    def server(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json={"data": [{"id": VLLM_MODEL, "max_model_len": 8192}]})

    default_url = "http://vllm-default.example.test/v1"
    provider = OpenAICompatProvider(
        default_url, frozenset({default_url, VLLM_URL}), inner_transport=httpx.MockTransport(server)
    )
    now = datetime.now(UTC)
    assert provider_health(harness.engine, provider, now) == 1
    assert calls == [f"{VLLM_URL}/models"]
    health = harness.get()["providers"][1]["health"]
    assert health["ok"] is True and health["at"] is not None
    # A URL that is no longer allowlisted is marked unhealthy without any request.
    narrowed = OpenAICompatProvider(
        default_url, frozenset({default_url}), inner_transport=httpx.MockTransport(server)
    )
    assert provider_health(harness.engine, narrowed, now) == 1
    assert calls == [f"{VLLM_URL}/models"]
    assert harness.get()["providers"][1]["health"]["ok"] is False
