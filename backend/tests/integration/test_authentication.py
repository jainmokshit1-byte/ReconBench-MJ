"""PostgreSQL integration checks for Phase 3 Tasks 3.1–3.4."""

from __future__ import annotations

import hashlib
import os
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from threading import Lock
from typing import Any

import boto3
import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from argon2 import PasswordHasher
from fastapi import FastAPI
from fastapi.testclient import TestClient
from reconbench_app.api.application import create_app
from reconbench_app.audit import write_audit_event
from reconbench_app.authentication import AuthService, LoginRateLimited
from reconbench_app.authorization import ActorContext, resolve_effective_role
from reconbench_app.db.ids import new_id
from reconbench_app.db.metadata import metadata
from reconbench_app.db.seed import BootstrapSettings, seed_database
from reconbench_app.idempotency import (
    IdempotencyKeyConflict,
    IdempotentResult,
    execute_idempotent,
)
from reconbench_app.settings import Settings
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session

_EMAIL = "phase3-manager@example.invalid"
_PASSWORD = "Synthetic P0 password! 123"
_WRONG_PASSWORD = "Synthetic incorrect password"
_PEER_IP = "198.51.100.77"
_LOW_COST_HASHER = PasswordHasher(time_cost=1, memory_cost=8192, parallelism=1)


@dataclass
class FrozenClock:
    current: datetime

    def __call__(self) -> datetime:
        return self.current

    def advance(self, delta: timedelta) -> None:
        self.current += delta

    def set(self, value: datetime) -> None:
        self.current = value


@dataclass
class AuthHarness:
    app: FastAPI
    client: TestClient
    engine: Engine
    service: AuthService
    clock: FrozenClock


@pytest.fixture(scope="session")
def auth_database_url() -> str:
    dsn = os.environ.get("RB_PHASE3_TEST_DATABASE_URL", "").strip()
    if not dsn:
        pytest.skip("RB_PHASE3_TEST_DATABASE_URL is required for disposable PostgreSQL tests")
    database_name = make_url(dsn).database
    if not database_name or not database_name.startswith("reconbench_phase2_"):
        raise RuntimeError(
            "auth integration tests require a disposable reconbench_phase2_* database"
        )

    engine = sa.create_engine(dsn, pool_size=12, max_overflow=12)
    try:
        with engine.connect() as connection:
            server_version = int(connection.scalar(sa.text("SHOW server_version_num")))
            if server_version // 10_000 != 16:
                raise RuntimeError("auth integration tests require PostgreSQL 16")
            expected_heads = set(
                ScriptDirectory.from_config(Config("backend/alembic.ini")).get_heads()
            )
            current_heads = set(MigrationContext.configure(connection).get_current_heads())
            if current_heads != expected_heads:
                raise RuntimeError("auth integration database must be migrated to head")

        bootstrap_hash = _LOW_COST_HASHER.hash(_PASSWORD)
        seed_database(
            engine,
            BootstrapSettings(
                workspace_name="Synthetic Phase 3 Workspace",
                email=_EMAIL,
                display_name="Synthetic Phase 3 Manager",
                password_hash=bootstrap_hash,
            ),
        )
    finally:
        engine.dispose()
    return dsn


@pytest.fixture(scope="session")
def auth_admin_engine(auth_database_url: str) -> Engine:
    engine = sa.create_engine(auth_database_url, pool_size=12, max_overflow=12)
    yield engine
    engine.dispose()


@pytest.fixture
def auth_harness(auth_admin_engine: Engine, monkeypatch: pytest.MonkeyPatch) -> Any:
    workspace_id = _reset_auth_state(auth_admin_engine)
    del workspace_id

    class FakeObjectStore:
        def list_buckets(self) -> dict[str, list[object]]:
            return {"Buckets": []}

    monkeypatch.setattr(boto3, "client", lambda *args, **kwargs: FakeObjectStore())
    settings = Settings(
        _env_file=None,
        environment="ci",
        database_url=auth_admin_engine.url.render_as_string(hide_password=False),
        s3_endpoint_url="http://127.0.0.1:1",
        s3_region="us-east-1",
        s3_bucket="synthetic-test",
        s3_api_access_key="synthetic-access",
        s3_api_secret_key="synthetic-secret",
        public_base_url="https://example.invalid",
        argon2_time_cost=1,
        argon2_memory_cost_kib=8192,
        argon2_parallelism=1,
    )
    app = create_app(settings)
    clock = FrozenClock(datetime.now(UTC))
    app.state.auth_service.clock = clock
    with TestClient(
        app,
        base_url="https://testserver",
        client=(_PEER_IP, 12345),
    ) as client:
        yield AuthHarness(
            app=app,
            client=client,
            engine=auth_admin_engine,
            service=app.state.auth_service,
            clock=clock,
        )


