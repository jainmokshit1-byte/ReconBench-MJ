"""Vision reader (h6_vision, AC-5) end to end on a disposable PostgreSQL 16 database.

PDF parse → vision page preparation (PDF sandbox) → AI worker runner with the
offline ``ScriptedProvider`` replaying a recorded answer → review view →
correction save. Synthetic documents only; nothing leaves the process.

These tests cover the reader's mechanics with the Gemini stub and a digital invoice
fixture. Which documents may be read (PD-2c/PD-2d: scanned invoices only, the company
server only, the switch on AI) is tested unpatched in ``test_pd2c_pd2d_reading.py``;
``_reader_mechanics_gate`` opens that gate here for invoices only.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import boto3
import httpx
import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from reconbench_app import ingestion as ingestion_module
from reconbench_app import reading_state
from reconbench_app.ai import consensus as consensus_app
from reconbench_app.ai import vision
from reconbench_app.ai.consensus import ConsensusService, enqueue_consensus_run
from reconbench_app.ai.consent import (
    AUTO_VERIFY_CONSENT_TEXT_VERSION,
    CONSENT_TEXT_VERSION,
    consent_fingerprint,
)
from reconbench_app.ai.contracts import vision_response_schema, vision_response_schema_v5
from reconbench_app.ai.enqueue import AiHookEnqueuer
from reconbench_app.ai.gate import DeploymentGate
from reconbench_app.ai.key_seal import SealingPublicKey, key_hint, seal
from reconbench_app.ai.limits import AiLimits, LimitsState
from reconbench_app.ai.provider import ProviderRequest, ProviderResult
from reconbench_app.ai.runtime_checks import SettingsSnapshot, fingerprint_for
from reconbench_app.ai.stub import ScriptedProvider
from reconbench_app.ai.worker.handlers import default_handlers
from reconbench_app.ai.worker.key_open import Keyring
from reconbench_app.ai.worker.keytool import KEYRING_NAME, PUBLIC_KEY_NAME, generate
from reconbench_app.ai.worker.openai_compat import OpenAICompatProvider
from reconbench_app.ai.worker.runner import AiRequestRunner
from reconbench_app.api.application import create_app
from reconbench_app.authentication import COOKIE_NAME, AuthContext, AuthenticationCheck
from reconbench_app.configuration import canonical_hash
from reconbench_app.db.ids import new_id
from reconbench_app.db.metadata import metadata
from reconbench_app.db.seed_data import DEFAULT_CONFIG, REASON_CODES
from reconbench_app.extraction.vision import DEFAULT_VISION_READER, vision_reader
from reconbench_app.ingestion import IngestionService
from reconbench_app.settings import Settings
from reconbench_app.storage import LocalObjectStore
from reportlab.pdfgen import canvas
from scripts.vision_fixtures import (
    HANDWRITTEN_PO,
    INVOICE_NO,
    SUPPLIER_GSTIN,
    SUPPLIER_NAME,
    recorded_response,
    synthetic_invoice_pdf,
)
from sqlalchemy.engine import Connection, Engine, make_url

SENTINEL_KEY = "AIzaSyD-synthetic-vision-key-000000000000"
MODEL = "gemini-3.8-flash"
APPROVAL = "SYNTHETIC-TEST-APPROVAL-1"
CSRF = "synthetic-csrf"
GRN_NO = "GRN/SYN/26-27/04-055"
GRN_PO = "PO/26-27/04-901"
_CREATED: list[UUID] = []
REQUESTS = metadata.tables["ai_request"]
PAGES = metadata.tables["document_page_image"]
DOCUMENTS = metadata.tables["document"]
LINKS = metadata.tables["link"]
CONSENSUS_RUNS = metadata.tables["consensus_run"]
CONSENSUS_DECISIONS = metadata.tables["consensus_decision"]


@pytest.fixture(autouse=True)
def _reader_mechanics_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test-only: the digital fixture counts as scanned and any active reader may read it.

    PO/GRN files stay unreadable (``READ_DOC_TYPES`` is not widened).
    """

    pages = vision._version_pages
    monkeypatch.setattr(
        vision, "_version_pages", lambda *args, **kwargs: (pages(*args, **kwargs)[0], True)
    )
    monkeypatch.setattr(
        SettingsSnapshot,
        "reads_scanned_invoices",
        property(lambda self: self.scanned_invoice_reader == "ai"),
    )


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    dsn = os.environ.get("RB_PHASE2_TEST_DATABASE_URL", "").strip()
    if not dsn:
        pytest.skip("RB_PHASE2_TEST_DATABASE_URL is required for disposable PostgreSQL tests")
    if not (make_url(dsn).database or "").startswith("reconbench_phase2_"):
        raise RuntimeError("vision checks require a disposable reconbench_phase2_* database")
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
    directory: Path = tmp_path_factory.mktemp("ai-vision-keys")
    generate(directory, "k1")
    return Keyring.load(directory / KEYRING_NAME), SealingPublicKey.from_file(
        directory / PUBLIC_KEY_NAME
    )


class RecordingAiQueue:
    def __init__(self) -> None:
        self.request_ids: list[UUID] = []

    def enqueue_ai_request(self, *, ai_request_id: UUID, connection: Connection) -> int:
        del connection
        self.request_ids.append(ai_request_id)
        return len(self.request_ids)


@dataclass
class _FakeQueue:
    vision_versions: list[UUID] = field(default_factory=list)

    def enqueue(self, task_name: str, *, file_id: UUID, connection: Connection) -> int:
        del task_name, file_id, connection
        return 1

    retried_by: list[UUID | None] = field(default_factory=list)

    def enqueue_vision_pages(
        self,
        *,
        workspace_id: UUID,
        document_version_id: UUID,
        connection: Connection,
        requested_by: UUID | None = None,
    ) -> int:
        del workspace_id, connection
        self.vision_versions.append(document_version_id)
        self.retried_by.append(requested_by)
        return len(self.vision_versions)


class _FakeObjectStoreClient:
    def list_buckets(self) -> dict[str, list[object]]:
        return {"Buckets": []}


