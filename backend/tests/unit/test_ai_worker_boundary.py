"""Only the AI worker may load the Gemini SDK or AI-worker code.

Complements the import-linter contract: this scan covers every application
module (including imports inside functions and literal ``importlib`` calls),
and the subprocess check proves the producer-side entry points stay SDK-free
at runtime.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]
APP_ROOT = BACKEND / "reconbench_app"
AI_WORKER_ROOT = APP_ROOT / "ai" / "worker"
_FORBIDDEN_PREFIXES = ("google.genai", "reconbench_app.ai.worker")
_DYNAMIC_IMPORTERS = frozenset({"import_module", "__import__"})


def _module_name(path: Path) -> str:
    relative = path.relative_to(BACKEND).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _is_forbidden(module: str) -> bool:
    return any(
        module == prefix or module.startswith(f"{prefix}.") for prefix in _FORBIDDEN_PREFIXES
    )


def _resolve_relative(module: str | None, level: int, current_module: str, is_package: bool) -> str:
    if level == 0:
        return module or ""
    package_parts = current_module.split(".")
    if not is_package:
        package_parts = package_parts[:-1]
    base = package_parts[: len(package_parts) - (level - 1)]
    return ".".join([*base, module] if module else base)


def forbidden_imports(source: str, current_module: str, *, is_package: bool = False) -> list[str]:
    """Return forbidden module references in ``source`` with their line numbers."""

    violations: list[str] = []
    for node in ast.walk(ast.parse(source)):
        targets: list[str] = []
        if isinstance(node, ast.Import):
            targets = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = _resolve_relative(node.module, node.level, current_module, is_package)
            targets = [base, *(f"{base}.{alias.name}" for alias in node.names)]
        elif isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if (
                name in _DYNAMIC_IMPORTERS
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                targets = [node.args[0].value]
        violations.extend(
            f"{current_module}:{node.lineno} imports {target}"
            for target in targets
            if _is_forbidden(target)
        )
    return violations


@pytest.mark.parametrize(
    "source",
    [
        "import google.genai\n",
        "from google import genai\n",
        "from google.genai import types\n",
        "def later():\n    from google.genai import types\n",
        "import importlib\nimportlib.import_module('google.genai')\n",
        "from reconbench_app.ai.worker import transport_guard\n",
        "from reconbench_app.ai import worker\n",
        "from .worker import transport_guard\n",
    ],
)
def test_scanner_detects_forbidden_imports(source: str) -> None:
    assert forbidden_imports(source, "reconbench_app.ai.leaky_module")


@pytest.mark.parametrize(
    "source",
    [
        "import httpx\n",
        "from reconbench_app.ai_assist import run_stub_guardrail_test\n",
        "import googleapiclient\n",
        "from reconbench_app.ai import contracts\n",
    ],
)
def test_scanner_allows_unrelated_imports(source: str) -> None:
    assert forbidden_imports(source, "reconbench_app.ai.clean_module") == []


def test_only_ai_worker_modules_import_gemini_or_ai_worker_code() -> None:
    violations: list[str] = []
    for path in sorted(APP_ROOT.rglob("*.py")):
        if AI_WORKER_ROOT in path.parents:
            continue
        violations.extend(
            forbidden_imports(
                path.read_text(encoding="utf-8"),
                _module_name(path),
                is_package=path.name == "__init__.py",
            )
        )
    assert violations == []


def test_ai_worker_package_exists_and_owns_the_sdk_import() -> None:
    guard = AI_WORKER_ROOT / "transport_guard.py"
    assert guard.is_file()
    source = guard.read_text(encoding="utf-8")
    assert "from google import genai" in source


def test_producer_entry_points_do_not_load_gemini_or_ai_worker_code() -> None:
    script = (
        "import sys\n"
        "import reconbench_app.api.application, reconbench_app.jobs, reconbench_app.worker\n"
        "loaded = sorted(m for m in sys.modules if m == 'google.genai'"
        " or m.startswith(('google.genai.', 'reconbench_app.ai.worker')))\n"
        "print(','.join(loaded))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    assert result.stdout.strip() == ""
