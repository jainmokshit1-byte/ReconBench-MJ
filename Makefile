.PHONY: up down clean-local check check-static check-python check-web test golden openapi migrate api web secrets audit services spikes

up:
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
	uv run --project backend python scripts/export_openapi.py
	npm run api:generate --prefix web
	git diff --exit-code -- backend/openapi.json web/src/api/generated.ts

check-python:
	cd backend && uv run ruff check reconbench_core reconbench_app tests ../scripts
	cd backend && uv run mypy --strict
	cd backend && uv run lint-imports

check-web:
	npm ci --prefix web
	npm run lint --prefix web
	npm run typecheck --prefix web

test:
	uv run --project backend pytest backend/tests/unit backend/tests/property backend/tests/scenarios -q
	npm run test --prefix web -- --run --pool=threads --maxWorkers=1

golden:
	uv run --project backend python scripts/golden_run.py --self-check

spikes:
	uv run --project backend python -m scripts.spikes.sp1_procrastinate
	uv run --project backend python -m scripts.spikes.sp2_assignment
	uv run --project backend python scripts/spikes/sp3_pdf_baseline.py
	uv run --project backend python scripts/spikes/sp4_profiles.py

openapi:
	uv run --project backend python scripts/export_openapi.py
	npm run api:generate --prefix web

audit:
	uv run --project backend --group dev pip-audit --skip-editable
	npm audit --prefix web --audit-level=high

migrate:
	@echo "Database migrations are Phase 2 work; no migration command is available yet."
	@exit 2

api:
	uv run --project backend uvicorn reconbench_app.api.main:app --reload --host 127.0.0.1 --port 8000

web:
	npm run dev --prefix web -- --host 127.0.0.1
