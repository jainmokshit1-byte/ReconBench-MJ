"""ReconBench Terminal Trace Tool.

A strictly READ-ONLY terminal view for ReconBench document ingestion,
vendor resolution, line mapping, PO ledger, and 3-way reconciliation match.

Usage:
  python -m reconbench_tools.trace doc <invoice number | document id>
  python -m reconbench_tools.trace run <latest | run id>
  python -m reconbench_tools.trace follow [--interval 3]
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.engine import Engine, make_url

from reconbench_tools.trace_format import (
    enable_windows_ansi,
    format_ai_reader_line,
    format_doc_header,
    format_fields_table,
    format_follow_event,
    format_ingestion_line,
    format_ledger_section,
    format_lines_table,
    format_links_section,
    format_match_section,
    format_queue_line,
    format_run_mode,
    should_color,
)
from reconbench_tools.trace_queries import (
    find_document,
    get_ai_reading,
    get_doc_lines,
    get_document_links,
    get_document_version,
    get_field_evidence,
    get_po_ledger,
    get_queue_counts,
    get_recent_follow_events,
    get_recon_case_and_lines,
    get_recon_run_summary,
    get_source_file,
    resolve_workspace,
)


def _load_env_file() -> dict[str, str]:
    """Search and parse .env without external dependencies."""
    candidates = [
        Path.cwd() / ".env",
        Path.cwd().parent / ".env",
        Path(__file__).resolve().parents[2] / ".env",
    ]
    env_vars: dict[str, str] = {}
    for p in candidates:
        if p.exists() and p.is_file():
            try:
                with open(p, encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        k, v = line.split("=", 1)
                        k = k.strip()
                        v = v.strip().strip("'\"")
                        if k and k not in env_vars:
                            env_vars[k] = v
            except Exception:
                pass
            break
    return env_vars


def get_db_url() -> str:
    """Resolve database URL and apply host/port override for Windows host.

    Uses app login only:
    - RB_DATABASE_URL from the environment or .env;
    - if it isn't there, build the URL the same way compose.yaml builds the api
      service's RB_DATABASE_URL (same variables: POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB);
    - if neither works, exit with 'refusing: app database URL not found'.
    """
    raw_url = os.environ.get("RECONBENCH_TRACE_DB_URL") or os.environ.get("RB_DATABASE_URL")
    env_vars = _load_env_file()

    if not raw_url:
        val = env_vars.get("RB_DATABASE_URL")
        if val and val.strip():
            raw_url = val.strip()

    if not raw_url:
        # Build the same way compose.yaml builds the api service's RB_DATABASE_URL:
        # postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/${POSTGRES_DB:-reconbench}
        user = os.environ.get("POSTGRES_USER") or env_vars.get("POSTGRES_USER")
        pwd = os.environ.get("POSTGRES_PASSWORD") or env_vars.get("POSTGRES_PASSWORD")
        db = os.environ.get("POSTGRES_DB") or env_vars.get("POSTGRES_DB") or "reconbench"
        if user and pwd:
            raw_url = f"postgresql+psycopg://{user}:{pwd}@postgres:5432/{db}"

    if not raw_url:
        print("refusing: app database URL not found", file=sys.stderr)
        sys.exit(2)

    # Ensure postgresql+psycopg driver prefix
    if raw_url.startswith("postgresql://"):
        raw_url = "postgresql+psycopg://" + raw_url[len("postgresql://"):]

    url_obj = make_url(raw_url)

    # When running on the Windows host, override ONLY host and port to 127.0.0.1:15432
    # if pointing to the docker container host name ('postgres')
    if sys.platform == "win32" and url_obj.host == "postgres":
        url_obj = url_obj.set(host="127.0.0.1", port=15432)

    return url_obj.render_as_string(hide_password=False)


def create_trace_engine() -> Engine:
    """Create a strictly read-only SQLAlchemy engine."""
    url = get_db_url()
    connect_args = {
        "options": (
            "-c default_transaction_read_only=on "
            "-c statement_timeout=3000ms "
            "-c lock_timeout=500ms "
            "-c application_name=reconbench_trace"
        )
    }

    engine = sa.create_engine(
        url,
        connect_args=connect_args,
        pool_pre_ping=True,
    )

    # Verify read-only enforcement on connect
    try:
        with engine.connect() as conn:
            status = conn.execute(sa.text("SHOW default_transaction_read_only")).scalar()
            if str(status).strip().lower() != "on":
                print("refusing: read-only mode not confirmed", file=sys.stderr)
                sys.exit(2)
    except SystemExit:
        raise
    except Exception as exc:
        sanitized = re.sub(r"://[^@]+@", "://***:***@", str(exc))
        print(
            f"refusing: database connection failed ({type(exc).__name__}: {sanitized})",
            file=sys.stderr,
        )
        sys.exit(2)

    return engine


# --- Mode Handlers ---


def handle_doc(
    engine: Engine,
    workspace_id: UUID,
    doc_identifier: str,
    mask: bool = False,
    no_color: bool = False,
) -> int:
    """Execute `doc` mode: trace full document lifecycle."""
    with engine.connect() as conn:
        conn.execute(sa.text("SET TRANSACTION READ ONLY"))

        doc = find_document(conn, workspace_id, doc_identifier)
        if not doc:
            print(f"Document '{doc_identifier}' not found in workspace.")
            return 1

        doc_id = doc["id"]
        version_id = doc.get("current_version_id")
        ver = get_document_version(conn, workspace_id, version_id) if version_id else None

        # Ingestion info
        source_file = None
        if ver and ver.get("source_file_id"):
            source_file = get_source_file(conn, workspace_id, ver["source_file_id"])

        lines = get_doc_lines(conn, workspace_id, version_id) if version_id else []

        ingest_info = {
            "status": (
                source_file.get("status")
                if source_file
                else (ver.get("extraction_method") or "parsed" if ver else "parsed")
            ),
            "uploaded_at": (
                source_file.get("uploaded_at") if source_file else doc.get("first_seen_at")
            ),
            "parse_finished_at": (
                source_file.get("parse_finished_at")
                if source_file
                else (ver.get("created_at") if ver else doc.get("first_seen_at"))
            ),
            "extraction_method": ver.get("extraction_method") if ver else None,
            "line_count": len(lines),
        }

        # AI Reading
        ai_info = get_ai_reading(conn, workspace_id, doc_id, version_id) if version_id else None

        # Header fields & comparisons
        field_evidence = get_field_evidence(conn, workspace_id, version_id) if version_id else {}
        ai_fields = ai_info.get("fields", {}) if ai_info else {}

        # Build fields table
        # invoice no, total, taxable, vendor gstin, doc date
        fields_data = [
            {
                "name": "invoice no",
                "stored_val": ver.get("doc_no") if ver else doc.get("doc_no"),
                "source": (
                    field_evidence.get("doc_no", {}).get("source")
                    or (ver.get("extraction_method") if ver else None)
                    or "OCR"
                ),
                "conf": field_evidence.get("doc_no", {}).get("confidence"),
                "ai_val": ai_fields.get("doc_no"),
            },
            {
                "name": "total",
                "stored_val": ver.get("grand_total") if ver else doc.get("grand_total"),
                "source": field_evidence.get("grand_total", {}).get("source") or "OCR",
                "conf": field_evidence.get("grand_total", {}).get("confidence"),
                "ai_val": ai_fields.get("grand_total"),
            },
            {
                "name": "taxable",
                "stored_val": ver.get("taxable_total") if ver else None,
                "source": field_evidence.get("taxable_total", {}).get("source") or "OCR",
                "conf": field_evidence.get("taxable_total", {}).get("confidence"),
                "ai_val": ai_fields.get("taxable_total"),
            },
            {
                "name": "gstin",
                "stored_val": ver.get("vendor_gstin") if ver else None,
                "source": field_evidence.get("vendor_gstin", {}).get("source") or "OCR",
                "conf": field_evidence.get("vendor_gstin", {}).get("confidence"),
                "ai_val": ai_fields.get("supplier_gstin") or ai_fields.get("vendor_gstin"),
            },
            {
                "name": "date",
                "stored_val": ver.get("doc_date") if ver else doc.get("doc_date"),
                "source": field_evidence.get("doc_date", {}).get("source") or "OCR",
                "conf": field_evidence.get("doc_date", {}).get("confidence"),
                "ai_val": ai_fields.get("doc_date"),
            },
        ]

        # Links
        links, grn_links = get_document_links(conn, workspace_id, doc_id)
        linked_po_ids = [
            UUID(str(lk["to_document_id"]))
            for lk in links
            if lk.get("to_doc_type") == "po" or lk.get("target_doc_type") == "po"
        ]
        if doc.get("doc_type") == "po":
            linked_po_ids.append(doc_id)

        # Ledger
        ledger_rows = get_po_ledger(conn, workspace_id, linked_po_ids) if linked_po_ids else []

        # Recon match
        case_info, line_results = get_recon_case_and_lines(conn, workspace_id, doc_id)

        # Print output
        print(format_doc_header(doc, mask=mask, no_color=no_color))
        print(format_ingestion_line(ingest_info, no_color=no_color))
        print(format_ai_reader_line(ai_info, no_color=no_color))

        # Fields table
        for line in format_fields_table(fields_data, mask=mask, no_color=no_color):
            print(line)

        # Lines table
        for line in format_lines_table(lines, mask=mask, no_color=no_color):
            print(line)

        # Links section
        for line in format_links_section(links, grn_links=grn_links, no_color=no_color):
            print(line)

        # Ledger section
        for line in format_ledger_section(ledger_rows, no_color=no_color):
            print(line)

        # Match section
        for line in format_match_section(case_info, line_results, mask=mask, no_color=no_color):
            print(line)

        return 0


def handle_run(
    engine: Engine,
    workspace_id: UUID,
    run_identifier: str = "latest",
    no_color: bool = False,
) -> int:
    """Execute `run` mode: trace reconciliation run summary and cases."""
    with engine.connect() as conn:
        conn.execute(sa.text("SET TRANSACTION READ ONLY"))
        run_info, cases = get_recon_run_summary(conn, workspace_id, run_identifier)
        if not run_info:
            print(f"Reconciliation run '{run_identifier}' not found in workspace.")
            return 1

        for line in format_run_mode(run_info, cases, no_color=no_color):
            print(line)
        return 0


def handle_follow(
    engine: Engine,
    workspace_id: UUID,
    interval: float = 3.0,
    duration: float | None = None,
    mask: bool = False,
    no_color: bool = False,
) -> int:
    """Execute `follow` mode: live polling feed of queues and activities."""
    # Look back 10 minutes for initial context
    since_time = datetime.now(UTC) - timedelta(minutes=10)
    seen_ids: set[str] = set()
    start_mono = time.monotonic()

    try:
        # Initial queue line
        with engine.connect() as conn:
            conn.execute(sa.text("SET TRANSACTION READ ONLY"))
            q_counts = get_queue_counts(conn)
            print(format_queue_line(q_counts, no_color=no_color), flush=True)

            initial_events = get_recent_follow_events(
                conn, workspace_id, since=since_time, limit=50
            )
            for ev in initial_events:
                seen_ids.add(ev["id"])
                print(format_follow_event(ev, mask=mask, no_color=no_color), flush=True)

        # Main polling loop
        while True:
            if duration is not None and (time.monotonic() - start_mono) >= duration:
                break
            time.sleep(interval)
            if duration is not None and (time.monotonic() - start_mono) >= duration:
                break

            with engine.connect() as conn:
                conn.execute(sa.text("SET TRANSACTION READ ONLY"))
                q_counts = get_queue_counts(conn)
                print(format_queue_line(q_counts, no_color=no_color), flush=True)

                new_events = get_recent_follow_events(
                    conn, workspace_id, since=since_time, limit=50
                )
                for ev in new_events:
                    if ev["id"] not in seen_ids:
                        seen_ids.add(ev["id"])
                        print(format_follow_event(ev, mask=mask, no_color=no_color), flush=True)
                        since_time = max(since_time, ev.get("time") or since_time)

        return 0

    except KeyboardInterrupt:
        print("\nFollow stopped.", flush=True)
        return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint."""
    enable_windows_ansi()

    common_parser = argparse.ArgumentParser(add_help=False)
    common_parser.add_argument(
        "--mask",
        action="store_true",
        help="Mask sensitive fields (names, GSTINs, amounts) on screen.",
    )
    common_parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable ANSI terminal colors.",
    )
    common_parser.add_argument(
        "--workspace-id",
        type=UUID,
        default=None,
        help="Target workspace UUID (auto-detected if only one exists).",
    )

    parser = argparse.ArgumentParser(
        prog="reconbench_tools.trace",
        description="Read-only terminal trace tool for ReconBench.",
        parents=[common_parser],
    )

    subparsers = parser.add_subparsers(dest="mode", required=True)

    # doc mode
    doc_parser = subparsers.add_parser(
        "doc",
        parents=[common_parser],
        help="Show full lifecycle of a document.",
    )
    doc_parser.add_argument(
        "doc_id",
        type=str,
        help="Invoice number or document UUID.",
    )

    # run mode
    run_parser = subparsers.add_parser(
        "run",
        parents=[common_parser],
        help="Show reconciliation run summary and cases.",
    )
    run_parser.add_argument(
        "run_id",
        type=str,
        nargs="?",
        default="latest",
        help="Run identifier or 'latest' (default: latest).",
    )

    # follow mode
    follow_parser = subparsers.add_parser(
        "follow",
        parents=[common_parser],
        help="Live monitor queue status and new events.",
    )
    follow_parser.add_argument(
        "--interval",
        type=float,
        default=3.0,
        help="Poll interval in seconds (default: 3.0).",
    )
    follow_parser.add_argument(
        "--duration",
        type=float,
        default=None,
        help="Optional maximum monitor duration in seconds.",
    )

    args = parser.parse_args(argv)
    no_color = not should_color(args.no_color)

    engine = create_trace_engine()
    try:
        with engine.connect() as conn:
            conn.execute(sa.text("SET TRANSACTION READ ONLY"))
            workspace_id, ws_name = resolve_workspace(conn, args.workspace_id)
    except ValueError as err:
        print(f"Error: {err}", file=sys.stderr)
        return 1
    except Exception as exc:
        sanitized = re.sub(r"://[^@]+@", "://***:***@", str(exc))
        print(f"Error accessing workspace: {sanitized}", file=sys.stderr)
        return 1

    if args.mode == "doc":
        return handle_doc(
            engine,
            workspace_id,
            args.doc_id,
            mask=args.mask,
            no_color=no_color,
        )
    elif args.mode == "run":
        return handle_run(
            engine,
            workspace_id,
            args.run_id,
            no_color=no_color,
        )
    elif args.mode == "follow":
        return handle_follow(
            engine,
            workspace_id,
            interval=args.interval,
            duration=args.duration,
            mask=args.mask,
            no_color=no_color,
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
