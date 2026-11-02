"""Integration tests for KI-013: re-uploading a withdrawn document.

Verifies:
1. Upload -> remove -> re-upload of identical bytes revives the previous document.
2. Upload -> remove repeated 3+ times, then upload again: reuses the most recently
   withdrawn copy, revives it, leaves older copies withdrawn, and does not fail with duplicate key.
3. A collision-key owner that is withdrawn is revived, not duplicated.
4. An active document already holding the collision key marks the file failed with
   DOCUMENT_COLLISION_ACTIVE rather than crashing with an IntegrityError.
5. Non-retryable: an IntegrityError during parse marks the file failed on attempt 1.
6. A brand-new different file is unaffected and creates a new active document.
"""

from __future__ import annotations

import hashlib
import io
from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock
from uuid import UUID

import sqlalchemy as sa
from reconbench_app.authorization import ActorContext
from reconbench_app.db.ids import new_id
from reconbench_app.db.metadata import metadata
from reconbench_app.document_identity import (
    document_identity_collision_key,
    document_natural_key,
)
from reconbench_app.ingestion import PARSER_VERSION, IngestionService
from reconbench_app.jobs import (
    MAX_INFRASTRUCTURE_ATTEMPTS,
    InfrastructureRetryStrategy,
    register_ingestion_tasks,
)
from reconbench_app.settings import Settings
from reconbench_app.storage import LocalObjectStore
from reconbench_core.parsers.csv import parse_csv
from reconbench_core.parsers.types import ParserHints
from reconbench_core.schema import DocType, Document, ExtractionMethod, FileFormat, UploadZone
from sqlalchemy.engine import Engine
from tests.integration.test_phase4_task_411_413 import (
    _grn_csv,
    _Queue,
    _seed_workspace,
    _upload_csv,
    scratch_engine,
)

__all__ = ["scratch_engine"]

_GRN_MAPPING = {
    "GRN No": "doc_no",
    "GRN Date": "doc_date",
    "PO Ref": "po_refs",
}


def _upload_grn(
    ingestion: IngestionService,
    actor: ActorContext,
    batch_id: UUID,
    name: str,
    content: bytes,
    job_base: int,
) -> UUID:
    return _upload_csv(
        ingestion,
        actor,
        batch_id,
        zone=UploadZone.GRN,
        display_name=name,
        content=content,
        job_base=job_base,
        mapping=_GRN_MAPPING,
    )


def _setup(
    engine: Engine, label: str, tmp_path: Any
) -> tuple[ActorContext, IngestionService, UUID]:
    _workspace_id, actor = _seed_workspace(engine, label)
    ingestion = IngestionService(engine, LocalObjectStore(tmp_path / label), _Queue())
    batch_id = UUID(str(ingestion.create_batch(actor, label=f"KI-013 {label}")["id"]))
    return actor, ingestion, batch_id


def _parsed_grn(content: bytes) -> Document:
    hints = ParserHints(
        doc_type=DocType.GRN,
        file_format=FileFormat.CSV,
        column_mapping=_GRN_MAPPING,
    )
    return parse_csv(content, hints).documents[0]


