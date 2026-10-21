.PHONY: up down clean-local demo check check-static check-python check-web test test-e2e golden openapi migrate schema-drift phase2-catalog phase2-indexes phase2-decisions phase2-migration-safety phase2-table-count seed-phase2 phase2-db-tests queue-schema ingestion-worker ai-keys ai-worker api web web-docker secrets audit services spikes accuracy-bench accuracy-attribution accuracy-bench-vision accuracy-bench-vllm vision-probe

up: ai-keys
	uv run --project backend python scripts/ensure_local_env.py
	docker compose up -d --build postgres minio api

down:
	uv run --project backend python scripts/ensure_local_env.py
	docker compose down

clean-local:
	uv run --project backend python scripts/ensure_local_env.py
	docker compose down --volumes --remove-orphans

check: check-static test

check-static: check-python check-web
	uv run --project backend python scripts/check_core_purity.py
	uv run --project backend python scripts/check_rules_hash.py
	uv run --project backend python scripts/export_reconciliation_schemas.py
	uv run --project backend python scripts/export_openapi.py
	npm run api:generate --prefix web
	git diff --exit-code -- backend/openapi.json web/src/api/generated.ts docs/schemas

check-python:
	cd backend && uv run ruff check reconbench_core reconbench_app tests ../scripts
	cd backend && uv run mypy --strict
	cd backend && uv run lint-imports

check-web:
	npm ci --prefix web
	npm run lint --prefix web
	npm run typecheck --prefix web

test:
	uv run --project backend pytest backend/tests/unit backend/tests/parsers backend/tests/property backend/tests/scenarios -q
	npm run test --prefix web -- --run --pool=threads --maxWorkers=1

test-e2e:
	npm run test:e2e --prefix web

golden:
	uv run --project backend python scripts/golden_run.py --self-check

# Accuracy bench on the owner's local truth set (work/ is git-ignored). Runs in the
# worker image (same OCR as the app) with the working tree mounted and no network.
# Build the image first with `docker compose build worker`. Extra flags: BENCH_ARGS=...
accuracy-bench:
	docker run --rm --network none \
		-v "$(CURDIR)/backend/reconbench_core:/app/backend/reconbench_core:ro" \
		-v "$(CURDIR)/backend/reconbench_app:/app/backend/reconbench_app:ro" \
		-v "$(CURDIR)/scripts:/app/scripts:ro" \
		-v "$(CURDIR)/work:/app/work" \
		reconbench-worker /app/backend/.venv/bin/python /app/scripts/accuracy_bench.py $(BENCH_ARGS)

# Per-stage miss attribution (scan fix plan WI-1) for one bench report, from the
# reader cache only; exits 2 when the working tree changed since that report.
#   make accuracy-attribution REPORT=work/accuracy-reports/<name>.json [ATTR_ARGS="--json OUT"]
accuracy-attribution:
	docker run --rm --network none 		-v "$(CURDIR)/backend/reconbench_core:/app/backend/reconbench_core:ro" 		-v "$(CURDIR)/backend/reconbench_app:/app/backend/reconbench_app:ro" 		-v "$(CURDIR)/scripts:/app/scripts:ro" 		-v "$(CURDIR)/work:/app/work" 		reconbench-worker /app/backend/.venv/bin/python /app/scripts/accuracy_attribution.py 		--report /app/$(REPORT) $(ATTR_ARGS)

# Owner only (accuracy plan AC-5): records missing page-image readings with the
# network on. Needs BENCH_GEMINI_KEY and a page budget, for example
#   BENCH_GEMINI_KEY=... make accuracy-bench-vision BENCH_ARGS="--max-provider-pages 120"
# Optional (D38): --vision-max-output-tokens N, --vision-pause SECONDS; model via RB_BENCH_VISION_MODEL.
accuracy-bench-vision:
	docker run --rm -e BENCH_GEMINI_KEY -e RB_BENCH_VISION_MODEL \
		-v "$(CURDIR)/backend/reconbench_core:/app/backend/reconbench_core:ro" \
		-v "$(CURDIR)/backend/reconbench_app:/app/backend/reconbench_app:ro" \
		-v "$(CURDIR)/scripts:/app/scripts:ro" \
		-v "$(CURDIR)/work:/app/work" \
		reconbench-worker /app/backend/.venv/bin/python /app/scripts/accuracy_bench.py \
		--vision provider --allow-provider $(BENCH_ARGS)

