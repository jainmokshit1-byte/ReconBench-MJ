"""Hand-checked synthetic encoding of the PRD Appendix C1 partial-delivery example."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]


def test_c1_numbers_match_prd_worked_example() -> None:
    inputs = yaml.safe_load((ROOT / "fixtures/SCN-02/input.yaml").read_text(encoding="utf-8"))
    expected = yaml.safe_load((ROOT / "fixtures/SCN-02/expected.yaml").read_text(encoding="utf-8"))
    purchase_order = inputs["input"]["purchase_order"]
    results = expected["results"]

    assert purchase_order["number"] == "BM-PO-0142"
    assert purchase_order["qty"] == "100"
    assert purchase_order["rate"] == "48000.00"
    assert [
        (
            row["invoice"],
            row["ledger"]["billed_before"],
            row["ledger"]["this_invoice"],
            row["ledger"]["grn_accepted_cumulative"],
        )
        for row in results
    ] == [
        ("ACR/26-27/2201", "0", "90", "90"),
        ("ACR/26-27/2260", "90", "10", "100"),
    ]
    assert inputs["input"]["documents"][1]["taxable"] == "4320000.00"
    assert inputs["input"]["documents"][1]["gst"] == "777600.00"
    assert inputs["input"]["documents"][1]["total"] == "5097600.00"
    assert inputs["input"]["documents"][3]["taxable"] == "480000.00"
    assert inputs["input"]["documents"][3]["gst"] == "86400.00"
    assert inputs["input"]["documents"][3]["total"] == "566400.00"
    assert all(result["status"] == "Matched" for result in results)
