"""Real AI task implementations, registered only inside the AI worker process."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from procrastinate import App, JobContext
from procrastinate.retry import RetryStrategy

from reconbench_app.ai.task_contracts import AI_QUEUE, RUN_AI_REQUEST
from reconbench_app.ai.worker.runner import AiRequestDeferred, AiRequestRunner

# Re-running a deferred job only retries the *reservation*; the provider has not
# been called. Bounded well past the default 30-minute queue timeout.
_RESERVATION_RETRY = RetryStrategy(max_attempts=240, wait=10, retry_exceptions=[AiRequestDeferred])


def register_ai_worker_tasks(app: App, runner: AiRequestRunner) -> dict[str, Any]:
    @app.task(name=RUN_AI_REQUEST, queue=AI_QUEUE, retry=_RESERVATION_RETRY, pass_context=True)
    def run_request(context: JobContext, ai_request_id: str) -> None:
        runner.run(UUID(ai_request_id), job_id=context.job.id)

    return {RUN_AI_REQUEST: run_request}


__all__ = ["register_ai_worker_tasks"]