def _reset_auth_state(engine: Engine) -> Any:
    workspace = metadata.tables["workspace"]
    app_user = metadata.tables["app_user"]
    session = metadata.tables["session"]
    rate_limit_event = metadata.tables["rate_limit_event"]
    with engine.begin() as connection:
        workspace_id = connection.scalar(sa.select(workspace.c.id).limit(1))
        if workspace_id is None:
            raise RuntimeError("auth test bootstrap did not create the synthetic workspace")
        user_id = connection.scalar(
            sa.select(app_user.c.id).where(
                app_user.c.workspace_id == workspace_id,
                app_user.c.email == _EMAIL,
            )
        )
        if user_id is None:
            raise RuntimeError("auth test bootstrap did not create the synthetic manager")
        connection.execute(sa.delete(session).where(session.c.workspace_id == workspace_id))
        connection.execute(
            sa.delete(rate_limit_event).where(
                rate_limit_event.c.workspace_id == workspace_id,
                rate_limit_event.c.kind == "login",
            )
        )
        connection.execute(
            sa.update(app_user)
            .where(app_user.c.workspace_id == workspace_id, app_user.c.id == user_id)
            .values(
                password_hash=_LOW_COST_HASHER.hash(_PASSWORD),
                failed_logins=0,
                locked_until=None,
                last_login_at=None,
                version=app_user.c.version + 1,
            )
        )
        return workspace_id


def _failed(service: AuthService, email: str, source_ip: str) -> None:
    assert service.login(email, _WRONG_PASSWORD, source_ip) is None


def _request_body(email: str, password: str) -> dict[str, str]:
    return {"email": email, "password": password}


def test_login_me_logout_hashes_token_rotates_session_and_enforces_csrf(
    auth_harness: AuthHarness,
) -> None:
    harness = auth_harness
    client = harness.client

    login_response = client.post("/api/v1/auth/login", json=_request_body(_EMAIL, _PASSWORD))
    assert login_response.status_code == 200
    login_data = login_response.json()
    assert login_data["name"] == "Synthetic Phase 3 Manager"
    assert login_data["role"] == "manager"
    assert "data.view" in login_data["capabilities"]
    assert login_data["capabilities"] == [
        "files.upload",
        "links.edit",
        "recon.run",
        "cases.decide",
        "cases.approve_exception",
        "settings.change",
        "ai.manage",
        "users.manage",
        "export.create",
        "data.view",
    ]
    assert login_data["csrf_token"]
    set_cookie = login_response.headers["set-cookie"]
    assert "HttpOnly" in set_cookie
    assert "Secure" in set_cookie
    assert "SameSite=lax" in set_cookie
    assert "Path=/api/v1" in set_cookie

    token_one = client.cookies.get("reconbench_session")
    assert token_one
    sessions = metadata.tables["session"]
    with harness.engine.connect() as connection:
        row_one = (
            connection.execute(
                sa.select(sessions).where(
                    sessions.c.token_hash == hashlib.sha256(token_one.encode()).hexdigest()
                )
            )
            .mappings()
            .one()
        )
    assert row_one["token_hash"] == hashlib.sha256(token_one.encode()).hexdigest()
    assert token_one not in row_one["token_hash"]
    assert row_one["csrf_hash"] == hashlib.sha256(login_data["csrf_token"].encode()).hexdigest()

    me_response = client.get("/api/v1/auth/me")
    assert me_response.status_code == 200
    assert me_response.json()["csrf_token"] == login_data["csrf_token"]

    csrf_rejected = client.post("/api/v1/test-write", json={})
    assert csrf_rejected.status_code == 403
    assert csrf_rejected.json()["code"] == "CSRF_FAILED"
    assert csrf_rejected.headers["x-content-type-options"] == "nosniff"

    second_login = client.post("/api/v1/auth/login", json=_request_body(_EMAIL, _PASSWORD))
    assert second_login.status_code == 200
    second_data = second_login.json()
    token_two = client.cookies.get("reconbench_session")
    assert token_two and token_two != token_one
    with harness.engine.connect() as connection:
        old_session = connection.execute(
            sa.select(sessions.c.revoked_at).where(sessions.c.id == row_one["id"])
        ).scalar_one()
    assert old_session is not None

    logout_response = client.post(
        "/api/v1/auth/logout",
        headers={"X-CSRF-Token": second_data["csrf_token"]},
    )
    assert logout_response.status_code == 204
    assert "Max-Age=0" in logout_response.headers["set-cookie"]
    assert logout_response.headers["referrer-policy"] == "same-origin"
    assert client.get("/api/v1/auth/me").status_code == 401

    client.cookies.set("reconbench_session", token_one, path="/api/v1")
    old_token_response = client.get("/api/v1/auth/me")
    assert old_token_response.status_code == 401
    assert old_token_response.headers["x-session-ended"] == "true"


