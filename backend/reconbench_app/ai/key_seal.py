"""Seal provider keys with the AI worker's public key (decision D4).

The API holds only an X25519 public key, so it can seal but never open a key.
Envelope: ``version (1 byte) | len(key_id) (1 byte) | key_id | ephemeral public
key (32 bytes) | AES-256-GCM ciphertext and tag``. The 12-byte nonce is stored in
``secret.nonce``. The AES key is HKDF-SHA256 over the X25519 shared secret, with
both public keys as salt. Additional authenticated data binds the workspace,
secret row, purpose and key id, so a sealed value cannot be moved between rows.
"""

from __future__ import annotations

import base64
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

SEAL_VERSION = 1
PURPOSE_AI_API_KEY = "ai_api_key"
PUBLIC_KEY_FORMAT = "reconbench-x25519-public.v1"
NONCE_BYTES = 12
_PUBLIC_KEY_BYTES = 32
_KEY_ID = re.compile(r"[A-Za-z0-9._-]{1,64}\Z")
_HKDF_INFO = b"reconbench/secret-seal/v1|"
MAX_PLAINTEXT_BYTES = 512


class SealingKeyError(ValueError):
    """The public sealing key is missing or malformed."""


class EnvelopeError(ValueError):
    """A sealed value is malformed; the reason is deliberately generic."""


@dataclass(frozen=True, slots=True)
class SealedSecret:
    envelope: bytes
    nonce: bytes
    key_id: str


@dataclass(frozen=True, slots=True)
class Envelope:
    version: int
    key_id: str
    ephemeral_public: bytes
    ciphertext: bytes


@dataclass(frozen=True, slots=True)
class SealingPublicKey:
    key_id: str
    public_key: X25519PublicKey

    @classmethod
    def from_file(cls, path: Path) -> SealingPublicKey:
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise SealingKeyError("AI sealing public key file is unreadable") from exc
        if not isinstance(document, dict) or document.get("format") != PUBLIC_KEY_FORMAT:
            raise SealingKeyError("AI sealing public key file has an unknown format")
        key_id = document.get("key_id")
        encoded = document.get("public_key")
        if not isinstance(key_id, str) or not _KEY_ID.fullmatch(key_id):
            raise SealingKeyError("AI sealing public key file has an invalid key id")
        if not isinstance(encoded, str):
            raise SealingKeyError("AI sealing public key file has no public key")
        try:
            raw = base64.b64decode(encoded, validate=True)
        except ValueError as exc:
            raise SealingKeyError("AI sealing public key is not base64") from exc
        if len(raw) != _PUBLIC_KEY_BYTES:
            raise SealingKeyError("AI sealing public key has the wrong length")
        return cls(key_id=key_id, public_key=X25519PublicKey.from_public_bytes(raw))

    def to_document(self) -> dict[str, str]:
        raw = self.public_key.public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        return {
            "format": PUBLIC_KEY_FORMAT,
            "key_id": self.key_id,
            "public_key": base64.b64encode(raw).decode("ascii"),
        }


def validate_key_id(key_id: str) -> str:
    if not _KEY_ID.fullmatch(key_id):
        raise SealingKeyError("key ids may contain only letters, digits, '.', '_' and '-'")
    return key_id


def associated_data(*, workspace_id: UUID, secret_id: UUID, purpose: str, key_id: str) -> bytes:
    return f"{workspace_id}|{secret_id}|{purpose}|{key_id}".encode("ascii")


def derive_aes_key(
    shared_secret: bytes, *, ephemeral_public: bytes, recipient_public: bytes, key_id: str
) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=ephemeral_public + recipient_public,
        info=_HKDF_INFO + key_id.encode("ascii"),
    ).derive(shared_secret)


def seal(
    plaintext: bytes,
    *,
    public_key: SealingPublicKey,
    workspace_id: UUID,
    secret_id: UUID,
    purpose: str = PURPOSE_AI_API_KEY,
) -> SealedSecret:
    """Encrypt to the worker's public key with a fresh ephemeral key and nonce."""

    if not plaintext or len(plaintext) > MAX_PLAINTEXT_BYTES:
        raise ValueError("secret plaintext length is out of range")
    ephemeral = X25519PrivateKey.generate()
    ephemeral_public = ephemeral.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    recipient_public = public_key.public_key.public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    aes_key = derive_aes_key(
        ephemeral.exchange(public_key.public_key),
        ephemeral_public=ephemeral_public,
        recipient_public=recipient_public,
        key_id=public_key.key_id,
    )
    nonce = os.urandom(NONCE_BYTES)
    ciphertext = AESGCM(aes_key).encrypt(
        nonce,
        plaintext,
        associated_data(
            workspace_id=workspace_id,
            secret_id=secret_id,
            purpose=purpose,
            key_id=public_key.key_id,
        ),
    )
    key_id_bytes = public_key.key_id.encode("ascii")
    envelope = (
        bytes((SEAL_VERSION, len(key_id_bytes))) + key_id_bytes + ephemeral_public + ciphertext
    )
    return SealedSecret(envelope=envelope, nonce=nonce, key_id=public_key.key_id)


def parse_envelope(envelope: bytes) -> Envelope:
    if len(envelope) < 2:
        raise EnvelopeError("sealed value is malformed")
    version, key_id_length = envelope[0], envelope[1]
    if version != SEAL_VERSION or not 1 <= key_id_length <= 64:
        raise EnvelopeError("sealed value is malformed")
    key_id_end = 2 + key_id_length
    public_end = key_id_end + _PUBLIC_KEY_BYTES
    if len(envelope) <= public_end + 16:
        raise EnvelopeError("sealed value is malformed")
    try:
        key_id = envelope[2:key_id_end].decode("ascii")
    except UnicodeDecodeError as exc:
        raise EnvelopeError("sealed value is malformed") from exc
    if not _KEY_ID.fullmatch(key_id):
        raise EnvelopeError("sealed value is malformed")
    return Envelope(
        version=version,
        key_id=key_id,
        ephemeral_public=envelope[key_id_end:public_end],
        ciphertext=envelope[public_end:],
    )


def key_hint(api_key: str) -> str:
    """Masked hint shown in the UI; never more than the last four characters."""

    stripped = api_key.strip()
    return "****" + stripped[-4:] if len(stripped) >= 12 else "****"


__all__ = [
    "MAX_PLAINTEXT_BYTES",
    "NONCE_BYTES",
    "PUBLIC_KEY_FORMAT",
    "PURPOSE_AI_API_KEY",
    "SEAL_VERSION",
    "Envelope",
    "EnvelopeError",
    "SealedSecret",
    "SealingKeyError",
    "SealingPublicKey",
    "associated_data",
    "derive_aes_key",
    "key_hint",
    "parse_envelope",
    "seal",
    "validate_key_id",
]
