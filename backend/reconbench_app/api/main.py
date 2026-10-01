"""ASGI entry point for the Phase 1 API scaffold."""

from reconbench_app.api.application import create_app

app = create_app()
