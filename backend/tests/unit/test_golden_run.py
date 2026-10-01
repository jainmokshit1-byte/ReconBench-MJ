"""The acceptance harness must fail closed on any synthetic false Matched."""

from scripts.golden_run import GoldenCase, self_check, summarize


def test_harness_detects_false_matched() -> None:
    case = GoldenCase("synthetic", "Exception", "PO-SYNTH-1", ("QTY_OVER_PO",), {})
    result = summarize([case], [{"status": "Matched", "link": "PO-SYNTH-1"}])

    assert result["false_matched"] == 1
    assert self_check()
