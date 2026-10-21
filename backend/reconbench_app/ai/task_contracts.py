"""AI task names, queue and ID-only arguments shared by producers and the AI worker.

Producers (API, general worker) register the declarations below so they can
enqueue by name; executing a declaration raises. Only ``reconbench_app.ai.worker``
registers the real implementations under the same names, and only the AI worker
process consumes the ``ai`` queue.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from procrastinate import App

AI_QUEUE = "ai"
RUN_AI_REQUEST = "reconbench.ai.run_request"
AI_TASK_NAMES = (RUN_AI_REQUEST,)
CONSENSUS_QUEUE = "parse"
RUN_CONSENSUS = "reconbench.process_consensus"


class AiTaskNotExecutableHere(RuntimeError):
    """An AI task was executed by a process that is not the AI worker."""

    def __init__(self, task_name: str) -> None:
        super().__init__(f"{task_name} runs only in the AI worker")


def run_request_kwargs(ai_request_id: UUID) -> dict[str, str]:
    """Return the only argument an AI job carries: a ledger row id."""

    return {"ai_request_id": str(UUID(str(ai_request_id)))}


def register_ai_task_declarations(app: App) -> dict[str, Any]:
    """Register producer-side declarations that can be deferred but never run."""

    @app.task(name=RUN_AI_REQUEST, queue=AI_QUEUE)
    def run_request(ai_request_id: str) -> None:
        del ai_request_id
        raise AiTaskNotExecutableHere(RUN_AI_REQUEST)

    return {RUN_AI_REQUEST: run_request}


def register_consensus_task_declaration(app: App) -> Any:
    """Declare the parse-queue follow-up in the AI worker for atomic deferral."""

    @app.task(name=RUN_CONSENSUS, queue=CONSENSUS_QUEUE)
    def process_consensus(workspace_id: str, consensus_run_id: str) -> None:
        del workspace_id, consensus_run_id
        raise AiTaskNotExecutableHere(RUN_CONSENSUS)

    return process_consensus


__all__ = [
    "AI_QUEUE",
    "AI_TASK_NAMES",
    "CONSENSUS_QUEUE",
    "RUN_AI_REQUEST",
    "RUN_CONSENSUS",
    "AiTaskNotExecutableHere",
    "register_ai_task_declarations",
    "register_consensus_task_declaration",
    "run_request_kwargs",
]