def test_reupload_after_single_removal_revives_document(
    scratch_engine: Engine, tmp_path: Any
) -> None:
    """1. Upload -> remove -> upload the same bytes succeeds and revives the earlier document."""
    actor, ingestion, batch_id = _setup(scratch_engine, "single-revive", tmp_path)
    content = _grn_csv("SYN-GRN-101", date(2026, 11, 1), "SYN-PO-101")

    # 1. First upload creates one active document.
    file_id = _upload_grn(ingestion, actor, batch_id, "grn-101.csv", content, 100)
    doc_table = metadata.tables["document"]
    with scratch_engine.connect() as conn:
        docs = (
            conn.execute(
                sa.select(doc_table.c.id, doc_table.c.withdrawn_at).where(
                    doc_table.c.workspace_id == actor.workspace_id
                )
            )
            .mappings()
            .all()
        )
    assert len(docs) == 1
    assert docs[0]["withdrawn_at"] is None
    original_doc_id = docs[0]["id"]

    # 2. Remove the file: document becomes withdrawn.
    ingestion.soft_delete(actor, file_id)
    with scratch_engine.connect() as conn:
        doc = conn.execute(
            sa.select(doc_table.c.withdrawn_at).where(doc_table.c.id == original_doc_id)
        ).scalar_one()
    assert doc is not None

    # 3. Re-upload the same bytes: reactivates file, revives existing document.
    file_id_again = _upload_grn(ingestion, actor, batch_id, "grn-101.csv", content, 200)
    assert file_id_again == file_id

    with scratch_engine.connect() as conn:
        active_docs = (
            conn.execute(
                sa.select(doc_table.c.id, doc_table.c.withdrawn_at).where(
                    doc_table.c.workspace_id == actor.workspace_id,
                    doc_table.c.withdrawn_at.is_(None),
                )
            )
            .mappings()
            .all()
        )
    assert len(active_docs) == 1
    assert active_docs[0]["id"] == original_doc_id
    assert active_docs[0]["withdrawn_at"] is None


def test_reupload_after_repeated_removals_reuses_most_recent_withdrawn(
    scratch_engine: Engine, tmp_path: Any
) -> None:
    """2. Upload -> remove repeated with multiple withdrawn copies sharing file identity succeeds.

    This reproduces the live bug where 4 withdrawn copies exist. Exactly one active
    document is revived (the most recently withdrawn), older withdrawn copies stay
    withdrawn, and 0 duplicate documents are created.
    """
    actor, ingestion, batch_id = _setup(scratch_engine, "multi-withdrawn", tmp_path)
    content = _grn_csv("SYN-GRN-202", date(2026, 11, 1), "SYN-PO-202")
    file_sha = hashlib.sha256(content).hexdigest()

    # 1. Upload once and remove to get the reactivated file and first withdrawn document.
    file_id = _upload_grn(ingestion, actor, batch_id, "grn-202.csv", content, 100)
    ingestion.soft_delete(actor, file_id)

    doc_table = metadata.tables["document"]
    version_table = metadata.tables["document_version"]
    identity_table = metadata.tables["document_identity"]

    with scratch_engine.connect() as conn:
        first_doc = (
            conn.execute(
                sa.select(doc_table.c.id, identity_table.c.source_locator)
                .join(version_table, version_table.c.document_id == doc_table.c.id)
                .join(identity_table, identity_table.c.document_version_id == version_table.c.id)
                .where(doc_table.c.workspace_id == actor.workspace_id)
            )
            .mappings()
            .first()
        )
    assert first_doc is not None
    first_doc_id = first_doc["id"]
    source_locator = first_doc["source_locator"]

    # Make first_doc an older withdrawn copy so candidate 2 is strictly the newest.
    with scratch_engine.begin() as conn:
        conn.execute(
            sa.update(doc_table)
            .where(doc_table.c.id == first_doc_id)
            .values(withdrawn_at=datetime(2026, 10, 1, 0, 0, tzinfo=UTC))
        )

    # 2. Simulate 3 additional earlier withdrawn copies sharing the file's identity,
    # as existed in the live incident.
    withdrawn_timestamps = [
        datetime(2026, 10, 15, 10, 0, tzinfo=UTC),
        datetime(2026, 10, 20, 12, 0, tzinfo=UTC),
        datetime(2026, 11, 1, 18, 0, tzinfo=UTC),  # Most recently withdrawn
    ]
    withdrawn_doc_ids: list[UUID] = []

    with scratch_engine.begin() as conn:
        for i, ts in enumerate(withdrawn_timestamps):
            cand_doc_id = new_id()
            cand_ver_id = new_id()
            cand_natural_key = hashlib.sha256(
                f"synthetic_withdrawn_cand_{i}_{cand_doc_id.hex}".encode()
            ).hexdigest()
            conn.execute(
                sa.insert(doc_table).values(
                    id=cand_doc_id,
                    workspace_id=actor.workspace_id,
                    doc_type="grn",
                    natural_key=cand_natural_key,
                    batch_id=batch_id,
                    doc_no=f"SYN-GRN-202-{i}",
                    withdrawn_at=ts,
                )
            )
            conn.execute(
                sa.insert(version_table).values(
                    id=cand_ver_id,
                    workspace_id=actor.workspace_id,
                    document_id=cand_doc_id,
                    seq=1,
                    content_hash=hashlib.sha256(
                        f"cand_hash_{i}_{cand_doc_id.hex}".encode()
                    ).hexdigest(),
                    extraction_method=ExtractionMethod.CSV_MAPPING.value,
                    reading_method="rules",
                    needs_verification=False,
                    source_file_id=file_id,
                    parser_version=PARSER_VERSION,
                    created_by=actor.actor_id,
                )
            )
            conn.execute(
                sa.insert(identity_table).values(
                    workspace_id=actor.workspace_id,
                    document_version_id=cand_ver_id,
                    source_sha256=file_sha,
                    source_locator=source_locator,
                    page_hashes=[],
                )
            )
            withdrawn_doc_ids.append(cand_doc_id)

    # Total 4 withdrawn candidate documents now share this file's identity.
    most_recently_withdrawn_id = withdrawn_doc_ids[-1]

    # 3. Re-upload the exact same file.
    file_id_again = _upload_grn(ingestion, actor, batch_id, "grn-202.csv", content, 300)
    assert file_id_again == file_id

    # 4. Assert: Exactly ONE document is now active in the workspace.
    with scratch_engine.connect() as conn:
        all_docs = (
            conn.execute(
                sa.select(doc_table.c.id, doc_table.c.withdrawn_at).where(
                    doc_table.c.workspace_id == actor.workspace_id
                )
            )
            .mappings()
            .all()
        )

    active_docs = [d for d in all_docs if d["withdrawn_at"] is None]
    withdrawn_docs = [d for d in all_docs if d["withdrawn_at"] is not None]

    assert len(active_docs) == 1
    # It must be the most recently withdrawn copy!
    assert active_docs[0]["id"] == most_recently_withdrawn_id
    # Total count is 4: no 5th duplicate was created.
    assert len(all_docs) == 4
    assert len(withdrawn_docs) == 3
    # The older copies stay withdrawn.
    older_ids = {first_doc_id, withdrawn_doc_ids[0], withdrawn_doc_ids[1]}
    assert {d["id"] for d in withdrawn_docs} == older_ids


