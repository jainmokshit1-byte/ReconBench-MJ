# Known issues

## Confirmed
- KI-001 Company vLLM server address committed in tracked files — Evidence: Makefile:79-80 (example command with a literal internal IP); same address also in backend/reconbench_app/ai/endpoint_policy.py, backend/tests/unit/test_accuracy_vision_eval.py, docs/accuracy-plan.md, docs/decision-log.md — Impact: internal network address in git history and any shared copy — Repro: `git grep -nE "http://[0-9.]+/vllm"`

## Documented, not re-measured
- KI-002 No accuracy target met (documented, not re-measured) — Evidence: docs/accuracy-plan.md §0, last changed in commit 704e8d8 (D98: PH2 gate "not met"; headers 101/136, line amount 28/47, quantity 24/44, rate 26/45 on same-day Qwen v4 read) — Impact: extraction values need human review — Repro: `make accuracy-bench` (owner's local truth set only)

## Suspected (unverified)
- PDFs over 20 pages: the sandbox always calls layout_parse with PDF_LAYOUT_MAX_PAGES=20 (backend/reconbench_app/pdf_sandbox.py:37, :423) while workspace hints allow more; whether pages 21+ raise a visible warning is not checked.
- Scan misdetection: local_hybrid.py:is_scanned flags a page as scanned when any single image covers ≥50% of it, and that page's text layer is then discarded for OCR (local_hybrid.py `layout`); a digital PDF with a full-page background image would be OCR'd. A text-less page with no large image (e.g. outlined fonts) gets no OCR. Impact on real files not measured.
- README "Current implementation scope" says reconciliation runs, review actions and exports are unfinished, but reconciliation_service.py, api/review.py and export_service.py exist; README may be stale (completeness not verified).
- Excel export on real data has never been measured (docs/accuracy-plan.md §0, as of 20 Oct; may be outdated).

## Open questions
- P1 lump-sum line rule and P2 "review should verify displayed fields" are listed as open in docs/accuracy-plan.md §0 (D100); current state not checked.
- Web tests (vitest) and lint not run on this machine: Node is not on PATH.
