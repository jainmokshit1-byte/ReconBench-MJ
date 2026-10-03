"""Require byte-identical deterministic CLI output from every supplied runner."""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("outputs", nargs="+", type=Path)
    args = parser.parse_args()
    if len(args.outputs) < 2:
        parser.error("provide at least two runner output files")
    try:
        contents = [(path, path.read_bytes()) for path in args.outputs]
    except OSError as exc:
        print(f"Could not read a runner output: {exc}", file=sys.stderr)
        return 1
    baseline = contents[0][1]
    mismatches = [str(path) for path, content in contents[1:] if content != baseline]
    if mismatches:
        print(
            f"Reconciliation CLI output differs from {args.outputs[0]}: "
            + ", ".join(mismatches),
            file=sys.stderr,
        )
        return 1
    digest = hashlib.sha256(baseline).hexdigest()
    print(f"{len(contents)} runner outputs are byte-identical (sha256={digest}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