def test_withdrawn_collision_key_owner_is_revived_not_duplicated(
    scratch_engine: Engine, tmp_path: Any
) -> None:
    """3. A collision-key owner that is withdrawn is revived, not duplicated."""
    actor, ingestion, batch_id = _setup(scratch_engine, "collision-withdrawn", tmp_path)
    content = _grn_csv("SYN-GRN-303", date(2026, 11, 1), "SYN-PO-303")
    file_sha = hashlib.sha256(content).hexdigest()

    doc_table = metadata.tables["document"]
    version_table = metadata.tables["document_version"]
    identity_table = metadata.tables["document_identity"]

    parsed = _parsed_grn(content)
    natural_key = document_natural_key(parsed, file_sha, structured=True)
    collision_key = document_identity_collision_key(parsed, file_sha, structured=True)

    receipt = ingestion.receive_file(
        actor,
        batch_id=batch_id,
        zone=UploadZone.GRN,
        display_name="grn-303.csv",
        size_bytes=len(content),
        sha256=file_sha,
        file_format=FileFormat.CSV,
        stream=io.BytesIO(content),
    )

    doc1_id, doc2_id = new_id(), new_id()
    ver1_id, ver2_id = new_id(), new_id()

    with scratch_engine.begin() as conn:
        # Doc 1 is active and owns natural_key
        conn.execute(
            sa.insert(doc_table).values(
                id=doc1_id,
                workspace_id=actor.workspace_id,
                doc_type="grn",
                natural_key=natural_key,
                batch_id=batch_id,
                doc_no="SYN-GRN-303-1",
                withdrawn_at=None,
            )
        )
        conn.execute(
            sa.insert(version_table).values(
                id=ver1_id,
                workspace_id=actor.workspace_id,
                document_id=doc1_id,
                seq=1,
                content_hash=hashlib.sha256(b"v1").hexdigest(),
                extraction_method="csv_mapping",
                reading_method="rules",
                needs_verification=False,
                source_file_id=receipt.file_id,
                parser_version=PARSER_VERSION,
                created_by=actor.actor_id,
            )
        )
        conn.execute(
            sa.insert(identity_table).values(
                workspace_id=actor.workspace_id,
                document_version_id=ver1_id,
                source_sha256=file_sha,
                source_locator=parsed.source_locator,
                page_hashes=[],
            )
        )

        # Doc 2 is WITHDRAWN and owns collision_key
        conn.execute(
            sa.insert(doc_table).values(
                id=doc2_id,
                workspace_id=actor.workspace_id,
                doc_type="grn",
                natural_key=collision_key,
                batch_id=batch_id,
                doc_no="SYN-GRN-303-2",
                withdrawn_at=datetime(2026, 10, 30, tzinfo=UTC),
            )
        )
        conn.execute(
            sa.insert(version_table).values(
                id=ver2_id,
                workspace_id=actor.workspace_id,
                document_id=doc2_id,
                seq=1,
                content_hash=hashlib.sha256(b"v2").hexdigest(),
                extraction_method="csv_mapping",
                reading_method="rules",
                needs_verification=False,
                source_file_id=receipt.file_id,
                parser_version=PARSER_VERSION,
                created_by=actor.actor_id,
            )
        )
        conn.execute(
            sa.insert(identity_table).values(
                workspace_id=actor.workspace_id,
                document_version_id=ver2_id,
                source_sha256=file_sha,
                source_locator=parsed.source_locator,
                page_hashes=[],
            )
        )

    # Now upload the file. Because doc 1 is active, candidate_ids has an active doc,
    # so existing remains None. Natural key is owned by doc 1 -> switches to collision key.
    # It finds doc 2 is withdrawn and owns collision key -> revives doc 2!
    ingestion.queue_files(actor, batch_id=batch_id)
    ingestion.ingest_file(receipt.file_id, job_id=100)
    ingestion.parse_file(receipt.file_id, job_id=101)
    file_state = ingestion.get_file(actor.workspace_id, receipt.file_id)
    if file_state and file_state["error_code"] == "CSV_MAPPING_REQUIRED":
        ingestion.save_csv_mapping(actor, receipt.file_id, column_mapping=_GRN_MAPPING)
        ingestion.ingest_file(receipt.file_id, job_id=102)
        ingestion.parse_file(receipt.file_id, job_id=103)
        file_state = ingestion.get_file(actor.workspace_id, receipt.file_id)

    assert file_state is not None
    assert file_state["status"] == "parsed"
    assert file_state["error_code"] is None

    with scratch_engine.connect() as conn:
        doc2_withdrawn = conn.scalar(
            sa.select(doc_table.c.withdrawn_at).where(doc_table.c.id == doc2_id)
        )
    # The withdrawn collision owner was revived.
    assert doc2_withdrawn is None


