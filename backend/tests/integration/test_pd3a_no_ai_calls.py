"""PD-3a: Disabled AI hooks (H2, H3, H4) produce 0 AI requests across all modules.

Every 0-requests assertion has a positive control alongside it showing that
restoring the pre-D103 legacy hooks produces at least 1 AI request under the
exact same inputs and setup.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest
import sqlalchemy as sa
from reconbench_app.ai.consent import CONSENT_TEXT_VERSION, consent_fingerprint
from reconbench_app.ai.enqueue import AiHookEnqueuer
from reconbench_app.ai.gate import DeploymentGate
from reconbench_app.ai.key_seal import SealingPublicKey, seal
from reconbench_app.ai.limits import AiLimits, LimitsState
from reconbench_app.ai.worker.key_open import Keyring
from reconbench_app.ai.worker.keytool import KEYRING_NAME, PUBLIC_KEY_NAME, generate
from reconbench_app.configuration import canonical_hash
from reconbench_app.db.ids import new_id
from reconbench_app.db.metadata import metadata
from reconbench_app.db.seed_data import DEFAULT_CONFIG, REASON_CODES
from reconbench_app.mapping_service import MappingService
from reconbench_app.reconciliation_service import ReconciliationService
from reconbench_core.version import ENGINE_VERSION
from sqlalchemy.engine import Connection, Engine, make_url

LEGACY_HOOKS_BY_MODE: dict[str, tuple[str, ...]] = {
    "ai_assisted": ("h1_extract:fallback", "h2_item", "h3_rerank", "h4_vendor"),
    "ai_first": ("h1_extract:all_pdfs", "h2_item", "h3_rerank", "h4_vendor"),
}

SENTINEL_KEY = "AIzaSyD-synthetic-pd3a-key-00000000000"
MODEL = "gemini-3.8-flash"
APPROVAL = "SYNTHETIC-PD3A-APPROVAL-1"
GSTIN_A = "27AAPFU0939F1ZV"
PAN_A = "AAPFU0939F"
ITEM = "Dell Latitude 5440 Laptop"
INVOICE_DATE = date(2026, 4, 10)
PO_DATE = date(2026, 4, 1)

_CREATED: list[UUID] = []


class RecordingQueue:
    def __init__(self) -> None:
        self.request_ids: list[UUID] = []

    def enqueue_ai_request(self, *, ai_request_id: UUID, connection: Connection) -> int:
        del connection
        self.request_ids.append(ai_request_id)
        return len(self.request_ids)


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    dsn = os.environ.get("RB_PHASE2_TEST_DATABASE_URL", "").strip()
    if not dsn:
        pytest.skip("RB_PHASE2_TEST_DATABASE_URL is required for disposable PostgreSQL tests")
    if not (make_url(dsn).database or "").startswith("reconbench_phase2_"):
        raise RuntimeError("PD-3a checks require a disposable reconbench_phase2_* database")
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
    directory: Path = tmp_path_factory.mktemp("ai-pd3a-keys")
    generate(directory, "k1")
    return Keyring.load(directory / KEYRING_NAME), SealingPublicKey.from_file(
        directory / PUBLIC_KEY_NAME
    )


def _enqueuer(queue: RecordingQueue) -> AiHookEnqueuer:
    return AiHookEnqueuer(
        gate=DeploymentGate.from_values("enabled", APPROVAL),
        queue=queue,
        limits=lambda: LimitsState(AiLimits()),
    )


def _setup_workspace(
    connection: Connection, public_key: SealingPublicKey, *, mode: str = "ai_assisted"
) -> tuple[UUID, UUID]:
    workspace_id = new_id()
    user_id = new_id()
    secret_id = new_id()
    _CREATED.append(workspace_id)
    connection.execute(
        sa.insert(metadata.tables["workspace"]).values(id=workspace_id, name="PD3A Workspace")
    )
    connection.execute(
        sa.insert(metadata.tables["app_user"]).values(
            id=user_id,
            workspace_id=workspace_id,
            email=f"pd3a-{user_id}@example.invalid",
            display_name="Synthetic PD3A User",
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
            change_note="Synthetic PD3A baseline",
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
    # Sealed Secret Key
    sealed = seal(
        SENTINEL_KEY.encode(),
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
    # AI settings with current consent
    fp = consent_fingerprint(provider="gemini", model=MODEL, mode=mode)
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
            consent_given=True,
            consent_fingerprint=fp,
            consent_by=user_id,
            consent_at=datetime.now(UTC),
            consent_text_version=CONSENT_TEXT_VERSION,
        )
    )
    return workspace_id, user_id


def _add_document(
    connection: Connection,
    workspace_id: UUID,
    batch_id: UUID,
    source_file_id: UUID,
    *,
    doc_type: str,
    doc_no: str,
    doc_date: date,
    descriptions: list[str],
    vendor_id: UUID,
    document_id: UUID | None = None,
    po_refs: list[str] | None = None,
) -> UUID:
    document_id = document_id or new_id()
    version_id = new_id()
    total = Decimal("100.00") * len(descriptions)
    key = hashlib.sha256(f"{workspace_id}:{doc_type}:{doc_no}".encode()).hexdigest()
    connection.execute(
        sa.insert(metadata.tables["document"]).values(
            id=document_id,
            workspace_id=workspace_id,
            doc_type=doc_type,
            natural_key=key,
            batch_id=batch_id,
            doc_no=doc_no,
            canonical_doc_no=doc_no,
            doc_date=doc_date,
            grand_total=total,
            vendor_id=vendor_id,
        )
    )
    refs = po_refs or []
    connection.execute(
        sa.insert(metadata.tables["document_version"]).values(
            id=version_id,
            workspace_id=workspace_id,
            document_id=document_id,
            seq=1,
            content_hash=key,
            doc_no=doc_no,
            canonical_doc_no=doc_no,
            doc_date=doc_date,
            vendor_name="Synthetic Supplies",
            vendor_name_norm="synthetic supplies",
            currency="INR",
            taxable_total=total,
            grand_total=total,
            po_refs=refs,
            canonical_po_refs=refs,
            extraction_method="csv_mapping",
            extraction_conf=Decimal("0.90"),
            source_file_id=source_file_id,
            parser_version="pd3a-test",
            needs_verification=False,
        )
    )
    for line_no, description in enumerate(descriptions, start=1):
        connection.execute(
            sa.insert(metadata.tables["doc_line"]).values(
                id=new_id(),
                workspace_id=workspace_id,
                document_version_id=version_id,
                line_no=line_no,
                line_type="goods",
                description=description,
                description_norm=description.casefold(),
                tokens=description.casefold().split(),
                hsn_sac="84716060",
                uom="NOS",
                uom_as_read="NOS",
                quantity=Decimal("1"),
                rate=Decimal("100.00"),
                amount=Decimal("100.00"),
                extras={},
            )
        )
    connection.execute(
        sa.update(metadata.tables["document"])
        .where(metadata.tables["document"].c.id == document_id)
        .values(current_version_id=version_id)
    )
    return document_id


def test_pd3a_reconciliation_makes_zero_ai_requests(
    engine: Engine, keys: tuple[Keyring, SealingPublicKey], monkeypatch: pytest.MonkeyPatch
) -> None:
    """PD-3a: Reconciliation run queues 0 H2 item requests under default HOOKS_BY_MODE.

    Positive control: Enabling legacy hooks on the identical setup queues >= 1 H2 request.
    """
    keyring, public_key = keys
    with engine.begin() as connection:
        workspace_id, user_id = _setup_workspace(connection, public_key)
        batch_id, source_file_id, vendor_id = new_id(), new_id(), new_id()
        connection.execute(
            sa.insert(metadata.tables["batch"]).values(
                id=batch_id, workspace_id=workspace_id, label="PD3A H2", created_by=user_id
            )
        )
        connection.execute(
            sa.insert(metadata.tables["source_file"]).values(
                id=source_file_id,
                workspace_id=workspace_id,
                batch_id=batch_id,
                sha256=hashlib.sha256(f"pd3a-h2:{workspace_id}".encode()).hexdigest(),
                display_name="pd3a-h2.csv",
                zone="invoice",
                zone_uploaded="invoice",
                format="csv",
                status="parsed",
                size_bytes=128,
                object_key=f"{workspace_id}/pd3a-h2",
                docs_found=2,
                docs_new=2,
                uploaded_by=user_id,
            )
        )
        connection.execute(
            sa.insert(metadata.tables["vendor"]).values(
                id=vendor_id,
                workspace_id=workspace_id,
                canonical_name="Synthetic H2 Supplies",
                normalized_name="synthetic h2 supplies",
                anchor="name",
            )
        )
        lines = (
            ("HP LaserJet Pro M404dn Printer", "HP LaserJet Pro M404dn Printer"),
            (
                f"Logitech wireless desktop set ref {GSTIN_A}",
                "Logitech wireless keyboard and mouse combo",
            ),
            ("Kingston DDR4 RAM 16GB", "Kingston DDR4 RAM 8GB"),
        )
        _add_document(
            connection,
            workspace_id,
            batch_id,
            source_file_id,
            doc_type="po",
            doc_no="PO-H2-100",
            doc_date=PO_DATE,
            descriptions=[po for _inv, po in lines],
            vendor_id=vendor_id,
        )
        _add_document(
            connection,
            workspace_id,
            batch_id,
            source_file_id,
            doc_type="invoice",
            doc_no="INV-H2-100",
            doc_date=INVOICE_DATE,
            descriptions=[inv for inv, _po in lines],
            vendor_id=vendor_id,
            po_refs=["PO-H2-100"],
        )

    # Link them first
    MappingService(engine).resolve_workspace(workspace_id)

    # 1. Run reconciliation under shipped PD-3a hooks (H2 off)
    queue = RecordingQueue()
    service = ReconciliationService(engine, ai_hooks=_enqueuer(queue))
    run_id = new_id()
    with engine.begin() as conn:
        cfg = conn.execute(
            sa.select(metadata.tables["config_active"].c.config_version_id).where(
                metadata.tables["config_active"].c.workspace_id == workspace_id
            )
        ).scalar_one()
        conn.execute(
            sa.insert(metadata.tables["recon_run"]).values(
                id=run_id,
                workspace_id=workspace_id,
                status="queued",
                trigger_kind="user",
                scope={"kind": "all"},
                scope_hash=hashlib.sha256(b'{"kind":"all"}').hexdigest(),
                run_date=date(2026, 4, 15),
                engine_version=ENGINE_VERSION,
                rules_hash="beefcafe" * 8,
                config_version_id=cfg,
                ai_mode="rules_only",
                ai_consent=False,
                total_cases=0,
                done_cases=0,
                status_counts={},
                metrics={},
                requested_by=user_id,
            )
        )
    service.evaluate_run(run_id)

    # Assert 0 requests queued under PD-3a
    assert len(queue.request_ids) == 0
    with engine.connect() as conn:
        count = conn.scalar(
            sa.text("SELECT count(*) FROM ai_request WHERE workspace_id = :w"), {"w": workspace_id}
        )
    assert count == 0

    # 2. Positive Control: under LEGACY_HOOKS_BY_MODE with matching consent,
    # identical recon queues H2
    monkeypatch.setattr("reconbench_app.ai.consent.HOOKS_BY_MODE", LEGACY_HOOKS_BY_MODE)
    with engine.begin() as conn:
        legacy_fp = consent_fingerprint(provider="gemini", model=MODEL, mode="ai_assisted")
        conn.execute(
            sa.update(metadata.tables["ai_setting"])
            .where(metadata.tables["ai_setting"].c.workspace_id == workspace_id)
            .values(consent_fingerprint=legacy_fp)
        )
    pos_queue = RecordingQueue()
    pos_service = ReconciliationService(engine, ai_hooks=_enqueuer(pos_queue))
    run_id2 = new_id()
    with engine.begin() as conn:
        conn.execute(
            sa.insert(metadata.tables["recon_run"]).values(
                id=run_id2,
                workspace_id=workspace_id,
                status="queued",
                trigger_kind="user",
                scope={"kind": "all"},
                scope_hash=hashlib.sha256(b'{"kind":"all2"}').hexdigest(),
                run_date=date(2026, 4, 15),
                engine_version=ENGINE_VERSION,
                rules_hash="beefcafe" * 8,
                config_version_id=cfg,
                ai_mode="rules_only",
                ai_consent=False,
                total_cases=0,
                done_cases=0,
                status_counts={},
                metrics={},
                requested_by=user_id,
            )
        )
    pos_service.evaluate_run(run_id2)
    assert len(pos_queue.request_ids) >= 1
    with engine.connect() as conn:
        pos_count = conn.scalar(
            sa.text(
                "SELECT count(*) FROM ai_request WHERE workspace_id = :w AND hook = 'h2_item'"
            ),
            {"w": workspace_id},
        )
    assert pos_count >= 1


def test_pd3a_mapping_pass_makes_zero_ai_requests(
    engine: Engine, keys: tuple[Keyring, SealingPublicKey], monkeypatch: pytest.MonkeyPatch
) -> None:
    """PD-3a: Mapping pass queues 0 H3 re-rank requests under default HOOKS_BY_MODE.

    Positive control: Enabling legacy hooks on the identical setup queues >= 1 H3 request.
    """
    keyring, public_key = keys
    with engine.begin() as connection:
        workspace_id, user_id = _setup_workspace(connection, public_key)
        batch_id, source_file_id, vendor_id = new_id(), new_id(), new_id()
        connection.execute(
            sa.insert(metadata.tables["batch"]).values(
                id=batch_id, workspace_id=workspace_id, label="PD3A H3", created_by=user_id
            )
        )
        connection.execute(
            sa.insert(metadata.tables["source_file"]).values(
                id=source_file_id,
                workspace_id=workspace_id,
                batch_id=batch_id,
                sha256=hashlib.sha256(f"pd3a-h3:{workspace_id}".encode()).hexdigest(),
                display_name="pd3a-h3.csv",
                zone="invoice",
                zone_uploaded="invoice",
                format="csv",
                status="parsed",
                size_bytes=128,
                object_key=f"{workspace_id}/pd3a-h3",
                docs_found=3,
                docs_new=3,
                uploaded_by=user_id,
            )
        )
        connection.execute(
            sa.insert(metadata.tables["vendor"]).values(
                id=vendor_id,
                workspace_id=workspace_id,
                canonical_name="Synthetic H3 Vendor",
                normalized_name="synthetic h3 vendor",
                anchor="name",
            )
        )
        # Invoice unlinked with multiple same-vendor POs with tied content
        _add_document(
            connection,
            workspace_id,
            batch_id,
            source_file_id,
            doc_type="invoice",
            doc_no="INV-H3-1",
            doc_date=INVOICE_DATE,
            descriptions=[f"{ITEM} (ref {GSTIN_A})"],
            vendor_id=vendor_id,
        )
        _add_document(
            connection,
            workspace_id,
            batch_id,
            source_file_id,
            doc_type="po",
            doc_no="PO-H3-A",
            doc_date=PO_DATE,
            descriptions=[ITEM],
            vendor_id=vendor_id,
        )
        _add_document(
            connection,
            workspace_id,
            batch_id,
            source_file_id,
            doc_type="po",
            doc_no="PO-H3-B",
            doc_date=PO_DATE,
            descriptions=[ITEM],
            vendor_id=vendor_id,
        )

    # 1. Run mapping pass under shipped PD-3a hooks (H3 off)
    queue = RecordingQueue()
    mapping_service = MappingService(engine, None, _enqueuer(queue))
    mapping_service.resolve_workspace(workspace_id)

    assert len(queue.request_ids) == 0
    with engine.connect() as conn:
        count = conn.scalar(
            sa.text("SELECT count(*) FROM ai_request WHERE workspace_id = :w"), {"w": workspace_id}
        )
    assert count == 0

    # 2. Positive Control: under LEGACY_HOOKS_BY_MODE with matching consent,
    # identical mapping queues H3
    monkeypatch.setattr("reconbench_app.ai.consent.HOOKS_BY_MODE", LEGACY_HOOKS_BY_MODE)
    with engine.begin() as conn:
        legacy_fp = consent_fingerprint(provider="gemini", model=MODEL, mode="ai_assisted")
        conn.execute(
            sa.update(metadata.tables["ai_setting"])
            .where(metadata.tables["ai_setting"].c.workspace_id == workspace_id)
            .values(consent_fingerprint=legacy_fp)
        )
    pos_queue = RecordingQueue()
    pos_mapping = MappingService(engine, None, _enqueuer(pos_queue))
    pos_mapping.resolve_workspace(workspace_id)

    assert len(pos_queue.request_ids) >= 1
    with engine.connect() as conn:
        pos_count = conn.scalar(
            sa.text(
                "SELECT count(*) FROM ai_request WHERE workspace_id = :w AND hook = 'h3_rerank'"
            ),
            {"w": workspace_id},
        )
    assert pos_count >= 1


def test_pd3a_vendor_resolution_makes_zero_ai_requests(
    engine: Engine, keys: tuple[Keyring, SealingPublicKey], monkeypatch: pytest.MonkeyPatch
) -> None:
    """PD-3a: Vendor resolution queues 0 H4 vendor requests under default HOOKS_BY_MODE.

    Positive control: Enabling legacy hooks on identical setup queues >= 1 H4 request.
    """
    keyring, public_key = keys
    with engine.begin() as connection:
        workspace_id, user_id = _setup_workspace(connection, public_key)
        vendor_a = new_id()
        vendor_b = new_id()
        connection.execute(
            sa.insert(metadata.tables["vendor"]).values(
                id=vendor_a,
                workspace_id=workspace_id,
                canonical_name="Benchmark Computer Solutions",
                normalized_name="benchmark computer solutions",
                pan=PAN_A,
                anchor="name",
            )
        )
        connection.execute(
            sa.insert(metadata.tables["vendor"]).values(
                id=vendor_b,
                workspace_id=workspace_id,
                canonical_name="Benchmark Computer Soln Pvt Ltd",
                normalized_name="benchmark computer soln pvt ltd",
                pan=None,
                anchor="name",
            )
        )
        connection.execute(
            sa.insert(metadata.tables["vendor_gstin"]).values(
                id=new_id(),
                workspace_id=workspace_id,
                vendor_id=vendor_a,
                gstin=GSTIN_A,
                state_code="27",
                pan=PAN_A,
                checksum_ok=True,
            )
        )
        connection.execute(
            sa.insert(metadata.tables["vendor_alias"]).values(
                id=new_id(),
                workspace_id=workspace_id,
                vendor_id=vendor_b,
                alias_raw=f"Benchmark Computer Soln {GSTIN_A} accounts@benchmark.example",
                alias_norm="benchmark computer soln",
                source="learned",
            )
        )
        first, second = sorted((vendor_a, vendor_b), key=str)
        suggestion_id = new_id()
        connection.execute(
            sa.insert(metadata.tables["vendor_merge_suggestion"]).values(
                id=suggestion_id,
                workspace_id=workspace_id,
                vendor_a_id=first,
                vendor_b_id=second,
                score="0.85",
            )
        )

    # 1. Run vendor resolution opinions under shipped PD-3a hooks (H4 off)
    queue = RecordingQueue()
    with engine.begin() as connection:
        queued = _enqueuer(queue).vendor_opinions(connection, workspace_id)
    assert queued == 0
    assert len(queue.request_ids) == 0
    with engine.connect() as conn:
        count = conn.scalar(
            sa.text("SELECT count(*) FROM ai_request WHERE workspace_id = :w"), {"w": workspace_id}
        )
    assert count == 0

    # 2. Positive Control: under LEGACY_HOOKS_BY_MODE with matching consent,
    # identical setup queues H4
    monkeypatch.setattr("reconbench_app.ai.consent.HOOKS_BY_MODE", LEGACY_HOOKS_BY_MODE)
    with engine.begin() as conn:
        legacy_fp = consent_fingerprint(provider="gemini", model=MODEL, mode="ai_assisted")
        conn.execute(
            sa.update(metadata.tables["ai_setting"])
            .where(metadata.tables["ai_setting"].c.workspace_id == workspace_id)
            .values(consent_fingerprint=legacy_fp)
        )
    pos_queue = RecordingQueue()
    with engine.begin() as connection:
        pos_queued = _enqueuer(pos_queue).vendor_opinions(connection, workspace_id)
    assert pos_queued >= 1
    assert len(pos_queue.request_ids) >= 1
    with engine.connect() as conn:
        pos_count = conn.scalar(
            sa.text(
                "SELECT count(*) FROM ai_request WHERE workspace_id = :w AND hook = 'h4_vendor'"
            ),
            {"w": workspace_id},
        )
    assert pos_count >= 1
