"""AI ledger, budgets, cache and runner on a disposable PostgreSQL 16 database.

All provider calls use the offline ``ScriptedProvider``; nothing leaves the host.
Each test creates its own workspace so committed rows never interfere.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
import sqlalchemy as sa
from reconbench_app.ai import cache as ai_cache
from reconbench_app.ai import ledger
from reconbench_app.ai.gate import DeploymentGate
from reconbench_app.ai.key_seal import SealingPublicKey, key_hint, seal
from reconbench_app.ai.limits import AiLimits, LimitsState, Reservation
from reconbench_app.ai.provider import ProviderFailure
from reconbench_app.ai.stub import ScriptedProvider
from reconbench_app.ai.worker.handlers import default_handlers
from reconbench_app.ai.worker.key_open import Keyring
from reconbench_app.ai.worker.keytool import KEYRING_NAME, PUBLIC_KEY_NAME, generate, reseal
from reconbench_app.ai.worker.runner import AiRequestDeferred, AiRequestRunner
from reconbench_app.db.ids import new_id
from reconbench_app.db.metadata import metadata
from sqlalchemy.engine import Engine, make_url

SENTINEL_KEY = "AIzaSyD-synthetic-runner-key-00000000000"
MODEL = "gemini-3.8-flash"
OK = '{"status": "ok"}'


_CREATED_WORKSPACES: list[UUID] = []
_CLEANUP_TABLES = (
    "ai_call_log",
    "ai_request",
    "ai_cache",
    "ai_setting",
    "secret",
    "session",
    "rate_limit_event",
    "app_user",
)


def _remove_workspaces(engine: Engine) -> None:
    """P0 login requires exactly one workspace, so test workspaces never outlive the module."""

    if not _CREATED_WORKSPACES:
        return
    with engine.begin() as connection:
        for table in _CLEANUP_TABLES:
            connection.execute(
                sa.text(f"DELETE FROM {table} WHERE workspace_id = ANY(:ids)"),
                {"ids": list(_CREATED_WORKSPACES)},
            )
        connection.execute(
            sa.text("DELETE FROM workspace WHERE id = ANY(:ids)"),
            {"ids": list(_CREATED_WORKSPACES)},
        )
    _CREATED_WORKSPACES.clear()


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    dsn = os.environ.get("RB_PHASE2_TEST_DATABASE_URL", "").strip()
    if not dsn:
        pytest.skip("RB_PHASE2_TEST_DATABASE_URL is required for disposable PostgreSQL tests")
    database = make_url(dsn).database or ""
    if not database.startswith("reconbench_phase2_"):
        raise RuntimeError("AI runner checks require a disposable reconbench_phase2_* database")
    created = sa.create_engine(dsn, pool_size=20, max_overflow=10)
    try:
        yield created
    finally:
        _remove_workspaces(created)
        created.dispose()


@pytest.fixture(scope="module")
def keys(tmp_path_factory: pytest.TempPathFactory) -> tuple[Keyring, SealingPublicKey, Path]:
    directory = tmp_path_factory.mktemp("ai-keys")
    generate(directory, "k1")
    return (
        Keyring.load(directory / KEYRING_NAME),
        SealingPublicKey.from_file(directory / PUBLIC_KEY_NAME),
        directory,
    )


@dataclass
class Workspace:
    id: UUID
    user_id: UUID
    secret_id: UUID | None


def _workspace(
    engine: Engine,
    public_key: SealingPublicKey,
    *,
    with_key: bool = True,
    session_bound: bool = False,
) -> Workspace:
    workspace_id, user_id = new_id(), new_id()
    _CREATED_WORKSPACES.append(workspace_id)
    now = datetime.now(UTC)
    with engine.begin() as connection:
        connection.execute(
            sa.insert(metadata.tables["workspace"]).values(id=workspace_id, name="AI test")
        )
        connection.execute(
            sa.insert(metadata.tables["app_user"]).values(
                id=user_id,
                workspace_id=workspace_id,
                email=f"ai-{user_id}@example.invalid",
                display_name="Synthetic AI tester",
                password_hash="not-a-real-hash",
            )
        )
        secret_id = None
        if with_key:
            secret_id = new_id()
            sealed = seal(
                SENTINEL_KEY.encode(),
                public_key=public_key,
                workspace_id=workspace_id,
                secret_id=secret_id,
            )
            session_id = None
            expires_at = None
            if session_bound:
                session_id = new_id()
                expires_at = now + timedelta(hours=1)
                connection.execute(
                    sa.insert(metadata.tables["session"]).values(
                        id=session_id,
                        workspace_id=workspace_id,
                        user_id=user_id,
                        token_hash=hashlib.sha256(str(session_id).encode()).hexdigest(),
                        csrf_hash=hashlib.sha256(f"c{session_id}".encode()).hexdigest(),
                        idle_expires_at=now + timedelta(minutes=30),
                        abs_expires_at=now + timedelta(hours=12),
                    )
                )
            connection.execute(
                sa.insert(metadata.tables["secret"]).values(
                    id=secret_id,
                    workspace_id=workspace_id,
                    purpose="ai_api_key",
                    ciphertext=sealed.envelope,
                    nonce=sealed.nonce,
                    key_id=sealed.key_id,
                    key_hint=key_hint(SENTINEL_KEY),
                    created_by=user_id,
                    session_id=session_id,
                    expires_at=expires_at,
                )
            )
        connection.execute(
            sa.insert(metadata.tables["ai_setting"]).values(
                id=new_id(),
                workspace_id=workspace_id,
                mode="rules_only",
                provider="gemini",
                model=MODEL,
                key_storage="session" if session_bound else "saved",
                key_secret_id=secret_id,
                updated_by=user_id,
            )
        )
    return Workspace(workspace_id, user_id, secret_id)


def _new_test(
    workspace: Workspace, *, operation: str = "test", subject: str = "1"
) -> ledger.NewRequest:
    return ledger.NewRequest(
        workspace_id=workspace.id,
        call_kind="provider_test",
        hook=None,
        mode="rules_only",
        subject_type="fixture",
        subject_id=None,
        subject_version=subject,
        operation_key=operation,
        settings_version=1,
        consent_fingerprint=None,
        provider="gemini",
        model=MODEL,
        prompt_version="provider_test.v1",
        schema_version="provider_test.schema.v1",
        redaction_version="redaction.v1",
        requested_by=workspace.user_id,
    )


def _enqueue(engine: Engine, request: ledger.NewRequest) -> UUID:
    with engine.begin() as connection:
        request_id = ledger.create_request(connection, request)
    assert request_id is not None
    return request_id


def _runner(
    engine: Engine,
    keyring: Keyring,
    provider: ScriptedProvider,
    *,
    gate: str = "synthetic_only",
    limits: AiLimits | None = None,
    clock: Any = None,
) -> AiRequestRunner:
    configured = limits or AiLimits()
    # The runner only sends for the provider the workspace selected.
    provider.name = "gemini"
    return AiRequestRunner(
        engine=engine,
        keyring=keyring,
        gate=DeploymentGate.from_values(gate, None),
        limits=lambda: LimitsState(configured),
        prices=lambda: None,
        provider=provider,
        handlers=default_handlers(),
        **({"clock": clock} if clock else {}),
    )


def _row(engine: Engine, request_id: UUID) -> Any:
    with engine.connect() as connection:
        return (
            connection.execute(sa.select(ledger.REQUESTS).where(ledger.REQUESTS.c.id == request_id))
            .mappings()
            .one()
        )


def _test_result(engine: Engine, workspace: Workspace) -> tuple[Any, Any]:
    settings = metadata.tables["ai_setting"]
    with engine.connect() as connection:
        row = connection.execute(
            sa.select(settings.c.last_test_ok, settings.c.last_test_at).where(
                settings.c.workspace_id == workspace.id
            )
        ).one()
    return row[0], row[1]


def test_provider_test_succeeds_end_to_end_with_the_opened_key(engine: Engine, keys: Any) -> None:
    keyring, public_key, _ = keys
    workspace = _workspace(engine, public_key)
    provider = ScriptedProvider([OK])
    request_id = _enqueue(engine, _new_test(workspace))

    result = _runner(engine, keyring, provider).run(request_id)

    assert (result.state, result.apply_outcome) == ("succeeded", "applied")
    assert provider.keys_seen == [SENTINEL_KEY]
    [sent] = provider.calls
    assert sent.max_output_tokens == 32 and sent.timeout_ms == 60_000
    row = _row(engine, request_id)
    assert row["request_state"] == "succeeded" and row["usage_known"]
    assert row["reserved_at"] and row["sent_at"] and row["finished_at"]
    assert row["reservation_is_estimate"] is True
    assert _test_result(engine, workspace)[0] is True
    for value in row.values():
        assert SENTINEL_KEY not in str(value)
    # A duplicate job for the same row is a no-op.
    assert _runner(engine, keyring, provider).run(request_id).state == "not_queued"
    assert len(provider.calls) == 1


def test_disabled_gate_skips_without_calling_the_provider(engine: Engine, keys: Any) -> None:
    keyring, public_key, _ = keys
    workspace = _workspace(engine, public_key)
    provider = ScriptedProvider([OK])
    request_id = _enqueue(engine, _new_test(workspace))
    result = _runner(engine, keyring, provider, gate="disabled").run(request_id)
    assert (result.state, result.code) == ("skipped", "EXTERNAL_CALLS_DISABLED")
    assert provider.calls == []
    assert _row(engine, request_id)["reserved_at"] is None


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ("no_key", "KEY_NOT_SET"),
        ("revoked_session", "KEY_EXPIRED"),
        ("settings_bumped", "SETTINGS_CHANGED"),
        ("limits_missing", "LIMITS_NOT_CONFIGURED"),
    ],
)
def test_blockers_skip_before_any_reservation(
    engine: Engine, keys: Any, change: str, code: str
) -> None:
    keyring, public_key, _ = keys
    workspace = _workspace(
        engine, public_key, with_key=change != "no_key", session_bound=change == "revoked_session"
    )
    request_id = _enqueue(engine, _new_test(workspace))
    with engine.begin() as connection:
        if change == "revoked_session":
            connection.execute(
                sa.text("UPDATE session SET revoked_at = now() WHERE workspace_id = :w"),
                {"w": workspace.id},
            )
        if change == "settings_bumped":
            connection.execute(
                sa.text("UPDATE ai_setting SET version = version + 1 WHERE workspace_id = :w"),
                {"w": workspace.id},
            )
    provider = ScriptedProvider([OK])
    runner = _runner(engine, keyring, provider)
    if change == "limits_missing":
        runner.limits = lambda: LimitsState(None)
    result = runner.run(request_id)
    assert (result.state, result.code) == ("skipped", code)
    assert provider.calls == []


def test_unreadable_key_is_released_and_never_sent(
    engine: Engine, keys: Any, tmp_path: Path
) -> None:
    _keyring, public_key, _ = keys
    workspace = _workspace(engine, public_key)
    generate(tmp_path, "k1")
    wrong = Keyring.load(tmp_path / KEYRING_NAME)
    provider = ScriptedProvider([OK])
    request_id = _enqueue(engine, _new_test(workspace))
    result = _runner(engine, wrong, provider).run(request_id)
    assert (result.state, result.code) == ("released", "KEY_UNREADABLE")
    assert provider.calls == []
    row = _row(engine, request_id)
    assert row["sent_at"] is None and row["reserved_at"] is not None


def test_uncertain_outcome_needs_one_acknowledgement_per_new_attempt(
    engine: Engine, keys: Any
) -> None:
    keyring, public_key, _ = keys
    workspace = _workspace(engine, public_key)
    provider = ScriptedProvider([ProviderFailure("timeout", delivery="may_have_been_sent"), OK])
    runner = _runner(engine, keyring, provider)
    first = _enqueue(engine, _new_test(workspace))
    assert runner.run(first).state == "uncertain"
    assert _row(engine, first)["error_code"] == "timeout"
    assert _test_result(engine, workspace)[0] is False

    with engine.begin() as connection:
        assert ledger.create_request(connection, _new_test(workspace)) is None
        assert ledger.acknowledge(
            connection,
            workspace_id=workspace.id,
            request_id=first,
            user_id=workspace.user_id,
            now=datetime.now(UTC),
        )
        assert not ledger.acknowledge(
            connection,
            workspace_id=workspace.id,
            request_id=first,
            user_id=workspace.user_id,
            now=datetime.now(UTC),
        )
        second = ledger.create_request(connection, _new_test(workspace))
        assert second is not None
        assert ledger.create_request(connection, _new_test(workspace)) is None
    assert _row(engine, second)["attempt_no"] == 2
    assert runner.run(second).state == "succeeded"
    assert len(provider.calls) == 2


def test_not_sent_failures_release_and_responded_failures_fail(engine: Engine, keys: Any) -> None:
    keyring, public_key, _ = keys
    workspace = _workspace(engine, public_key)
    provider = ScriptedProvider(
        [
            ProviderFailure("PROVIDER_UNAVAILABLE", delivery="not_sent"),
            ProviderFailure("bad_key", delivery="responded"),
        ]
    )
    runner = _runner(engine, keyring, provider)
    released = _enqueue(engine, _new_test(workspace, subject="a"))
    assert runner.run(released).state == "released"
    row = _row(engine, released)
    assert row["sent_at"] is None and row["error_code"] == "PROVIDER_UNAVAILABLE"
    failed = _enqueue(engine, _new_test(workspace, subject="b"))
    assert runner.run(failed).state == "failed"
    assert _row(engine, failed)["error_code"] == "bad_key"
    # A released attempt may be retried by a new request; a failed one may not.
    with engine.begin() as connection:
        assert ledger.create_request(connection, _new_test(workspace, subject="a")) is not None
        assert ledger.create_request(connection, _new_test(workspace, subject="b")) is None


def test_rate_limit_sets_a_cooldown_that_defers_the_next_reservation(
    engine: Engine, keys: Any
) -> None:
    keyring, public_key, _ = keys
    workspace = _workspace(engine, public_key)
    provider = ScriptedProvider(
        [ProviderFailure("rate_limited", delivery="responded", cooldown_seconds=60), OK]
    )
    runner = _runner(engine, keyring, provider)
    assert runner.run(_enqueue(engine, _new_test(workspace, subject="a"))).state == "failed"
    second = _enqueue(engine, _new_test(workspace, subject="b"))
    with pytest.raises(AiRequestDeferred) as caught:
        runner.run(second)
    assert caught.value.code == "COOLDOWN"
    assert _row(engine, second)["request_state"] == "queued"
    later = _runner(
        engine, keyring, provider, clock=lambda: datetime.now(UTC) + timedelta(seconds=61)
    )
    assert later.run(second).state == "succeeded"


def test_invalid_output_is_recorded_and_discarded(engine: Engine, keys: Any) -> None:
    keyring, public_key, _ = keys
    workspace = _workspace(engine, public_key)
    provider = ScriptedProvider(['{"status": "connected", "verdict": "matched"}'])
    request_id = _enqueue(engine, _new_test(workspace))
    result = _runner(engine, keyring, provider).run(request_id)
    assert (result.state, result.code, result.apply_outcome) == (
        "succeeded",
        "invalid_output",
        "discarded_invalid",
    )
    assert _test_result(engine, workspace)[0] is False


def test_settings_change_during_the_call_discards_the_result(engine: Engine, keys: Any) -> None:
    keyring, public_key, _ = keys
    workspace = _workspace(engine, public_key)

    class _ChangingProvider(ScriptedProvider):
        def complete(self, request: Any, *, api_key: str) -> Any:
            with engine.begin() as connection:
                connection.execute(
                    sa.text("UPDATE ai_setting SET version = version + 1 WHERE workspace_id = :w"),
                    {"w": workspace.id},
                )
            return super().complete(request, api_key=api_key)

    request_id = _enqueue(engine, _new_test(workspace))
    result = _runner(engine, keyring, _ChangingProvider([OK])).run(request_id)
    assert (result.state, result.apply_outcome) == ("succeeded", "discarded_revoked")
    assert _test_result(engine, workspace)[0] is None


def test_crash_after_sending_is_swept_to_uncertain_and_never_retried(
    engine: Engine, keys: Any
) -> None:
    _keyring, public_key, _ = keys
    workspace = _workspace(engine, public_key)
    request_id = _enqueue(engine, _new_test(workspace))
    now = datetime.now(UTC)
    reserved = ledger.reserve(
        engine,
        request_id,
        reservation=Reservation(100, 32, True, None, None),
        limits=AiLimits(),
        now=now,
    )
    assert reserved.status == "reserved"
    assert ledger.mark_sending(engine, request_id, now, 60)
    # The worker dies here. Before the lease ends, sweeping changes nothing.
    assert ledger.sweep_requests(engine, now=now, queue_timeout_seconds=1_800).uncertain == 0
    swept = ledger.sweep_requests(
        engine, now=now + timedelta(seconds=200), queue_timeout_seconds=1_800
    )
    assert swept.uncertain >= 1
    row = _row(engine, request_id)
    assert (row["request_state"], row["error_code"]) == ("uncertain", "LEASE_EXPIRED")
    with engine.begin() as connection:
        assert ledger.create_request(connection, _new_test(workspace)) is None


def test_queue_timeout_skips_stale_requests(engine: Engine, keys: Any) -> None:
    keyring, public_key, _ = keys
    workspace = _workspace(engine, public_key)
    request_id = _enqueue(engine, _new_test(workspace))
    provider = ScriptedProvider([OK])
    runner = _runner(
        engine, keyring, provider, clock=lambda: datetime.now(UTC) + timedelta(hours=1)
    )
    assert runner.run(request_id).code == "QUEUE_TIMEOUT"
    assert provider.calls == []


def _reserve_all(engine: Engine, ids: list[UUID], limits: AiLimits) -> list[str]:
    now = datetime.now(UTC)

    def attempt(request_id: UUID) -> str:
        return ledger.reserve(
            engine,
            request_id,
            reservation=Reservation(1_000, 100, True, None, None),
            limits=limits,
            now=now,
        ).status

    with ThreadPoolExecutor(max_workers=len(ids)) as pool:
        return list(pool.map(attempt, ids))


@pytest.mark.parametrize(
    ("limits", "allowed"),
    [
        (AiLimits(in_flight=2), 2),
        (AiLimits(in_flight=50, per_minute_calls=3), 3),
        (AiLimits(in_flight=50, per_minute_calls=100, per_day_calls=4), 4),
        (AiLimits(in_flight=50, per_minute_calls=100, per_operation_calls=5), 5),
        (AiLimits(in_flight=50, per_minute_calls=100, per_day_tokens=3_300), 3),
        (AiLimits(in_flight=50, per_minute_calls=100, per_operation_tokens=2_200), 2),
    ],
)
def test_concurrent_workers_cannot_exceed_caps(
    engine: Engine, keys: Any, limits: AiLimits, allowed: int
) -> None:
    _keyring, public_key, _ = keys
    workspace = _workspace(engine, public_key)
    ids = [_enqueue(engine, _new_test(workspace, subject=str(index))) for index in range(12)]
    statuses = _reserve_all(engine, ids, limits)
    assert statuses.count("reserved") == allowed
    assert set(statuses) <= {"reserved", "deferred", "skipped"}


def test_unknown_usage_counts_at_its_reservation(engine: Engine, keys: Any) -> None:
    _keyring, public_key, _ = keys
    workspace = _workspace(engine, public_key)
    now = datetime.now(UTC)
    known, unknown = (_enqueue(engine, _new_test(workspace, subject=s)) for s in ("k", "u"))
    for request_id in (known, unknown):
        ledger.reserve(
            engine,
            request_id,
            reservation=Reservation(1_000, 200, True, None, None),
            limits=AiLimits(),
            now=now,
        )
        ledger.mark_sending(engine, request_id, now, 60)
    with engine.begin() as connection:
        ledger.finish(
            connection,
            known,
            state="succeeded",
            now=now,
            usage=ledger.Usage(10, 5, 3),
            apply_outcome="applied",
        )
        ledger.finish(connection, unknown, state="uncertain", now=now, error_code="timeout")
        summary = ledger.usage_summary(connection, workspace.id, now)
    assert summary["calls_today"] == 2
    assert summary["tokens_today"] == 18 + 1_200
    assert summary["calls_with_estimated_usage"] == 1
    assert [row["id"] for row in summary["uncertain_requests"]] == [unknown]


def test_cache_is_isolated_per_workspace_and_expires(engine: Engine, keys: Any) -> None:
    _keyring, public_key, _ = keys
    first, second = _workspace(engine, public_key), _workspace(engine, public_key)
    key = ai_cache.cache_key(
        hook="h4_vendor",
        model=MODEL,
        prompt_version="h4.prompt.v1",
        schema_version="h4.schema.v1",
        redaction_version="redaction.v1",
        payload_digest="a" * 64,
    )
    now = datetime.now(UTC)
    with engine.begin() as connection:
        ai_cache.store(
            connection,
            workspace_id=first.id,
            key=key,
            hook="h4_vendor",
            model=MODEL,
            prompt_version="h4.prompt.v1",
            document_hash=None,
            normalized_output={"opinion": "same", "reason": "r", "quote": "q"},
        )
        assert (
            ai_cache.lookup(connection, workspace_id=second.id, key=key, now=now, ttl_days=30)
            is None
        )
        hit = ai_cache.lookup(connection, workspace_id=first.id, key=key, now=now, ttl_days=30)
        assert hit is not None and hit[1]["opinion"] == "same"
        assert (
            ai_cache.lookup(
                connection,
                workspace_id=first.id,
                key=key,
                now=now + timedelta(days=31),
                ttl_days=30,
            )
            is None
        )
    assert ai_cache.purge_expired(engine, now=now + timedelta(days=31), ttl_days=30) >= 1
    with engine.connect() as connection:
        assert (
            ai_cache.lookup(connection, workspace_id=first.id, key=key, now=now, ttl_days=30)
            is None
        )


def test_expired_session_keys_are_swept_and_saved_keys_survive(engine: Engine, keys: Any) -> None:
    _keyring, public_key, _ = keys
    session_workspace = _workspace(engine, public_key, session_bound=True)
    saved_workspace = _workspace(engine, public_key)
    with engine.begin() as connection:
        connection.execute(
            sa.text("UPDATE session SET revoked_at = now() WHERE workspace_id = :w"),
            {"w": session_workspace.id},
        )
    assert ledger.sweep_expired_secrets(engine, now=datetime.now(UTC)) >= 1
    with engine.connect() as connection:
        secrets = dict(
            connection.execute(
                sa.text(
                    "SELECT workspace_id, count(*) FROM secret WHERE workspace_id IN (:a, :b) "
                    "GROUP BY workspace_id"
                ),
                {"a": session_workspace.id, "b": saved_workspace.id},
            ).all()
        )
        key_ref = connection.scalar(
            sa.text("SELECT key_secret_id FROM ai_setting WHERE workspace_id = :w"),
            {"w": session_workspace.id},
        )
    assert secrets == {saved_workspace.id: 1}
    assert key_ref is None


def test_keytool_reseals_old_keys_to_the_current_key(
    engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    generate(tmp_path, "old")
    workspace = _workspace(engine, SealingPublicKey.from_file(tmp_path / PUBLIC_KEY_NAME))
    generate(tmp_path, "new")
    rotated = Keyring.load(tmp_path / KEYRING_NAME)
    # Other tests' keys in this shared database were sealed under another keyring
    # and are correctly reported as unreadable; only this workspace's key matters.
    resealed, _unreadable = reseal(rotated, engine.url.render_as_string(hide_password=False))
    assert resealed >= 1
    with engine.connect() as connection:
        row = connection.execute(
            sa.text("SELECT ciphertext, nonce, key_id FROM secret WHERE id = :id"),
            {"id": workspace.secret_id},
        ).one()
    assert row.key_id == "new"
    assert workspace.secret_id is not None
    opened = rotated.open(
        envelope=bytes(row.ciphertext),
        nonce=bytes(row.nonce),
        stored_key_id="new",
        workspace_id=workspace.id,
        secret_id=workspace.secret_id,
    )
    assert opened == SENTINEL_KEY.encode()
    only_new = Keyring(current_key_id="new", private_keys={"new": rotated.private_keys["new"]})
    assert (
        only_new.open(
            envelope=bytes(row.ciphertext),
            nonce=bytes(row.nonce),
            stored_key_id="new",
            workspace_id=workspace.id,
            secret_id=workspace.secret_id,
        )
        == SENTINEL_KEY.encode()
    )
