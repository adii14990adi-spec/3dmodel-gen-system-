from __future__ import annotations


def test_required_endpoints(client, models, animations):
    paths = ["/api/v1/health", "/api/v1/assets", "/api/v1/models", "/api/v1/animations",
             "/api/v1/releases/latest", "/api/v1/reports/models/" + models[0]["id"]]
    for p in paths:
        r = client.get(p)
        assert r.status_code == 200, (p, r.text[:200])
    assert isinstance(client.get("/api/v1/assets").json(), list)
    assert client.get("/api/v1/models").json()["count"] >= 1


def test_asset_detail_and_download(client, models):
    m = next(x for x in models if (x.get("processed") or {}).get("path"))
    r = client.get(f"/api/v1/models/{m['id']}")
    assert r.status_code == 200 and r.json()["id"] == m["id"]
    d = client.get(f"/api/v1/assets/{m['id']}/download")
    assert d.status_code == 200
    assert d.content[:4] == b"glTF"
    assert d.headers["content-type"].startswith("model/gltf-binary")
    assert d.headers.get("x-asset-sha256", "") == (m["processed"] or {}).get("sha256", "")


def test_compatible_animations_playable_semantics(client, models, animations):
    mid = next(x["id"] for x in models if x.get("skeleton", {}).get("present"))
    r = client.get(f"/api/v1/models/{mid}/compatible-animations")
    assert r.status_code == 200
    for a in r.json()["animations"]:
        if a["playable"]:
            assert a["compatible"]
            assert a["target_character_id"] == mid
            assert a["retarget_status"] in ("completed", "passthrough_identical_skeleton")
    assert any(not a["compatible"] for a in r.json()["animations"]), "quadruped must not be compatible"


def test_processing_jobs_and_reports(client, pipeline):
    jobs = sorted((pipeline / "reports/jobs").glob("*.json"))
    assert jobs
    import json
    jid = json.loads(jobs[0].read_text())["job_id"]
    assert client.get(f"/api/v1/processing-jobs/{jid}").status_code == 200
    assert client.get("/api/v1/processing-jobs/nope").status_code == 404


def test_console_is_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "Operations console" in r.text


def test_signed_url_roundtrip(client, models):
    m = next(x for x in models if (x.get("processed") or {}).get("path"))
    r = client.post(f"/api/v1/assets/{m['id']}/signed-url")
    assert r.status_code == 200
    url = r.json()["url"]
    assert "signature=" in url
    fetched = client.get(url.replace("http://testserver", ""))
    assert fetched.status_code == 200 and fetched.content[:4] == b"glTF"
