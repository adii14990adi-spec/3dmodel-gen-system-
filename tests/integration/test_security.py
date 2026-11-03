from __future__ import annotations

import pytest

from backend.security import safe_resolve, signed_token, verify_signed_url
from processing.common import ROOT


def test_safe_resolve_blocks_traversal():
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        safe_resolve("../../etc/passwd")


def test_signed_url_expiry(monkeypatch):
    from backend.config import Settings
    cfg = Settings(signed_secret="unit-test-secret")
    import time
    now = int(time.time())
    tok = signed_token("asset", now + 100, cfg.signed_secret)
    assert verify_signed_url("asset", str(now + 100), tok, cfg)
    assert not verify_signed_url("asset", str(now - 1), tok, cfg)
    assert not verify_signed_url("asset", str(now + 100), "bad", cfg)


def test_auth_required_when_key_configured(monkeypatch):
    monkeypatch.setenv("ASSET_STUDIO_API_KEY", "secret-key")
    from fastapi.testclient import TestClient
    from backend.app import create_app
    with TestClient(create_app(sync=True)) as c:
        assert c.get("/api/v1/assets").status_code == 401
        assert c.get("/api/v1/assets", headers={"X-API-Key": "secret-key"}).status_code == 200
        assert c.get("/api/v1/assets", headers={"X-API-Key": "wrong"}).status_code == 401
    monkeypatch.delenv("ASSET_STUDIO_API_KEY")
