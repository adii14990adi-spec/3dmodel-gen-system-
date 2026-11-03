"""Shared helpers for headless Blender scripts. Assets are treated as untrusted data."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def args() -> list[str]:
    return sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []


def guard() -> None:
    """No embedded Python/Draco scripts are executed; only mesh/armature data is read."""
    import bpy  # noqa: F401
    os.environ.setdefault("BLENDER_DISABLE_AUTOEXEC", "1")


def finish(payload: dict) -> None:
    """Write JSON then hard-exit: bpy 4.2 as a module can segfault at interpreter teardown."""
    out = Path(args()[-1]) if args() else None
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False), flush=True)
    sys.stdout.flush()
    os._exit(0)