@dataclass
class World:
    workspace_id: UUID
    user_id: UUID
    client: TestClient
    service: IngestionService
    ai_queue: RecordingAiQueue
    queue: _FakeQueue


def _seed(
    connection: Connection, public_key: SealingPublicKey, *, mode: str, vision_on: bool
) -> tuple[UUID, UUID]:
    workspace_id, user_id, config_id, secret_id = new_id(), new_id(), new_id(), new_id()
    _CREATED.append(workspace_id)
    connection.execute(
        sa.insert(metadata.tables["workspace"]).values(id=workspace_id, name="Vision")
    )
    connection.execute(
        sa.insert(metadata.tables["app_user"]).values(
            id=user_id,
            workspace_id=workspace_id,
            email=f"vision-{user_id}@example.invalid",
            display_name="Synthetic vision tester",
            role="manager",
            password_hash="synthetic-unusable-hash",
        )
    )
    connection.execute(
        sa.insert(metadata.tables["config_version"]).values(
            id=config_id,
            workspace_id=workspace_id,
            seq=1,
            body=DEFAULT_CONFIG,
            engine_hash=canonical_hash(DEFAULT_CONFIG["engine"]),
            body_hash=canonical_hash(DEFAULT_CONFIG),
            change_note="Synthetic vision baseline",
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
    for item in REASON_CODES:
        connection.execute(
            sa.insert(metadata.tables["reason_code"]).values(
                id=new_id(), workspace_id=workspace_id, **item
            )
        )
    sealed = seal(
        SENTINEL_KEY.encode(), public_key=public_key, workspace_id=workspace_id, secret_id=secret_id
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
        )
    )
    connection.execute(
        sa.insert(metadata.tables["ai_setting"]).values(
            id=new_id(),
            workspace_id=workspace_id,
            mode=mode,
            provider="gemini",
            model=MODEL,
            key_storage="saved",
            key_secret_id=secret_id,
            updated_by=user_id,
            vision_enabled=vision_on,
            scanned_invoice_reader="ai" if vision_on else "rules",
            consent_given=True,
            consent_fingerprint=consent_fingerprint(
                provider="gemini", model=MODEL, mode=mode, vision=vision_on
            ),
            consent_by=user_id,
            consent_at=datetime.now(UTC),
            consent_text_version=CONSENT_TEXT_VERSION,
        )
    )
    return workspace_id, user_id


@pytest.fixture
def make_world(
    engine: Engine,
    keys: tuple[Keyring, SealingPublicKey],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Any]:
    clients: list[TestClient] = []
    monkeypatch.setattr(boto3, "client", lambda *args, **kwargs: _FakeObjectStoreClient())

    def build(
        *,
        mode: str = "ai_first",
        gate: str = "enabled",
        vision_on: bool = True,
        reader: str = "vision_gemini",
    ) -> World:
        with engine.begin() as connection:
            workspace_id, user_id = _seed(connection, keys[1], mode=mode, vision_on=vision_on)
        settings = Settings(
            _env_file=None,
            environment="ci",
            database_url=engine.url.render_as_string(hide_password=False),
            s3_endpoint_url="http://127.0.0.1:1",
            s3_region="us-east-1",
            s3_bucket="synthetic-vision",
            s3_api_access_key="synthetic-access",
            s3_api_secret_key="synthetic-secret",
            public_base_url="https://example.invalid",
            argon2_time_cost=1,
            argon2_memory_cost_kib=8192,
            argon2_parallelism=1,
        )
        app = create_app(settings)
        ai_queue, queue = RecordingAiQueue(), _FakeQueue()
        storage = LocalObjectStore(tmp_path / f"objects-{workspace_id.hex}")
        service = IngestionService(
            engine,
            storage,
            queue,
            ai_hooks=AiHookEnqueuer(
                gate=DeploymentGate.from_values(gate, APPROVAL if gate == "enabled" else None),
                queue=ai_queue,
                limits=lambda: LimitsState(AiLimits()),
                vision_reader_name=reader,
            ),
        )
        app.state.ingestion_service = service
        context = AuthContext(
            session_id=new_id(),
            workspace_id=workspace_id,
            user_id=user_id,
            display_name="Synthetic vision tester",
            role="manager",
            csrf_token=CSRF,
        )
        app.state.auth_service.authenticate = lambda *args, **kwargs: AuthenticationCheck(
            context, True
        )
        client = TestClient(app, base_url="https://testserver")
        client.__enter__()
        clients.append(client)
        app.state.file_storage = storage
        client.cookies.set(COOKIE_NAME, "synthetic-session", path="/api/v1")
        return World(workspace_id, user_id, client, service, ai_queue, queue)

    yield build
    for client in clients:
        client.__exit__(None, None, None)


def _grn_pdf() -> bytes:
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(595, 842))
    pdf.setFont("Helvetica-Bold", 16)
    pdf.drawString(200, 790, "GOODS RECEIPT NOTE")
    pdf.setFont("Helvetica", 10)
    for index, line in enumerate(
        (
            f"GRN No: {GRN_NO}",
            "Date: 05/04/2026",
            f"Supplier: {SUPPLIER_NAME}",
            f"GSTIN: {SUPPLIER_GSTIN}",
            f"Reference No. & Date: {GRN_PO} 02/04/2026",
            "Synthetic Laptop Model X1      2 Nos",
        )
    ):
        pdf.drawString(40, 750 - index * 18, line)
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def _upload(world: World, content: bytes, *, name: str, zone: str = "invoice") -> UUID:
    client = world.client
    batch = client.post("/api/v1/batches", json={"label": name}, headers={"X-CSRF-Token": CSRF})
    assert batch.status_code == 201, batch.text
    batch_id = UUID(batch.json()["id"])
    upload = client.post(
        f"/api/v1/files?batch_id={batch_id}&zone={zone}",
        content=content,
        headers={
            "Content-Type": "application/octet-stream",
            "X-File-Name": name,
            "X-CSRF-Token": CSRF,
        },
    )
    assert upload.status_code == 201, upload.text
    file_id = UUID(upload.json()["file_id"])
    queued = client.post(f"/api/v1/batches/{batch_id}/process", headers={"X-CSRF-Token": CSRF})
    assert queued.status_code == 202, queued.text
    world.service.ingest_file(file_id, job_id=1)
    world.service.parse_file(file_id, job_id=2)
    documents = client.get(f"/api/v1/documents?batch_id={batch_id}").json()
    assert len(documents) == 1, documents
    return UUID(documents[0]["id"])