def test_wrong_email_and_password_have_identical_401_and_similar_timing(
    auth_harness: AuthHarness,
) -> None:
    client = auth_harness.client
    samples: dict[str, list[float]] = {"unknown": [], "known": []}
    responses = {}
    for _ in range(3):
        start = time.perf_counter()
        unknown = client.post(
            "/api/v1/auth/login",
            json=_request_body("missing@example.invalid", _WRONG_PASSWORD),
        )
        samples["unknown"].append(time.perf_counter() - start)
        start = time.perf_counter()
        known = client.post(
            "/api/v1/auth/login",
            json=_request_body(_EMAIL, _WRONG_PASSWORD),
        )
        samples["known"].append(time.perf_counter() - start)
        responses["unknown"] = unknown
        responses["known"] = known

    assert responses["unknown"].status_code == responses["known"].status_code == 401
    assert responses["unknown"].content == responses["known"].content
    assert abs(statistics.median(samples["unknown"]) - statistics.median(samples["known"])) < 0.5


def test_unknown_email_uses_dummy_hash_and_x_forwarded_for_is_ignored(
    auth_harness: AuthHarness,
) -> None:
    response = auth_harness.client.post(
        "/api/v1/auth/login",
        headers={"X-Forwarded-For": "203.0.113.222"},
        json=_request_body("not-registered@example.invalid", _WRONG_PASSWORD),
    )
    assert response.status_code == 401
    events = metadata.tables["rate_limit_event"]
    with auth_harness.engine.connect() as connection:
        event = (
            connection.execute(
                sa.select(events.c.email, events.c.ip, events.c.succeeded).where(
                    events.c.email == "not-registered@example.invalid"
                )
            )
            .mappings()
            .one()
        )
    assert str(event["ip"]) == _PEER_IP
    assert event["email"] == "not-registered@example.invalid"
    assert event["succeeded"] is False


def test_email_bucket_spans_source_ips_and_account_success_resets_it(
    auth_harness: AuthHarness,
) -> None:
    service = auth_harness.service
    for index in range(9):
        _failed(service, _EMAIL, f"198.51.100.{index + 1}")
    assert service.login(_EMAIL, _PASSWORD, "198.51.100.90") is not None

    # The successful event clears the email bucket across IPs.
    for index in range(10):
        _failed(service, _EMAIL, f"203.0.113.{index + 1}")
    with pytest.raises(LoginRateLimited):
        service.login(_EMAIL, _WRONG_PASSWORD, "203.0.113.100")


def test_ip_bucket_spans_emails_and_success_does_not_clear_it(
    auth_harness: AuthHarness,
) -> None:
    service = auth_harness.service
    for index in range(29):
        _failed(service, f"unknown-{index}@example.invalid", _PEER_IP)

    assert service.login(_EMAIL, _PASSWORD, _PEER_IP) is not None
    _failed(service, "another-unknown@example.invalid", _PEER_IP)
    with pytest.raises(LoginRateLimited):
        service.login("one-more-unknown@example.invalid", _WRONG_PASSWORD, _PEER_IP)

    response = auth_harness.client.post(
        "/api/v1/auth/login",
        json=_request_body("yet-another-unknown@example.invalid", _WRONG_PASSWORD),
    )
    assert response.status_code == 429
    assert response.headers["retry-after"] == "900"
    assert response.json()["code"] == "login_rate_limited"
    assert response.headers["content-security-policy"] == (
        "default-src 'self'; frame-ancestors 'none'"
    )


