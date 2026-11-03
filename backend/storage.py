"""Object storage abstraction. Local driver is the default; S3 is optional."""
from __future__ import annotations

from pathlib import Path
from typing import Protocol

from backend.config import Settings, settings
from backend.security import safe_resolve
from processing.common import ROOT


class StorageDriver(Protocol):
    def exists(self, reference: str) -> bool: ...
    def read(self, reference: str) -> bytes: ...
    def path(self, reference: str) -> Path: ...
    def external_url(self, reference: str) -> str | None: ...


class LocalStorage:
    name = "local"

    def path(self, reference: str) -> Path:
        return safe_resolve(reference, ROOT)

    def exists(self, reference: str) -> bool:
        return self.path(reference).is_file()

    def read(self, reference: str) -> bytes:
        return self.path(reference).read_bytes()

    def external_url(self, reference: str) -> str | None:
        return None


class S3Storage:
    """Optional S3-compatible driver; credentials come only from environment."""
    name = "s3"

    def __init__(self, cfg: Settings) -> None:
        self.cfg = cfg
        if not (cfg.s3_bucket and cfg.s3_access_key and cfg.s3_secret_key):
            raise RuntimeError("S3 storage requires ASSET_STUDIO_S3_* environment variables")
        try:
            import boto3  # type: ignore
        except ImportError as exc:
            raise RuntimeError("boto3 is required for S3 storage (pip install boto3)") from exc
        self.client = boto3.client("s3", endpoint_url=cfg.s3_endpoint or None,
                                   region_name=cfg.s3_region,
                                   aws_access_key_id=cfg.s3_access_key,
                                   aws_secret_access_key=cfg.s3_secret_key)

    def path(self, reference: str) -> Path:  # pragma: no cover - local ROOT semantics
        return safe_resolve(reference, ROOT)

    def exists(self, reference: str) -> bool:
        try:
            self.client.head_object(Bucket=self.cfg.s3_bucket, Key=reference.lstrip("/"))
            return True
        except Exception:
            return False

    def read(self, reference: str) -> bytes:
        return self.client.get_object(Bucket=self.cfg.s3_bucket, Key=reference.lstrip("/"))["Body"].read()

    def external_url(self, reference: str) -> str:
        return self.client.generate_presigned_url(
            "get_object", Params={"Bucket": self.cfg.s3_bucket, "Key": reference.lstrip("/")},
            ExpiresIn=self.cfg.signed_ttl)


def storage() -> StorageDriver:
    cfg = settings()
    return S3Storage(cfg) if cfg.storage_driver == "s3" else LocalStorage()
