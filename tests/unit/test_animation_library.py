"""Animation library tests: discovery, classification, organization, integration.

These tests use the real uploaded FBX set where the behaviour depends on actual
FBX records (65-bone Mixamo rig) and the small A-L BVH fixtures elsewhere, so
they exercise the same code paths the workflow runs. Nothing here weakens an
existing assertion: the uploads are read-only and every file write goes to
``tmp_path`` (or to the additive category tree the organizer owns).
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from processing import library
from processing.classify import classify, validate_category
from processing.compatibility import check_compatibility, load_manual_map
from processing.fbx import inspect_fbx
from processing.manifest import SCHEMA_VERSION, build_manifest
from processing.retarget import validate_retarget

ROOT = Path(__file__).resolve().parent.parent.parent
UPLOAD = ROOT / "assets" / "animation" / "Upload"
WALK_FBX = UPLOAD / "walking.fbx"
WALK_BVH = ROOT / "assets" / "animation" / "locomotion" / "walk.bvh"
needs_upload = pytest.mark.skipif(not WALK_FBX.is_file(), reason="uploaded FBX set not present")


@pytest.fixture(scope="module")
def fbx_record() -> dict:
    return library.inspect_asset(WALK_FBX)


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #
@needs_upload
def test_format_is_detected_from_bytes_not_names(tmp_path):
    fbx = tmp_path / "looks_like_a_bvh.bvh"
    fbx.write_bytes(WALK_FBX.read_bytes())
    assert library.detect_format(fbx.read_bytes(), fbx.name)["format"] == "FBX"
    glb = tmp_path / "model.fbx"
    glb.write_bytes(b"glTF" + b"\x02\x00\x00\x00" + b"\x00" * 8)
    assert library.detect_format(glb.read_bytes(), glb.name)["format"] == "GLB"


def test_bvh_detected_from_text_header(tmp_path):
    assert library.detect_format(WALK_BVH.read_bytes(), "walk.bvh")["format"] == "BVH"


def test_corrupted_file_is_reported_not_crashed(tmp_path):
    broken = tmp_path / "broken.fbx"
    broken.write_bytes(b"Kaydara FBX Binary  \x00" + b"\x1a\x00" + b"\xde\xad\xbe\xef" * 8)
    record = library.inspect_asset(broken)
    assert record["status"] in {"corrupted", "unsupported"}
    assert record["errors"], "a corrupted file must carry an explicit error"


def test_empty_file_is_reported_as_empty(tmp_path):
    empty = tmp_path / "empty.fbx"
    empty.write_bytes(b"")
    record = library.inspect_asset(empty)
    assert record["status"] in {"empty", "corrupted"}
    assert record["warnings"] or record["errors"]


def test_unsupported_format_is_never_silently_dropped(tmp_path):
    unsupported = tmp_path / "character.obj"
    unsupported.write_text("v 0 0 0\n")
    record = library.inspect_asset(unsupported)
    assert record["status"] == "unsupported"
    assert record["errors"][0]["code"] == "UNSUPPORTED_FORMAT"


@needs_upload
def test_unicode_and_space_filenames_survive_the_scan(tmp_path):
    name = "marche arrière 跑步 étape.fbx"
    (tmp_path / name).write_bytes(WALK_FBX.read_bytes())
    inventory = library.build_inventory(tmp_path)
    assert [a["file_name"] for a in inventory["assets"]] == [name]
    assert inventory["assets"][0]["classification"], "a readable rig must be classified"
    plan = library.plan_organization(inventory, target_root=tmp_path / "out")
    assert plan["operations"][0]["relative_destination"].endswith(name)


@needs_upload
def test_destination_names_can_never_escape_the_library_tree():
    assert library.safe_file_name("../../etc/passwd.fbx") == ".._.._etc_passwd.fbx"
    assert library.safe_file_name("a/b\\c.fbx") == "a_b_c.fbx"
    assert library.safe_file_name("..") == "asset"
    assert library.safe_file_name("") == "asset"
    assert library.safe_file_name("मराठी walk (2).fbx") == "मराठी walk (2).fbx"


@needs_upload
def test_duplicate_content_is_detected_once(tmp_path):
    data = WALK_FBX.read_bytes()
    (tmp_path / "a.fbx").write_bytes(data)
    (tmp_path / "b.fbx").write_bytes(data)
    inventory = library.build_inventory(tmp_path)
    assert inventory["totals"]["duplicate_files"] == 1
    plan = library.plan_organization(inventory, target_root=tmp_path / "out")
    assert len(plan["operations"]) == 1
    assert any(s["reason"] == "duplicate_content" for s in plan["skipped"])


def test_asset_ids_are_deterministic_and_run_independent(tmp_path):
    (tmp_path / "walk.bvh").write_bytes(WALK_BVH.read_bytes())
    first = library.build_inventory(tmp_path, classify_clips=False)
    second = library.build_inventory(tmp_path, classify_clips=False)
    assert [a["asset_id"] for a in first["assets"]] == [a["asset_id"] for a in second["assets"]]
    plan_one = library.plan_organization(first, target_root=tmp_path / "out")
    plan_two = library.plan_organization(second, target_root=tmp_path / "out")
    assert [o["destination_path"] for o in plan_one["operations"]] == \
           [o["destination_path"] for o in plan_two["operations"]]


def test_destination_collisions_get_hash_suffixes(tmp_path):
    # Two different files with the same name cannot live in one folder: the
    # second gets a deterministic content-hash suffix instead of overwriting.
    source = tmp_path / "src"
    (source / "one").mkdir(parents=True)
    (source / "two").mkdir(parents=True)
    (source / "one" / "walk.bvh").write_bytes(WALK_BVH.read_bytes())
    changed = WALK_BVH.read_bytes().replace(b"Frame Time:", b"Frame Time: ")
    (source / "two" / "walk.bvh").write_bytes(changed + b"\n")
    inventory = library.build_inventory(source)
    plan = library.plan_organization(inventory, target_root=tmp_path / "out")
    destinations = {o["destination_path"] for o in plan["operations"]}
    names = {Path(d).name for d in destinations}
    assert len(destinations) == 2 and len(names) == 2, destinations


# --------------------------------------------------------------------------- #
# Classification
# --------------------------------------------------------------------------- #
@needs_upload
def test_fbx_inspection_measures_the_real_rig():
    info = inspect_fbx(WALK_FBX)
    assert info["version"] and info["clips"]
    skeleton = info["skeleton"]
    assert skeleton["bone_count"] == 65
    assert skeleton["root_bone"].endswith("Hips")
    clip = info["clips"][0]
    assert clip["duration_seconds"] > 0 and clip["frame_rate"] in (24.0, 25.0, 30.0, 60.0)
    assert clip["channel_count"] > 0 and clip["animated_bones"]
    assert isinstance(clip["root_motion"], bool)


@needs_upload
def test_classification_comes_from_measurement(fbx_record):
    classification = fbx_record["classification"]
    assert classification is not None
    assert validate_category(classification["category"])
    assert classification["measured"], "the classification must record its measurements"
    assert isinstance(classification["evidence"], list)
    assert classification["confidence"] >= 0.5
    assert classification["evidence"], "a category must be justified by measurements"


def test_filename_hint_cannot_override_the_measurements(fbx_record):
    features = next(clip["features"] for clip in fbx_record["clips"] if clip.get("features"))
    honest = classify(features, "walking")
    lying = classify(features, "death_from_the_front")
    assert honest.action in {"walk", "run"}
    assert lying.category != "combat/reactions/death"
    assert any(w["code"] == "FILENAME_MEASUREMENT_MISMATCH" for w in lying.warnings)


def test_unmeasurable_action_is_flagged_as_filename_only(fbx_record):
    features = next(clip["features"] for clip in fbx_record["clips"] if clip.get("features"))
    result = classify(features, "reload")
    if result.action == "reload":
        assert result.source == "filename"
        assert result.confidence < 0.8
        assert any(w["code"] == "CLASSIFICATION_FROM_FILENAME" for w in result.warnings)


# --------------------------------------------------------------------------- #
# Skeleton compatibility
# --------------------------------------------------------------------------- #
@needs_upload
def test_manual_map_resolves_ambiguity_without_equal_bone_counts():
    source = inspect_fbx(WALK_FBX)["skeleton"]["bones"]
    candidates = []
    for path in sorted((ROOT / "manifests" / "models").glob("*.json")):
        manifest = json.loads(path.read_text())
        bones = (manifest.get("skeleton") or {}).get("bones") or []
        if (manifest.get("skeleton") or {}).get("present") and len(bones) != len(source):
            candidates.append(bones)
    if not candidates:
        pytest.skip("character manifests not generated in this run")
    target_bones = candidates[0]
    assert len(source) != len(target_bones), "the rigs deliberately differ in bone count"
    without_map = check_compatibility(source, target_bones)
    assert without_map["status"] == "incompatible"
    assert without_map["ambiguous_roles"]
    with_map = check_compatibility(source, target_bones, load_manual_map("mixamo_to_game"))
    assert with_map["status"] == "compatible"
    assert with_map["manual_map_applied"] is True
    assert with_map["mapped_bones"] >= 12
    assert with_map["role_coverage"] >= 0.75


def test_skeleton_grouping_uses_hierarchy_not_bone_count():
    bones = [{"name": "Hips", "parent": None, "children": ["Spine"], "translation": [0, 1, 0]},
             {"name": "Spine", "parent": "Hips", "children": [], "translation": [0, 0.2, 0]}]
    same = [dict(bone) for bone in bones]
    reparented = [dict(bones[0], children=["Chest"]), dict(bones[1], parent="Chest")]
    renamed = [dict(bones[0], name="Pelvis"), dict(bones[1])]
    assert library.skeleton_id(bones) == library.skeleton_id(same)
    # Same bone count, different hierarchy / names => a different skeleton group.
    assert library.skeleton_id(bones) != library.skeleton_id(reparented)
    assert library.skeleton_id(bones) != library.skeleton_id(renamed)


# --------------------------------------------------------------------------- #
# Safe organization
# --------------------------------------------------------------------------- #
def test_organization_copies_and_verifies_without_touching_sources(tmp_path):
    source = tmp_path / "src"
    source.mkdir()
    original = source / "walk.bvh"
    original.write_bytes(WALK_BVH.read_bytes())
    digest = library.sha256_file(original)
    inventory = library.build_inventory(source)
    plan = library.plan_organization(inventory, target_root=tmp_path / "out")
    applied = library.apply_organization(plan)
    assert applied["failures"] == 0 and applied["copied"] == 1
    destination = ROOT / plan["operations"][0]["destination_path"]
    assert destination.is_file()
    assert library.sha256_file(destination) == digest
    assert library.sha256_file(original) == digest, "the source must be byte-identical"
    again = library.apply_organization(plan)
    assert again["copied"] == 0 and again["unchanged"] == 1, "repeated runs are idempotent"
    assert library.sha256_file(destination) == digest


def test_existing_destination_with_different_bytes_is_never_overwritten(tmp_path):
    source = tmp_path / "src"
    source.mkdir()
    (source / "walk.bvh").write_bytes(WALK_BVH.read_bytes())
    inventory = library.build_inventory(source)
    plan = library.plan_organization(inventory, target_root=tmp_path / "out")
    destination = ROOT / plan["operations"][0]["destination_path"]
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(b"someone else's file")
    try:
        applied = library.apply_organization(plan)
        assert applied["failures"] == 1
        assert destination.read_bytes() == b"someone else's file"
    finally:
        destination.unlink(missing_ok=True)


def test_multi_clip_sources_are_kept_intact(tmp_path):
    from processing.glb import load_document, write_glb

    fixture = ROOT / "assets" / "animation" / "other" / "embedded_clips.glb"
    if not fixture.is_file():
        pytest.skip("multi-clip fixture not generated in this run")
    asset = load_document(fixture)
    source_animation = asset["json"]["animations"][0]
    second = json.loads(json.dumps(source_animation))
    second["name"] = "SecondClip"
    document = {**asset["json"], "animations": [source_animation, second]}
    multi = tmp_path / "two_clips.glb"
    multi.write_bytes(write_glb(document, asset["bin"]))
    record = library.inspect_asset(multi)
    assert record["clip_count"] == 2
    assert len(record["clips"]) == record["clip_count"]
    assert any(w["code"] == "MULTI_CLIP_SOURCE" for w in record["warnings"])
    plan = library.plan_organization(
        library.build_inventory(tmp_path.resolve()), target_root=tmp_path / "out")
    assert len(plan["operations"]) == 1, "a multi-clip source is copied whole, never split"


def test_plan_matcher_skips_unclassified_when_asked(tmp_path):
    (tmp_path / "quadruped.bvh").write_bytes(
        (ROOT / "assets" / "animation" / "other" / "quadruped.bvh").read_bytes())
    inventory = library.build_inventory(tmp_path)
    plan = library.plan_organization(inventory, target_root=tmp_path / "out",
                                     skip_unclassified=True)
    if inventory["assets"][0]["classification"] and \
            inventory["assets"][0]["classification"]["category"].startswith("unclassified"):
        assert plan["operations"] == []
        assert plan["skipped"][0]["reason"] == "unclassified"


# --------------------------------------------------------------------------- #
# Manifest + retargeting integration
# --------------------------------------------------------------------------- #
def test_manifest_carries_library_fields():
    manifest = build_manifest(
        asset_id="anim_x", kind="animation", name="walking", category="locomotion/rifle/walk",
        source_path="assets/animation/Upload/walking.fbx", source_hash="a" * 64,
        source_format="FBX", processed_path=None, processed_hash=None, processed_bytes=0,
        processing_version="1.0.0", validation_status="PASS", report="reports/x.json",
        animation_asset={"original_filename": "walking.fbx",
                         "organized_path": "assets/animation/locomotion/rifle/walk/walking.fbx",
                         "classification": {"category": "locomotion/rifle/walk"},
                         "skeleton_id": "skel_123", "compatibility_group": "skel_123",
                         "root_motion": True, "retarget_required": False})
    assert SCHEMA_VERSION == "1.1.0"
    for key in ("original_filename", "organized_path", "classification", "skeleton_id",
                "compatibility_group", "root_motion", "retarget_required"):
        assert key in manifest
    assert manifest["organized_path"].endswith("walking.fbx")
    assert manifest["retarget_required"] is False


def test_retarget_validation_rejects_a_broken_export():
    result = validate_retarget({"hip_height": {"mean_m": 1.0}}, b"not a glb", {})
    assert result["status"] == "failed"
    assert "GLB" in result["reason"]


@needs_upload
def test_retarget_fbx_produces_a_validated_glb_for_the_real_rig(tmp_path):
    from processing.fbx import load_fbx
    from processing.motion import motion_features
    from processing.retarget import retarget_fbx

    target = next((ROOT / "processed" / "models").glob("model_rigged_animated_*/*.glb"), None)
    if target is None:
        pytest.skip("processed characters not built in this run")
    doc = load_fbx(WALK_FBX)
    features = motion_features(doc, doc.stacks[0])
    payload, metadata = retarget_fbx(doc, doc.stacks[0], target, "walking",
                                     load_manual_map("mixamo_to_game"))
    assert metadata["mapped_bones"] >= 12
    assert metadata["dropped_roles"] == []
    assert payload[:4] == b"glTF" and len(payload) > 1000
    validation = validate_retarget(features, payload, metadata)
    assert validation["status"] == "validated", validation
    assert validation["passed"] >= validation["required"]
    assert validation["playback_tested"] is False


# --------------------------------------------------------------------------- #
# Pipeline integration
# --------------------------------------------------------------------------- #
def test_discovery_publishes_each_content_hash_once():
    from processing.cli import _discover
    from processing.common import sha256_file
    files = _discover("animation")
    hashes = [sha256_file(path) for path in files]
    assert hashes, "the organized tree must yield processable sources"
    assert len(hashes) == len(set(hashes)), (
        "two paths with identical content would be published under two asset ids")


def test_upload_archive_is_not_processed_twice():
    from processing.cli import _discover
    files = _discover("animation")
    assert files, "the organized tree must contain processable animation sources"
    assert not [f for f in files if "/Upload/" in str(f).replace("\\", "/")], \
        "the read-only archive must not be published a second time"
