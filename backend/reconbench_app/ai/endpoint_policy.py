"""Endpoint policy for self-hosted model servers (LLM plan WI-BE2, SSRF guard).

A local provider (vLLM) is reached at a base URL such as
``http://43.242.226.63/vllm/v1``. That URL is never trusted as typed: it is
normalised here and must match an entry of the deployment allowlist
(``RB_AI_VLLM_ENDPOINTS``) exactly, origin *and* path prefix. The deployment
gate checks it before a call is allowed and the provider checks it again before
every request, so a URL that is not allowlisted never reaches the network.

Pure module: standard library only, no HTTP client.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from urllib.parse import urlsplit

_SCHEMES = {"http": 80, "https": 443}
_HOST = re.compile(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*\Z")
_SEGMENT = re.compile(r"[A-Za-z0-9._~-]{1,64}\Z")
MAX_URL_LENGTH = 512


class EndpointNotAllowed(ValueError):
    """The URL is malformed or not on the deployment allowlist."""


def normalize_base_url(url: str) -> str:
    """``scheme://host[:port]/path`` with a lowercase host and no trailing slash.

    Only ``http``/``https``; no user info, query, fragment, ``.``/``..`` or empty
    path segments, percent-escapes or non-ASCII. A port equal to the scheme
    default is dropped so ``http://h:80/v1`` and ``http://h/v1`` are one endpoint.
    """

    if not isinstance(url, str) or not url or len(url) > MAX_URL_LENGTH:
        raise EndpointNotAllowed("endpoint URL is empty or too long")
    if not url.isascii() or any(ch.isspace() or ord(ch) < 32 for ch in url) or "\\" in url:
        raise EndpointNotAllowed("endpoint URL contains characters that are not allowed")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise EndpointNotAllowed("endpoint URL is malformed") from exc
    scheme = parts.scheme.lower()
    if scheme not in _SCHEMES:
        raise EndpointNotAllowed("endpoint URL must use http or https")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise EndpointNotAllowed("endpoint URL must not contain user info")
    if parts.query or parts.fragment or "?" in url or "#" in url:
        raise EndpointNotAllowed("endpoint URL must not contain a query or fragment")
    host = (parts.hostname or "").lower()
    if not host:
        raise EndpointNotAllowed("endpoint URL has no host")
    if ":" in host:
        if not re.fullmatch(r"[0-9a-f:.]{2,45}", host):
            raise EndpointNotAllowed("endpoint URL host is malformed")
        host = f"[{host}]"
    elif not _HOST.fullmatch(host):
        raise EndpointNotAllowed("endpoint URL host is malformed")
    path = parts.path.rstrip("/")
    segments = path.split("/")[1:] if path else []
    for segment in segments:
        if segment in {"", ".", ".."} or not _SEGMENT.fullmatch(segment):
            raise EndpointNotAllowed("endpoint URL path is malformed")
    netloc = host if port is None or port == _SCHEMES[scheme] else f"{host}:{port}"
    return f"{scheme}://{netloc}{path}"


def parse_allowlist(values: Iterable[str]) -> tuple[str, ...]:
    """Normalised, de-duplicated allowlist entries; a malformed entry is an error."""

    entries: list[str] = []
    for value in values:
        item = value.strip()
        if not item:
            continue
        normalized = normalize_base_url(item)
        if normalized not in entries:
            entries.append(normalized)
    return tuple(entries)


def check_allowed(url: str, allowlist: Iterable[str]) -> str:
    """The normalised URL when it exactly matches an allowlisted base URL."""

    normalized = normalize_base_url(url)
    allowed = {normalize_base_url(entry) for entry in allowlist}
    if normalized not in allowed:
        raise EndpointNotAllowed("endpoint URL is not on the deployment allowlist")
    return normalized


__all__ = [
    "MAX_URL_LENGTH",
    "EndpointNotAllowed",
    "check_allowed",
    "normalize_base_url",
    "parse_allowlist",
]
