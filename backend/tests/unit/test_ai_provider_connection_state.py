"""Connection state, reason codes and the provider notice (WI-PV2); no database needed."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from reconbench_app.ai import connection_state
from reconbench_app.ai.provider_notice import (
    FREE_TIER_WARNING,
    notice,
    notice_fingerprint,
)

NOW = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)


def _card(**overrides: object) -> dict[str, object]:
    card: dict[str, object] = {
        "enabled": True,
        "connected_at": None,
        "last_error_code": None,
        "last_error_at": None,
        "last_test_at": None,
        "last_health_at": None,
        "key_rotated_at": None,
        "pending_key_secret_id": None,
    }
    card.update(overrides)
    return card


def test_a_card_is_connected_only_after_a_test_and_not_while_an_error_stands() -> None:
    assert connection_state.derive(None, usable=True)["state"] == "not_connected"
    assert connection_state.derive(_card(), usable=True)["state"] == "not_connected"
    connected = connection_state.derive(_card(connected_at=NOW, last_test_at=NOW), usable=True)
    assert connected["state"] == "connected" and connected["checked_at"] == NOW
    broken = connection_state.derive(
        _card(connected_at=NOW, last_error_code="bad_key", last_error_at=NOW, last_test_at=NOW),
        usable=True,
    )
    assert broken["state"] == "error" and broken["reason_code"] == "bad_key"
    # Whatever it records, a card without what a call needs is Not connected.
    unusable = connection_state.derive(_card(connected_at=NOW, last_test_at=NOW), usable=False)
    assert unusable["state"] == "not_connected" and unusable["reason_code"] is None
    pending = connection_state.derive(_card(pending_key_secret_id=uuid4()), usable=True)
    assert pending["rotation_pending"] is True


def test_reason_codes_are_generic_and_never_provider_text() -> None:
    assert connection_state.reason_code("bad_key") == "bad_key"
    assert connection_state.reason_code("PROVIDER_UNAVAILABLE") == "PROVIDER_UNAVAILABLE"
    assert connection_state.reason_code("some provider message with a key AIza123") == "error"
    assert connection_state.reason_code(None) == "error"


def test_request_kinds_come_from_the_operation_key() -> None:
    secret_id = uuid4()
    rotate = {
        "call_kind": "provider_test",
        "operation_key": connection_state.rotation_operation_key("gemini", secret_id),
    }
    health = {"call_kind": "provider_test", "operation_key": "provider_health:gemini:2026"}
    plain = {"call_kind": "provider_test", "operation_key": "provider_test:2026-10-20"}
    hook = {"call_kind": "hook", "operation_key": "provider_test_rotate:gemini:x"}
    assert connection_state.request_kind(rotate) == "rotate"
    assert connection_state.request_kind(health) == "health"
    assert connection_state.request_kind(plain) == "test"
    assert connection_state.is_rotation_test(rotate) is True
    assert connection_state.is_rotation_test(health) is False
    assert connection_state.is_rotation_test(hook) is False
    assert str(rotate["operation_key"]).rsplit(":", 1)[-1] == str(secret_id)


def test_notice_text_and_fingerprint_follow_the_declarations() -> None:
    free = notice("gemini", tier="free", model="gemini-3.5-flash-lite")
    paid = notice("gemini", tier="paid", model="gemini-3.5-flash-lite")
    assert free["data_use_warning"] is True and FREE_TIER_WARNING in free["text"]
    assert paid["data_use_warning"] is False and FREE_TIER_WARNING not in paid["text"]
    prints = {
        notice_fingerprint("gemini", tier="free", model="gemini-3.5-flash-lite"),
        notice_fingerprint("gemini", tier="paid", model="gemini-3.5-flash-lite"),
        notice_fingerprint("gemini", tier="free", model="gemini-3.8-flash"),
        notice_fingerprint("vllm", tier="free", model="qwen3.5:9b", base_url="http://a.test/v1"),
        notice_fingerprint("vllm", tier="free", model="qwen3.5:9b", base_url="http://b.test/v1"),
    }
    assert len(prints) == 5 and all(len(item) == 64 for item in prints)
    # The tier is not part of the company server's notice.
    assert notice_fingerprint(
        "vllm", tier="free", model="qwen3.5:9b", base_url="http://a.test/v1"
    ) == notice_fingerprint("vllm", tier="paid", model="qwen3.5:9b", base_url="http://a.test/v1")
