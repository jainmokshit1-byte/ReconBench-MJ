# Phase 1 technical spikes

Every spike uses synthetic inputs only. Do not add actual customer/sample files here. Record commands, environment, results, limits, and the resulting keep/replace decision in the matching report and `../..\docs\decision-log.md`.

- SP-1 covers transactional Procrastinate enqueue, retry after a worker is killed, graceful SIGTERM drain, and PostgreSQL advisory locks. It is gated on a disposable PostgreSQL runtime and has no ReconBench tables or migrations.
- SP-2 checks SciPy's Hungarian assignment after input sorting and 4-decimal score quantization.
- SP-3 measures a synthetic text-layer PDF only; real vendor PDF extraction accuracy remains open under G-07.
- SP-4 checks a synthetic Tally-style XML shape and a CSV header while preserving all CSV values as text.
