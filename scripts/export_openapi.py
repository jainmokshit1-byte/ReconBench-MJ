"""Write the API's checked-in OpenAPI snapshot without connecting to dependencies."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))


def main() -> int:
    defaults = {
        "RB_ENV": "ci",
        "RB_DATABASE_URL": "postgresql+psycopg://openapi:openapi@127.0.0.1:5432/openapi",
        "RB_S3_ENDPOINT_URL": "http://127.0.0.1:9000",
        "RB_S3_REGION": "us-east-1",
        "RB_S3_BUCKET": "openapi",
        "RB_S3_API_ACCESS_KEY": "openapi",
        "RB_S3_API_SECRET_KEY": "openapi",
        "RB_PUBLIC_BASE_URL": "http://localhost:5173",
    }
    for key, value in defaults.items():
        os.environ.setdefault(key, value)

    from reconbench_app.api.application import create_app

    schema = create_app().openapi()
    output_path = BACKEND / "openapi.json"
    output_path.write_text(
        json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"Wrote {output_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
