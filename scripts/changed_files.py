#!/usr/bin/env python3
"""Resolve changed source assets between two git refs for path-triggered Actions.

Contract used by the workflows:

* prints ``--all`` when the diff cannot be trusted (first push / zero SHA,
  unknown ref, shallow history) so the job processes everything instead of
  silently skipping new assets;
* prints nothing when no supported source file changed (the job is skipped);
* prints the changed source paths otherwise, filtered by kind and allowlisted
  extension. Deleted files are never returned, renames are returned as the new
  path only, and paths are handled byte-for-byte (spaces, quotes, unicode) via
  ``--null``.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from processing.common import SUPPORTED_ANIMATIONS, SUPPORTED_MODELS

# Refs we are willing to hand to git. Anything else (empty, all-zero, or an
# option-looking string such as "--output=/tmp/x") is treated as untrusted and
# degrades to "process everything" rather than being passed to argv.
REF_PATTERN = re.compile(r"^(?:[0-9a-fA-F]{4,40}|[A-Za-z0-9][A-Za-z0-9._/@^~{}-]*)$")


def valid_ref(ref: str) -> bool:
    return bool(ref) and not ref.startswith("-") and bool(REF_PATTERN.match(ref))


def changed(base: str, head: str, repo: Path | None = None) -> list[str] | None:
    """Return changed paths, or None when the caller must process everything."""
    repository = Path(repo) if repo is not None else ROOT
    if head and not valid_ref(head):
        return None
    if not base or set(base) == {"0"}:
        # First push of a branch, or an event without a `before` SHA: there is
        # no reliable base to diff against. `HEAD~1` would only cover the tip
        # commit and silently skip the rest of the branch, so process all.
        return None
    if not valid_ref(base):
        return None
    # -z + core.quotepath=false keep paths byte-exact: git would otherwise
    # C-quote non-ASCII names ("\303\274..."), which never matches a prefix.
    cmd = ["git", "-c", "core.quotepath=false", "diff", "--name-only", "-z",
           "--diff-filter=ACMR", base, head, "--"]
    try:
        out = subprocess.run(cmd, cwd=repository, capture_output=True, text=True,
                             check=True).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        # Shallow/incomplete history, force push, or missing git: be safe.
        return None
    return [path for path in out.split("\0") if path]


def resolve(kind: str, base: str, head: str, repo: Path | None = None) -> list[str] | None:
    """Filter a diff down to supported source assets of one kind."""
    diff = changed(base, head, repo)
    if diff is None:
        return None
    allowed = SUPPORTED_MODELS if kind == "model" else SUPPORTED_ANIMATIONS
    prefix = "assets/models/" if kind == "model" else "assets/animation/"
    return sorted(path for path in diff
                  if path.startswith(prefix) and Path(path).suffix.lower() in allowed)


def emit(tokens: list[str], null: bool) -> None:
    if null:
        sys.stdout.write("".join(f"{token}\0" for token in tokens))
    else:
        print(" ".join(tokens))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="HEAD~1")
    ap.add_argument("--head", default="HEAD")
    ap.add_argument("--kind", choices=["model", "animation"], default="model")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--null", action="store_true",
                    help="NUL-separate output so paths with spaces/quotes survive the shell")
    ap.add_argument("--repo", default=None, help="repository to inspect (defaults to this checkout)")
    args = ap.parse_args()
    if args.all:
        tokens: list[str] = ["--all"]
    else:
        files = resolve(args.kind, args.base, args.head, args.repo)
        # None means "cannot trust the diff" (zero SHA, shallow history, bad ref).
        tokens = ["--all"] if files is None else files
    emit(tokens, args.null)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
