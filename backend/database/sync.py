"""Public sync entry point used by `asset-studio build-index`."""
from backend.database.db import sync_from_manifests

__all__ = ["sync_from_manifests"]
