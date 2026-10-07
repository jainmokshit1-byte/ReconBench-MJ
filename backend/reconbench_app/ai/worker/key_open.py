"""Open sealed provider keys; only the AI worker holds the private keyring."""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from reconbench_app.ai.key_seal import (
    NONCE_BYTES,
    PURPOSE_AI_API_KEY,
    EnvelopeError,
    SealingPublicKey,
    associated_data,
    derive_aes_key,
    parse_envelope,
    validate_key_id,
)

KEYRING_FORMAT = "reconbench-x25519-keyring.v1"


class KeyringError(RuntimeError):
    """The private keyring is missing or malformed; the AI worker must not start."""


class KeyUnreadable(RuntimeError):
    """A sealed key cannot be opened; the user must re-enter it."""

    def __init__(self) -> None:
        super().__init__("stored provider key is unreadable")


@dataclass(frozen=True, slots=True)
class Keyring:
    current_key_id: str
    private_keys: Mapping[str, X25519PrivateKey]

    @classmethod
    def load(cls, path: Path) -> Keyring:
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise KeyringError("AI keyring file is missing or unreadable") from exc
        if not isinstance(document, dict) or document.get("format") != KEYRING_FORMAT:
            raise KeyringError("AI keyring file has an unknown format")
        keys = document.get("keys")
        current = document.get("current")
        if not isinstance(keys, dict) or not keys or not isinstance(current, str):
            raise KeyringError("AI keyring file has no keys")
        private_keys: dict[str, X25519PrivateKey] = {}
        for key_id, encoded in keys.items():
            try:
                validate_key_id(str(key_id))
                raw = base64.b64decode(str(encoded), validate=True)
                private_keys[str(key_id)] = X25519PrivateKey.from_private_bytes(raw)
            except ValueError as exc:
                raise KeyringError("AI keyring file contains an invalid key") from exc
        if current not in private_keys:
            raise KeyringError("AI keyring current key id is not in the keyring")
        return cls(current_key_id=current, private_keys=MappingProxyType(private_keys))

    def to_document(self) -> dict[str, object]:
        return {
            "format": KEYRING_FORMAT,
            "current": self.current_key_id,
            "keys": {
                key_id: base64.b64encode(
                    key.private_bytes(
                        serialization.Encoding.Raw,
                        serialization.PrivateFormat.Raw,
                        serialization.NoEncryption(),
                    )
                ).decode("ascii")
                for key_id, key in sorted(self.private_keys.items())
            },
        }

    def public_key(self, key_id: str | None = None) -> SealingPublicKey:
        selected = key_id or self.current_key_id
        return SealingPublicKey(
            key_id=selected, public_key=self.private_keys[selected].public_key()
        )

    def open(
        self,
        *,
        envelope: bytes,
        nonce: bytes,
        stored_key_id: str,
        workspace_id: UUID,
        secret_id: UUID,
        purpose: str = PURPOSE_AI_API_KEY,
    ) -> bytes:
        """Return the plaintext key or raise ``KeyUnreadable`` without detail."""

        try:
            parsed = parse_envelope(envelope)
        except EnvelopeError as exc:
            raise KeyUnreadable() from exc
        private_key = self.private_keys.get(parsed.key_id)
        if private_key is None or parsed.key_id != stored_key_id or len(nonce) != NONCE_BYTES:
            raise KeyUnreadable()
        try:
            ephemeral = X25519PublicKey.from_public_bytes(parsed.ephemeral_public)
            recipient_public = private_key.public_key().public_bytes(
                serialization.Encoding.Raw, serialization.PublicFormat.Raw
            )
            aes_key = derive_aes_key(
                private_key.exchange(ephemeral),
                ephemeral_public=parsed.ephemeral_public,
                recipient_public=recipient_public,
                key_id=parsed.key_id,
            )
            return AESGCM(aes_key).decrypt(
                nonce,
                parsed.ciphertext,
                associated_data(
                    workspace_id=workspace_id,
                    secret_id=secret_id,
                    purpose=purpose,
                    key_id=parsed.key_id,
                ),
            )
        except (InvalidTag, ValueError) as exc:
            raise KeyUnreadable() from exc


__all__ = ["KEYRING_FORMAT", "KeyUnreadable", "Keyring", "KeyringError"]
