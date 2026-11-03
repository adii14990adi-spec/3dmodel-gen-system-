"""SQLite metadata store populated from published manifests (never from guesses)."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from backend.config import settings
from processing.common import path_for, read_json

SCHEMA = """
CREATE TABLE IF NOT EXISTS assets (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, name TEXT NOT NULL, category TEXT,
  source_path TEXT, source_format TEXT, source_sha256 TEXT,
  processed_path TEXT, processed_sha256 TEXT, processed_bytes INTEGER,
  validation_status TEXT, humanoid INTEGER, retarget_status TEXT,
  target_character_id TEXT, updated_at TEXT, download_reference TEXT
);
CREATE TABLE IF NOT EXISTS compatibility (
  animation_id TEXT NOT NULL, target_character_id TEXT NOT NULL,
  status TEXT, score REAL, role_coverage REAL, reasons TEXT,
  PRIMARY KEY (animation_id, target_character_id)
);
CREATE TABLE IF NOT EXISTS jobs (
  job_id TEXT PRIMARY KEY, kind TEXT, status TEXT, assets TEXT,
  started_at TEXT, finished_at TEXT, message TEXT
);
CREATE TABLE IF NOT EXISTS reports (
  asset_id TEXT PRIMARY KEY, kind TEXT, status TEXT, path TEXT,
  error_count INTEGER, warning_count INTEGER
);
CREATE INDEX IF NOT EXISTS idx_assets_kind ON assets(kind);
"""


def connect() -> sqlite3.Connection:
    db = settings().database_path
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def query(sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    with connect() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def query_one(sql: str, params: tuple = ()) -> dict[str, Any] | None:
    rows = query(sql, params)
    return rows[0] if rows else None


def sync_from_manifests() -> dict[str, int]:
    """Ingest every published manifest, report and job record. Idempotent."""
    counts = {"models": 0, "animations": 0, "jobs": 0, "reports": 0}
    with connect() as conn:
        for kind, sub in (("model", "models"), ("animation", "animations")):
            directory = path_for("manifests", sub)
            if not directory.is_dir():
                continue
            for p in sorted(directory.glob("*.json")):
                m = read_json(p)
                skeleton = m.get("skeleton") or {}
                conn.execute(
                    "INSERT OR REPLACE INTO assets "
                    "(id,kind,name,category,source_path,source_format,source_sha256,"
                    "processed_path,processed_sha256,processed_bytes,validation_status,"
                    "humanoid,retarget_status,target_character_id,updated_at,download_reference) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        m["id"], kind, m.get("name", m["id"]), m.get("category", ""),
                        (m.get("source") or {}).get("path", ""),
                        (m.get("source") or {}).get("format", ""),
                        (m.get("source") or {}).get("sha256", ""),
                        (m.get("processed") or {}).get("path"),
                        (m.get("processed") or {}).get("sha256"),
                        (m.get("processed") or {}).get("bytes", 0),
                        (m.get("validation") or {}).get("status", "UNKNOWN"),
                        1 if skeleton.get("humanoid") else 0,
                        m.get("retarget_status", ""),
                        m.get("target_character_id", ""), m.get("timestamp", ""),
                        m.get("download_reference", ""),
                    ),
                )
                counts[sub] += 1
                for rec in m.get("compatibility", []):
                    conn.execute(
                        "INSERT OR REPLACE INTO compatibility "
                        "(animation_id,target_character_id,status,score,role_coverage,reasons) "
                        "VALUES (?,?,?,?,?,?)",
                        (
                            m["id"], rec.get("target_character_id", ""),
                            rec.get("status", ""), rec.get("score", 0),
                            rec.get("role_coverage", 0), json.dumps(rec.get("reasons", [])),
                        ),
                    )
            report_dir = path_for("reports", sub)
            if report_dir.is_dir():
                for p in sorted(report_dir.glob("*.json")):
                    r = read_json(p)
                    summary = r.get("summary") or {}
                    conn.execute(
                        "INSERT OR REPLACE INTO reports "
                        "(asset_id,kind,status,path,error_count,warning_count) VALUES (?,?,?,?,?,?)",
                        (
                            r.get("asset_id", p.stem), kind, r.get("status", "UNKNOWN"),
                            str(p), summary.get("ERROR", 0), summary.get("WARNING", 0),
                        ),
                    )
                    counts["reports"] += 1
        jobs_dir = path_for("reports") / "jobs"
        if jobs_dir.is_dir():
            for p in sorted(jobs_dir.glob("*.json")):
                j = read_json(p)
                conn.execute(
                    "INSERT OR REPLACE INTO jobs "
                    "(job_id,kind,status,assets,started_at,finished_at,message) VALUES (?,?,?,?,?,?,?)",
                    (
                        j.get("job_id", p.stem), j.get("kind", ""), j.get("status", ""),
                        json.dumps(j.get("assets", [])), j.get("started_at", ""),
                        j.get("finished_at", ""), j.get("message", ""),
                    ),
                )
                counts["jobs"] += 1
        conn.commit()
    return counts
