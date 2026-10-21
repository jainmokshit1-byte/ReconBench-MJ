"""Connect, test, rotate, disconnect and health of provider cards over HTTP (WI-PV2).

Runs against the single seeded workspace of a disposable PostgreSQL 16 database, with the
harness of ``test_ai_settings_api``. Provider calls go through offline scripted providers
only; no model is called. Keys in this file are made up.
"""

# ruff: noqa: F811 -- the harness fixtures are imported from test_ai_settings_api

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx
import pytest
import sqlalchemy as sa
from reconbench_app.ai import connection_state
from reconbench_app.ai.gate import DeploymentGate
from reconbench_app.ai.limits import AiLimits, LimitsState
from reconbench_app.ai.provider import ProviderFailure, ProviderRequest, ProviderResult
from reconbench_app.ai.provider_notice import FREE_TIER_WARNING
from reconbench_app.ai.stub import ScriptedProvider
from reconbench_app.ai.worker.handlers import default_handlers
from reconbench_app.ai.worker.openai_compat import OpenAICompatProvider
from reconbench_app.ai.worker.runner import AiRequestRunner
from reconbench_app.ai.worker.sweeps import gemini_health, provider_health
from test_ai_settings_api import (  # noqa: F401  (fixtures are registered by import)
    VLLM_MODEL,
    VLLM_URL,
    Harness,
    _audit,
    _vllm_gate,
    harness,
    scratch_dsn,
)

KEY_ONE = "AIzaSyD-SENTINEL-pv2-key-one-00000000"
KEY_TWO = "AIzaSyD-SENTINEL-pv2-key-two-00000000"
KEY_THREE = "AIzaSyD-SENTINEL-pv2-key-three-000000"
LOCAL_KEY = "local-SENTINEL-pv2-vllm-key-0000"
ALL_KEYS = (KEY_ONE, KEY_TWO, KEY_THREE, LOCAL_KEY)
OK = '{"status": "ok"}'
GEMINI = "/api/v1/ai/providers/gemini"
VLLM = "/api/v1/ai/providers/vllm"


def _clear_rate_limit(h: Harness) -> None:
    with h.engine.begin() as connection:
        connection.execute(
            sa.text("DELETE FROM rate_limit_event WHERE workspace_id = :w"), {"w": h.workspace_id}
        )


def _notice(h: Harness, base: str, **params: Any) -> dict[str, Any]:
    response = h.client.get(f"{base}/connect-notice", params=params)
    assert response.status_code == 200, response.text
    return dict(response.json())


def _connect_gemini(
    h: Harness,
    *,
    key: str = KEY_ONE,
    tier: str = "free",
    storage: str = "saved",
    fingerprint: str | None = None,
    extra: dict[str, Any] | None = None,
) -> Any:
    _clear_rate_limit(h)
    shown = _notice(h, GEMINI, tier=tier)
    body: dict[str, Any] = {
        "tier": tier,
        "api_key": key,
        "key_storage": storage,
        "consent": {"accept": True, "fingerprint": fingerprint or shown["fingerprint"]},
        **(extra or {}),
    }
    return h.client.post(f"{GEMINI}/connect", json=body, headers=h.headers())


def _connect_vllm(h: Harness, *, key: str | None = None, base_url: str = VLLM_URL) -> Any:
    _clear_rate_limit(h)
    shown = _notice(h, VLLM, model=VLLM_MODEL, base_url=base_url)
    body: dict[str, Any] = {
        "model": VLLM_MODEL,
        "base_url": base_url,
        "consent": {"accept": True, "fingerprint": shown["fingerprint"]},
    }
    if key is not None:
        body["api_key"] = key
        body["key_storage"] = "saved"
    return h.client.post(f"{VLLM}/connect", json=body, headers=h.headers())


def _card(h: Harness, name: str) -> dict[str, Any]:
    cards = {item["provider"]: item for item in h.get()["providers"]}
    return dict(cards[name])


def _state(h: Harness, name: str) -> str:
    return str(_card(h, name)["connection"]["state"])


def _row(h: Harness, name: str) -> Any:
    with h.engine.connect() as connection:
        return (
            connection.execute(
                sa.text(
                    "SELECT * FROM ai_provider_config WHERE workspace_id = :w "
                    "AND provider = CAST(:p AS ai_provider)"
                ),
                {"w": h.workspace_id, "p": name},
            )
            .mappings()
            .first()
        )


