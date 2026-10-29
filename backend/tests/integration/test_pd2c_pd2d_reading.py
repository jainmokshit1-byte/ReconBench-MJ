"""PD-2c + PD-2d on a disposable PostgreSQL 16 database, with no gate patched.

PD-2c: one workspace switch "Scanned invoices: read with rules / read with AI" (default
rules, managers only, audited); AI only with a company server whose Test proved both
capabilities; the method that read each version is stored and survives a person's save.
PD-2d: 0 LLM calls for digital invoices, PO/GRN files and scanned invoices with the
switch on rules. Synthetic documents only; nothing leaves the process.
"""

from __future__ import annotations

import io
from typing import Any
from uuid import UUID

import pytest
import sqlalchemy as sa
from reconbench_app.ai.enqueue import AiHookEnqueuer
from reconbench_app.ai.gate import DeploymentGate
from reconbench_app.ai.limits import AiLimits, LimitsState
from reconbench_app.ai.runtime_checks import fingerprint_for
from reconbench_app.ai.settings_service import (
    AiServiceConfig,
    AiSettingsError,
    AiSettingsService,
    AiSettingsUpdate,
    ProviderConfigUpdate,
)
from reconbench_app.authorization import ActorContext
from reconbench_app.db.ids import new_id
from reconbench_app.db.metadata import metadata
from reportlab.pdfgen import canvas
from scripts.vision_fixtures import SUPPLIER_GSTIN, SUPPLIER_NAME, synthetic_invoice_pdf
from sqlalchemy.engine import Engine
from tests.integration.test_ai_vision import (
    APPROVAL,
    CSRF,
    MODEL,
    World,
    _grn_pdf,
    _prepare,
    engine,
    keys,
    make_world,
)
from tests.support.scan_images import scanned_invoice_pdf

# Fixtures shared with the vision mechanics tests (their gate patch is not imported).
__all__ = ["engine", "keys", "make_world"]

BASE_URL = "http://vllm.example.test/vllm/v1"
LOCAL_MODEL = "qwen3.5:9b"
REQUESTS = metadata.tables["ai_request"]
SETTINGS = metadata.tables["ai_setting"]
CONFIGS = metadata.tables["ai_provider_config"]
DOCUMENTS = metadata.tables["document"]
VERSIONS = metadata.tables["document_version"]
AUDIT = metadata.tables["audit_log"]
TEXTS = metadata.tables["document_text"]
_NOT_COPIED = frozenset({"doc_type", "created_at"})


def _company_server(engine: Engine, world: World, *, capable: bool, scanned: str) -> None:
    """The company server is the one page reader (consented); ``capable``: its Test passed."""

    with engine.begin() as connection:
        connection.execute(
            sa.insert(CONFIGS).values(
                id=new_id(),
                workspace_id=world.workspace_id,
                provider="vllm",
                enabled=True,
                model=LOCAL_MODEL,
                base_url=BASE_URL,
                supports_vision=True if capable else None,
                supports_json_schema=True if capable else None,
            )
        )
        connection.execute(
            sa.update(SETTINGS)
            .where(SETTINGS.c.workspace_id == world.workspace_id)
            .values(
                reader_strategy="single",
                primary_reader="vllm",
                scanned_invoice_reader=scanned,
                version=SETTINGS.c.version + 1,
                consent_fingerprint=fingerprint_for(
                    provider="gemini",
                    model=MODEL,
                    mode="ai_first",
                    reader_strategy="single",
                    primary_reader="vllm",
                    vllm_model=LOCAL_MODEL,
                    vllm_base_url=BASE_URL,
                ),
            )
        )
    world.service.ai_hooks = AiHookEnqueuer(
        gate=DeploymentGate.from_values(
            "enabled", APPROVAL, vllm_mode="enabled", vllm_endpoints=BASE_URL
        ),
        queue=world.ai_queue,
        limits=lambda: LimitsState(AiLimits()),
    )


def _po_pdf() -> bytes:
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(595, 842))
    pdf.setFont("Helvetica-Bold", 16)
    pdf.drawString(200, 790, "PURCHASE ORDER")
    pdf.setFont("Helvetica", 10)
    for index, line in enumerate(
        (
            "PO No: PO/SYN/26-27/04-077",
            "PO Date: 01/04/2026",
            f"Supplier: {SUPPLIER_NAME}",
            f"Supplier GSTIN: {SUPPLIER_GSTIN}",
            "1  Synthetic Laptop Model X1   8471   2 Nos   50,000.00   1,00,000.00",
            "Taxable Value: 1,00,000.00",
        )
    ):
        pdf.drawString(40, 750 - index * 18, line)
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def _upload(world: World, content: bytes, *, name: str, zone: str = "invoice") -> list[UUID]:
    """Upload and parse one file; the documents it made (PO/GRN parsing may make none)."""

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
    return [UUID(item["id"]) for item in documents]


