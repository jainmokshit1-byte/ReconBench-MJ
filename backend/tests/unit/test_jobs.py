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


class _LockedDeferrer(_Deferrer):
    def __init__(self, job_manager: Any, error: Exception | None = None) -> None:
        super().__init__(job_manager)
        self.error = error

    def defer(self, **arguments: str) -> int:
        if self.error is not None:
            raise self.error
        return super().defer(**arguments)


class _LockedTask:
    def __init__(self, deferrer: _Deferrer) -> None:
        self.deferrer = deferrer
        self.queueing_lock: str | None = None

    def configure(self, *, connection: Any, queueing_lock: str | None = None) -> _Deferrer:
        del connection
        self.queueing_lock = queueing_lock
        return self.deferrer


class _Savepoint:
    def __init__(self, log: list[str]) -> None:
        self.log = log

    def __enter__(self) -> None:
        self.log.append("savepoint")

    def __exit__(self, kind: Any, value: Any, traceback: Any) -> bool:
        self.log.append("rollback" if kind is not None else "release")
        return False


class _LockConnection:
    def __init__(self, waiting: int | None) -> None:
        self.waiting = waiting
        self.locks: list[str] = []
        self.log: list[str] = []
        self.connection = SimpleNamespace(driver_connection=object())

    def scalar(self, statement: Any, parameters: dict[str, str]) -> int | None:
        del statement
        self.locks.append(parameters["lock"])
        return self.waiting

    def begin_nested(self) -> _Savepoint:
        return _Savepoint(self.log)


WS = UUID("00000000-0000-0000-0000-0000000000aa")
PO = UUID("00000000-0000-0000-0000-0000000000bb")


def test_recompute_po_reuses_a_waiting_job_for_the_same_po() -> None:
    deferrer = _LockedDeferrer(object())
    task = _LockedTask(deferrer)
    connection = _LockConnection(waiting=7)
    queue = ProcrastinateJobQueue({"recompute_po": task})

    result = queue.enqueue_recompute_po(
        workspace_id=WS, po_document_id=PO, connection=cast(Any, connection)
    )

    assert result == 7
    assert deferrer.arguments is None  # nothing new was queued
    assert connection.locks == [f"recompute_po:{WS}:{PO}"]


def test_recompute_po_is_queued_with_its_lock_inside_a_savepoint() -> None:
    deferrer = _LockedDeferrer(object())
    task = _LockedTask(deferrer)
    connection = _LockConnection(waiting=None)
    queue = ProcrastinateJobQueue({"recompute_po": task, "drain_dirty_scopes": task})

    assert (
        queue.enqueue_recompute_po(
            workspace_id=WS, po_document_id=PO, connection=cast(Any, connection)
        )
        == 42
    )
    assert task.queueing_lock == f"recompute_po:{WS}:{PO}"
    assert connection.log == ["savepoint", "release"]
    queue.enqueue_dirty_scopes(workspace_id=WS, connection=cast(Any, connection))
    assert task.queueing_lock == f"drain_dirty_scopes:{WS}"


def test_a_concurrent_duplicate_rolls_back_only_the_savepoint() -> None:
    from procrastinate.exceptions import AlreadyEnqueued

    deferrer = _LockedDeferrer(object(), error=AlreadyEnqueued("taken"))
    task = _LockedTask(deferrer)
    connection = _LockConnection(waiting=None)
    queue = ProcrastinateJobQueue({"drain_dirty_scopes": task})

    assert queue.enqueue_dirty_scopes(workspace_id=WS, connection=cast(Any, connection)) == 0
    assert connection.log == ["savepoint", "rollback"]


class _PriorityTask:
    def __init__(self, deferrer: _Deferrer) -> None:
        self.deferrer = deferrer
        self.options: dict[str, Any] = {}

    def configure(self, **options: Any) -> _Deferrer:
        self.options = options
        return self.deferrer


def test_a_reconciliation_run_is_queued_ahead_of_ledger_and_mapping_jobs() -> None:
    from reconbench_app.jobs import RECONCILIATION_JOB_PRIORITY

    deferrer = _Deferrer(object())
    task = _PriorityTask(deferrer)
    sqlalchemy_connection = SimpleNamespace(connection=SimpleNamespace(driver_connection=object()))
    queue = ProcrastinateJobQueue({"evaluate_cases": task, "parse_file": task})

    run_id = UUID("00000000-0000-0000-0000-0000000000cc")
    assert (
        queue.enqueue_reconciliation(run_id=run_id, connection=cast(Any, sqlalchemy_connection))
        == 42
    )
    assert task.options["priority"] == RECONCILIATION_JOB_PRIORITY > 0
    assert deferrer.arguments == {"run_id": str(run_id)}
    # Other jobs keep Procrastinate's default priority (no option passed).
    queue.enqueue(
        "parse_file",
        file_id=UUID("00000000-0000-0000-0000-000000000003"),
        connection=cast(Any, sqlalchemy_connection),
    )
    assert "priority" not in task.options
