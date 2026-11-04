#!/usr/bin/env python3
"""Verify that the original uploads are byte-identical to the recorded hashes.

The organizer copies, never moves: ``docs/animation-library/sha256sums.txt`` is
written from the scan of ``assets/animation/Upload`` and this script recomputes
every hash so a modified, truncated or deleted original fails CI.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_sums(text: str) -> dict[str, str]:
    entries: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        digest, _, path = line.partition("  ")
        if len(digest) == 64 and path:
            entries[path] = digest
    return entries


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="assets/animation/Upload",
                        help="directory holding the untouched originals")
    parser.add_argument("--sums", default="docs/animation-library/sha256sums.txt")
    args = parser.parse_args(argv)

    sums_path = ROOT / args.sums
    if not sums_path.is_file():
        print(f"ERROR: no recorded hashes at {args.sums}", file=sys.stderr)
        return 2
    recorded = parse_sums(sums_path.read_text(encoding="utf-8"))
    archive = ROOT / args.root
    if not archive.is_dir():
        print(f"ERROR: archive {args.root} is missing", file=sys.stderr)
        return 2

    relevant = {path: digest for path, digest in recorded.items()
                if Path(path).is_relative_to(Path(args.root)) or args.root in path}
    if not relevant:
        print(f"ERROR: no recorded hashes apply to {args.root}", file=sys.stderr)
        return 2

    failures = 0
    for path, expected in sorted(relevant.items()):
        source = ROOT / path
        if not source.is_file():
            print(f"MISSING: {path}", file=sys.stderr)
            failures += 1
            continue
        actual = sha256(source)
        if actual != expected:
            print(f"CHANGED: {path} ({expected[:12]} -> {actual[:12]})", file=sys.stderr)
            failures += 1
    print(f"verified {len(relevant) - failures}/{len(relevant)} original upload(s)")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
