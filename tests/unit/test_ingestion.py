"""Step 02 — asset ingestion audit tests.

Covers changed-file detection against real temporary git repositories, stable
asset identity, untrusted-input validation and source immutability.

Nothing here mocks git or the filesystem: each case builds a real repository or
real file and asserts on the bytes that the pipeline actually reads.
"""
from __future__ import annotations

import hashlib
import importlib.util
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from processing.common import (ROOT, SUPPORTED_ANIMATIONS, SUPPORTED_MODELS, UnsafeAssetError,
                               asset_id, normalize_path, sha256_file, validate_source)


def _load_changed_files():
    spec = importlib.util.spec_from_file_location(
        "changed_files", ROOT / "scripts/changed_files.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


changed_files = _load_changed_files()


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
    assert proc.returncode == 0, f"git {' '.join(args)} failed: {proc.stderr}"
    return proc.stdout


def init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.email", "ci@example.com")
    git(path, "config", "user.name", "CI")
    git(path, "config", "commit.gpgsign", "false")
    return path


def write(repo: Path, relative: str, data: bytes = b"x") -> Path:
    target = repo / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target


def commit(repo: Path, message: str = "commit") -> str:
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", message)
    return git(repo, "rev-parse", "HEAD").strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return init_repo(tmp_path / "repo")


def run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "scripts/changed_files.py", *args],
                          cwd=ROOT, capture_output=True, text=True)


# --------------------------------------------------------------------------- #
# 1. changed-file detection
# --------------------------------------------------------------------------- #

def test_added_assets_detected_per_kind(repo: Path):
    write(repo, "docs/readme.md", b"doc")
    base = commit(repo, "base")
    write(repo, "assets/models/props/crate.glb", b"model")
    write(repo, "assets/animation/locomotion/walk.bvh", b"anim")
    write(repo, "README.md", b"unrelated")
    head = commit(repo, "assets")

    assert changed_files.resolve("model", base, head, repo) == ["assets/models/props/crate.glb"]
    assert changed_files.resolve("animation", base, head, repo) == \
        ["assets/animation/locomotion/walk.bvh"]


def test_modified_file_is_detected(repo: Path):
    write(repo, "assets/models/props/crate.glb", b"v1")
    base = commit(repo, "base")
    write(repo, "assets/models/props/crate.glb", b"v2")
    head = commit(repo, "modify")
    assert changed_files.resolve("model", base, head, repo) == ["assets/models/props/crate.glb"]


def test_deleted_file_is_not_returned(repo: Path):
    write(repo, "assets/models/props/gone.glb", b"gone")
    write(repo, "assets/models/props/kept.glb", b"kept")
    base = commit(repo, "base")
    (repo / "assets/models/props/gone.glb").unlink()
    write(repo, "assets/models/props/kept.glb", b"kept-v2")
    head = commit(repo, "delete + modify")

    result = changed_files.resolve("model", base, head, repo)
    assert result == ["assets/models/props/kept.glb"]
    assert "assets/models/props/gone.glb" not in result
    assert not (repo / "assets/models/props/gone.glb").exists()


def test_rename_returns_new_path_only(repo: Path):
    write(repo, "assets/models/characters/old.glb", b"payload")
    base = commit(repo, "base")
    git(repo, "mv", "assets/models/characters/old.glb", "assets/models/characters/new.glb")
    head = commit(repo, "rename")

    result = changed_files.resolve("model", base, head, repo)
    assert result == ["assets/models/characters/new.glb"]
    assert (repo / result[0]).is_file()


def test_unrelated_changes_yield_empty_list(repo: Path):
    write(repo, "assets/models/props/crate.glb", b"crate")
    base = commit(repo, "base")
    write(repo, "docs/processing.md", b"docs")
    write(repo, "processing/cli.py", b"print('x')")
    write(repo, "assets/textures/skin.png", b"png")
    head = commit(repo, "unrelated")
    assert changed_files.resolve("model", base, head, repo) == []


def test_empty_diff_yields_empty_list(repo: Path):
    write(repo, "assets/models/props/crate.glb", b"crate")
    head = commit(repo, "base")
    assert changed_files.resolve("model", head, head, repo) == []
    assert run_cli("--repo", str(repo), "--base", head, "--head", head, "--kind", "model").stdout.strip() == ""


