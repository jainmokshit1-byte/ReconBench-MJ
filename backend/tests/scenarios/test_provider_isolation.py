"""PD-1d: Provider isolation and safety suite (Batch 2 Prompt A).

Tests strict single-provider execution, absence of cross-provider fallback,
safe handling of disconnected cards, gate enforcement, sentinel secret shielding,
and pure guards on routing and URL hygiene.
"""

from __future__ import annotations

import hashlib
import json
import os
import zipfile
from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import httpx
import pytest
import sqlalchemy as sa
from pydantic import SecretStr
from reconbench_app.ai.consent import CONSENT_TEXT_VERSION, consent_fingerprint
from reconbench_app.ai.enqueue import AiHookEnqueuer
from reconbench_app.ai.gate import DeploymentGate
from reconbench_app.ai.key_seal import SealingPublicKey, seal
from reconbench_app.ai.limits import AiLimits, LimitsState
from reconbench_app.ai.provider import ProviderRequest, ProviderResult
from reconbench_app.ai.provider_connection import (
    ConnectBody,
    ConsentToNotice,
    ProviderConnectionService,
)
from reconbench_app.ai.runtime_checks import (
    ProviderConfig,
    SettingsSnapshot,
    load_settings,
)
from reconbench_app.ai.settings_service import (
    AiServiceConfig,
    AiSettingsError,
    AiSettingsService,
    AiSettingsUpdate,
    ConsentAcceptance,
)
from reconbench_app.ai.stub import ScriptedProvider
from reconbench_app.ai.vision import RenderedPage, fingerprint, store_pages
from reconbench_app.ai.worker.gemini import GeminiProvider
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
from reconbench_app.export_service import ExportService
from reconbench_app.storage import LocalObjectStore
from scripts.vision_fixtures import recorded_response
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
    def __init__(self, responses: list[httpx.Response | Exception] | None = None) -> None:
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
            item = self.responses.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
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
                for table in ("ai_provider_config", "ai_setting", "secret"):
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
    scanned_ai: bool = False,
) -> tuple[UUID, UUID]:
    """``scanned_ai``: the switch on AI and a company server whose Test proved both
    capabilities, so it may read scanned invoices (PD-2c)."""

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
    # Sealed vLLM Secret Key
    vllm_secret_id = new_id()
    sealed_vllm = seal(
        SENTINEL_VLLM_KEY.encode(),
        public_key=public_key,
        workspace_id=workspace_id,
        secret_id=vllm_secret_id,
    )
    connection.execute(
        sa.insert(metadata.tables["secret"]).values(
            id=vllm_secret_id,
            workspace_id=workspace_id,
            purpose="ai_api_key",
            ciphertext=sealed_vllm.envelope,
            nonce=sealed_vllm.nonce,
            key_id=sealed_vllm.key_id,
            key_hint="9999",
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
            key_secret_id=vllm_secret_id,
            limits={},
            updated_by=user_id,
            updated_at=now,
            supports_vision=True if scanned_ai else None,
            supports_json_schema=True if scanned_ai else None,
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
            key_secret_id=secret_id,
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
            scanned_invoice_reader="ai" if scanned_ai else "rules",
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


def _setup_vision_document(
    conn: Connection,
    ws_id: UUID,
    uid: UUID,
    *,
    extraction_method: str = "pdf_text",
    scanned: bool = False,
) -> tuple[UUID, UUID, str]:
    batch_id = new_id()
    doc_id = new_id()
    ver_id = new_id()
    file_id = new_id()
    conn.execute(
        sa.insert(metadata.tables["batch"]).values(
            id=batch_id, workspace_id=ws_id, label="Vision Test Batch", created_by=uid
        )
    )
    conn.execute(
        sa.insert(metadata.tables["source_file"]).values(
            id=file_id,
            workspace_id=ws_id,
            batch_id=batch_id,
            sha256=hashlib.sha256(b"source_file_vision").hexdigest(),
            display_name="vision_test.pdf",
            object_key="files/vision_test.pdf",
            zone="invoice",
            zone_uploaded="invoice",
            format="pdf",
            status="parsed",
            size_bytes=200,
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
            doc_no="INV-VISION-01",
            canonical_doc_no="INV-VISION-01",
            doc_date=date(2026, 4, 1),
            grand_total=Decimal("123900.00"),
            current_version_id=ver_id,
        )
    )
    content_hash = hashlib.sha256(b"vision_content").hexdigest()
    conn.execute(
        sa.insert(metadata.tables["document_version"]).values(
            id=ver_id,
            workspace_id=ws_id,
            document_id=doc_id,
            seq=1,
            content_hash=content_hash,
            doc_no="INV-VISION-01",
            canonical_doc_no="INV-VISION-01",
            doc_date=date(2026, 4, 1),
            vendor_name="Acme Synthetic Traders",
            vendor_name_norm="acme synthetic traders",
            currency="INR",
            taxable_total=Decimal("105000.00"),
            grand_total=Decimal("123900.00"),
            po_refs=[],
            canonical_po_refs=[],
            extraction_method=extraction_method,
            extraction_conf=Decimal("0.95"),
            source_file_id=file_id,
            parser_version="1.0",
            needs_verification=True,
        )
    )
    conn.execute(
        sa.insert(metadata.tables["document_text"]).values(
            workspace_id=ws_id,
            document_version_id=ver_id,
            page=1,
            content="Synthetic invoice content",
            char_count=len("Synthetic invoice content"),
            scanned=scanned,
        )
    )
    png_data = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\n\x00\x00\x00\n\x08\x02\x00\x00"
        b"\x00\x02PX\xea\x00\x00\x00\x16IDATx\x9cc\xfc\xff\xff?\x03n\xc0\x84G\x8ea\xe4J"
        b"\x03\x00\xa5\xe3\x03\x11\xc7z\x1cU\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    page = RenderedPage(
        page=1,
        mime_type="image/png",
        data=png_data,
        sha256=hashlib.sha256(png_data).hexdigest(),
        width=10,
        height=10,
        render={"version": 1},
        mask={"boxes": []},
    )
    store_pages(conn, ws_id, ver_id, [page])
    sub_ver = fingerprint([page])
    return doc_id, ver_id, sub_ver


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
            conn, public_key, active_provider="vllm", scanned_ai=True
        )
        _doc1, ver_id1, sub_ver1 = _setup_vision_document(
            conn, ws_vllm, uid1, extraction_method="pdf_ocr", scanned=True
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
    gh_runs_1 = gemini_health(engine, runner_1.run, datetime.now(UTC))
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

        # Queue real h6_vision hook request
        req_id_1_h6 = enqueuer_1.enqueue(
            conn,
            settings_snap,
            hook="h6_vision",
            subject_type="document_version",
            subject_id=ver_id1,
            subject_version=sub_ver1,
            operation_key=f"h6:{new_id()}",
        )
        assert req_id_1_h6 is not None

    # Run real hook request
    vllm_server_1.responses.append(
        httpx.Response(
            200,
            json={
                "model": VLLM_MODEL,
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(recorded_response()),
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2},
            },
        )
    )
    res_1_h6 = runner_1.run(req_id_1_h6)
    assert res_1_h6.state == "succeeded"

    # 2. Runner check: execute a provider test request for active provider
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
    # The hook request and the company server's Test: its text and image checks (PD-1b).
    assert vllm_rows_1 == 3

    # --- Direction 2: Active = Gemini, Inactive = vLLM ---
    with engine.begin() as conn:
        ws_gemini, uid2 = _setup_dual_provider_workspace(
            conn, public_key, active_provider="gemini", scanned_ai=True
        )
        _doc2, ver_id2, sub_ver2 = _setup_vision_document(
            conn, ws_gemini, uid2, extraction_method="pdf_ocr", scanned=True
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
    print(f"VLLM_HEALTH_PROBES_WHILE_INACTIVE={probes_ran}")
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

        # Queue real h6_vision hook request
        req_id_2_h6 = enqueuer_2.enqueue(
            conn,
            settings_snap2,
            hook="h6_vision",
            subject_type="document_version",
            subject_id=ver_id2,
            subject_version=sub_ver2,
            operation_key=f"h6:{new_id()}",
        )
        assert req_id_2_h6 is not None

    # Gemini never reads scanned invoices until O-25 is re-confirmed (D103 Q11, PD-2c): the
    # worker skips the page read without a call to either provider.
    res_2_h6 = runner_2.run(req_id_2_h6)
    assert (res_2_h6.state, res_2_h6.code) == ("skipped", "NOT_ELIGIBLE")
    assert gemini_stub_2.calls == []

    # 2. Runner check: execute a provider test request for active provider
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
    assert gemini_rows_2 == 2


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

    # 3. Each gate off -> hook enqueue creates 0 ai_request rows
    # With external calls off (Gemini active)
    queue_ext_off = RecordingQueue()
    enqueuer_ext_off = AiHookEnqueuer(
        gate=gate_ext_off, queue=queue_ext_off, limits=lambda: LimitsState(AiLimits())
    )
    with engine.begin() as conn:
        ready_ext = enqueuer_ext_off.ready(conn, ws_id, "h6_vision")
        assert ready_ext is None
        gemini_req_count = conn.scalar(
            sa.text("SELECT count(*) FROM ai_request WHERE workspace_id = :w"),
            {"w": ws_id},
        )
        assert gemini_req_count == 0

    # With vLLM calls off (vLLM active)
    with engine.begin() as conn:
        ws_vllm_id, _ = _setup_dual_provider_workspace(conn, public_key, active_provider="vllm")
    queue_vllm_off = RecordingQueue()
    enqueuer_vllm_off = AiHookEnqueuer(
        gate=gate_vllm_off, queue=queue_vllm_off, limits=lambda: LimitsState(AiLimits())
    )
    with engine.begin() as conn:
        ready_vllm = enqueuer_vllm_off.ready(conn, ws_vllm_id, "h6_vision")
        assert ready_vllm is None
        vllm_req_count = conn.scalar(
            sa.text("SELECT count(*) FROM ai_request WHERE workspace_id = :w"),
            {"w": ws_vllm_id},
        )
        assert vllm_req_count == 0


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
    engine: Engine,
    keys: tuple[Keyring, SealingPublicKey],
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Sentinel keys for providers never appear in public tables, logs, responses, or workbooks."""
    keyring, public_key = keys
    with engine.begin() as conn:
        ws_id, uid = _setup_dual_provider_workspace(conn, public_key, active_provider="gemini")

    gate = DeploymentGate.from_values(
        "enabled", APPROVAL, vllm_mode="enabled", vllm_endpoints=VLLM_URL
    )
    vllm_server = MockVllmServer()
    vllm_provider = OpenAICompatProvider(
        VLLM_URL, frozenset({VLLM_URL}), inner_transport=httpx.MockTransport(vllm_server)
    )
    gemini_stub = ScriptedProvider([json.dumps({"status": "ok"})])
    gemini_stub.name = "gemini"

    runner = AiRequestRunner(
        engine=engine,
        keyring=keyring,
        gate=gate,
        limits=lambda: LimitsState(AiLimits()),
        prices=lambda: None,
        providers={"gemini": gemini_stub, "vllm": vllm_provider},
        handlers=default_handlers(),
    )
    queue = RecordingQueue()
    cfg = AiServiceConfig(gate=gate, public_key_file=None, limits_file=None, price_file=None)
    service = AiSettingsService(engine, cfg, queue)
    actor = ActorContext(ws_id, uid, "manager")

    # Run one provider test per provider through runner with stubs
    req_gemini = service.request_provider_test(
        actor, provider="gemini", ip="127.0.0.1"
    )["request_id"]
    res_gemini = runner.run(req_gemini)
    assert res_gemini.state == "succeeded"

    req_vllm = service.request_provider_test(actor, provider="vllm", ip="127.0.0.1")["request_id"]
    res_vllm = runner.run(req_vllm)
    assert res_vllm.state == "succeeded"

    # 1. Scan every table in public schema (cast each row to text; secret holds sealed bytes)
    with engine.connect() as conn:
        tables = conn.execute(
            sa.text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
            )
        ).scalars().all()
        for table in tables:
            rows = conn.execute(sa.text(f"SELECT t::text FROM {table} AS t")).scalars().all()
            for row_str in rows:
                assert SENTINEL_GEMINI_KEY not in row_str
                assert SENTINEL_VLLM_KEY not in row_str

        # Scan secret table: only sealed ciphertext/nonce, not raw keys
        sec_rows = conn.execute(
            sa.text("SELECT ciphertext, nonce, id, key_id FROM secret WHERE workspace_id = :w"),
            {"w": ws_id},
        ).all()
        for ct, nonce, _sid, _kid in sec_rows:
            assert SENTINEL_GEMINI_KEY.encode() not in bytes(ct)
            assert SENTINEL_GEMINI_KEY.encode() not in bytes(nonce)
            assert SENTINEL_VLLM_KEY.encode() not in bytes(ct)
            assert SENTINEL_VLLM_KEY.encode() not in bytes(nonce)

    # 2. GET settings and providers API responses
    settings_view = service.view(actor)
    settings_str = json.dumps(settings_view, default=str)
    assert SENTINEL_GEMINI_KEY not in settings_str
    assert SENTINEL_VLLM_KEY not in settings_str

    conn_service = ProviderConnectionService(service)
    gemini_notice = conn_service.connect_notice(
        actor, provider="gemini", tier="free", model=None, base_url=None
    )
    assert SENTINEL_GEMINI_KEY not in json.dumps(gemini_notice, default=str)
    vllm_notice = conn_service.connect_notice(
        actor, provider="vllm", tier=None, model=VLLM_MODEL, base_url=VLLM_URL
    )
    assert SENTINEL_VLLM_KEY not in json.dumps(vllm_notice, default=str)

    # 3. caplog and capsys
    assert SENTINEL_GEMINI_KEY not in caplog.text
    assert SENTINEL_VLLM_KEY not in caplog.text
    captured = capsys.readouterr()
    assert SENTINEL_GEMINI_KEY not in captured.out
    assert SENTINEL_GEMINI_KEY not in captured.err
    assert SENTINEL_VLLM_KEY not in captured.out
    assert SENTINEL_VLLM_KEY not in captured.err

    # 4. Export workbook (LocalObjectStore on tmp_path, scan XML parts)
    storage = LocalObjectStore(tmp_path / f"export-{ws_id.hex}")
    wb_path = tmp_path / "export.xlsx"
    ExportService._write_workbook(
        ExportService.__new__(ExportService),
        wb_path,
        cases=[],
        lines=[],
        context={
            "workspace_id": ws_id,
            "run_id": new_id(),
            "run": {
                "run_date": date(2026, 4, 1),
                "finished_at": datetime(2026, 4, 1, 10, 0, tzinfo=UTC),
                "status_counts": {"match": 1},
                "engine_version": "1.0",
                "rules_hash": "a" * 64,
                "ai_mode": None,
                "ai_provider": None,
                "ai_model": None,
                "config_body": {"engine": {"tolerances": {"qty": {"pct": 1}}}},
            },
            "filters": {},
            "vendor_doc_counts": {},
            "gstins": {},
        },
    )
    with wb_path.open("rb") as f:
        storage.put_fileobj(
            "export.xlsx",
            f,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    read_obj = storage.get("export.xlsx")
    with zipfile.ZipFile(read_obj.stream) as zf:
        for name in zf.namelist():
            if name.endswith((".xml", ".rels")):
                xml_content = zf.read(name).decode("utf-8", errors="ignore")
                assert SENTINEL_GEMINI_KEY not in xml_content
                assert SENTINEL_VLLM_KEY not in xml_content

    # 5. Positive control: sealed key decrypts to sentinel with test keyring
    with engine.connect() as conn:
        for row in sec_rows:
            decrypted = keyring.open(
                envelope=bytes(row[0]),
                nonce=bytes(row[1]),
                stored_key_id=str(row[3]),
                workspace_id=ws_id,
                secret_id=row[2],
            ).decode("utf-8")
            assert decrypted in (SENTINEL_GEMINI_KEY, SENTINEL_VLLM_KEY)


def test_non_admin_refused_and_audited(
    engine: Engine, keys: tuple[Keyring, SealingPublicKey]
) -> None:
    """Non-admin user without ai.manage is refused.

    Audit event is logged across all 4 actions.
    """
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
    conn_service = ProviderConnectionService(service)

    def _denied_count() -> int:
        with engine.connect() as conn:
            return conn.execute(
                sa.text(
                    "SELECT count(*) FROM audit_log "
                    "WHERE workspace_id = :w AND action = 'ai.manage_denied'"
                ),
                {"w": ws_id},
            ).scalar_one()

    def _request_count() -> int:
        with engine.connect() as conn:
            return conn.execute(
                sa.text("SELECT count(*) FROM ai_request WHERE workspace_id = :w"),
                {"w": ws_id},
            ).scalar_one()

    initial_denied = _denied_count()
    initial_requests = _request_count()

    # Action 1: Settings save (PUT)
    with pytest.raises(AiSettingsError) as exc_put:
        service.update(
            actor,
            session_id=new_id(),
            expected_version=1,
            body=AiSettingsUpdate(redaction_enabled=True),
        )
    assert exc_put.value.status == 403
    assert _denied_count() == initial_denied + 1
    assert _request_count() == initial_requests

    # Action 2: Reader switch
    with pytest.raises(AiSettingsError) as exc_switch:
        service.update(
            actor,
            session_id=new_id(),
            expected_version=1,
            body=AiSettingsUpdate(primary_reader="vllm"),
        )
    assert exc_switch.value.status == 403
    assert _denied_count() == initial_denied + 2
    assert _request_count() == initial_requests

    # Action 3: Provider connect
    with pytest.raises(AiSettingsError) as exc_connect:
        conn_service.connect(
            actor,
            session_id=new_id(),
            provider="gemini",
            body=ConnectBody(
                tier="free",
                api_key=SecretStr(SENTINEL_GEMINI_KEY),
                key_storage="saved",
                consent=ConsentToNotice(accept=True, fingerprint="0" * 64),
            ),
            ip="127.0.0.1",
        )
    assert exc_connect.value.status == 403
    assert _denied_count() == initial_denied + 3
    assert _request_count() == initial_requests

    # Action 4: Provider test
    with pytest.raises(AiSettingsError) as exc_test:
        service.request_provider_test(actor, provider="gemini", ip="127.0.0.1")
    assert exc_test.value.status == 403
    assert _denied_count() == initial_denied + 4
    assert _request_count() == initial_requests

    # Positive control: the manager role saves settings successfully
    manager_actor = ActorContext(ws_id, admin_id, "manager")
    mgr_res = service.update(
        manager_actor,
        session_id=new_id(),
        expected_version=1,
        body=AiSettingsUpdate(redaction_enabled=True),
    )
    assert mgr_res["redaction_enabled"] is True


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

    with engine.connect() as conn:
        sug_before = conn.scalar(sa.text("SELECT count(*) FROM vendor_merge_suggestion"))
        doc_before = conn.scalar(
            sa.text("SELECT count(*) FROM document WHERE workspace_id = :w"), {"w": ws_id}
        )
        ver_before = conn.scalar(
            sa.text("SELECT count(*) FROM document_version WHERE workspace_id = :w"),
            {"w": ws_id},
        )
        line_before = conn.scalar(
            sa.text("SELECT count(*) FROM doc_line WHERE workspace_id = :w"), {"w": ws_id}
        )

    result_2 = runner_disc.run(req_id_2)
    assert (result_2.state, result_2.code, result_2.apply_outcome) == (
        "succeeded",
        None,
        "discarded_revoked",
    )
    assert len(gemini_stub.calls) == 0, "No request must go to Gemini"

    with engine.connect() as conn:
        sug_after = conn.scalar(sa.text("SELECT count(*) FROM vendor_merge_suggestion"))
        doc_after = conn.scalar(
            sa.text("SELECT count(*) FROM document WHERE workspace_id = :w"), {"w": ws_id}
        )
        ver_after = conn.scalar(
            sa.text("SELECT count(*) FROM document_version WHERE workspace_id = :w"),
            {"w": ws_id},
        )
        line_after = conn.scalar(
            sa.text("SELECT count(*) FROM doc_line WHERE workspace_id = :w"), {"w": ws_id}
        )

    assert (sug_before, doc_before, ver_before, line_before) == (
        sug_after,
        doc_after,
        ver_after,
        line_after,
    ), "No suggestion or document change must be written from the disconnected card"


@pytest.mark.parametrize("case", [401, 429, 500, 503, "timeout"])
def test_no_cross_provider_fallback_on_http_errors(
    engine: Engine, keys: tuple[Keyring, SealingPublicKey], case: int | str
) -> None:
    """When active provider returns HTTP error, runner never falls back to other provider."""
    keyring, public_key = keys

    # Direction 1: Active = vLLM, Inactive = Gemini
    with engine.begin() as conn:
        ws_vllm, uid_vllm = _setup_dual_provider_workspace(conn, public_key, active_provider="vllm")

    if case == "timeout":
        vllm_response: httpx.Response | Exception = httpx.ReadTimeout("Local connection timed out")
        expected_vllm_code = "timeout"
    else:  # the runner's codes per status today; each case ends "failed"
        vllm_response = httpx.Response(case, json={"error": f"Error {case}"})
        expected_vllm_code = {
            401: "bad_key",
            429: "rate_limited",
            500: "error",
            503: "PROVIDER_UNAVAILABLE",
        }[case]

    vllm_server_1 = MockVllmServer([vllm_response])
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

    cfg = AiServiceConfig(gate=gate, public_key_file=None, limits_file=None, price_file=None)
    service_1 = AiSettingsService(engine, cfg, RecordingQueue())
    actor_1 = ActorContext(ws_vllm, uid_vllm, "manager")
    req_id_1 = service_1.request_provider_test(
        actor_1, provider="vllm", ip="127.0.0.1"
    )["request_id"]

    result_1 = runner_1.run(req_id_1)
    assert (result_1.state, result_1.code) == ("failed", expected_vllm_code)
    assert len(gemini_stub_1.calls) == 0, "Never fall back to Gemini on vLLM error"
    with engine.connect() as conn:
        gemini_rows_1 = conn.scalar(
            sa.text(
                "SELECT count(*) FROM ai_request WHERE workspace_id = :w AND provider = 'gemini'"
            ),
            {"w": ws_vllm},
        )
    assert gemini_rows_1 == 0, "No ledger row created for Gemini"

    # Direction 2: Active = Gemini, Inactive = vLLM
    with engine.begin() as conn:
        ws_gemini, uid_gemini = _setup_dual_provider_workspace(
            conn, public_key, active_provider="gemini"
        )

    if case == "timeout":
        def gemini_transport(_req: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("Gemini connection timed out")
        expected_gemini_code = "timeout"
        # The Gemini request may have reached the server, so a timeout is "uncertain".
        expected_gemini_state = "uncertain"
    else:
        gemini_status_dict = {
            401: ("UNAUTHENTICATED", "bad_key"),
            429: ("RESOURCE_EXHAUSTED", "rate_limited"),
            500: ("INTERNAL", "PROVIDER_UNAVAILABLE"),
            503: ("UNAVAILABLE", "PROVIDER_UNAVAILABLE"),
        }[case]
        def gemini_transport(_req: httpx.Request) -> httpx.Response:
            return httpx.Response(
                case,  # type: ignore[arg-type]
                json={
                    "error": {
                        "code": case,
                        "message": "error",
                        "status": gemini_status_dict[0],
                    }
                },
            )
        expected_gemini_code = gemini_status_dict[1]
        expected_gemini_state = "failed"

    vllm_server_2 = MockVllmServer()
    vllm_provider_2 = OpenAICompatProvider(
        VLLM_URL, frozenset({VLLM_URL}), inner_transport=httpx.MockTransport(vllm_server_2)
    )
    gemini_provider_2 = GeminiProvider(inner_transport=httpx.MockTransport(gemini_transport))

    runner_2 = AiRequestRunner(
        engine=engine,
        keyring=keyring,
        gate=gate,
        limits=lambda: LimitsState(AiLimits()),
        prices=lambda: None,
        providers={"gemini": gemini_provider_2, "vllm": vllm_provider_2},
        handlers=default_handlers(),
    )

    service_2 = AiSettingsService(engine, cfg, RecordingQueue())
    actor_2 = ActorContext(ws_gemini, uid_gemini, "manager")
    req_id_2 = service_2.request_provider_test(
        actor_2, provider="gemini", ip="127.0.0.1"
    )["request_id"]

    result_2 = runner_2.run(req_id_2)
    assert (result_2.state, result_2.code) == (expected_gemini_state, expected_gemini_code)
    assert len(vllm_server_2.completions) == 0, "Never fall back to vLLM on Gemini error"
    with engine.connect() as conn:
        vllm_rows_2 = conn.scalar(
            sa.text(
                "SELECT count(*) FROM ai_request WHERE workspace_id = :w AND provider = 'vllm'"
            ),
            {"w": ws_gemini},
        )
    assert vllm_rows_2 == 0, "No ledger row created for vLLM"


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


def test_unused_d7_columns_do_not_affect_routing(
    engine: Engine, keys: tuple[Keyring, SealingPublicKey]
) -> None:
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

    # DB test: write other values into D7 columns and batch.provider_override
    keyring, public_key = keys
    with engine.begin() as conn:
        ws_id, uid = _setup_dual_provider_workspace(conn, public_key, active_provider="vllm")
        batch_id = new_id()
        conn.execute(
            sa.insert(metadata.tables["batch"]).values(
                id=batch_id,
                workspace_id=ws_id,
                label="D7 Synthetic Batch",
                created_by=uid,
                provider_override="gemini",
            )
        )
        conn.execute(
            sa.update(metadata.tables["ai_setting"])
            .where(metadata.tables["ai_setting"].c.workspace_id == ws_id)
            .values(
                extraction_provider="gemini",
                assist_provider="gemini",
                shadow_provider="gemini",
                allow_provider_fallback=True,
            )
        )

    # Reload with load_settings and assert target() for every hook is unchanged
    with engine.connect() as conn:
        reloaded = load_settings(conn, ws_id)
        assert reloaded is not None
        assert reloaded.active_provider == "vllm"
        assert reloaded.target("h1_extract") == ("vllm", VLLM_MODEL)
        assert reloaded.target("h6_vision") == ("vllm", VLLM_MODEL)
        assert reloaded.target("h2_item") == ("vllm", VLLM_MODEL)
        assert reloaded.target("h3_rerank") == ("vllm", VLLM_MODEL)
        assert reloaded.target("h4_vendor") == ("vllm", VLLM_MODEL)

    # Positive control: changing primary_reader does change target()
    with engine.begin() as conn:
        conn.execute(
            sa.update(metadata.tables["ai_setting"])
            .where(metadata.tables["ai_setting"].c.workspace_id == ws_id)
            .values(primary_reader="gemini")
        )
    with engine.connect() as conn:
        changed_snap = load_settings(conn, ws_id)
        assert changed_snap is not None
        assert changed_snap.active_provider == "gemini"
        assert changed_snap.target("h1_extract") == ("gemini", GEMINI_MODEL)
        assert changed_snap.target("h6_vision") == ("gemini", GEMINI_MODEL)


def test_gemini_health_zero_requests_when_no_saved_key(
    engine: Engine, keys: tuple[Keyring, SealingPublicKey]
) -> None:
    """F4(g): Workspace whose active provider is Gemini with no saved key gets 0 health requests."""
    keyring, public_key = keys
    now = datetime.now(UTC)
    with engine.begin() as conn:
        ws_id, uid = _setup_dual_provider_workspace(conn, public_key, active_provider="gemini")
        # Ensure only ws_id's Gemini card is evaluated by updating other workspaces
        conn.execute(
            sa.update(metadata.tables["ai_provider_config"])
            .where(metadata.tables["ai_provider_config"].c.workspace_id != ws_id)
            .values(last_health_at=now)
        )
        # Remove saved key from both ai_setting and ai_provider_config
        conn.execute(
            sa.update(metadata.tables["ai_setting"])
            .where(metadata.tables["ai_setting"].c.workspace_id == ws_id)
            .values(key_secret_id=None)
        )
        conn.execute(
            sa.update(metadata.tables["ai_provider_config"])
            .where(
                metadata.tables["ai_provider_config"].c.workspace_id == ws_id,
                metadata.tables["ai_provider_config"].c.provider == "gemini",
            )
            .values(key_secret_id=None, connected_at=now, last_health_at=None)
        )

    gate = DeploymentGate.from_values(
        "enabled", APPROVAL, vllm_mode="enabled", vllm_endpoints=VLLM_URL
    )
    gemini_stub = ScriptedProvider([json.dumps({"status": "ok"})])
    gemini_stub.name = "gemini"
    vllm_server = MockVllmServer()
    vllm_provider = OpenAICompatProvider(
        VLLM_URL, frozenset({VLLM_URL}), inner_transport=httpx.MockTransport(vllm_server)
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
    runs_no_key = gemini_health(engine, runner.run, now)
    assert runs_no_key == 0, "Workspace with no saved key must get 0 gemini_health requests"

    # Positive control: attach a key to the Gemini card
    with engine.begin() as conn:
        secret_row = conn.execute(
            sa.select(metadata.tables["secret"].c.id).where(
                metadata.tables["secret"].c.workspace_id == ws_id,
                metadata.tables["secret"].c.key_hint == "0000",
            )
        ).first()
        assert secret_row is not None
        secret_id = secret_row[0]
        conn.execute(
            sa.update(metadata.tables["ai_provider_config"])
            .where(
                metadata.tables["ai_provider_config"].c.workspace_id == ws_id,
                metadata.tables["ai_provider_config"].c.provider == "gemini",
            )
            .values(key_secret_id=secret_id, connected_at=now, last_health_at=None)
        )

    runs_with_key = gemini_health(engine, runner.run, now)
    assert runs_with_key == 1, "Workspace with saved key must get 1 gemini_health request"


def test_switching_mode_to_rules_only_emits_active_provider_change_audit_row(
    engine: Engine, keys: tuple[Keyring, SealingPublicKey]
) -> None:
    """F4(i): ai_first + single vLLM -> save mode rules_only -> one ai.active_provider_change

    row with vllm -> None, and response active_provider is None; switching back writes None -> vllm.
    """
    keyring, public_key = keys
    with engine.begin() as conn:
        ws_id, uid = _setup_dual_provider_workspace(conn, public_key, active_provider="vllm")
        fp_ai_first = consent_fingerprint(
            provider="gemini",
            model=GEMINI_MODEL,
            mode="ai_first",
            reader_strategy="single",
            vision_provider="vllm",
            vision_model=VLLM_MODEL,
            vision_base_url=VLLM_URL,
        )
        conn.execute(
            sa.update(metadata.tables["ai_setting"])
            .where(metadata.tables["ai_setting"].c.workspace_id == ws_id)
            .values(
                mode="ai_first",
                reader_strategy="single",
                primary_reader="vllm",
                consent_fingerprint=fp_ai_first,
                consent_given=True,
            )
        )

    gate = DeploymentGate.from_values(
        "enabled", APPROVAL, vllm_mode="enabled", vllm_endpoints=VLLM_URL
    )
    cfg = AiServiceConfig(gate=gate, public_key_file=None, limits_file=None, price_file=None)
    service = AiSettingsService(engine, cfg, RecordingQueue())
    actor = ActorContext(ws_id, uid, "manager")

    def _provider_changes() -> list[tuple[object, object]]:
        with engine.connect() as conn:
            rows = conn.execute(
                sa.text(
                    "SELECT before_state, after_state FROM audit_log "
                    "WHERE workspace_id = :w AND action = 'ai.active_provider_change' "
                    "ORDER BY seq"
                ),
                {"w": ws_id},
            ).all()
        return [(row[0], row[1]) for row in rows]

    changes_before = _provider_changes()

    # 1. Start with ai_first + single vLLM; save mode rules_only
    v1 = service.view(actor)["version"]
    res1 = service.update(
        actor,
        session_id=new_id(),
        expected_version=v1,
        body=AiSettingsUpdate(mode="rules_only"),
    )
    assert res1["mode"] == "rules_only"

    with engine.connect() as conn:
        snap1 = load_settings(conn, ws_id)
        assert snap1 is not None and snap1.active_provider is None
    assert _provider_changes() == [
        *changes_before,
        ({"active_provider": "vllm"}, {"active_provider": None}),
    ]

    # 2. Switching back to ai_first: writes None -> vllm
    v2 = service.view(actor)["version"]
    res2 = service.update(
        actor,
        session_id=new_id(),
        expected_version=v2,
        body=AiSettingsUpdate(
            mode="ai_first",
            consent=ConsentAcceptance(accept=True, fingerprint=fp_ai_first),
        ),
    )
    assert res2["mode"] == "ai_first"

    with engine.connect() as conn:
        snap2 = load_settings(conn, ws_id)
        assert snap2 is not None and snap2.active_provider == "vllm"
    assert _provider_changes() == [
        *changes_before,
        ({"active_provider": "vllm"}, {"active_provider": None}),
        ({"active_provider": None}, {"active_provider": "vllm"}),
    ]
