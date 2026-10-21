"""Deployment gate for external AI calls (decision D3).

``disabled`` (default) refuses every provider call. ``synthetic_only`` allows a
call only when its subject is a repository fixture *and* the exact outgoing
payload digest is in the compiled allowlist, so an uploaded document is refused
even if its text matches a fixture. ``enabled`` also requires an approval
reference; real-document calls stay unapproved until compliance supplies one.

Self-hosted model servers (vLLM, LLM plan WI-BE2) have their own switch,
``RB_AI_VLLM_CALLS`` with the same three modes, and an endpoint allowlist
``RB_AI_VLLM_ENDPOINTS``. They need no approval reference (the server is the
company's own), but the call's base URL must be allowlisted.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from reconbench_app.ai.endpoint_policy import EndpointNotAllowed, check_allowed, parse_allowlist
from reconbench_app.ai.fixtures import SYNTHETIC_FIXTURE_DIGESTS

ExternalCallsMode = Literal["disabled", "synthetic_only", "enabled"]
EXTERNAL_CALLS_MODES: tuple[ExternalCallsMode, ...] = ("disabled", "synthetic_only", "enabled")
_APPROVAL_REF = re.compile(r"[A-Z0-9][A-Z0-9._/-]{2,63}\Z")

SKIP_EXTERNAL_CALLS_DISABLED = "EXTERNAL_CALLS_DISABLED"
SKIP_SYNTHETIC_ONLY = "SYNTHETIC_ONLY"
SKIP_UNKNOWN_FIXTURE = "UNKNOWN_SYNTHETIC_FIXTURE"
SKIP_LOCAL_CALLS_DISABLED = "LOCAL_CALLS_DISABLED"
SKIP_ENDPOINT_NOT_ALLOWED = "ENDPOINT_NOT_ALLOWED"


class GateConfigurationError(ValueError):
    """The deployment gate settings are invalid; the process must not start."""


def payload_digest(
    *,
    system_instruction: str,
    input_text: str,
    response_schema: Mapping[str, Any],
    images: Sequence[tuple[str, str, str, str]] = (),
) -> str:
    """SHA-256 over the exact provider payload fields that carry content.

    ``images`` are ``(caption, mime type, sha256 of the bytes, resolution)``; the
    key is added only when images are sent, so text-only digests (and the
    synthetic fixture allowlist) are unchanged.
    """

    document: dict[str, Any] = {
        "input": input_text,
        "response_schema": response_schema,
        "system_instruction": system_instruction,
    }
    if images:
        document["images"] = [list(image) for image in images]
    canonical = json.dumps(
        document,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class DeploymentGate:
    mode: ExternalCallsMode
    approval_ref: str | None = None
    vllm_mode: ExternalCallsMode = "disabled"
    vllm_endpoints: tuple[str, ...] = ()
    """Normalised base URLs a self-hosted provider may be called at."""

    @classmethod
    def from_values(
        cls,
        mode: str,
        approval_ref: str | None,
        *,
        vllm_mode: str = "disabled",
        vllm_endpoints: str | None = None,
    ) -> DeploymentGate:
        if mode not in EXTERNAL_CALLS_MODES:
            raise GateConfigurationError(
                f"RB_AI_EXTERNAL_CALLS must be one of {', '.join(EXTERNAL_CALLS_MODES)}"
            )
        if vllm_mode not in EXTERNAL_CALLS_MODES:
            raise GateConfigurationError(
                f"RB_AI_VLLM_CALLS must be one of {', '.join(EXTERNAL_CALLS_MODES)}"
            )
        try:
            endpoints = parse_allowlist((vllm_endpoints or "").split(","))
        except EndpointNotAllowed as exc:
            raise GateConfigurationError(f"RB_AI_VLLM_ENDPOINTS: {exc}") from None
        if vllm_mode != "disabled" and not endpoints:
            raise GateConfigurationError(
                "RB_AI_VLLM_CALLS requires RB_AI_VLLM_ENDPOINTS (comma-separated base URLs)"
            )
        reference = (approval_ref or "").strip() or None
        if mode == "enabled":
            if reference is None or not _APPROVAL_REF.fullmatch(reference):
                raise GateConfigurationError(
                    "RB_AI_EXTERNAL_CALLS=enabled requires RB_AI_EXTERNAL_CALLS_APPROVAL_REF "
                    "(an uppercase compliance approval reference)"
                )
        else:
            reference = None
        return cls(
            mode=mode,
            approval_ref=reference,
            vllm_mode=vllm_mode,
            vllm_endpoints=endpoints,
        )

    def endpoint_allowed(self, endpoint: str | None) -> bool:
        if endpoint is None:
            return False
        try:
            check_allowed(endpoint, self.vllm_endpoints)
        except EndpointNotAllowed:
            return False
        return True

    def refusal(
        self,
        *,
        subject_type: str,
        fixture_id: str | None,
        digest: str,
        external: bool = True,
        endpoint: str | None = None,
    ) -> str | None:
        """Return a skip code when this call must not be sent, otherwise ``None``.

        ``external`` calls follow ``RB_AI_EXTERNAL_CALLS``; a self-hosted provider
        follows ``RB_AI_VLLM_CALLS`` and its ``endpoint`` must be allowlisted.
        """

        mode = self.mode if external else self.vllm_mode
        if mode == "disabled":
            return SKIP_EXTERNAL_CALLS_DISABLED if external else SKIP_LOCAL_CALLS_DISABLED
        if not external and not self.endpoint_allowed(endpoint):
            return SKIP_ENDPOINT_NOT_ALLOWED
        if mode == "synthetic_only":
            if subject_type != "fixture" or fixture_id is None:
                return SKIP_SYNTHETIC_ONLY
            if SYNTHETIC_FIXTURE_DIGESTS.get(fixture_id) != digest:
                return SKIP_UNKNOWN_FIXTURE
        return None

    @property
    def status_label(self) -> str | None:
        if self.mode == "disabled":
            return "External calls disabled"
        if self.mode == "synthetic_only":
            return "External calls: synthetic fixtures only"
        return None


__all__ = [
    "EXTERNAL_CALLS_MODES",
    "SKIP_ENDPOINT_NOT_ALLOWED",
    "SKIP_EXTERNAL_CALLS_DISABLED",
    "SKIP_LOCAL_CALLS_DISABLED",
    "SKIP_SYNTHETIC_ONLY",
    "SKIP_UNKNOWN_FIXTURE",
    "DeploymentGate",
    "ExternalCallsMode",
    "GateConfigurationError",
    "payload_digest",
]