def _prepare(world: World) -> list[str]:
    return [
        world.service.prepare_vision_pages(world.workspace_id, version_id)
        for version_id in world.queue.vision_versions
    ]


class _Recorder(ScriptedProvider):
    """Replays answers and keeps what would have been sent."""

    def complete(self, request: ProviderRequest, *, api_key: str) -> ProviderResult:
        return super().complete(request, api_key=api_key)


def _runner(
    engine: Engine,
    keyring: Keyring,
    provider: ScriptedProvider,
    *,
    consensus_enqueue: Any | None = None,
    **limits: Any,
) -> Any:
    provider.name = "gemini"
    return AiRequestRunner(
        engine=engine,
        keyring=keyring,
        gate=DeploymentGate.from_values("enabled", APPROVAL),
        limits=lambda: LimitsState(AiLimits(**limits)),
        prices=lambda: None,
        providers={provider.name: provider},
        handlers=default_handlers(consensus_enqueue=consensus_enqueue),
    )


def _view(world: World, document_id: UUID) -> tuple[dict[str, Any], str]:
    response = world.client.get(f"/api/v1/documents/{document_id}/ai-suggestions")
    assert response.status_code == 200, response.text
    return response.json(), response.headers["etag"]


def _state(engine: Engine, document_id: UUID) -> tuple[Any, ...]:
    with engine.connect() as connection:
        row = connection.execute(
            sa.select(
                DOCUMENTS.c.current_version_id, DOCUMENTS.c.version, DOCUMENTS.c.vendor_id
            ).where(DOCUMENTS.c.id == document_id)
        ).one()
        links = connection.scalar(
            sa.select(sa.func.count())
            .select_from(LINKS)
            .where(LINKS.c.from_document_id == document_id)
        )
    return row.current_version_id, row.version, row.vendor_id, links


def _page_rows(engine: Engine, world: World) -> list[Any]:
    with engine.connect() as connection:
        return list(
            connection.execute(
                sa.select(PAGES).where(PAGES.c.workspace_id == world.workspace_id)
            ).mappings()
        )


def _vision_requests(engine: Engine, world: World) -> list[Any]:
    with engine.connect() as connection:
        return list(
            connection.execute(
                sa.select(REQUESTS)
                .where(
                    REQUESTS.c.workspace_id == world.workspace_id,
                    REQUESTS.c.hook == vision.HOOK,
                )
                .order_by(REQUESTS.c.created_at)
            ).mappings()
        )


def _reading(engine: Engine, document_id: UUID) -> tuple[str, str | None]:
    with engine.connect() as connection:
        row = connection.execute(
            sa.select(DOCUMENTS.c.reading_state, DOCUMENTS.c.reading_failure_code).where(
                DOCUMENTS.c.id == document_id
            )
        ).one()
    return str(row.reading_state), row.reading_failure_code


def _ready_invoice(
    engine: Engine, keys: tuple[Keyring, SealingPublicKey], world: World
) -> tuple[UUID, _Recorder]:
    document_id = _upload(world, synthetic_invoice_pdf(), name="vision-invoice.pdf")
    assert _prepare(world) == ["queued"]
    [request_id] = world.ai_queue.request_ids
    provider = _Recorder([json.dumps(recorded_response())])
    result = _runner(engine, keys[0], provider).run(request_id)
    assert (result.state, result.apply_outcome) == ("succeeded", "applied"), result
    return document_id, provider


