"""Create the ignored local .env from documented disposable defaults when absent."""

from __future__ import annotations

import secrets
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env"
EXAMPLE_FILE = ROOT / ".env.example"


def main() -> int:
    if not ENV_FILE.exists():
        shutil.copyfile(EXAMPLE_FILE, ENV_FILE)
        print("Created .env from .env.example.")

    lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
    secret_names = {"POSTGRES_PASSWORD", "MINIO_ROOT_PASSWORD"}
    found: set[str] = set()
    generated: list[str] = []
    for index, line in enumerate(lines):
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        name, current_value = line.split("=", 1)
        if name in secret_names:
            found.add(name)
            if not current_value:
                lines[index] = f"{name}={secrets.token_hex(24)}"
                generated.append(name)
    for name in sorted(secret_names - found):
        lines.append(f"{name}={secrets.token_hex(24)}")
        generated.append(name)
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if generated:
        print(f"Generated fresh local credentials for {', '.join(generated)} in ignored .env.")
    else:
        print("Using existing ignored .env credentials.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
