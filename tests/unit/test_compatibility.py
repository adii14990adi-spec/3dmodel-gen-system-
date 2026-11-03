from __future__ import annotations

from processing.bvh import skeleton_summary, load_bvh
from processing.compatibility import check_compatibility, map_roles, role_for
from processing.glb import load_document, inspect_document


def _bones(model_path):
    return inspect_document(load_document(model_path), "x")["skeleton"]["bones"]


def test_humanoid_compatible(pipeline):
    src = skeleton_summary(load_bvh(pipeline / "assets/animation/locomotion/walk.bvh"))["bones"]
    tgt = _bones(pipeline / "assets/models/characters/warrior.glb")
    rec = check_compatibility(src, tgt)
    assert rec["status"] == "compatible", rec["reasons"]
    assert rec["mapped_bones"] >= 12
    assert not rec["ambiguous_roles"]


def test_quadruped_is_incompatible(pipeline):
    src = skeleton_summary(load_bvh(pipeline / "assets/animation/other/quadruped.bvh"))["bones"]
    tgt = _bones(pipeline / "assets/models/characters/warrior.glb")
    rec = check_compatibility(src, tgt)
    assert rec["status"] == "incompatible"
    assert rec["reasons"]


def test_ambiguous_mapping_not_silently_resolved():
    bones = [{"name": "Spine"}, {"name": "Spine1"}, {"name": "Spine2"},
             {"name": "Hips"}, {"name": "Head"}, {"name": "LeftArm"},
             {"name": "RightArm"}, {"name": "LeftUpLeg"}, {"name": "RightUpLeg"}]
    roles, ambiguous, _ = map_roles(bones)
    assert ambiguous, "duplicate spine/chest roles must be reported as ambiguous"


def test_role_alias_side_tokens(pipeline):
    assert role_for("mixamorig:LeftForeArm", __import__("json").loads(
        (pipeline / "config/bone_mappings/humanoid.json").read_text()))[0] == "left_lower_arm"
