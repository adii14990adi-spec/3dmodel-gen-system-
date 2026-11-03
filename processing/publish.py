"""Atomic publication of processed outputs, reports, manifests and job records."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from processing.common import path_for, read_json, write_bytes_atomic, write_json


def publish_asset(asset_id: str, kind: str, glb: bytes | None, metadata: dict[str, Any],
                  report: dict[str, Any], report_md: str, manifest: dict[str, Any]) -> dict[str, str]:
    sub = "models" if kind == "model" else "animations"
    out_dir = path_for("processed", sub, asset_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, str] = {}
    if glb is not None:
        p = out_dir / f"{asset_id}.glb"
        write_bytes_atomic(p, glb)
        paths["processed"] = str(p)
    meta_path = out_dir / f"{asset_id}.metadata.json"
    write_json(meta_path, metadata)
    paths["metadata"] = str(meta_path)

    report_dir = path_for("reports", sub)
    report_dir.mkdir(parents=True, exist_ok=True)
    write_json(report_dir / f"{asset_id}.json", report)
    (report_dir / f"{asset_id}.md").write_text(report_md, encoding="utf-8")
    paths["report"] = str(report_dir / f"{asset_id}.json")

    manifest_dir = path_for("manifests", sub)
    manifest_dir.mkdir(parents=True, exist_ok=True)
    write_json(manifest_dir / f"{asset_id}.json", manifest)
    paths["manifest"] = str(manifest_dir / f"{asset_id}.json")
    return paths


def build_index() -> dict[str, Any]:
    models: list[dict[str, Any]] = []
    animations: list[dict[str, Any]] = []
    for sub, bucket in (("models", models), ("animations", animations)):
        directory = path_for("manifests", sub)
        if not directory.is_dir():
            continue
        for p in sorted(directory.glob("*.json")):
            bucket.append(read_json(p))
    jobs: list[dict[str, Any]] = []
    jobs_dir = path_for("reports") / "jobs"
    if jobs_dir.is_dir():
        for p in sorted(jobs_dir.glob("*.json")):
            jobs.append(read_json(p))
    release = path_for("manifests") / "releases" / "latest.json"
    index = {
        "schema_version": "1.0.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "processing_version": "1.0.0",
        "counts": {"models": len(models), "animations": len(animations)},
        "models": models, "animations": animations, "jobs": jobs,
        "release": read_json(release) if release.is_file() else None,
    }
    write_json(path_for("manifests") / "index.json", index)
    return index


def record_job(job_id: str, kind: str, status: str, assets: list[str], message: str = "") -> None:
    write_json(path_for("reports") / "jobs" / f"{job_id}.json", {
        "job_id": job_id, "kind": kind, "status": status, "assets": assets,
        "message": message, "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
    })
