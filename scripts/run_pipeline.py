#!/usr/bin/env python3
"""Thin workflow entry point: run the CLI with a timeout and a job id."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["process-models", "process-animations", "build-index"])
    ap.add_argument("files", nargs="*")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--timeout", type=int, default=900)
    args = ap.parse_args()
    env = dict(os.environ, PYTHONPATH=str(ROOT), BLENDER_DISABLE_AUTOEXEC="1",
               PYTHONUNBUFFERED="1")
    cmd = [sys.executable, "-m", "processing.cli", args.command]
    cmd += ["--all"] if args.all else args.files
    if args.command != "build-index":
        cmd += ["--job-id", datetime.now(timezone.utc).strftime("ci-%Y%m%dT%H%M%SZ")]
    proc = subprocess.run(cmd, cwd=ROOT, env=env, timeout=args.timeout)
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
