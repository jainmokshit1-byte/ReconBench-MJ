"""P3 (PD-2e): the AI worker queues the consensus job inside its own transaction.

The AI worker runs with Procrastinate's async connector open. Its answer
transaction is a synchronous SQLAlchemy connection, so the parse-queue job must be
deferred through a synchronous job manager on that same connection. Disposable
PostgreSQL only.
"""

from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import pytest
import sqlalchemy as sa
from procrastinate import App, PsycopgConnector
from reconbench_app.ai.task_contracts import RUN_CONSENSUS
from reconbench_app.ai.worker.main import build_consensus_deferrer
from sqlalchemy.engine import make_url


def _dsn() -> str:
    dsn = os.environ.get("RB_PHASE2_TEST_DATABASE_URL", "").strip()
    if not dsn:
        pytest.skip("RB_PHASE2_TEST_DATABASE_URL is required for disposable PostgreSQL tests")
    return dsn


def _jobs(engine: sa.Engine, run_id: str) -> int:
    with engine.connect() as connection:
        return int(
            connection.scalar(
                sa.text(
                    "SELECT count(*) FROM procrastinate_jobs "
                    "WHERE task_name = :task AND args->>'consensus_run_id' = :run_id"
                ),
                {"task": RUN_CONSENSUS, "run_id": run_id},
            )
            or 0
        )


def _defer_with_open_async_connector(dsn: str, *, commit: bool) -> str:
    engine = sa.create_engine(dsn)
    conninfo = make_url(dsn).set(drivername="postgresql").render_as_string(hide_password=False)
    app = App(connector=PsycopgConnector(conninfo=conninfo))
    defer = build_consensus_deferrer(app)
    run_id = uuid4()

    def answer_transaction() -> None:
        connection = engine.connect()
        transaction = connection.begin()
        defer(connection, uuid4(), run_id)
        if commit:
            transaction.commit()
        else:
            transaction.rollback()
        connection.close()

    async def worker() -> None:
        async with app.open_async():
            await asyncio.to_thread(answer_transaction)

    policy = getattr(asyncio, "WindowsSelectorEventLoopPolicy", None)
    loop = policy().new_event_loop() if policy is not None else asyncio.new_event_loop()
    try:
        loop.run_until_complete(worker())
    finally:
        loop.close()
    engine.dispose()
    return str(run_id)


def test_the_consensus_job_is_queued_with_the_answer_transaction() -> None:
    dsn = _dsn()
    run_id = _defer_with_open_async_connector(dsn, commit=True)
    engine = sa.create_engine(dsn)
    try:
        assert _jobs(engine, run_id) == 1
    finally:
        with engine.begin() as connection:
            connection.execute(
                sa.text("DELETE FROM procrastinate_jobs WHERE args->>'consensus_run_id' = :id"),
                {"id": run_id},
            )
        engine.dispose()


def test_a_rolled_back_answer_leaves_no_consensus_job() -> None:
    dsn = _dsn()
    run_id = _defer_with_open_async_connector(dsn, commit=False)
    engine = sa.create_engine(dsn)
    try:
        assert _jobs(engine, run_id) == 0
    finally:
        engine.dispose()
