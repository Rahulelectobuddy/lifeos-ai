import asyncio
import os
import re
from pathlib import Path
from uuid import UUID


class LocalStorage:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, key: str) -> Path:
        parts = key.split("/")
        if (
            len(parts) != 2
            or not re.fullmatch(r"[a-zA-Z0-9_.-]{1,180}", parts[1])
            or parts[1] in {".", ".."}
        ):
            raise ValueError("Invalid artifact key")
        UUID(parts[0])
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("Artifact path escapes storage")
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    async def put(self, key, data):
        path = self.path(key)

        def write():
            temporary = path.with_suffix(path.suffix + ".tmp")
            temporary.write_bytes(data)
            os.replace(temporary, path)

        await asyncio.to_thread(write)
        return path

    async def publish(self, key):
        if not self.path(key).is_file():
            raise FileNotFoundError("Artifact is missing")


class S3Storage(LocalStorage):
    """Shared filesystem remains the working cache; S3 holds published artifacts."""

    def __init__(self, settings):
        super().__init__(settings.artifact_root)
        if not settings.s3_bucket:
            raise ValueError("S3_BUCKET is required")
        import boto3

        self.bucket = settings.s3_bucket
        self.client = boto3.client(
            "s3", endpoint_url=settings.s3_endpoint_url, region_name=settings.s3_region
        )

    async def publish(self, key):
        await asyncio.to_thread(self.client.upload_file, str(self.path(key)), self.bucket, key)


def create_storage(settings):
    return (
        S3Storage(settings)
        if settings.storage_provider == "s3"
        else LocalStorage(settings.artifact_root)
    )