def test_page_images_are_read_as_suggestions_and_never_change_the_document(
    engine: Engine, keys: tuple[Keyring, SealingPublicKey], make_world: Any
) -> None:
    world = make_world()
    document_id = _upload(world, synthetic_invoice_pdf(), name="vision-invoice.pdf")
    before = _state(engine, document_id)
    assert len(world.queue.vision_versions) == 1
    assert world.ai_queue.request_ids == []  # nothing is sent before the pages exist
    assert _prepare(world) == ["queued"]
    [row] = _page_rows(engine, world)
    assert row["image"] is not None and row["purged_at"] is None
    assert hashlib.sha256(bytes(row["image"])).hexdigest() == row["image_sha256"]
    assert {"email", "phone", "bank_account", "ifsc", "signature"} <= set(row["mask"]["counts"])
    [request] = _vision_requests(engine, world)
    assert request["redaction_version"] == "image_mask.v1"
    assert request["schema_version"] == "vision.schema.v4"
    assert request["prompt_version"] == "vision.prompt.v4.gemini"
    assert request["subject_version"] == vision.fingerprint(
        vision.load_pages(engine.connect(), world.workspace_id, request["subject_id"])
    )

    [request_id] = world.ai_queue.request_ids
    response_payload = recorded_response()
    response_payload["annotations"] = [
        {
            "kind": "handwritten",
            "text": HANDWRITTEN_PO,
            "looks_like": "po_number",
            "page": 1,
            "box_2d": [545, 95, 570, 290],
        },
        {
            "kind": "handwritten",
            "text": "15/04/2026",
            "looks_like": "date",
            "page": 1,
            "box_2d": [500, 100, 520, 250],
        },
        {
            "kind": "handwritten",
            "text": "1,234.50",
            "looks_like": "amount",
            "page": 1,
            "box_2d": [520, 100, 540, 250],
        },
        {
            "kind": "handwritten",
            "text": "not a date",
            "looks_like": "date",
            "page": 1,
            "box_2d": [540, 100, 560, 250],
        },
    ]
    provider = _Recorder([json.dumps(response_payload)])
    result = _runner(engine, keys[0], provider).run(request_id)
    assert (result.state, result.apply_outcome) == ("succeeded", "applied")
    [call] = provider.calls
    assert [image.sha256 for image in call.images] == [row["image_sha256"]]
    # Only the masked image carries document content; the text part has none.
    assert INVOICE_NO not in call.input_text and INVOICE_NO not in call.system_instruction
    assert provider.keys_seen == [SENTINEL_KEY]

    # Suggestion only: the document, its vendor and its links are unchanged.
    assert _state(engine, document_id) == before
    [after] = _page_rows(engine, world)
    assert after["image"] is None and after["purged_at"] is not None  # bytes cleared
    assert after["image_sha256"] == row["image_sha256"]  # evidence stays

    view, etag = _view(world, document_id)
    assert view["state"] == "available" and view["source"] == "vision"
    assert view["correctable"] is True
    by_field = {item["field"]: item for item in view["suggestions"] if item["scope"] == "header"}
    assert by_field["doc_no"]["value"] == INVOICE_NO
    assert by_field["vendor_gstin"]["value"] == SUPPLIER_GSTIN
    assert by_field["doc_no"]["box"] and by_field["doc_no"]["start"] is None
    # Handwritten date and amount become suggestions; unparsable does not
    assert by_field["doc_date"]["value"] == "2026-04-15"
    assert by_field["doc_date"]["handwritten"] is True
    assert by_field["grand_total"]["value"] == "1234.50"
    assert by_field["grand_total"]["handwritten"] is True
    assert not any(item["value"] == "not a date" for item in view["suggestions"])
    assert len(view["annotations"]) == 4
    assert all(item["page"] == 1 for item in view["suggestions"])

    # Refused while waiting
    refused = world.client.put(
        f"/api/v1/documents/{document_id}/correction",
        json={"doc_date": "2026-04-15"},
        headers={"If-Match": etag, "X-CSRF-Token": CSRF},
    )
    assert refused.status_code == 409 and refused.json()["code"] == "reading_pending"

    # Reading made ready
    rules = world.client.post(
        f"/api/v1/documents/{document_id}/reading/use-rules",
        headers={"If-Match": etag, "X-CSRF-Token": CSRF},
    )
    assert rules.status_code == 200
    etag = rules.headers["etag"]

    # Applying handwritten suggestion records origin person and audit
    saved = world.client.put(
        f"/api/v1/documents/{document_id}/correction",
        json={
            "doc_date": "2026-04-15",
            "reason": "Applied handwritten invoice date.",
            "ai_suggestion_refs": [
                {"ai_call_id": view["ai_call_id"], "scope": "header", "field": "doc_date"}
            ],
        },
        headers={"If-Match": etag, "X-CSRF-Token": CSRF},
    )
    assert saved.status_code == 200, saved.text
    evidence = {item["field_name"]: item for item in saved.json()["field_evidence"]}
    assert evidence["doc_date"]["source"] == "manual"
    assert evidence["doc_date"]["ai_call_id"] == view["ai_call_id"]

    audit_table = metadata.tables["audit_log"]
    with engine.connect() as connection:
        events = list(
            connection.execute(
                sa.select(audit_table).where(
                    audit_table.c.workspace_id == world.workspace_id,
                    audit_table.c.action == "document.correct",
                )
            ).mappings()
        )
    assert len(events) >= 1


@pytest.fixture
def forget_ai_values(engine: Engine) -> Iterator[Any]:
    """Remove the AI-origin evidence a test stored in the shared database.

    The migration tests rewind a copy of this database below revision 0032, which is
    refused while any value has origin AI (PD-2a, by design). The append-only trigger is
    paused for that one statement, as revisions 0024 and 0035 do.
    """

    workspaces: list[UUID] = []
    yield workspaces.append
    if workspaces:
        with engine.begin() as connection:
            connection.execute(
                sa.text("ALTER TABLE field_evidence DISABLE TRIGGER field_evidence_append_only")
            )
            connection.execute(
                sa.text(
                    "DELETE FROM field_evidence WHERE origin = 'ai' AND workspace_id = ANY(:ids)"
                ),
                {"ids": workspaces},
            )
            connection.execute(
                sa.text("ALTER TABLE field_evidence ENABLE TRIGGER field_evidence_append_only")
            )


