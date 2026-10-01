"""Prove Procrastinate queue properties against a disposable PostgreSQL database."""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import psycopg
from procrastinate import App, JobContext
from procrastinate.psycopg_connector import PsycopgConnector
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.sql import SQL, Identifier

ROOT = Path(__file__).resolve().parents[2]
WORKER_QUEUES = ["phase1-spike"]


def normalize_dsn(value: str) -> str:
    """Convert the SQLAlchemy psycopg scheme to a libpq DSN."""

    return value.replace("postgresql+psycopg://", "postgresql://", 1)


def create_app(database_url: str) -> tuple[App, object]:
    """Create the same synthetic task registry in the parent and worker processes."""

    app = App(connector=PsycopgConnector(conninfo=database_url))

    @app.task(name="phase1.spike_marker", queue="phase1-spike", pass_context=True)
    async def write_marker(
        context: JobContext, marker_path: str, delay_seconds: float
    ) -> None:
        marker = Path(marker_path)

        def record_start() -> None:
            marker.parent.mkdir(parents=True, exist_ok=True)
            with marker.open("a", encoding="utf-8") as stream:
                stream.write(f"attempt={context.job.attempts}\n")

        await asyncio.to_thread(record_start)
        first_attempt = marker.with_suffix(".first")
        if not first_attempt.exists():
            await asyncio.to_thread(
                first_attempt.write_text, "first-attempt\n", encoding="utf-8"
            )
            await asyncio.sleep(delay_seconds)
        await asyncio.to_thread(
            marker.with_suffix(".done").write_text, "complete\n", encoding="utf-8"
        )

    return app, write_marker


def create_disposable_database(source_dsn: str) -> tuple[str, str, str]:
    """Create a uniquely named queue database and return its admin and worker DSNs."""

    source_dsn = normalize_dsn(source_dsn)
    info = conninfo_to_dict(source_dsn)
    info["dbname"] = "postgres"
    admin_dsn = make_conninfo(**info)
    database_name = f"rb_spike_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(admin_dsn, autocommit=True) as connection:
        connection.execute(SQL("CREATE DATABASE {}").format(Identifier(database_name)))
    info["dbname"] = database_name
    return admin_dsn, database_name, make_conninfo(**info)


async def verify_transactional_enqueue(app: App, task: object, database_url: str) -> None:
    """Show the entity row and queue row commit or roll back together."""

    await app.connector.execute_query_async(
        query="CREATE TABLE phase1_spike_records (record_key text PRIMARY KEY)"
    )
    async with await psycopg.AsyncConnection.connect(database_url) as connection:
        async with connection.transaction():
            await connection.execute(
                "INSERT INTO phase1_spike_records (record_key) VALUES (%s)",
                ("commit-pair",),
            )
            await task.configure(queue="atomic-proof", connection=connection).defer_async(
                marker_path="commit-pair", delay_seconds=0
            )

        class RollbackProof(Exception):
            pass

        try:
            async with connection.transaction():
                await connection.execute(
                    "INSERT INTO phase1_spike_records (record_key) VALUES (%s)",
                    ("rollback-pair",),
                )
                await task.configure(
                    queue="atomic-proof", connection=connection
                ).defer_async(marker_path="rollback-pair", delay_seconds=0)
                raise RollbackProof
        except RollbackProof:
            pass

    committed = await app.connector.execute_query_one_async(
        query="""
            SELECT
                (
                    SELECT count(*)
                    FROM phase1_spike_records
                    WHERE record_key = 'commit-pair'
                ) AS record_count,
                (
                    SELECT count(*)
                    FROM procrastinate_jobs
                    WHERE args->>'marker_path' = 'commit-pair'
                ) AS job_count
        """
    )
    rolled_back = await app.connector.execute_query_one_async(
        query="""
            SELECT
                (
                    SELECT count(*)
                    FROM phase1_spike_records
                    WHERE record_key = 'rollback-pair'
                ) AS record_count,
                (
                    SELECT count(*)
                    FROM procrastinate_jobs
                    WHERE args->>'marker_path' = 'rollback-pair'
                ) AS job_count
        """
    )
    assert committed == {"record_count": 1, "job_count": 1}, committed
    assert rolled_back == {"record_count": 0, "job_count": 0}, rolled_back
    print("PASS: synthetic data row and Procrastinate job commit and roll back atomically.")


async def verify_advisory_lock(database_url: str) -> None:
    """Verify PostgreSQL transaction advisory locks exclude a competing session."""

    lock_id = int(uuid.uuid4().int & 0x7FFFFFFF)
    async with (
        await psycopg.AsyncConnection.connect(database_url) as first,
        await psycopg.AsyncConnection.connect(database_url) as second,
    ):
        async with first.transaction():
            await first.execute("SELECT pg_advisory_xact_lock(%s)", (lock_id,))
            row = await (
                await second.execute(
                    "SELECT pg_try_advisory_xact_lock(%s) AS acquired", (lock_id,)
                )
            ).fetchone()
            assert row is not None and row[0] is False
        row = await (
            await second.execute(
                "SELECT pg_try_advisory_xact_lock(%s) AS acquired", (lock_id,)
            )
        ).fetchone()
        assert row is not None and row[0] is True
    print("PASS: PostgreSQL transaction advisory locks reject a concurrent holder.")


