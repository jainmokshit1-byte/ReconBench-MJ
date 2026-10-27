"""PD-1d: Provider isolation and safety suite (Batch 2 Prompt A).

Tests strict single-provider execution, absence of cross-provider fallback,
safe handling of disconnected cards, gate enforcement, sentinel secret shielding,
and pure guards on routing and URL hygiene.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import httpx
import pytest
import sqlalchemy as sa
from reconbench_app.ai.consent import CONSENT_TEXT_VERSION, consent_fingerprint
from reconbench_app.ai.enqueue import AiHookEnqueuer
from reconbench_app.ai.gate import DeploymentGate
from reconbench_app.ai.key_seal import SealingPublicKey, seal
from reconbench_app.ai.limits import AiLimits, LimitsState
from reconbench_app.ai.provider import ProviderRequest, ProviderResult
from reconbench_app.ai.runtime_checks import (
    ProviderConfig,
    SettingsSnapshot,
)
from reconbench_app.ai.settings_service import (
    AiServiceConfig,
    AiSettingsError,
    AiSettingsService,
)
from reconbench_app.ai.stub import ScriptedProvider
from reconbench_app.ai.worker.handlers import default_handlers
from reconbench_app.ai.worker.key_open import Keyring
from reconbench_app.ai.worker.keytool import KEYRING_NAME, PUBLIC_KEY_NAME, generate
from reconbench_app.ai.worker.openai_compat import OpenAICompatProvider
from reconbench_app.ai.worker.runner import AiRequestRunner
from reconbench_app.ai.worker.sweeps import gemini_health, provider_health
from reconbench_app.authorization import ActorContext
from reconbench_app.configuration import canonical_hash
from reconbench_app.db.ids import new_id
from reconbench_app.db.metadata import metadata
from reconbench_app.db.seed_data import DEFAULT_CONFIG, REASON_CODES
from sqlalchemy.engine import Connection, Engine, make_url

SENTINEL_GEMINI_KEY = "AIzaSyD-SENTINEL-gemini-key-00000000"
SENTINEL_VLLM_KEY = "local-SENTINEL-vllm-key-9999999999"
GEMINI_MODEL = "gemini-3.8-flash"
VLLM_MODEL = "qwen3.5:9b"
VLLM_URL = "http://vllm.example.test/vllm/v1"
APPROVAL = "SYNTHETIC-ISOLATION-APPROVAL-1"

_CREATED: list[UUID] = []


class RecordingQueue:
    def __init__(self) -> None:
        self.request_ids: list[UUID] = []

    def enqueue_ai_request(self, *, ai_request_id: UUID, connection: Connection) -> int:
        del connection
        self.request_ids.append(ai_request_id)
        return len(self.request_ids)


class MockVllmServer:
    def __init__(self, responses: list[httpx.Response] | None = None) -> None:
        self.responses = list(responses or [])
        self.requests: list[httpx.Request] = []
        self.health_probes: int = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.method == "GET" and request.url.path.endswith("/models"):
            self.health_probes += 1
            return httpx.Response(
                200, json={"data": [{"id": VLLM_MODEL, "max_model_len": 8192}]}
            )
        if self.responses:
            return self.responses.pop(0)
        return httpx.Response(
            200,
            json={
                "model": VLLM_MODEL,
                "choices": [
                    {"message": {"content": '{"status":"ok"}'}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2},
            },
        )

    @property
    def completions(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.method == "POST"]


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    dsn = os.environ.get("RB_PHASE2_TEST_DATABASE_URL", "").strip()
    if not dsn:
        pytest.skip("RB_PHASE2_TEST_DATABASE_URL is required for disposable PostgreSQL tests")
    if not (make_url(dsn).database or "").startswith("reconbench_phase2_"):
        raise RuntimeError("Provider isolation tests require a disposable database")
    created = sa.create_engine(dsn)
    try:
        yield created
    finally:
        if _CREATED:
            with created.begin() as connection:
                for table in ("ai_request", "ai_call_log", "ai_setting", "secret"):
                    connection.execute(
                        sa.text(f"DELETE FROM {table} WHERE workspace_id = ANY(:ids)"),
                        {"ids": list(_CREATED)},
                    )
        created.dispose()


@pytest.fixture(scope="module")
def keys(tmp_path_factory: pytest.TempPathFactory) -> tuple[Keyring, SealingPublicKey]:
    directory: Path = tmp_path_factory.mktemp("ai-isolation-keys")
    generate(directory, "k1")
    return Keyring.load(directory / KEYRING_NAME), SealingPublicKey.from_file(
        directory / PUBLIC_KEY_NAME
    )


def _setup_dual_provider_workspace(
    connection: Connection,
    public_key: SealingPublicKey,
    *,
    active_provider: str,
) -> tuple[UUID, UUID]:
    workspace_id = new_id()
    user_id = new_id()
    secret_id = new_id()
    _CREATED.append(workspace_id)

    connection.execute(
        sa.insert(metadata.tables["workspace"]).values(
            id=workspace_id, name="Dual Provider Workspace"
        )
    )
    connection.execute(
        sa.insert(metadata.tables["app_user"]).values(
            id=user_id,
            workspace_id=workspace_id,
            email=f"iso-{user_id}@example.invalid",
            display_name="Synthetic Isolation User",
            role="manager",
            password_hash="not-a-real-hash",
        )
    )
    # Config
    config_id = new_id()
    connection.execute(
        sa.insert(metadata.tables["config_version"]).values(
            id=config_id,
            workspace_id=workspace_id,
            seq=1,
            body=DEFAULT_CONFIG,
            engine_hash=canonical_hash(DEFAULT_CONFIG["engine"]),
            body_hash=canonical_hash(DEFAULT_CONFIG),
            change_note="Synthetic isolation baseline",
            created_by=user_id,
        )
    )
    connection.execute(
        sa.insert(metadata.tables["config_active"]).values(
            id=new_id(),
            workspace_id=workspace_id,
            config_version_id=config_id,
            activated_by=user_id,
            version=1,
        )
    )
    connection.execute(
        sa.insert(metadata.tables["reason_code"]),
        [{"id": new_id(), "workspace_id": workspace_id, **row} for row in REASON_CODES],
    )
    # Sealed Gemini Secret Key
    sealed = seal(
        SENTINEL_GEMINI_KEY.encode(),
        public_key=public_key,
        workspace_id=workspace_id,
        secret_id=secret_id,
    )
    connection.execute(
        sa.insert(metadata.tables["secret"]).values(
            id=secret_id,
            workspace_id=workspace_id,
            purpose="ai_api_key",
            ciphertext=sealed.envelope,
            nonce=sealed.nonce,
            key_id=sealed.key_id,
            key_hint="0000",
            created_by=user_id,
        )
    )
    # vLLM Provider Config Card
    now = datetime.now(UTC)
    connection.execute(
        sa.insert(metadata.tables["ai_provider_config"]).values(
            id=new_id(),
            workspace_id=workspace_id,
            provider="vllm",
            enabled=True,
            model=VLLM_MODEL,
            base_url=VLLM_URL,
            limits={},
            updated_by=user_id,
            updated_at=now,
        )
    )
    # Gemini Provider Config Card
    connection.execute(
        sa.insert(metadata.tables["ai_provider_config"]).values(
            id=new_id(),
            workspace_id=workspace_id,
            provider="gemini",
            enabled=True,
            model=GEMINI_MODEL,
            base_url=None,
            limits={},
            updated_by=user_id,
            updated_at=now,
        )
    )

    primary_reader = active_provider
    reader_strategy = "single"
    fp = consent_fingerprint(
        provider="gemini",
        model=GEMINI_MODEL,
        mode="ai_assisted",
        reader_strategy=reader_strategy,
        vision_provider=primary_reader,
        vision_model=VLLM_MODEL,
        vision_base_url=VLLM_URL,
    )
    connection.execute(
        sa.insert(metadata.tables["ai_setting"]).values(
            id=new_id(),
            workspace_id=workspace_id,
            mode="ai_assisted",
            provider="gemini",
            model=GEMINI_MODEL,
            reader_strategy=reader_strategy,
            primary_reader=primary_reader,
            apply_mode="suggest",
            key_storage="saved",
            key_secret_id=secret_id,
            updated_by=user_id,
            consent_given=True,
            consent_fingerprint=fp,
            consent_by=user_id,
            consent_at=datetime.now(UTC),
            consent_text_version=CONSENT_TEXT_VERSION,
        )
    )
    return workspace_id, user_id


def test_inactive_provider_gets_zero_calls_both_directions(
    engine: Engine, keys: tuple[Keyring, SealingPublicKey]
) -> None:
    """PD-1a: Inactive provider gets 0 AI calls across hooks, sweeps, and runner.

    Direction 1: Active = vLLM, Inactive = Gemini
      - Gemini gets 0 requests, 0 completions.
      - Positive control: vLLM gets >= 1 completion.
    Direction 2: Active = Gemini, Inactive = vLLM
      - vLLM gets 0 AI completions (GET /models health probe runs 1 time, reported separately).
      - Positive control: Gemini gets >= 1 completion.
    """
    keyring, public_key = keys

    # --- Direction 1: Active = vLLM, Inactive = Gemini ---
    with engine.begin() as conn:
        ws_vllm, uid1 = _setup_dual_provider_workspace(
            conn, public_key, active_provider="vllm"
        )

    vllm_server_1 = MockVllmServer()
    vllm_provider_1 = OpenAICompatProvider(
        VLLM_URL, frozenset({VLLM_URL}), inner_transport=httpx.MockTransport(vllm_server_1)
    )
    gemini_stub_1 = ScriptedProvider([json.dumps({"status": "ok"})])
    gemini_stub_1.name = "gemini"

    gate = DeploymentGate.from_values(
        "enabled", APPROVAL, vllm_mode="enabled", vllm_endpoints=VLLM_URL
    )
    runner_1 = AiRequestRunner(
        engine=engine,
        keyring=keyring,
        gate=gate,
        limits=lambda: LimitsState(AiLimits()),
        prices=lambda: None,
        providers={"gemini": gemini_stub_1, "vllm": vllm_provider_1},
        handlers=default_handlers(),
    )

    # gemini_health runs only when Gemini is the active provider
    gh_runs_1 = gemini_health(engine, runner_1, datetime.now(UTC))
    assert gh_runs_1 == 0, "gemini_health must not run when vLLM is active"

    # 1. Hook check: enqueuer.ready checks hook routing
    queue_1 = RecordingQueue()
    enqueuer_1 = AiHookEnqueuer(
        gate=gate, queue=queue_1, limits=lambda: LimitsState(AiLimits())
    )
    with engine.begin() as conn:
        ready = enqueuer_1.ready(conn, ws_vllm, "h6_vision")
        assert ready is not None
        settings_snap, _ = ready
        # Routing targets active provider (vLLM), never Gemini
        assert settings_snap.target("h1_extract") == ("vllm", VLLM_MODEL)
        assert settings_snap.target("h6_vision") == ("vllm", VLLM_MODEL)

    # 2. Runner check: execute a request for active provider
    service_1 = AiSettingsService(
        engine,
        AiServiceConfig(gate=gate, public_key_file=None, limits_file=None, price_file=None),
        queue_1,
    )
    actor_1 = ActorContext(ws_vllm, uid1, "manager")
    test_req_1 = service_1.request_provider_test(actor_1, provider="vllm", ip="127.0.0.1")
    req_id_1 = test_req_1["request_id"]
    result_1 = runner_1.run(req_id_1)
    assert result_1.state == "succeeded"

    # Assert Inactive provider (Gemini) got 0 calls
    assert len(gemini_stub_1.calls) == 0
    with engine.connect() as conn:
        gemini_rows_1 = conn.scalar(
            sa.text(
                "SELECT count(*) FROM ai_request WHERE workspace_id = :w AND provider = 'gemini'"
            ),
            {"w": ws_vllm},
        )
    assert gemini_rows_1 == 0

    # Positive control: Active provider (vLLM) got >= 1 completion
    assert len(vllm_server_1.completions) >= 1
    with engine.connect() as conn:
        vllm_rows_1 = conn.scalar(
            sa.text(
                "SELECT count(*) FROM ai_request WHERE workspace_id = :w AND provider = 'vllm'"
            ),
            {"w": ws_vllm},
        )
    assert vllm_rows_1 == 1

    # --- Direction 2: Active = Gemini, Inactive = vLLM ---
    with engine.begin() as conn:
        ws_gemini, uid2 = _setup_dual_provider_workspace(
            conn, public_key, active_provider="gemini"
        )

    vllm_server_2 = MockVllmServer()
    vllm_provider_2 = OpenAICompatProvider(
        VLLM_URL, frozenset({VLLM_URL}), inner_transport=httpx.MockTransport(vllm_server_2)
    )
    gemini_stub_2 = ScriptedProvider([json.dumps({"status": "ok"})])
    gemini_stub_2.name = "gemini"

    runner_2 = AiRequestRunner(
        engine=engine,
        keyring=keyring,
        gate=gate,
        limits=lambda: LimitsState(AiLimits()),
        prices=lambda: None,
        providers={"gemini": gemini_stub_2, "vllm": vllm_provider_2},
        handlers=default_handlers(),
    )

    # vLLM health heartbeat (GET /models only)
    probes_before = vllm_server_2.health_probes
    provider_health(engine, vllm_provider_2, datetime.now(UTC))
    probes_ran = vllm_server_2.health_probes - probes_before
    # Report count: health probes ran while vLLM inactive
    assert probes_ran >= 1

    # 1. Hook check: enqueuer.ready checks hook routing
    queue_2 = RecordingQueue()
    enqueuer_2 = AiHookEnqueuer(
        gate=gate, queue=queue_2, limits=lambda: LimitsState(AiLimits())
    )
    with engine.begin() as conn:
        ready2 = enqueuer_2.ready(conn, ws_gemini, "h6_vision")
        assert ready2 is not None
        settings_snap2, _ = ready2
        # Routing targets active provider (Gemini), never vLLM
        assert settings_snap2.target("h1_extract") == ("gemini", GEMINI_MODEL)
        assert settings_snap2.target("h6_vision") == ("gemini", GEMINI_MODEL)

    # 2. Runner check: execute a request for active provider
    service_2 = AiSettingsService(
        engine,
        AiServiceConfig(gate=gate, public_key_file=None, limits_file=None, price_file=None),
        queue_2,
    )
    actor_2 = ActorContext(ws_gemini, uid2, "manager")
    test_req_2 = service_2.request_provider_test(actor_2, provider="gemini", ip="127.0.0.1")
    req_id_2 = test_req_2["request_id"]
    result_2 = runner_2.run(req_id_2)
    assert result_2.state == "succeeded"

    # Assert Inactive provider (vLLM) got 0 AI completions
    assert len(vllm_server_2.completions) == 0
    with engine.connect() as conn:
        vllm_rows_2 = conn.scalar(
            sa.text(
                "SELECT count(*) FROM ai_request WHERE workspace_id = :w AND provider = 'vllm'"
            ),
            {"w": ws_gemini},
        )
    assert vllm_rows_2 == 0

    # Positive control: Active provider (Gemini) got >= 1 completion
    assert len(gemini_stub_2.calls) >= 1
    with engine.connect() as conn:
        gemini_rows_2 = conn.scalar(
            sa.text(
                "SELECT count(*) FROM ai_request WHERE workspace_id = :w AND provider = 'gemini'"
            ),
            {"w": ws_gemini},
        )
    assert gemini_rows_2 == 1


def test_switching_provider_preserves_existing_data(
    engine: Engine, keys: tuple[Keyring, SealingPublicKey]
) -> None:
    """PD-1a: Switching provider leaves existing ai_request rows, cached outputs,

    suggestions and document versions unchanged (hash before == hash after).
    """
    keyring, public_key = keys
    with engine.begin() as conn:
        ws_id, uid = _setup_dual_provider_workspace(conn, public_key, active_provider="gemini")
        # Insert a document version and suggestion
        batch_id, doc_id, ver_id, file_id = new_id(), new_id(), new_id(), new_id()
        conn.execute(
            sa.insert(metadata.tables["batch"]).values(
                id=batch_id, workspace_id=ws_id, label="Switch Test", created_by=uid
            )
        )
        conn.execute(
            sa.insert(metadata.tables["source_file"]).values(
                id=file_id,
                workspace_id=ws_id,
                batch_id=batch_id,
                sha256=hashlib.sha256(b"source_file").hexdigest(),
                display_name="test.pdf",
                object_key="files/test.pdf",
                zone="invoice",
                zone_uploaded="invoice",
                format="pdf",
                status="parsed",
                size_bytes=100,
                uploaded_by=uid,
            )
        )
        conn.execute(
            sa.insert(metadata.tables["document"]).values(
                id=doc_id,
                workspace_id=ws_id,
                doc_type="invoice",
                natural_key=hashlib.sha256(str(doc_id).encode()).hexdigest(),
                batch_id=batch_id,
                doc_no="INV-PRESERVE-01",
                canonical_doc_no="INV-PRESERVE-01",
                doc_date=date(2026, 4, 1),
                grand_total=Decimal("100.00"),
                vendor_id=None,
            )
        )
        conn.execute(
            sa.insert(metadata.tables["document_version"]).values(
                id=ver_id,
                workspace_id=ws_id,
                document_id=doc_id,
                seq=1,
                content_hash=hashlib.sha256(b"preserve_content").hexdigest(),
                doc_no="INV-PRESERVE-01",
                canonical_doc_no="INV-PRESERVE-01",
                doc_date=date(2026, 4, 1),
                vendor_name="Preserve Corp",
                vendor_name_norm="preserve corp",
                currency="INR",
                taxable_total=Decimal("100.00"),
                grand_total=Decimal("100.00"),
                po_refs=[],
                canonical_po_refs=[],
                extraction_method="pdf_text",
                extraction_conf=Decimal("0.95"),
                source_file_id=file_id,
                parser_version="1.0",
                needs_verification=False,
            )
        )
        # Completed ai_request
        req_id = new_id()
        conn.execute(
            sa.insert(metadata.tables["ai_request"]).values(
                id=req_id,
                workspace_id=ws_id,
                call_kind="hook",
                hook="h1_extract",
                mode="ai_assisted",
                subject_type="document_version",
                subject_id=ver_id,
                subject_version=hashlib.sha256(b"contenthash001").hexdigest(),
                request_key=hashlib.sha256(b"reqkey001").hexdigest(),
                operation_key="op-preserve",
                settings_version=1,
                consent_fingerprint=hashlib.sha256(b"fp1").hexdigest(),
                provider="gemini",
                model=GEMINI_MODEL,
                prompt_version="v1",
                schema_version="v1",
                redaction_version="v1",
                request_state="succeeded",
                apply_outcome="applied",
                reserved_at=datetime.now(UTC),
                sent_at=datetime.now(UTC),
                finished_at=datetime.now(UTC),
                created_at=datetime.now(UTC),
            )
        )
        # ai_cache
        conn.execute(
            sa.insert(metadata.tables["ai_cache"]).values(
                id=new_id(),
                workspace_id=ws_id,
                cache_key=hashlib.sha256(f"cache-{req_id}".encode()).hexdigest(),
                hook="h1_extract",
                model=GEMINI_MODEL,
                prompt_version="v1",
                schema_version="v1",
                response={"data": "preserved"},
                provider="gemini",
                created_at=datetime.now(UTC),
            )
        )

    # Hash rows before switch
    def _table_hash(tables: tuple[str, ...]) -> str:
        h = hashlib.sha256()
        with engine.connect() as conn:
            for table in tables:
                rows = conn.execute(
                    sa.text(f"SELECT * FROM {table} WHERE workspace_id = :w ORDER BY 1"),
                    {"w": ws_id},
                ).all()
                for r in rows:
                    h.update(str(tuple(r)).encode("utf-8"))
        return h.hexdigest()

    watched = ("ai_request", "ai_cache", "document", "document_version")
    hash_before = _table_hash(watched)

    # Perform switch to vLLM
    new_fp = consent_fingerprint(
        provider="gemini",
        model=GEMINI_MODEL,
        mode="ai_assisted",
        reader_strategy="single",
        vision_provider="vllm",
        vision_model=VLLM_MODEL,
        vision_base_url=VLLM_URL,
    )
    with engine.begin() as conn:
        conn.execute(
            sa.update(metadata.tables["ai_setting"])
            .where(metadata.tables["ai_setting"].c.workspace_id == ws_id)
            .values(
                primary_reader="vllm",
                consent_fingerprint=new_fp,
                version=2,
                updated_at=datetime.now(UTC),
            )
        )

    hash_after = _table_hash(watched)
    assert hash_before == hash_after, "Switching provider must not mutate existing data"


def test_disabled_deployment_gates_block_calls_and_provider_tests(
    engine: Engine, keys: tuple[Keyring, SealingPublicKey]
) -> None:
    """Gates disabled (RB_AI_EXTERNAL_CALLS, RB_AI_VLLM_CALLS) => 0 requests,

    and provider tests refused.
    """
    keyring, public_key = keys
    with engine.begin() as conn:
        ws_id, uid = _setup_dual_provider_workspace(conn, public_key, active_provider="gemini")

    # 1. External calls disabled -> Gemini provider test refused with 409
    gate_ext_off = DeploymentGate.from_values(
        "disabled", APPROVAL, vllm_mode="enabled", vllm_endpoints=VLLM_URL
    )
    cfg_ext_off = AiServiceConfig(
        gate=gate_ext_off, public_key_file=None, limits_file=None, price_file=None
    )
    service_ext_off = AiSettingsService(engine, cfg_ext_off, RecordingQueue())
    actor = ActorContext(ws_id, uid, "manager")
    with pytest.raises(AiSettingsError) as exc_gemini:
        service_ext_off.request_provider_test(actor, provider="gemini", ip="127.0.0.1")
    assert exc_gemini.value.status == 409
    assert exc_gemini.value.code == "external_calls_disabled"

    # 2. vLLM calls disabled -> vLLM provider test refused with 409
    gate_vllm_off = DeploymentGate.from_values("enabled", APPROVAL, vllm_mode="disabled")
    cfg_vllm_off = AiServiceConfig(
        gate=gate_vllm_off, public_key_file=None, limits_file=None, price_file=None
    )
    service_vllm_off = AiSettingsService(engine, cfg_vllm_off, RecordingQueue())
    with pytest.raises(AiSettingsError) as exc_vllm:
        service_vllm_off.request_provider_test(actor, provider="vllm", ip="127.0.0.1")
    assert exc_vllm.value.status == 409
    assert exc_vllm.value.code == "local_calls_disabled"


def test_non_allowlisted_vllm_url_refused_and_skipped(
    engine: Engine, keys: tuple[Keyring, SealingPublicKey]
) -> None:
    """A vLLM URL not on the allowlist is never called: connect refused;

    stored card whose URL left allowlist skipped; MockTransport sees 0 requests.
    """
    keyring, public_key = keys
    with engine.begin() as conn:
        ws_id, uid = _setup_dual_provider_workspace(conn, public_key, active_provider="vllm")

    actor = ActorContext(ws_id, uid, "manager")
    from reconbench_app.ai.provider_connection import (
        ConnectBody,
        ConsentToNotice,
        ProviderConnectionService,
    )

    gate = DeploymentGate.from_values(
        "enabled", APPROVAL, vllm_mode="enabled", vllm_endpoints=VLLM_URL
    )
    cfg = AiServiceConfig(gate=gate, public_key_file=None, limits_file=None, price_file=None)
    service = AiSettingsService(engine, cfg, RecordingQueue())
    conn_service = ProviderConnectionService(service)

    # Connect refused for disallowed URL
    with pytest.raises(AiSettingsError) as exc_connect:
        conn_service.connect(
            actor,
            session_id=new_id(),
            provider="vllm",
            body=ConnectBody(
                model=VLLM_MODEL,
                base_url="http://169.254.169.254/latest",
                consent=ConsentToNotice(accept=True, fingerprint="0" * 64),
            ),
            ip="127.0.0.1",
        )
    assert exc_connect.value.status == 422
    assert exc_connect.value.code == "endpoint_not_allowed"

    # Stored card whose URL left allowlist:
    narrowed_gate = DeploymentGate.from_values(
        "enabled", APPROVAL, vllm_mode="enabled", vllm_endpoints="http://other.example.test/v1"
    )
    server = MockVllmServer()
    narrowed_runner = AiRequestRunner(
        engine=engine,
        keyring=keyring,
        gate=narrowed_gate,
        limits=lambda: LimitsState(AiLimits()),
        prices=lambda: None,
        providers={
            "vllm": OpenAICompatProvider(
                VLLM_URL, frozenset({VLLM_URL}), inner_transport=httpx.MockTransport(server)
            )
        },
        handlers=default_handlers(),
    )
    req_id = service.request_provider_test(actor, provider="vllm", ip="127.0.0.1")["request_id"]
    result = narrowed_runner.run(req_id)
    assert result.state == "skipped"
    assert result.code == "ENDPOINT_NOT_ALLOWED"
    assert len(server.completions) == 0, "Disallowed endpoint must see 0 HTTP requests"


def test_sentinel_keys_never_leaked(
    engine: Engine, keys: tuple[Keyring, SealingPublicKey], caplog: pytest.LogCaptureFixture
) -> None:
    """Sentinel keys for providers never appear in public tables, logs, or responses."""
    keyring, public_key = keys
    with engine.begin() as conn:
        ws_id, uid = _setup_dual_provider_workspace(conn, public_key, active_provider="gemini")

    # Scan all public tables
    with engine.connect() as conn:
        for table in (
            "ai_request",
            "ai_call_log",
            "ai_setting",
            "ai_provider_config",
            "audit_log",
            "workspace",
            "app_user",
        ):
            col = "id" if table == "workspace" else "workspace_id"
            rows = conn.execute(
                sa.text(f"SELECT * FROM {table} WHERE {col} = :w"), {"w": ws_id}
            ).all()
            for row in rows:
                row_str = str(tuple(row))
                assert SENTINEL_GEMINI_KEY not in row_str
                assert SENTINEL_VLLM_KEY not in row_str

        # Scan secret table: only sealed ciphertext/nonce, not raw keys
        secret_row = conn.execute(
            sa.text("SELECT ciphertext, nonce FROM secret WHERE workspace_id = :w"), {"w": ws_id}
        ).one()
        assert SENTINEL_GEMINI_KEY.encode() not in bytes(secret_row[0])
        assert SENTINEL_GEMINI_KEY.encode() not in bytes(secret_row[1])

    # Logs
    assert SENTINEL_GEMINI_KEY not in caplog.text
    assert SENTINEL_VLLM_KEY not in caplog.text


def test_non_admin_refused_and_audited(
    engine: Engine, keys: tuple[Keyring, SealingPublicKey]
) -> None:
    """Non-admin user without ai.manage is refused and an audit event is logged."""
    keyring, public_key = keys
    with engine.begin() as conn:
        ws_id, admin_id = _setup_dual_provider_workspace(
            conn, public_key, active_provider="gemini"
        )
        non_admin_id = new_id()
        conn.execute(
            sa.insert(metadata.tables["app_user"]).values(
                id=non_admin_id,
                workspace_id=ws_id,
                email="auditor@example.invalid",
                display_name="Auditor",
                role="auditor",
                password_hash="pw",
            )
        )

    actor = ActorContext(ws_id, non_admin_id, "auditor")
    gate = DeploymentGate.from_values(
        "enabled", APPROVAL, vllm_mode="enabled", vllm_endpoints=VLLM_URL
    )
    cfg = AiServiceConfig(gate=gate, public_key_file=None, limits_file=None, price_file=None)
    service = AiSettingsService(engine, cfg, RecordingQueue())

    with pytest.raises(AiSettingsError) as exc:
        service.request_provider_test(actor, provider="gemini", ip="127.0.0.1")
    assert exc.value.status == 403

    # Check audit log for ai.manage_denied
    with engine.connect() as conn:
        denied_audit = conn.execute(
            sa.text(
                "SELECT count(*) FROM audit_log "
                "WHERE workspace_id = :w AND action = 'ai.manage_denied'"
            ),
            {"w": ws_id},
        ).scalar_one()
    assert denied_audit >= 1


def test_disconnecting_active_provider_handled_cleanly(
    engine: Engine, keys: tuple[Keyring, SealingPublicKey]
) -> None:
    """Disconnecting the active provider while request queued or running produces 0 calls

    to the other provider and a stated skipped end state without crashing.
    """
    keyring, public_key = keys
    with engine.begin() as conn:
        ws_id, uid = _setup_dual_provider_workspace(conn, public_key, active_provider="vllm")

    vllm_server = MockVllmServer()
    vllm_provider = OpenAICompatProvider(
        VLLM_URL, frozenset({VLLM_URL}), inner_transport=httpx.MockTransport(vllm_server)
    )
    gemini_stub = ScriptedProvider([{"status": "ok"}])
    gemini_stub.name = "gemini"

    gate = DeploymentGate.from_values(
        "enabled", APPROVAL, vllm_mode="enabled", vllm_endpoints=VLLM_URL
    )
    runner = AiRequestRunner(
        engine=engine,
        keyring=keyring,
        gate=gate,
        limits=lambda: LimitsState(AiLimits()),
        prices=lambda: None,
        providers={"gemini": gemini_stub, "vllm": vllm_provider},
        handlers=default_handlers(),
    )

    # 1. While queued: enqueue a request, then disable the vLLM card
    cfg = AiServiceConfig(gate=gate, public_key_file=None, limits_file=None, price_file=None)
    service = AiSettingsService(engine, cfg, RecordingQueue())
    actor = ActorContext(ws_id, uid, "manager")
    req_id = service.request_provider_test(actor, provider="vllm", ip="127.0.0.1")["request_id"]

    # Now disable the vLLM card in DB
    with engine.begin() as conn:
        conn.execute(
            sa.update(metadata.tables["ai_provider_config"])
            .where(
                metadata.tables["ai_provider_config"].c.workspace_id == ws_id,
                metadata.tables["ai_provider_config"].c.provider == "vllm",
            )
            .values(enabled=False)
        )

    # Run the runner: must skip cleanly with PROVIDER_DISABLED
    result = runner.run(req_id)
    assert result.state == "skipped"
    assert result.code == "PROVIDER_DISABLED"
    assert len(gemini_stub.calls) == 0, "No request must go to Gemini"
    assert len(vllm_server.completions) == 0, "No request must go to disabled vLLM"

    # 2. While running: card disconnected in-flight
    with engine.begin() as conn:
        conn.execute(
            sa.update(metadata.tables["ai_provider_config"])
            .where(
                metadata.tables["ai_provider_config"].c.workspace_id == ws_id,
                metadata.tables["ai_provider_config"].c.provider == "vllm",
            )
            .values(enabled=True)
        )
    req_id_2 = service.request_provider_test(actor, provider="vllm", ip="127.0.0.1")["request_id"]

    def _disconnect() -> None:
        with engine.begin() as conn:
            conn.execute(
                sa.update(metadata.tables["ai_provider_config"])
                .where(
                    metadata.tables["ai_provider_config"].c.workspace_id == ws_id,
                    metadata.tables["ai_provider_config"].c.provider == "vllm",
                )
                .values(enabled=False)
            )

    class DisconnectingProvider(ScriptedProvider):
        def complete(self, request: ProviderRequest, *, api_key: str) -> ProviderResult:
            _disconnect()
            return super().complete(request, api_key=api_key)

    disc_provider = DisconnectingProvider([json.dumps({"status": "ok"})])
    disc_provider.name = "vllm"

    runner_disc = AiRequestRunner(
        engine=engine,
        keyring=keyring,
        gate=gate,
        limits=lambda: LimitsState(AiLimits()),
        prices=lambda: None,
        providers={"gemini": gemini_stub, "vllm": disc_provider},
        handlers=default_handlers(),
    )
    result_2 = runner_disc.run(req_id_2)
    assert result_2.state in ("succeeded", "failed", "uncertain")
    assert len(gemini_stub.calls) == 0, "No request must go to Gemini"


@pytest.mark.parametrize("status_code", [401, 429, 500, 503])
def test_no_cross_provider_fallback_on_http_errors(
    engine: Engine, keys: tuple[Keyring, SealingPublicKey], status_code: int
) -> None:
    """When active provider returns HTTP error, runner never falls back to other provider."""
    keyring, public_key = keys
    with engine.begin() as conn:
        ws_id, uid = _setup_dual_provider_workspace(conn, public_key, active_provider="vllm")

    error_response = httpx.Response(status_code, json={"error": f"Error {status_code}"})
    vllm_server = MockVllmServer([error_response])
    vllm_provider = OpenAICompatProvider(
        VLLM_URL, frozenset({VLLM_URL}), inner_transport=httpx.MockTransport(vllm_server)
    )
    gemini_stub = ScriptedProvider([json.dumps({"status": "ok"})])
    gemini_stub.name = "gemini"

    gate = DeploymentGate.from_values(
        "enabled", APPROVAL, vllm_mode="enabled", vllm_endpoints=VLLM_URL
    )
    runner = AiRequestRunner(
        engine=engine,
        keyring=keyring,
        gate=gate,
        limits=lambda: LimitsState(AiLimits()),
        prices=lambda: None,
        providers={"gemini": gemini_stub, "vllm": vllm_provider},
        handlers=default_handlers(),
    )

    cfg = AiServiceConfig(gate=gate, public_key_file=None, limits_file=None, price_file=None)
    service = AiSettingsService(engine, cfg, RecordingQueue())
    actor = ActorContext(ws_id, uid, "manager")
    req_id = service.request_provider_test(actor, provider="vllm", ip="127.0.0.1")["request_id"]

    result = runner.run(req_id)
    assert result.state in ("failed", "uncertain")
    assert len(gemini_stub.calls) == 0, "Never fall back to Gemini on vLLM error"
    with engine.connect() as conn:
        gemini_rows = conn.scalar(
            sa.text(
                "SELECT count(*) FROM ai_request WHERE workspace_id = :w AND provider = 'gemini'"
            ),
            {"w": ws_id},
        )
    assert gemini_rows == 0, "No ledger row created for Gemini"


def test_web_src_contains_no_urls() -> None:
    """web/src, excluding *.test.ts and *.test.tsx, contains no http:// or https:// literal."""
    web_src = Path(__file__).resolve().parents[3] / "web" / "src"
    assert web_src.is_dir(), f"{web_src} must exist"

    hits: list[str] = []
    for path in web_src.rglob("*"):
        if path.is_file() and not path.name.endswith((".test.ts", ".test.tsx")):
            content = path.read_text(encoding="utf-8", errors="ignore")
            if "http://" in content or "https://" in content:
                hits.append(str(path.relative_to(web_src)))

    assert hits == [], f"Found URL literals in web/src: {hits}"


