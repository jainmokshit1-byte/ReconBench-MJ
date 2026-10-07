from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

from procrastinate import SyncPsycopgConnector
from procrastinate.manager import JobManager
from reconbench_app.jobs import ProcrastinateJobQueue


class _Deferrer:
    def __init__(self, job_manager: Any) -> None:
        self.job_manager = job_manager
        self.arguments: dict[str, str] | None = None

    def defer(self, **arguments: str) -> int:
        self.arguments = arguments
        return 42


class _Task:
    def __init__(self, deferrer: _Deferrer) -> None:
        self.deferrer = deferrer
        self.connection: Any = None

    def configure(self, *, connection: Any) -> _Deferrer:
        self.connection = connection
        return self.deferrer


def test_worker_queue_uses_sync_manager_with_transaction_connection() -> None:
    original_manager = object()
    sync_manager = cast(JobManager, object())
    deferrer = _Deferrer(original_manager)
    task = _Task(deferrer)
    psycopg_connection = object()
    sqlalchemy_connection = SimpleNamespace(
        connection=SimpleNamespace(driver_connection=psycopg_connection)
    )
    queue = ProcrastinateJobQueue(
        {"parse_file": task},
        sync_job_manager=sync_manager,
    )

    result = queue.enqueue(
        "parse_file",
        file_id=UUID("00000000-0000-0000-0000-000000000001"),
        connection=cast(Any, sqlalchemy_connection),
    )

    assert result == 42
    assert task.connection is psycopg_connection
    assert deferrer.job_manager is sync_manager
    assert deferrer.arguments == {"file_id": "00000000-0000-0000-0000-000000000001"}


def test_api_queue_keeps_task_manager_when_no_override_is_configured() -> None:
    task_manager = JobManager(SyncPsycopgConnector(conninfo="dbname=unused"))
    deferrer = _Deferrer(task_manager)
    task = _Task(deferrer)
    queue = ProcrastinateJobQueue({"parse_file": task})
    sqlalchemy_connection = SimpleNamespace(connection=SimpleNamespace(driver_connection=object()))

    assert (
        queue.enqueue(
            "parse_file",
            file_id=UUID("00000000-0000-0000-0000-000000000002"),
            connection=cast(Any, sqlalchemy_connection),
        )
        == 42
    )
    assert deferrer.job_manager is task_manager
