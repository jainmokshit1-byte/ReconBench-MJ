"""Entry point for the dedicated AI worker: the only consumer of the ``ai`` queue."""

from __future__ import annotations

import asyncio
import os
import socket
from uuid import UUID

import sqlalchemy as sa
from procrastinate import App, PsycopgConnector
from sqlalchemy.engine import Connection, make_url

from reconbench_app.ai.consensus import enqueue_consensus_run
from reconbench_app.ai.limits import LimitsState, PriceFile, load_limits, load_prices
from reconbench_app.ai.task_contracts import AI_QUEUE, register_consensus_task_declaration
from reconbench_app.ai.worker.config import AiWorkerSettings, check_startup_environment
from reconbench_app.ai.worker.handlers import default_handlers
from reconbench_app.ai.worker.key_open import Keyring
from reconbench_app.ai.worker.providers import build_providers
from reconbench_app.ai.worker.runner import AiRequestRunner
from reconbench_app.ai.worker.sweeps import gemini_health, heartbeat_loop
from reconbench_app.ai.worker.tasks import register_ai_worker_tasks
from reconbench_app.logging_config import configure_logging
from reconbench_app.worker_control import run_controlled_worker

AI_WORKER_QUEUES = (AI_QUEUE,)


def main() -> int:
    check_startup_environment(os.environ)
    settings = AiWorkerSettings()
    configure_logging(settings.log_level)
    keyring = Keyring.load(settings.ai_keyring_file)
    gate = settings.deployment_gate()

    def limits_state() -> LimitsState:
        return load_limits(settings.ai_limits_file)

    def prices() -> PriceFile | None:
        return load_prices(settings.ai_price_file)

    engine = sa.create_engine(settings.database_url, pool_pre_ping=True)
    conninfo = (
        make_url(settings.database_url)
        .set(drivername="postgresql")
        .render_as_string(hide_password=False)
    )
    app = App(connector=PsycopgConnector(conninfo=conninfo))
    consensus_task = register_consensus_task_declaration(app)

    def defer_consensus(connection: Connection, workspace_id: UUID, consensus_run_id: UUID) -> None:
        consensus_task.configure(
            connection=connection.connection.driver_connection,
            queueing_lock=f"consensus:{consensus_run_id}",
        ).defer(workspace_id=str(workspace_id), consensus_run_id=str(consensus_run_id))

    def enqueue_consensus(
        connection: Connection,
        workspace_id: UUID,
        document_version_id: UUID,
        ai_request_id: UUID,
        ai_call_id: UUID,
    ) -> bool:
        return enqueue_consensus_run(
            connection,
            workspace_id=workspace_id,
            document_version_id=document_version_id,
            ai_request_id=ai_request_id,
            ai_call_id=ai_call_id,
            defer_job=defer_consensus,
        )

    providers = build_providers(settings)
    runner = AiRequestRunner(
        engine=engine,
        keyring=keyring,
        gate=gate,
        limits=limits_state,
        prices=prices,
        providers=providers,
        handlers=default_handlers(
            vision_reader=settings.vision_reader,
            consensus_enqueue=enqueue_consensus,
        ),
    )
    register_ai_worker_tasks(app, runner)
    instance = f"reconbench-ai-{socket.gethostname()}"
    selector_policy = getattr(asyncio, "WindowsSelectorEventLoopPolicy", None)
    if selector_policy is not None:
        asyncio.set_event_loop_policy(selector_policy())

    async def run() -> None:
        heartbeat = asyncio.create_task(
            heartbeat_loop(
                engine,
                instance,
                interval_seconds=settings.ai_heartbeat_seconds,
                limits=limits_state,
                vllm_provider=providers.get("vllm"),
                gemini_health_check=lambda at: gemini_health(engine, runner.run, at),
            )
        )
        try:
            await run_controlled_worker(
                app, settings.database_url, queues=list(AI_WORKER_QUEUES), name=instance
            )
        finally:
            heartbeat.cancel()

    try:
        asyncio.run(run())
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
