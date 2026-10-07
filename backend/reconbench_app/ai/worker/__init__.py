"""AI-worker-only code: Gemini SDK use and provider transport guards.

Only the dedicated AI worker may import this package. API, job-registry and
general-worker modules must not import it (enforced by import-linter and
``tests/unit/test_ai_worker_boundary.py``).
"""