# Owner only (LLM plan WI-EV1/EV2): the same bench read by the company vLLM server.
# Network on (the server is reached through the host's company VPN), but the only
# outbound call is to --vision-base-url, which must be on RB_AI_VLLM_ENDPOINTS; no
# Gemini client is built and no Gemini key is passed in. For example
#   RB_AI_VLLM_ENDPOINTS=http://43.242.226.63/vllm/v1 make accuracy-bench-vllm \
#     BENCH_ARGS="--vision-base-url http://43.242.226.63/vllm/v1 --max-provider-pages 3"
# With the default page budget (--max-provider-pages 0) nothing is sent; cached answers are scored.
accuracy-bench-vllm:
	docker run --rm -e RB_AI_VLLM_ENDPOINTS -e RB_AI_PRICE_FILE \
		-v "$(CURDIR)/backend/reconbench_core:/app/backend/reconbench_core:ro" \
		-v "$(CURDIR)/backend/reconbench_app:/app/backend/reconbench_app:ro" \
		-v "$(CURDIR)/scripts:/app/scripts:ro" \
		-v "$(CURDIR)/work:/app/work" \
		reconbench-worker /app/backend/.venv/bin/python /app/scripts/accuracy_bench.py \
		--vision provider --allow-provider --vision-provider vllm $(BENCH_ARGS)

# Owner only: vision reader probe (dry run unless PROBE_ARGS has --send; key in PROBE_KEY).
vision-probe:
	docker run --rm -e PROBE_KEY -e PROBE_MODEL \
		-v "$(CURDIR)/backend/reconbench_core:/app/backend/reconbench_core:ro" \
		-v "$(CURDIR)/backend/reconbench_app:/app/backend/reconbench_app:ro" \
		-v "$(CURDIR)/scripts:/app/scripts:ro" \
		-v "$(CURDIR)/work:/app/work" \
		reconbench-worker sh -c "uv pip install --python /app/backend/.venv/bin/python 'reportlab>=4.4' && /app/backend/.venv/bin/python /app/scripts/vision_probe.py $(PROBE_ARGS)"

spikes:
	uv run --project backend python -m scripts.spikes.sp1_procrastinate
	uv run --project backend python -m scripts.spikes.sp2_assignment
	uv run --project backend python scripts/spikes/sp3_pdf_baseline.py
	uv run --project backend python scripts/spikes/sp4_profiles.py

openapi:
	uv run --project backend python scripts/export_reconciliation_schemas.py
	uv run --project backend python scripts/export_openapi.py
	npm run api:generate --prefix web

audit:
	uv run --project backend --group dev pip-audit --skip-editable
	npm audit --prefix web --audit-level=high

migrate:
	uv run --project backend python scripts/ensure_local_env.py
	uv run --project backend alembic -c backend/alembic.ini upgrade head

schema-drift:
	uv run --project backend python scripts/check_schema_drift.py

phase2-catalog:
	uv run --project backend python scripts/check_phase2_catalog.py

phase2-indexes:
	uv run --project backend python scripts/check_phase2_indexes.py

phase2-decisions:
	uv run --project backend python scripts/check_phase2_decisions.py

phase2-migration-safety:
	uv run --project backend python scripts/check_migration_safety.py

seed-phase2:
	uv run --project backend python scripts/seed_database.py

phase2-db-tests:
	uv run --project backend pytest backend/tests/integration/test_phase2_database.py backend/tests/integration/test_phase2_readyz.py -q

phase2-table-count:
	uv run --project backend python scripts/check_phase2_table_count.py

queue-schema:
	uv run --project backend python scripts/ensure_local_env.py
	uv run --project backend procrastinate --app reconbench_app.db.procrastinate_schema.app schema --apply

ingestion-worker:
	docker compose up -d --build worker

# Creates the AI sealing keyring once; rotate with the keytool's generate/reseal/retire.
ai-keys:
	uv run --project backend python -c "import pathlib, sys; sys.exit(0 if pathlib.Path('deploy/secrets/worker/ai_keyring.json').exists() else 1)" || uv run --project backend python -m reconbench_app.ai.worker.keytool generate --dir deploy/secrets/worker
	uv run --project backend python -c "import pathlib, shutil; pathlib.Path('deploy/secrets/api').mkdir(parents=True, exist_ok=True); shutil.copyfile('deploy/secrets/worker/ai_seal_public.json', 'deploy/secrets/api/ai_seal_public.json')"

ai-worker: ai-keys
	docker compose up -d --build ai-worker

api:
	uv run --project backend uvicorn reconbench_app.api.main:app --reload --host 127.0.0.1 --port 8000

web:
	npm run dev --prefix web -- --host 127.0.0.1

web-docker:
	uv run --project backend python scripts/ensure_local_env.py
	docker compose up -d --build web

# Synthetic month through a disposable stack (API, worker, PostgreSQL, MinIO);
# compares every case with fixtures/demo-month/expected.yaml.
demo:
	uv run --project backend python scripts/demo_month.py run
