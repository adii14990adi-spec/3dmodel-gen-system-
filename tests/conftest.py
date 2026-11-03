from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="session")
def pipeline():
    """Generate the real A-L dataset and run both pipelines once for the suite."""
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    subprocess.run([sys.executable, "scripts/generate_sample_assets.py"], cwd=ROOT,
                   env=env, check=True, capture_output=True)
    for cmd in (["process-models", "--all"], ["process-animations", "--all"], ["build-index"]):
        proc = subprocess.run([sys.executable, "-m", "processing.cli", *cmd], cwd=ROOT,
                              env=env, capture_output=True, text=True)
        assert proc.returncode == 0, f"{cmd} failed: {proc.stdout}\n{proc.stderr}"
    return ROOT


@pytest.fixture(scope="session")
def client(pipeline):
    from fastapi.testclient import TestClient
    from backend.app import create_app
    with TestClient(create_app(sync=True)) as c:
        yield c


@pytest.fixture
def models(pipeline):
    return sorted((json.loads(p.read_text()) for p in (ROOT / "manifests/models").glob("*.json")),
                  key=lambda m: m["id"])


@pytest.fixture
def animations(pipeline):
    return sorted((json.loads(p.read_text()) for p in (ROOT / "manifests/animations").glob("*.json")),
                  key=lambda m: m["id"])
