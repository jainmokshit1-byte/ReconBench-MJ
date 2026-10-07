"""E-invoice portal public keys from ``RB_EINVOICE_PUBLIC_KEY_FILE``."""

from __future__ import annotations

from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from reconbench_core.extraction.qr import load_public_keys


def einvoice_keys(path: Path | None) -> tuple[RSAPublicKey, ...]:
    """The configured keys; a configured file that holds no usable RSA key fails startup."""

    if path is None:
        return ()
    try:
        return load_public_keys(path.read_bytes())
    except (OSError, ValueError) as error:
        raise ValueError(f"RB_EINVOICE_PUBLIC_KEY_FILE is not a usable RSA key: {error}") from error


__all__ = ["einvoice_keys"]