def test_the_ai_reading_becomes_the_current_version_with_crop_provenance(
    engine: Engine,
    keys: tuple[Keyring, SealingPublicKey],
    make_world: Any,
    monkeypatch: pytest.MonkeyPatch,
    forget_ai_values: Any,
) -> None:
    """PD-2e: answer → consensus run → merged ``ai_first`` version, current and unverified."""

    world = make_world()
    forget_ai_values(world.workspace_id)
    document_id = _upload(world, synthetic_invoice_pdf(), name="consensus-invoice.pdf")
    before = _state(engine, document_id)
    assert _reading(engine, document_id) == ("waiting", None)
    assert _prepare(world) == ["queued"]
    [request_id] = world.ai_queue.request_ids
    run_ids: list[UUID] = []

    def defer_job(_connection: Connection, _workspace_id: UUID, run_id: UUID) -> None:
        run_ids.append(run_id)

    def enqueue(
        connection: Connection,
        workspace_id: UUID,
        version_id: UUID,
        ai_request_id: UUID,
        ai_call_id: UUID,
    ) -> bool:
        return enqueue_consensus_run(
            connection,
            workspace_id=workspace_id,
            document_version_id=version_id,
            ai_request_id=ai_request_id,
            ai_call_id=ai_call_id,
            defer_job=defer_job,
        )

    result = _runner(
        engine,
        keys[0],
        _Recorder([json.dumps(recorded_response())]),
        consensus_enqueue=enqueue,
    ).run(request_id)
    assert (result.state, result.apply_outcome) == ("succeeded", "applied"), result
    [consensus_run_id] = run_ids
    [request] = _vision_requests(engine, world)
    assert request["cache_key"] and request["ai_cache_id"] is None
    # WI-BE5: the request carries its reader parameters and the cache row its provenance.
    reader = vision_reader(DEFAULT_VISION_READER)
    assert reader is not None
    assert request["reader_params"] == vision.reader_params(reader)
    with engine.connect() as connection:
        cached = (
            connection.execute(
                sa.select(metadata.tables["ai_cache"]).where(
                    metadata.tables["ai_cache"].c.workspace_id == world.workspace_id,
                    metadata.tables["ai_cache"].c.cache_key == request["cache_key"],
                )
            )
            .mappings()
            .one()
        )
    assert (cached["provider"], cached["schema_version"]) == ("gemini", request["schema_version"])
    assert cached["reader_params"] == request["reader_params"]
    [page] = _page_rows(engine, world)
    assert page["image"] is not None  # retained only for the queued local crop pass

    crop_rounds: list[int] = []

    def recorded_crop_reads(
        _pages: Any, needs: Any, *, languages: tuple[str, ...], round_no: int
    ) -> dict[str, str]:
        del languages
        crop_rounds.append(round_no)
        return {need.key.text(): "" for need in needs}

    monkeypatch.setattr(consensus_app, "reread_vision_crops_isolated", recorded_crop_reads)
    followups: list[UUID] = []
    service = ConsensusService(
        engine,
        enqueue_followup=lambda _c, _w, doc_id, _f, _t: followups.append(doc_id),
        append_ai_version=world.service.append_ai_version,
    )
    assert service._process(world.workspace_id, consensus_run_id) == "succeeded"
    assert crop_rounds == [1, 2]
    after = _state(engine, document_id)
    assert after[0] != before[0]  # a new current version
    assert _reading(engine, document_id) == ("ready", None)
    assert followups == [document_id]  # vendor resolution, then mapping, run again
    with engine.connect() as connection:
        version = (
            connection.execute(
                sa.select(metadata.tables["document_version"]).where(
                    metadata.tables["document_version"].c.id == after[0]
                )
            )
            .mappings()
            .one()
        )
        ai_evidence = (
            connection.execute(
                sa.select(metadata.tables["field_evidence"]).where(
                    metadata.tables["field_evidence"].c.document_version_id == after[0],
                    metadata.tables["field_evidence"].c.origin == "ai",
                )
            )
            .mappings()
            .all()
        )
    assert (version["extraction_method"], version["reading_method"]) == ("ai_first", "ai")
    assert version["derived_from_version_id"] == before[0]
    assert version["needs_verification"] is True
    assert version["extras"]["header"]["reading_policy"] == "arb.llm"
    assert version["extras"]["header"]["cross_check"]
    assert ai_evidence
    # Every AI value: its call, unverified, below the review floor (PD-2a, MG-2, MG-10).
    assert all(row["ai_call_id"] is not None for row in ai_evidence)
    assert all(row["verification"] == "none" for row in ai_evidence)
    assert all(row["confidence"] < Decimal("0.70") for row in ai_evidence)
    # The document page names the provider and lists the AI values beside OCR (PD-4d).
    page = world.client.get(f"/api/v1/documents/{document_id}").json()
    assert page["reading_provider"] == "gemini"
    assert page["ai_values"] and all(item["verified"] is False for item in page["ai_values"])
    [after_page] = _page_rows(engine, world)
    assert after_page["image"] is None and after_page["purged_at"] is not None
    with engine.connect() as connection:
        run = (
            connection.execute(
                sa.select(CONSENSUS_RUNS).where(CONSENSUS_RUNS.c.id == consensus_run_id)
            )
            .mappings()
            .one()
        )
        decisions = (
            connection.execute(
                sa.select(CONSENSUS_DECISIONS).where(
                    CONSENSUS_DECISIONS.c.consensus_run_id == consensus_run_id
                )
            )
            .mappings()
            .all()
        )
    assert run["status"] == "succeeded" and run["summary"]
    assert run["result_version_id"] == after[0]
    assert decisions
    assert all(not item["auto_verified"] and not item["reopened"] for item in decisions)
    assert any(
        item["crop"]["outcome"] == "empty"
        and len(item["crop"]["rounds"]) == 2
        and [attempt["round"] for attempt in item["crop"]["rounds"]] == [1, 2]
        for item in decisions
        if item["crop"] is not None
    )


