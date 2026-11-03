from __future__ import annotations

import pytest

from processing.bvh import BVHError, load_bvh, parse_bvh


def test_bvh_real_parser(pipeline):
    bvh = load_bvh(pipeline / "assets/animation/locomotion/walk.bvh")
    assert bvh.frame_count == 60
    assert bvh.fps == pytest.approx(30.0, abs=0.01)
    assert bvh.root == "Hips"
    assert len(bvh.joints) >= 21
    assert bvh.joints["LeftArm"].parent == "LeftShoulder"
    assert len(bvh.frames[0]) == sum(len(j.channels) for j in bvh.joints.values())


def test_bvh_rejects_malformed_frames():
    text = "HIERARCHY\nROOT Hips\n{\nOFFSET 0 0 0\nCHANNELS 3 Xrotation Yrotation Zrotation\nEnd Site\n{\nOFFSET 0 -1 0\n}\n}\nMOTION\nFrames: 1\nFrame Time: 0.033\n1 2\n"
    with pytest.raises(BVHError):
        parse_bvh(text)
