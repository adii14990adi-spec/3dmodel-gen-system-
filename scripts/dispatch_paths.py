#!/usr/bin/env python3
"""Validate ``workflow_dispatch`` path input before it reaches the pipeline.

The ``paths`` dispatch input is attacker-controlled text. It is passed to the
workflow through an environment variable (never interpolated into shell code)
and handed to this script as a file, so the shell never parses it. This script
owns the whole contract:

Input format (unambiguous, one path per line)
    UTF-8 text, one repo-relative POSIX path per line, LF line endings, no blank
    lines. A single trailing newline is allowed. Because the delimiter is a
    newline rather than a space, file names containing spaces work.

Rejected (``DispatchPathError``, exit status 2, nothing written to stdout)
    * blank entries, NUL bytes, carriage returns, other ASCII control characters;
    * absolute paths (``/…``), Windows drive paths (``C:…``), UNC paths (``\\\\…``);
    * backslashes: the canonical form uses ``/`` only, so nothing is silently rewritten;
    * ``.``, ``..`` or empty segments, ``./`` prefixes and ``//`` separators;
    * anything not under the kind's asset root (``assets/models/`` or
      ``assets/animation/``), including other asset directories;
    * unsupported or unsafe extensions (the pipeline's format allowlist);
    * anything that is missing, not a regular file, or resolves outside the asset
      root (a symlink in ``assets/`` pointing elsewhere is rejected, not followed).

Output
    ``--null`` writes NUL-separated tokens to stdout, exactly like
    ``changed_files.py --null``, so ``xargs -0 -r`` keeps spaces, quotes and
    unicode intact. Duplicates keep their first occurrence only.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from processing.common import SUPPORTED_ANIMATIONS, SUPPORTED_MODELS

ASSET_ROOTS = {"model": "assets/models/", "animation": "assets/animation/"}
SUPPORTED_SUFFIXES = {"model": SUPPORTED_MODELS, "animation": SUPPORTED_ANIMATIONS}

# C0 controls plus DEL. `\n` never reaches an entry (it is the delimiter), `\r`
# and `\0` are rejected before splitting.
CONTROL_CHARS = frozenset(chr(code) for code in range(0x20)) | {chr(0x7F)}
DRIVE_PREFIX = re.compile(r"^[A-Za-z]:")


class DispatchPathError(ValueError):
    """Raised when dispatch input is malformed or points somewhere it may not."""


def split_entries(text: str) -> list[str]:
    """Split newline-delimited input into raw entries (no validation yet)."""
    if "\x00" in text:
        raise DispatchPathError("input contains a NUL byte")
    if "\r" in text:
        raise DispatchPathError("input contains a carriage return; use LF line endings")
    entries = text.split("\n")
    if entries and entries[-1] == "":
        entries.pop()  # tolerate one trailing newline from the editor/shell
    if not entries:
        raise DispatchPathError("no paths were provided")
    return entries


def validate_entry(kind: str, entry: str, root: Path = ROOT) -> str:
    """Validate one entry and return it unchanged (canonical form only)."""
    if kind not in ASSET_ROOTS:
        raise DispatchPathError(f"unknown kind: {kind!r}")
    shown = repr(entry)
    if not entry.strip():
        raise DispatchPathError(f"path entry {shown} is empty")
    bad = sorted({ch for ch in entry if ch in CONTROL_CHARS})
    if bad:
        raise DispatchPathError(f"path entry {shown} contains control characters {bad!r}")
    if entry.startswith("/") or entry.startswith("\\") or DRIVE_PREFIX.match(entry):
        raise DispatchPathError(f"path entry {shown} is absolute; use a repo-relative path")
    if "\\" in entry:
        raise DispatchPathError(f"path entry {shown} uses backslashes; use POSIX '/' separators")
    segments = entry.split("/")
    if any(segment in ("", ".", "..") for segment in segments):
        raise DispatchPathError(
            f"path entry {shown} has an empty, '.' or '..' segment")

    prefix = ASSET_ROOTS[kind]
    if not entry.startswith(prefix) or len(entry) == len(prefix):
        raise DispatchPathError(f"path entry {shown} is not under {prefix}")
    suffix = Path(entry).suffix.lower()
    if suffix in {"", "."} or suffix not in SUPPORTED_SUFFIXES[kind]:
        allowed = ", ".join(sorted(SUPPORTED_SUFFIXES[kind]))
        raise DispatchPathError(
            f"path entry {shown} has an unsupported extension {suffix or '(none)'}; "
            f"{kind} sources must end in one of {allowed}")

    base = Path(root).resolve()
    asset_root = (base / prefix).resolve()
    resolved = (base / entry).resolve()
    if asset_root not in resolved.parents:
        # `..` can no longer appear here, so this is a symlink leaving the root.
        raise DispatchPathError(
            f"path entry {shown} resolves outside {prefix} (symlink escape)")
    if not resolved.is_file():
        raise DispatchPathError(f"path entry {shown} is not a regular file under {prefix}")
    return entry


def resolve_paths(kind: str, text: str, root: Path = ROOT) -> list[str]:
    """Parse and validate dispatch input, returning paths in input order."""
    seen: set[str] = set()
    paths: list[str] = []
    for entry in split_entries(text):
        path = validate_entry(kind, entry, root)
        if path not in seen:
            seen.add(path)
            paths.append(path)
    return paths


def emit(tokens: list[str], null: bool) -> None:
    if null:
        sys.stdout.write("".join(f"{token}\0" for token in tokens))
    else:
        sys.stdout.write("".join(f"{token}\n" for token in tokens))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Validate workflow_dispatch path input.")
    ap.add_argument("--kind", required=True, choices=sorted(ASSET_ROOTS))
    ap.add_argument("--paths-file", default="-",
                    help="UTF-8 file with one path per line ('-' for stdin)")
    ap.add_argument("--null", action="store_true",
                    help="NUL-separate output so paths with spaces/quotes survive the shell")
    ap.add_argument("--root", default=None, help="repository root (defaults to this checkout)")
    args = ap.parse_args(argv)

    try:
        raw = (sys.stdin.buffer.read() if args.paths_file == "-"
               else Path(args.paths_file).read_bytes())
    except OSError as exc:
        print(f"::error::cannot read dispatch input: {exc}", file=sys.stderr)
        return 2
    try:
        paths = resolve_paths(args.kind, raw.decode("utf-8"),
                              Path(args.root) if args.root else ROOT)
    except UnicodeDecodeError:
        print("::error::dispatch input is not valid UTF-8", file=sys.stderr)
        return 2
    except DispatchPathError as exc:
        print(f"::error::rejected dispatch input: {exc}", file=sys.stderr)
        return 2
    emit(paths, args.null)
    print(f"Validated {len(paths)} {args.kind} path(s)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