@pytest.mark.parametrize("zero", ["0" * 40, "0000000", ""])
def test_zero_sha_requests_full_processing(repo: Path, zero: str):
    write(repo, "assets/models/props/first.glb", b"1")
    commit(repo, "c1")
    write(repo, "assets/models/props/second.glb", b"2")
    head = commit(repo, "c2")

    # A zero/absent base has no trustworthy diff: process everything. Diffing
    # HEAD~1 would silently skip first.glb, which is exactly the bug this guards.
    assert changed_files.resolve("model", zero, head, repo) is None
    out = run_cli("--repo", str(repo), "--base", zero, "--head", head, "--kind", "model")
    assert out.returncode == 0
    assert out.stdout.strip() == "--all"


def test_multi_commit_branch_under_zero_sha_processes_everything(repo: Path):
    write(repo, "assets/models/props/first.glb", b"1")
    commit(repo, "c1")
    write(repo, "assets/models/props/second.glb", b"2")
    head = commit(repo, "c2")
    files = changed_files.resolve("model", "0" * 40, head, repo)
    assert files is None, "must fall back to --all so first.glb is not skipped"


def test_unknown_ref_falls_back_to_full_processing(repo: Path):
    write(repo, "assets/models/props/crate.glb", b"crate")
    head = commit(repo, "base")
    assert changed_files.resolve("model", "does-not-exist-ref", head, repo) is None


def test_shallow_history_falls_back_to_full_processing(repo: Path, tmp_path: Path):
    write(repo, "assets/models/props/first.glb", b"1")
    commit(repo, "c1")
    write(repo, "assets/models/props/second.glb", b"2")
    commit(repo, "c2")

    shallow = tmp_path / "shallow"
    clone = subprocess.run(["git", "clone", "-q", "--depth", "1", f"file://{repo}", str(shallow)],
                           capture_output=True, text=True)
    if clone.returncode != 0:  # pragma: no cover - environment without file:// clones
        pytest.skip(f"shallow clone unavailable: {clone.stderr.strip()}")
    head = git(shallow, "rev-parse", "HEAD").strip()
    assert changed_files.resolve("model", "HEAD~1", head, shallow) is None


def test_multiple_changed_assets_in_one_commit(repo: Path):
    write(repo, "assets/models/props/crate.glb", b"crate")
    base = commit(repo, "base")
    for name in ("a.glb", "b.glb", "c.glb"):
        write(repo, f"assets/models/props/{name}", name.encode())
    head = commit(repo, "three assets")
    assert changed_files.resolve("model", base, head, repo) == [
        "assets/models/props/a.glb", "assets/models/props/b.glb", "assets/models/props/c.glb"]


def test_paths_with_spaces_and_unicode_survive(repo: Path):
    write(repo, "assets/models/props/base.glb", b"base")
    base = commit(repo, "base")
    tricky = ["assets/models/props/with space.glb",
              "assets/models/props/quote'and$dollar.glb",
              "assets/models/props/ünïcode-ñame.glb"]
    for name in tricky:
        write(repo, name, name.encode())
    head = commit(repo, "tricky names")

    assert changed_files.resolve("model", base, head, repo) == sorted(tricky)

    # --null keeps the exact bytes for shell consumption (xargs -0).
    proc = run_cli("--repo", str(repo), "--base", base, "--head", head,
                   "--kind", "model", "--null")
    assert proc.returncode == 0
    assert [p for p in proc.stdout.split("\0") if p] == sorted(tricky)


def test_unsupported_and_unsafe_extensions_are_filtered(repo: Path):
    write(repo, "assets/models/props/crate.glb", b"crate")
    base = commit(repo, "base")
    write(repo, "assets/models/props/notes.txt", b"n")
    write(repo, "assets/models/props/payload.py", b"print(1)")
    write(repo, "assets/models/props/scene.blend", b"blend")
    write(repo, "assets/models/props/real.glb", b"real")
    head = commit(repo, "mixed")
    assert changed_files.resolve("model", base, head, repo) == ["assets/models/props/real.glb"]


