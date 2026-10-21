"""Shadow reader (LLM plan WI-BE6) end to end on a disposable PostgreSQL 16 database.

The workspace runs its one reader (``primary_reader``) in ``shadow``: H1 keeps
running, page images are read in the background, and the checked answer becomes
one append-only ``reader_comparison`` row. No document, line, consensus run,
vendor or link changes, the review view never shows the shadow answer, and the
report holds aggregates only. Synthetic documents; ``ScriptedProvider`` and
``httpx.MockTransport`` only, nothing leaves the process.
"""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from typing import Any
from uuid import UUID

import httpx
import pytest
import sqlalchemy as sa
import test_ai_vision
from reconbench_app.ai import shadow
from reconbench_app.ai.enqueue import AiHookEnqueuer
from reconbench_app.ai.gate import DeploymentGate
from reconbench_app.ai.limits import AiLimits, LimitsState
from reconbench_app.ai.runtime_checks import fingerprint_for
from reconbench_app.ai.shadow_report import main as shadow_report_main
from reconbench_app.ai.worker.handlers import default_handlers
from reconbench_app.ai.worker.openai_compat import OpenAICompatProvider
from reconbench_app.ai.worker.runner import AiRequestRunner
from reconbench_app.db.ids import new_id
from reconbench_app.db.metadata import metadata
from scripts.vision_fixtures import recorded_response, synthetic_invoice_pdf
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import DBAPIError
from test_ai_vision import (
    APPROVAL,
    MODEL,
    World,
    _grn_answer,
    _grn_pdf,
    _page_rows,
    _prepare,
    _Recorder,
    _runner,
    _upload,
    _view,
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
    document_id = _upload(world, synthetic_invoice_pdf(), name="shadow-invoice.pdf")
    # H1 keeps running (vision_enabled stays false) and the shadow reader is queued too.
    assert len(world.ai_queue.request_ids) == 1
    assert len(world.queue.vision_versions) == 1
    before = _counts(engine, world)

    assert _prepare(world) == ["queued"]
    [request] = _vision_requests(engine, world)
    assert request["shadow"] is True and request["provider"] == "gemini"
    assert request["request_state"] == "queued"
    provider = _Recorder([json.dumps(recorded_response())])
    runner = _runner(engine, keys[0], provider)
    runner.handlers = default_handlers(consensus_enqueue=_never_consensus)
    result = runner.run(request["id"])

    assert (result.state, result.apply_outcome) == ("succeeded", "applied"), result
    assert len(provider.calls) == 1
    assert request["id"] not in CONSENSUS_CALLS
    assert _counts(engine, world) == before  # 0 rows changed
    [page] = _page_rows(engine, world)
    assert page["image"] is None and page["purged_at"] is not None  # bytes purged
    [row] = _comparisons(engine, world)
    assert (row["ai_request_id"], row["provider"], row["model"]) == (request["id"], "gemini", MODEL)
    assert row["prompt_version"] == request["prompt_version"]
    assert len(row["reader_params_digest"]) == 64
    assert row["counts"]["fields_total"] == len(row["labels"]) > 0
    assert row["counts"]["agree_ocr"] > 0
    assert all(set(item) == {"field_key", "label"} for item in row["labels"])
    assert {item["label"] for item in row["labels"]} <= set(shadow.LABELS)
    values = _values(recorded_response())
    stored = json.dumps({"counts": row["counts"], "labels": row["labels"]})
    assert values and not [value for value in values if value in stored]

    # The review view never shows the shadow answer; H1 (still queued) is the source.
    view, _etag = _view(world, document_id)
    assert view["source"] == "text" and view["state"] == "pending"
    assert view["suggestions"] == []

    # Aggregates only, from the API and the CLI.
    response = world.client.get("/api/v1/ai/shadow-report")
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["reader_strategy"], body["shadow_reader"]) == ("shadow", "gemini")
    [entry] = body["providers"]
    assert entry["provider"] == "gemini" and entry["comparisons"] == 1
    assert entry["requests"] == {"succeeded": 1}
    assert entry["latency_ms_p50"] is not None
    assert {item["field"] for item in entry["fields"]} >= {"header:doc_no", "line:amount"}
    printed = io.StringIO()
    with redirect_stdout(printed):
        assert shadow_report_main(["--workspace", str(world.workspace_id)], engine=engine) == 0
    cli = json.loads(printed.getvalue())
    assert cli["providers"] == json.loads(json.dumps(body["providers"]))
    for text in (response.text, printed.getvalue()):
        assert not [value for value in values if value in text]
        assert str(document_id) not in text and str(request["subject_id"]) not in text

    # Append-only, and the report role cannot read it.
    with engine.connect() as connection:
        privileges = connection.execute(
            sa.text(
                "SELECT has_table_privilege('reconbench_report', 'reader_comparison', 'SELECT'),"
                " has_table_privilege('reconbench_app', 'reader_comparison', 'SELECT'),"
                " has_table_privilege('reconbench_app', 'reader_comparison', 'INSERT'),"
                " has_table_privilege('reconbench_app', 'reader_comparison', 'UPDATE'),"
                " has_table_privilege('reconbench_app', 'reader_comparison', 'DELETE')"
            )
        ).one()
    assert tuple(privileges) == (False, True, True, False, False)
    with pytest.raises(DBAPIError), engine.begin() as connection:
        connection.execute(
            sa.update(COMPARISONS).where(COMPARISONS.c.id == row["id"]).values(model="x")
        )


