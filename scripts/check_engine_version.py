"""Require a semantic engine-version increase when rule-bearing core code changes."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION_PATH = "backend/reconbench_core/version.py"
RULE_PATHS = (
    "backend/reconbench_app/db/seed_data.py",
    "backend/reconbench_core/align",
    "backend/reconbench_core/mapping",
    "backend/reconbench_core/normalize",
    "backend/reconbench_core/recon",
    "backend/reconbench_core/schema",
)
_VERSION = re.compile(r'^ENGINE_VERSION\s*=\s*["\']([^"\']+)["\']', re.MULTILINE)


def _git_show(revision: str, path: str) -> str | None:
    result = subprocess.run(
        ["git", "show", f"{revision}:{path}"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout if result.returncode == 0 else None


def _version(text: str | None) -> tuple[int, int, int] | None:
    if text is None:
        return None
    match = _VERSION.search(text)
    if match is None:
        return None
    parts = match.group(1).split(".")
    if len(parts) != 3 or not all(part.isdecimal() for part in parts):
        return None
    return tuple(int(part) for part in parts)  # type: ignore[return-value]


def _changed_rule_paths(base_ref: str) -> list[str] | None:
    result = subprocess.run(
        ["git", "diff", "--name-only", base_ref, "--", *RULE_PATHS],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        return None
    return [line for line in result.stdout.splitlines() if line]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-ref", required=True)
    args = parser.parse_args()
    if not args.base_ref or set(args.base_ref) == {"0"}:
        print("Engine version comparison skipped: this push has no prior commit.")
        return 0

    changed_paths = _changed_rule_paths(args.base_ref)
    if changed_paths is None:
        print(
            f"Could not compare rule sources with base ref {args.base_ref}.",
            file=sys.stderr,
        )
        return 1
    if not changed_paths:
        print("Rule-bearing core modules are unchanged from the comparison commit.")
        return 0

    base_version_text = _git_show(args.base_ref, VERSION_PATH)
    current_version = _version((ROOT / VERSION_PATH).read_text(encoding="utf-8"))
    base_version = _version(base_version_text) or (0, 0, 0)
    if current_version is None:
        print(
            "ENGINE_VERSION must be a semantic major.minor.patch version.",
            file=sys.stderr,
        )
        return 1
    if current_version <= base_version:
        print(
            "Rule-bearing core source changed but ENGINE_VERSION did not increase "
            f"({'.'.join(map(str, base_version))} → {'.'.join(map(str, current_version))}).",
            file=sys.stderr,
        )
        return 1
    print(
        "Engine version bump verified for the changed rules source: "
        f"{'.'.join(map(str, base_version))} → {'.'.join(map(str, current_version))}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