def _current(engine: Engine, document_id: UUID) -> dict[str, Any]:
    with engine.connect() as connection:
        return dict(
            connection.execute(
                sa.select(VERSIONS, DOCUMENTS.c.doc_type)
                .join(DOCUMENTS, DOCUMENTS.c.current_version_id == VERSIONS.c.id)
                .where(DOCUMENTS.c.id == document_id)
            )
            .mappings()
            .one()
        )


def _scanned_pages(engine: Engine, version_id: UUID) -> list[bool]:
    with engine.connect() as connection:
        return [
            bool(value)
            for value in connection.scalars(
                sa.select(TEXTS.c.scanned)
                .where(TEXTS.c.document_version_id == version_id)
                .order_by(TEXTS.c.page)
            )
        ]


def _request_count(engine: Engine, world: World) -> int:
    with engine.connect() as connection:
        return int(
            connection.scalar(
                sa.select(sa.func.count())
                .select_from(REQUESTS)
                .where(REQUESTS.c.workspace_id == world.workspace_id)
            )
            or 0
        )


def _service(world: World) -> AiSettingsService:
    gate = DeploymentGate.from_values(
        "enabled", APPROVAL, vllm_mode="enabled", vllm_endpoints=BASE_URL
    )
    return AiSettingsService(
        world.service.engine,
        AiServiceConfig(gate=gate, public_key_file=None, limits_file=None, price_file=None),
        world.ai_queue,  # type: ignore[arg-type]
    )


def _version(engine: Engine, world: World) -> int:
    with engine.connect() as connection:
        return int(
            connection.scalar(
                sa.select(SETTINGS.c.version).where(SETTINGS.c.workspace_id == world.workspace_id)
            )
            or 0
        )


def _update(engine: Engine, world: World, body: AiSettingsUpdate) -> dict[str, Any]:
    return _service(world).update(
        ActorContext(world.workspace_id, world.user_id, "manager"),
        session_id=new_id(),
        expected_version=_version(engine, world),
        body=body,
    )


def _switch_audit(engine: Engine, world: World) -> list[tuple[Any, Any]]:
    with engine.connect() as connection:
        return [
            (row.before_state, row.after_state)
            for row in connection.execute(
                sa.select(AUDIT.c.before_state, AUDIT.c.after_state)
                .where(
                    AUDIT.c.workspace_id == world.workspace_id,
                    AUDIT.c.action == "ai.scanned_invoice_reader_change",
                )
                .order_by(AUDIT.c.seq)
            )
        ]


# --------------------------------------------------------------------------- PD-2d


def test_rules_switch_digital_invoices_and_po_grn_files_make_zero_ai_requests(
    engine: Engine, make_world: Any
) -> None:
    """Every upload below makes 0 ``ai_request`` rows; scans read with rules stay rules."""

    world = make_world()
    _company_server(engine, world, capable=True, scanned="rules")
    [scanned_id] = _upload(world, scanned_invoice_pdf(), name="scan-rules.pdf")
    scanned = _current(engine, scanned_id)
    assert (scanned["extraction_method"], scanned["reading_method"]) == ("pdf_ocr", "rules")
    assert _scanned_pages(engine, scanned["id"]) == [True]
    assert world.queue.vision_versions == [] and _request_count(engine, world) == 0

    # Switch on AI: still nothing for a digital invoice, a PO or a GRN file.
    with engine.begin() as connection:
        connection.execute(
            sa.update(SETTINGS)
            .where(SETTINGS.c.workspace_id == world.workspace_id)
            .values(scanned_invoice_reader="ai", version=SETTINGS.c.version + 1)
        )
    [digital_id] = _upload(world, synthetic_invoice_pdf(), name="digital.pdf")
    digital = _current(engine, digital_id)
    assert digital["extraction_method"] == "pdf_text"
    assert _scanned_pages(engine, digital["id"]) == [False]
    for document_id in _upload(world, _po_pdf(), name="po.pdf", zone="po") + _upload(
        world, _grn_pdf(), name="grn.pdf", zone="grn"
    ):
        current = _current(engine, document_id)
        assert current["doc_type"] in {"po", "grn"} and current["reading_method"] == "rules"
    assert world.queue.vision_versions == [] and _request_count(engine, world) == 0
    assert world.ai_queue.request_ids == []


def test_scanned_invoice_with_the_switch_on_ai_is_read_by_the_company_server(
    engine: Engine, make_world: Any
) -> None:
    world = make_world()
    _company_server(engine, world, capable=True, scanned="ai")
    [document_id] = _upload(world, scanned_invoice_pdf(), name="scan-ai.pdf")
    assert world.queue.vision_versions == [_current(engine, document_id)["id"]]
    assert _prepare(world) == ["queued"]
    with engine.connect() as connection:
        [request] = connection.execute(
            sa.select(REQUESTS.c.hook, REQUESTS.c.provider, REQUESTS.c.model).where(
                REQUESTS.c.workspace_id == world.workspace_id
            )
        ).all()
    assert tuple(request) == ("h6_vision", "vllm", LOCAL_MODEL)
    # The AI answer stays a suggestion until PD-2e, so the version is still read by rules.
    assert _current(engine, document_id)["reading_method"] == "rules"


