"""Command-line entry point for the ingestion Procrastinate worker."""

from reconbench_app.jobs import worker_main

if __name__ == "__main__":
    raise SystemExit(worker_main())