def _gemini_run(h: Harness, request_id: str, *outputs: str | ProviderFailure) -> ScriptedProvider:
    provider = ScriptedProvider(list(outputs))
    result = h.runner(provider).run(UUID(request_id))
    assert result.state in {"succeeded", "failed", "released", "skipped"}, result
    return provider


def _vllm_runner(h: Harness, handler: Callable[[httpx.Request], httpx.Response]) -> AiRequestRunner:
    provider = OpenAICompatProvider(
        VLLM_URL, frozenset({VLLM_URL}), inner_transport=httpx.MockTransport(handler)
    )
    return AiRequestRunner(
        engine=h.engine,
        keyring=h.keyring,
        gate=DeploymentGate.from_values(
            "disabled", None, vllm_mode="synthetic_only", vllm_endpoints=VLLM_URL
        ),
        limits=lambda: LimitsState(AiLimits()),
        prices=lambda: None,
        providers={"vllm": provider},
        handlers=default_handlers(),
    )


def _vllm_ok(request: httpx.Request) -> httpx.Response:
    if request.method == "GET":
        return httpx.Response(200, json={"data": [{"id": VLLM_MODEL, "max_model_len": 8192}]})
    return httpx.Response(
        200,
        json={
            "model": VLLM_MODEL,
            "choices": [{"message": {"content": OK}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 40, "completion_tokens": 4},
        },
    )


def _no_key_anywhere(h: Harness, response_texts: list[str]) -> None:
    """None of the made-up keys appears in a response, an audit row, a request or settings."""

    haystack = list(response_texts)
    with h.engine.connect() as connection:
        for table in ("audit_log", "ai_request", "ai_setting", "ai_provider_config", "ai_call_log"):
            for row in connection.execute(
                sa.text(f"SELECT * FROM {table} WHERE workspace_id = :w"), {"w": h.workspace_id}
            ).mappings():
                haystack.append(json.dumps(dict(row), default=str))
    for key in ALL_KEYS:
        assert all(key not in text for text in haystack), key


# ---- notice and consent --------------------------------------------------------------------


def test_free_tier_notice_states_the_data_use_and_is_part_of_the_fingerprint(
    harness: Harness,
) -> None:
    free = _notice(harness, GEMINI, tier="free")
    paid = _notice(harness, GEMINI, tier="paid")
    assert free["data_use_warning"] is True and FREE_TIER_WARNING in free["text"]
    assert "improve its products" in free["text"] and "human reviewers may read" in free["text"]
    assert paid["data_use_warning"] is False and "improve its products" not in paid["text"]
    # The tier is declared, so it is in the fingerprint; so are the model and the server.
    other_model = _notice(harness, GEMINI, tier="free", model="gemini-3.8-flash")
    assert len({free["fingerprint"], paid["fingerprint"], other_model["fingerprint"]}) == 3
    assert all(len(item["fingerprint"]) == 64 for item in (free, paid, other_model))
    missing = harness.client.get(f"{GEMINI}/connect-notice")
    assert missing.status_code == 422 and missing.json()["code"] == "tier_required"
    _vllm_gate(harness)
    company = _notice(harness, VLLM, model=VLLM_MODEL, base_url=VLLM_URL)
    assert company["data_use_warning"] is False and "company network" in company["text"]
    assert company["tier"] is None


def test_connect_needs_the_notice_tier_and_a_valid_key_and_saves_nothing_on_refusal(
    harness: Harness,
) -> None:
    before = harness.get()
    no_consent = harness.client.post(
        f"{GEMINI}/connect", json={"tier": "free", "api_key": KEY_ONE}, headers=harness.headers()
    )
    assert no_consent.status_code == 422
    wrong = _connect_gemini(harness, fingerprint="0" * 64)
    assert wrong.status_code == 409 and wrong.json()["code"] == "consent_mismatch"
    # A notice accepted for the free tier does not cover a key declared as paid.
    shown = _notice(harness, GEMINI, tier="free")
    _clear_rate_limit(harness)
    paid_with_free_notice = harness.client.post(
        f"{GEMINI}/connect",
        json={
            "tier": "paid",
            "api_key": KEY_ONE,
            "consent": {"accept": True, "fingerprint": shown["fingerprint"]},
        },
        headers=harness.headers(),
    )
    assert paid_with_free_notice.status_code == 409
    no_tier = harness.client.post(
        f"{GEMINI}/connect",
        json={"api_key": KEY_ONE, "consent": {"accept": True, "fingerprint": shown["fingerprint"]}},
        headers=harness.headers(),
    )
    assert no_tier.status_code == 422 and no_tier.json()["code"] == "tier_required"
    paid = _connect_gemini(harness, tier="paid")
    assert paid.status_code == 422 and paid.json()["code"] == "price_not_configured"
    bad_key = _connect_gemini(harness, key="short")
    assert bad_key.status_code == 422 and bad_key.json()["code"] == "invalid_key"
    assert "short" not in bad_key.text
    with_url = _connect_gemini(harness, extra={"base_url": "http://x.example.test/v1"})
    assert with_url.status_code == 422 and with_url.json()["code"] == "invalid_base_url"
    # Nothing was saved by any refusal.
    after = harness.get()
    assert after["version"] == before["version"] and after["key"]["set"] is False
    assert _row(harness, "gemini") is None
    with harness.engine.connect() as connection:
        assert not connection.scalar(
            sa.text("SELECT count(*) FROM ai_request WHERE workspace_id = :w"),
            {"w": harness.workspace_id},
        )
        assert not connection.scalar(
            sa.text("SELECT count(*) FROM secret WHERE workspace_id = :w"),
            {"w": harness.workspace_id},
        )


def test_connect_is_refused_when_the_deployment_gate_is_off(harness: Harness) -> None:
    service = harness.app.state.ai_settings_service
    from dataclasses import replace

    service.config = replace(service.config, gate=DeploymentGate.from_values("disabled", None))
    response = _connect_gemini(harness)
    assert response.status_code == 409 and response.json()["code"] == "external_calls_disabled"
    assert harness.get()["key"]["set"] is False


# ---- connected only after a passing test ----------------------------------------------------


def test_connected_only_after_a_passing_test_and_the_consent_is_recorded(
    harness: Harness,
) -> None:
    response = _connect_gemini(harness)
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["state"] == "queued"
    # Saved but not tested: Not connected, the key is write-only, the tier is declared.
    view = harness.get()
    card = _card(harness, "gemini")
    assert card["connection"]["state"] == "not_connected"
    assert card["tier"] == "free" and view["key"]["set"] is True
    assert card["consent"]["accepted"] is True and card["consent"]["current"] is True
    row = _row(harness, "gemini")
    assert row["key_tier"] == "free" and row["connected_at"] is None
    assert row["consent_accepted_by"] == harness.user_id and row["consent_accepted_at"]
    # The key lives on the card, not on ai_setting.
    with harness.engine.connect() as connection:
        assert (
            connection.scalar(
                sa.text("SELECT key_secret_id FROM ai_setting WHERE workspace_id = :w"),
                {"w": harness.workspace_id},
            )
            is None
        )
    provider = _gemini_run(harness, body["request_id"], OK)
    assert provider.keys_seen == [KEY_ONE]
    assert _state(harness, "gemini") == "connected"
    done = harness.client.get(f"/api/v1/ai/requests/{body['request_id']}").json()
    assert done["request_state"] == "succeeded" and done["apply_outcome"] == "applied"
    connection_block = _card(harness, "gemini")["connection"]
    assert connection_block["reason_code"] is None and connection_block["checked_at"]
    audit = _audit(harness, "ai.provider_connect")[-1]
    assert audit["after_state"]["tier"] == "free" and audit["after_state"]["key_set"] is True
    assert (
        audit["after_state"]["consent_fingerprint"]
        == _notice(harness, GEMINI, tier="free")["fingerprint"]
    )
    _no_key_anywhere(harness, [response.text, json.dumps(harness.get())])


def test_a_failing_test_is_an_error_with_a_generic_reason_and_a_pass_recovers(
    harness: Harness,
) -> None:
    request_id = _connect_gemini(harness).json()["request_id"]
    _gemini_run(harness, request_id, ProviderFailure("bad_key", delivery="responded"))
    block = _card(harness, "gemini")["connection"]
    assert block["state"] == "error" and block["reason_code"] == "bad_key"
    # No provider text reaches the card; only the generic code.
    assert set(block) == {
        "state",
        "reason_code",
        "checked_at",
        "connected_at",
        "error_at",
        "key_rotated_at",
        "rotation_pending",
    }
    retry = harness.client.post(
        "/api/v1/ai/test/provider", json={"provider": "gemini"}, headers=harness.headers()
    )
    assert retry.status_code == 202
    _gemini_run(harness, retry.json()["request_id"], OK)
    assert _state(harness, "gemini") == "connected"
    assert _card(harness, "gemini")["connection"]["reason_code"] is None


def test_any_change_to_the_card_puts_it_back_to_not_connected(harness: Harness) -> None:
    _gemini_run(harness, _connect_gemini(harness).json()["request_id"], OK)
    assert _state(harness, "gemini") == "connected"
    changed = harness.put({"model": "gemini-3.8-flash"})
    assert changed.status_code == 200, changed.text
    assert _state(harness, "gemini") == "not_connected"
    # Connecting again (new key, same card) also starts from Not connected.
    _gemini_run(harness, _connect_gemini(harness, key=KEY_TWO).json()["request_id"], OK)
    assert _state(harness, "gemini") == "connected"
    _connect_gemini(harness, key=KEY_THREE)
    assert _state(harness, "gemini") == "not_connected"


# ---- rotation: test then swap ------------------------------------------------------------


def test_rotate_with_a_bad_key_keeps_the_old_connection_and_key(harness: Harness) -> None:
    _gemini_run(harness, _connect_gemini(harness).json()["request_id"], OK)
    live = _row(harness, "gemini")["key_secret_id"]
    _clear_rate_limit(harness)
    rotation = harness.client.post(
        f"{GEMINI}/rotate", json={"api_key": KEY_TWO}, headers=harness.headers()
    )
    assert rotation.status_code == 202, rotation.text
    # While the candidate waits, the old key is still the live one and the card stays Connected.
    waiting = _row(harness, "gemini")
    assert waiting["key_secret_id"] == live and waiting["pending_key_secret_id"] is not None
    assert _state(harness, "gemini") == "connected"
    assert _card(harness, "gemini")["connection"]["rotation_pending"] is True
    provider = _gemini_run(
        harness, rotation.json()["request_id"], ProviderFailure("bad_key", delivery="responded")
    )
    assert provider.keys_seen == [KEY_TWO]  # the candidate was the key tested
    after = _row(harness, "gemini")
    assert after["key_secret_id"] == live and after["pending_key_secret_id"] is None
    assert after["key_rotated_at"] is None
    assert _state(harness, "gemini") == "connected"
    with harness.engine.connect() as connection:
        assert (
            connection.scalar(
                sa.text("SELECT count(*) FROM secret WHERE workspace_id = :w"),
                {"w": harness.workspace_id},
            )
            == 1
        )
    failed = harness.client.get(f"/api/v1/ai/requests/{rotation.json()['request_id']}").json()
    assert failed["error_code"] == "bad_key"
    # The old key still works for a normal call.
    plain = harness.client.post(
        "/api/v1/ai/test/provider", json={"provider": "gemini"}, headers=harness.headers()
    )
    assert _gemini_run(harness, plain.json()["request_id"], OK).keys_seen == [KEY_ONE]


def test_rotate_with_a_good_key_swaps_atomically_and_deletes_the_old_key(
    harness: Harness,
) -> None:
    _gemini_run(harness, _connect_gemini(harness).json()["request_id"], OK)
    old = _row(harness, "gemini")["key_secret_id"]
    _clear_rate_limit(harness)
    rotation = harness.client.post(
        f"{GEMINI}/rotate", json={"api_key": KEY_TWO}, headers=harness.headers()
    )
    assert rotation.status_code == 202
    pending = _row(harness, "gemini")["pending_key_secret_id"]
    assert _gemini_run(harness, rotation.json()["request_id"], OK).keys_seen == [KEY_TWO]
    after = _row(harness, "gemini")
    assert after["key_secret_id"] == pending and after["pending_key_secret_id"] is None
    assert after["key_rotated_at"] is not None
    assert _state(harness, "gemini") == "connected"
    with harness.engine.connect() as connection:
        ids = set(
            connection.scalars(
                sa.text("SELECT id FROM secret WHERE workspace_id = :w"),
                {"w": harness.workspace_id},
            )
        )
    assert ids == {pending} and old not in ids
    plain = harness.client.post(
        "/api/v1/ai/test/provider", json={"provider": "gemini"}, headers=harness.headers()
    )
    assert _gemini_run(harness, plain.json()["request_id"], OK).keys_seen == [KEY_TWO]
    assert _audit(harness, "ai.provider_rotate")
    _no_key_anywhere(harness, [rotation.text, json.dumps(harness.get())])


def test_rotate_needs_a_connected_card_and_a_valid_key(harness: Harness) -> None:
    nothing = harness.client.post(
        f"{GEMINI}/rotate", json={"api_key": KEY_TWO}, headers=harness.headers()
    )
    assert nothing.status_code == 409 and nothing.json()["code"] == "not_connected"
    _gemini_run(harness, _connect_gemini(harness).json()["request_id"], OK)
    bad = harness.client.post(
        f"{GEMINI}/rotate", json={"api_key": "short"}, headers=harness.headers()
    )
    assert bad.status_code == 422 and bad.json()["code"] == "invalid_key"
    assert _row(harness, "gemini")["pending_key_secret_id"] is None


def test_a_candidate_nobody_tested_is_dropped_by_the_sweep(harness: Harness) -> None:
    _gemini_run(harness, _connect_gemini(harness).json()["request_id"], OK)
    _clear_rate_limit(harness)
    harness.client.post(f"{GEMINI}/rotate", json={"api_key": KEY_TWO}, headers=harness.headers())
    now = datetime.now(UTC)
    assert connection_state.clear_stale_rotations(harness.engine, now=now) == 0
    assert connection_state.clear_stale_rotations(harness.engine, now=now + timedelta(hours=2)) == 1
    row = _row(harness, "gemini")
    assert row["pending_key_secret_id"] is None and row["key_secret_id"] is not None
    assert _state(harness, "gemini") == "connected"


# ---- disconnect ---------------------------------------------------------------------------


def test_disconnect_deletes_the_keys_records_who_and_leaves_the_selection(
    harness: Harness,
) -> None:
    _gemini_run(harness, _connect_gemini(harness).json()["request_id"], OK)
    harness.put({"mode": "ai_assisted", "provider": "gemini", "consent": _legacy_consent(harness)})
    response = harness.client.post(f"{GEMINI}/disconnect", headers=harness.headers())
    assert response.status_code == 200, response.text
    view = response.json()
    card = {item["provider"]: item for item in view["providers"]}["gemini"]
    assert card["connection"]["state"] == "not_connected" and view["key"]["set"] is False
    assert card["consent"]["accepted"] is False
    # The selection still names the provider; there is no effective provider.
    assert view["provider"] == "gemini" and view["status"]["code"] in {
        "CONSENT_OUTDATED",
        "KEY_REMOVED",
        "KEY_NOT_SET",
    }
    with harness.engine.connect() as connection:
        assert not connection.scalar(
            sa.text("SELECT count(*) FROM secret WHERE workspace_id = :w"),
            {"w": harness.workspace_id},
        )
    event = _audit(harness, "ai.provider_disconnect")[-1]
    assert event["before_state"]["key_set"] is True and event["after_state"]["key_set"] is False
    with harness.engine.connect() as connection:
        actor = connection.scalar(
            sa.text(
                "SELECT actor_id FROM audit_log WHERE workspace_id = :w "
                "AND action = 'ai.provider_disconnect' ORDER BY seq DESC LIMIT 1"
            ),
            {"w": harness.workspace_id},
        )
    assert actor == harness.user_id
    again = harness.client.post(f"{GEMINI}/disconnect", headers=harness.headers())
    assert again.status_code == 200  # idempotent: nothing left to delete
    _no_key_anywhere(harness, [response.text])


def _legacy_consent(h: Harness) -> dict[str, Any]:
    from reconbench_app.ai import consent as ai_consent

    model = str(h.get()["model"])
    return {
        "accept": True,
        "fingerprint": ai_consent.consent_fingerprint(
            provider="gemini", model=model, mode="ai_assisted"
        ),
    }


def test_disconnect_between_queue_and_run_sends_nothing(harness: Harness) -> None:
    _gemini_run(harness, _connect_gemini(harness).json()["request_id"], OK)
    queued = harness.client.post(
        "/api/v1/ai/test/provider", json={"provider": "gemini"}, headers=harness.headers()
    )
    harness.client.post(f"{GEMINI}/disconnect", headers=harness.headers())
    provider = ScriptedProvider([OK])
    result = harness.runner(provider).run(UUID(queued.json()["request_id"]))
    assert result.state == "skipped" and provider.calls == []
    assert _state(harness, "gemini") == "not_connected"


def test_disconnect_while_a_call_is_in_flight_discards_the_answer(harness: Harness) -> None:
    _gemini_run(harness, _connect_gemini(harness).json()["request_id"], OK)
    queued = harness.client.post(
        "/api/v1/ai/test/provider", json={"provider": "gemini"}, headers=harness.headers()
    )

    class DisconnectsMidCall(ScriptedProvider):
        def complete(self, request: ProviderRequest, *, api_key: str) -> ProviderResult:
            outcome = harness.client.post(f"{GEMINI}/disconnect", headers=harness.headers())
            assert outcome.status_code == 200
            return super().complete(request, api_key=api_key)

    provider = DisconnectsMidCall([OK])
    result = harness.runner(provider).run(UUID(queued.json()["request_id"]))
    assert provider.keys_seen == [KEY_ONE]
    assert (result.state, result.apply_outcome) == ("succeeded", "discarded_revoked"), result
    # No partial rows: the answer changed nothing, no key is left, no Connected state.
    row = _row(harness, "gemini")
    assert row["key_secret_id"] is None and row["connected_at"] is None
    assert row["last_test_ok"] is None
    with harness.engine.connect() as connection:
        assert (
            connection.scalar(
                sa.text("SELECT last_test_ok FROM ai_setting WHERE workspace_id = :w"),
                {"w": harness.workspace_id},
            )
            is None
        )
        assert not connection.scalar(
            sa.text("SELECT count(*) FROM secret WHERE workspace_id = :w"),
            {"w": harness.workspace_id},
        )
    assert _state(harness, "gemini") == "not_connected"


def test_a_pending_rotation_is_discarded_by_disconnect(harness: Harness) -> None:
    _gemini_run(harness, _connect_gemini(harness).json()["request_id"], OK)
    _clear_rate_limit(harness)
    rotation = harness.client.post(
        f"{GEMINI}/rotate", json={"api_key": KEY_TWO}, headers=harness.headers()
    )
    harness.client.post(f"{GEMINI}/disconnect", headers=harness.headers())
    provider = ScriptedProvider([OK])
    harness.runner(provider).run(UUID(rotation.json()["request_id"]))
    assert provider.calls == []
    row = _row(harness, "gemini")
    assert row["key_secret_id"] is None and row["pending_key_secret_id"] is None


# ---- health --------------------------------------------------------------------------------


def test_gemini_health_runs_at_most_hourly_and_flips_the_status_in_one_cycle(
    harness: Harness,
) -> None:
    _gemini_run(harness, _connect_gemini(harness).json()["request_id"], OK)
    failing = ScriptedProvider([ProviderFailure("PROVIDER_UNAVAILABLE", delivery="responded")])
    runner = harness.runner(failing)
    now = datetime.now(UTC)
    # A fresh connection tested moments ago has no health result yet: one request runs.
    assert gemini_health(harness.engine, runner.run, now) == 1
    assert _state(harness, "gemini") == "error"
    assert _card(harness, "gemini")["connection"]["reason_code"] == "PROVIDER_UNAVAILABLE"
    assert _card(harness, "gemini")["health"]["ok"] is False
    # Within the hour nothing is sent, however often the sweep wakes up.
    assert gemini_health(harness.engine, runner.run, now + timedelta(minutes=10)) == 0
    assert len(failing.calls) == 1
    # One cycle later the provider is back: Error returns to Connected.
    recovered = ScriptedProvider([OK])
    assert (
        gemini_health(harness.engine, harness.runner(recovered).run, now + timedelta(hours=2)) == 1
    )
    assert _state(harness, "gemini") == "connected"
    assert _card(harness, "gemini")["health"]["ok"] is True
    # Health requests are ledger rows (counted in the day's requests), never document data.
    with harness.engine.connect() as connection:
        kinds = connection.scalars(
            sa.text(
                "SELECT call_kind::text FROM ai_request WHERE workspace_id = :w "
                "AND operation_key LIKE 'provider_health:%'"
            ),
            {"w": harness.workspace_id},
        ).all()
    assert kinds == ["provider_test", "provider_test"]


def test_health_never_makes_a_never_tested_card_connected(harness: Harness) -> None:
    _connect_gemini(harness)  # saved, never tested
    provider = ScriptedProvider([OK])
    assert gemini_health(harness.engine, harness.runner(provider).run, datetime.now(UTC)) == 0
    assert provider.calls == [] and _state(harness, "gemini") == "not_connected"


def test_company_server_health_flips_connected_and_error(harness: Harness) -> None:
    _vllm_gate(harness, mode="synthetic_only")
    response = _connect_vllm(harness)
    assert response.status_code == 202, response.text
    assert _state(harness, "vllm") == "not_connected"
    result = _vllm_runner(harness, _vllm_ok).run(UUID(response.json()["request_id"]))
    assert (result.state, result.apply_outcome) == ("succeeded", "applied"), result
    assert _state(harness, "vllm") == "connected"
    down = OpenAICompatProvider(
        VLLM_URL,
        frozenset({VLLM_URL}),
        inner_transport=httpx.MockTransport(lambda request: httpx.Response(503)),
    )
    now = datetime.now(UTC)
    assert provider_health(harness.engine, down, now) == 1
    assert _state(harness, "vllm") == "error"
    up = OpenAICompatProvider(
        VLLM_URL, frozenset({VLLM_URL}), inner_transport=httpx.MockTransport(_vllm_ok)
    )
    assert provider_health(harness.engine, up, now + timedelta(minutes=5)) == 1
    assert _state(harness, "vllm") == "connected"


# ---- both providers, the same endpoints ----------------------------------------------------


@pytest.mark.parametrize("provider_name", ["gemini", "vllm"])
def test_both_providers_connect_rotate_and_disconnect_through_the_same_endpoints(
    harness: Harness, provider_name: str
) -> None:
    _vllm_gate(harness, mode="synthetic_only")
    first_key = KEY_ONE if provider_name == "gemini" else LOCAL_KEY
    second_key = KEY_TWO if provider_name == "gemini" else LOCAL_KEY + "-2"
    if provider_name == "gemini":
        response = _connect_gemini(harness, key=first_key)
    else:
        response = _connect_vllm(harness, key=first_key)
    assert response.status_code == 202, response.text

    def run(request_id: str, *gemini_outputs: str | ProviderFailure) -> list[str]:
        if provider_name == "gemini":
            return _gemini_run(harness, request_id, *gemini_outputs).keys_seen
        seen: list[str] = []

        def server(request: httpx.Request) -> httpx.Response:
            seen.append(request.headers.get("authorization", ""))
            return _vllm_ok(request)

        _vllm_runner(harness, server).run(UUID(request_id))
        return seen

    assert _state(harness, provider_name) == "not_connected"
    run(response.json()["request_id"], OK)
    assert _state(harness, provider_name) == "connected"
    _clear_rate_limit(harness)
    rotation = harness.client.post(
        f"/api/v1/ai/providers/{provider_name}/rotate",
        json={"api_key": second_key},
        headers=harness.headers(),
    )
    assert rotation.status_code == 202, rotation.text
    seen = run(rotation.json()["request_id"], OK)
    assert seen and second_key in seen[-1] or seen == [second_key]
    assert _state(harness, provider_name) == "connected"
    assert _row(harness, provider_name)["key_rotated_at"] is not None
    gone = harness.client.post(
        f"/api/v1/ai/providers/{provider_name}/disconnect", headers=harness.headers()
    )
    assert gone.status_code == 200
    assert _state(harness, provider_name) == "not_connected"
    with harness.engine.connect() as connection:
        assert not connection.scalar(
            sa.text("SELECT count(*) FROM secret WHERE workspace_id = :w"),
            {"w": harness.workspace_id},
        )
    _no_key_anywhere(harness, [response.text, rotation.text, gone.text])


def test_company_server_tier_and_url_rules(harness: Harness) -> None:
    _vllm_gate(harness, mode="synthetic_only")
    shown = _notice(harness, VLLM, model=VLLM_MODEL, base_url=VLLM_URL)
    consent = {"accept": True, "fingerprint": shown["fingerprint"]}
    with_tier = harness.client.post(
        f"{VLLM}/connect",
        json={"tier": "free", "model": VLLM_MODEL, "base_url": VLLM_URL, "consent": consent},
        headers=harness.headers(),
    )
    assert with_tier.status_code == 422 and with_tier.json()["code"] == "invalid_tier"
    elsewhere = "http://elsewhere.example.test/v1"
    refused_notice = harness.client.get(
        f"{VLLM}/connect-notice", params={"model": VLLM_MODEL, "base_url": elsewhere}
    )
    assert refused_notice.status_code == 422
    assert refused_notice.json()["code"] == "endpoint_not_allowed"
    outside = harness.client.post(
        f"{VLLM}/connect",
        json={"model": VLLM_MODEL, "base_url": elsewhere, "consent": consent},
        headers=harness.headers(),
    )
    assert outside.status_code == 422 and outside.json()["code"] == "endpoint_not_allowed"
    assert _row(harness, "vllm") is None


def test_the_legacy_key_location_is_read_until_the_card_has_a_key(harness: Harness) -> None:
    """A workspace not yet moved (key on ai_setting) still works and rotates onto its card."""

    _gemini_run(harness, _connect_gemini(harness).json()["request_id"], OK)
    with harness.engine.begin() as connection:
        connection.execute(
            sa.text(
                "UPDATE ai_setting SET key_secret_id = (SELECT key_secret_id FROM "
                "ai_provider_config WHERE workspace_id = :w AND provider = 'gemini') "
                "WHERE workspace_id = :w"
            ),
            {"w": harness.workspace_id},
        )
        connection.execute(
            sa.text(
                "UPDATE ai_provider_config SET key_secret_id = NULL WHERE workspace_id = :w "
                "AND provider = 'gemini'"
            ),
            {"w": harness.workspace_id},
        )
    assert harness.get()["key"]["set"] is True
    plain = harness.client.post(
        "/api/v1/ai/test/provider", json={"provider": "gemini"}, headers=harness.headers()
    )
    assert _gemini_run(harness, plain.json()["request_id"], OK).keys_seen == [KEY_ONE]
    _clear_rate_limit(harness)
    rotation = harness.client.post(
        f"{GEMINI}/rotate", json={"api_key": KEY_TWO}, headers=harness.headers()
    )
    assert rotation.status_code == 202, rotation.text
    assert _gemini_run(harness, rotation.json()["request_id"], OK).keys_seen == [KEY_TWO]
    row = _row(harness, "gemini")
    assert row["key_secret_id"] is not None
    with harness.engine.connect() as connection:
        assert (
            connection.scalar(
                sa.text("SELECT key_secret_id FROM ai_setting WHERE workspace_id = :w"),
                {"w": harness.workspace_id},
            )
            is None
        )
        assert (
            connection.scalar(
                sa.text("SELECT count(*) FROM secret WHERE workspace_id = :w"),
                {"w": harness.workspace_id},
            )
            == 1
        )


def test_connecting_while_ai_is_on_keeps_the_workspace_consent_and_refuses_a_model_change(
    harness: Harness,
) -> None:
    from test_ai_settings_api import MODEL, _enable

    view = _enable(harness)
    assert view["consent"]["current"] is True
    # The key a normal settings save stores is on the Gemini card, not on ai_setting.
    assert _row(harness, "gemini")["key_secret_id"] is not None
    other = _connect_gemini(harness, extra={"model": "gemini-3.5-flash-lite"})
    assert other.status_code == 409 and other.json()["code"] == "model_change_via_settings"
    same = _connect_gemini(harness, key=KEY_TWO)
    assert same.status_code == 202, same.text
    after = harness.get()
    assert after["model"] == MODEL and after["mode"] == "ai_assisted"
    assert after["consent"]["current"] is True
    assert _card(harness, "gemini")["connection"]["state"] == "not_connected"
    _gemini_run(harness, same.json()["request_id"], OK)
    assert _state(harness, "gemini") == "connected"
    with harness.engine.connect() as connection:
        assert (
            connection.scalar(
                sa.text("SELECT count(*) FROM secret WHERE workspace_id = :w"),
                {"w": harness.workspace_id},
            )
            == 1
        )
