"""The acceptance harness must fail closed on any synthetic false Matched."""

from scripts.golden_run import GoldenCase, run_engine_case, self_check, summarize


def test_harness_detects_false_matched() -> None:
    case = GoldenCase("synthetic", "Exception", "PO-SYNTH-1", ("QTY_OVER_PO",), {})
    result = summarize([case], [{"status": "Matched", "link": "PO-SYNTH-1"}])

    assert result["false_matched"] == 1
    assert self_check()


def test_empty_golden_corpus_is_reported_as_unverified() -> None:
    report = summarize([], [])

    assert report["case_count"] == 0
    assert report["false_matched"] == 0
    assert report["gate_status"] == "unverified_no_cases"


def test_golden_case_without_engine_snapshot_fails_closed() -> None:
    case = GoldenCase("no-snapshot", "Matched", None, (), {})

    prediction = run_engine_case(case)

    assert prediction["status"] == "Needs Review"
    assert prediction["adapter_error"]
