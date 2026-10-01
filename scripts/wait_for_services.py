"""Wait for configured PostgreSQL and S3-compatible services to answer health calls."""

from __future__ import annotations

import argparse
import time

import boto3
from botocore.config import Config
from reconbench_app.settings import Settings
from sqlalchemy import create_engine, text


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attempts", type=int, default=60)
    parser.add_argument("--delay", type=float, default=2.0)
    args = parser.parse_args()
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
    last_error = "not checked"
    try:
        for attempt in range(1, args.attempts + 1):
            try:
                with engine.connect() as connection:
                    connection.execute(text("SELECT 1"))
                s3.list_buckets()
                print(f"PostgreSQL and object storage are ready (attempt {attempt}).")
                return 0
            except Exception as exc:  # noqa: BLE001 - retries are the point of this helper.
                last_error = type(exc).__name__
                time.sleep(args.delay)
        print(
            f"Services did not become ready after {args.attempts} attempts ({last_error})."
        )
        return 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