def test_consented_d42_field_appends_a_same_value_verified_version(
    engine: Engine,
    keys: tuple[Keyring, SealingPublicKey],
    make_world: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = make_world()
    document_id = _upload(world, synthetic_invoice_pdf(), name="verified-consensus.pdf")
    with engine.begin() as connection:
        setting = metadata.tables["ai_setting"]
        row = (
            connection.execute(
                sa.select(setting).where(setting.c.workspace_id == world.workspace_id)
            )
            .mappings()
            .one()
        )
        fingerprint = consent_fingerprint(
            provider="gemini",
            model=MODEL,
            mode="ai_first",
            vision=True,
            auto_verify_field_types=("doc_no",),
        )
        connection.execute(
            sa.update(setting)
            .where(setting.c.id == row["id"])
            .values(
                auto_verify_field_types=["doc_no"],
                consent_fingerprint=fingerprint,
                consent_text_version=AUTO_VERIFY_CONSENT_TEXT_VERSION,
                version=setting.c.version + 1,
            )
        )
    assert _prepare(world) == ["queued"]
    [request_id] = world.ai_queue.request_ids
    run_ids: list[UUID] = []

    def defer_job(_connection: Connection, _workspace_id: UUID, run_id: UUID) -> None:
        run_ids.append(run_id)

    def enqueue(
        connection: Connection,
        workspace_id: UUID,
        version_id: UUID,
        ai_request_id: UUID,
        ai_call_id: UUID,
    ) -> bool:
        return enqueue_consensus_run(
            connection,
            workspace_id=workspace_id,
            document_version_id=version_id,
            ai_request_id=ai_request_id,
            ai_call_id=ai_call_id,
            defer_job=defer_job,
        )

    result = _runner(
        engine,
        keys[0],
        _Recorder([json.dumps(recorded_response())]),
        consensus_enqueue=enqueue,
    ).run(request_id)
    assert (result.state, result.apply_outcome) == ("succeeded", "applied"), result
    [consensus_run_id] = run_ids
    # The person chose the rules reading meanwhile, so no AI version is made (plan R3);
    # D42 (the workspace opt-in) still applies to that rules version.
    chosen = world.client.post(
        f"/api/v1/documents/{document_id}/reading/use-rules",
        headers={"If-Match": _view(world, document_id)[1], "X-CSRF-Token": CSRF},
    )
    assert chosen.status_code == 200, chosen.text

    def recorded_crop_reads(
        _pages: Any, needs: Any, *, languages: tuple[str, ...], round_no: int
    ) -> dict[str, str]:
        del languages, round_no
        return {
            need.key.text(): (
                " ".join(need.value) if isinstance(need.value, tuple) else str(need.value)
            )
            for need in needs
        }

    monkeypatch.setattr(consensus_app, "reread_vision_crops_isolated", recorded_crop_reads)
    followups: list[tuple[UUID, UUID, UUID, str]] = []

    def enqueue_followup(
        _connection: Connection,
        workspace_id: UUID,
        doc_id: UUID,
        file_id: UUID,
        doc_type: str,
    ) -> None:
        followups.append((workspace_id, doc_id, file_id, doc_type))

    service = ConsensusService(engine, enqueue_followup=enqueue_followup)
    assert service._process(world.workspace_id, consensus_run_id) == "succeeded"

    with engine.connect() as connection:
        run = (
            connection.execute(
                sa.select(CONSENSUS_RUNS).where(CONSENSUS_RUNS.c.id == consensus_run_id)
            )
            .mappings()
            .one()
        )
        document = (
            connection.execute(sa.select(DOCUMENTS).where(DOCUMENTS.c.id == document_id))
            .mappings()
            .one()
        )
        assert run["result_version_id"] == document["current_version_id"]
        assert run["result_version_id"] != run["document_version_id"]
        versions = (
            connection.execute(
                sa.select(
                    metadata.tables["document_version"].c.id,
                    metadata.tables["document_version"].c.derived_from_version_id,
                    metadata.tables["document_version"].c.doc_no,
                    metadata.tables["document_version"].c.source_file_id,
                ).where(
                    metadata.tables["document_version"].c.id.in_(
                        [run["document_version_id"], run["result_version_id"]]
                    )
                )
            )
            .mappings()
            .all()
        )
        evidence = (
            connection.execute(
                sa.select(metadata.tables["field_evidence"]).where(
                    metadata.tables["field_evidence"].c.document_version_id
                    == run["result_version_id"],
                    metadata.tables["field_evidence"].c.field_name == "doc_no",
                )
            )
            .mappings()
            .one()
        )
        decision = (
            connection.execute(
                sa.select(CONSENSUS_DECISIONS).where(
                    CONSENSUS_DECISIONS.c.consensus_run_id == consensus_run_id,
                    CONSENSUS_DECISIONS.c.field_key == "header:doc_no",
                )
            )
            .mappings()
            .one()
        )
    assert len(versions) == 2
    assert next(row["doc_no"] for row in versions if row["id"] == run["result_version_id"]) == next(
        row["doc_no"] for row in versions if row["id"] == run["document_version_id"]
    )
    assert (
        next(
            row["derived_from_version_id"]
            for row in versions
            if row["id"] == run["result_version_id"]
        )
        == run["document_version_id"]
    )
    assert evidence["confidence"] == Decimal("0.98")
    assert decision["auto_verified"] is True
    source_file_id = next(
        row["source_file_id"] for row in versions if row["id"] == run["document_version_id"]
    )
    assert followups == [(world.workspace_id, document_id, source_file_id, "invoice")]


def test_correction_save_records_vision_provenance(
    engine: Engine, keys: tuple[Keyring, SealingPublicKey], make_world: Any
) -> None:
    world = make_world()
    document_id, _provider = _ready_invoice(engine, keys, world)
    view, etag = _view(world, document_id)
    ai_call_id = view["ai_call_id"]
    # Step 6 D4: no correction while the invoice waits for its reading (no merge job runs
    # here, so the reading failed); the person chooses the rules reading first.
    refused = world.client.put(
        f"/api/v1/documents/{document_id}/correction",
        json={"vendor_name": "Typo Traders"},
        headers={"If-Match": etag, "X-CSRF-Token": CSRF},
    )
    assert refused.status_code == 409 and refused.json()["code"] == "reading_pending"
    rules = world.client.post(
        f"/api/v1/documents/{document_id}/reading/use-rules",
        headers={"If-Match": etag, "X-CSRF-Token": CSRF},
    )
    assert rules.status_code == 200, rules.text
    assert rules.json()["document"]["reading_state"] == "ready"
    etag = rules.headers["etag"]
    rejected = world.client.put(
        f"/api/v1/documents/{document_id}/correction",
        json={
            "vendor_name": "Someone Else Traders",
            "ai_suggestion_refs": [
                {"ai_call_id": ai_call_id, "scope": "header", "field": "vendor_name"}
            ],
        },
        headers={"If-Match": etag, "X-CSRF-Token": CSRF},
    )
    assert rejected.status_code == 422 and rejected.json()["code"] == "ai_suggestion_ref_invalid"
    # A person first types a wrong name, then applies the page-image reading over it.
    typed = world.client.put(
        f"/api/v1/documents/{document_id}/correction",
        json={"vendor_name": "Typo Traders", "reason": "Typed by hand."},
        headers={"If-Match": etag, "X-CSRF-Token": CSRF},
    )
    assert typed.status_code == 200, typed.text
    etag = typed.headers["etag"]
    saved = world.client.put(
        f"/api/v1/documents/{document_id}/correction",
        json={
            "vendor_name": SUPPLIER_NAME,
            "reason": "Applied the page-image reading after review.",
            "ai_suggestion_refs": [
                {"ai_call_id": ai_call_id, "scope": "header", "field": "vendor_name"}
            ],
        },
        headers={"If-Match": etag, "X-CSRF-Token": CSRF},
    )
    assert saved.status_code == 200, saved.text
    evidence = {item["field_name"]: item for item in saved.json()["field_evidence"]}
    assert evidence["vendor_name"]["source"] == "manual"
    assert evidence["vendor_name"]["ai_call_id"] == ai_call_id
    assert evidence["vendor_name"]["snippet"] == SUPPLIER_NAME
    assert evidence["vendor_name"]["page"] == 1 and len(evidence["vendor_name"]["bbox"]) == 4
    # The reading stays reviewable on the corrected version.
    after, _etag = _view(world, document_id)
    assert after["state"] == "available"


def test_an_answer_nothing_can_merge_fails_the_reading(
    engine: Engine, keys: tuple[Keyring, SealingPublicKey], make_world: Any
) -> None:
    """P3 no longer hides (PD-2e/PD-2f): a failed merge enqueue fails the reading visibly."""

    world = make_world()
    document_id = _upload(world, synthetic_invoice_pdf(), name="vision-merge.pdf")
    assert _reading(engine, document_id) == ("waiting", None)
    assert _prepare(world) == ["queued"]
    [request_id] = world.ai_queue.request_ids

    def broken(*_args: Any) -> bool:
        raise TypeError("synthetic enqueue failure")

    provider = _Recorder([json.dumps(recorded_response())])
    result = _runner(engine, keys[0], provider, consensus_enqueue=broken).run(request_id)
    assert (result.state, result.apply_outcome) == ("succeeded", "applied"), result
    assert _reading(engine, document_id) == ("failed", reading_state.MERGE_FAILED)


def test_grn_pdfs_are_never_read_by_ai(engine: Engine, make_world: Any) -> None:
    """PD-2d: PO/GRN files are always read by rules, even with the reader gate open."""

    world = make_world()
    _upload(world, _grn_pdf(), name="vision-grn.pdf", zone="grn")
    assert world.queue.vision_versions == []
    assert world.ai_queue.request_ids == [] and _vision_requests(engine, world) == []
    assert _page_rows(engine, world) == []


def test_without_page_images_no_h1_is_queued(make_world: Any) -> None:
    """PD-2d: H1 is never queued (it used to replace the vision reader here)."""

    world = make_world(vision_on=False)
    _upload(world, synthetic_invoice_pdf(), name="vision-off.pdf")
    assert world.queue.vision_versions == []
    assert world.ai_queue.request_ids == []


@pytest.mark.parametrize(
    ("mode", "gate", "reader"),
    [
        ("rules_only", "enabled", "vision_gemini"),
        ("ai_first", "disabled", "vision_gemini"),
        ("ai_first", "synthetic_only", "vision_gemini"),
        ("ai_first", "enabled", "off"),
    ],
)
def test_no_page_is_prepared_unless_images_may_leave(
    engine: Engine, make_world: Any, mode: str, gate: str, reader: str
) -> None:
    world = make_world(mode=mode, gate=gate, reader=reader)
    _upload(world, synthetic_invoice_pdf(), name="vision-closed.pdf")
    assert world.queue.vision_versions == []
    assert _page_rows(engine, world) == []


def test_render_failure_is_recorded_without_a_call(
    engine: Engine, make_world: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = make_world()
    document_id = _upload(world, synthetic_invoice_pdf(), name="vision-fail.pdf")
    monkeypatch.setattr(ingestion_module, "vision_pages_isolated", lambda *args, **kwargs: None)
    assert _prepare(world) == ["failed"]
    [request] = _vision_requests(engine, world)
    assert request["request_state"] == "skipped"
    assert request["skip_code"] == vision.SKIP_PAGES_FAILED
    assert world.ai_queue.request_ids == [] and _page_rows(engine, world) == []
    view, _etag = _view(world, document_id)
    assert view["state"] == "unavailable" and view["code"] == vision.SKIP_PAGES_FAILED


def test_month_cap_stops_the_call_and_the_sweep_clears_the_bytes(
    engine: Engine, keys: tuple[Keyring, SealingPublicKey], make_world: Any
) -> None:
    world = make_world()
    _upload(world, synthetic_invoice_pdf(), name="vision-cap.pdf")
    assert _prepare(world) == ["queued"]
    [request_id] = world.ai_queue.request_ids
    provider = _Recorder([json.dumps(recorded_response())])
    result = _runner(engine, keys[0], provider, per_month_tokens=1_000).run(request_id)
    assert (result.state, result.code) == ("skipped", "MONTH_BUDGET_REACHED")
    assert provider.calls == []
    assert vision.purge_idle_pages(engine, now=datetime.now(UTC)) >= 1
    assert all(row["image"] is None for row in _page_rows(engine, world))


def test_a_superseded_version_discards_the_answer(
    engine: Engine, keys: tuple[Keyring, SealingPublicKey], make_world: Any
) -> None:
    world = make_world()
    document_id = _upload(world, synthetic_invoice_pdf(), name="vision-stale.pdf")
    assert _prepare(world) == ["queued"]
    [request_id] = world.ai_queue.request_ids
    _body, etag = _view(world, document_id)

    class _DuringCall(_Recorder):
        def complete(self, request: ProviderRequest, *, api_key: str) -> ProviderResult:
            # The person chooses the rules reading while the call runs (plan R3), then
            # corrects it, so the answer's version is no longer current.
            rules = world.client.post(
                f"/api/v1/documents/{document_id}/reading/use-rules",
                headers={"If-Match": etag, "X-CSRF-Token": CSRF},
            )
            assert rules.status_code == 200, rules.text
            saved = world.client.put(
                f"/api/v1/documents/{document_id}/correction",
                json={"doc_no": "SYN-INV-0042-R"},
                headers={"If-Match": rules.headers["etag"], "X-CSRF-Token": CSRF},
            )
            assert saved.status_code == 200, saved.text
            return super().complete(request, api_key=api_key)

    result = _runner(engine, keys[0], _DuringCall([json.dumps(recorded_response())])).run(
        request_id
    )
    assert (result.state, result.apply_outcome) == ("succeeded", "discarded_stale")
    view, _etag = _view(world, document_id)
    assert view["state"] == "unavailable" and view["code"] == "discarded_stale"
    # The late answer changes nothing: the person's rules reading stays the one used.
    assert _reading(engine, document_id) == ("ready", None)


def test_vllm_reader_reads_one_page_per_call_and_feeds_the_same_pipeline(
    engine: Engine, keys: tuple[Keyring, SealingPublicKey], make_world: Any
) -> None:
    """WI-BE2/BE3: prepare → one-page vLLM request (MockTransport) → answer → cache → consensus.

    The workspace picks the company server as its one vision reader (``single`` +
    ``vllm``, WI-BE3): the ledger row names provider ``vllm`` and the served model, no
    key is stored (key-optional path) and the worker calls the workspace's own
    allowlisted URL, not the provider's default one.
    """

    world = make_world()
    document_id = _upload(world, synthetic_invoice_pdf(), name="vllm-invoice.pdf")
    before = _state(engine, document_id)
    base_url = "http://vllm.example.test/vllm/v1"
    local_model = "qwen3.5:9b"
    with engine.begin() as connection:
        connection.execute(
            sa.insert(metadata.tables["ai_provider_config"]).values(
                id=new_id(),
                workspace_id=world.workspace_id,
                provider="vllm",
                enabled=True,
                model=local_model,
                base_url=base_url,
            )
        )
        connection.execute(
            sa.update(metadata.tables["ai_setting"])
            .where(metadata.tables["ai_setting"].c.workspace_id == world.workspace_id)
            .values(
                reader_strategy="single",
                primary_reader="vllm",
                version=metadata.tables["ai_setting"].c.version + 1,
                consent_fingerprint=fingerprint_for(
                    provider="gemini",
                    model=MODEL,
                    mode="ai_first",
                    reader_strategy="single",
                    primary_reader="vllm",
                    vllm_model=local_model,
                    vllm_base_url=base_url,
                ),
            )
        )
    world.service.ai_hooks = AiHookEnqueuer(
        gate=DeploymentGate.from_values(
            "enabled", APPROVAL, vllm_mode="enabled", vllm_endpoints=base_url
        ),
        queue=world.ai_queue,
        limits=lambda: LimitsState(AiLimits()),
    )
    assert _prepare(world) == ["queued"]
    [request_id] = world.ai_queue.request_ids
    [page] = _page_rows(engine, world)
    sent: list[httpx.Request] = []

    def server(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": MODEL, "max_model_len": 8_192}]})
        return httpx.Response(
            200,
            json={
                "model": MODEL,
                "choices": [
                    {
                        "message": {"content": json.dumps(recorded_response())},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 4_100, "completion_tokens": 900},
            },
        )

    consensus_calls: list[UUID] = []

    def enqueue(
        _connection: Connection,
        _workspace_id: UUID,
        _version_id: UUID,
        ai_request_id: UUID,
        _ai_call_id: UUID,
    ) -> bool:
        consensus_calls.append(ai_request_id)
        return False

    default_url = "http://vllm-default.example.test/v1"
    provider = OpenAICompatProvider(
        default_url,
        frozenset({default_url, base_url}),
        inner_transport=httpx.MockTransport(server),
    )
    runner = AiRequestRunner(
        engine=engine,
        keyring=keys[0],
        gate=DeploymentGate.from_values(
            "disabled", None, vllm_mode="enabled", vllm_endpoints=f"{default_url},{base_url}"
        ),
        limits=lambda: LimitsState(AiLimits()),
        prices=lambda: None,
        providers={"vllm": provider},
        handlers=default_handlers(consensus_enqueue=enqueue),
    )
    result = runner.run(request_id)

    assert (result.state, result.apply_outcome) == ("succeeded", "applied"), result
    # One call per page, then the page-1 header band (WI-Q2, D93): an invoice adds one call.
    assert [request.method for request in sent] == ["GET", "POST", "POST"]
    assert [str(request.url) for request in sent] == [
        f"{base_url}/models",
        f"{base_url}/chat/completions",
        f"{base_url}/chat/completions",
    ]
    assert all("authorization" not in request.headers for request in sent)
    body = json.loads(sent[1].read())
    assert body["model"] == local_model
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert body["response_format"]["json_schema"]["name"] == "vision_reading"
    assert body["response_format"]["json_schema"]["schema"] == vision_response_schema(
        "openai_json_schema"
    )
    assert body["messages"][0]["content"].startswith("You read one page image")  # qwen variant
    content = body["messages"][1]["content"]
    assert [part["type"] for part in content] == ["text", "text", "image_url"]
    assert content[1]["text"] == "Page 1"
    assert 1_024 <= body["max_tokens"] <= AiLimits().h6_vision_local.max_output_tokens
    assert INVOICE_NO not in json.dumps(body["messages"][:1]) + content[0]["text"]
    band = json.loads(sent[2].read())
    assert band["response_format"]["json_schema"]["schema"] == vision_response_schema_v5(
        "openai_json_schema"
    )
    assert "looks_like po_number" in band["messages"][0]["content"]  # the v4.q2 band prompt
    band_content = band["messages"][1]["content"]
    assert [part["type"] for part in band_content] == ["text", "text", "image_url"]
    assert band_content[2] != content[2]  # the band image, not the full page
    assert consensus_calls == [request_id]

    [request] = _vision_requests(engine, world)
    assert (request["provider"], request["model"]) == ("vllm", local_model)
    assert request["cache_key"] and request["usage_known"]
    assert (request["tokens_input"], request["tokens_output"]) == (8_200, 1_800)
    with engine.connect() as connection:
        cached = connection.scalar(
            sa.select(sa.func.count())
            .select_from(metadata.tables["ai_cache"])
            .where(
                metadata.tables["ai_cache"].c.workspace_id == world.workspace_id,
                metadata.tables["ai_cache"].c.cache_key == request["cache_key"],
            )
        )
    assert cached == 1
    # Still a suggestion only, through the same checks as the Gemini reader.
    assert _state(engine, document_id) == before
    view, _etag = _view(world, document_id)
    by_field = {item["field"]: item for item in view["suggestions"] if item["scope"] == "header"}
    assert by_field["doc_no"]["value"] == INVOICE_NO
    assert page["image_sha256"] == _page_rows(engine, world)[0]["image_sha256"]
