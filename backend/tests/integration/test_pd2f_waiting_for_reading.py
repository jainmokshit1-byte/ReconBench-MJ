"""PD-2f "Waiting for reading" on a disposable PostgreSQL 16 database, no gate patched.

A scanned invoice read with the switch on AI waits for its reading; mapping, vendor
resolution and reconciliation skip it. A reading that fails says why and waits for a
person's Retry or "Read with rules" (both audited); a Retry asks the active provider
only and never replays a cached answer. Synthetic documents only; the company server is
an in-process mock.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx
import pytest
import sqlalchemy as sa
from reconbench_app import reading_state
from reconbench_app.ai import ledger
from reconbench_app.ai.gate import DeploymentGate
from reconbench_app.ai.limits import AiLimits, LimitsState
from reconbench_app.ai.worker.handlers import default_handlers
from reconbench_app.ai.worker.openai_compat import OpenAICompatProvider
from reconbench_app.ai.worker.runner import AiRequestRunner
from reconbench_app.db.metadata import metadata
from reconbench_app.mapping_service import MappingService
from scripts.vision_fixtures import synthetic_invoice_pdf
from sqlalchemy.engine import Engine
from tests.integration.test_ai_vision import CSRF, World, _prepare, engine, keys, make_world
from tests.integration.test_pd2c_pd2d_reading import (
    BASE_URL,
    LOCAL_MODEL,
    _company_server,
    _upload,
)
from tests.support.scan_images import scanned_invoice_pdf

__all__ = ["engine", "keys", "make_world"]

DOCUMENTS = metadata.tables["document"]
REQUESTS = metadata.tables["ai_request"]
AUDIT = metadata.tables["audit_log"]
SETTINGS = metadata.tables["ai_setting"]


def _reading(engine: Engine, document_id: UUID) -> tuple[str, str | None]:
    with engine.connect() as connection:
        row = connection.execute(
            sa.select(DOCUMENTS.c.reading_state, DOCUMENTS.c.reading_failure_code).where(
                DOCUMENTS.c.id == document_id
            )
        ).one()
    return str(row.reading_state), row.reading_failure_code


def _used_by_mapping(engine: Engine, world: World) -> set[UUID]:
    """The documents mapping, the ledger and reconciliation load (one shared loader)."""

    with engine.connect() as connection:
        rows, *_rest = MappingService._load_current(connection, world.workspace_id)
    return {row["document_id"] for row in rows}


def _runner(engine: Engine, keyring: Any, server: Any, consensus: Any = None) -> AiRequestRunner:
    default_url = "http://vllm-default.example.test/v1"
    provider = OpenAICompatProvider(
        default_url,
        frozenset({default_url, BASE_URL}),
        inner_transport=httpx.MockTransport(server),
    )
    return AiRequestRunner(
        engine=engine,
        keyring=keyring,
        gate=DeploymentGate.from_values(
            "disabled", None, vllm_mode="enabled", vllm_endpoints=f"{default_url},{BASE_URL}"
        ),
        limits=lambda: LimitsState(AiLimits()),
        prices=lambda: None,
        providers={"vllm": provider},
        handlers=default_handlers(consensus_enqueue=consensus),
    )


def _down(request: httpx.Request) -> httpx.Response:
    del request
    return httpx.Response(500, json={"error": "synthetic server error"})


def _answer(content: str) -> Any:
    def server(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": LOCAL_MODEL, "max_model_len": 8192}]})
        return httpx.Response(
            200,
            json={
                "model": LOCAL_MODEL,
                "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 10},
            },
        )

    return server


def _failed_scan(engine: Engine, keys: Any, world: World) -> UUID:
    _company_server(engine, world, capable=True, scanned="ai")
    [document_id] = _upload(world, scanned_invoice_pdf(), name="scan-wait.pdf")
    assert _prepare(world) == ["queued"]
    [request_id] = world.ai_queue.request_ids
    _runner(engine, keys[0], _down).run(request_id)
    return document_id


def _etag(world: World, document_id: UUID) -> str:
    response = world.client.get(f"/api/v1/documents/{document_id}")
    assert response.status_code == 200, response.text
    return response.headers["etag"]


def _audit(engine: Engine, world: World, action: str) -> list[Any]:
    with engine.connect() as connection:
        return list(
            connection.execute(
                sa.select(AUDIT.c.before_state, AUDIT.c.after_state).where(
                    AUDIT.c.workspace_id == world.workspace_id, AUDIT.c.action == action
                )
            ).all()
        )


def test_a_scanned_invoice_waits_and_mapping_skips_it(engine: Engine, make_world: Any) -> None:
    world = make_world()
    _company_server(engine, world, capable=True, scanned="ai")
    [scanned_id] = _upload(world, scanned_invoice_pdf(), name="scan-wait.pdf")
    [digital_id] = _upload(world, synthetic_invoice_pdf(), name="digital.pdf")
    assert _reading(engine, scanned_id) == ("waiting", None)
    assert _reading(engine, digital_id) == ("ready", None)
    used = _used_by_mapping(engine, world)
    assert digital_id in used and scanned_id not in used


def test_with_the_switch_on_rules_a_scan_is_ready_at_once(engine: Engine, make_world: Any) -> None:
    world = make_world()
    _company_server(engine, world, capable=True, scanned="rules")
    [document_id] = _upload(world, scanned_invoice_pdf(), name="scan-rules.pdf")
    assert _reading(engine, document_id) == ("ready", None)
    assert document_id in _used_by_mapping(engine, world)


def test_a_provider_failure_fails_the_reading_with_its_reason(
    engine: Engine, keys: Any, make_world: Any
) -> None:
    world = make_world()
    document_id = _failed_scan(engine, keys, world)
    state, code = _reading(engine, document_id)
    assert state == "failed" and code
    assert document_id not in _used_by_mapping(engine, world)
    # A person can't edit it until they choose (step 6 D4).
    refused = world.client.put(
        f"/api/v1/documents/{document_id}/correction",
        json={"vendor_name": "Typo Traders"},
        headers={"If-Match": _etag(world, document_id), "X-CSRF-Token": CSRF},
    )
    assert refused.status_code == 409 and refused.json()["code"] == "reading_pending"


@pytest.mark.parametrize(
    ("content", "code"),
    [
        # Not JSON: the adapter refuses it as the provider's failure.
        ("not json at all", "invalid_output"),
        # JSON that breaks the answer schema: an answered request that can't be used.
        ('{"unexpected": true}', reading_state.ANSWER_INVALID),
    ],
)
def test_an_answer_that_fails_the_checks_fails_the_reading(
    engine: Engine, keys: Any, make_world: Any, content: str, code: str
) -> None:
    world = make_world()
    _company_server(engine, world, capable=True, scanned="ai")
    [document_id] = _upload(world, scanned_invoice_pdf(), name="scan-bad.pdf")
    assert _prepare(world) == ["queued"]
    [request_id] = world.ai_queue.request_ids
    _runner(engine, keys[0], _answer(content)).run(request_id)
    assert _reading(engine, document_id) == ("failed", code)


def test_retry_asks_the_active_provider_again_without_the_cache(
    engine: Engine, keys: Any, make_world: Any
) -> None:
    world = make_world()
    document_id = _failed_scan(engine, keys, world)
    retried = world.client.post(
        f"/api/v1/documents/{document_id}/reading/retry",
        headers={"If-Match": _etag(world, document_id), "X-CSRF-Token": CSRF},
    )
    assert retried.status_code == 200, retried.text
    assert retried.json()["document"]["reading_state"] == "waiting"
    assert world.queue.retried_by[-1] == world.user_id
    [(before, after)] = _audit(engine, world, "document.reading_retry")
    assert before["reading_state"] == "failed" and after == {"reading_state": "waiting"}
    # The page job runs again for the person: a new attempt, to the company server only.
    version_id = world.queue.vision_versions[-1]
    assert (
        world.service.prepare_vision_pages(
            world.workspace_id, version_id, requested_by=world.user_id
        )
        == "queued"
    )
    with engine.connect() as connection:
        attempts = connection.execute(
            sa.select(REQUESTS.c.provider, REQUESTS.c.requested_by, REQUESTS.c.attempt_no)
            .where(REQUESTS.c.workspace_id == world.workspace_id)
            .order_by(REQUESTS.c.attempt_no)
        ).all()
    assert [(row.provider, row.requested_by, row.attempt_no) for row in attempts] == [
        ("vllm", None, 1),
        ("vllm", world.user_id, 2),
    ]
    _runner(engine, keys[0], _down).run(world.ai_queue.request_ids[-1])
    with engine.connect() as connection:
        cache_key = connection.scalar(
            sa.select(REQUESTS.c.cache_key).where(REQUESTS.c.id == world.ai_queue.request_ids[-1])
        )
    assert cache_key is None
    assert _reading(engine, document_id)[0] == "failed"


def test_retry_is_refused_when_the_switch_is_back_on_rules(
    engine: Engine, keys: Any, make_world: Any
) -> None:
    world = make_world()
    document_id = _failed_scan(engine, keys, world)
    with engine.begin() as connection:
        connection.execute(
            sa.update(SETTINGS)
            .where(SETTINGS.c.workspace_id == world.workspace_id)
            .values(scanned_invoice_reader="rules", version=SETTINGS.c.version + 1)
        )
    refused = world.client.post(
        f"/api/v1/documents/{document_id}/reading/retry",
        headers={"If-Match": _etag(world, document_id), "X-CSRF-Token": CSRF},
    )
    assert refused.status_code == 422 and refused.json()["code"] == "reading_retry_unavailable"
    assert _reading(engine, document_id)[0] == "failed"


def test_read_with_rules_makes_the_rules_version_count(
    engine: Engine, keys: Any, make_world: Any
) -> None:
    world = make_world()
    document_id = _failed_scan(engine, keys, world)
    chosen = world.client.post(
        f"/api/v1/documents/{document_id}/reading/use-rules",
        headers={"If-Match": _etag(world, document_id), "X-CSRF-Token": CSRF},
    )
    assert chosen.status_code == 200, chosen.text
    assert chosen.json()["version"]["reading_method"] == "rules"
    assert _reading(engine, document_id) == ("ready", None)
    assert document_id in _used_by_mapping(engine, world)
    [(before, after)] = _audit(engine, world, "document.reading_use_rules")
    assert before["reading_state"] == "failed" and after == {"reading_state": "ready"}
    # A second click finds nothing to choose.
    again = world.client.post(
        f"/api/v1/documents/{document_id}/reading/use-rules",
        headers={"If-Match": _etag(world, document_id), "X-CSRF-Token": CSRF},
    )
    assert again.status_code == 422 and again.json()["code"] == "reading_state_changed"


def test_page_images_that_cannot_be_made_fail_the_reading(
    engine: Engine, make_world: Any, monkeypatch: Any
) -> None:
    from reconbench_app import ingestion as ingestion_module

    world = make_world()
    _company_server(engine, world, capable=True, scanned="ai")
    [document_id] = _upload(world, scanned_invoice_pdf(), name="scan-pages.pdf")
    monkeypatch.setattr(ingestion_module, "vision_pages_isolated", lambda *args, **kwargs: None)
    assert _prepare(world) == ["failed"]
    assert _reading(engine, document_id) == ("failed", "VISION_PAGES_FAILED")


def test_the_sweep_fails_a_reading_nothing_is_working_on(engine: Engine, make_world: Any) -> None:
    world = make_world()
    _company_server(engine, world, capable=True, scanned="ai")
    [document_id] = _upload(world, scanned_invoice_pdf(), name="scan-stall.pdf")
    # The page job never ran (it gave up): no request exists for the version.
    with engine.begin() as connection:
        connection.execute(
            sa.update(DOCUMENTS)
            .where(DOCUMENTS.c.id == document_id)
            .values(updated_at=datetime.now(UTC) - timedelta(hours=3))
        )
    ledger.sweep_requests(engine, now=datetime.now(UTC), queue_timeout_seconds=3600)
    assert _reading(engine, document_id) == ("failed", reading_state.STALLED)


def test_an_older_failed_attempt_never_fails_a_retried_reading(
    engine: Engine, keys: Any, make_world: Any
) -> None:
    world = make_world()
    document_id = _failed_scan(engine, keys, world)
    world.client.post(
        f"/api/v1/documents/{document_id}/reading/retry",
        headers={"If-Match": _etag(world, document_id), "X-CSRF-Token": CSRF},
    )
    world.service.prepare_vision_pages(
        world.workspace_id, world.queue.vision_versions[-1], requested_by=world.user_id
    )
    with engine.begin() as connection:
        assert reading_state.fail_finished_readings(connection) == 0
    assert _reading(engine, document_id) == ("waiting", None)
