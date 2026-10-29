from __future__ import annotations

import inspect
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from procrastinate import App, testing
from pydantic import ValidationError
from reconbench_app import jobs
from reconbench_app.ai import key_seal
from reconbench_app.ai.fixtures import (
    PROVIDER_TEST,
    SYNTHETIC_FIXTURE_DIGESTS,
    SYNTHETIC_FIXTURES,
    plain_schema,
)
from reconbench_app.ai.gate import (
    SKIP_EXTERNAL_CALLS_DISABLED,
    SKIP_SYNTHETIC_ONLY,
    SKIP_UNKNOWN_FIXTURE,
    DeploymentGate,
    GateConfigurationError,
    payload_digest,
)
from reconbench_app.ai.key_seal import SealingPublicKey, key_hint, seal
from reconbench_app.ai.task_contracts import (
    AI_QUEUE,
    RUN_AI_REQUEST,
    AiTaskNotExecutableHere,
    register_ai_task_declarations,
)
from reconbench_app.ai.worker.config import AiWorkerStartupError, check_startup_environment
from reconbench_app.ai.worker.key_open import Keyring, KeyringError, KeyUnreadable
from reconbench_app.ai.worker.keytool import KEYRING_NAME, PUBLIC_KEY_NAME, generate
from reconbench_app.logging_config import redact_sensitive_fields
from reconbench_app.settings import Settings

SENTINEL_KEY = "AIzaSyD-synthetic-sentinel-key-0000000000"
WORKSPACE = UUID("00000000-0000-0000-0000-00000000a001")
SECRET = UUID("00000000-0000-0000-0000-00000000b001")


@pytest.fixture
def keyring(tmp_path: Path) -> Keyring:
    generate(tmp_path, "k1")
    return Keyring.load(tmp_path / KEYRING_NAME)


def _seal(public_key: SealingPublicKey, **overrides: Any) -> key_seal.SealedSecret:
    arguments: dict[str, Any] = {"workspace_id": WORKSPACE, "secret_id": SECRET}
    arguments.update(overrides)
    return seal(SENTINEL_KEY.encode(), public_key=public_key, **arguments)


def _open(keyring: Keyring, sealed: key_seal.SealedSecret, **overrides: Any) -> bytes:
    arguments: dict[str, Any] = {
        "envelope": sealed.envelope,
        "nonce": sealed.nonce,
        "stored_key_id": sealed.key_id,
        "workspace_id": WORKSPACE,
        "secret_id": SECRET,
    }
    arguments.update(overrides)
    return keyring.open(**arguments)


def test_seal_and_open_round_trip_with_the_public_key_file(tmp_path: Path) -> None:
    generate(tmp_path, "k1")
    public_key = SealingPublicKey.from_file(tmp_path / PUBLIC_KEY_NAME)
    sealed = _seal(public_key)
    assert sealed.key_id == "k1"
    assert SENTINEL_KEY.encode() not in sealed.envelope
    assert _open(Keyring.load(tmp_path / KEYRING_NAME), sealed) == SENTINEL_KEY.encode()


def test_each_seal_uses_a_fresh_ephemeral_key_and_nonce(keyring: Keyring) -> None:
    first, second = _seal(keyring.public_key()), _seal(keyring.public_key())
    assert first.envelope != second.envelope
    assert first.nonce != second.nonce


@pytest.mark.parametrize(
    "overrides",
    [
        {"workspace_id": uuid4()},
        {"secret_id": uuid4()},
        {"purpose": "other_purpose"},
        {"stored_key_id": "k2"},
        {"nonce": b"\x00" * 12},
        {"nonce": b"\x00" * 8},
    ],
)
def test_open_fails_closed_for_wrong_context(keyring: Keyring, overrides: dict[str, Any]) -> None:
    sealed = _seal(keyring.public_key())
    with pytest.raises(KeyUnreadable) as caught:
        _open(keyring, sealed, **overrides)
    assert SENTINEL_KEY not in str(caught.value)


def test_open_fails_with_a_different_private_key(tmp_path: Path, keyring: Keyring) -> None:
    other_dir = tmp_path / "other"
    generate(other_dir, "k1")
    other = Keyring.load(other_dir / KEYRING_NAME)
    with pytest.raises(KeyUnreadable):
        _open(other, _seal(keyring.public_key()))


@pytest.mark.parametrize("position", [0, 1, 5, 40, -1])
def test_tampered_envelopes_are_unreadable(keyring: Keyring, position: int) -> None:
    sealed = _seal(keyring.public_key())
    envelope = bytearray(sealed.envelope)
    envelope[position] ^= 0x01
    with pytest.raises(KeyUnreadable):
        _open(keyring, sealed, envelope=bytes(envelope))
    with pytest.raises(KeyUnreadable):
        _open(keyring, sealed, envelope=sealed.envelope[:20])


