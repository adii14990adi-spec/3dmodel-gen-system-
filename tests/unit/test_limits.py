from __future__ import annotations

import pytest

from processing.common import ROOT, UnsafeAssetError, validate_source


def test_rejects_script_suffix(tmp_path):
    f = tmp_path / "evil.py"; f.write_text("print(1)")
    with pytest.raises(UnsafeAssetError):
        validate_source(ROOT / "assets/models/props" / "evil.py", "model")


def test_rejects_empty_file(tmp_path):
    f = ROOT / "assets/models/props" / "empty.glb"
    f.write_bytes(b"")
    try:
        with pytest.raises(UnsafeAssetError):
            validate_source(f, "model")
    finally:
        f.unlink(missing_ok=True)


def test_rejects_traversal(tmp_path):
    with pytest.raises(UnsafeAssetError):
        validate_source((ROOT / "assets/models/../../config/studio.yaml").resolve(), "model")


def test_size_limit_enforced():
    f = ROOT / "assets/models/props" / "crate.glb"
    with pytest.raises(UnsafeAssetError):
        validate_source(f, "model", max_bytes=1)
