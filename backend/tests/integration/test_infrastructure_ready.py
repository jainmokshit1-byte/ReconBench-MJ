"""Disposable local-service smoke checks for PostgreSQL and MinIO-compatible storage."""

import boto3
from botocore.config import Config
from reconbench_app.settings import Settings
from sqlalchemy import create_engine, text


def test_postgres_and_object_store_are_reachable() -> None:
    settings = Settings()
    engine = create_engine(settings.database_url, pool_pre_ping=True)
    s3 = boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        region_name=settings.s3_region,
        aws_access_key_id=settings.s3_api_access_key.get_secret_value(),
        aws_secret_access_key=settings.s3_api_secret_key.get_secret_value(),
        config=Config(connect_timeout=2, read_timeout=2, retries={"max_attempts": 1}),
    )
    try:
        with engine.connect() as connection:
            assert connection.execute(text("SELECT 1")).scalar_one() == 1
        s3.list_buckets()
    finally:
        engine.dispose()
