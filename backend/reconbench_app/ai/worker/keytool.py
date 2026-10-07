"""Generate, rotate, reseal and retire the AI worker's X25519 sealing keys.

``generate`` creates the keyring (or adds a new current key) and writes the
matching public-key file for the API. ``reseal`` re-encrypts every stored
provider key to the current key id. ``retire`` removes an old key once no secret
uses it. Output never contains key material or provider keys.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import sqlalchemy as sa
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from reconbench_app.ai.key_seal import seal, validate_key_id
from reconbench_app.ai.worker.key_open import Keyring, KeyringError, KeyUnreadable

KEYRING_NAME = "ai_keyring.json"
PUBLIC_KEY_NAME = "ai_seal_public.json"


def _write_private(path: Path, document: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(document, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(temporary, path)
    with contextlib.suppress(OSError):
        os.chmod(path, 0o600)


def generate(directory: Path, key_id: str | None = None) -> str:
    keyring_path = directory / KEYRING_NAME
    new_id = validate_key_id(key_id or datetime.now(UTC).strftime("k%Y%m%dT%H%M%SZ"))
    keys: dict[str, X25519PrivateKey] = {}
    if keyring_path.exists():
        existing = Keyring.load(keyring_path)
        keys.update(existing.private_keys)
        if new_id in keys:
            raise KeyringError(f"key id {new_id} already exists in the keyring")
    keys[new_id] = X25519PrivateKey.generate()
    keyring = Keyring(current_key_id=new_id, private_keys=keys)
    _write_private(keyring_path, keyring.to_document())
    _write_private(directory / PUBLIC_KEY_NAME, keyring.public_key().to_document())
    return new_id


def reseal(keyring: Keyring, database_url: str) -> tuple[int, int]:
    """Re-encrypt secrets sealed under non-current keys; return (resealed, unreadable)."""

    engine = sa.create_engine(database_url)
    public_key = keyring.public_key()
    resealed = unreadable = 0
    try:
        with engine.connect() as connection:
            ids = list(
                connection.scalars(
                    sa.text("SELECT id FROM secret WHERE key_id <> :current ORDER BY id"),
                    {"current": keyring.current_key_id},
                )
            )
        for secret_id in ids:
            with engine.begin() as connection:
                row = (
                    connection.execute(
                        sa.text(
                            "SELECT id, workspace_id, purpose, ciphertext, nonce, key_id "
                            "FROM secret WHERE id = :id FOR UPDATE"
                        ),
                        {"id": secret_id},
                    )
                    .mappings()
                    .first()
                )
                if row is None or row["key_id"] == keyring.current_key_id:
                    continue
                workspace_id = UUID(str(row["workspace_id"]))
                try:
                    plaintext = keyring.open(
                        envelope=bytes(row["ciphertext"]),
                        nonce=bytes(row["nonce"]),
                        stored_key_id=str(row["key_id"]),
                        workspace_id=workspace_id,
                        secret_id=UUID(str(row["id"])),
                        purpose=str(row["purpose"]),
                    )
                except KeyUnreadable:
                    unreadable += 1
                    continue
                sealed = seal(
                    plaintext,
                    public_key=public_key,
                    workspace_id=workspace_id,
                    secret_id=UUID(str(row["id"])),
                    purpose=str(row["purpose"]),
                )
                connection.execute(
                    sa.text(
                        "UPDATE secret SET ciphertext = :envelope, nonce = :nonce, "
                        "key_id = :key_id, rotated_at = now() WHERE id = :id"
                    ),
                    {
                        "envelope": sealed.envelope,
                        "nonce": sealed.nonce,
                        "key_id": sealed.key_id,
                        "id": row["id"],
                    },
                )
                resealed += 1
    finally:
        engine.dispose()
    return resealed, unreadable


def retire(directory: Path, key_id: str, database_url: str) -> None:
    keyring_path = directory / KEYRING_NAME
    keyring = Keyring.load(keyring_path)
    if key_id == keyring.current_key_id:
        raise KeyringError("the current key cannot be retired")
    if key_id not in keyring.private_keys:
        raise KeyringError(f"key id {key_id} is not in the keyring")
    engine = sa.create_engine(database_url)
    try:
        with engine.connect() as connection:
            in_use = int(
                connection.scalar(
                    sa.text("SELECT count(*) FROM secret WHERE key_id = :key_id"),
                    {"key_id": key_id},
                )
                or 0
            )
    finally:
        engine.dispose()
    if in_use:
        raise KeyringError(f"{in_use} stored key(s) still use {key_id}; run reseal first")
    remaining = {k: v for k, v in keyring.private_keys.items() if k != key_id}
    _write_private(
        keyring_path,
        Keyring(current_key_id=keyring.current_key_id, private_keys=remaining).to_document(),
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m reconbench_app.ai.worker.keytool")
    commands = parser.add_subparsers(dest="command", required=True)
    generate_parser = commands.add_parser("generate", help="create or rotate the keyring")
    generate_parser.add_argument("--dir", type=Path, required=True)
    generate_parser.add_argument("--key-id")
    commands.add_parser("reseal", help="re-encrypt stored keys to the current key")
    retire_parser = commands.add_parser("retire", help="remove an unused old key")
    retire_parser.add_argument("--dir", type=Path, required=True)
    retire_parser.add_argument("--key-id", required=True)
    arguments = parser.parse_args(argv)

    try:
        if arguments.command == "generate":
            key_id = generate(arguments.dir, arguments.key_id)
            print(
                f"Current AI sealing key: {key_id}. Wrote {KEYRING_NAME} (AI worker only) "
                f"and {PUBLIC_KEY_NAME} (API) in {arguments.dir}."
            )
            return 0
        database_url = os.environ.get("RB_DATABASE_URL", "").strip()
        if not database_url:
            print("RB_DATABASE_URL is required", file=sys.stderr)
            return 2
        if arguments.command == "reseal":
            keyring_file = os.environ.get("RB_AI_KEYRING_FILE", "").strip()
            if not keyring_file:
                print("RB_AI_KEYRING_FILE is required", file=sys.stderr)
                return 2
            resealed, unreadable = reseal(Keyring.load(Path(keyring_file)), database_url)
            print(f"Resealed {resealed} stored key(s); {unreadable} unreadable.")
            return 1 if unreadable else 0
        retire(arguments.dir, arguments.key_id, database_url)
        print(f"Retired AI sealing key {arguments.key_id}.")
        return 0
    except KeyringError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
