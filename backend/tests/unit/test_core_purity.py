"""Regression tests for the pure-core dependency and deterministic numeric rules."""

from pathlib import Path

from scripts.check_core_purity import analyze_source


def test_rejects_infrastructure_import_in_core_file(tmp_path: Path) -> None:
    source_file = tmp_path / "leaky.py"
    source_file.write_text("import sqlalchemy\n", encoding="utf-8")

    violations = analyze_source(
        source_file.read_text(encoding="utf-8"), "reconbench_core.parsers.leaky"
    )

    assert any("forbidden import sqlalchemy" in item for item in violations)


def test_rejects_clock_reads_in_core_file(tmp_path: Path) -> None:
    source_file = tmp_path / "clock.py"
    source_file.write_text(
        "from datetime import datetime\nnow = datetime.now()\n", encoding="utf-8"
    )

    violations = analyze_source(
        source_file.read_text(encoding="utf-8"), "reconbench_core.normalize.clock"
    )

    assert any("datetime.now" in item for item in violations)


def test_rejects_float_in_numeric_core_file(tmp_path: Path) -> None:
    source_file = tmp_path / "money.py"
    source_file.write_text(
        "def total(amount: float) -> float:\n    return float(amount)\n", encoding="utf-8"
    )

    violations = analyze_source(
        source_file.read_text(encoding="utf-8"), "reconbench_core.recon.money"
    )

    assert sum("float" in item for item in violations) >= 2
