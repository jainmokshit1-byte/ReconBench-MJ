"""PostgreSQL-coordinated control for the ReconBench Procrastinate workers."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Iterable
from contextlib import suppress
from typing import Any, cast

import psycopg
import sqlalchemy as sa
from procrastinate import App
from procrastinate.worker import Worker
from sqlalchemy.engine import create_engine, make_url

CONTROL_CHANNEL = "reconbench_worker_control"
CONTROL_DRAIN = "drain"
CONTROL_RESUME = "resume"
_LOCK_KEY = (0x52424354, 0x44524149)
_CONTROL_ID = 1
_LOGGER = logging.getLogger(__name__)


def _set_maintenance_pause(database_url: str, *, paused: bool, payload: str) -> None:
    """Persist the pause state before notifying processes that may be parked."""

    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with engine.begin() as connection:
            updated = connection.execute(
                sa.text(
                    "UPDATE worker_control SET paused = :paused, updated_at = now() "
                    "WHERE control_id = :control_id RETURNING paused"
                ),
                {"paused": paused, "control_id": _CONTROL_ID},
            ).scalar_one_or_none()
            if updated is None:
                raise RuntimeError("worker control state is missing; apply the latest migration")
            connection.execute(
                sa.text("SELECT pg_notify(:channel, :payload)"),
                {"channel": CONTROL_CHANNEL, "payload": payload},
            )
    finally:
        engine.dispose()


def drain_worker_processes(database_url: str, *, timeout_seconds: float = 300.0) -> int:
    """Persist a global pause and park controlled workers after active jobs finish.

    A timeout or unmanaged worker leaves the durable pause set. Operators must
    explicitly resume after resolving the condition, which prevents a fresh
    supervisor-started process from claiming jobs during maintenance.
    """

    if timeout_seconds <= 0:
        raise ValueError("worker drain timeout must be greater than zero")
    engine = create_engine(database_url, pool_pre_ping=True)
    lock_held = False
    try:
        with engine.begin() as connection:
            paused = connection.execute(
                sa.text(
                    "UPDATE worker_control SET paused = TRUE, updated_at = now() "
                    "WHERE control_id = :control_id RETURNING paused"
                ),
                {"control_id": _CONTROL_ID},
            ).scalar_one_or_none()
            if paused is None:
                raise RuntimeError("worker control state is missing; apply the latest migration")
            known_workers = int(
                connection.scalar(sa.text("SELECT count(*) FROM procrastinate_workers")) or 0
            )
            connection.execute(
                sa.text("SELECT pg_notify(:channel, :payload)"),
                {"channel": CONTROL_CHANNEL, "payload": CONTROL_DRAIN},
            )
        with engine.connect() as connection:
            deadline = time.monotonic() + timeout_seconds
            while time.monotonic() < deadline:
                lock_held = bool(
                    connection.scalar(
                        sa.text("SELECT pg_try_advisory_lock(:key_a, :key_b)"),
                        {"key_a": _LOCK_KEY[0], "key_b": _LOCK_KEY[1]},
                    )
                )
                connection.commit()
                if lock_held:
                    remaining_workers = int(
                        connection.scalar(sa.text("SELECT count(*) FROM procrastinate_workers"))
                        or 0
                    )
                    connection.commit()
                    if remaining_workers:
                        raise RuntimeError(
                            "Uncontrolled Procrastinate workers remain registered; "
                            "stop or upgrade them before draining ReconBench workers."
                        )
                    return known_workers
                time.sleep(min(0.1, max(0, deadline - time.monotonic())))
            raise TimeoutError(
                f"ReconBench workers did not drain within {timeout_seconds:g} seconds."
            )
    finally:
        if lock_held:
            with engine.begin() as connection:
                connection.execute(
                    sa.text("SELECT pg_advisory_unlock(:key_a, :key_b)"),
                    {"key_a": _LOCK_KEY[0], "key_b": _LOCK_KEY[1]},
                )
        engine.dispose()


def resume_worker_processes(database_url: str) -> None:
    """Clear the durable maintenance pause and wake parked workers."""

    _set_maintenance_pause(database_url, paused=False, payload=CONTROL_RESUME)


async def run_controlled_worker(
    app: App,
    database_url: str,
    *,
    queues: Iterable[str],
    name: str,
    fetch_job_polling_interval: float = 5.0,
    abort_job_polling_interval: float = 5.0,
    update_heartbeat_interval: float = 10.0,
    stalled_worker_timeout: float = 30.0,
    shutdown_graceful_timeout: float = 300.0,
) -> None:
    """Run workers that can be drained and resumed by another ReconBench process.

    One connection holds a shared advisory lock for the worker's active lifetime;
    another listens for control notifications. The persisted pause row fences
    newly started processes as well as workers receiving a drain notification.
    A worker rechecks the row after acquiring the shared lock to close the startup
    race with a concurrent drain.
    """

    conninfo = (
        make_url(database_url).set(drivername="postgresql").render_as_string(hide_password=False)
    )
    async with (
        app.open_async(),
        await psycopg.AsyncConnection.connect(conninfo) as lock_connection,
        await psycopg.AsyncConnection.connect(conninfo) as listener_connection,
    ):
        await listener_connection.execute(f"LISTEN {CONTROL_CHANNEL}")
        await listener_connection.commit()
        notifications: asyncio.Queue[str] = asyncio.Queue()

        async def receive_notifications() -> None:
            async for notification in listener_connection.notifies():
                await notifications.put(notification.payload)
            raise ConnectionError("worker control notification connection closed")

        listener_task = asyncio.create_task(receive_notifications())
        lock_held = False
        try:
            while True:
                while not lock_held:
                    if await _maintenance_is_paused(lock_connection):
                        _LOGGER.info("controlled worker parked by persisted maintenance pause")
                        await _wait_for_control(notifications, listener_task)
                        continue
                    acquired = await lock_connection.execute(
                        "SELECT pg_try_advisory_lock_shared(%s, %s)", _LOCK_KEY
                    )
                    lock_row = await acquired.fetchone()
                    lock_held = bool(lock_row and lock_row[0])
                    await lock_connection.commit()
                    if not lock_held:
                        await _wait_for_control(notifications, listener_task)
                        continue
                    if await _maintenance_is_paused(lock_connection):
                        await _unlock_shared(lock_connection)
                        lock_held = False
                        continue

                worker: Any = Worker(
                    app=app,
                    queues=list(queues),
                    name=name,
                    wait=True,
                    fetch_job_polling_interval=fetch_job_polling_interval,
                    abort_job_polling_interval=abort_job_polling_interval,
                    update_heartbeat_interval=update_heartbeat_interval,
                    stalled_worker_timeout=stalled_worker_timeout,
                    shutdown_graceful_timeout=shutdown_graceful_timeout,
                    install_signal_handlers=True,
                )
                worker_task = asyncio.create_task(worker.run())
                while True:
                    next_control = asyncio.create_task(notifications.get())
                    done, _pending = await asyncio.wait(
                        {worker_task, next_control, listener_task},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if listener_task in done:
                        next_control.cancel()
                        await _cancel_task(next_control)
                        await listener_task
                    if worker_task in done:
                        next_control.cancel()
                        await _cancel_task(next_control)
                        await worker_task
                        await _unlock_shared(lock_connection)
                        lock_held = False
                        return

                    control = next_control.result()
                    if control != CONTROL_DRAIN:
                        continue
                    worker.stop()
                    await worker_task
                    await _unlock_shared(lock_connection)
                    lock_held = False
                    break

                # Stay alive but unregistered while drained. This prevents a
                # Compose restart policy from immediately starting work again.
                _LOGGER.info("controlled worker parked after graceful drain")
                await _wait_for_control(notifications, listener_task)
        finally:
            listener_task.cancel()
            await _cancel_task(listener_task)
            if lock_held:
                await _unlock_shared(lock_connection)


async def _wait_for_control(
    notifications: asyncio.Queue[str], listener_task: asyncio.Task[None]
) -> str:
    control_task = asyncio.create_task(notifications.get())
    done, _pending = await asyncio.wait(
        {control_task, listener_task}, return_when=asyncio.FIRST_COMPLETED
    )
    if listener_task in done:
        control_task.cancel()
        await _cancel_task(control_task)
        await listener_task
    return control_task.result()


async def _unlock_shared(connection: psycopg.AsyncConnection[object]) -> None:
    result = await connection.execute("SELECT pg_advisory_unlock_shared(%s, %s)", _LOCK_KEY)
    await result.fetchone()
    await connection.commit()


async def _maintenance_is_paused(connection: psycopg.AsyncConnection[object]) -> bool:
    result = await connection.execute(
        "SELECT paused FROM worker_control WHERE control_id = %s", (_CONTROL_ID,)
    )
    row = await result.fetchone()
    await connection.commit()
    if row is None:
        raise RuntimeError("worker control state is missing; apply the latest migration")
    return cast(tuple[bool, ...], row)[0]


async def _cancel_task(task: asyncio.Task[object]) -> None:
    if not task.done():
        task.cancel()
    with suppress(asyncio.CancelledError):
        await task


__all__ = [
    "CONTROL_CHANNEL",
    "CONTROL_DRAIN",
    "CONTROL_RESUME",
    "drain_worker_processes",
    "resume_worker_processes",
    "run_controlled_worker",
]
