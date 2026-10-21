"""Gemini Interactions adapter; the only code that sends provider requests."""

from __future__ import annotations

import base64
from typing import Any

import httpx
from google.genai._gaos.lib import compat_errors

from reconbench_app.ai.provider import (
    ProviderFailure,
    ProviderRequest,
    ProviderResult,
    ProviderUsage,
)
from reconbench_app.ai.worker.transport_guard import (
    AsyncProviderPathBlocked,
    DuplicateAttemptBlocked,
    ProviderAttemptFinished,
    ProviderTransportFailure,
    map_provider_error,
    map_provider_http_exception,
    map_provider_transport_failure,
    new_gemini_client,
)


def _count(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _usage(interaction: Any) -> ProviderUsage | None:
    usage = getattr(interaction, "usage", None)
    input_tokens = _count(getattr(usage, "total_input_tokens", None))
    output_tokens = _count(getattr(usage, "total_output_tokens", None))
    if input_tokens is None or output_tokens is None:
        return None
    return ProviderUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        thought_tokens=_count(getattr(usage, "total_thought_tokens", None)),
    )


def _input(request: ProviderRequest) -> Any:
    """Plain text for text hooks (unchanged shape); text and page images for h6_vision."""

    if not request.images:
        return request.input_text
    parts: list[dict[str, Any]] = [{"type": "text", "text": request.input_text}]
    for image in request.images:
        parts.append({"type": "text", "text": image.caption})
        parts.append(
            {
                "type": "image",
                "data": base64.b64encode(image.data).decode("ascii"),
                "mime_type": image.mime_type,
                "resolution": image.resolution,
            }
        )
    return parts


def _generation_config(request: ProviderRequest) -> dict[str, Any]:
    """``request.options`` is not read: Gemini traffic stays as recorded (WI-BE1 golden)."""

    config: dict[str, Any] = {"max_output_tokens": request.max_output_tokens}
    if request.thinking_level is not None:
        config["thinking_level"] = request.thinking_level
    return config


class GeminiProvider:
    name = "gemini"

    def __init__(self, *, inner_transport: httpx.BaseTransport | None = None) -> None:
        self._inner_transport = inner_transport

    def complete(self, request: ProviderRequest, *, api_key: str) -> ProviderResult:
        with new_gemini_client(
            api_key, timeout_ms=request.timeout_ms, inner_transport=self._inner_transport
        ) as client:
            try:
                interaction = client.interactions.create(
                    model=request.model,
                    system_instruction=request.system_instruction,
                    input=_input(request),
                    response_format={
                        "type": "text",
                        "mime_type": "application/json",
                        "schema": dict(request.response_schema),
                    },
                    generation_config=_generation_config(request),
                    store=False,
                )
            except ProviderAttemptFinished as exc:
                mapping = map_provider_error(exc.status_code, exc.safe_metadata)
                raise ProviderFailure(
                    mapping.code, delivery="responded", cooldown_seconds=mapping.cooldown_seconds
                ) from None
            except ProviderTransportFailure as exc:
                mapping = map_provider_transport_failure(exc)
                raise ProviderFailure(mapping.code, delivery=exc.delivery) from None
            except compat_errors.APIStatusError as exc:
                mapping = map_provider_http_exception(exc)
                raise ProviderFailure(
                    mapping.code, delivery="responded", cooldown_seconds=mapping.cooldown_seconds
                ) from None
            except (DuplicateAttemptBlocked, AsyncProviderPathBlocked):
                raise ProviderFailure("error", delivery="may_have_been_sent") from None
            except Exception:  # noqa: BLE001 - SDK errors may embed provider text.
                raise ProviderFailure("error", delivery="may_have_been_sent") from None

        usage = _usage(interaction)
        if getattr(interaction, "status", None) != "completed":
            raise ProviderFailure("invalid_output", delivery="responded", usage=usage)
        output_text = getattr(interaction, "output_text", None)
        if not isinstance(output_text, str) or not output_text.strip():
            raise ProviderFailure("invalid_output", delivery="responded", usage=usage)
        model = getattr(interaction, "model", None)
        return ProviderResult(
            output_text=output_text,
            usage=usage,
            model_reported=model if isinstance(model, str) else None,
        )


__all__ = ["GeminiProvider"]
