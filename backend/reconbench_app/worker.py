"""Command-line entry point for the ingestion Procrastinate worker."""

if __name__ == "__main__":
    # Imported here, not at module level: a sandbox child started with "spawn" re-imports
    # this module as __mp_main__, and loading the whole job registry there took ~9 s of the
    # PDF sandbox's 12 s wall budget (PDF_SANDBOX_UNAVAILABLE under load).
    from reconbench_app.jobs import worker_main

    raise SystemExit(worker_main())