def test_active_collision_key_owner_marks_file_failed_with_clear_error(
    scratch_engine: Engine, tmp_path: Any
) -> None:
    """4. Active document already holding the key -> no crash, file ends with error code."""
    actor, ingestion, batch_id = _setup(scratch_engine, "collision-active", tmp_path)
    content = _grn_csv("SYN-GRN-404", date(2026, 11, 1), "SYN-PO-404")
    file_sha = hashlib.sha256(content).hexdigest()

    doc_table = metadata.tables["document"]
    version_table = metadata.tables["document_version"]
    identity_table = metadata.tables["document_identity"]

    parsed = _parsed_grn(content)
    natural_key = document_natural_key(parsed, file_sha, structured=True)
    collision_key = document_identity_collision_key(parsed, file_sha, structured=True)

    receipt = ingestion.receive_file(
        actor,
        batch_id=batch_id,
        zone=UploadZone.GRN,
        display_name="grn-404.csv",
        size_bytes=len(content),
        sha256=file_sha,
        file_format=FileFormat.CSV,
        stream=io.BytesIO(content),
    )

    doc1_id, doc2_id = new_id(), new_id()
    ver1_id, ver2_id = new_id(), new_id()

    with scratch_engine.begin() as conn:
        # Doc 1 is ACTIVE and owns natural_key
        conn.execute(
            sa.insert(doc_table).values(
                id=doc1_id,
                workspace_id=actor.workspace_id,
                doc_type="grn",
                natural_key=natural_key,
                batch_id=batch_id,
                doc_no="SYN-GRN-404-1",
                withdrawn_at=None,
            )
        )
        conn.execute(
            sa.insert(version_table).values(
                id=ver1_id,
                workspace_id=actor.workspace_id,
                document_id=doc1_id,
                seq=1,
                content_hash=hashlib.sha256(b"v1").hexdigest(),
                extraction_method="csv_mapping",
                reading_method="rules",
                needs_verification=False,
                source_file_id=receipt.file_id,
                parser_version=PARSER_VERSION,
                created_by=actor.actor_id,
            )
        )
        conn.execute(
            sa.insert(identity_table).values(
                workspace_id=actor.workspace_id,
                document_version_id=ver1_id,
                source_sha256=file_sha,
                source_locator=parsed.source_locator,
                page_hashes=[],
            )
        )

        # Doc 2 is ACTIVE and owns collision_key
        conn.execute(
            sa.insert(doc_table).values(
                id=doc2_id,
                workspace_id=actor.workspace_id,
                doc_type="grn",
                natural_key=collision_key,
                batch_id=batch_id,
                doc_no="SYN-GRN-404-2",
                withdrawn_at=None,
            )
        )
        conn.execute(
            sa.insert(version_table).values(
                id=ver2_id,
                workspace_id=actor.workspace_id,
                document_id=doc2_id,
                seq=1,
                content_hash=hashlib.sha256(b"v2").hexdigest(),
                extraction_method="csv_mapping",
                reading_method="rules",
                needs_verification=False,
                source_file_id=receipt.file_id,
                parser_version=PARSER_VERSION,
                created_by=actor.actor_id,
            )
        )
        conn.execute(
            sa.insert(identity_table).values(
                workspace_id=actor.workspace_id,
                document_version_id=ver2_id,
                source_sha256=file_sha,
                source_locator=parsed.source_locator,
                page_hashes=[],
            )
        )

    # Ingestion should NOT crash with an unhandled IntegrityError.
    ingestion.queue_files(actor, batch_id=batch_id)
    ingestion.ingest_file(receipt.file_id, job_id=100)
    ingestion.parse_file(receipt.file_id, job_id=101)

    file_state = ingestion.get_file(actor.workspace_id, receipt.file_id)
    assert file_state is not None
    if file_state["error_code"] == "CSV_MAPPING_REQUIRED":
        ingestion.save_csv_mapping(actor, receipt.file_id, column_mapping=_GRN_MAPPING)
        ingestion.ingest_file(receipt.file_id, job_id=102)
        # This parse attempts upsert where natural_key is owned, switches to collision_key,
        # detects active collision_owner, and sets DOCUMENT_COLLISION_ACTIVE.
        ingestion.parse_file(receipt.file_id, job_id=103)
        file_state = ingestion.get_file(actor.workspace_id, receipt.file_id)
        assert file_state is not None

    assert file_state["status"] == "failed"
    assert file_state["error_code"] == "DOCUMENT_COLLISION_ACTIVE"

    # Confirm no second document was created with that collision key.
    with scratch_engine.connect() as conn:
        docs = conn.execute(
            sa.select(doc_table.c.id).where(
                doc_table.c.workspace_id == actor.workspace_id,
                doc_table.c.natural_key == collision_key,
            )
        ).all()
    assert len(docs) == 1


