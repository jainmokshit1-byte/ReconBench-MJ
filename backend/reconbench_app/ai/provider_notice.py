"""The notice an admin accepts when connecting a provider, and its fingerprint (WI-PV2).

The notice is per provider and is stored on the provider card with who accepted it and
when. Its fingerprint covers the notice version, provider, tier (Gemini), model and data
destination, so changing any of them asks again. It is separate from the workspace-wide
AI consent of ``consent.py`` (whose fingerprints stay byte-identical, D82).

The API cannot tell which tier a Gemini key belongs to, so the admin declares it. A card
declared ``free`` shows the data-use warning explicitly (decision-log D83/D84, O-25).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from reconbench_app.ai.consent import data_destination

NOTICE_VERSION = "provider-notice.v1"
Tier = Literal["free", "paid"]

# Owner-approved wording of the free-tier warning (D83); kept as one constant so the text
# shown, the text fingerprinted and the tests all read the same words.
FREE_TIER_WARNING = (
    "This key is on Google's free tier. On the free tier, Google may use prompts and "
    "responses to improve its products, and human reviewers may read them. Real company "
    "documents sent through this key are covered by that warning."
)
PAID_TIER_NOTE = (
    "This key is declared as billed (paid tier). ReconBench cannot see your Google "
    "project's billing or data terms; check them yourself."
)

_GEMINI_TEXT = (
    "Connecting Google Gemini ({model}) lets ReconBench send the data listed for each AI "
    "step (masked page images and redacted text of your documents) to Google's Gemini API "
    "using the key you enter, with storage disabled on the provider side. The key is sealed "
    "on receipt and never shown again. AI output is only a suggestion until a person "
    "accepts it."
)
_VLLM_TEXT = (
    "Connecting the company vLLM server at {base_url} ({model}) lets ReconBench send masked "
    "page images and redacted text of your documents to that server over the company "
    "network. They leave this machine but not the company network. AI output is only a "
    "suggestion until a person accepts it."
)


def notice(provider: str, *, tier: Tier, model: str, base_url: str | None = None) -> dict[str, Any]:
    """Text, data-use flag and destination for one provider card."""

    if provider == "gemini":
        text = _GEMINI_TEXT.format(model=model)
        text += " " + (FREE_TIER_WARNING if tier == "free" else PAID_TIER_NOTE)
        destination = data_destination("gemini")
        data_use_warning = tier == "free"
    else:
        text = _VLLM_TEXT.format(base_url=base_url or "the configured address", model=model)
        destination = data_destination("vllm", base_url)
        data_use_warning = False
    return {
        "version": NOTICE_VERSION,
        "provider": provider,
        "tier": tier if provider == "gemini" else None,
        "model": model,
        "text": text,
        "data_destination": destination,
        "data_use_warning": data_use_warning,
    }


def notice_fingerprint(
    provider: str, *, tier: Tier, model: str, base_url: str | None = None
) -> str:
    shown = notice(provider, tier=tier, model=model, base_url=base_url)
    canonical = json.dumps(
        {
            "version": NOTICE_VERSION,
            "provider": provider,
            "tier": shown["tier"],
            "model": model,
            "base_url": base_url if provider == "vllm" else None,
            "text": shown["text"],
            "destination": shown["data_destination"],
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


__all__ = [
    "FREE_TIER_WARNING",
    "NOTICE_VERSION",
    "PAID_TIER_NOTE",
    "Tier",
    "notice",
    "notice_fingerprint",
]
