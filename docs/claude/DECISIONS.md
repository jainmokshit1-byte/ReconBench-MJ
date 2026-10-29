# Decisions (append-only; mark replaced entries "superseded by D-00X")
Full history with rationale: docs/decision-log.md (G-xx, Dxx) and docs/accuracy-plan.md. This file holds the few that every change must respect.

## D-001 Project memory lives in docs/claude/ — 2026-10-29
Decision: CLAUDE.md + docs/claude/{PROJECT_STATUS,DECISIONS,KNOWN_ISSUES}.md are the shared session memory; docs/decision-log.md stays the authoritative log | Reason: owner's setup instruction (chat, 2026-10-29) | Evidence: this file

## D-002 Pure core library — pre-existing
Decision: reconbench_core imports no app/DB/HTTP/LLM code, no env/clock/random, no floats | Reason: reason not documented (in files read) | Evidence: backend/pyproject.toml [tool.importlinter]; scripts/check_core_purity.py

## D-003 Engine version bump on rule changes — pre-existing
Decision: any change under rule paths needs a higher ENGINE_VERSION (backend/reconbench_core/version.py) | Reason: reason not documented (in files read) | Evidence: scripts/check_engine_version.py; .github/workflows/ci.yml

## D-004 Hard zeros gate every change — pre-existing
Decision: no false Matched, no false auto-links, no auto-accepted wrong values | Reason: stated as the accuracy contract | Evidence: README.md; docs/accuracy-plan.md §2

## D-005 Post-demo AI scope (owner, D103, 26 Oct 2026) — pre-existing
Decision: digital invoices always rules + text layer; one Settings switch for scanned invoices (default rules); AI only for scanned with switch on; handwritten values count only after a person's click; one active provider; mapping and reconciliation make 0 AI calls; no duplicate ingestion | Reason: owner decision on post-demo audit; details in docs/accuracy-plan.md §15 | Evidence: docs/accuracy-plan.md §0; commits 6a1a4a7, e1c042b

## D-006 Qwen reader = v4 full page + header band — pre-existing
Decision: full-page v4.q2 prompt and WI-BE8 cropped two-pass reading not adopted | Reason: measured worse against same-day v4 control (fields fell, accepted-wrong > 0) | Evidence: docs/accuracy-plan.md §0 (D92, D97)

## D-007 MinIO is local-only — pre-existing
Decision: archived MinIO Community image used only for local/CI | Reason: upstream repository archived | Evidence: README.md; docs/dependency-review.md; decision G-28

## D-008 recon-check project skill — 2026-10-29
Decision: .claude/skills/recon-check/SKILL.md runs static checks + focused pytest; full suite only on request; no allowed-tools | Reason: owner approval (chat) for the CI/Makefile check sequence | Evidence: .github/workflows/ci.yml; Makefile check-python

## D-009 Type-error authority — 2026-10-29
Decision: mypy --strict is the source of truth for type errors; LSP diagnostics are hints | Reason: owner instruction (chat) | Evidence: CLAUDE.md Conventions; backend/pyproject.toml [tool.mypy]

## D-010 Disabled skills, permission rules, pyright-lsp — 2026-10-29
Decision: settings applied in .claude/settings.local.json: skillOverrides "off" (migrate-to-shoehorn, scaffold-exercises, setup-ts-deep-modules, setup-pre-commit); deny reads of .env, deploy/secrets, work, golden inputs/documents; ask before destructive git, rm and Remove-Item (Bash + PowerShell); "Bash(python -)" allow removed | Reason: owner approval (chat); TS/course skills unused here | Evidence: .claude/settings.local.json
pyright-langserver 1.1.414 installed globally (`uv tool install "pyright[nodejs]"`, on PATH at ~/.local/bin); plugin pyright-lsp@claude-plugins-official 1.0.0 installed 2026-10-29 (scope local, enabled; marketplace already configured); typescript-lsp skipped | Reason: owner approval (chat) | Evidence: `pyright --version`, `claude plugin list` run 2026-10-29
