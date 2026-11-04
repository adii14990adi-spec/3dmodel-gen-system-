"""Android playback milestone: static contract + real GLB playback-data checks.

The sandbox has no JDK, Android SDK or device, so the Kotlin transport tests in
``android/animation/src/test`` cannot be executed here (CI runs them with
``:animation:testDebugUnitTest``). What *can* be executed is:

* the source-contract checker (does the renderer really drive the animator, does
  the player expose play/pause/stop, do the JVM tests exist), including proofs
  that the checker fails when the contract is broken;
* the GLB playback-data validator, which proves the files the device would load
  actually contain animation channels on skeleton joints with real durations.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------- #
# Static contract (runs everywhere)
# --------------------------------------------------------------------------- #
def test_android_source_checks_pass():
    import subprocess
    import sys
    proc = subprocess.run([sys.executable, "scripts/check_android_sources.py"], cwd=ROOT,
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Android source checks passed" in proc.stdout


def test_contract_check_catches_a_renderer_without_bone_matrix_updates(tmp_path):
    module = _load_script("check_android_sources")
    fake = tmp_path / "android"
    (fake / "renderer/src/main/java/com/assetstudio/renderer").mkdir(parents=True)
    (fake / "renderer/src/main/java/com/assetstudio/renderer/FilamentViewport.kt").write_text(
        "class FilamentViewport {\n"
        "    fun applyAnimation(index: Int, seconds: Float): Boolean {\n"
        "        animator.applyAnimation(index, seconds)\n"
        "        return true\n"
        "    }\n"
        "}\n", encoding="utf-8")
    module.ANDROID = fake
    issues = module.playback_contract_issues()
    assert any("updateBoneMatrices" in issue for issue in issues), issues
    assert any("getAnimationCount" in issue or "getAnimationDuration" in issue for issue in issues)


def test_contract_check_rejects_a_static_pose_substitute(tmp_path):
    module = _load_script("check_android_sources")
    fake = tmp_path / "android"
    target = fake / "app/src/main/java/com/assetstudio/app"
    target.mkdir(parents=True)
    (target / "Player.kt").write_text(
        "val fakePlayback = true\n", encoding="utf-8")
    renderer_dir = fake / "renderer/src/main/java/com/assetstudio/renderer"
    renderer_dir.mkdir(parents=True)
    (renderer_dir / "FilamentViewport.kt").write_text(
        "class FilamentViewport {\n"
        "    val poseFrames = 0\n"
        "    fun applyAnimation(index: Int, seconds: Float): Boolean {\n"
        "        animator.applyAnimation(index, seconds)\n"
        "        animator.updateBoneMatrices()\n"
        "        animator.getAnimationCount()\n"
        "        animator.getAnimationDuration(index)\n"
        "        return true\n"
        "    }\n"
        "}\n", encoding="utf-8")
    module.ANDROID = fake
    issues = module.playback_contract_issues()
    assert any("fake-playback marker" in issue for issue in issues), issues


def test_animation_module_declares_jvm_test_dependencies():
    build = (ROOT / "android/animation/build.gradle.kts").read_text(encoding="utf-8")
    assert "testImplementation(libs.junit)" in build
    tests = sorted((ROOT / "android/animation/src/test").rglob("*.kt"))
    names = {path.name for path in tests}
    assert "AnimationControllerTest.kt" in names
    assert "PlaybackValidatorTest.kt" in names


# --------------------------------------------------------------------------- #
# GLB playback data
# --------------------------------------------------------------------------- #
def _animated_glb(with_animation: bool = True, with_skin: bool = True) -> bytes:
    from processing.glb import write_glb
    from processing.retarget import append_accessor

    doc: dict = {
        "asset": {"version": "2.0"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"name": "Root", "children": [1]}, {"name": "Bone", "translation": [0.0, 1.0, 0.0]}],
    }
    if with_skin:
        doc["skins"] = [{"joints": [0, 1], "inverseBindMatrices": 0,
                         "skeleton": 0}]
    binary = b""
    if with_animation:
        times, binary = append_accessor(doc, binary, [0.0, 0.5, 1.0], 5126, "SCALAR", minmax=True)
        quats, binary = append_accessor(
            doc, binary,
            [[0.0, 0.0, 0.0, 1.0], [0.0, 0.3826834, 0.0, 0.9238795], [0.0, 0.0, 0.0, 1.0]],
            5126, "VEC4")
        doc["animations"] = [{
            "name": "Retarget_idle",
            "samplers": [{"input": times, "output": quats, "interpolation": "LINEAR"}],
            "channels": [{"sampler": 0, "target": {"node": 1, "path": "rotation"}}],
        }]
    if with_skin:
        # inverseBindMatrices must exist for the skin to be valid
        identity = [[1.0 if r == c else 0.0 for c in range(4)] + [0.0] for r in range(3)]
        mats, binary = append_accessor(doc, binary, identity, 5126, "MAT4")
        doc["skins"][0]["inverseBindMatrices"] = mats
    return write_glb(doc, binary)


def test_playback_data_validator_accepts_a_real_clip(tmp_path):
    module = _load_script("validate_playback_assets")
    glb = tmp_path / "animated.glb"
    glb.write_bytes(_animated_glb())
    record = module.inspect_glb(glb)
    assert record["playable"], record["issues"]
    assert record["animations"][0]["duration_seconds"] == pytest.approx(1.0, abs=1e-6)
    assert record["animations"][0]["all_targets_are_joints"] is True


def test_playback_data_validator_rejects_a_glb_without_animation(tmp_path):
    module = _load_script("validate_playback_assets")
    glb = tmp_path / "static.glb"
    glb.write_bytes(_animated_glb(with_animation=False))
    record = module.inspect_glb(glb)
    assert not record["playable"]
    assert any("no animation clips" in issue for issue in record["issues"])


def test_playback_data_validator_rejects_animation_without_a_skin(tmp_path):
    module = _load_script("validate_playback_assets")
    glb = tmp_path / "nokin.glb"
    glb.write_bytes(_animated_glb(with_skin=False))
    record = module.inspect_glb(glb)
    assert any("no skin" in issue for issue in record["issues"])


def test_playback_data_validator_flags_out_of_order_key_times(tmp_path):
    from processing.glb import write_glb, parse_glb
    from processing.retarget import append_accessor

    module = _load_script("validate_playback_assets")
    doc: dict = {"asset": {"version": "2.0"},
                 "nodes": [{"name": "Bone"}],
                 "skins": [{"joints": [0], "inverseBindMatrices": 0}]}
    binary = b""
    times, binary = append_accessor(doc, binary, [0.0, 0.6, 0.3], 5126, "SCALAR", minmax=True)
    quats, binary = append_accessor(doc, binary, [[0.0, 0.0, 0.0, 1.0]] * 3, 5126, "VEC4")
    mats, binary = append_accessor(doc, binary, [[1.0, 0, 0, 0], [0, 1.0, 0, 0], [0, 0, 1.0, 0]],
                                 5126, "MAT4")
    doc["skins"][0]["inverseBindMatrices"] = mats
    doc["animations"] = [{"name": "broken",
                          "samplers": [{"input": times, "output": quats, "interpolation": "LINEAR"}],
                          "channels": [{"sampler": 0, "target": {"node": 0, "path": "rotation"}}]}]
    glb = tmp_path / "broken.glb"
    glb.write_bytes(write_glb(doc, binary))
    record = module.inspect_glb(glb)
    assert not record["playable"]
    assert any("not increasing" in issue for issue in record["issues"])


def test_real_exported_clips_are_playable_data(pipeline):
    """End-to-end: the GLBs the device loads must carry real clip data."""
    import subprocess
    import sys
    proc = subprocess.run([sys.executable, "scripts/validate_playback_assets.py", "--strict"],
                          cwd=ROOT, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    report = json.loads((ROOT / "reports/animations/playback-validation.json").read_text())
    assert report["failed"] == 0
    assert report["missing"] == 0, report
    for row in report["categories"]:
        assert row["clips"] >= 1, row
        assert row["duration_seconds"] > 0.0, row
        assert row["joints"] > 0, row
    assert {row["category"] for row in report["categories"]} == {
        "idle", "walk", "run", "jump", "attack", "death"}
