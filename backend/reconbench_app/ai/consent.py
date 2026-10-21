"""Consent text, per-hook data disclosure and the consent fingerprint.

The fingerprint covers everything a person agreed to: consent text version,
provider, model, mode, the hooks that mode enables, and the prompt, schema and
redaction versions. Any change requires consent again.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from types import MappingProxyType

from reconbench_app.ai.contracts import SCHEMA_VERSIONS
from reconbench_app.ai.prompts import prompt_version
from reconbench_app.ai.redaction import IMAGE_MASK_VERSION, REDACTION_VERSION
from reconbench_app.extraction.vision import (
    DEFAULT_VISION_READER,
    PROVIDER_READERS,
    VISION_RENDER,
    vision_reader,
)

CONSENT_TEXT_VERSION = "consent.v1"
AUTO_VERIFY_CONSENT_TEXT_VERSION = "consent.v2"

# Only field types with a zero-false-safe benchmark gate may be selected. Keep
# this list in sync with D42 and the database constraint in migration 0022.
AUTO_VERIFY_FIELD_TYPES = ("doc_date", "doc_no", "gstin", "line_amount", "reference")
AUTO_VERIFY_FIELD_LABELS: Mapping[str, str] = MappingProxyType(
    {
        "doc_date": "document date",
        "doc_no": "document number",
        "gstin": "GSTIN",
        "line_amount": "line amount",
        "reference": "document references",
    }
)

# ``ai_first`` has its own hook list: H1 covers every PDF, not only low-confidence ones.
# With page images on (workspace ``vision_enabled``, AC-5), the vision reader takes
# H1's place in the same mode: ``h6_vision:fallback`` / ``h6_vision:all_pdfs``.
HOOKS_BY_MODE: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "ai_assisted": ("h1_extract:fallback", "h2_item", "h3_rerank", "h4_vendor"),
        "ai_first": ("h1_extract:all_pdfs", "h2_item", "h3_rerank", "h4_vendor"),
    }
)

MODE_LABELS: Mapping[str, str] = MappingProxyType(
    {
        "rules_only": "Off: rules only",
        "ai_assisted": "AI Assist: suggestions where the rules are unsure",
        "ai_first": "AI-first: extraction suggestions for every PDF",
    }
)

HOOK_LABELS: Mapping[str, str] = MappingProxyType(
    {
        "h1_extract:fallback": "PDF extraction suggestions (unclear fields only)",
        "h1_extract:all_pdfs": "AI-first PDF extraction suggestions (every PDF)",
        "h2_item": "Item match opinion",
        "h3_rerank": "PO candidate re-rank",
        "h4_vendor": "Duplicate vendor opinion",
        "h6_vision:fallback": "Page-image reading (documents the rules could not verify)",
        "h6_vision:all_pdfs": "Page-image reading (every PDF)",
    }
)

_VISION_DATA = (
    "Images of the pages of {which} (up to the configured page limit). Before sending, the "
    "areas that OCR identifies as e-mail addresses, UPI ids, phone numbers, bank account "
    "numbers, IFSC codes, PANs and signatures are blacked out. Masking is best effort: "
    "anything OCR cannot read (handwriting, stamps, faint or unusual print) can stay visible. "
    "GSTINs, names, addresses, amounts and handwritten notes are sent because they are needed "
    "to read the document."
)

DATA_SENT: Mapping[str, str] = MappingProxyType(
    {
        "h1_extract:fallback": (
            "Text of up to 8 pages of a PDF invoice, PO or GRN whose critical fields could not "
            "be read reliably. Email addresses, phone numbers, bank account numbers, IFSC "
            "codes and PANs are replaced by placeholders first. GSTINs are sent because they "
            "are fields to read."
        ),
        "h1_extract:all_pdfs": (
            "Text of up to 8 pages of every PDF invoice, PO or GRN with readable text, with "
            "the same placeholders. GSTINs are sent because they are fields to read."
        ),
        "h2_item": (
            "Descriptions, item codes, HSN/SAC and units of invoice and PO lines the rules "
            "marked unsure (score 0.60 to 0.80). No quantities, rates, amounts, GSTINs or PANs."
        ),
        "h3_rerank": (
            "Invoice text and up to five same-vendor PO numbers, dates and line descriptions. "
            "GSTINs, PANs and banking or contact details are replaced by placeholders."
        ),
        "h4_vendor": (
            "Names and aliases of two vendor records that may be duplicates. GSTINs and PANs "
            "are never sent."
        ),
        "h6_vision:fallback": _VISION_DATA.format(
            which="a PDF invoice, PO or GRN whose fields the rules could not verify"
        ),
        "h6_vision:all_pdfs": _VISION_DATA.format(which="every PDF invoice, PO or GRN"),
    }
)

CONSENT_TEXT = (
    "When AI Assist is on, ReconBench sends the data listed for each enabled hook to "
    "{provider} ({model}) using your workspace's API key, with storage disabled on the "
    "provider side. AI output is only a suggestion or an advisory opinion: it never changes "
    "quantities, totals, links used in reconciliation or verdicts unless a person accepts it."
)

AUTO_VERIFY_CONSENT_TEXT = (
    " When enabled for {field_types}, ReconBench may mark those fields verified without a person "
    "only after two independent readers agree, all deterministic validators pass, a local crop "
    "reread agrees, and the field type has passed its benchmark gate. It never changes the value."
)


# Where masked page images go, per vision provider (LLM plan WI-BE3).
DATA_DESTINATIONS: Mapping[str, str] = MappingProxyType(
    {
        "gemini": "Google Gemini API: masked page images leave this server",
        "vllm": (
            "Company vLLM server at {base_url} (over the company VPN): masked page images "
            "leave this machine but not the company network"
        ),
    }
)

LOCAL_READER_CONSENT_TEXT = (
    " Page images are read by the company vLLM server at {base_url} ({model}) instead of "
    "{provider}: they leave this machine but not the company network."
)

SHADOW_CONSENT_TEXT = (
    " In shadow mode, page images are also sent to {destination} ({model}); its answers are "
    "stored for comparison only and are never shown as suggestions or written to documents."
)


def data_destination(provider: str, base_url: str | None = None) -> str:
    return DATA_DESTINATIONS[provider].format(base_url=base_url or "the configured URL")


def hooks_for_mode(mode: str, *, vision: bool = False) -> tuple[str, ...]:
    hooks = HOOKS_BY_MODE.get(mode, ())
    if not vision:
        return hooks
    return tuple(
        "h6_vision:" + hook.split(":", 1)[1] if hook.startswith("h1_extract:") else hook
        for hook in hooks
    )


def consent_text_version(*, auto_verify_field_types: tuple[str, ...] = ()) -> str:
    return AUTO_VERIFY_CONSENT_TEXT_VERSION if auto_verify_field_types else CONSENT_TEXT_VERSION


def consent_text(
    *,
    provider: str,
    model: str,
    auto_verify_field_types: tuple[str, ...] = (),
    reader_strategy: str | None = None,
    vision_provider: str | None = None,
    vision_model: str | None = None,
    vision_base_url: str | None = None,
) -> str:
    """Consent text; unchanged unless a vLLM reader or shadow mode is chosen."""

    text = CONSENT_TEXT.format(provider=provider, model=model)
    if reader_strategy == "single" and vision_provider == "vllm":
        text += LOCAL_READER_CONSENT_TEXT.format(
            base_url=vision_base_url or "the configured URL",
            model=vision_model or "the selected model",
            provider=provider,
        )
    elif reader_strategy == "shadow" and vision_provider is not None:
        destination = (
            f"the company vLLM server at {vision_base_url or 'the configured URL'}"
            if vision_provider == "vllm"
            else "Google Gemini"
        )
        text += SHADOW_CONSENT_TEXT.format(
            destination=destination, model=vision_model or "the selected model"
        )
    if auto_verify_field_types:
        labels = ", ".join(
            AUTO_VERIFY_FIELD_LABELS[item]
            for item in AUTO_VERIFY_FIELD_TYPES
            if item in auto_verify_field_types
        )
        text += AUTO_VERIFY_CONSENT_TEXT.format(field_types=labels)
    return text


def consent_fingerprint(
    *,
    provider: str,
    model: str,
    mode: str,
    vision: bool = False,
    auto_verify_field_types: tuple[str, ...] = (),
    vision_reader_name: str = DEFAULT_VISION_READER,
    reader_strategy: str | None = None,
    vision_provider: str | None = None,
    vision_model: str | None = None,
    vision_base_url: str | None = None,
    apply_mode: str = "suggest",
) -> str:
    """Hash of consent; the legacy hash stays identical when both new options are off.

    With page images on, the reader's prompt version and prompt variant are covered,
    so a new vision prompt (or another reader) needs consent again.

    Reader strategy, vision provider and apply mode (WI-BE3) are added only when
    they differ from the legacy mapping (``off``, or ``single`` with Gemini, and
    ``suggest``), so every migrated workspace keeps its fingerprint byte for byte.
    ``single`` with a vLLM reader covers that reader's prompt, model and base URL;
    ``shadow`` covers the shadow reader but leaves the hooks as with images off.
    """

    if reader_strategy == "single":
        vision = True
    elif reader_strategy in {"off", "shadow"}:
        vision = False
    local_single = reader_strategy == "single" and vision_provider == "vllm"
    if local_single:
        vision_reader_name = PROVIDER_READERS["vllm"]
    hooks = hooks_for_mode(mode, vision=vision)
    hook_names = sorted({hook.split(":", 1)[0] for hook in hooks})
    document: dict[str, object] = {
        "consent_text_version": consent_text_version(
            auto_verify_field_types=auto_verify_field_types
        ),
        "hooks": list(hooks),
        "mode": mode,
        "model": model,
        "prompt_versions": {
            hook: prompt_version(hook, reader=vision_reader_name) for hook in hook_names
        },
        "provider": provider,
        "redaction_version": REDACTION_VERSION,
        "schema_versions": {hook: SCHEMA_VERSIONS[hook] for hook in hook_names},
    }
    if vision and "h6_vision" in hook_names:
        reader = vision_reader(vision_reader_name)
        document["image_mask_version"] = IMAGE_MASK_VERSION
        document["vision_render_version"] = VISION_RENDER.version
        document["vision_prompt_variant"] = reader.prompt_variant if reader else "off"
    if local_single:
        document["vision_reader"] = {
            "base_url": vision_base_url,
            "model": vision_model,
            "provider": vision_provider,
        }
    if reader_strategy == "shadow":
        shadow_reader = vision_reader(PROVIDER_READERS.get(vision_provider or "", "off"))
        document["reader_strategy"] = "shadow"
        document["shadow_reader"] = {
            "base_url": vision_base_url if vision_provider == "vllm" else None,
            "image_mask_version": IMAGE_MASK_VERSION,
            "model": vision_model,
            "prompt_version": (
                prompt_version("h6_vision", reader=shadow_reader.name) if shadow_reader else None
            ),
            "provider": vision_provider,
            "vision_render_version": VISION_RENDER.version,
        }
    if apply_mode != "suggest":
        document["apply_mode"] = apply_mode
    if auto_verify_field_types:
        document["auto_verify_field_types"] = [
            item for item in AUTO_VERIFY_FIELD_TYPES if item in auto_verify_field_types
        ]
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


__all__ = [
    "CONSENT_TEXT",
    "CONSENT_TEXT_VERSION",
    "DATA_DESTINATIONS",
    "AUTO_VERIFY_CONSENT_TEXT_VERSION",
    "AUTO_VERIFY_CONSENT_TEXT",
    "AUTO_VERIFY_FIELD_LABELS",
    "AUTO_VERIFY_FIELD_TYPES",
    "DATA_SENT",
    "HOOKS_BY_MODE",
    "HOOK_LABELS",
    "MODE_LABELS",
    "consent_fingerprint",
    "data_destination",
    "consent_text",
    "consent_text_version",
    "hooks_for_mode",
]
