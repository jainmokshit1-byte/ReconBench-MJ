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
from reconbench_app.ai.prompts import PROMPT_VERSIONS
from reconbench_app.ai.redaction import IMAGE_MASK_VERSION, REDACTION_VERSION
from reconbench_app.extraction.vision import VISION_RENDER

CONSENT_TEXT_VERSION = "consent.v1"

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


def hooks_for_mode(mode: str, *, vision: bool = False) -> tuple[str, ...]:
    hooks = HOOKS_BY_MODE.get(mode, ())
    if not vision:
        return hooks
    return tuple(
        "h6_vision:" + hook.split(":", 1)[1] if hook.startswith("h1_extract:") else hook
        for hook in hooks
    )


def consent_fingerprint(*, provider: str, model: str, mode: str, vision: bool = False) -> str:
    """Hash of what was consented to; unchanged from earlier versions while vision is off."""

    hooks = hooks_for_mode(mode, vision=vision)
    hook_names = sorted({hook.split(":", 1)[0] for hook in hooks})
    document: dict[str, object] = {
        "consent_text_version": CONSENT_TEXT_VERSION,
        "hooks": list(hooks),
        "mode": mode,
        "model": model,
        "prompt_versions": {hook: PROMPT_VERSIONS[hook] for hook in hook_names},
        "provider": provider,
        "redaction_version": REDACTION_VERSION,
        "schema_versions": {hook: SCHEMA_VERSIONS[hook] for hook in hook_names},
    }
    if vision and "h6_vision" in hook_names:
        document["image_mask_version"] = IMAGE_MASK_VERSION
        document["vision_render_version"] = VISION_RENDER.version
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


__all__ = [
    "CONSENT_TEXT",
    "CONSENT_TEXT_VERSION",
    "DATA_SENT",
    "HOOKS_BY_MODE",
    "HOOK_LABELS",
    "MODE_LABELS",
    "consent_fingerprint",
    "hooks_for_mode",
]
