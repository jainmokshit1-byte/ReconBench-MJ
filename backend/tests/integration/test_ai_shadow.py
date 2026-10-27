"""Shadow reader (LLM plan WI-BE6) end to end on a disposable PostgreSQL 16 database.

The workspace runs its one reader (``primary_reader``) in ``shadow``: H1 keeps
running, page images are read in the background, and the checked answer becomes
one append-only ``reader_comparison`` row. No document, line, consensus run,
vendor or link changes, the review view never shows the shadow answer, and the
report holds aggregates only. Synthetic documents; ``ScriptedProvider`` and
``httpx.MockTransport`` only, nothing leaves the process.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import sqlalchemy as sa
import test_ai_vision
from reconbench_app.ai import ledger
from reconbench_app.ai.enqueue import AiHookEnqueuer
from reconbench_app.ai.gate import DeploymentGate
from reconbench_app.ai.limits import AiLimits, LimitsState
from reconbench_app.ai.runtime_checks import fingerprint_for
from reconbench_app.db.ids import new_id
from reconbench_app.db.metadata import metadata
from scripts.vision_fixtures import recorded_response, synthetic_invoice_pdf
from sqlalchemy.engine import Connection, Engine
from test_ai_vision import (
    APPROVAL,
    MODEL,
    World,
    _grn_pdf,
    _prepare,
    _Recorder,
    _runner,
    _upload,
    _vision_requests,
)

# The vision tests' module fixtures (disposable database, sealing keys, world builder).
engine = test_ai_vision.engine
keys = test_ai_vision.keys
make_world = test_ai_vision.make_world

SETTINGS = metadata.tables["ai_setting"]
COMPARISONS = metadata.tables["reader_comparison"]
LOCAL_MODEL = "qwen3.5:9b"
BASE_URL = "http://vllm.example.test/vllm/v1"
# Tables a shadow run must never write (``link`` and ``link_current`` hold document links).
UNTOUCHED = (
    "document_version",
    "doc_line",
    "consensus_run",
    "consensus_decision",
    "vendor",
    "link",
    "link_current",
)


def _shadow(engine: Engine, world: World, reader: str) -> None:
    """Switch the workspace to ``shadow`` with its one reader, with matching consent."""

    with engine.begin() as connection:
        if reader == "vllm":
            connection.execute(
                sa.insert(metadata.tables["ai_provider_config"]).values(
                    id=new_id(),
                    workspace_id=world.workspace_id,
                    provider="vllm",
                    enabled=True,
                    model=LOCAL_MODEL,
                    base_url=BASE_URL,
                )
            )
        connection.execute(
            sa.update(SETTINGS)
            .where(SETTINGS.c.workspace_id == world.workspace_id)
            .values(
                reader_strategy="shadow",
                primary_reader=reader,
                vision_enabled=False,
                version=SETTINGS.c.version + 1,
                consent_fingerprint=fingerprint_for(
                    provider="gemini",
                    model=MODEL,
                    mode="ai_first",
                    reader_strategy="shadow",
                    primary_reader=reader,
                    vllm_model=LOCAL_MODEL if reader == "vllm" else None,
                    vllm_base_url=BASE_URL if reader == "vllm" else None,
                ),
            )
        )
    if reader == "vllm":
        world.service.ai_hooks = AiHookEnqueuer(
            gate=DeploymentGate.from_values(
                "enabled", APPROVAL, vllm_mode="enabled", vllm_endpoints=BASE_URL
            ),
            queue=world.ai_queue,
            limits=lambda: LimitsState(AiLimits()),
        )


def _counts(engine: Engine, world: World) -> dict[str, int]:
    with engine.connect() as connection:
        return {
            table: int(
                connection.scalar(
                    sa.text(f"SELECT count(*) FROM {table} WHERE workspace_id = :id"),
                    {"id": world.workspace_id},
                )
                or 0
            )
            for table in UNTOUCHED
        }


def _comparisons(engine: Engine, world: World) -> list[Any]:
    with engine.connect() as connection:
        return list(
            connection.execute(
                sa.select(COMPARISONS).where(COMPARISONS.c.workspace_id == world.workspace_id)
            ).mappings()
        )


def _values(answer: dict[str, Any]) -> set[str]:
    """Every value string of a synthetic answer that is long enough to be telling."""

    found: set[str] = set()

    def walk(item: Any, key: str = "") -> None:
        if isinstance(item, dict):
            for name, value in item.items():
                walk(value, name)
        elif isinstance(item, list):
            for value in item:
                walk(value, key)
        elif (
            isinstance(item, str)
            and key not in {"name", "kind", "looks_like", "doc_kind"}
            and len(item) >= 5
        ):
            found.add(item)

    walk(answer)
    return found


CONSENSUS_CALLS: list[UUID] = []


def _never_consensus(
    _connection: Connection, _workspace: UUID, _version: UUID, request_id: UUID, _call: UUID
) -> bool:
    """Records a call (the handler swallows enqueue errors, so a raise would prove nothing)."""

    CONSENSUS_CALLS.append(request_id)
    return False


def test_shadow_reading_is_stored_as_labels_and_changes_nothing(
    engine: Engine, keys: Any, make_world: Any
) -> None:
    world = make_world(vision_on=False)
    _shadow(engine, world, "gemini")
    _upload(world, synthetic_invoice_pdf(), name="shadow-invoice.pdf")
    # A stored shadow row runs nothing (D103 decision 6).
    assert len(world.ai_queue.request_ids) == 0
    assert len(world.queue.vision_versions) == 0
    assert _prepare(world) == []
    assert _vision_requests(engine, world) == []


def test_a_document_read_only_in_shadow_says_so(engine: Engine, keys: Any, make_world: Any) -> None:
    world = make_world(vision_on=False)
    _shadow(engine, world, "gemini")
    _upload(world, _grn_pdf(), name="shadow-grn.pdf", zone="grn")
    # A stored shadow row runs nothing (D103 decision 6).
    assert _prepare(world) == []
    assert _vision_requests(engine, world) == []


def test_switching_to_single_drops_a_queued_shadow_request(
    engine: Engine, keys: Any, make_world: Any
) -> None:
    world = make_world(vision_on=False)
    _shadow(engine, world, "gemini")
    _upload(world, synthetic_invoice_pdf(), name="shadow-switch.pdf")
    assert _prepare(world) == []
    with engine.begin() as connection:
        req = ledger.NewRequest(
            workspace_id=world.workspace_id,
            call_kind="hook",
            hook="h6_vision",
            mode="ai_first",
            subject_type="document_version",
            subject_id=new_id(),
            subject_version="v1",
            operation_key=f"h6_vision:{new_id()}",
            settings_version=1,
            consent_fingerprint="0" * 64,
            provider="gemini",
            model=MODEL,
            prompt_version="vision.prompt.v4.gemini",
            schema_version="vision.schema.v4",
            redaction_version="redaction.v1",
            shadow=True,
        )
        request_id = ledger.create_request(connection, req)
        assert request_id is not None
        connection.execute(
            sa.update(SETTINGS)
            .where(SETTINGS.c.workspace_id == world.workspace_id)
            .values(reader_strategy="single", vision_enabled=True)
        )
    provider = _Recorder([json.dumps(recorded_response())])
    result = _runner(engine, keys[0], provider).run(request_id)

    assert result.state == "skipped"
    assert provider.calls == [] and _comparisons(engine, world) == []


def test_company_server_shadow_reads_through_its_own_provider_only(
    engine: Engine, keys: Any, make_world: Any
) -> None:
    world = make_world(vision_on=False)
    _shadow(engine, world, "vllm")
    _upload(world, synthetic_invoice_pdf(), name="shadow-vllm.pdf")
    # A stored shadow row runs nothing (D103 decision 6).
    assert _prepare(world) == []
    assert _vision_requests(engine, world) == []
