"""Operational commands for derived reconciliation ledgers and queue work."""

from __future__ import annotations

import argparse
import json
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.engine import create_engine

from reconbench_app.db.metadata import metadata
from reconbench_app.ledger_service import LedgerRebuildService
from reconbench_app.settings import Settings
from reconbench_app.worker_control import drain_worker_processes, resume_worker_processes


def rebuild_ledgers(database_url: str) -> dict[str, int]:
    """Rebuild every current PO ledger and consume outstanding dirty scopes."""

    engine = create_engine(database_url, pool_pre_ping=True)
    service = LedgerRebuildService(engine)
    totals = {"workspaces": 0, "pos": 0, "changed": 0, "entries": 0, "scopes": 0}
    try:
        workspace = metadata.tables["workspace"]
        document = metadata.tables["document"]
        with engine.connect() as connection:
            workspace_ids = connection.scalars(sa.select(workspace.c.id).order_by(workspace.c.id))
            workspaces = [UUID(str(item)) for item in workspace_ids]
        for workspace_id in workspaces:
            totals["workspaces"] += 1
            with engine.connect() as connection:
                po_ids = connection.scalars(
                    sa.select(document.c.id)
                    .where(
                        document.c.workspace_id == workspace_id,
                        document.c.doc_type == "po",
                        document.c.current_version_id.is_not(None),
                        document.c.withdrawn_at.is_(None),
                    )
                    .order_by(document.c.id)
                )
                purchase_orders = [UUID(str(item)) for item in po_ids]
            for po_id in purchase_orders:
                result = service.rebuild_po(workspace_id, po_id)
                totals["pos"] += 1
                totals["changed"] += int(bool(result["changed"]))
                totals["entries"] += int(result["entries"])
            while True:
                result = service.drain_dirty_scopes(workspace_id)
                totals["scopes"] += result["scopes"]
                totals["changed"] += result["changed"]
                totals["entries"] += result["entries"]
                if result["scopes"] == 0:
                    break
        return totals
    finally:
        engine.dispose()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="reconbench")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("rebuild-ledgers", help="rebuild all current PO ledgers")
    commands.add_parser(
        "drain-workers",
        help="persist a maintenance pause and park ReconBench workers after active jobs finish",
    ).add_argument(
        "--timeout-seconds",
        type=float,
        default=300.0,
        help="maximum drain wait; timeout leaves the persistent pause set (default: 300)",
    )
    commands.add_parser(
        "resume-workers", help="clear the maintenance pause and resume parked workers"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "rebuild-ledgers":
        settings = Settings()  # type: ignore[call-arg]  # BaseSettings loads required values from env.
        summary = rebuild_ledgers(settings.database_url)
        print(json.dumps(summary, sort_keys=True))
        return 0
    if args.command == "drain-workers":
        settings = Settings()  # type: ignore[call-arg]  # BaseSettings loads required values from env.
        count = drain_worker_processes(
            settings.database_url,
            timeout_seconds=args.timeout_seconds,
        )
        print(f"Drained and parked {count} ReconBench worker(s).")
        return 0
    if args.command == "resume-workers":
        settings = Settings()  # type: ignore[call-arg]  # BaseSettings loads required values from env.
        resume_worker_processes(settings.database_url)
        print("Resume requested for parked ReconBench workers.")
        return 0
    raise AssertionError(f"unsupported command: {args.command}")


__all__ = ["main", "rebuild_ledgers"]


if __name__ == "__main__":
    raise SystemExit(main())