def test_a_document_read_only_in_shadow_says_so(engine: Engine, keys: Any, make_world: Any) -> None:
    world = make_world(vision_on=False)
    _shadow(engine, world, "gemini")
    document_id = _upload(world, _grn_pdf(), name="shadow-grn.pdf", zone="grn")
    assert _prepare(world) == ["queued"]
    [request] = _vision_requests(engine, world)
    assert request["shadow"] is True
    result = _runner(engine, keys[0], _Recorder([json.dumps(_grn_answer())])).run(request["id"])
    assert (result.state, result.apply_outcome) == ("succeeded", "applied"), result

    view, _etag = _view(world, document_id)
    assert (view["state"], view["code"]) == ("not_applicable", shadow.SHADOW_CODE)
    assert view["suggestions"] == [] and view["provider"] is None
    assert len(_comparisons(engine, world)) == 1


def test_switching_to_single_drops_a_queued_shadow_request(
    engine: Engine, keys: Any, make_world: Any
) -> None:
    world = make_world(vision_on=False)
    _shadow(engine, world, "gemini")
    _upload(world, synthetic_invoice_pdf(), name="shadow-switch.pdf")
    assert _prepare(world) == ["queued"]
    [request] = _vision_requests(engine, world)
    with engine.begin() as connection:
        connection.execute(
            sa.update(SETTINGS)
            .where(SETTINGS.c.workspace_id == world.workspace_id)
            .values(reader_strategy="single", vision_enabled=True)
        )
    provider = _Recorder([json.dumps(recorded_response())])
    result = _runner(engine, keys[0], provider).run(request["id"])

    assert (result.state, result.code) == ("skipped", "AI_OFF")
    assert provider.calls == [] and _comparisons(engine, world) == []


def test_company_server_shadow_reads_through_its_own_provider_only(
    engine: Engine, keys: Any, make_world: Any
) -> None:
    world = make_world(vision_on=False)
    _shadow(engine, world, "vllm")
    _upload(world, synthetic_invoice_pdf(), name="shadow-vllm.pdf")
    before = _counts(engine, world)
    assert _prepare(world) == ["queued"]
    [request] = _vision_requests(engine, world)
    assert (request["shadow"], request["provider"], request["model"]) == (
        True,
        "vllm",
        LOCAL_MODEL,
    )
    sent: list[httpx.Request] = []

    def server(call: httpx.Request) -> httpx.Response:
        sent.append(call)
        if call.method == "GET":
            return httpx.Response(200, json={"data": [{"id": LOCAL_MODEL, "max_model_len": 8_192}]})
        return httpx.Response(
            200,
            json={
                "model": LOCAL_MODEL,
                "choices": [
                    {
                        "message": {"content": json.dumps(recorded_response())},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 4_100, "completion_tokens": 900},
            },
        )

    runner = AiRequestRunner(
        engine=engine,
        keyring=keys[0],
        gate=DeploymentGate.from_values(
            "disabled", None, vllm_mode="enabled", vllm_endpoints=BASE_URL
        ),
        limits=lambda: LimitsState(AiLimits()),
        prices=lambda: None,
        providers={
            "vllm": OpenAICompatProvider(
                BASE_URL, frozenset({BASE_URL}), inner_transport=httpx.MockTransport(server)
            )
        },
        handlers=default_handlers(consensus_enqueue=_never_consensus),
    )
    result = runner.run(request["id"])

    assert (result.state, result.apply_outcome) == ("succeeded", "applied"), result
    # The page, then the page-1 header band (WI-Q2, D93).
    assert [str(call.url) for call in sent] == [
        f"{BASE_URL}/models",
        f"{BASE_URL}/chat/completions",
        f"{BASE_URL}/chat/completions",
    ]
    assert _counts(engine, world) == before
    assert request["id"] not in CONSENSUS_CALLS
    [row] = _comparisons(engine, world)
    assert (row["provider"], row["model"]) == ("vllm", LOCAL_MODEL)
    assert all(page["image"] is None for page in _page_rows(engine, world))
    with engine.connect() as connection:
        report = shadow.workspace_report(connection, world.workspace_id)
    assert [entry["provider"] for entry in report["providers"]] == ["vllm"]
    assert report["shadow_reader"] == "vllm"