def test_switch_on_ai_without_a_proven_server_reads_nothing(
    engine: Engine, make_world: Any
) -> None:
    world = make_world()
    _company_server(engine, world, capable=False, scanned="ai")
    _upload(world, scanned_invoice_pdf(), name="scan-unproven.pdf")
    assert world.queue.vision_versions == [] and _request_count(engine, world) == 0


# --------------------------------------------------------------------------- PD-2c


def test_switch_needs_a_tested_company_server_and_every_change_is_audited(
    engine: Engine, make_world: Any
) -> None:
    world = make_world(vision_on=False)
    view = _service(world).view(ActorContext(world.workspace_id, world.user_id, "manager"))
    assert view["scanned_invoices"] == {
        "reader": "rules",
        "ai_available": False,
        "ai_reading": False,
    }

    _company_server(engine, world, capable=False, scanned="rules")
    with pytest.raises(AiSettingsError) as refused:
        _update(engine, world, AiSettingsUpdate(scanned_invoices="ai"))
    assert refused.value.code == "scanned_ai_not_available"
    assert _switch_audit(engine, world) == []

    with engine.begin() as connection:
        connection.execute(
            sa.update(CONFIGS)
            .where(CONFIGS.c.workspace_id == world.workspace_id, CONFIGS.c.provider == "vllm")
            .values(supports_vision=True, supports_json_schema=True)
        )
    view = _update(engine, world, AiSettingsUpdate(scanned_invoices="ai"))
    assert view["scanned_invoices"] == {"reader": "ai", "ai_available": True, "ai_reading": True}

    # The page reader's model cannot change while it reads (PD-1b); the switch stays on AI.
    with pytest.raises(AiSettingsError) as refused:
        _update(
            engine,
            world,
            AiSettingsUpdate(
                providers=[ProviderConfigUpdate(provider="vllm", model="qwen3.5:27b")]
            ),
        )
    assert refused.value.code == "provider_capability_missing"

    # Withdrawing consent turns AI off, so the switch goes back to rules.
    view = _update(engine, world, AiSettingsUpdate(withdraw_consent=True))
    assert view["mode"] == "rules_only"
    assert view["scanned_invoices"]["reader"] == "rules"
    assert _switch_audit(engine, world) == [
        ({"scanned_invoice_reader": "rules"}, {"scanned_invoice_reader": "ai"}),
        ({"scanned_invoice_reader": "ai"}, {"scanned_invoice_reader": "rules"}),
    ]


def test_a_persons_save_keeps_the_reading_method(engine: Engine, make_world: Any) -> None:
    world = make_world(vision_on=False)
    [document_id] = _upload(world, synthetic_invoice_pdf(), name="keep-method.pdf")
    current = _current(engine, document_id)
    # Stand-in for a PD-2e AI reading: a copy of the version read with AI.
    with engine.begin() as connection:
        ai_version = {
            **{key: value for key, value in current.items() if key not in _NOT_COPIED},
            "id": new_id(),
            "seq": int(current["seq"]) + 1,
            "extraction_method": "ai_first",
            "reading_method": "ai",
        }
        connection.execute(sa.insert(VERSIONS).values(**ai_version))
        connection.execute(
            sa.update(DOCUMENTS)
            .where(DOCUMENTS.c.id == document_id)
            .values(current_version_id=ai_version["id"], version=DOCUMENTS.c.version + 1)
        )
    details = world.client.get(f"/api/v1/documents/{document_id}")
    assert details.status_code == 200 and details.json()["version"]["reading_method"] == "ai"
    saved = world.client.put(
        f"/api/v1/documents/{document_id}/correction",
        json={"doc_no": "SYN-INV-KEEP-1"},
        headers={"If-Match": details.headers["etag"], "X-CSRF-Token": CSRF},
    )
    assert saved.status_code == 200, saved.text
    after = _current(engine, document_id)
    assert (after["extraction_method"], after["reading_method"]) == ("manual", "ai")
    assert after["derived_from_version_id"] == ai_version["id"]


@pytest.mark.parametrize(
    ("method", "reading"),
    [("pdf_text", "ai"), ("pdf_ocr", "ai"), ("ai_first", "rules"), ("pdf_text", "gemini")],
)
def test_the_database_refuses_a_reading_method_that_contradicts_the_version(
    engine: Engine, make_world: Any, method: str, reading: str
) -> None:
    world = make_world(vision_on=False)
    [document_id] = _upload(world, synthetic_invoice_pdf(), name=f"check-{method}-{reading}.pdf")
    current = _current(engine, document_id)
    row = {
        **{key: value for key, value in current.items() if key not in _NOT_COPIED},
        "id": new_id(),
        "seq": int(current["seq"]) + 1,
        "extraction_method": method,
        "reading_method": reading,
    }
    with (
        pytest.raises(sa.exc.IntegrityError, match="document_version_reading_method_ck"),
        engine.begin() as connection,
    ):
        connection.execute(sa.insert(VERSIONS).values(**row))