def test_rotation_keeps_old_keys_openable_and_seals_with_the_new_id(tmp_path: Path) -> None:
    generate(tmp_path, "k1")
    old_sealed = _seal(SealingPublicKey.from_file(tmp_path / PUBLIC_KEY_NAME))
    generate(tmp_path, "k2")
    public_key = SealingPublicKey.from_file(tmp_path / PUBLIC_KEY_NAME)
    assert public_key.key_id == "k2"
    rotated = Keyring.load(tmp_path / KEYRING_NAME)
    assert rotated.current_key_id == "k2"
    assert set(rotated.private_keys) == {"k1", "k2"}
    assert _open(rotated, old_sealed) == SENTINEL_KEY.encode()
    assert _open(rotated, _seal(public_key)) == SENTINEL_KEY.encode()
    with pytest.raises(KeyringError):
        generate(tmp_path, "k2")


def test_public_key_file_contains_no_private_material(tmp_path: Path) -> None:
    generate(tmp_path, "k1")
    public_document = json.loads((tmp_path / PUBLIC_KEY_NAME).read_text(encoding="utf-8"))
    keyring_document = json.loads((tmp_path / KEYRING_NAME).read_text(encoding="utf-8"))
    assert set(public_document) == {"format", "key_id", "public_key"}
    assert keyring_document["keys"]["k1"] not in json.dumps(public_document)


@pytest.mark.parametrize(
    "document",
    [
        "not json",
        {"format": "other"},
        {"format": "reconbench-x25519-keyring.v1", "current": "k9", "keys": {"k1": "AAAA"}},
        {"format": "reconbench-x25519-keyring.v1", "current": "k1", "keys": {}},
    ],
)
def test_bad_keyrings_refuse_to_load(tmp_path: Path, document: object) -> None:
    path = tmp_path / "keyring.json"
    path.write_text(document if isinstance(document, str) else json.dumps(document))
    with pytest.raises(KeyringError):
        Keyring.load(path)
    with pytest.raises(KeyringError):
        Keyring.load(tmp_path / "missing.json")


def test_api_side_sealing_module_cannot_open_keys() -> None:
    public_names = set(key_seal.__all__)
    assert not {name for name in public_names if "open" in name.lower()}
    assert "AESGCM(aes_key).decrypt" not in inspect.getsource(key_seal)
    assert "ai_keyring_file" not in Settings.model_fields


def test_key_hint_never_reveals_more_than_four_characters() -> None:
    assert key_hint(SENTINEL_KEY) == "****" + SENTINEL_KEY[-4:]
    assert key_hint("short") == "****"


def test_fixture_digest_allowlist_is_compiled_and_current() -> None:
    assert set(SYNTHETIC_FIXTURE_DIGESTS) == set(SYNTHETIC_FIXTURES)
    for fixture_id, fixture in SYNTHETIC_FIXTURES.items():
        assert SYNTHETIC_FIXTURE_DIGESTS[fixture_id] == payload_digest(
            system_instruction=fixture.system_instruction,
            input_text=fixture.input_text,
            response_schema=plain_schema(fixture),
            images=[
                (image.caption, image.mime_type, image.sha256, image.resolution)
                for image in fixture.images
            ],
        )


def _provider_test_digest() -> str:
    return payload_digest(
        system_instruction=PROVIDER_TEST.system_instruction,
        input_text=PROVIDER_TEST.input_text,
        response_schema=plain_schema(PROVIDER_TEST),
    )


def test_gate_disabled_refuses_everything() -> None:
    gate = DeploymentGate.from_values("disabled", "IGNORED-REF")
    assert gate.approval_ref is None
    assert (
        gate.refusal(
            subject_type="fixture", fixture_id="provider_test.v1", digest=_provider_test_digest()
        )
        == SKIP_EXTERNAL_CALLS_DISABLED
    )


