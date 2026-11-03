from __future__ import annotations

import json
import subprocess
import sys

from processing.common import ROOT
from processing.report import build_report
from processing.validate import validate_glb, validator_available
from processing.glb import GLBError, load_document


def test_error_status_wins_over_warning():
    r = build_report("x", "model", "p", [{"severity": "WARNING", "code": "W"},
                                        {"severity": "ERROR", "code": "E"}])
    assert r["status"] == "ERROR"
    assert r["summary"]["ERROR"] == 1 and r["summary"]["WARNING"] == 1


def test_khronos_detects_truncated_glb(pipeline):
    assert validator_available()
    codes = [c["code"] for c in validate_glb(pipeline / "assets/models/props/invalid.glb")]
    assert any("GLB" in c or "VALIDATOR" in c for c in codes)


def test_khronos_detects_missing_texture(pipeline):
    codes = [c["code"] for c in validate_glb(pipeline / "assets/models/props/missing_texture.glb")]
    assert "UNRESOLVED_REFERENCE" in codes


def test_cli_status_json():
    proc = subprocess.run([sys.executable, "-m", "processing.cli", "status"],
                          cwd=ROOT, capture_output=True, text=True, check=True)
    data = json.loads(proc.stdout)
    assert data["khronos_validator"] is True
    assert "blender_binary" in data and "android_sdk" in data


def test_changed_files_script_runs():
    proc = subprocess.run([sys.executable, "scripts/changed_files.py", "--kind", "model", "--all"],
                          cwd=ROOT, capture_output=True, text=True, check=True)
    assert proc.stdout.strip() == "--all"


def test_publish_storage_noop_without_credentials(monkeypatch):
    monkeypatch.delenv("ASSET_STUDIO_S3_BUCKET", raising=False)
    proc = subprocess.run([sys.executable, "scripts/publish_storage.py"],
                          cwd=ROOT, capture_output=True, text=True,
                          env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(ROOT)})
    assert proc.returncode == 0
    assert "not configured" in proc.stdout


def test_gltf_source_can_be_packed_to_glb(pipeline, tmp_path):
    p = tmp_path / "tri.gltf"
    (tmp_path / "tri.bin").write_bytes(b"\x00\x00\x00\x00")
    p.write_text(json.dumps({"asset": {"version": "2.0"}, "buffers": [{"uri": "tri.bin", "byteLength": 4}],
                             "scenes": [{}], "scene": 0}))
    doc = load_document(p)
    assert doc["bin"] == b"\x00\x00\x00\x00"


def test_manifests_have_no_credential_words(models, animations):
    for m in models + animations:
        text = json.dumps(m).lower()
        for word in ("password", "secret", "token", "credential", "access_key", "private_key"):
            assert word not in text, (m["id"], word)