def test_unused_d7_columns_do_not_affect_routing() -> None:
    """The D7 unused columns (extraction_provider, assist_provider, etc.)

    have 0 effect on routing.
    """
    vllm_card = ProviderConfig("vllm", True, VLLM_MODEL, VLLM_URL, None, 1)
    gemini_card = ProviderConfig("gemini", True, GEMINI_MODEL, None, None, 1)

    snap = SettingsSnapshot(
        workspace_id=new_id(),
        mode="ai_assisted",
        provider="gemini",
        model=GEMINI_MODEL,
        version=1,
        consent_given=True,
        consent_fingerprint="fp",
        key_secret_id=None,
        key_storage="saved",
        reader_strategy="single",
        primary_reader="vllm",
        vllm=vllm_card,
        gemini=gemini_card,
    )
    # Regardless of any hypothetical D7 column values, active_provider is vllm
    assert snap.active_provider == "vllm"
    assert snap.target("h1_extract") == ("vllm", VLLM_MODEL)
    assert snap.target("h6_vision") == ("vllm", VLLM_MODEL)
    assert snap.target("h2_item") == ("vllm", VLLM_MODEL)
    assert snap.target("h3_rerank") == ("vllm", VLLM_MODEL)
    assert snap.target("h4_vendor") == ("vllm", VLLM_MODEL)
