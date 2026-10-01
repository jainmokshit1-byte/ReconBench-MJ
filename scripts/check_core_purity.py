"""AST guard for environment, clock, random, forbidden imports, and numeric floats."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

FORBIDDEN_IMPORTS = {
    "reconbench_app",
    "sqlalchemy",
    "boto3",
    "httpx",
    "requests",
    "anthropic",
    "openai",
    "google",
}
FORBIDDEN_CLOCKS = {"datetime.now", "datetime.today", "date.today", "time.time"}
FORBIDDEN_RANDOM = {
    "random",
    "randint",
    "randrange",
    "choice",
    "choices",
    "shuffle",
    "uniform",
}
NUMERIC_MODULE_PARTS = {"money", "quantity", "amount", "price", "ledger", "recon"}


def _dotted_name(node: ast.AST) -> str:
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return ".".join(reversed(parts))


def analyze_source(source: str, module_name: str) -> list[str]:
    """Return policy violations found in one Python source file."""

    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [f"line {exc.lineno}: invalid Python syntax: {exc.msg}"]

    violations: list[str] = []
    numeric_module = any(
        part.lower() in NUMERIC_MODULE_PARTS for part in module_name.split(".")
    )
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if any(
                    alias.name == blocked or alias.name.startswith(f"{blocked}.")
                    for blocked in FORBIDDEN_IMPORTS
                ):
                    violations.append(
                        f"line {node.lineno}: forbidden import {alias.name}"
                    )
                if alias.name == "random":
                    violations.append(
                        f"line {node.lineno}: random is forbidden in core"
                    )
        elif isinstance(node, ast.ImportFrom):
            name = node.module or ""
            if any(
                name == blocked or name.startswith(f"{blocked}.")
                for blocked in FORBIDDEN_IMPORTS
            ):
                violations.append(f"line {node.lineno}: forbidden import {name}")
            if name == "random":
                violations.append(f"line {node.lineno}: random is forbidden in core")
        elif isinstance(node, ast.Call):
            called = _dotted_name(node.func)
            if called in FORBIDDEN_CLOCKS:
                violations.append(
                    f"line {node.lineno}: system clock call {called} is forbidden in core"
                )
            if called.startswith("os.environ"):
                violations.append(
                    f"line {node.lineno}: environment access {called} is forbidden in core"
                )
            if called.startswith("random.") or called in FORBIDDEN_RANDOM:
                violations.append(
                    f"line {node.lineno}: random call {called} is forbidden in core"
                )
            if numeric_module and called in {"float", "builtins.float"}:
                violations.append(
                    f"line {node.lineno}: float() is forbidden in money/quantity code"
                )
        elif isinstance(node, ast.Attribute):
            name = _dotted_name(node)
            if name == "os.environ":
                violations.append(
                    f"line {node.lineno}: environment access os.environ is forbidden in core"
                )
            if name in FORBIDDEN_CLOCKS:
                violations.append(
                    f"line {node.lineno}: system clock access {name} is forbidden in core"
                )
        elif numeric_module and isinstance(node, ast.Name) and node.id == "float":
            parent_context = getattr(node, "ctx", None)
            if isinstance(parent_context, ast.Load):
                violations.append(
                    f"line {node.lineno}: float annotation/reference is forbidden "
                    "in money/quantity code"
                )

    return sorted(set(violations))


def scan_core(root: Path) -> list[str]:
    """Scan source files in the pure-core package without importing them."""

    failures: list[str] = []
    for path in sorted(root.rglob("*.py")):
        module = ".".join(
            ("reconbench_core", *path.relative_to(root).with_suffix("").parts)
        )
        for violation in analyze_source(path.read_text(encoding="utf-8"), module):
            failures.append(f"{path}: {violation}")
    return failures


def main() -> int:
    root = Path(__file__).resolve().parents[1] / "backend" / "reconbench_core"
    failures = scan_core(root)
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    print(f"Core purity checks passed for {root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
