FROM ghcr.io/astral-sh/uv:0.12.19 AS uv

FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONPATH=/app/backend
WORKDIR /app
COPY --from=uv /uv /uvx /bin/
COPY backend/pyproject.toml backend/uv.lock /app/backend/
COPY backend/alembic.ini /app/backend/alembic.ini
COPY backend/migrations /app/backend/migrations
COPY backend/reconbench_core /app/backend/reconbench_core
COPY backend/reconbench_app /app/backend/reconbench_app
RUN uv sync --project /app/backend --locked --no-dev
EXPOSE 8000
CMD ["/app/backend/.venv/bin/uvicorn", "reconbench_app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
