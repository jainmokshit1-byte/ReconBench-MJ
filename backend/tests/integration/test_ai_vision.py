"""Vision reader (h6_vision, AC-5) end to end on a disposable PostgreSQL 16 database.

PDF parse → vision page preparation (PDF sandbox) → AI worker runner with the
offline ``ScriptedProvider`` replaying a recorded answer → review view →
correction save. Synthetic documents only; nothing leaves the process.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import boto3
import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from reconbench_app import ingestion as ingestion_module
from reconbench_app.ai import vision
from reconbench_app.ai.consent import CONSENT_TEXT_VERSION, consent_fingerprint
from reconbench_app.ai.enqueue import AiHookEnqueuer
from reconbench_app.ai.gate import DeploymentGate
from reconbench_app.ai.key_seal import SealingPublicKey, key_hint, seal
from reconbench_app.ai.limits import AiLimits, LimitsState
from reconbench_app.ai.provider import ProviderRequest, ProviderResult
from reconbench_app.ai.stub import ScriptedProvider
from reconbench_app.ai.worker.handlers import default_handlers
from reconbench_app.ai.worker.key_open import Keyring
from reconbench_app.ai.worker.keytool import KEYRING_NAME, PUBLIC_KEY_NAME, generate
from reconbench_app.ai.worker.runner import AiRequestRunner
from reconbench_app.api.application import create_app
from reconbench_app.authentication import COOKIE_NAME, AuthContext, AuthenticationCheck
from reconbench_app.configuration import canonical_hash
from reconbench_app.db.ids import new_id
from reconbench_app.db.metadata import metadata
from reconbench_app.db.seed_data import DEFAULT_CONFIG, REASON_CODES
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
                for table in ("ai_setting", "secret"):
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

    def enqueue_vision_pages(
        self, *, workspace_id: UUID, document_version_id: UUID, connection: Connection
    ) -> int:
        del workspace_id, connection
        self.vision_versions.append(document_version_id)
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


def _grn_answer() -> dict[str, Any]:
    box = [80, 60, 100, 400]
    return {
        "doc_kind": "goods_receipt",
        "pages": [{"page": 1, "kind": "goods_receipt", "title": "GOODS RECEIPT NOTE"}],
        "fields": [
            {"name": "doc_no", "text": GRN_NO, "page": 1, "box_2d": box},
            {"name": "doc_date", "text": "05/04/2026", "page": 1, "box_2d": [100, 60, 120, 300]},
            {
                "name": "supplier_gstin",
                "text": SUPPLIER_GSTIN,
                "page": 1,
                "box_2d": [140, 60, 160, 400],
            },
            {
                "name": "po_ref",
                "text": GRN_PO,
                "label": "Reference No. & Date",
                "page": 1,
                "box_2d": [160, 60, 180, 500],
            },
        ],
        "lines": [
            {
                "page": 1,
                "box_2d": [180, 40, 200, 600],
                "description": "Synthetic Laptop Model X1",
                "quantity": "2 Nos",
            }
        ],
        "annotations": [],
    }


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


def _runner(engine: Engine, keyring: Keyring, provider: ScriptedProvider, **limits: Any) -> Any:
    provider.name = "gemini"
    return AiRequestRunner(
        engine=engine,
        keyring=keyring,
        gate=DeploymentGate.from_values("enabled", APPROVAL),
        limits=lambda: LimitsState(AiLimits(**limits)),
        prices=lambda: None,
        provider=provider,
        handlers=default_handlers(),
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
    assert request["schema_version"] == "vision.schema.v3"
    assert request["subject_version"] == vision.fingerprint(
        vision.load_pages(engine.connect(), world.workspace_id, request["subject_id"])
    )

    [request_id] = world.ai_queue.request_ids
    provider = _Recorder([json.dumps(recorded_response())])
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

    view, _etag = _view(world, document_id)
    assert view["state"] == "available" and view["source"] == "vision"
    assert view["correctable"] is True
    by_field = {item["field"]: item for item in view["suggestions"] if item["scope"] == "header"}
    assert by_field["doc_no"]["value"] == INVOICE_NO
    assert by_field["vendor_gstin"]["value"] == SUPPLIER_GSTIN
    assert by_field["doc_no"]["box"] and by_field["doc_no"]["start"] is None
    assert [note["text"] for note in view["annotations"]] == [HANDWRITTEN_PO]
    assert all(item["page"] == 1 for item in view["suggestions"])


def test_correction_save_records_vision_provenance(
    engine: Engine, keys: tuple[Keyring, SealingPublicKey], make_world: Any
) -> None:
    world = make_world()
    document_id, _provider = _ready_invoice(engine, keys, world)
    view, etag = _view(world, document_id)
    ai_call_id = view["ai_call_id"]
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


def test_grn_readings_are_shown_read_only(
    engine: Engine, keys: tuple[Keyring, SealingPublicKey], make_world: Any
) -> None:
    world = make_world()
    document_id = _upload(world, _grn_pdf(), name="vision-grn.pdf", zone="grn")
    assert _prepare(world) == ["queued"]
    [request_id] = world.ai_queue.request_ids
    result = _runner(engine, keys[0], _Recorder([json.dumps(_grn_answer())])).run(request_id)
    assert (result.state, result.apply_outcome) == ("succeeded", "applied")
    view, _etag = _view(world, document_id)
    assert view["state"] == "available" and view["correctable"] is False
    by_field = {item["field"]: item["value"] for item in view["suggestions"]}
    assert by_field["doc_no"] == GRN_NO and by_field["po_refs"] == GRN_PO


def test_without_page_images_h1_runs_as_before(make_world: Any) -> None:
    world = make_world(vision_on=False)
    _upload(world, synthetic_invoice_pdf(), name="vision-off.pdf")
    assert world.queue.vision_versions == []
    assert len(world.ai_queue.request_ids) == 1  # the H1 text request


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
            saved = world.client.put(
                f"/api/v1/documents/{document_id}/correction",
                json={"doc_no": "SYN-INV-0042-R"},
                headers={"If-Match": etag, "X-CSRF-Token": CSRF},
            )
            assert saved.status_code == 200, saved.text
            return super().complete(request, api_key=api_key)

    result = _runner(engine, keys[0], _DuringCall([json.dumps(recorded_response())])).run(
        request_id
    )
    assert (result.state, result.apply_outcome) == ("succeeded", "discarded_stale")
    view, _etag = _view(world, document_id)
    assert view["state"] == "unavailable" and view["code"] == "discarded_stale"