def test_gate_synthetic_only_accepts_only_exact_fixture_payloads() -> None:
    gate = DeploymentGate.from_values("synthetic_only", None)
    digest = _provider_test_digest()
    assert (
        gate.refusal(subject_type="fixture", fixture_id="provider_test.v1", digest=digest) is None
    )
    # An uploaded document is refused even when its content equals the fixture.
    assert (
        gate.refusal(subject_type="document_version", fixture_id=None, digest=digest)
        == SKIP_SYNTHETIC_ONLY
    )
    assert (
        gate.refusal(subject_type="document_version", fixture_id="provider_test.v1", digest=digest)
        == SKIP_SYNTHETIC_ONLY
    )
    assert (
        gate.refusal(subject_type="fixture", fixture_id="provider_test.v1", digest="0" * 64)
        == SKIP_UNKNOWN_FIXTURE
    )
    assert (
        gate.refusal(subject_type="fixture", fixture_id="made_up.v1", digest=digest)
        == SKIP_UNKNOWN_FIXTURE
    )


@pytest.mark.parametrize("reference", [None, "", "lowercase-ref", "X"])
def test_gate_enabled_requires_an_approval_reference(reference: str | None) -> None:
    with pytest.raises(GateConfigurationError):
        DeploymentGate.from_values("enabled", reference)
    with pytest.raises(GateConfigurationError):
        DeploymentGate.from_values("sometimes", None)
    gate = DeploymentGate.from_values("enabled", "CMP-2026-017")
    assert gate.refusal(subject_type="document_version", fixture_id=None, digest="x") is None


def test_api_settings_reject_an_unapproved_enabled_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in {
        "RB_DATABASE_URL": "postgresql+psycopg://u:p@localhost/db",
        "RB_S3_ENDPOINT_URL": "http://localhost:9000",
        "RB_S3_REGION": "us-east-1",
        "RB_S3_BUCKET": "b",
        "RB_S3_API_ACCESS_KEY": "a",
        "RB_S3_API_SECRET_KEY": "s",
        "RB_PUBLIC_BASE_URL": "http://localhost:5173",
        "RB_AI_EXTERNAL_CALLS": "enabled",
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("RB_AI_EXTERNAL_CALLS_APPROVAL_REF", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]
    monkeypatch.setenv("RB_AI_EXTERNAL_CALLS", "disabled")
    assert Settings(_env_file=None).ai_external_calls == "disabled"  # type: ignore[call-arg]


def test_ai_task_declarations_can_be_deferred_but_never_executed() -> None:
    app = App(connector=testing.InMemoryConnector())
    tasks = register_ai_task_declarations(app)
    task = tasks[RUN_AI_REQUEST]
    assert task.name == RUN_AI_REQUEST
    assert task.queue == AI_QUEUE
    with pytest.raises(AiTaskNotExecutableHere):
        task(ai_request_id=str(uuid4()))


def test_general_worker_never_consumes_the_ai_queue() -> None:
    assert AI_QUEUE not in jobs.GENERAL_WORKER_QUEUES
    source = inspect.getsource(jobs.worker_main)
    assert "queues=list(GENERAL_WORKER_QUEUES)" in source
    assert '"ai"' not in source


def test_ai_enqueue_carries_only_the_ledger_id() -> None:
    captured: dict[str, Any] = {}

    class _Deferrer:
        job_manager: Any = None

        def defer(self, **arguments: str) -> int:
            captured.update(arguments)
            return 7

    class _Task:
        def configure(self, *, connection: Any) -> _Deferrer:
            del connection
            return _Deferrer()

    queue = jobs.ProcrastinateJobQueue({RUN_AI_REQUEST: _Task()})
    request_id = uuid4()
    connection = SimpleNamespace(connection=SimpleNamespace(driver_connection=object()))
    assert queue.enqueue_ai_request(ai_request_id=request_id, connection=cast(Any, connection)) == 7
    assert captured == {"ai_request_id": str(request_id)}


@pytest.mark.parametrize("name", ["GEMINI_API_KEY", "GOOGLE_API_KEY"])
def test_ai_worker_refuses_process_wide_provider_keys(name: str) -> None:
    with pytest.raises(AiWorkerStartupError) as caught:
        check_startup_environment({name: SENTINEL_KEY})
    assert SENTINEL_KEY not in str(caught.value)
    with pytest.raises(AiWorkerStartupError):
        check_startup_environment({name: ""})
    check_startup_environment({"RB_DATABASE_URL": "x"})


def test_logs_redact_provider_keys_by_name_and_by_value() -> None:
    event = redact_sensitive_fields(
        None,
        "info",
        {
            "event": f"request to https://example.test/v1/interactions?key={SENTINEL_KEY}",
            "detail": f"key {SENTINEL_KEY} rejected",
            "headers": {"x-goog-api-key": SENTINEL_KEY},
            "count": 3,
        },
    )
    assert SENTINEL_KEY not in json.dumps(event)
    assert event["headers"]["x-goog-api-key"] == "[REDACTED]"
    assert event["count"] == 3
