from __future__ import annotations

import subprocess
import sys


def test_worker_entry_module_is_light_for_spawned_sandbox_children() -> None:
    """A spawned PDF sandbox child re-imports the worker's main module; it must not load jobs."""

    probe = (
        "import sys, reconbench_app.worker; "
        "print('reconbench_app.jobs' in sys.modules, 'reconbench_app.ingestion' in sys.modules)"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )
    assert result.stdout.split() == ["False", "False"]
