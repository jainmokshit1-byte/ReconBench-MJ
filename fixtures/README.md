# Synthetic acceptance fixtures

`SCN-01` through `SCN-20` map to the PRD acceptance scenario matrix. Each directory contains a synthetic `input.yaml` and its documented `expected.yaml`. SCN-02 is the worked partial-delivery example from PRD Appendix C1; its values are checked by `backend/tests/scenarios/test_c1_fixture.py`.

These files were authored from the supplied PRD and Implementation Plan. No real supplier, buyer, invoice, or sample files were opened or copied. SCN-19 is metadata for an unreadable-scan case; it contains no PDF. SCN-20 uses a synthetic stub-provider response.