def test_uppercase_extension_is_detected(repo: Path):
    write(repo, "assets/models/props/base.glb", b"base")
    base = commit(repo, "base")
    write(repo, "assets/models/props/SHOUT.GLB", b"shout")
    write(repo, "assets/animation/locomotion/RUN.BVH", b"run")
    head = commit(repo, "upper")
    assert changed_files.resolve("model", base, head, repo) == ["assets/models/props/SHOUT.GLB"]
    assert changed_files.resolve("animation", base, head, repo) == \
        ["assets/animation/locomotion/RUN.BVH"]


def test_kind_prefixes_do_not_leak(repo: Path):
    """Model jobs never receive animation files and vice versa."""
    write(repo, "assets/models/props/crate.glb", b"crate")
    base = commit(repo, "base")
    write(repo, "assets/animation/locomotion/walk.bvh", b"walk")
    write(repo, "assets/models/props/mesh.ply", b"ply")
    head = commit(repo, "mixed kinds")
    assert changed_files.resolve("model", base, head, repo) == ["assets/models/props/mesh.ply"]
    assert changed_files.resolve("animation", base, head, repo) == \
        ["assets/animation/locomotion/walk.bvh"]


@pytest.mark.parametrize("bad_ref", ["--output=/tmp/pwned.txt", "-c", "--all", "HEAD --stat", ";(touch /tmp/pwned)"])
def test_option_like_refs_are_rejected_without_side_effects(repo: Path, bad_ref: str):
    write(repo, "assets/models/props/crate.glb", b"crate")
    head = commit(repo, "base")
    marker = Path("/tmp/pwned.txt")
    marker.unlink(missing_ok=True)

    assert changed_files.resolve("model", bad_ref, head, repo) is None
    assert not marker.exists(), "a ref must never be interpreted as a git option"


def test_cli_all_flag_is_unchanged(repo: Path):
    proc = run_cli("--kind", "model", "--all")
    assert proc.returncode == 0
    assert proc.stdout.strip() == "--all"


# --------------------------------------------------------------------------- #
# 2. stable asset identity
# --------------------------------------------------------------------------- #

def test_asset_id_is_stable_for_same_path_and_content():
    digest = hashlib.sha256(b"identical").hexdigest()
    path = "assets/models/characters/warrior.glb"
    assert asset_id("model", path, digest) == asset_id("model", path, digest)


def test_asset_id_changes_with_content():
    path = "assets/models/characters/warrior.glb"
    a = asset_id("model", path, hashlib.sha256(b"v1").hexdigest())
    b = asset_id("model", path, hashlib.sha256(b"v2").hexdigest())
    assert a != b


def test_identical_content_at_different_paths_does_not_collide():
    """Regression: the digest used to cover only the stem + content hash."""
    digest = hashlib.sha256(b"identical bytes").hexdigest()
    characters = asset_id("model", "assets/models/characters/warrior.glb", digest)
    props = asset_id("model", "assets/models/props/warrior.glb", digest)
    assert characters != props


def test_model_and_animation_ids_are_distinct():
    digest = hashlib.sha256(b"same bytes").hexdigest()
    path = "assets/animation/locomotion/walk.glb"
    assert asset_id("model", path, digest) != asset_id("animation", path, digest)


def test_asset_id_is_os_independent():
    digest = hashlib.sha256(b"bytes").hexdigest()
    canonical = asset_id("model", "assets/models/props/crate.glb", digest)
    assert asset_id("model", "assets\\models\\props\\crate.glb", digest) == canonical
    assert asset_id("model", "./assets/models/props/crate.glb", digest) == canonical
    # macOS reports decomposed unicode; the id must not depend on that form.
    nfd = "assets/models/props/" + "cafe\u0301.glb"
    nfc = "assets/models/props/caf\u00e9.glb"
    assert asset_id("model", nfd, digest) == asset_id("model", nfc, digest)
    assert normalize_path(nfd) == nfc


def test_asset_id_is_deterministic_across_processes():
    code = ("import sys; sys.path.insert(0, %r);"
            "from processing.common import asset_id;"
            "print(asset_id('model', 'assets/models/props/crate.glb', 'a'*64))" % str(ROOT))
    runs = {subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                           env=dict(os.environ, PYTHONHASHSEED=str(seed))).stdout.strip()
            for seed in (0, 1, 12345)}
    assert len(runs) == 1 and runs.pop()


