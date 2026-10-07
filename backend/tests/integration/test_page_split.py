"""A bundle PDF is split into documents only after a person answers (accuracy plan AC-3, D-9).

Synthetic data only: the PDF reader is replaced by a fake that returns documents
built from synthetic CSV/XML records, so no OCR and no real document is involved.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import boto3
import pytest
import reconbench_app.ingestion as ingestion_module
import sqlalchemy as sa
from fastapi.testclient import TestClient
from reconbench_app.api.application import create_app
from reconbench_app.authentication import COOKIE_NAME, AuthContext, AuthenticationCheck
from reconbench_app.db.ids import new_id
from reconbench_app.db.metadata import metadata
from reconbench_app.ingestion import IngestionService
from reconbench_app.settings import Settings
from reconbench_app.storage import LocalObjectStore
from reconbench_core.extraction.pages import split_pages
from reconbench_core.parsers import parse
from reconbench_core.parsers.types import (
    PageSplit,
    PageText,
    ParseResult,
    ParserHints,
    ParseWarning,
)
from reconbench_core.schema import DocType, Document, FileFormat
from sqlalchemy.engine import Engine
from tests.integration.test_ingestion import (
    _CSV,
    _PO_CSV,
    _FakeObjectStoreClient,
    _FakeQueue,
    _pdf_bytes,
    _seed_workspace,
    ingestion_engine,  # noqa: F401 - shared module fixture
)

CSRF = {"X-CSRF-Token": "synthetic-csrf"}
_GRN_XML = (
    b"<GRN><GRNNo>GRN-2026-501</GRNNo><GRNDate>20260405</GRNDate>"
    b"<Supplier>Acme Components Pvt Ltd</Supplier><PORef>PO-2026-001</PORef>"
    b"<Line><Description>Widget A</Description><Qty>2</Qty></Line></GRN>"
)


def _document(content: bytes, doc_type: DocType, file_format: FileFormat) -> Document:
    result = parse(content, ParserHints(file_format=file_format, doc_type=doc_type))
    assert result.success and len(result.documents) == 1
    return result.documents[0]


def _marked(document: Document, pages: list[int], index: int, state: str) -> Document:
    marker = {"pages": pages, "group": index, "groups": 3, "state": state}
    header = document.header.model_copy(
        update={"extras": {**document.header.extras, "page_split": marker}}
    )
    return document.model_copy(
        update={
            "header": header,
            "needs_verification": state != "confirmed" or document.needs_verification,
            "source_locator": f"layout:p{pages[0]}-{pages[-1]}",
        }
    )


class _FakeReader:
    """Stands in for the PDF sandbox: PO p1, GRN p2, invoice p3."""

    def __init__(self) -> None:
        self.modes: list[PageSplit] = []

    def __call__(self, content: bytes, hints: ParserHints, **kwargs: Any) -> ParseResult:
        del content, kwargs
        self.modes.append(hints.page_split)
        pages = tuple(PageText(page=n, content=f"synthetic page {n}") for n in (1, 2, 3))
        invoice = _document(_CSV, DocType.INVOICE, FileFormat.CSV)
        if hints.page_split is PageSplit.OFF:
            documents: tuple[Document, ...] = (invoice,)
            warnings: tuple[ParseWarning, ...] = ()
        else:
            state = "confirmed" if hints.page_split is PageSplit.CONFIRMED else "unconfirmed"
            documents = (
                _marked(_document(_PO_CSV, DocType.PO, FileFormat.CSV), [1], 1, state),
                _marked(_document(_GRN_XML, DocType.GRN, FileFormat.XML), [2], 2, state),
                _marked(invoice, [3], 3, state),
            )
            warnings = (
                ()
                if state == "confirmed"
                else (
                    ParseWarning(
                        code="PAGE_SPLIT_SUGGESTED",
                        message="This file looks like 3 document(s): PO p1, GRN p2, INVOICE p3.",
                    ),
                )
            )
        return ParseResult(
            success=True,
            file_format=FileFormat.PDF,
            documents=documents,
            warnings=warnings,
            layout_signature="synthetic-split",
            page_texts=pages,
        )


@dataclass
class _Harness:
    client: TestClient
    service: IngestionService
    queue: _FakeQueue
    reader: _FakeReader
    workspace_id: UUID
    bundle: UUID


@contextmanager
def _harness(engine: Engine, tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> Iterator[_Harness]:
    """A workspace with a CSV PO and a bundle PDF in the invoice zone, both parsed."""

    workspace_id, user_id = _seed_workspace(engine)
    fake_client = _FakeObjectStoreClient()
    monkeypatch.setattr(boto3, "client", lambda *args, **kwargs: fake_client)
    storage = LocalObjectStore(tmp_path / "objects")
    reader = _FakeReader()
    monkeypatch.setattr(ingestion_module, "parse_pdf_isolated", reader)
    settings = Settings(
        _env_file=None,
        environment="ci",
        database_url=engine.url.render_as_string(hide_password=False),
        s3_endpoint_url="http://127.0.0.1:1",
        s3_region="us-east-1",
        s3_bucket="synthetic-split",
        s3_api_access_key="synthetic-access",
        s3_api_secret_key="synthetic-secret",
        public_base_url="https://example.invalid",
        argon2_time_cost=1,
        argon2_memory_cost_kib=8192,
        argon2_parallelism=1,
    )
    app = create_app(settings)
    queue = _FakeQueue()
    service = IngestionService(engine, storage, queue)
    app.state.ingestion_service = service
    context = AuthContext(
        session_id=new_id(),
        workspace_id=workspace_id,
        user_id=user_id,
        display_name="Synthetic Ingestion Manager",
        role="manager",
        csrf_token="synthetic-csrf",
    )
    app.state.auth_service.authenticate = lambda *args, **kwargs: AuthenticationCheck(context, True)
    with TestClient(app, base_url="https://testserver") as client:
        app.state.file_storage = storage
        client.cookies.set(COOKIE_NAME, "synthetic-session", path="/api/v1")
        batch = client.post("/api/v1/batches", json={"label": "Synthetic split"}, headers=CSRF)
        assert batch.status_code == 201, batch.text
        batch_id = batch.json()["id"]

        def upload(zone: str, name: str, content: bytes) -> UUID:
            response = client.post(
                f"/api/v1/files?batch_id={batch_id}&zone={zone}",
                content=content,
                headers={
                    "Content-Type": "application/octet-stream",
                    "X-File-Name": name,
                    **CSRF,
                },
            )
            assert response.status_code == 201, response.text
            return UUID(response.json()["file_id"])

        po_file = upload("po", "po.csv", _PO_CSV)
        bundle = upload("invoice", "bundle.pdf", _pdf_bytes("Synthetic bundle"))
        assert client.post(f"/api/v1/batches/{batch_id}/process", headers=CSRF).status_code == 202
        for file_id in (po_file, bundle):
            service.ingest_file(file_id, job_id=1)
            service.parse_file(file_id, job_id=2)
        assert reader.modes == [PageSplit.AUTO]
        yield _Harness(client, service, queue, reader, workspace_id, bundle)


def _answer(harness: _Harness, decision: str) -> Any:
    etag = harness.client.get(f"/api/v1/files/{harness.bundle}").headers["ETag"]
    return harness.client.post(
        f"/api/v1/files/{harness.bundle}/page-split",
        json={"decision": decision},
        headers={**CSRF, "If-Match": etag},
    )


def _open_questions(engine: Engine, file_id: UUID) -> int:
    warning = metadata.tables["document_warning"]
    with engine.connect() as connection:
        count = connection.scalar(
            sa.select(sa.func.count())
            .select_from(warning)
            .where(
                warning.c.source_file_id == file_id,
                warning.c.code == "PAGE_SPLIT_SUGGESTED",
                warning.c.resolved_at.is_(None),
            )
        )
    return int(count or 0)


def _current(engine: Engine, workspace_id: UUID, doc_type: str) -> dict[str, Any]:
    document = metadata.tables["document"]
    version = metadata.tables["document_version"]
    with engine.connect() as connection:
        row = (
            connection.execute(
                sa.select(
                    document.c.id,
                    document.c.withdrawn_at,
                    version.c.extraction_method,
                    version.c.extras,
                    version.c.id.label("version_id"),
                )
                .select_from(
                    document.outerjoin(version, document.c.current_version_id == version.c.id)
                )
                .where(document.c.workspace_id == workspace_id, document.c.doc_type == doc_type)
            )
            .mappings()
            .one()
        )
    return dict(row)


def test_bundle_split_waits_for_a_person_and_keep_restores_one_document(
    ingestion_engine: Engine,  # noqa: F811
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    warning = metadata.tables["document_warning"]
    text_table = metadata.tables["document_text"]
    with _harness(ingestion_engine, tmp_path, monkeypatch) as harness:
        client, bundle, workspace_id = harness.client, harness.bundle, harness.workspace_id

        # The proposal: three documents, all waiting, the question open on the file.
        file_row = client.get(f"/api/v1/files/{bundle}").json()
        assert file_row["mismatch_state"] == "suggested"
        assert file_row["docs_found"] == 3
        assert "PO p1, GRN p2, INVOICE p3" in file_row["error_message"]
        with ingestion_engine.connect() as connection:
            open_warning = connection.execute(
                sa.select(warning.c.detail).where(
                    warning.c.source_file_id == bundle,
                    warning.c.code == "PAGE_SPLIT_SUGGESTED",
                    warning.c.resolved_at.is_(None),
                )
            ).scalar_one()
        assert [group["pages"] for group in open_warning["groups"]] == [[1], [2], [3]]
        assert [group["doc_type"] for group in open_warning["groups"]] == ["po", "grn", "invoice"]

        # The PO page printed from the CSV record never replaces the CSV version.
        po = _current(ingestion_engine, workspace_id, "po")
        assert po["extraction_method"] == "csv_mapping"
        grn = _current(ingestion_engine, workspace_id, "grn")
        invoice = _current(ingestion_engine, workspace_id, "invoice")
        assert invoice["extras"]["header"]["page_split"]["state"] == "unconfirmed"
        with ingestion_engine.connect() as connection:
            invoice_pages = connection.execute(
                sa.select(text_table.c.page).where(
                    text_table.c.document_version_id == invoice["version_id"]
                )
            ).scalars()
            assert list(invoice_pages) == [3]

        # An answer needs the current version and a pending question.
        stale = client.post(
            f"/api/v1/files/{bundle}/page-split",
            json={"decision": "keep_one"},
            headers={**CSRF, "If-Match": '"1"'},
        )
        assert stale.status_code in {409, 412}, stale.text
        keep = _answer(harness, "keep_one")
        assert keep.status_code == 202, keep.text
        assert keep.json()["mismatch_state"] == "kept"
        assert keep.json()["status"] == "queued"
        assert harness.queue.jobs[-1] == ("parse_file", bundle)
        again = _answer(harness, "accept_split")
        assert again.status_code == 409, again.text

        harness.service.parse_file(bundle, job_id=3)
        assert harness.reader.modes[-1] is PageSplit.OFF

    # Kept as one: the GRN only the split produced is withdrawn, the invoice has a
    # new version without the split marker, and the CSV PO is untouched.
    withdrawn = _current(ingestion_engine, workspace_id, "grn")
    assert withdrawn["id"] == grn["id"] and withdrawn["withdrawn_at"] is not None
    kept_invoice = _current(ingestion_engine, workspace_id, "invoice")
    assert kept_invoice["id"] == invoice["id"]
    assert "page_split" not in kept_invoice["extras"]["header"]
    assert _current(ingestion_engine, workspace_id, "po")["version_id"] == po["version_id"]
    assert _open_questions(ingestion_engine, bundle) == 0


def test_accepted_split_reads_the_file_again_as_confirmed_documents(
    ingestion_engine: Engine,  # noqa: F811
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _harness(ingestion_engine, tmp_path, monkeypatch) as harness:
        accepted = _answer(harness, "accept_split")
        assert accepted.status_code == 202, accepted.text
        assert accepted.json()["mismatch_state"] == "accepted"
        harness.service.parse_file(harness.bundle, job_id=3)
        assert harness.reader.modes[-1] is PageSplit.CONFIRMED
        file_row = harness.client.get(f"/api/v1/files/{harness.bundle}").json()

    assert file_row["mismatch_state"] == "accepted"
    assert file_row["error_message"] is None
    assert _open_questions(ingestion_engine, harness.bundle) == 0
    for doc_type in ("grn", "invoice"):
        current = _current(ingestion_engine, harness.workspace_id, doc_type)
        assert current["withdrawn_at"] is None
        assert current["extras"]["header"]["page_split"]["state"] == "confirmed"
    po = _current(ingestion_engine, harness.workspace_id, "po")
    assert po["extraction_method"] == "csv_mapping"


def test_split_pages_helper_reads_the_marker() -> None:
    document = _marked(_document(_PO_CSV, DocType.PO, FileFormat.CSV), [4, 5], 2, "confirmed")
    assert split_pages(document) == (4, 5)
