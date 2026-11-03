#!/usr/bin/env python3
"""Atomic publication of processed assets to persistent object storage.

Runners are temporary; this script uploads to a staging prefix, verifies every
upload, then copies into the final prefix and publishes a manifest. Without
configured S3 credentials it exits successfully with a clear no-op message.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from processing.common import ROOT, path_for, sha256_file


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prefix", default="staging/local")
    ap.add_argument("--then-promote", action="store_true")
    ap.add_argument("--final-prefix", default="assets")
    args = ap.parse_args()
    bucket = os.environ.get("ASSET_STUDIO_S3_BUCKET", "")
    if not bucket:
        print("Object storage not configured (ASSET_STUDIO_S3_BUCKET unset); skipping publication.")
        return 0
    try:
        import boto3  # type: ignore
    except ImportError:
        print("boto3 is not installed; install with `pip install boto3` to publish.")
        return 1
    client = boto3.client("s3",
        endpoint_url=os.environ.get("ASSET_STUDIO_S3_ENDPOINT") or None,
        region_name=os.environ.get("ASSET_STUDIO_S3_REGION", "us-east-1"),
        aws_access_key_id=os.environ["ASSET_STUDIO_S3_ACCESS_KEY"],
        aws_secret_access_key=os.environ["ASSET_STUDIO_S3_SECRET_KEY"])
    objects = sorted(path_for("processed").rglob("*.glb"))
    staged: list[tuple[str, str, str, int]] = []
    for path in objects:
        rel = str(path.relative_to(ROOT)).replace("\\", "/")
        key = f"{args.prefix.strip('/')}/{rel}"
        client.upload_file(str(path), bucket, key)
        head = client.head_object(Bucket=bucket, Key=key)
        if head["ContentLength"] != path.stat().st_size:
            raise RuntimeError(f"size mismatch for {key}")
        staged.append((key, f"{args.final_prefix.strip('/')}/{rel}", sha256_file(path), path.stat().st_size))
    for key, final, digest, size in staged:
        if args.then_promote:
            client.copy_object(Bucket=bucket, Key=final,
                               CopySource={"Bucket": bucket, "Key": key})
    release = {
        "schema_version": "1.0.0", "status": "published" if args.then_promote else "staged",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "processing_version": "1.0.0", "bucket": bucket,
        "objects": [{"key": k, "final_key": f, "sha256": d, "bytes": b} for k, f, d, b in staged],
        "count": len(staged),
    }
    out = path_for("manifests", "releases", "latest.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(release, indent=2) + "\n", encoding="utf-8")
    client.put_object(Bucket=bucket, Key="manifests/releases/latest.json",
                      Body=json.dumps(release, indent=2).encode(), ContentType="application/json")
    print(f"Published {len(staged)} objects (promoted={args.then_promote}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
