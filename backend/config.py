"""Runtime configuration from environment variables; no secrets in source."""
from __future__ import annotations

import os
from dataclasses import dataclass

from processing.common import path_for


@dataclass(frozen=True)
class Settings:
    api_key: str = ""
    storage_driver: str = "local"
    signed_secret: str = ""
    signed_ttl: int = 900
    rate_limit: int = 120
    s3_bucket: str = ""
    s3_endpoint: str = ""
    s3_region: str = "us-east-1"
    s3_access_key: str = ""
    s3_secret_key: str = ""
    public_base_url: str = ""

    @property
    def database_path(self):
        return path_for("database").parent / "asset_studio.db"

    @property
    def auth_enabled(self) -> bool:
        return bool(self.api_key)

    @property
    def auth_description(self) -> str:
        return ("API key required" if self.auth_enabled
                else "local demo mode (set ASSET_STUDIO_API_KEY to enable auth)")


def settings() -> Settings:
    """Read the environment on every call so tests and runtime reconfiguration work."""
    return Settings(
        api_key=os.environ.get("ASSET_STUDIO_API_KEY", ""),
        storage_driver=os.environ.get("ASSET_STUDIO_STORAGE", "local"),
        signed_secret=os.environ.get("ASSET_STUDIO_SIGNING_SECRET", ""),
        signed_ttl=int(os.environ.get("ASSET_STUDIO_SIGNED_TTL", "900")),
        rate_limit=int(os.environ.get("ASSET_STUDIO_RATE_LIMIT", "120")),
        s3_bucket=os.environ.get("ASSET_STUDIO_S3_BUCKET", ""),
        s3_endpoint=os.environ.get("ASSET_STUDIO_S3_ENDPOINT", ""),
        s3_region=os.environ.get("ASSET_STUDIO_S3_REGION", "us-east-1"),
        s3_access_key=os.environ.get("ASSET_STUDIO_S3_ACCESS_KEY", ""),
        s3_secret_key=os.environ.get("ASSET_STUDIO_S3_SECRET_KEY", ""),
        public_base_url=os.environ.get("ASSET_STUDIO_PUBLIC_BASE_URL", ""),
    )
