from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from processing.common import ROOT, path_for
from processing.glb import load_document, inspect_document
from processing.validate import validate_glb, validator_available


def test_all_models_inspected(models):
    assert len(models) >= 8
    for m in models:
        assert m["source"]["sha256"]
        assert m["processing_version"] == "1.0.0"
        assert m["validation"]["status"] in ("PASS", "WARNING", "ERROR")
        assert "geometry" in m and "skeleton" in m
    static = [m for m in models if not m["skeleton"]["present"]]
    rigged = [m for m in models if m["skeleton"]["present"]]
    assert static and rigged
    assert all(m["skeleton"]["bone_count"] == 0 for m in static)
    assert all(m["skeleton"]["bone_count"] >= 20 for m in rigged)


def test_invalid_and_missing_texture_detected(models):
    by_name = {m["name"]: m for m in models}
    assert by_name["invalid"]["validation"]["status"] == "ERROR"
    assert by_name["missing_texture"]["validation"]["status"] == "ERROR"
    multi = by_name["multi_material_pbr"]
    assert multi["geometry"]["material_count"] >= 2
    assert multi["geometry"]["texture_count"] >= 1


def test_retargeted_glbs_are_valid(animations, models):
    exported = [a for a in animations if (a.get("processed") or {}).get("path")]
    assert len(exported) >= 7
    for a in exported:
        path = ROOT / a["processed"]["path"]
        assert path.is_file()
        meta = inspect_document(load_document(path), path.name)
        assert meta["animations"]["clip_count"] == 1, a["id"]
        assert meta["skeleton"]["present"] is True
        assert meta["geometry"]["triangle_count"] > 0
        if validator_available():
            errors = [c for c in validate_glb(path) if c["severity"] == "ERROR"]
            assert not errors, (path, errors)


def test_quadruped_is_not_playable(animations):
    q = next(a for a in animations if a["name"] == "quadruped")
    assert q["retarget_status"] == "incompatible"
    assert q["processed"]["path"] is None
    assert q["compatibility"]


def test_khronos_validator_installed():
    assert validator_available(), "npm ci in tools/gltf-validator is required"


def test_android_source_structure():
    proc = subprocess.run([sys.executable, "scripts/check_android_sources.py"],
                          cwd=ROOT, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_build_index_counts(pipeline):
    idx = json.loads((path_for("manifests") / "index.json").read_text())
    assert idx["counts"]["models"] >= 8
    assert idx["counts"]["animations"] >= 10
