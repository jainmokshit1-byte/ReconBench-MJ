"""Vision reader in the accuracy bench (accuracy plan AC-5): cached page-image readings.

For every PDF document the bench parsed, the app's own sandbox code renders and
masks its pages (``vision_pages_isolated``), and the app's own prompt, payload
digest and cache key identify the request the AI worker would send. A recorded
answer for that key (``work/accuracy-cache/vision/<key>.json``) is checked by
the same rules as in the app and scored *alone* against the truth, and next to
the OCR reading (the agreement matrix AC-6 needs). In AC-5 vision values are
suggestions only, so the main pipeline (vendors, links, reconciliation) never
sees them.

Provider calls happen only in the owner's run: ``--vision provider
--allow-provider`` with ``BENCH_GEMINI_KEY`` set and a page budget
(``--max-provider-pages``) that covers every missing page; otherwise nothing is
sent. Masked page images and answers stay in ``work/`` (git-ignored); the report
holds counts only, never document values.

Each run reads with exactly one vision provider (owner decision of 16 Oct 2026,
LLM plan §0.6): ``--vision-provider gemini`` (the default) or ``vllm`` (the
company server at an allowlisted ``--vision-base-url``; no Gemini client is
built). A :class:`VisionArm` names that provider and its reader settings
(thinking, sampling, strategy, repeats); the default arm is exactly the app's
Gemini reader, so its keys and answers are the ones recorded before.

Cache keys are the app's v2 vision keys (provider + reader parameters, WI-BE5).
``--vision-cache-compat v1`` additionally reads answers recorded under the old v1
key (read-only, from ``work/accuracy-cache/vision-v1``) when the reader is still
Gemini with the parameters that applied before v2, so the D41 evidence stays
usable without sending again.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from reconbench_app.ai.cache import cache_key, request_cache_key
from reconbench_app.ai.contracts import (
    InvalidOutput,
    VisionOutput,
    error_patterns,
    parse_output,
)
from reconbench_app.ai.endpoint_policy import EndpointNotAllowed, check_allowed, parse_allowlist
from reconbench_app.ai.gate import payload_digest
from reconbench_app.ai.limits import MIN_LOCAL_OUTPUT_TOKENS, AiLimits, local_output_budget
from reconbench_app.ai.prompts import (
    LEGACY_VISION_PROMPT_VERSION,
    LEGACY_VISION_SCHEMA_VERSION,
    VISION_PROMPT_VARIANTS,
    PromptPayload,
    legacy_vision_prompt_v3,
    vision_prompt,
    vision_schema_version,
)
from reconbench_app.ai.provider import ProviderFailure, ProviderRequest, ProviderUsage
from reconbench_app.ai.redaction import IMAGE_MASK_VERSION
from reconbench_app.ai.vision import (
    HOOK,
    PageRow,
    RenderedPage,
    VisionContext,
    VisionValidation,
    merge_page_answers,
    page_prompts,
    provider_images,
    reader_params,
    validate_vision,
)
from reconbench_app.ai.vision_bands import header_band
from reconbench_app.ai.vision_crops import (
    CROP_BANDS,
    CROP_VERSION,
    crop_band,
    crop_reasons,
    merge_page_crops,
    totals_open,
)
from reconbench_app.extraction.vision import (
    PROVIDER_READERS,
    VISION_RENDER,
    VisionReaderInfo,
    vision_reader,
)
from reconbench_app.extraction.vision_pages import vision_pages_isolated
from reconbench_core.extraction.pages import split_pages
from reconbench_core.parsers.types import BuyerEntityHint
from reconbench_core.schema import DocType, Document

READER = "vision_gemini"
V1_READER_PARAMS: Mapping[str, Any] = {
    "jpeg_quality": 90,
    "mask_version": "image_mask.v1",
    "max_pixels": None,
    "max_side": 2048,
    "media_resolution": "high",
    "pass": "full",
    "prompt_variant": "gemini",
    "reader": "vision_gemini",
    "render_version": "vision.render.v1",
    "sampling_profile": "provider_default",
    "thinking": "low",
}
"""The reader parameters every v1-keyed answer was recorded with (before WI-BE5)."""
PROVIDERS = ("gemini", "vllm")
THINKING_CHOICES = ("off", "minimal", "low", "medium", "high")
SAMPLING_CHOICES = ("provider_default", "qwen_card_nonthinking", "qwen_card_nonthinking_pp0")
STRATEGY_CHOICES = ("full", "crops", "band", "crops-control")
CROP_STRATEGIES = frozenset({"crops", "crops-control"})
DEFAULT_VLLM_MODEL = "qwen3.5:9b"
"""The served model id on the company server (plan §0.5)."""
MAX_REPEATS = 10
MAX_PAGES = AiLimits().h6_vision.max_pages or 8
_PAGE_KIND_TYPES = {
    "invoice": "invoice",
    "credit_note": "invoice",
    "debit_note": "invoice",
    "purchase_order": "po",
    "goods_receipt": "grn",
}
_VISION_CODE = (
    "backend/reconbench_app/extraction/vision_pages.py",
    "backend/reconbench_app/extraction/vision.py",
    "backend/reconbench_app/extraction/local_hybrid.py",
    "backend/reconbench_app/ai/redaction.py",
)


class ArmRefused(SystemExit):
    """A bench setting that cannot be run (the message says why; nothing was sent)."""


@dataclass(frozen=True)
class VisionArm:
    """One vision provider and its reader settings for a whole bench run (§0.6).

    ``None`` keeps the provider reader's own setting, so the default arm is the
    app's reader and its cache keys are unchanged. ``repeats`` is how many times
    each request is read; repeat 0 has the reader's own key, repeat ``i`` a key
    derived from it (:func:`repeat_key`).
    """

    provider: str = "gemini"
    thinking: str | None = None
    sampling: str | None = None
    strategy: str = "full"
    base_url: str | None = None
    repeats: int = 1
    prompt_variant: str | None = None
    send_from_repeat: int = 0
    """Send only repeats ``>= send_from_repeat``; earlier ones are scored, never sent (a
    same-day control read of one new repeat, D77). Not part of any key."""
    """Another wording of the reader's prompt (``VISION_PROMPT_VARIANTS``), e.g. to read
    ``qwen`` (v4.qwen) again after the app moved the vLLM reader on (D76)."""

    @property
    def local(self) -> bool:
        return self.provider == "vllm"

    def reader(self) -> VisionReaderInfo:
        base = vision_reader(PROVIDER_READERS[self.provider])
        assert base is not None
        # The bench reads the header band as its own arm (``--vision-strategy band``), so
        # an arm's reader never carries the app's band read and its request keys (and the
        # stored answers they name) stay those of the full-page reads (D93).
        changes: dict[str, Any] = {"band_prompt_variant": None}
        if self.thinking is not None:
            changes["thinking"] = self.thinking
        if self.sampling is not None:
            changes["sampling_profile"] = self.sampling
        if self.prompt_variant is not None:
            changes["prompt_variant"] = self.prompt_variant
        return replace(base, **changes)

    def describe(self) -> dict[str, Any]:
        """The arm for the report (no URL: that is deployment detail)."""

        reader = self.reader()
        return {
            "provider": self.provider,
            "reader": reader.name,
            "thinking": reader.thinking,
            "sampling_profile": reader.sampling_profile,
            "prompt_version": VISION_PROMPT_VARIANTS[reader.prompt_variant],
            "schema_version": vision_schema_version(reader.prompt_variant),
            "strategy": self.strategy,
            "repeats": self.repeats,
            "send_from_repeat": self.send_from_repeat,
        }


DEFAULT_ARM = VisionArm()


def check_arm(arm: VisionArm, *, allowlist: str | None, model: str) -> VisionArm:
    """Refuse settings that are not runnable yet or would mix providers (§0.5, §0.6)."""

    if arm.provider not in PROVIDERS:
        raise ArmRefused(f"--vision-provider must be one of {', '.join(PROVIDERS)}")
    if arm.strategy not in STRATEGY_CHOICES:
        raise ArmRefused(f"--vision-strategy must be one of {', '.join(STRATEGY_CHOICES)}")
    if arm.strategy in CROP_STRATEGIES and arm.provider != "vllm":
        raise ArmRefused("--vision-strategy crops is the Qwen two-pass read (WI-BE8): vllm only")
    if not 1 <= arm.repeats <= MAX_REPEATS:
        raise ArmRefused(f"--repeat must be between 1 and {MAX_REPEATS}")
    if not 0 <= arm.send_from_repeat < arm.repeats:
        raise ArmRefused("--send-from-repeat must be between 0 and --repeat minus 1")
    if arm.thinking is not None and arm.thinking not in THINKING_CHOICES:
        raise ArmRefused(f"--vision-thinking must be one of {', '.join(THINKING_CHOICES)}")
    if arm.sampling is not None and arm.sampling not in SAMPLING_CHOICES:
        raise ArmRefused(f"--vision-sampling must be one of {', '.join(SAMPLING_CHOICES)}")
    if arm.prompt_variant is not None and arm.prompt_variant not in VISION_PROMPT_VARIANTS:
        raise ArmRefused(
            f"--vision-prompt-variant must be one of {', '.join(VISION_PROMPT_VARIANTS)}"
        )
    if arm.provider == "gemini":
        if arm.base_url is not None:
            raise ArmRefused("--vision-base-url is for --vision-provider vllm only")
        if arm.thinking == "off":
            raise ArmRefused("Gemini takes --vision-thinking minimal, low, medium or high")
        if arm.sampling not in {None, "provider_default"}:
            raise ArmRefused("Gemini reads with --vision-sampling provider_default only")
        if not model.startswith("gemini"):
            raise ArmRefused(f"model {model!r} is not a Gemini model; use --vision-provider vllm")
        if arm.prompt_variant not in {None, "gemini", "gemini_q2"}:
            raise ArmRefused("Gemini reads with --vision-prompt-variant gemini or gemini_q2 only")
        return arm
    # vLLM: thinking must be off in every request (no reasoning parser, plan §0.5).
    if arm.thinking not in {None, "off"}:
        raise ArmRefused("the vLLM server is read with --vision-thinking off only (plan §0.5)")
    if arm.sampling == "provider_default":
        raise ArmRefused(
            "vLLM takes --vision-sampling qwen_card_nonthinking or qwen_card_nonthinking_pp0"
        )
    if model.startswith("gemini"):
        raise ArmRefused(f"model {model!r} is a Gemini model; use --vision-provider gemini")
    if arm.prompt_variant in {"gemini", "gemini_q2"}:
        raise ArmRefused("the vLLM server takes a Qwen --vision-prompt-variant (qwen…)")
    if arm.base_url is not None:
        try:
            entries = parse_allowlist((allowlist or "").split(","))
            normalized = check_allowed(arm.base_url, entries)
        except EndpointNotAllowed as error:
            raise ArmRefused(
                f"--vision-base-url refused: {error} (RB_AI_VLLM_ENDPOINTS); nothing was sent"
            ) from None
        return replace(arm, base_url=normalized)
    return arm


def repeat_key(key: str, index: int) -> str:
    """The cache key of repeat ``index`` (0 keeps the request's own key)."""

    if index == 0:
        return key
    return _sha256(json.dumps({"key": key, "repeat": index}, sort_keys=True))


@dataclass
class VisionTarget:
    """One parsed PDF document and the pages the vision reader would read."""

    file_id: str
    index: int
    truth_id: str | None
    doc_type: DocType
    document: Document
    content: bytes = field(repr=False)
    pages: tuple[int, ...]
    rows: list[PageRow] = field(default_factory=list)
    payload: PromptPayload | None = None
    key: str | None = None
    legacy_key: str | None = None
    """The v1 cache key of the same request (``--vision-cache-compat v1`` only)."""
    validation: VisionValidation | None = None
    outcome: str = "pending"
    output_tokens: int | None = None
    """Output plus thought tokens of the recorded answer (for the output cap decision)."""
    ocr_pages: bool = False
    """Whether the rules reader used OCR on any page of this document."""
    scan_type: str = "unknown"
    """``digital`` (no OCR page), ``scan`` (every page OCR) or ``mixed`` (rules reader)."""
    ocr_text: str = field(default="", repr=False)
    """The rules reader's page text (for the not-in-OCR-text count; never reported)."""
    parts: tuple[PromptPayload, ...] = ()
    """One payload per page for a one-page-per-call reader (vLLM); empty otherwise."""
    repeat_keys: tuple[str, ...] = ()
    """Cache keys of repeats 1..N-1 (``--repeat N``)."""
    repeat_validations: list[VisionValidation | None] = field(default_factory=list)
    """The checked reading of each repeat 1..N-1 (``None``: no usable answer)."""
    latencies_ms: list[int] = field(default_factory=list)
    """Recorded latency of every answer read for this target (all repeats)."""
    answer_codes: list[str] = field(default_factory=list)
    """Outcome of every answer read for this target (all repeats): ``read`` or a code."""
    parse_errors: list[str] = field(default_factory=list)
    """Schema rules a failed page merge broke (``location: type``, no values)."""


def _sha256(data: bytes | str) -> str:
    return hashlib.sha256(data.encode("utf-8") if isinstance(data, str) else data).hexdigest()


def code_fingerprint(root: Path, tool_versions: Mapping[str, str]) -> str:
    digest = hashlib.sha256()
    for relative in _VISION_CODE:
        digest.update(relative.encode("utf-8"))
        digest.update((root / relative).read_bytes())
    digest.update(json.dumps(dict(tool_versions), sort_keys=True).encode("utf-8"))
    digest.update(f"{VISION_RENDER.version}|{IMAGE_MASK_VERSION}".encode())
    return digest.hexdigest()


def document_pages(document: Document, page_count: int) -> tuple[int, ...]:
    own = split_pages(document)
    return tuple(own) if own else tuple(range(1, page_count + 1))


def _rows(pages: Sequence[RenderedPage]) -> list[PageRow]:
    return [
        PageRow(
            page.page,
            page.sha256,
            page.mime_type,
            page.width,
            page.height,
            page.render,
            page.mask,
            page.data,
        )
        for page in pages
    ]


def load_or_render(
    target: VisionTarget,
    cache: Path,
    fingerprint: str,
    languages: tuple[str, ...],
    *,
    refresh: bool,
) -> list[PageRow] | None:
    """Masked page images from the page cache, rendered by the app's sandbox on a miss."""

    key = _sha256(
        json.dumps(
            {
                "file": _sha256(target.content),
                "pages": list(target.pages),
                "code": fingerprint,
                "languages": list(languages),
            },
            sort_keys=True,
        )
    )
    meta_path = cache / f"{key}.json"
    if not refresh and meta_path.is_file():
        try:
            stored = json.loads(meta_path.read_text(encoding="utf-8"))
            rows = []
            for item in stored["pages"]:
                data = (cache / f"{item['sha256']}.jpg").read_bytes()
                if _sha256(data) != item["sha256"]:
                    raise ValueError("page image changed on disk")
                rows.append(
                    PageRow(
                        int(item["page"]),
                        item["sha256"],
                        item["mime_type"],
                        int(item["width"]),
                        int(item["height"]),
                        item["render"],
                        item["mask"],
                        data,
                    )
                )
            return rows
        except (OSError, ValueError, KeyError):
            pass
    rendered = vision_pages_isolated(target.content, target.pages, languages=languages)
    if rendered is None:
        return None
    cache.mkdir(parents=True, exist_ok=True)
    for page in rendered:
        (cache / f"{page.sha256}.jpg").write_bytes(page.data)
    meta_path.write_text(
        json.dumps(
            {
                "pages": [
                    {
                        "page": page.page,
                        "sha256": page.sha256,
                        "mime_type": page.mime_type,
                        "width": page.width,
                        "height": page.height,
                        "render": dict(page.render),
                        "mask": dict(page.mask),
                    }
                    for page in rendered
                ]
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return _rows(rendered)


def request_key(
    target: VisionTarget, model: str, arm: VisionArm = DEFAULT_ARM
) -> tuple[PromptPayload, str, str] | None:
    """The app's prompt, its v2 ``ai_cache`` key and the legacy v1 key for these pages."""

    reader = arm.reader()
    images = provider_images(target.rows, reader.media_resolution)
    if not images:
        return None
    doc_type = target.doc_type.value
    payload = vision_prompt(
        doc_type,  # type: ignore[arg-type]
        images,
        variant=reader.prompt_variant,
        dialect=reader.schema_dialect,
    )
    key = request_cache_key(
        {
            "hook": HOOK,
            "provider": reader.provider,
            "model": model,
            # The version of the variant actually sent: for an app reader this is
            # prompt_version(HOOK, reader=reader.name); --vision-prompt-variant overrides it.
            "prompt_version": VISION_PROMPT_VARIANTS[reader.prompt_variant],
            # v4 for every earlier variant (their keys are unchanged); v5 for WI-Q2.
            "schema_version": vision_schema_version(reader.prompt_variant),
            "redaction_version": IMAGE_MASK_VERSION,
            "reader_params": reader_params(reader),
        },
        _digest(payload),
    )
    # The v1 key names the v3 payload that the recorded D41 answers were read from.
    legacy = cache_key(
        hook=HOOK,
        model=model,
        prompt_version=LEGACY_VISION_PROMPT_VERSION,
        schema_version=LEGACY_VISION_SCHEMA_VERSION,
        redaction_version=IMAGE_MASK_VERSION,
        payload_digest=_digest(legacy_vision_prompt_v3(doc_type, images)),  # type: ignore[arg-type]
    )
    return payload, key, legacy


def _digest(payload: PromptPayload) -> str:
    return payload_digest(
        system_instruction=payload.system_instruction,
        input_text=payload.input_text,
        response_schema=payload.response_schema,
        images=payload.image_digest_parts(),
    )


def compat_v1_applies(arm: VisionArm = DEFAULT_ARM) -> bool:
    """v1 answers stand for v2 ones only for Gemini with the pre-v2 reader parameters.

    Model, prompt and schema versions are part of the v1 key itself, so a v1 entry
    is found only when those match too. Repeats other than 0 never had a v1 key.
    """

    reader = arm.reader()
    return (
        reader is not None
        and reader.provider == "gemini"
        and reader_params(reader) == dict(V1_READER_PARAMS)
    )


def answer_path(
    target: VisionTarget,
    cache: Path,
    compat_v1: Path | None = None,
    arm: VisionArm = DEFAULT_ARM,
) -> Path | None:
    """Where the recorded answer for ``target`` is: its v2 entry, else (compat) its v1 entry."""

    if target.key is None:
        return None
    path = _answer_path(cache, target.key)
    if path.is_file():
        return path
    if compat_v1 is not None and target.legacy_key is not None and compat_v1_applies(arm):
        legacy = _answer_path(compat_v1, target.legacy_key)
        if legacy.is_file():
            return legacy
    return None


def _answer_path(cache: Path, key: str) -> Path:
    return cache / f"{key}.json"


def send(
    targets: Sequence[VisionTarget],
    cache: Path,
    *,
    model: str,
    api_key: str,
    complete: Callable[[ProviderRequest, str], Any] | None = None,
    max_output_tokens: int | None = None,
    failures: list[dict[str, Any]] | None = None,
    pause_seconds: float = 0.0,
    sleep: Callable[[float], None] = time.sleep,
    arm: VisionArm = DEFAULT_ARM,
    context_window: Callable[[str], int | None] | None = None,
    vllm_transport: Any = None,
    retry: frozenset[str] = frozenset(),
) -> dict[str, int]:
    """Owner-run only: send the missing requests and record the raw answers.

    Each failure is appended to ``failures`` with its code and, from
    :mod:`scripts.vision_diagnostics`, Google's error text or the answer's status,
    tokens and row counts (never a document value). Failed answers are not
    recorded, so the next run sends them again. The run stops early when the
    rest would fail anyway (daily quota, bad key, a rejected request shape, or
    the provider down several times in a row), so a small free quota is not
    spent on calls that cannot succeed; the counts say ``stopped:<reason>``.

    Every call goes to the arm's one provider. For ``vllm`` no Gemini client is
    built: the app's OpenAI-compatible adapter is bound to the allowlisted
    ``arm.base_url`` only, each page is its own call (the answer is merged as the
    AI worker merges it) and the answer budget comes from the server's context
    window (``CONTEXT_TOO_SMALL`` skips the request, as in the app). Repeats
    1..N-1 are separate requests under their own keys.
    """

    last: dict[str, Any] = {}
    if complete is None and arm.local:
        if arm.base_url is None:
            raise ArmRefused("--vision-provider vllm sends only with --vision-base-url")
        from reconbench_app.ai.worker.openai_compat import OpenAICompatProvider

        # Bound to the one checked URL; the adapter re-checks it before every request.
        server = OpenAICompatProvider(
            arm.base_url, frozenset({arm.base_url}), inner_transport=vllm_transport
        )

        def complete(request: ProviderRequest, key: str) -> Any:
            return server.complete(request, api_key=key)

        if context_window is None:
            context_window = server.context_window
    elif complete is None:
        from reconbench_app.ai.worker.gemini import GeminiProvider

        from scripts.vision_diagnostics import DiagnosticTransport

        def complete(request: ProviderRequest, key: str) -> Any:
            transport = DiagnosticTransport()
            try:
                return GeminiProvider(inner_transport=transport).complete(request, api_key=key)
            finally:
                last.clear()
                last.update(transport.info)

    limits = AiLimits()
    reader = arm.reader()
    kind = limits.for_kind(HOOK, local=arm.local)
    cap = max_output_tokens or kind.max_output_tokens
    timeout_ms = limits.timeout_for(HOOK, local=arm.local) * 1_000
    # The app sends the reader parameters as provider options (Gemini ignores them).
    options = reader_params(reader)
    thinking = None if arm.local else reader.thinking
    jobs = [
        (target, index, key)
        for target in targets
        for index, key in enumerate((target.key, *target.repeat_keys))
        if index >= arm.send_from_repeat
    ]
    counts: Counter[str] = Counter()
    resent: set[str] = set()
    cache.mkdir(parents=True, exist_ok=True)
    streak: tuple[str, int] = ("", 0)
    wait = 0.0
    for number, (target, index, key) in enumerate(jobs):
        assert target.payload is not None and key is not None
        if _answer_path(cache, key).is_file() and (key not in retry or key in resent):
            continue  # a copy with the same request key was answered in this run
        calls = target.parts or (target.payload,)
        call_cap = cap
        window = context_window(model) if context_window is not None else None
        if arm.local and isinstance(window, int):
            budget = min(
                local_output_budget(
                    context_window=window,
                    prompt_bytes=_prompt_bytes(call),
                    image_tokens=len(call.images) * (kind.image_tokens_estimate or 0),
                )
                for call in calls
            )
            if budget < MIN_LOCAL_OUTPUT_TOKENS:
                counts["skipped:CONTEXT_TOO_SMALL"] += 1
                if failures is not None:
                    failures.append(
                        {
                            "code": "CONTEXT_TOO_SMALL",
                            "pages": len(target.payload.images),
                            **_who(target),
                        }
                    )
                continue
            call_cap = min(cap, budget)
        if number and max(pause_seconds, wait) > 0:
            sleep(max(pause_seconds, wait))
        wait = 0.0
        started = time.monotonic()
        last.clear()
        texts: list[str] = []
        usage: ProviderUsage | None = None
        try:
            for call in calls:
                result = complete(
                    ProviderRequest(
                        model=model,
                        system_instruction=call.system_instruction,
                        input_text=call.input_text,
                        response_schema=call.response_schema,
                        max_output_tokens=call_cap,
                        timeout_ms=timeout_ms,
                        thinking_level=thinking,  # type: ignore[arg-type]
                        images=call.images,
                        options=options,
                    ),
                    api_key,
                )
                texts.append(result.output_text)
                usage = _add_usage(usage, result.usage)
        except ProviderFailure as failure:
            failure.usage = _add_usage(usage, failure.usage)
            counts[f"failed:{failure.code}"] += 1
            if failures is not None:
                entry = failure_entry(failure, last, len(target.payload.images), call_cap)
                failures.append({**entry, **_who(target)})
            streak = (failure.code, streak[1] + 1 if streak[0] == failure.code else 1)
            stop, wait = _after_failure(failure.code, str(last.get("error_message", "")), streak[1])
            if stop is not None:
                counts[f"stopped:{stop}"] = len(jobs) - number - 1
                break
            continue
        streak = ("", 0)
        merged, diagnosis = _merged(texts, target) if target.parts else ("", {})
        record: dict[str, Any] = {
            "model": model,
            "output_text": merged if target.parts else texts[0],
            "usage": None
            if usage is None
            else {
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "thought_tokens": usage.thought_tokens,
            },
            "latency_ms": int((time.monotonic() - started) * 1_000),
            "pages": len(target.payload.images),
            "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        if arm != DEFAULT_ARM:
            record.update(provider=arm.provider, calls=len(calls), repeat=index)
        record.update(diagnosis)
        _answer_path(cache, key).write_text(json.dumps(record, sort_keys=True), encoding="utf-8")
        resent.add(key)
        counts["sent"] += 1
    return dict(counts)


def _who(target: VisionTarget) -> dict[str, str | None]:
    """Which bench document a failed request was for: truth-file IDs only, no values."""

    return {"document": f"{target.file_id}#{target.index}", "truth_id": target.truth_id}


def _prompt_bytes(payload: PromptPayload) -> int:
    """Prompt text a self-hosted model reads (as ``reconbench_app.ai.worker.runner``)."""

    text = payload.system_instruction + payload.input_text
    text += "".join(image.caption for image in payload.images)
    return len(text.encode("utf-8"))


def _add_usage(total: ProviderUsage | None, usage: ProviderUsage | None) -> ProviderUsage | None:
    if usage is None:
        return total
    if total is None:
        return usage
    thought = (
        None
        if total.thought_tokens is None and usage.thought_tokens is None
        else (total.thought_tokens or 0) + (usage.thought_tokens or 0)
    )
    return ProviderUsage(
        total.input_tokens + usage.input_tokens, total.output_tokens + usage.output_tokens, thought
    )


def _merged(texts: Sequence[str], target: VisionTarget) -> tuple[str, dict[str, Any]]:
    """Per-page answers as one document answer, plus what to record when that fails.

    An unparsable page leaves the answer empty (the check reports ``invalid_output``
    like any bad answer). The raw page answers are then kept in the record
    (``page_outputs``, in ``work/`` only) with ``merge_error``: per page, the schema
    rule that failed as a location pattern and error type, never a value.
    """

    pages = [row.page for row in target.rows]
    try:
        return merge_page_answers(texts, pages=pages), {}
    except InvalidOutput:
        problems = []
        for page, text in zip(pages, texts, strict=False):
            errors = parse_errors(text)
            if errors:
                problems.append({"page": page, "errors": errors})
        return "", {"page_outputs": list(texts), "merge_error": {"pages": problems}}


def unusable_keys(targets: Iterable[VisionTarget], cache: Path) -> frozenset[str]:
    """Request keys whose recorded answer fails the strict parse (``--vision-retry-invalid``)."""

    keys: set[str] = set()
    for target in targets:
        for key in (target.key, *target.repeat_keys):
            path = _answer_path(cache, key) if key is not None else None
            if path is None or not path.is_file():
                continue
            stored = json.loads(path.read_text(encoding="utf-8"))
            if parse_errors(stored_answer(stored, target)):
                keys.add(str(key))
    return frozenset(keys)


def parse_errors(text: str) -> list[str]:
    """Why one answer fails the strict parse: ``location: type`` patterns, no values.

    List positions become ``*`` so the same rule broken on many items counts once
    per pattern (``lines.*.hsn_sac: string_too_long``); ``[]`` when it parses.
    """

    try:
        parse_output(HOOK, text)
    except InvalidOutput as error:
        cause = error.__cause__
        if isinstance(cause, ValidationError):
            return sorted(set(error_patterns(cause)))
        return [error.reason]
    return []


def stored_answer(stored: Mapping[str, Any], target: VisionTarget) -> str:
    """The recorded answer to check (D73): its ``output_text``, or, when a failed merge
    left that empty and kept ``page_outputs``, those page answers merged again offline
    (no request is sent). A merge that still fails gives ``""`` (``invalid_output``).
    """

    text = str(stored.get("output_text", ""))
    outputs = stored.get("page_outputs")
    if text or not isinstance(outputs, list):
        return text
    try:
        return merge_page_answers(
            [str(item) for item in outputs], pages=[row.page for row in target.rows]
        )
    except InvalidOutput:
        return ""


_FATAL_CODES = frozenset(
    {
        "bad_key",
        "PERMISSION_DENIED",
        "PREPAY_DEPLETED",
        "BILLING_OR_PREREQUISITE",
        "QUOTA_EXHAUSTED",
    }
)


def _after_failure(code: str, message: str, streak: int) -> tuple[str | None, float]:
    """(stop reason or ``None``, seconds to wait before the next call) after one failure."""

    lowered = message.lower()
    if code in _FATAL_CODES:
        return code, 0.0
    if code == "rate_limited":
        if "per day" in lowered or streak >= 3:
            return "daily_quota" if "per day" in lowered else "rate_limited", 0.0
        return None, 60.0
    if code == "INVALID_REQUEST":
        if "recitation" in lowered:
            return None, 0.0  # this document's output was filtered; the next may pass
        return ("request_rejected" if streak >= 2 else None), 0.0
    if code in {"PROVIDER_UNAVAILABLE", "timeout"}:
        return ("provider_down" if streak >= 5 else None), 30.0
    return None, 0.0


_SAFE_DETAILS = frozenset(
    {
        "at_output_cap",
        "not_json",
        "one page per call",
        "thinking_leak",
        "timeout bound",
        "unknown sampling",
    }
)
"""Adapter-made failure details (fixed strings); server error text is never copied."""


def failure_entry(
    failure: ProviderFailure, info: Mapping[str, Any], pages: int, cap: int
) -> dict[str, Any]:
    """One failed call: code, pages, and the counts-only diagnostics (no document values)."""

    from scripts.vision_diagnostics import at_output_cap, looks_like_a_loop

    entry: dict[str, Any] = {"code": failure.code, "pages": pages, **info}
    if failure.detail in _SAFE_DETAILS:
        entry.setdefault("detail", failure.detail)
    usage = failure.usage
    if usage is not None:
        entry.setdefault("output_tokens", usage.output_tokens)
        entry.setdefault("thought_tokens", usage.thought_tokens)
        entry.setdefault("input_tokens", usage.input_tokens)
    if entry.get("status") not in {None, "completed"} or failure.code == "invalid_output":
        entry["max_output_tokens"] = cap
        entry["at_output_cap"] = at_output_cap(entry, cap)
        entry["loop_suspected"] = looks_like_a_loop(entry)
    return entry


def _fold(value: str) -> str:
    # Letters and digits only, zeros kept: a handwritten "04-077" must still be found
    # inside "PO/26-27/04-077" (the document-number canonical form drops the zeros).
    return re.sub(r"[^0-9A-Z]", "", value.upper()).replace("O", "0").replace("I", "1")


_MIN_PARTIAL_REF = 5
"""A partial reading counts only when the shorter side has at least this many characters."""


def _ref_hit(expected: Iterable[str], found: Iterable[str]) -> tuple[int, int]:
    wanted = [_fold(str(item)) for item in expected if str(item).strip()]
    seen = [_fold(str(item)) for item in found if str(item).strip()]

    def hit(item: str, other: str) -> bool:
        if not item or not other:
            return False
        if item == other:
            return True
        short = min(item, other, key=len)
        return len(short) >= _MIN_PARTIAL_REF and (item in other or other in item)

    hits = sum(any(hit(item, other) for other in seen) for item in wanted)
    return hits, len(wanted)


@dataclass
class VisionRun:
    mode: str
    model: str
    targets: list[VisionTarget]
    usage: Counter[str] = field(default_factory=Counter)
    provider: dict[str, int] = field(default_factory=dict)
    failures: list[dict[str, Any]] = field(default_factory=list)
    arm: VisionArm = DEFAULT_ARM
    repeat_usage: Counter[str] = field(default_factory=Counter)
    """Tokens of repeats 1..N-1 (the vision block counts repeat 0 only)."""


def prepare(
    targets: list[VisionTarget],
    *,
    root: Path,
    pages_cache: Path,
    tool_versions: Mapping[str, str],
    languages: tuple[str, ...],
    refresh: bool,
    model: str,
    workers: int,
    arm: VisionArm = DEFAULT_ARM,
) -> None:
    fingerprint = code_fingerprint(root, tool_versions)
    reader = arm.reader()

    def render(target: VisionTarget) -> None:
        if len(target.pages) > MAX_PAGES:
            target.outcome = "too_many_pages"
            return
        rows = load_or_render(target, pages_cache, fingerprint, languages, refresh=refresh)
        if rows is None:
            target.outcome = "render_failed"
            return
        if arm.strategy == "band":
            # WI-Q2: one more read of the top band of page 1 only (handwritten references).
            band = header_band(rows[0]) if rows else None
            if band is None:
                target.outcome = "render_failed"
                return
            rows = [band]
        target.rows = rows
        keyed = request_key(target, model, arm)
        # A one-page-per-call reader (vLLM) sends each page alone, as the AI worker does;
        # the key stays the whole document's request key.
        parts = page_prompts(target.doc_type.value, rows, reader)  # type: ignore[arg-type]
        if keyed is None or parts is None:
            target.outcome = "render_failed"
            return
        target.payload, target.key, target.legacy_key = keyed
        target.parts = parts
        target.repeat_keys = tuple(repeat_key(target.key, index) for index in range(1, arm.repeats))
        target.outcome = "missing"

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        list(pool.map(render, targets))


def _pass1_key(target: VisionTarget) -> str | None:
    """Pass 1 of the cropped read is the newest repeat (a same-day full-page read)."""

    return target.repeat_keys[-1] if target.repeat_keys else target.key


def _page_answer(answer: Mapping[str, Any], page: int) -> dict[str, Any]:
    kept: dict[str, Any] = {"doc_kind": answer.get("doc_kind", "other")}
    for name in ("pages", "fields", "lines", "annotations", "unreadable"):
        kept[name] = [
            item
            for item in answer.get(name) or []
            if isinstance(item, Mapping) and item.get("page") == page
        ]
    return kept


def _rules_rows(document: Document, page: int) -> int:
    rows = 0
    for line in document.lines:
        evidence = [
            getattr(getattr(line, name, None), "evidence", None)
            for name in ("amount", "description", "quantity")
        ]
        if any(item is not None and item.page == page for item in evidence):
            rows += 1
    return rows


def _crop_key(
    model: str, reader: VisionReaderInfo, base: str, page: int, payload: PromptPayload
) -> str:
    identity = {
        "crop": CROP_VERSION,
        "model": model,
        "pass1": base,
        "page": page,
        "prompt_version": VISION_PROMPT_VARIANTS[reader.prompt_variant],
        "reader_params": reader_params(reader),
        "digest": _digest(payload),
    }
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


def crop_pass(
    targets: Sequence[VisionTarget],
    cache: Path,
    *,
    arm: VisionArm,
    model: str,
    send_crops: bool,
    max_calls: int,
    failures: list[dict[str, Any]] | None = None,
    api_key: str = "",
) -> dict[str, Any]:
    """WI-BE8 pass 2 (D97): crops of the pages pass 1 lost or under-read; merged answers.

    Pass 1 is the target's newest repeat (``--repeat N --send-from-repeat N-1``: a
    same-day full-page read). ``crops-control`` scores that pass alone; ``crops`` sends
    three crops of each flagged page (only with ``send_crops`` and within
    ``max_calls``), merges them (:mod:`reconbench_app.ai.vision_crops`) and writes the
    merged answer under its own key, which the check then reads as the reading.
    """

    reader = arm.reader()
    counts: Counter[str] = Counter()
    jobs: list[VisionTarget] = []
    plans: list[
        tuple[
            VisionTarget, str, dict[str, Any] | None, dict[int, list[tuple[str, tuple[int, int]]]]
        ]
    ] = []
    for target in targets:
        base = _pass1_key(target)
        if base is None or not target.rows:
            continue
        if arm.strategy == "crops-control":
            target.key, target.repeat_keys = base, ()
            continue
        path = _answer_path(cache, base)
        answer: dict[str, Any] | None = None
        if path.is_file():
            text = stored_answer(json.loads(path.read_text(encoding="utf-8")), target)
            try:
                parse_output(HOOK, text)
                answer = json.loads(text)
            except (InvalidOutput, ValueError):
                answer = None
        capped = answer is None
        open_totals = totals_open(answer)
        flagged: dict[int, list[tuple[str, tuple[int, int]]]] = {}
        for row in target.rows:
            reasons = crop_reasons(
                _page_answer(answer, row.page) if answer is not None else None,
                page=row.page,
                rules_rows=_rules_rows(target.document, row.page),
                totals_open=open_totals,
                capped=capped,
            )
            if not reasons:
                continue
            counts.update(f"page_reason:{reason}" for reason in reasons)
            counts["pages_flagged"] += 1
            for top, bottom in CROP_BANDS:
                crop = crop_band(row, top, bottom)
                if crop is None:
                    counts["crop_render_failed"] += 1
                    continue
                prompts = page_prompts(target.doc_type.value, [crop], reader)
                if not prompts:
                    continue
                payload = prompts[0]
                key = _crop_key(model, reader, base, row.page, payload)
                flagged.setdefault(row.page, []).append((key, (top, bottom)))
                if not _answer_path(cache, key).is_file():
                    jobs.append(
                        replace(
                            target,
                            rows=[crop],
                            payload=payload,
                            parts=(payload,),
                            key=key,
                            repeat_keys=(),
                            legacy_key=None,
                        )
                    )
        plans.append((target, base, answer, flagged))
    counts["crop_calls_needed"] = len(jobs)
    if jobs and send_crops:
        if len(jobs) > max_calls:
            counts["crops_not_sent_over_budget"] = len(jobs)
        else:
            sent = send(
                jobs,
                cache,
                model=model,
                api_key=api_key,
                failures=failures,
                arm=replace(arm, repeats=1, send_from_repeat=0),
            )
            counts.update({f"crop_{name}": value for name, value in sent.items()})
    for target, base, answer, flagged in plans:
        if not flagged:
            target.key, target.repeat_keys = base, ()
            continue
        pages: list[int] = []
        texts: list[str] = []
        merged_stats: Counter[str] = Counter()
        for row in target.rows:
            page_answer = _page_answer(answer, row.page) if answer is not None else None
            crops: list[tuple[Mapping[str, Any], tuple[int, int]]] = []
            for key, band in flagged.get(row.page, []):
                path = _answer_path(cache, key)
                if not path.is_file():
                    merged_stats["crop_missing"] += 1
                    continue
                text = str(json.loads(path.read_text(encoding="utf-8")).get("output_text", ""))
                try:
                    parse_output(HOOK, text)
                    crops.append((json.loads(text), band))
                except (InvalidOutput, ValueError):
                    merged_stats["crop_unusable"] += 1
            if page_answer is None and not crops:
                pages, texts = [], []
                break
            merged, stats = merge_page_crops(
                page_answer, crops, page=row.page, box_order=reader.box_order
            )
            merged_stats.update(stats)
            pages.append(row.page)
            texts.append(json.dumps(merged, ensure_ascii=False))
        counts.update(merged_stats)
        if not texts:
            counts["documents_still_lost"] += 1
            target.key, target.repeat_keys = base, ()
            continue
        try:
            output = merge_page_answers(texts, pages=pages)
        except InvalidOutput:
            counts["documents_merge_failed"] += 1
            target.key, target.repeat_keys = base, ()
            continue
        merged_key = hashlib.sha256(
            json.dumps(
                {
                    "crops": CROP_VERSION,
                    "pass1": base,
                    "parts": [k for v in flagged.values() for k, _ in v],
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        record = {
            "model": model,
            "output_text": output,
            "pages": len(pages),
            "crops": {"version": CROP_VERSION, "pass1_key": base, "pages": sorted(flagged)},
            "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        _answer_path(cache, merged_key).write_text(
            json.dumps(record, sort_keys=True), encoding="utf-8"
        )
        target.key, target.repeat_keys = merged_key, ()
        counts["documents_merged"] += 1
    return dict(sorted(counts.items()))


def check_answers(
    targets: Sequence[VisionTarget],
    answers: Path,
    buyer_entities: Sequence[BuyerEntityHint],
    usage: Counter[str],
    *,
    compat_v1: Path | None = None,
    arm: VisionArm = DEFAULT_ARM,
    repeat_usage: Counter[str] | None = None,
) -> None:
    """Check every recorded answer: repeat 0 sets the target's reading, the rest its repeats.

    ``usage`` sums repeat 0 (the vision block), ``repeat_usage`` repeats 1..N-1.
    """

    box_order = arm.reader().box_order
    for target in targets:
        path = answer_path(target, answers, compat_v1, arm)
        if path is not None:
            stored = json.loads(path.read_text(encoding="utf-8"))
            for name, value in (stored.get("usage") or {}).items():
                if isinstance(value, int):
                    usage[name] += value
            recorded = stored.get("usage") or {}
            if isinstance(recorded.get("output_tokens"), int):
                target.output_tokens = recorded["output_tokens"] + int(
                    recorded.get("thought_tokens") or 0
                )
            target.validation, target.outcome = _checked(stored, target, buyer_entities, box_order)
            _note(target, stored)
        target.repeat_validations = []
        for key in target.repeat_keys:
            repeat_path = _answer_path(answers, key)
            if not repeat_path.is_file():
                target.repeat_validations.append(None)
                continue
            stored = json.loads(repeat_path.read_text(encoding="utf-8"))
            if repeat_usage is not None:
                for name, value in (stored.get("usage") or {}).items():
                    if isinstance(value, int):
                        repeat_usage[name] += value
            validation, outcome = _checked(stored, target, buyer_entities, box_order)
            target.repeat_validations.append(validation)
            _note(target, stored, outcome)


def _checked(
    stored: Mapping[str, Any],
    target: VisionTarget,
    buyer_entities: Sequence[BuyerEntityHint],
    box_order: Any,
) -> tuple[VisionValidation | None, str]:
    try:
        parsed = parse_output(HOOK, stored_answer(stored, target))
        assert isinstance(parsed, VisionOutput)
        validation = validate_vision(
            parsed,
            VisionContext(
                doc_type=target.doc_type.value,  # type: ignore[arg-type]
                pages={row.page: row.sent_page for row in target.rows},
                buyer_entities=tuple(buyer_entities),
                ocr=target.document,
                box_order=box_order,
            ),
        )
    except InvalidOutput as error:
        return None, error.code
    return validation, "read"


def _note(target: VisionTarget, stored: Mapping[str, Any], outcome: str | None = None) -> None:
    latency = stored.get("latency_ms")
    if isinstance(latency, int) and not isinstance(latency, bool):
        target.latencies_ms.append(latency)
    code = outcome or target.outcome
    target.answer_codes.append(code)
    if code != "invalid_output":
        return  # a kept failed merge that now merges again offline (D73)
    for page in (stored.get("merge_error") or {}).get("pages", []):
        target.parse_errors.extend(str(item) for item in page.get("errors", []))


def summary(
    run: VisionRun,
    truth_by_id: Mapping[str, Any],
    score_document: Callable[..., Any],
    extraction_summary: Callable[[list[Any]], dict[str, Any]],
    normal: Callable[[str, Any], Any],
    same: Callable[[str, Any, Any], bool],
    header_fields: Sequence[str],
) -> dict[str, Any]:
    """Counts only: what was read, vision alone per field, and OCR × vision agreement."""

    outcomes = Counter(target.outcome for target in run.targets)
    masked: Counter[str] = Counter()
    pages = 0
    for target in run.targets:
        for row in target.rows:
            pages += 1
            masked.update(row.mask.get("counts", {}))
    scores = []
    agreement: dict[str, Counter[str]] = defaultdict(Counter)
    dropped: Counter[str] = Counter()
    annotation = [0, 0]
    marketplace = [0, 0]
    handwritten: Counter[str] = Counter()
    kinds = Counter()
    line_cells: Counter[str] = Counter()
    for target in run.targets:
        if target.validation is not None:
            line_cells.update(_line_cells(target.validation.check.lines))
    for target in run.targets:
        if target.validation is None or target.truth_id is None:
            continue
        truth = truth_by_id[target.truth_id]
        vision_doc = target.validation.document
        dropped.update(target.validation.check.dropped)
        scores.append(score_document(truth, target.file_id, vision_doc))
        for name in header_fields:
            if name not in truth.header:
                continue
            expected = normal(name, truth.header[name])
            ocr = normal(name, getattr(target.document.header, name, None))
            seen = normal(name, getattr(vision_doc.header, name, None))
            if ocr is None and seen is None:
                outcome = "both_missing"
            elif ocr is None:
                outcome = "vision_only_" + (
                    "correct" if expected is not None and same(name, expected, seen) else "wrong"
                )
            elif seen is None:
                outcome = "ocr_only_" + (
                    "correct" if expected is not None and same(name, expected, ocr) else "wrong"
                )
            elif same(name, ocr, seen):
                right = expected is not None and same(name, expected, ocr)
                outcome = "agree_correct" if right else "agree_wrong"
            else:
                ocr_right = expected is not None and same(name, expected, ocr)
                vision_right = expected is not None and same(name, expected, seen)
                outcome = (
                    "disagree_ocr_right"
                    if ocr_right
                    else "disagree_vision_right"
                    if vision_right
                    else "disagree_both_wrong"
                )
            agreement[name][outcome] += 1
        extras = vision_doc.header.extras
        expected_refs = _truth_refs(truth, "annotation_refs")
        if expected_refs:
            hits, total = _ref_hit(expected_refs, extras.get("annotation_refs", []))
            annotation[0] += hits
            annotation[1] += total
            handwritten.update(_handwritten_outcomes(expected_refs, vision_doc))
        expected_orders = _truth_refs(truth, "marketplace_order_no")
        if expected_orders:
            found = extras.get("marketplace_order_no")
            hits, total = _ref_hit(expected_orders, [found] if found else [])
            marketplace[0] += hits
            marketplace[1] += total
        for item in extras.get("vision", {}).get("page_kinds", []):
            doc_type = _PAGE_KIND_TYPES.get(str(item.get("kind")))
            if doc_type is not None:
                kinds["correct" if doc_type == truth.doc_type.value else "wrong"] += 1
    totals: Counter[str] = Counter()
    for counts in agreement.values():
        totals.update(counts)
    return {
        "mode": run.mode,
        "model": run.model,
        "pdf_documents": len(run.targets),
        "pages": pages,
        "outcomes": dict(sorted(outcomes.items())),
        "masked_regions": dict(sorted(masked.items())),
        "usage": dict(sorted(run.usage.items())),
        "provider": dict(sorted(run.provider.items())),
        "alone": extraction_summary(scores) if scores else None,
        "agreement": {
            "all": dict(sorted(totals.items())),
            **{name: dict(sorted(counts.items())) for name, counts in sorted(agreement.items())},
        },
        "annotation_refs_found": f"{annotation[0]}/{annotation[1]}",
        "handwritten_refs": dict(sorted(handwritten.items())),
        "marketplace_order_found": f"{marketplace[0]}/{marketplace[1]}",
        "page_kinds": dict(sorted(kinds.items())),
        "line_cells": dict(sorted(line_cells.items())),
        "dropped": dict(sorted(dropped.items())),
        "output_tokens_per_call": _output_spread(run.targets),
        "failures": _failure_summary(run.failures),
    }


def _output_spread(targets: Sequence[VisionTarget]) -> dict[str, int]:
    """How close recorded answers came to the output cap (output + thought tokens)."""

    used = [target.output_tokens for target in targets if target.output_tokens is not None]
    if not used:
        return {}
    return {
        "answers": len(used),
        "max": max(used),
        "over_4096": sum(value > 4_096 for value in used),
        "over_8192": sum(value > 8_192 for value in used),
        "over_16384": sum(value > 16_384 for value in used),
    }


def _truth_refs(truth: Any, name: str) -> list[str]:
    """Handwritten refs / marketplace orders sit beside the header in truth v2."""

    value = getattr(truth, name, None) or truth.header.get(name)
    if not value:
        return []
    items = value if isinstance(value, (list, tuple)) else [value]
    return [str(item) for item in items if str(item).strip()]


def _handwritten_outcomes(expected: Sequence[str], document: Document) -> Counter[str]:
    """Where each handwritten truth ref turned up in the vision reading (counts only).

    ``typed``: an annotation the model called a PO/GRN/invoice number (what the
    review shows as a reference); ``annotation_other``: an annotation of another
    kind; ``printed_field``: only in a printed reference field (rule 7 broken);
    ``missed``: nowhere.
    """

    extras = document.header.extras
    typed = [str(item) for item in extras.get("annotation_refs", [])]
    notes = [str(item.get("text", "")) for item in extras.get("vision", {}).get("annotations", [])]
    printed: list[str] = []
    for name in ("po_refs", "grn_refs"):
        item = getattr(document.header, name, None)
        if item is not None and item.value:
            printed.extend(str(value) for value in item.value)
    for values in extras.get("vision", {}).get("other_fields", {}).values():
        printed.extend(str(value) for value in values)
    outcomes: Counter[str] = Counter()
    for ref in expected:
        if _ref_hit([ref], typed)[0]:
            outcomes["typed"] += 1
        elif _ref_hit([ref], notes)[0]:
            outcomes["annotation_other"] += 1
        elif _ref_hit([ref], printed)[0]:
            outcomes["printed_field"] += 1
        else:
            outcomes["missed"] += 1
    outcomes["expected"] += len(expected)
    return outcomes


def _line_cells(lines: Iterable[Any]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for line in lines:
        counts["rows"] += 1
        names = {cell.name for cell in line.cells}
        counts["with_description"] += "description" in names
        counts["with_amount"] += "amount" in names
        counts["with_quantity"] += bool(names & {"quantity", "received_qty", "accepted_qty"})
        counts["with_rate"] += "rate" in names
    return counts


def _failure_summary(failures: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Failed calls grouped by what went wrong; counts and Google's error text only."""

    grouped: Counter[str] = Counter()
    for entry in failures:
        grouped[json.dumps(dict(entry), sort_keys=True, default=str)] += 1
    return [{**json.loads(key), "calls": count} for key, count in sorted(grouped.items())]


def provider_key() -> str:
    key = os.environ.get("BENCH_GEMINI_KEY", "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.\-]{20,256}", key):
        raise SystemExit("--vision provider needs BENCH_GEMINI_KEY (the Gemini API key)")
    return key


__all__ = [
    "CROP_STRATEGIES",
    "DEFAULT_ARM",
    "MAX_PAGES",
    "ArmRefused",
    "VisionArm",
    "VisionRun",
    "VisionTarget",
    "check_answers",
    "crop_pass",
    "check_arm",
    "document_pages",
    "prepare",
    "provider_key",
    "repeat_key",
    "stored_answer",
    "unusable_keys",
    "send",
    "summary",
]