def test_email_and_ip_429_responses_are_identical_and_do_not_identify_bucket(
    auth_harness: AuthHarness,
) -> None:
    service = auth_harness.service
    for index in range(10):
        _failed(service, _EMAIL, f"198.51.100.{index + 1}")
    for index in range(10):
        _failed(service, "locked@example.invalid", f"203.0.113.{index + 1}")

    known_email = auth_harness.client.post(
        "/api/v1/auth/login", json=_request_body(_EMAIL, _PASSWORD)
    )
    unknown_email = auth_harness.client.post(
        "/api/v1/auth/login", json=_request_body("locked@example.invalid", _WRONG_PASSWORD)
    )
    assert known_email.status_code == unknown_email.status_code == 429
    assert known_email.content == unknown_email.content
    assert known_email.headers["retry-after"] == unknown_email.headers["retry-after"] == "900"
    assert "email" not in known_email.text.lower()
    assert "ip" not in known_email.text.lower()

    for index in range(30):
        _failed(service, f"ip-only-{index}@example.invalid", _PEER_IP)
    ip_limited = auth_harness.client.post(
        "/api/v1/auth/login", json=_request_body("ip-trigger@example.invalid", _PASSWORD)
    )
    assert ip_limited.status_code == 429
    assert ip_limited.content == known_email.content
    assert ip_limited.headers["retry-after"] == known_email.headers["retry-after"]


def test_rolling_window_boundary_and_automatic_email_lock_expiry(
    auth_harness: AuthHarness,
) -> None:
    service = auth_harness.service
    clock = auth_harness.clock
    start = clock.current

    for index in range(9):
        _failed(service, "window@example.invalid", f"198.51.100.{index + 1}")
    clock.set(start + timedelta(minutes=15, microseconds=8))
    _failed(service, "window@example.invalid", "198.51.100.90")

    clock.set(start + timedelta(minutes=20))
    for index in range(10):
        _failed(service, "expire@example.invalid", f"203.0.113.{index + 1}")
    with pytest.raises(LoginRateLimited):
        service.login("expire@example.invalid", _WRONG_PASSWORD, "203.0.113.80")

    # The threshold event is ordered at most a few microseconds after start.
    clock.set(start + timedelta(minutes=35, seconds=1))
    _failed(service, "expire@example.invalid", "203.0.113.81")


def test_ip_throttle_automatically_expires_after_15_minutes(
    auth_harness: AuthHarness,
) -> None:
    service = auth_harness.service
    clock = auth_harness.clock
    start = clock.current
    for index in range(30):
        _failed(service, f"ip-{index}@example.invalid", _PEER_IP)
    with pytest.raises(LoginRateLimited):
        service.login("blocked@example.invalid", _WRONG_PASSWORD, _PEER_IP)

    clock.set(start + timedelta(minutes=15, seconds=1))
    _failed(service, "after-expiry@example.invalid", _PEER_IP)


def test_concurrent_failures_cannot_bypass_email_bucket(
    auth_harness: AuthHarness,
) -> None:
    service = auth_harness.service

    def attempt(_: int) -> bool:
        try:
            return service.login("parallel@example.invalid", _WRONG_PASSWORD, _PEER_IP) is None
        except LoginRateLimited:
            return False

    with ThreadPoolExecutor(max_workers=12) as executor:
        results = list(executor.map(attempt, range(12)))
    assert sum(results) == 10
    assert results.count(False) == 2

    events = metadata.tables["rate_limit_event"]
    with auth_harness.engine.connect() as connection:
        count = connection.scalar(
            sa.select(sa.func.count())
            .select_from(events)
            .where(
                events.c.email == "parallel@example.invalid",
                events.c.kind == "login",
            )
        )
    assert count == 10


def test_idle_and_absolute_session_expiry_with_fake_clock(
    auth_harness: AuthHarness,
) -> None:
    service = auth_harness.service
    clock = auth_harness.clock
    login = service.login(_EMAIL, _PASSWORD, _PEER_IP)
    assert login is not None

    clock.advance(timedelta(minutes=29))
    assert service.authenticate(login.token).context is not None
    clock.advance(timedelta(minutes=29))
    assert service.authenticate(login.token).context is not None
    clock.advance(timedelta(minutes=30))
    assert service.authenticate(login.token).context is None

    absolute_login = service.login(_EMAIL, _PASSWORD, _PEER_IP)
    assert absolute_login is not None
    while clock.current + timedelta(minutes=29) < absolute_login.absolute_expires_at:
        clock.advance(timedelta(minutes=29))
        assert service.authenticate(absolute_login.token).context is not None
    clock.set(absolute_login.absolute_expires_at - timedelta(seconds=1))
    assert service.authenticate(absolute_login.token).context is not None
    clock.set(absolute_login.absolute_expires_at)
    assert service.authenticate(absolute_login.token).context is None


