# Golden dataset

`make golden` runs `scripts/golden_run.py`, which evaluates every `*/truth.yaml` here with the deterministic engine. A case counts as verified when the predicted status and PO link equal the truth and every expected reason code is present. Any mismatch or false Matched fails the gate.

Current cases are synthetic only (`SYN-SCN-*`, mirroring `fixtures/SCN-*`). They use the compact form: `documents` (synthetic XML parsed by the core parser), `links` (labelled header links by document key), `invoice_key`, `run_date` and the expected status, link and reason codes. A `snapshot` key with a full engine snapshot is still accepted.

Real golden documents and truth labels are a gated input (G-07). They must be anonymised and approved before use, and are never committed.
