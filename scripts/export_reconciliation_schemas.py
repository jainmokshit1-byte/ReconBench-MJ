"""Publish the frozen reconciliation result JSON Schemas."""

from __future__ import annotations

import json
from pathlib import Path

from reconbench_core.schema import CaseResult, LineResult

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "schemas"


def _write_schema(model: type[CaseResult | LineResult], filename: str) -> None:
    schema = model.model_json_schema(mode="serialization")
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = f"https://reconbench.local/schemas/{filename}"
    rendered = json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    (OUTPUT / filename).write_text(rendered, encoding="utf-8", newline="\n")


def main() -> int:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    _write_schema(CaseResult, "case-result.schema.json")
    _write_schema(LineResult, "line-result.schema.json")
    print("Published CaseResult and LineResult JSON Schemas.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
