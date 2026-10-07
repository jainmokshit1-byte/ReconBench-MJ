"""Ensure core source changes are accompanied by an updated deterministic rules hash."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "backend" / "reconbench_core"
HASH_FILE = CORE / "RULES_HASH"


def calculate_rules_hash() -> str:
    digest = hashlib.sha256()
    for path in sorted(
        CORE.rglob("*.py"), key=lambda item: item.relative_to(CORE).as_posix()
    ):
        relative = path.relative_to(CORE).as_posix().encode("utf-8")
        # Git may check these files out with CRLF on Windows and LF on Linux.
        # Hash normalized source bytes so the recorded rules hash is portable.
        contents = path.read_bytes()
        contents = contents.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(len(contents).to_bytes(8, "big"))
        digest.update(contents)
    return digest.hexdigest()


def main() -> int:
    actual = calculate_rules_hash()
    if not HASH_FILE.exists():
        print(
            f"Missing {HASH_FILE.relative_to(ROOT)}; write the current hash {actual}.",
            file=sys.stderr,
        )
        return 1
    expected = HASH_FILE.read_text(encoding="ascii").strip()
    if expected != actual:
        print(
            f"Core rules hash changed: recorded {expected or '<empty>'}, computed {actual}. "
            "Review the rule change and update backend/reconbench_core/RULES_HASH.",
            file=sys.stderr,
        )
        return 1
    print(f"Core rules hash verified: {actual}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