def start_worker(database_url: str, log_path: Path) -> subprocess.Popen[bytes]:
    """Start a separate synthetic Procrastinate worker process."""

    environment = os.environ.copy()
    environment["RB_DATABASE_URL"] = database_url
    with log_path.open("wb") as log_stream:
        return subprocess.Popen(
            [sys.executable, "-m", "scripts.spikes.sp1_procrastinate", "worker"],
            cwd=ROOT,
            env=environment,
            stdout=log_stream,
            stderr=subprocess.STDOUT,
        )


def wait_for_file(path: Path, worker: subprocess.Popen[bytes], timeout: float = 25) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        if worker.poll() is not None:
            raise RuntimeError(f"Worker exited early with status {worker.returncode}.")
        time.sleep(0.1)
    raise TimeoutError(f"Worker did not create {path} within {timeout} seconds.")


def wait_for_job_status(database_url: str, job_id: int, status: str, timeout: float = 25) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with psycopg.connect(database_url) as connection:
            row = connection.execute(
                "SELECT status::text, attempts FROM procrastinate_jobs WHERE id = %s",
                (job_id,),
            ).fetchone()
        if row is not None and row[0] == status:
            return
        time.sleep(0.1)
    raise TimeoutError(f"Job {job_id} did not reach status {status!r}.")


async def verify_killed_worker_recovery(
    app: App, task: object, database_url: str, temp_dir: Path
) -> None:
    """Detect and explicitly retry a job left doing by a killed worker."""

    marker = temp_dir / "killed-worker-starts.txt"
    job_id = await task.defer_async(marker_path=str(marker), delay_seconds=60)
    worker_log = temp_dir / "worker-killed.log"
    worker = start_worker(database_url, worker_log)
    try:
        wait_for_file(marker, worker)
        worker.kill()
        worker.wait(timeout=10)
        await asyncio.sleep(1.25)
        stalled = await app.job_manager.get_stalled_jobs(seconds_since_heartbeat=1.0)
        matching = [job for job in stalled if job.id == job_id]
        assert matching, f"Job {job_id} was not detected as stalled: {stalled}"
        await app.job_manager.retry_job_by_id_async(job_id, retry_at=datetime.now(UTC))
        recovered_worker = start_worker(database_url, temp_dir / "worker-recovery.log")
        try:
            wait_for_file(marker.with_suffix(".done"), recovered_worker)
            wait_for_job_status(database_url, job_id, "succeeded")
            recovered_worker.send_signal(signal.SIGTERM)
            recovered_worker.wait(timeout=10)
        finally:
            if recovered_worker.poll() is None:
                recovered_worker.kill()
                recovered_worker.wait(timeout=10)
    finally:
        if worker.poll() is None:
            worker.kill()
            worker.wait(timeout=10)
    print(
        "PASS: killed-worker job is detected as stalled; JobManager retry re-queues it "
        "and a new worker completes it."
    )


async def verify_sigterm_drain(
    task: object, database_url: str, temp_dir: Path
) -> None:
    """Send SIGTERM during a synthetic job and verify the worker drains it."""

    if os.name == "nt":
        print("SKIP: SIGTERM drain needs a POSIX worker; CI runs this spike on Linux.")
        return
    marker = temp_dir / "sigterm-worker-starts.txt"
    job_id = await task.defer_async(marker_path=str(marker), delay_seconds=1.5)
    worker_log = temp_dir / "worker-sigterm.log"
    worker = start_worker(database_url, worker_log)
    try:
        wait_for_file(marker, worker)
        worker.send_signal(signal.SIGTERM)
        worker.wait(timeout=10)
        assert worker.returncode == 0, f"Worker exited with {worker.returncode}."
        assert marker.with_suffix(".done").exists(), "Running task did not drain before shutdown."
        wait_for_job_status(database_url, job_id, "succeeded")
    finally:
        if worker.poll() is None:
            worker.kill()
            worker.wait(timeout=10)
    print("PASS: Procrastinate handles SIGTERM and drains the running synthetic job.")


def worker_main() -> int:
    database_url = normalize_dsn(os.environ["RB_DATABASE_URL"])
    app, _task = create_app(database_url)
    app.run_worker(
        queues=WORKER_QUEUES,
        name=f"phase1-spike-{os.getpid()}",
        wait=True,
        fetch_job_polling_interval=0.1,
        abort_job_polling_interval=0.1,
        update_heartbeat_interval=0.2,
        stalled_worker_timeout=1.0,
        shutdown_graceful_timeout=5.0,
        listen_notify=False,
        install_signal_handlers=True,
    )
    return 0


async def run_spikes(database_url: str) -> None:
    admin_dsn, database_name, disposable_dsn = create_disposable_database(database_url)
    app, task = create_app(disposable_dsn)
    try:
        with tempfile.TemporaryDirectory(prefix="reconbench-sp1-") as temporary:
            temp_dir = Path(temporary)
            async with app.open_async():
                await app.schema_manager.apply_schema_async()
                await verify_transactional_enqueue(app, task, disposable_dsn)
                await verify_advisory_lock(disposable_dsn)
                await verify_killed_worker_recovery(app, task, disposable_dsn, temp_dir)
                await verify_sigterm_drain(task, disposable_dsn, temp_dir)
    finally:
        await app.close_async()
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute(
                SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(Identifier(database_name))
            )


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "worker":
        return worker_main()
    database_url = os.environ.get("RB_DATABASE_URL")
    if not database_url:
        print(
            "Set RB_DATABASE_URL to a disposable PostgreSQL database connection.",
            file=sys.stderr,
        )
        return 2
    asyncio.run(run_spikes(normalize_dsn(database_url)))
    print("SP-1 complete: Procrastinate remains behind the JobQueue port pending the M2 load test.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