def test_asset_id_shape_and_readability():
    import re

    digest = hashlib.sha256(b"bytes").hexdigest()
    aid = asset_id("model", "assets/models/props/My Crate (v2).glb", digest)
    match = re.fullmatch(r"model_([a-z0-9_]+)_([0-9a-f]{8})", aid)
    assert match, aid
    assert match.group(1) == "my_crate_v2"
    assert asset_id("animation", "assets/animation/locomotion/walk.bvh", digest).startswith("anim_walk_")
    unicode_id = asset_id("model", "assets/models/props/ünïcode-ñame.glb", digest)
    assert unicode_id.isascii() and re.fullmatch(r"model_[a-z0-9_]*_[0-9a-f]{8}", unicode_id)


# --------------------------------------------------------------------------- #
# 3. source validation and security
# --------------------------------------------------------------------------- #

@pytest.fixture
def scratch() -> Path:
    """A directory inside the repository (validation only allows repo-contained files)."""
    directory = ROOT / ".ingestion-tests" / uuid.uuid4().hex
    directory.mkdir(parents=True)
    try:
        yield directory
    finally:
        for child in sorted(directory.rglob("*"), reverse=True):
            child.unlink() if child.is_file() or child.is_symlink() else child.rmdir()
        directory.rmdir()


def test_rejects_unsupported_extension(scratch: Path):
    target = scratch / "notes.txt"
    target.write_bytes(b"hello")
    with pytest.raises(UnsafeAssetError, match="unsupported or unsafe file type"):
        validate_source(target, "model")


@pytest.mark.parametrize("suffix", [".py", ".sh", ".exe", ".blend", ".ps1"])
def test_rejects_unsafe_extension(scratch: Path, suffix: str):
    target = scratch / f"payload{suffix}"
    target.write_bytes(b"payload")
    with pytest.raises(UnsafeAssetError):
        validate_source(target, "model")


def test_rejects_empty_file(scratch: Path):
    target = scratch / "empty.glb"
    target.write_bytes(b"")
    with pytest.raises(UnsafeAssetError, match="empty"):
        validate_source(target, "model")


def test_rejects_oversize_file(scratch: Path):
    target = scratch / "big.glb"
    target.write_bytes(b"0" * 64)
    with pytest.raises(UnsafeAssetError, match="exceeds"):
        validate_source(target, "model", max_bytes=32)


def test_rejects_missing_file(scratch: Path):
    with pytest.raises(UnsafeAssetError, match="not a regular file"):
        validate_source(scratch / "ghost.glb", "model")


def test_rejects_directory(scratch: Path):
    directory = scratch / "folder.glb"
    directory.mkdir()
    with pytest.raises(UnsafeAssetError, match="not a regular file"):
        validate_source(directory, "model")


def test_rejects_path_traversal():
    escape = (ROOT / "assets/models/../../../etc/passwd.glb").resolve()
    with pytest.raises(UnsafeAssetError, match="escapes the repository root"):
        validate_source(escape, "model")


def test_rejects_symlink_escape(scratch: Path, tmp_path: Path):
    outside = tmp_path / "outside.glb"
    outside.write_bytes(b"secret")
    link = scratch / "link.glb"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):  # pragma: no cover - platform without symlinks
        pytest.skip("symlinks unavailable")
    with pytest.raises(UnsafeAssetError, match="escapes the repository root"):
        validate_source(link, "model")


def test_rejects_dangling_symlink(scratch: Path):
    link = scratch / "dangling.glb"
    try:
        link.symlink_to(scratch / "missing.glb")
    except (OSError, NotImplementedError):  # pragma: no cover
        pytest.skip("symlinks unavailable")
    with pytest.raises(UnsafeAssetError):
        validate_source(link, "model")


@pytest.mark.parametrize("name", ["UPPER.GLB", "Mixed.GlB", "lower.glb"])
def test_extension_check_is_case_insensitive(scratch: Path, name: str):
    target = scratch / name
    target.write_bytes(b"glTF payload")
    validate_source(target, "model")


@pytest.mark.parametrize("suffix", sorted(SUPPORTED_MODELS))
def test_accepts_every_supported_model_format(scratch: Path, suffix: str):
    target = scratch / f"asset{suffix}"
    target.write_bytes(b"payload")
    validate_source(target, "model")


