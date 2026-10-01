"""Ensure the four Plan-required open Schema §13 items remain recorded once."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DECISION_LOG = ROOT / "docs" / "decision-log.md"
SECTION = "## 3. Remaining Backend Schema §13 decisions"
EXPECTED_ROWS = {
    12: "| 12 | Ledger completeness detection remains a proposed rule. | G-15 |",
    14: "| 14 | Whether `rules_hash` excludes `body.app` remains unconfirmed. | G-25 |",
    16: (
        "| 16 | KPI/list treatment of approved, held, and rejected cases needs confirmation; "
        "the effective-status interpretation is still a proposal. | G-14 |"
    ),
    24: (
        "| 24 | Reason-code titles, messages, and suggested actions await the missing copy deck "
        "and Finance review. | G-06 |"
    ),
}


def main() -> int:
    content = DECISION_LOG.read_text(encoding="utf-8")
    if SECTION not in content:
        raise SystemExit(f"Missing decision-log section: {SECTION}")
    section = content.split(SECTION, maxsplit=1)[1].split("\n## ", maxsplit=1)[0]
    errors: list[str] = []
    for item, expected_row in EXPECTED_ROWS.items():
        matching_rows = [
            line.strip()
            for line in section.splitlines()
            if line.strip().startswith(f"| {item} |")
        ]
        if matching_rows != [expected_row]:
            errors.append(
                f"Schema §13 #{item}: expected exactly one unchanged open row; "
                f"found {matching_rows!r}"
            )
    if errors:
        print("Phase 2 decision-log check failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print("Phase 2 decision-log check passed: #12, #14, #16, and #24 remain open once each")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
