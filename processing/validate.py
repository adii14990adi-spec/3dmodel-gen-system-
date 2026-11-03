"""Official Khronos glTF Validator wrapper (Node binding) plus honest fallbacks."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from processing.common import ROOT, path_for

VALIDATOR_DIR = ROOT / "tools" / "gltf-validator"


def validator_available() -> bool:
    return (VALIDATOR_DIR / "node_modules" / "gltf-validator").is_dir() and shutil.which("node") is not None


def validate_glb(path: Path | str, timeout: int = 120) -> list[dict[str, Any]]:
    """Return real Khronos diagnostics; never invent a PASS when unavailable."""
    path = Path(path)
    checks: list[dict[str, Any]] = []
    if not validator_available():
        return [{"severity": "WARNING", "code": "VALIDATOR_UNAVAILABLE",
                 "description": "Khronos glTF Validator is not installed (npm ci in tools/gltf-validator)",
                 "asset": str(path), "location": "validator",
                 "suggested_fix": "Run npm ci in tools/gltf-validator or use the validation workflow."}]
    script = VALIDATOR_DIR / "validate.js"
    if not script.is_file():
        return [{"severity": "WARNING", "code": "VALIDATOR_UNAVAILABLE",
                 "description": "validate.js wrapper is missing", "asset": str(path),
                 "location": "validator", "suggested_fix": "Restore tools/gltf-validator/validate.js"}]
    proc = subprocess.run(["node", str(script), str(path)], capture_output=True, text=True, timeout=timeout)
    try:
        payload = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        payload = {}
    messages = (payload.get("issues") or {}).get("messages", []) if isinstance(payload.get("issues"), dict) else []
    for m in messages:
        code = m.get("code", "VALIDATOR_ISSUE")
        severity = (m.get("severity") or "Information").upper()
        sev = "ERROR" if severity == "ERROR" else ("WARNING" if severity == "WARNING" else "INFO")
        pointer = m.get("pointer", "")
        checks.append({
            "severity": sev, "code": code,
            "description": m.get("message") or code,
            "asset": str(path), "location": pointer or "glTF document",
            "component": code.split("_")[0].lower(),
            "suggested_fix": _fix_for(code),
        })
    if proc.returncode != 0 and not messages:
        checks.append({"severity": "WARNING", "code": "VALIDATOR_FAILED",
                       "description": (proc.stderr or "validator exited nonzero")[:400],
                       "asset": str(path), "location": "validator",
                       "suggested_fix": "Inspect the validator stderr; re-run npm ci."})
    if not checks:
        checks.append({"severity": "PASS", "code": "KHRONOS_VALID",
                       "description": "Khronos glTF Validator reported no issues",
                       "asset": str(path), "location": "glTF document",
                       "suggested_fix": ""})
    return checks


def _fix_for(code: str) -> str:
    fixes = {
        "UNRESOLVED_REFERENCE": "Supply the referenced buffer/image URI or embed it in the GLB.",
        "GLB_UNEXPECTED_END_OF_CHUNK_DATA": "Re-export the GLB; the BIN chunk is truncated.",
        "NODE_SKINNED_MESH_NON_ROOT": "Detach skinned meshes from parent nodes while preserving world transforms.",
        "MESH_PRIMITIVE_GENERATED_TANGENT_SPACE": "Generate tangents or supply a normal map without tangents.",
        "MISSING_NORMALS": "Generate vertex normals in the processed copy.",
        "EMPTY_ENTITY": "Remove empty arrays from the glTF JSON before export.",
        "ACCESSOR_VECTOR3_NON_UNIT": "Normalise normal vectors.",
        "INVALID_INDEX": "Correct out-of-range JOINTS/indices.",
    }
    return fixes.get(code, "Consult the Khronos validator message and repair the referenced glTF entity.")
