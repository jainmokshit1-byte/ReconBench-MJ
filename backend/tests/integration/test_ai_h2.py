"""H2 uncertain-item opinion end to end on a disposable PostgreSQL 16 database.

A real reconciliation run (producer) → AI worker runner → handler apply, with the
offline ``ScriptedProvider`` only. Each test creates its own workspace with one
invoice linked to one PO whose lines align exactly, uncertainly, and uncertainly
with a spec-token conflict (16GB vs 8GB).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import boto3
import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from reconbench_app.ai import h2
from reconbench_app.ai.consent import CONSENT_TEXT_VERSION, consent_fingerprint
from reconbench_app.ai.enqueue import AiHookEnqueuer
from reconbench_app.ai.gate import DeploymentGate
from reconbench_app.ai.key_seal import SealingPublicKey, key_hint, seal
from reconbench_app.ai.limits import AiLimits, LimitsState
from reconbench_app.ai.provider import ProviderFailure, ProviderRequest
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
from reconbench_app.mapping_service import MappingService
from reconbench_app.reconciliation_service import ReconciliationService
from reconbench_app.settings import Settings
from reconbench_core.version import ENGINE_VERSION
from sqlalchemy.engine import Connection, Engine, make_url

SENTINEL_KEY = "AIzaSyD-synthetic-h2-key-000000000000000"
MODEL = "gemini-3.8-flash"
APPROVAL = "SYNTHETIC-TEST-APPROVAL-1"
GSTIN = "27AAPFU0939F1ZV"
INVOICE_DATE = date(2026, 4, 10)
PO_DATE = date(2026, 4, 1)
PO_NO = "H2-PO-001"
# (invoice description, PO description): aligned, unsure in band, unsure with a spec conflict.
LINES = (
    ("HP LaserJet Pro M404dn Printer", "HP LaserJet Pro M404dn Printer"),
    (f"Logitech wireless desktop set ref {GSTIN}", "Logitech wireless keyboard and mouse combo"),
    ("Kingston DDR4 RAM 16GB", "Kingston DDR4 RAM 8GB"),
)
LINE_RESULTS = metadata.tables["recon_line_result"]
OPINIONS = metadata.tables["ai_line_opinion"]
REQUESTS = metadata.tables["ai_request"]
_CREATED: list[UUID] = []


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    dsn = os.environ.get("RB_PHASE2_TEST_DATABASE_URL", "").strip()
    if not dsn:
        pytest.skip("RB_PHASE2_TEST_DATABASE_URL is required for disposable PostgreSQL tests")
    if not (make_url(dsn).database or "").startswith("reconbench_phase2_"):
        raise RuntimeError("H2 checks require a disposable reconbench_phase2_* database")
    created = sa.create_engine(dsn)
    try:
        yield created
    finally:
        # Audit rows are append-only, so workspaces stay; stored keys are removed because
        # the 0017 migration checks require an empty secret table.
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
    directory: Path = tmp_path_factory.mktemp("ai-h2-keys")
    generate(directory, "k1")
    return Keyring.load(directory / KEYRING_NAME), SealingPublicKey.from_file(
        directory / PUBLIC_KEY_NAME
    )


@dataclass
class World:
    workspace_id: UUID
    user_id: UUID
    vendor_id: UUID
    batch_id: UUID
    source_file_id: UUID
    invoice_id: UUID = field(default_factory=new_id)
    po_id: UUID = field(default_factory=new_id)


class RecordingQueue:
    def __init__(self) -> None:
        self.request_ids: list[UUID] = []

    def enqueue_ai_request(self, *, ai_request_id: UUID, connection: Connection) -> int:
        del connection
        self.request_ids.append(ai_request_id)
        return len(self.request_ids)


def _document(
    connection: Connection,
    world: World,
    *,
    doc_type: str,
    doc_no: str,
    doc_date: date,
    descriptions: list[str],
    document_id: UUID,
    po_refs: list[str] | None = None,
) -> None:
    version_id = new_id()
    quantity, rate = Decimal("1"), Decimal("1000.00")
    total = rate * len(descriptions)
    key = hashlib.sha256(f"{world.workspace_id}:{doc_type}:{doc_no}".encode()).hexdigest()
    connection.execute(
        sa.insert(metadata.tables["document"]).values(
            id=document_id,
            workspace_id=world.workspace_id,
            doc_type=doc_type,
            natural_key=key,
            batch_id=world.batch_id,
            doc_no=doc_no,
            canonical_doc_no=doc_no,
            doc_date=doc_date,
            grand_total=total,
            vendor_id=world.vendor_id,
        )
    )
    refs = po_refs or []
    connection.execute(
        sa.insert(metadata.tables["document_version"]).values(
            id=version_id,
            workspace_id=world.workspace_id,
            document_id=document_id,
            seq=1,
            content_hash=key,
            doc_no=doc_no,
            canonical_doc_no=doc_no,
            doc_date=doc_date,
            vendor_name="Synthetic H2 Supplies",
            vendor_name_norm="synthetic h2 supplies",
            currency="INR",
            taxable_total=total,
            grand_total=total,
            po_refs=refs,
            canonical_po_refs=refs,
            extraction_method="csv_mapping",
            extraction_conf=Decimal("0.90"),
            source_file_id=world.source_file_id,
            parser_version="h2-test",
            needs_verification=False,
        )
    )
    if refs:
        connection.execute(
            sa.insert(metadata.tables["field_evidence"]).values(
                id=new_id(),
                workspace_id=world.workspace_id,
                document_version_id=version_id,
                field_name="po_refs",
                value_text=json.dumps(refs),
                confidence=None,
                source="manual",
                origin="manual",
                verification="user",
                verified_by=str(world.user_id),
                verified_at=datetime.now(UTC),
            )
        )
    for line_no, description in enumerate(descriptions, start=1):
        connection.execute(
            sa.insert(metadata.tables["doc_line"]).values(
                id=new_id(),
                workspace_id=world.workspace_id,
                document_version_id=version_id,
                line_no=line_no,
                line_type="goods",
                description=description,
                description_norm=description.casefold(),
                tokens=description.casefold().split(),
                hsn_sac="84716060",
                uom="NOS",
                uom_as_read="NOS",
                quantity=quantity,
                rate=rate,
                amount=rate,
                extras={},
            )
        )
    connection.execute(
        sa.update(metadata.tables["document"])
        .where(metadata.tables["document"].c.id == document_id)
        .values(current_version_id=version_id)
    )


def _world(
    engine: Engine,
    public_key: SealingPublicKey,
    *,
    mode: str = "ai_assisted",
    lines: tuple[tuple[str, str], ...] = LINES,
) -> World:
    workspace_id, user_id, secret_id, config_id = new_id(), new_id(), new_id(), new_id()
    batch_id, source_file_id, vendor_id = new_id(), new_id(), new_id()
    _CREATED.append(workspace_id)
    sealed = seal(
        SENTINEL_KEY.encode(), public_key=public_key, workspace_id=workspace_id, secret_id=secret_id
    )
    with engine.begin() as connection:
        connection.execute(
            sa.insert(metadata.tables["workspace"]).values(id=workspace_id, name="H2 test")
        )
        connection.execute(
            sa.insert(metadata.tables["app_user"]).values(
                id=user_id,
                workspace_id=workspace_id,
                email=f"h2-{user_id}@example.invalid",
                display_name="Synthetic H2 tester",
                role="manager",
                password_hash="not-a-real-hash",
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
                change_note="Synthetic H2 baseline",
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
                consent_given=True,
                consent_fingerprint=consent_fingerprint(provider="gemini", model=MODEL, mode=mode),
                consent_by=user_id,
                consent_at=datetime.now(UTC),
                consent_text_version=CONSENT_TEXT_VERSION,
            )
        )
        connection.execute(
            sa.insert(metadata.tables["batch"]).values(
                id=batch_id, workspace_id=workspace_id, label="H2 synthetic", created_by=user_id
            )
        )
        digest = hashlib.sha256(f"h2-source:{workspace_id}".encode()).hexdigest()
        connection.execute(
            sa.insert(metadata.tables["source_file"]).values(
                id=source_file_id,
                workspace_id=workspace_id,
                batch_id=batch_id,
                sha256=digest,
                display_name="h2-synthetic.csv",
                zone="invoice",
                zone_uploaded="invoice",
                format="csv",
                status="parsed",
                size_bytes=128,
                object_key=f"{workspace_id}/sha256/{digest}",
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
        world = World(workspace_id, user_id, vendor_id, batch_id, source_file_id)
        _document(
            connection,
            world,
            doc_type="po",
            doc_no=PO_NO,
            doc_date=PO_DATE,
            descriptions=[po for _invoice, po in lines],
            document_id=world.po_id,
        )
        _document(
            connection,
            world,
            doc_type="invoice",
            doc_no="H2-INV-001",
            doc_date=INVOICE_DATE,
            descriptions=[invoice for invoice, _po in lines],
            document_id=world.invoice_id,
            po_refs=[PO_NO],
        )
    MappingService(engine).resolve_workspace(workspace_id)
    return world


def _enqueuer(queue: RecordingQueue, *, gate: str = "enabled") -> AiHookEnqueuer:
    return AiHookEnqueuer(
        gate=DeploymentGate.from_values(gate, APPROVAL if gate == "enabled" else None),
        queue=queue,
        limits=lambda: LimitsState(AiLimits()),
    )


def _queued_run(engine: Engine, world: World) -> UUID:
    active = metadata.tables["config_active"]
    config_version = metadata.tables["config_version"]
    run_id = new_id()
    with engine.begin() as connection:
        config_id, engine_hash = connection.execute(
            sa.select(config_version.c.id, config_version.c.engine_hash)
            .select_from(
                active.join(config_version, active.c.config_version_id == config_version.c.id)
            )
            .where(active.c.workspace_id == world.workspace_id)
        ).one()
        connection.execute(
            sa.insert(metadata.tables["recon_run"]).values(
                id=run_id,
                workspace_id=world.workspace_id,
                status="queued",
                trigger_kind="user",
                scope={"kind": "all"},
                scope_hash=hashlib.sha256(b'{"kind":"all"}').hexdigest(),
                run_date=date(2026, 4, 15),
                engine_version=ENGINE_VERSION,
                rules_hash=engine_hash,
                config_version_id=config_id,
                ai_mode="rules_only",
                ai_consent=False,
                total_cases=0,
                done_cases=0,
                status_counts={},
                metrics={},
                requested_by=world.user_id,
            )
        )
    return run_id


def _run(engine: Engine, world: World, **kwargs: Any) -> tuple[UUID, list[UUID]]:
    queue = RecordingQueue()
    run_id = _queued_run(engine, world)
    service = ReconciliationService(engine, ai_hooks=_enqueuer(queue, **kwargs))
    assert len(service.evaluate_run(run_id)) == 1
    return run_id, queue.request_ids


def _runner(engine: Engine, keyring: Keyring, provider: ScriptedProvider) -> AiRequestRunner:
    provider.name = "gemini"
    return AiRequestRunner(
        engine=engine,
        keyring=keyring,
        gate=DeploymentGate.from_values("enabled", APPROVAL),
        limits=lambda: LimitsState(AiLimits()),
        prices=lambda: None,
        providers={provider.name: provider},
        handlers=default_handlers(),
    )


def _line_rows(engine: Engine, run_id: UUID) -> dict[int, Any]:
    with engine.connect() as connection:
        return {
            row.line_no: row
            for row in connection.execute(
                sa.select(LINE_RESULTS).where(LINE_RESULTS.c.run_id == run_id)
            )
        }


def _states(engine: Engine, world: World, run_id: UUID) -> dict[int, dict[str, Any]]:
    rows = _line_rows(engine, run_id)
    with engine.connect() as connection:
        states = h2.line_states(
            connection, world.workspace_id, run_id, [row.id for row in rows.values()]
        )
    return {line_no: states[row.id] for line_no, row in rows.items()}


def _answer(pairs: dict[str, str], opinion: str = "same", **extra: Any) -> str:
    """One opinion per sent pair, quoting the first word of each side."""

    body = {
        "opinions": [
            {
                "pair_id": pair_id,
                "opinion": opinion,
                "reason": "Both lines describe the same Logitech keyboard and mouse set.",
                "invoice_quote": invoice.split()[0],
                "po_quote": po.split()[0],
                **extra,
            }
            for pair_id, (invoice, po) in pairs.items()
        ]
    }
    return json.dumps(body)


def _sent_pairs(sent: ProviderRequest) -> dict[str, tuple[str, str]]:
    blocks = dict(
        re.findall(
            r"BEGIN-DATA PAIR (p\d+ (?:INVOICE|PO)) \w+\nDescription: ([^\n]*)", sent.input_text
        )
    )
    ids = sorted({key.split()[0] for key in blocks})
    return {pair_id: (blocks[f"{pair_id} INVOICE"], blocks[f"{pair_id} PO"]) for pair_id in ids}


def _snapshot(engine: Engine, run_id: UUID) -> list[Any]:
    with engine.connect() as connection:
        return [
            list(connection.execute(sa.select(table).where(table.c.run_id == run_id)).all())
            if table.name != "recon_run"
            else list(connection.execute(sa.select(table).where(table.c.id == run_id)).all())
            for table in (
                metadata.tables["recon_run"],
                metadata.tables["recon_case"],
                LINE_RESULTS,
            )
        ]


def test_run_fixture_has_aligned_in_band_and_spec_conflict_lines(engine: Engine, keys: Any) -> None:
    _, public_key = keys
    world = _world(engine, public_key)
    run_id, _requests = _run(engine, world, gate="disabled")
    rows = _line_rows(engine, run_id)
    assert rows[1].alignment_state == "aligned"
    assert rows[2].alignment_state == "unsure" and h2.in_band(rows[2].alignment_score)
    assert rows[3].alignment_state == "unsure" and h2.in_band(rows[3].alignment_score)
    assert h2.spec_conflict(*LINES[2])
    assert _states(engine, world, run_id) == {
        1: {"state": "not_applicable", "code": h2.NOT_ELIGIBLE},
        2: {"state": "not_applicable", "code": h2.NOT_REQUESTED},
        3: {"state": "not_applicable", "code": h2.SPEC_CONFLICT},
    }


def test_opinion_is_appended_beside_the_line_and_run_rows_are_unchanged(
    engine: Engine, keys: Any
) -> None:
    keyring, public_key = keys
    world = _world(engine, public_key)
    run_id, [request_id] = _run(engine, world)
    # The run has already succeeded; the AI worker has not run at all yet.
    with engine.connect() as connection:
        status = connection.scalar(
            sa.select(metadata.tables["recon_run"].c.status).where(
                metadata.tables["recon_run"].c.id == run_id
            )
        )
        request = connection.execute(sa.select(REQUESTS).where(REQUESTS.c.id == request_id)).one()
    assert status == "succeeded"
    assert (request.hook, request.subject_type, request.subject_id, request.run_id) == (
        "h2_item",
        "recon_line_batch",
        run_id,
        run_id,
    )
    assert _states(engine, world, run_id)[2] == {"state": "pending", "code": None}
    before = _snapshot(engine, run_id)
    provider = _Responder(lambda sent: _answer(_sent_pairs(sent)))
    result = _runner(engine, keyring, provider).run(request_id)

    assert (result.state, result.apply_outcome) == ("succeeded", "applied")
    [sent] = provider.calls
    # Only the in-band, non-conflicting pair is sent, with identifiers redacted and no amounts.
    assert list(_sent_pairs(sent)) == ["p1"]
    assert "Kingston" not in sent.input_text and "M404dn" not in sent.input_text
    assert GSTIN not in sent.input_text and "[GSTIN_1]" in sent.input_text
    assert "1000" not in sent.input_text and SENTINEL_KEY not in sent.input_text
    assert _snapshot(engine, run_id) == before
    rows = _line_rows(engine, run_id)
    assert rows[2].ai_item_opinion is None and rows[2].ai_item_reason is None
    with engine.connect() as connection:
        [opinion] = connection.execute(sa.select(OPINIONS).where(OPINIONS.c.run_id == run_id)).all()
    assert opinion.recon_line_result_id == rows[2].id
    assert (opinion.invoice_line_id, opinion.po_doc_line_id) == (
        rows[2].invoice_line_id,
        rows[2].po_doc_line_id,
    )
    assert (opinion.opinion, opinion.ai_request_id, opinion.model) == ("same", request_id, MODEL)
    states = _states(engine, world, run_id)
    assert states[2]["state"] == "available" and states[2]["opinion"] == "same"
    assert states[3] == {"state": "not_applicable", "code": h2.SPEC_CONFLICT}
    # A duplicate job is a no-op.
    assert _runner(engine, keyring, provider).run(request_id).state == "not_queued"


class _Responder(ScriptedProvider):
    def __init__(self, respond: Any) -> None:
        super().__init__([])
        self.respond = respond

    def complete(self, request: ProviderRequest, *, api_key: str) -> Any:
        self.outputs = [self.respond(request)]
        return super().complete(request, api_key=api_key)


@pytest.mark.parametrize(
    ("output", "code"),
    [
        ("not json", "invalid_output"),
        (
            json.dumps(
                {
                    "opinions": [
                        {
                            "pair_id": "p1",
                            "opinion": "same",
                            "reason": "x",
                            "invoice_quote": "Dell",
                            "po_quote": "Dell",
                        }
                    ]
                }
            ),
            "ungrounded",
        ),
        (json.dumps({"opinions": [], "verdict": "matched"}), "invalid_output"),
    ],
)
def test_invalid_output_leaves_the_line_unavailable(
    engine: Engine, keys: Any, output: str, code: str
) -> None:
    keyring, public_key = keys
    world = _world(engine, public_key)
    run_id, [request_id] = _run(engine, world)

    result = _runner(engine, keyring, ScriptedProvider([output])).run(request_id)

    assert (result.state, result.apply_outcome) == ("succeeded", "discarded_invalid")
    assert _states(engine, world, run_id)[2] == {"state": "unavailable", "code": code}
    with engine.connect() as connection:
        assert (
            connection.execute(sa.select(OPINIONS).where(OPINIONS.c.run_id == run_id)).all() == []
        )


@pytest.mark.parametrize(
    ("failure", "request_state"),
    [
        (ProviderFailure("http_400", delivery="responded"), "failed"),
        (ProviderFailure("PROVIDER_UNAVAILABLE", delivery="not_sent"), "released"),
        (ProviderFailure("timeout", delivery="may_have_been_sent"), "uncertain"),
    ],
)
def test_failed_or_uncertain_call_leaves_the_line_unavailable(
    engine: Engine, keys: Any, failure: ProviderFailure, request_state: str
) -> None:
    keyring, public_key = keys
    world = _world(engine, public_key)
    run_id, [request_id] = _run(engine, world)

    _runner(engine, keyring, ScriptedProvider([failure])).run(request_id)

    with engine.connect() as connection:
        state = connection.scalar(
            sa.select(REQUESTS.c.request_state).where(REQUESTS.c.id == request_id)
        )
    assert state == request_state
    assert _states(engine, world, run_id)[2] == {"state": "unavailable", "code": failure.code}
    assert _snapshot(engine, run_id)[2][1].ai_item_opinion is None


@pytest.mark.parametrize("setup", ["gate_disabled", "rules_only"])
def test_nothing_is_queued_unless_every_precondition_holds(
    engine: Engine, keys: Any, setup: str
) -> None:
    _, public_key = keys
    world = _world(
        engine, public_key, mode="rules_only" if setup == "rules_only" else "ai_assisted"
    )
    kwargs = {"gate": "disabled"} if setup == "gate_disabled" else {}
    _run_id, requests = _run(engine, world, **kwargs)
    assert requests == []


class _FailingEnqueuer:
    def item_opinions(self, connection: Connection, workspace_id: UUID, run_id: UUID) -> int:
        connection.execute(sa.text("SELECT 1 / 0"))
        return 0


def test_an_enqueue_failure_never_fails_the_run(engine: Engine, keys: Any) -> None:
    _, public_key = keys
    world = _world(engine, public_key)
    run_id = _queued_run(engine, world)
    service = ReconciliationService(engine, ai_hooks=_FailingEnqueuer())  # type: ignore[arg-type]
    assert len(service.evaluate_run(run_id)) == 1
    with engine.connect() as connection:
        status = connection.scalar(
            sa.select(metadata.tables["recon_run"].c.status).where(
                metadata.tables["recon_run"].c.id == run_id
            )
        )
    assert status == "succeeded"


class _FakeObjectStoreClient:
    def list_buckets(self) -> dict[str, list[object]]:
        return {"Buckets": []}


def _case_client(engine: Engine, world: World) -> TestClient:
    settings = Settings(
        _env_file=None,
        environment="ci",
        database_url=engine.url.render_as_string(hide_password=False),
        s3_endpoint_url="http://127.0.0.1:1",
        s3_region="us-east-1",
        s3_bucket="synthetic-h2",
        s3_api_access_key="synthetic-access",
        s3_api_secret_key="synthetic-secret",
        public_base_url="https://example.invalid",
        argon2_time_cost=1,
        argon2_memory_cost_kib=8192,
        argon2_parallelism=1,
    )
    app = create_app(settings)
    context = AuthContext(
        session_id=new_id(),
        workspace_id=world.workspace_id,
        user_id=world.user_id,
        display_name="Synthetic H2 tester",
        role="manager",
        csrf_token="synthetic-csrf",
    )
    app.state.auth_service.authenticate = lambda *args, **kwargs: AuthenticationCheck(context, True)
    client = TestClient(app, base_url="https://testserver")
    client.cookies.set(COOKIE_NAME, "synthetic-session", path="/api/v1")
    return client


def test_case_payload_shows_late_opinion_and_never_answers_304_while_it_can_change(
    engine: Engine, keys: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    keyring, public_key = keys
    monkeypatch.setattr(boto3, "client", lambda *args, **kwargs: _FakeObjectStoreClient())
    world = _world(engine, public_key)
    run_id, [request_id] = _run(engine, world)
    case_id = _line_rows(engine, run_id)[2].case_id
    with _case_client(engine, world) as client:
        first = client.get(f"/api/v1/cases/{case_id}")
        assert first.status_code == 200, first.text
        etag = first.headers["ETag"]
        lines = {line["line_no"]: line["ai_opinion"] for line in first.json()["lines"]}
        assert lines == {
            1: {"state": "not_applicable", "code": h2.NOT_ELIGIBLE},
            2: {"state": "pending", "code": None},
            3: {"state": "not_applicable", "code": h2.SPEC_CONFLICT},
        }

        provider = _Responder(lambda sent: _answer(_sent_pairs(sent), opinion="different"))
        assert _runner(engine, keyring, provider).run(request_id).apply_outcome == "applied"

        # The case version (and so the If-Match ETag) is unchanged, but the body is not stale.
        later = client.get(f"/api/v1/cases/{case_id}", headers={"If-None-Match": etag})
        assert later.status_code == 200 and later.headers["ETag"] == etag
        opinion = {line["line_no"]: line["ai_opinion"] for line in later.json()["lines"]}[2]
        assert (opinion["state"], opinion["opinion"], opinion["model"]) == (
            "available",
            "different",
            MODEL,
        )
        assert opinion["reason"]


def test_case_without_eligible_lines_keeps_conditional_get(
    engine: Engine, keys: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, public_key = keys
    monkeypatch.setattr(boto3, "client", lambda *args, **kwargs: _FakeObjectStoreClient())
    # Aligned and spec-conflict lines only: nothing in this case can change later.
    world = _world(engine, public_key, lines=(LINES[0], LINES[2]))
    run_id, requests = _run(engine, world)
    assert requests == []
    case_id = _line_rows(engine, run_id)[1].case_id
    with _case_client(engine, world) as client:
        first = client.get(f"/api/v1/cases/{case_id}")
        assert first.status_code == 200, first.text
        again = client.get(
            f"/api/v1/cases/{case_id}", headers={"If-None-Match": first.headers["ETag"]}
        )
        assert again.status_code == 304
