from __future__ import annotations

from processing.bvh import load_bvh
from processing.glb import load_document, inspect_document, parse_glb, write_glb
from processing.retarget import build_bone_map, retarget


def test_retarget_produces_real_single_clip_glb(pipeline):
    target = pipeline / "assets/models/characters/warrior.glb"
    bvh = load_bvh(pipeline / "assets/animation/locomotion/walk.bvh")
    glb, meta = retarget(bvh, target, "walk")
    assert glb[:4] == b"glTF"
    assert meta["clip_name"] == "Retarget_walk"
    assert meta["mapped_bones"] >= 12
    assert meta["channel_count"] == meta["mapped_bones"] + (1 if meta["root_motion"] else 0)
    assert meta["duration_seconds"] > 0
    inspected = inspect_document(parse_glb(glb), "walk.glb")
    assert inspected["animations"]["clip_count"] == 1
    assert inspected["animations"]["clips"][0]["name"] == "Retarget_walk"
    assert inspected["skeleton"]["present"] is True
    assert inspected["geometry"]["triangle_count"] > 0
    # Re-serialising the parsed document must remain a valid GLB.
    assert parse_glb(write_glb(load_document(_write(glb))["json"], load_document(_write(glb))["bin"]))["json"]["asset"]["version"] == "2.0"


def _write(data):
    import tempfile
    from pathlib import Path
    f = Path(tempfile.gettempdir()) / "test_retarget.glb"
    f.write_bytes(data)
    return f


def test_bone_map_uses_canonical_roles(pipeline):
    bvh = load_bvh(pipeline / "assets/animation/locomotion/run.bvh")
    doc = load_document(pipeline / "assets/models/characters/scout.glb")["json"]
    mapping = build_bone_map(bvh, doc)
    assert len(mapping) >= 12
    assert bvh.root in mapping
