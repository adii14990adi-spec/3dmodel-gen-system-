from __future__ import annotations

from processing.manifest import build_manifest
from processing.report import build_report, to_markdown


def test_report_contains_required_diagnostic_fields():
    report = build_report("m1", "model", "assets/models/props/x.glb", [
        {"severity": "WARNING", "code": "MISSING_NORMALS", "description": "no normals",
         "asset": "x.glb", "location": "meshes[0]", "component": "geometry",
         "suggested_fix": "generate normals"}])
    assert report["status"] == "WARNING"
    c = report["checks"][0]
    for field in ("description", "asset", "location", "component", "severity", "code", "suggested_fix"):
        assert field in c
    assert "MISSING_NORMALS" in to_markdown(report)


def test_manifest_has_no_credentials_and_real_hashes():
    m = build_manifest(asset_id="model_x", kind="model", name="x", category="props",
                       source_path="assets/models/props/x.glb", source_hash="a" * 64,
                       source_format="GLB", processed_path="processed/models/model_x/model_x.glb",
                       processed_hash="b" * 64, processed_bytes=123,
                       processing_version="1.0.0", validation_status="PASS",
                       report="reports/models/model_x.json")
    assert m["source"]["sha256"] == "a" * 64
    assert m["processed"]["sha256"] == "b" * 64
    assert "token" not in str(m).lower()
    assert m["download_reference"] == "assets/model_x/download"


def test_manifest_rejects_credentials():
    import pytest
    with pytest.raises(ValueError):
        build_manifest(asset_id="x", kind="model", name="x", category="p",
                       source_path="p", source_hash="a", source_format="GLB",
                       processed_path=None, processed_hash=None, processed_bytes=0,
                       processing_version="1", validation_status="PASS", report="r",
                       metadata={"animations": {"clips": [{"secret": "leak"}]}})
