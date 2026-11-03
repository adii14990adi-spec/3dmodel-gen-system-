"""Auth, rate limiting, signed URLs and traversal-safe path resolution."""
from __future__ import annotations

import base64
import hashlib
import hmac
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Callable

from fastapi import Depends, Header, HTTPException, Request

from backend.config import Settings, settings
from processing.common import ROOT


def require_api_key(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> None:
    cfg = settings()
    if not cfg.auth_enabled:
        return None
    if not x_api_key or not hmac.compare_digest(x_api_key, cfg.api_key):
        raise HTTPException(status_code=401, detail="invalid or missing API key")
    return None


class RateLimiter:
    def __init__(self, per_minute: int) -> None:
        self.per_minute = per_minute
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str) -> None:
        now = time.monotonic()
        bucket = self._hits[key]
        while bucket and now - bucket[0] > 60:
            bucket.popleft()
        if len(bucket) >= self.per_minute:
            raise HTTPException(status_code=429, detail="rate limit exceeded")
        bucket.append(now)


_limiter = RateLimiter(settings().rate_limit)


def rate_limit(request: Request) -> None:
    _limiter.check(request.client.host if request.client else "unknown")


def signed_token(asset_id: str, expires: int, secret: str) -> str:
    payload = f"{asset_id}:{expires}".encode()
    return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()


def make_signed_url(base: str, asset_id: str, cfg: Settings) -> tuple[str, int]:
    expires = int(time.time()) + cfg.signed_ttl
    if not cfg.signed_secret:
        # Short-lived, unguessable-by-design demo token; production must set a secret.
        cfg = Settings(signed_secret=hashlib.sha256(f"demo:{asset_id}".encode()).hexdigest())
    token = signed_token(asset_id, expires, cfg.signed_secret)
    separator = "&" if "?" in base else "?"
    return f"{base}assets/{asset_id}/download{separator}expires={expires}&signature={token}", expires


def verify_signed_url(asset_id: str, expires: str, signature: str, cfg: Settings) -> bool:
    try:
        exp = int(expires)
    except ValueError:
        return False
    if exp < int(time.time()):
        return False
    secret = cfg.signed_secret or hashlib.sha256(f"demo:{asset_id}".encode()).hexdigest()
    return hmac.compare_digest(signature, signed_token(asset_id, exp, secret))


def safe_resolve(relative: str, allowed_root: Path | None = None) -> Path:
    """Reject absolute paths and traversal; resolve strictly inside the root."""
    root = (allowed_root or ROOT).resolve()
    candidate = (root / relative).resolve()
    if root not in candidate.parents and candidate != root:
        raise HTTPException(status_code=400, detail="path escapes the storage root")
    return candidate
