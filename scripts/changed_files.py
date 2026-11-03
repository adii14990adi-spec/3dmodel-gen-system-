#!/usr/bin/env python3
"""Resolve changed source assets between two git refs for path-triggered Actions."""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from processing.common import SUPPORTED_ANIMATIONS, SUPPORTED_MODELS

def changed(base: str, head: str) -> list[str]:
    cmd = ["git", "diff", "--name-only", "--diff-filter=ACMR", base, head]
    out = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [line.strip() for line in out.splitlines() if line.strip()]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="HEAD~1")
    ap.add_argument("--head", default="HEAD")
    ap.add_argument("--kind", choices=["model", "animation"], default="model")
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()
    if args.all:
        print("--all"); return
    allowed = SUPPORTED_MODELS if args.kind == "model" else SUPPORTED_ANIMATIONS
    prefix = "assets/models/" if args.kind == "model" else "assets/animation/"
    files = [f for f in changed(args.base, args.head)
             if f.startswith(prefix) and Path(f).suffix.lower() in allowed]
    print(" ".join(files))


if __name__ == "__main__":
    main()
