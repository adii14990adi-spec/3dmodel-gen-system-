"""FastAPI metadata API for 3D Asset Studio.

Runners are temporary: this service reads published manifests + SQLite metadata,
and serves processed GLBs from a persistent storage driver (local or S3).
No GitHub PATs, storage credentials or signing secrets are ever exposed.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from backend.config import settings
from backend.database.db import query, query_one, sync_from_manifests
from backend.security import (make_signed_url, rate_limit, require_api_key,
                              safe_resolve, verify_signed_url)
from backend.storage import storage
from processing.common import ROOT, path_for, read_json
from processing.validate import validator_available
from processing import __version__

API = "/api/v1"
STATIC = Path(__file__).parent / "static"


def _asset_manifest(asset_id: str) -> dict[str, Any]:
    for sub in ("models", "animations"):
        p = path_for("manifests", sub, f"{asset_id}.json")
        if p.is_file():
            return read_json(p)
    raise HTTPException(status_code=404, detail=f"asset {asset_id} not found")


def _summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"], "kind": row["kind"], "name": row["name"],
        "category": row["category"] or "", "source_path": row["source_path"] or "",
        "source_format": row["source_format"] or "",
        "source_sha256": row["source_sha256"] or "",
        "processed_path": row["processed_path"],
        "processed_bytes": row["processed_bytes"] or 0,
        "processed_sha256": row["processed_sha256"] or "",
        "validation_status": row["validation_status"] or "",
        "humanoid": bool(row["humanoid"]),
        "retarget_status": row["retarget_status"] or "",
        "target_character_id": row["target_character_id"] or "",
        "updated_at": row["updated_at"] or "",
        "download_reference": row["download_reference"] or "",
    }


def _playable(manifest: dict[str, Any], model_id: str) -> tuple[bool, str]:
    """Skeleton compatibility alone is NOT enough: an exported GLB must exist."""
    processed = (manifest.get("processed") or {}).get("path")
    target = manifest.get("target_character_id", "")
    status = manifest.get("retarget_status", "")
    if not processed:
        return False, "no runtime GLB was exported for this animation"
    if target != model_id:
        return False, f"exported for a different target character ({target or 'none'})"
    if status not in ("completed", "passthrough_identical_skeleton"):
        return False, f"retarget status is {status or 'not set'}"
    if (manifest.get("validation") or {}).get("status") == "ERROR":
        return False, "runtime GLB failed validation"
    return True, ""


def create_app(sync: bool = True) -> FastAPI:
    app = FastAPI(title="3D Asset Studio API", version=__version__,
                  docs_url="/docs", redoc_url="/redoc")
    if sync:
        try:
            sync_from_manifests()
        except Exception as exc:  # pragma: no cover - startup resilience
            print(f"[warn] manifest sync failed: {exc}")

    guard = [Depends(require_api_key), Depends(rate_limit)]

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def console() -> HTMLResponse:
        index = STATIC / "index.html"
        if not index.is_file():
            return HTMLResponse("<h1>3D Asset Studio</h1><p>Console not installed.</p>")
        return HTMLResponse(index.read_text(encoding="utf-8"))

    @app.get(f"{API}/health", tags=["system"], dependencies=guard)
    def health() -> dict[str, Any]:
        cfg = settings()
        return {
            "status": "ok", "version": __version__,
            "authentication": cfg.auth_description,
            "storage_driver": storage().name,
            "validator": "Khronos" if validator_available() else "unavailable",
            "assets": {
                "models": len(query("SELECT id FROM assets WHERE kind='model'")),
                "animations": len(query("SELECT id FROM assets WHERE kind='animation'")),
            },
        }

    @app.get(f"{API}/assets", tags=["assets"], dependencies=guard)
    def assets(kind: str | None = None, search: str = "", sort: str = "name",
               limit: int = Query(default=200, le=1000)) -> list[dict[str, Any]]:
        sql = "SELECT * FROM assets WHERE 1=1"
        params: list[Any] = []
        if kind in ("model", "animation"):
            sql += " AND kind=?"; params.append(kind)
        if search:
            sql += " AND (name LIKE ? OR id LIKE ? OR category LIKE ?)"
            params += [f"%{search}%"] * 3
        sql += " ORDER BY " + {"name": "name", "recent": "updated_at DESC",
                               "size": "processed_bytes DESC", "status": "validation_status"}.get(sort, "name")
        sql += " LIMIT ?"; params.append(limit)
        return [_summary(r) for r in query(sql, tuple(params))]

    @app.get(f"{API}/models", tags=["models"], dependencies=guard)
    def models(search: str = "", sort: str = "name") -> dict[str, Any]:
        rows = query("SELECT * FROM assets WHERE kind='model' AND (name LIKE ? OR id LIKE ?) "
                     "ORDER BY " + {"name": "name", "recent": "updated_at DESC",
                                    "size": "processed_bytes DESC"}.get(sort, "name"),
                     (f"%{search}%", f"%{search}%"))
        return {"count": len(rows), "models": [_summary(r) for r in rows]}

    @app.get(f"{API}/animations", tags=["animations"], dependencies=guard)
    def animations(search: str = "", sort: str = "name") -> dict[str, Any]:
        rows = query("SELECT * FROM assets WHERE kind='animation' AND (name LIKE ? OR id LIKE ?) "
                     "ORDER BY " + {"name": "name", "recent": "updated_at DESC",
                                    "size": "processed_bytes DESC"}.get(sort, "name"),
                     (f"%{search}%", f"%{search}%"))
        return {"count": len(rows), "animations": [_summary(r) for r in rows]}

    @app.get(f"{API}/assets/{{asset_id}}", tags=["assets"], dependencies=guard)
    def asset_detail(asset_id: str) -> dict[str, Any]:
        return _asset_manifest(asset_id)

    @app.get(f"{API}/models/{{model_id}}", tags=["models"], dependencies=guard)
    def model_detail(model_id: str) -> dict[str, Any]:
        m = _asset_manifest(model_id)
        if m.get("kind") != "model":
            raise HTTPException(status_code=404, detail="not a model")
        return m

    @app.get(f"{API}/animations/{{animation_id}}", tags=["animations"], dependencies=guard)
    def animation_detail(animation_id: str) -> dict[str, Any]:
        m = _asset_manifest(animation_id)
        if m.get("kind") != "animation":
            raise HTTPException(status_code=404, detail="not an animation")
        return m

    @app.get(f"{API}/assets/{{asset_id}}/metadata", tags=["assets"], dependencies=guard)
    def asset_metadata(asset_id: str) -> dict[str, Any]:
        m = _asset_manifest(asset_id)
        ref = m.get("metadata_path")
        if not ref:
            raise HTTPException(status_code=404, detail="metadata not published")
        path = safe_resolve(ref)
        if not path.is_file():
            raise HTTPException(status_code=404, detail="metadata file missing")
        return read_json(path)

    @app.get(f"{API}/assets/{{asset_id}}/download", tags=["downloads"], dependencies=guard)
    def download(asset_id: str, expires: str = "", signature: str = "") -> Response:
        if expires or signature:
            if not verify_signed_url(asset_id, expires, signature, settings()):
                raise HTTPException(status_code=403, detail="signed URL expired or invalid")
        m = _asset_manifest(asset_id)
        ref = (m.get("processed") or {}).get("path")
        if not ref:
            raise HTTPException(status_code=404, detail="no processed GLB for this asset")
        driver = storage()
        if not driver.exists(ref):
            raise HTTPException(status_code=404, detail="processed object not found in storage")
        external = driver.external_url(ref)
        if external:
            return RedirectResponse(external, status_code=302)
        return FileResponse(str(driver.path(ref)), media_type="model/gltf-binary",
                            filename=f"{asset_id}.glb",
                            headers={"X-Asset-Sha256": (m.get("processed") or {}).get("sha256", ""),
                                     "Cache-Control": "private, max-age=300"})

    @app.post(f"{API}/assets/{{asset_id}}/signed-url", tags=["downloads"], dependencies=guard)
    def signed_url(asset_id: str, request: Request) -> dict[str, Any]:
        m = _asset_manifest(asset_id)
        if not (m.get("processed") or {}).get("path"):
            raise HTTPException(status_code=404, detail="no processed GLB for this asset")
        cfg = settings()
        external = storage().external_url((m.get("processed") or {}).get("path", ""))
        if external:
            return {"url": external, "expires_at": 0, "external": True}
        root = str(request.base_url).rstrip("/") + API
        base = cfg.public_base_url.rstrip("/") if cfg.public_base_url else root
        if not base.rstrip("/").endswith(API):
            base = base.rstrip("/") + API
        url, exp = make_signed_url(base.rstrip("/") + "/", asset_id, cfg)
        return {"url": url, "expires_at": exp, "external": False}

    @app.get(f"{API}/models/{{model_id}}/compatible-animations", tags=["compatibility"], dependencies=guard)
    def compatible_animations(model_id: str) -> dict[str, Any]:
        _asset_manifest(model_id)
        result: list[dict[str, Any]] = []
        for p in sorted(path_for("manifests", "animations").glob("*.json")):
            m = read_json(p)
            records = [r for r in m.get("compatibility", []) if r.get("target_character_id") == model_id]
            compatible = any(r.get("status") == "compatible" for r in records)
            playable, reason = _playable(m, model_id)
            clips = (m.get("animations") or {}).get("clips", [])
            result.append({
                "id": m["id"], "name": m.get("name", m["id"]),
                "category": m.get("category", ""),
                "duration_seconds": clips[0].get("duration_seconds", 0) if clips else 0,
                "frame_rate": clips[0].get("frame_rate") if clips else None,
                "root_motion": clips[0].get("root_motion", False) if clips else False,
                "retarget_status": m.get("retarget_status", ""),
                "target_character_id": m.get("target_character_id", ""),
                "compatible": compatible, "playable": compatible and playable,
                "reason": "" if (compatible and playable) else (reason or "not compatible with this character"),
                "download_reference": m.get("download_reference", ""),
            })
        result.sort(key=lambda r: (not r["playable"], r["name"]))
        return {"model_id": model_id, "found": True, "count": len(result), "animations": result}

    @app.get(f"{API}/processing-jobs/{{job_id}}", tags=["jobs"], dependencies=guard)
    def processing_job(job_id: str) -> dict[str, Any]:
        row = query_one("SELECT * FROM jobs WHERE job_id=?", (job_id,))
        if not row:
            raise HTTPException(status_code=404, detail="job not found")
        import json
        row["assets"] = json.loads(row.get("assets") or "[]")
        return row

    @app.get(f"{API}/releases/latest", tags=["releases"], dependencies=guard)
    def latest_release() -> dict[str, Any]:
        path = path_for("manifests", "releases", "latest.json")
        if path.is_file():
            return read_json(path)
        index = path_for("manifests", "index.json")
        if index.is_file():
            idx = read_json(index)
            return {"status": "local", "version": idx.get("processing_version", __version__),
                    "generated_at": idx.get("generated_at"), "counts": idx.get("counts", {}),
                    "note": "No signed release published yet; local manifests are authoritative."}
        return {"status": "empty", "version": __version__, "counts": {"models": 0, "animations": 0}}

    @app.get(f"{API}/reports/{{kind}}/{{asset_id}}", tags=["reports"], dependencies=guard)
    def validation_report(kind: str, asset_id: str) -> dict[str, Any]:
        if kind not in ("models", "animations"):
            raise HTTPException(status_code=400, detail="kind must be models or animations")
        path = path_for("reports", kind, f"{asset_id}.json")
        if not path.is_file():
            raise HTTPException(status_code=404, detail="report not found")
        return read_json(path)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code,
                            content={"error": {"status": exc.status_code, "detail": exc.detail}})

    return app


app = create_app(sync=False)
