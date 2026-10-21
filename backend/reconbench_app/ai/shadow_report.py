"""Shadow reader report (LLM plan WI-BE6): aggregates only, as ``GET /ai/shadow-report``.

Run inside the api container::

    python -m reconbench_app.ai.shadow_report --workspace <uuid> [--since YYYY-MM-DD]

Prints JSON: per provider and field the agree / disagree / reader-only / rules-only /
unreadable counts, latency p50/p95 and failures by code. Never a value or a document.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import date
from uuid import UUID

import sqlalchemy as sa

from reconbench_app.ai.shadow import workspace_report
from reconbench_app.json_serialization import to_jsonable


def main(argv: Sequence[str] | None = None, *, engine: sa.Engine | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--workspace", required=True, type=UUID, help="workspace id")
    parser.add_argument(
        "--since", type=date.fromisoformat, default=None, help="first day (YYYY-MM-DD, UTC)"
    )
    arguments = parser.parse_args(argv)
    if engine is None:
        from reconbench_app.settings import Settings

        engine = sa.create_engine(Settings().database_url)
    with engine.connect() as connection:
        body = workspace_report(connection, arguments.workspace, since=arguments.since)
    print(json.dumps(to_jsonable(body), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