def test_local_environment_omits_secure_cookie_flag(
    auth_database_url: str,
    auth_admin_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _reset_auth_state(auth_admin_engine)

    class FakeObjectStore:
        def list_buckets(self) -> dict[str, list[object]]:
            return {"Buckets": []}

    monkeypatch.setattr(boto3, "client", lambda *args, **kwargs: FakeObjectStore())
    settings = Settings(
        _env_file=None,
        environment="local",
        database_url=auth_database_url,
        s3_endpoint_url="http://127.0.0.1:1",
        s3_region="us-east-1",
        s3_bucket="synthetic-test",
        s3_api_access_key="synthetic-access",
        s3_api_secret_key="synthetic-secret",
        public_base_url="http://localhost",
        argon2_time_cost=1,
        argon2_memory_cost_kib=8192,
        argon2_parallelism=1,
    )
    app = create_app(settings)
    with TestClient(app, base_url="http://testserver", client=(_PEER_IP, 12345)) as client:
        response = client.post("/api/v1/auth/login", json=_request_body(_EMAIL, _PASSWORD))
    assert response.status_code == 200
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=lax" in response.headers["set-cookie"]
    assert "Secure" not in response.headers["set-cookie"]


def test_audit_helper_uses_database_hash_chain_and_append_only_permissions(
    auth_harness: AuthHarness,
) -> None:
    workspace = metadata.tables["workspace"]
    app_user = metadata.tables["app_user"]
    audit_log = metadata.tables["audit_log"]
    with auth_harness.engine.connect() as connection:
        actor_row = (
            connection.execute(
                sa.select(app_user.c.id, app_user.c.workspace_id, app_user.c.role).where(
                    app_user.c.email == _EMAIL
                )
            )
            .mappings()
            .one()
        )
        assert (
            connection.scalar(
                sa.select(workspace.c.id).where(workspace.c.id == actor_row["workspace_id"])
            )
            == actor_row["workspace_id"]
        )

    actor = ActorContext(
        workspace_id=actor_row["workspace_id"],
        actor_id=actor_row["id"],
        actor_role=resolve_effective_role(str(actor_row["role"])),
    )
    with auth_harness.engine.begin() as connection:
        connection.exec_driver_sql("SET LOCAL ROLE reconbench_app")
        first = write_audit_event(
            connection,
            actor,
            action="vendor.merge",
            target_type="vendor",
            target_id=new_id(),
            before_state={"name": "Old vendor", "amount": Decimal("12.30")},
            after_state={"name": "Merged vendor", "amount": Decimal("12.30")},
            reason="synthetic audit integration test",
        )
        second = write_audit_event(
            connection,
            actor,
            action="config.save",
            target_type="config",
            target_id=new_id(),
            before_state={"setting": "old"},
            after_state={"setting": "new"},
        )

    assert first.sequence < second.sequence
    assert first.occurred_at.tzinfo is not None
    assert len(first.row_hash) == 64
    assert second.previous_hash == first.row_hash
    with auth_harness.engine.connect() as connection:
        saved = (
            connection.execute(
                sa.select(
                    audit_log.c.actor_id,
                    audit_log.c.actor_role,
                    audit_log.c.before_state,
                    audit_log.c.after_state,
                    audit_log.c.row_hash,
                    audit_log.c.prev_hash,
                ).where(audit_log.c.id == first.id)
            )
            .mappings()
            .one()
        )
    assert saved["actor_id"] == actor.actor_id
    assert saved["actor_role"] == "manager"
    assert saved["before_state"]["amount"] == "12.30"
    assert saved["after_state"]["name"] == "Merged vendor"
    assert saved["row_hash"] == first.row_hash

    with pytest.raises(sa.exc.DBAPIError), auth_harness.engine.begin() as connection:
        connection.exec_driver_sql("SET LOCAL ROLE reconbench_app")
        connection.execute(
            sa.update(audit_log).where(audit_log.c.id == first.id).values(reason="tampered")
        )


def test_readyz_is_public_and_has_security_headers(auth_harness: AuthHarness) -> None:
    response = auth_harness.client.get("/readyz")
    assert response.status_code == 200
    assert response.headers["content-security-policy"] == (
        "default-src 'self'; frame-ancestors 'none'"
    )
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "same-origin"


def test_idempotency_replays_first_result_rejects_changed_body_and_expires(
    auth_harness: AuthHarness,
) -> None:
    app_user = metadata.tables["app_user"]
    with auth_harness.engine.connect() as connection:
        workspace_id = connection.scalar(sa.select(metadata.tables["workspace"].c.id).limit(1))
        user_id = connection.scalar(
            sa.select(app_user.c.id).where(app_user.c.email == _EMAIL).limit(1)
        )
    assert workspace_id is not None and user_id is not None
    key = f"phase3-run-idempotency-{new_id()}"
    first_resource_id = new_id()
    later_resource_id = new_id()
    start = datetime(2026, 10, 2, 9, 0, tzinfo=UTC)
    invocations = 0

    def first_operation(_: Session) -> IdempotentResult:
        nonlocal invocations
        invocations += 1
        return IdempotentResult(
            202,
            {"status": "queued", "amount": Decimal("9.20")},
            "runs",
            first_resource_id,
        )

    with Session(auth_harness.engine) as session:
        first = execute_idempotent(
            session,
            workspace_id=workspace_id,
            user_id=user_id,
            endpoint="POST /runs",
            key=key,
            request_body={"scope": "all", "threshold": Decimal("0.9500")},
            operation=first_operation,
            now=start,
        )
    assert first.status_code == 202 and first.replayed is False
    assert first.body == {"status": "queued", "amount": "9.20"}

    with Session(auth_harness.engine) as session:
        replay = execute_idempotent(
            session,
            workspace_id=workspace_id,
            user_id=user_id,
            endpoint="POST /runs",
            key=key,
            request_body={"threshold": Decimal("0.9500"), "scope": "all"},
            operation=lambda _: pytest.fail("a replay must not execute the operation"),
            now=start + timedelta(hours=23),
        )
    assert replay.replayed is True
    assert replay.status_code == first.status_code
    assert replay.body == first.body
    assert replay.resource_id == first_resource_id
    assert invocations == 1

    with Session(auth_harness.engine) as session, pytest.raises(IdempotencyKeyConflict):
        execute_idempotent(
            session,
            workspace_id=workspace_id,
            user_id=user_id,
            endpoint="POST /runs",
            key=key,
            request_body={"scope": "other"},
            operation=first_operation,
            now=start + timedelta(hours=23),
        )

    with Session(auth_harness.engine) as session:
        expired = execute_idempotent(
            session,
            workspace_id=workspace_id,
            user_id=user_id,
            endpoint="POST /runs",
            key=key,
            request_body={"scope": "after-expiry"},
            operation=lambda _: IdempotentResult(
                202, {"status": "queued-again"}, "runs", later_resource_id
            ),
            now=start + timedelta(hours=24),
        )
    assert expired.replayed is False
    assert expired.resource_id == later_resource_id


def test_concurrent_idempotent_attempts_execute_side_effect_once(
    auth_harness: AuthHarness,
) -> None:
    app_user = metadata.tables["app_user"]
    with auth_harness.engine.connect() as connection:
        workspace_id = connection.scalar(sa.select(metadata.tables["workspace"].c.id).limit(1))
        user_id = connection.scalar(
            sa.select(app_user.c.id).where(app_user.c.email == _EMAIL).limit(1)
        )
    assert workspace_id is not None and user_id is not None
    count_lock = Lock()
    invocations = 0
    key = f"phase3-concurrent-idempotency-{new_id()}"

    def operation(_: Session) -> IdempotentResult:
        nonlocal invocations
        with count_lock:
            invocations += 1
        time.sleep(0.05)
        return IdempotentResult(202, {"job": "synthetic-job"}, "jobs", new_id())

    def invoke(_: int) -> IdempotentResult:
        with Session(auth_harness.engine) as session:
            return execute_idempotent(
                session,
                workspace_id=workspace_id,
                user_id=user_id,
                endpoint="POST /exports",
                key=key,
                request_body={"filters": {"status": ["needs_review"]}},
                operation=operation,
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(invoke, range(2)))
    assert invocations == 1
    assert len({result.resource_id for result in results}) == 1
    assert sorted(result.replayed for result in results) == [False, True]
