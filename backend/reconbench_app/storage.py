"""Streaming object-store adapters used by ingestion and authenticated previews."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Protocol


@dataclass(slots=True)
class ObjectRead:
    stream: Any
    size: int
    status_code: int
    content_range: str | None = None


class ObjectStore(Protocol):
    """Minimal S3-compatible interface; callers retain ownership of upload streams."""

    def put_fileobj(self, key: str, stream: BinaryIO, content_type: str) -> None: ...

    def get(self, key: str, byte_range: str | None = None) -> ObjectRead: ...

    def delete(self, key: str) -> None: ...

    def presigned_get(self, key: str, *, expires_seconds: int = 60) -> str: ...


class S3ObjectStore:
    """Boto3-backed object store; upload_fileobj uses bounded multipart buffers."""

    def __init__(self, client: Any, bucket: str) -> None:
        self.client = client
        self.bucket = bucket

    def put_fileobj(self, key: str, stream: BinaryIO, content_type: str) -> None:
        stream.seek(0)
        self.client.upload_fileobj(
            stream,
            self.bucket,
            key,
            ExtraArgs={"ContentType": content_type},
        )

    def get(self, key: str, byte_range: str | None = None) -> ObjectRead:
        parameters: dict[str, str] = {"Bucket": self.bucket, "Key": key}
        if byte_range is not None:
            parameters["Range"] = byte_range
        result = self.client.get_object(**parameters)
        return ObjectRead(
            stream=result["Body"],
            size=int(result["ContentLength"]),
            status_code=206 if byte_range is not None else 200,
            content_range=result.get("ContentRange"),
        )

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def presigned_get(self, key: str, *, expires_seconds: int = 60) -> str:
        if not 1 <= expires_seconds <= 300:
            raise ValueError("signed object links must expire within five minutes")
        return str(
            self.client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": key},
                ExpiresIn=expires_seconds,
            )
        )


class LocalObjectStore:
    """Disk-backed adapter for isolated tests; object names cannot escape its root."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        candidate = (self.root / key).resolve()
        if not candidate.is_relative_to(self.root):
            raise ValueError("object keys must remain under the configured storage root")
        return candidate

    def put_fileobj(self, key: str, stream: BinaryIO, content_type: str) -> None:
        del content_type
        target = self._path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        stream.seek(0)
        with target.open("wb") as output:
            shutil.copyfileobj(stream, output, length=64 * 1024)

    def get(self, key: str, byte_range: str | None = None) -> ObjectRead:
        target = self._path(key)
        size = target.stat().st_size
        start, end = _local_range(byte_range, size)
        stream = target.open("rb")
        stream.seek(start)
        content_range = f"bytes {start}-{end}/{size}" if byte_range is not None else None
        return ObjectRead(
            stream=stream,
            size=end - start + 1 if size else 0,
            status_code=206 if byte_range is not None else 200,
            content_range=content_range,
        )

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def presigned_get(self, key: str, *, expires_seconds: int = 60) -> str:
        del key, expires_seconds
        raise NotImplementedError("local disk objects are served through the authenticated API")


def _local_range(value: str | None, size: int) -> tuple[int, int]:
    if value is None:
        return 0, max(0, size - 1)
    if not value.startswith("bytes=") or "," in value:
        raise ValueError("only one byte range is supported")
    start_text, separator, end_text = value.removeprefix("bytes=").partition("-")
    if not separator:
        raise ValueError("invalid byte range")
    if not start_text:
        length = int(end_text)
        if length < 1 or size == 0:
            raise ValueError("invalid byte range")
        return max(0, size - length), size - 1
    start = int(start_text)
    end = int(end_text) if end_text else max(0, size - 1)
    if start < 0 or start >= size or end < start:
        raise ValueError("invalid byte range")
    return start, min(end, size - 1)


__all__ = ["LocalObjectStore", "ObjectRead", "ObjectStore", "S3ObjectStore"]