@pytest.mark.parametrize("suffix", sorted(SUPPORTED_ANIMATIONS))
def test_accepts_every_supported_animation_format(scratch: Path, suffix: str):
    target = scratch / f"asset{suffix}"
    target.write_bytes(b"payload")
    validate_source(target, "animation")


def test_model_and_animation_allowlists_differ(scratch: Path):
    bvh = scratch / "walk.bvh"
    bvh.write_bytes(b"HIERARCHY\n")
    validate_source(bvh, "animation")
    with pytest.raises(UnsafeAssetError):
        validate_source(bvh, "model")


def test_validation_is_read_only(scratch: Path):
    target = scratch / "crate.glb"
    target.write_bytes(b"glTF" + b"\0" * 60)
    before = sha256_file(target)
    validate_source(target, "model")
    assert sha256_file(target) == before


def test_embedded_script_is_never_executed(scratch: Path):
    """A script renamed to .glb is parsed as data, never executed."""
    sentinel = scratch / "executed.marker"
    target = scratch / "evil.glb"
    target.write_bytes(
        f"#!/bin/sh\ntouch {sentinel}\n".encode() +
        b"import os; os.system('touch " + str(sentinel).encode() + b"')")
    validate_source(target, "model")  # suffix/size/path only; contents are data
    assert not sentinel.exists()

    from processing.cli import process_model
    result = process_model(target)
    assert result["status"] == "ERROR"
    assert not sentinel.exists()


# --------------------------------------------------------------------------- #
# 4. source immutability
# --------------------------------------------------------------------------- #

def snapshot(directory: Path) -> dict[str, str]:
    return {str(p.relative_to(directory)): sha256_file(p)
            for p in sorted(directory.rglob("*")) if p.is_file()}


def test_assets_tree_is_byte_identical_after_processing(pipeline):
    from processing.cli import process_animation, process_model

    assets = ROOT / "assets"
    before = snapshot(assets)

    model_path = ROOT / "assets/models/props/crate.glb"
    model_hash = sha256_file(model_path)
    result = process_model(model_path)
    assert sha256_file(model_path) == model_hash
    assert result["processed"] and "processed" in result["processed"]

    anim_path = ROOT / "assets/animation/locomotion/idle.bvh"
    anim_hash = sha256_file(anim_path)
    process_animation(anim_path)
    assert sha256_file(anim_path) == anim_hash

    assert snapshot(assets) == before, "processing must never modify assets/"


def test_processed_output_is_written_outside_assets(pipeline):
    from processing.cli import process_model
    from processing.common import path_for

    result = process_model(ROOT / "assets/models/props/crate.glb")
    processed = Path(result["processed"]).resolve()
    assert processed.is_file()
    assert path_for("processed").resolve() in processed.parents
    assert (ROOT / "assets").resolve() not in processed.parents


def test_reprocessing_same_source_is_idempotent(pipeline):
    from processing.cli import process_model

    source = ROOT / "assets/models/props/crate.glb"
    first = process_model(source)
    second = process_model(source)
    assert first["asset_id"] == second["asset_id"]
    assert first["processed"] == second["processed"]


def test_malformed_and_truncated_sources_are_rejected_as_runtime_outputs(pipeline, scratch: Path):
    from processing.cli import process_model

    truncated = scratch / "truncated.glb"
    truncated.write_bytes(b"glTF\x02\x00\x00\x00\x10\x00\x00\x00JSON")
    before = sha256_file(truncated)
    result = process_model(truncated)
    assert result["status"] == "ERROR"
    assert result["processed"] is None
    assert sha256_file(truncated) == before

    empty_json = scratch / "not_a_model.glb"
    empty_json.write_bytes(b"\xff" * 128)
    assert process_model(empty_json)["status"] == "ERROR"


def test_cli_fails_workflow_on_rejected_source():
    """An ingestion rejection must surface as a non-zero exit code in CI."""
    proc = subprocess.run([sys.executable, "-m", "processing.cli", "process-models",
                           "assets/models/props/definitely-not-a-model.py"],
                          cwd=ROOT, capture_output=True, text=True,
                          env=dict(os.environ, PYTHONPATH=str(ROOT)))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "unsupported or unsafe file type" in proc.stderr + proc.stdout
