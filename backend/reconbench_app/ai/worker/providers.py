"""The provider registry of the AI worker: ``ai_request.provider`` → adapter.

Every request goes to exactly one provider, the one its ledger row names; there
is no fallback to another provider and no request that calls two (owner decision
of 16 Oct 2026). A provider missing here skips the request instead of sending it.
"""

from __future__ import annotations

import httpx

from reconbench_app.ai.endpoint_policy import EndpointNotAllowed
from reconbench_app.ai.provider import AiProvider
from reconbench_app.ai.worker.config import AiWorkerSettings, AiWorkerStartupError
from reconbench_app.ai.worker.gemini import GeminiProvider
from reconbench_app.ai.worker.openai_compat import OpenAICompatProvider


def build_providers(
    settings: AiWorkerSettings, *, vllm_transport: httpx.BaseTransport | None = None
) -> dict[str, AiProvider]:
    """Gemini always; the vLLM provider only when ``RB_AI_VLLM_BASE_URL`` is set and
    ``RB_AI_VLLM_CALLS`` is not ``disabled``. A base URL that is not on
    ``RB_AI_VLLM_ENDPOINTS`` stops the worker at startup."""

    gemini = GeminiProvider()
    providers: dict[str, AiProvider] = {gemini.name: gemini}
    if not settings.ai_vllm_base_url:  # unset, or "" forwarded by Compose
        return providers
    gate = settings.deployment_gate()
    if gate.vllm_mode == "disabled":
        return providers
    try:
        vllm = OpenAICompatProvider(
            settings.ai_vllm_base_url,
            frozenset(gate.vllm_endpoints),
            inner_transport=vllm_transport,
        )
    except EndpointNotAllowed as exc:
        raise AiWorkerStartupError(f"RB_AI_VLLM_BASE_URL: {exc}") from None
    providers[vllm.name] = vllm
    return providers


__all__ = ["build_providers"]