def test_integrity_error_during_parse_marks_file_failed_on_attempt_1_without_retrying() -> None:
    """5. Non-retryable: an IntegrityError during parse marks the file failed on attempt 1."""
    # 1. Test the task wrapper in jobs.py: IntegrityError calls _fail_file on attempt 1.
    mock_service = MagicMock()
    fake_file_id = new_id()
    mock_service.parse_file.side_effect = sa.exc.IntegrityError("stmt", "params", "orig")

    # Build task app with mock service.
    settings = Settings(
        database_url="postgresql+psycopg://postgres:unused@127.0.0.1:5432/unused",
        s3_bucket="unused",
        s3_endpoint_url="http://127.0.0.1:9000",
        s3_region="us-east-1",
        s3_api_access_key="fake-key",  # type: ignore[call-arg]
        s3_api_secret_key="fake-secret",  # type: ignore[call-arg]
        public_base_url="http://localhost:5173",
    )
    fake_engine = MagicMock(spec=Engine)
    _task_app, queue = register_ingestion_tasks(settings, fake_engine, s3_client=None)

    # Retrieve the registered parse_file task function.
    parse_task = queue.tasks["parse_file"]
    job_context = SimpleNamespace(job=SimpleNamespace(id=1, attempts=1))

    # Replace the service inside the task closure with mock_service.
    task_fn = parse_task.func
    closure_vars = {
        var_name: cell.cell_contents
        for var_name, cell in zip(
            task_fn.__code__.co_freevars, task_fn.__closure__ or (), strict=False
        )
    }
    assert "service" in closure_vars

    task_fn.__closure__[task_fn.__code__.co_freevars.index("service")].cell_contents = mock_service

    # Must complete cleanly without raising retryable error.
    task_fn(job_context, str(fake_file_id))

    # Verify _fail_file was called on attempt 1 with INTEGRITY_ERROR.
    mock_service._fail_file.assert_called_once_with(
        fake_file_id,
        "INTEGRITY_ERROR",
        "A database integrity constraint was violated.",
        job_id=1,
    )
    mock_service._record_infrastructure_failure.assert_not_called()

    # 2. Test InfrastructureRetryStrategy: refuses to retry IntegrityError.
    retry_strategy = InfrastructureRetryStrategy(
        max_attempts=MAX_INFRASTRUCTURE_ATTEMPTS,
        retry_exceptions=(sa.exc.DBAPIError,),
    )
    decision = retry_strategy.get_retry_decision(
        exception=sa.exc.IntegrityError("stmt", "params", "orig"),
        job=cast(Any, job_context.job),
    )
    assert decision is None

    # Transient DBAPIError (e.g. OperationalError) IS retried.
    transient = sa.exc.OperationalError("connection lost", "params", "orig")
    decision_transient = retry_strategy.get_retry_decision(
        exception=transient,
        job=cast(Any, job_context.job),
    )
    assert decision_transient is not None


