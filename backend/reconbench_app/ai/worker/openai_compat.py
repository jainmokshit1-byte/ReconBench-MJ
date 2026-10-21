"""OpenAI-compatible chat-completions adapter for a self-hosted vLLM server (WI-BE2).

Plain ``httpx`` (no SDK). The target is the company's own vLLM server serving
Qwen3.5-9B (plan §0.5): an 8192-token context, thinking on by default and no
reasoning parser. So every request carries its own settings: thinking off
(``chat_template_kwargs``), the reader's sampling profile, a fixed seed and the
v4 schema in the ``openai_json_schema`` dialect, and at most one page image.

Like the Gemini adapter, a request is one HTTP attempt (``SingleAttemptTransport``):
no retry, no redirect, no proxy from the environment. The base URL is checked
against the deployment allowlist before any I/O. Gemini and this provider are
separate alternatives; nothing here is ever combined with a Gemini answer.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import time
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

import httpx

from reconbench_app.ai.endpoint_policy import EndpointNotAllowed, check_allowed
from reconbench_app.ai.provider import (
    Delivery,
    ProviderFailure,
    ProviderRequest,
    ProviderResult,
    ProviderUsage,
)
from reconbench_app.ai.worker.transport_guard import (
    DuplicateAttemptBlocked,
    ProviderAttemptFinished,
    ProviderTransportFailure,
    SingleAttemptTransport,
)

_LOGGER = logging.getLogger(__name__)

PROVIDER_NAME = "vllm"
SEED = 20261016
VISION_SCHEMA_NAME = "vision_reading"
TEXT_SCHEMA_NAME = "answer"
BACKOFF_SECONDS = 30
HEALTH_TIMEOUT_SECONDS = 5.0
MIN_TIMEOUT_MS = 1_000
MAX_TIMEOUT_MS = 300_000
DETAIL_MAX_CHARS = 300
THINKING_PREFIXES = ("Thinking Process", "<think>")
# Thinking off in every request: the server's default is on and it has no reasoning
# parser, so thinking text would land in ``content`` and burn the token budget.
CHAT_TEMPLATE_KWARGS: Mapping[str, Any] = MappingProxyType({"enable_thinking": False})

# Qwen3.5 model card, non-thinking general profile (AU §10). ``top_k``, ``min_p`` and
# ``repetition_penalty`` are vLLM extra parameters. ``_pp0`` is the WI-EV2 arm.
SAMPLING_PROFILES: Mapping[str, Mapping[str, float | int]] = MappingProxyType(
    {
        "qwen_card_nonthinking": MappingProxyType(
            {
                "temperature": 0.7,
                "top_p": 0.8,
                "top_k": 20,
                "min_p": 0.0,
                "presence_penalty": 1.5,
                "repetition_penalty": 1.0,
            }
        ),
        "qwen_card_nonthinking_pp0": MappingProxyType(
            {
                "temperature": 0.7,
                "top_p": 0.8,
                "top_k": 20,
                "min_p": 0.0,
                "presence_penalty": 0.0,
                "repetition_penalty": 1.0,
            }
        ),
    }
)
DEFAULT_SAMPLING_PROFILE = "qwen_card_nonthinking"

_LONG_DIGITS = re.compile(r"\d{6,}")


@dataclass(frozen=True, slots=True)
class OpenAICompatError:
    """Sanitized error code for one HTTP outcome (no provider message)."""

    code: str
    delivery: Delivery = "responded"
    cooldown_seconds: int | None = None


def map_openai_compat_error(status_code: int) -> OpenAICompatError:
    """Map an HTTP status from an OpenAI-compatible server (plan WI-BE2 table)."""

    if 300 <= status_code < 400:
        return OpenAICompatError("error")  # redirects are never followed
    if status_code == 400:
        return OpenAICompatError("INVALID_REQUEST")
    if status_code in {401, 403}:
        return OpenAICompatError("bad_key")
    if status_code == 404:
        return OpenAICompatError("MODEL_UNAVAILABLE")
    if status_code in {408, 504}:
        return OpenAICompatError("timeout")
    if status_code == 429:
        return OpenAICompatError("rate_limited", cooldown_seconds=BACKOFF_SECONDS)
    if status_code == 503:
        return OpenAICompatError("PROVIDER_UNAVAILABLE", cooldown_seconds=BACKOFF_SECONDS)
    if status_code >= 500:
        return OpenAICompatError("error")
    if 400 <= status_code < 500:
        return OpenAICompatError("INVALID_REQUEST")
    return OpenAICompatError("error")


def map_openai_compat_transport_failure(failure: ProviderTransportFailure) -> OpenAICompatError:
    """Nothing is billed on a self-hosted server, so there is no "uncertain" outcome:
    a refused connection was never sent, anything later counts as answered."""

    if failure.delivery == "not_sent":
        return OpenAICompatError("PROVIDER_UNAVAILABLE", delivery="not_sent")
    if failure.failure_kind in {"ReadTimeout", "WriteTimeout", "PoolTimeout"}:
        return OpenAICompatError("timeout")
    return OpenAICompatError("PROVIDER_UNAVAILABLE")


def sanitize_detail(text: object) -> str | None:
    """At most 300 characters on one line, every run of 6+ digits masked."""

    if not isinstance(text, str) or not text.strip():
        return None
    flat = " ".join(text.split())
    masked = _LONG_DIGITS.sub(lambda match: "D" * len(match.group()), flat)
    return masked[:DETAIL_MAX_CHARS]


def _error_detail(response: httpx.Response) -> str | None:
    try:
        body = response.json()
    except ValueError:
        return None
    if not isinstance(body, Mapping):
        return None
    error = body.get("error")
    if isinstance(error, Mapping):
        return sanitize_detail(error.get("message"))
    return sanitize_detail(body.get("message") or body.get("detail"))


def _count(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _usage(body: Mapping[str, Any]) -> ProviderUsage | None:
    usage = body.get("usage")
    if not isinstance(usage, Mapping):
        return None
    input_tokens = _count(usage.get("prompt_tokens"))
    output_tokens = _count(usage.get("completion_tokens"))
    if input_tokens is None or output_tokens is None:
        return None
    return ProviderUsage(
        input_tokens=input_tokens, output_tokens=output_tokens, thought_tokens=None
    )


def _user_content(request: ProviderRequest) -> str | list[dict[str, Any]]:
    if not request.images:
        return request.input_text
    parts: list[dict[str, Any]] = [{"type": "text", "text": request.input_text}]
    for image in request.images:
        encoded = base64.b64encode(image.data).decode("ascii")
        parts.append({"type": "text", "text": image.caption})
        parts.append(
            {"type": "image_url", "image_url": {"url": f"data:{image.mime_type};base64,{encoded}"}}
        )
    return parts


def request_body(request: ProviderRequest) -> dict[str, Any]:
    """The exact JSON body sent to ``{base_url}/chat/completions``."""

    profile_name = request.options.get("sampling_profile") or DEFAULT_SAMPLING_PROFILE
    if profile_name == "provider_default":
        profile_name = DEFAULT_SAMPLING_PROFILE
    profile = SAMPLING_PROFILES.get(str(profile_name))
    if profile is None:
        raise ProviderFailure("INVALID_REQUEST", delivery="not_sent", detail="unknown sampling")
    return {
        "model": request.model,
        "messages": [
            {"role": "system", "content": request.system_instruction},
            {"role": "user", "content": _user_content(request)},
        ],
        "max_tokens": request.max_output_tokens,
        **profile,
        "seed": SEED,
        "chat_template_kwargs": dict(CHAT_TEMPLATE_KWARGS),
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": VISION_SCHEMA_NAME if request.images else TEXT_SCHEMA_NAME,
                "schema": dict(request.response_schema),
                "strict": True,
            },
        },
        "stream": False,
    }


@dataclass(frozen=True, slots=True)
class HealthResult:
    ok: bool
    latency_ms: int | None
    models: tuple[str, ...] = ()
    max_model_len: Mapping[str, int] = field(default_factory=dict)
    code: str | None = None


class OpenAICompatProvider:
    """One vLLM server at an allowlisted base URL (which already ends in ``/v1``)."""

    name = PROVIDER_NAME
    external = False

    def __init__(
        self,
        base_url: str,
        allowed_prefixes: frozenset[str],
        *,
        inner_transport: httpx.BaseTransport | None = None,
    ) -> None:
        # Fails at construction (worker startup) on a URL that is not allowlisted.
        self.base_url = check_allowed(base_url, allowed_prefixes)
        self._allowed = allowed_prefixes
        self._inner_transport = inner_transport
        self._context_windows: dict[str, int] = {}
        self.counters: Counter[str] = Counter()
        self._siblings: dict[str, OpenAICompatProvider] = {self.base_url: self}

    def for_base_url(self, base_url: str) -> OpenAICompatProvider:
        """The provider for a workspace's own allowlisted server URL (WI-BE3).

        Same allowlist and transport; one cached instance per normalised URL, so
        each server keeps its own context-window cache. A URL that is not
        allowlisted raises :class:`EndpointNotAllowed` (nothing is sent).
        """

        normalized = check_allowed(base_url, self._allowed)
        sibling = self._siblings.get(normalized)
        if sibling is None:
            sibling = OpenAICompatProvider(
                normalized, self._allowed, inner_transport=self._inner_transport
            )
            sibling.counters = self.counters
            sibling._siblings = self._siblings
            self._siblings[normalized] = sibling
        return sibling

    # ------------------------------------------------------------------ transport

    def _endpoint(self) -> str:
        """Re-check the allowlist before every request (no I/O when it fails)."""

        try:
            return check_allowed(self.base_url, self._allowed)
        except EndpointNotAllowed:
            raise ProviderFailure("ENDPOINT_NOT_ALLOWED", delivery="not_sent") from None

    def _client(self, timeout: httpx.Timeout) -> httpx.Client:
        inner = self._inner_transport or httpx.HTTPTransport(retries=0)
        return httpx.Client(
            transport=SingleAttemptTransport(inner),
            follow_redirects=False,
            trust_env=False,
            timeout=timeout,
        )

    def _headers(self, api_key: str) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return headers

    # ------------------------------------------------------------------ health

    def health(self, *, api_key: str = "") -> HealthResult:
        """``GET {base_url}/models``: reachable, served model ids and their context window."""

        try:
            base_url = self._endpoint()
        except ProviderFailure as failure:
            return HealthResult(False, None, code=failure.code)
        started = time.monotonic()
        timeout = httpx.Timeout(HEALTH_TIMEOUT_SECONDS)
        try:
            with self._client(timeout) as client:
                response = client.get(f"{base_url}/models", headers=self._headers(api_key))
        except ProviderAttemptFinished as exc:
            return HealthResult(False, None, code=map_openai_compat_error(exc.status_code).code)
        except ProviderTransportFailure as exc:
            return HealthResult(False, None, code=map_openai_compat_transport_failure(exc).code)
        except Exception:  # noqa: BLE001 - never surface transport text.
            return HealthResult(False, None, code="error")
        latency_ms = int((time.monotonic() - started) * 1_000)
        if response.status_code != 200:
            return HealthResult(
                False, latency_ms, code=map_openai_compat_error(response.status_code).code
            )
        try:
            body = response.json()
        except ValueError:
            return HealthResult(False, latency_ms, code="invalid_output")
        data = body.get("data") if isinstance(body, Mapping) else None
        if not isinstance(data, list):
            return HealthResult(False, latency_ms, code="invalid_output")
        models: list[str] = []
        windows: dict[str, int] = {}
        for item in data:
            if not isinstance(item, Mapping) or not isinstance(item.get("id"), str):
                continue
            model_id = str(item["id"])
            models.append(model_id)
            window = _count(item.get("max_model_len"))
            if window:
                windows[model_id] = window
        self._context_windows.update(windows)
        return HealthResult(True, latency_ms, tuple(models), MappingProxyType(windows))

    def context_window(self, model: str) -> int | None:
        """``max_model_len`` of ``model`` (from the last health check, fetched once)."""

        if model not in self._context_windows:
            self.health()
        return self._context_windows.get(model)

    # ------------------------------------------------------------------ completion

    def complete(self, request: ProviderRequest, *, api_key: str) -> ProviderResult:
        base_url = self._endpoint()
        if len(request.images) > 1:
            raise ProviderFailure(
                "INVALID_REQUEST", delivery="not_sent", detail="one page per call"
            )
        if not MIN_TIMEOUT_MS <= request.timeout_ms <= MAX_TIMEOUT_MS:
            raise ProviderFailure("INVALID_REQUEST", delivery="not_sent", detail="timeout bound")
        body = request_body(request)
        timeout = httpx.Timeout(connect=5.0, read=request.timeout_ms / 1_000, write=30.0, pool=5.0)
        started = time.monotonic()
        try:
            with self._client(timeout) as client:
                response = client.post(
                    f"{base_url}/chat/completions", json=body, headers=self._headers(api_key)
                )
                response.read()
        except ProviderAttemptFinished as exc:
            mapping = map_openai_compat_error(exc.status_code)
            raise ProviderFailure(
                mapping.code, delivery="responded", cooldown_seconds=mapping.cooldown_seconds
            ) from None
        except ProviderTransportFailure as exc:
            mapping = map_openai_compat_transport_failure(exc)
            raise ProviderFailure(mapping.code, delivery=mapping.delivery) from None
        except DuplicateAttemptBlocked:
            raise ProviderFailure("error", delivery="responded") from None
        except Exception:  # noqa: BLE001 - transport text may echo request content.
            raise ProviderFailure("error", delivery="responded") from None
        latency_ms = int((time.monotonic() - started) * 1_000)

        if response.status_code != 200:
            mapping = map_openai_compat_error(response.status_code)
            detail = _error_detail(response) if response.status_code == 400 else None
            if detail:
                _LOGGER.warning("vllm_request_rejected status=400 detail=%s", detail)
            raise ProviderFailure(
                mapping.code,
                delivery=mapping.delivery,
                cooldown_seconds=mapping.cooldown_seconds,
                detail=detail,
            )
        return self._result(response, latency_ms)

    def _result(self, response: httpx.Response, latency_ms: int) -> ProviderResult:
        try:
            body = response.json()
        except ValueError:
            raise ProviderFailure("invalid_output", delivery="responded") from None
        if not isinstance(body, Mapping):
            raise ProviderFailure("invalid_output", delivery="responded")
        usage = _usage(body)
        choices = body.get("choices")
        choice = choices[0] if isinstance(choices, list) and choices else None
        message = choice.get("message") if isinstance(choice, Mapping) else None
        if not isinstance(message, Mapping):
            raise ProviderFailure("invalid_output", delivery="responded", usage=usage)
        assert isinstance(choice, Mapping)
        finish_reason = choice.get("finish_reason")
        reasoning = message.get("reasoning_content") or message.get("reasoning")
        reasoning_present = isinstance(reasoning, str) and bool(reasoning.strip())
        if reasoning_present:
            self.counters["reasoning_present"] += 1
            _LOGGER.warning("vllm_reasoning_present count=%d", self.counters["reasoning_present"])
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ProviderFailure("invalid_output", delivery="responded", usage=usage)
        if content.lstrip().startswith(THINKING_PREFIXES):
            self.counters["thinking_leak"] += 1
            _LOGGER.warning("vllm_thinking_leak count=%d", self.counters["thinking_leak"])
            raise ProviderFailure(
                "invalid_output", delivery="responded", usage=usage, detail="thinking_leak"
            )
        if finish_reason == "length":
            self.counters["at_output_cap"] += 1
            raise ProviderFailure(
                "invalid_output", delivery="responded", usage=usage, detail="at_output_cap"
            )
        try:
            json.loads(content)
        except ValueError:
            raise ProviderFailure(
                "invalid_output", delivery="responded", usage=usage, detail="not_json"
            ) from None
        model = body.get("model")
        return ProviderResult(
            output_text=content,
            usage=usage,
            latency_ms=latency_ms,
            finish_reason=finish_reason if isinstance(finish_reason, str) else None,
            model_reported=model if isinstance(model, str) else None,
            reasoning_present=reasoning_present,
        )


__all__ = [
    "BACKOFF_SECONDS",
    "CHAT_TEMPLATE_KWARGS",
    "DEFAULT_SAMPLING_PROFILE",
    "HealthResult",
    "OpenAICompatError",
    "OpenAICompatProvider",
    "PROVIDER_NAME",
    "SAMPLING_PROFILES",
    "SEED",
    "map_openai_compat_error",
    "map_openai_compat_transport_failure",
    "request_body",
    "sanitize_detail",
]