def test_brand_new_different_file_creates_new_document(
    scratch_engine: Engine, tmp_path: Any
) -> None:
    """6. A brand-new different file is unaffected and creates a new document."""
    actor, ingestion, batch_id = _setup(scratch_engine, "brand-new", tmp_path)
    content1 = _grn_csv("SYN-GRN-501", date(2026, 11, 1), "SYN-PO-501")
    content2 = _grn_csv("SYN-GRN-502", date(2026, 11, 2), "SYN-PO-502")

    file_id1 = _upload_grn(ingestion, actor, batch_id, "grn-501.csv", content1, 100)
    file_id2 = _upload_grn(ingestion, actor, batch_id, "grn-502.csv", content2, 200)
    assert file_id1 != file_id2

    doc_table = metadata.tables["document"]
    with scratch_engine.connect() as conn:
        docs = (
            conn.execute(
                sa.select(doc_table.c.id, doc_table.c.doc_no, doc_table.c.withdrawn_at).where(
                    doc_table.c.workspace_id == actor.workspace_id
                )
            )
            .mappings()
            .all()
        )

    assert len(docs) == 2
    assert all(d["withdrawn_at"] is None for d in docs)
    doc_numbers = {d["doc_no"] for d in docs}
    assert doc_numbers == {"SYN-GRN-501", "SYN-GRN-502"}
