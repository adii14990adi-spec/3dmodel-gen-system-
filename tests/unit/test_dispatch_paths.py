"""Security tests for the ``workflow_dispatch`` path input parser.

`scripts/dispatch_paths.py` is the only thing that turns the untrusted
`inputs.paths` string into pipeline arguments. These tests build real directory
trees and feed real payloads through it: shell metacharacters must be rejected as
data (never executed), and no path outside the kind's asset root may be touched.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
import unicodedata
from pathlib import Path

import pytest

from processing.common import ROOT, SUPPORTED_ANIMATIONS, SUPPORTED_MODELS


def _load_dispatch_paths():
    spec = importlib.util.spec_from_file_location(
        "dispatch_paths", ROOT / "scripts/dispatch_paths.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


dispatch_paths = _load_dispatch_paths()
DispatchPathError = dispatch_paths.DispatchPathError

SCRIPT = ROOT / "scripts/dispatch_paths.py"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

@pytest.fixture()
def tree(tmp_path: Path) -> Path:
    """A throwaway checkout with model and animation asset roots."""
    for kind in ("models", "animation"):
        (tmp_path / "assets" / kind / "props").mkdir(parents=True)
    (tmp_path / "assets" / "models" / "props" / "crate.glb").write_bytes(b"glTF")
    (tmp_path / "assets" / "models" / "props" / "with space.glb").write_bytes(b"glTF")
    (tmp_path / "assets" / "models" / "props" / "ünïcode.glb").write_bytes(b"glTF")
    (tmp_path / "assets" / "models" / "props" / "quote'and\"dollar$.glb").write_bytes(b"glTF")
    (tmp_path / "assets" / "models" / "props" / "second.glb").write_bytes(b"glTF")
    (tmp_path / "assets" / "animation" / "props" / "walk.bvh").write_bytes(b"HIERARCHY")
    return tmp_path


def resolve(kind: str, text: str, root: Path) -> list[str]:
    return dispatch_paths.resolve_paths(kind, text, root)


def run_cli(tmp_path: Path, kind: str, payload: bytes, *extra: str
            ) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--kind", kind, "--root", str(tmp_path),
         "--paths-file", "-", "--null", *extra],
        input=payload, capture_output=True)


def snapshot(root: Path) -> dict[str, bytes]:
    """Every file outside the asset roots, plus asset content, for tamper checks."""
    return {str(p.relative_to(root)): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file()}


# --------------------------------------------------------------------------- #
# 1-4, 16: legitimate input, including spaces, unicode and quotes
# --------------------------------------------------------------------------- #

def test_plain_relative_path_is_accepted(tree: Path):
    assert resolve("model", "assets/models/props/crate.glb", tree) == [
        "assets/models/props/crate.glb"]


def test_filename_with_spaces_is_one_path_not_two(tree: Path):
    paths = resolve("model", "assets/models/props/with space.glb", tree)
    assert paths == ["assets/models/props/with space.glb"]
    proc = run_cli(tree, "model", b"assets/models/props/with space.glb")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == b"assets/models/props/with space.glb\x00"


def test_unicode_filename_survives_byte_for_byte(tree: Path):
    name = unicodedata.normalize("NFC", "assets/models/props/ünïcode.glb")
    proc = run_cli(tree, "model", name.encode("utf-8"))
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == name.encode("utf-8") + b"\x00"


def test_quotes_and_dollar_sign_in_filename_are_data(tree: Path):
    name = "assets/models/props/quote'and\"dollar$.glb"
    proc = run_cli(tree, "model", name.encode())
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == name.encode() + b"\x00"


def test_multiple_valid_paths_keep_input_order(tree: Path):
    text = "assets/models/props/second.glb\nassets/models/props/crate.glb\n"
    assert resolve("model", text, tree) == ["assets/models/props/second.glb",
                                            "assets/models/props/crate.glb"]
    proc = run_cli(tree, "model", text.encode())
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == (b"assets/models/props/second.glb\x00"
                           b"assets/models/props/crate.glb\x00")


def test_duplicate_entries_are_emitted_once(tree: Path):
    text = "assets/models/props/crate.glb\nassets/models/props/crate.glb"
    assert resolve("model", text, tree) == ["assets/models/props/crate.glb"]


def test_animation_kind_accepts_its_own_root(tree: Path):
    assert resolve("animation", "assets/animation/props/walk.bvh", tree) == [
        "assets/animation/props/walk.bvh"]


# --------------------------------------------------------------------------- #
# 5-8: live shell payloads must never be executed
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("payload", [
    "assets/models/props/crate.glb; touch {marker}",        # 5 semicolon
    "assets/models/props/crate.glb\n; touch {marker}",      # 5 as its own entry
    "$(touch {marker})",                                    # 6 command substitution
    "assets/models/props/$(touch {marker}).glb",            # 6 nested in a path
    "`touch {marker}`",                                      # 7 backticks
    "assets/models/props/crate.glb\ntouch {marker}",        # 8 newline separated
    "assets/models/props/crate.glb $(touch {marker})",      # 6 with a valid prefix
])
def test_malicious_payload_is_rejected_without_executing_anything(tree: Path, payload: str):
    marker = tree / "pwned"
    attack = payload.format(marker=marker)
    before = snapshot(tree)

    proc = run_cli(tree, "model", attack.encode())

    assert proc.returncode == 2, proc.stdout
    assert proc.stdout == b"", "a rejected input must not emit tokens"
    assert not marker.exists(), f"payload executed a command: {attack!r}"
    assert snapshot(tree) == before, "a rejected input must not modify any file"
    with pytest.raises(DispatchPathError):
        resolve("model", attack, tree)


def test_semicolon_payload_cannot_emit_a_token(tree: Path):
    """Validation must reject the whole entry, not strip the injected command."""
    with pytest.raises(DispatchPathError):
        resolve("model", "assets/models/props/crate.glb; id", tree)


# --------------------------------------------------------------------------- #
# 9-13: path shape, root, and extension rules
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("entry", [
    "/etc/passwd",                                                    # 9 absolute
    "/assets/models/props/crate.glb",                                 # 9 absolute, right root
    "~/assets/models/props/crate.glb",                                # absolute-ish home
    "../../etc/passwd",                                               # 10 traversal
    "assets/models/../../etc/passwd",                                 # 10 traversal from inside
    "assets/models/props/../../../../etc/passwd",                     # 10 deeper traversal
    r"assets\models\..\..\etc\passwd",                                # 11 windows traversal
    r"assets\models\props\crate.glb",                                 # windows separators
    r"C:\Windows\System32\config",                                    # 11 windows absolute
    "C:/Windows/System32/config",                                     # 11 windows drive
    r"\\server\share\evil.glb",                                       # UNC
    "assets/animation/props/walk.bvh",                                # 12 other asset root
    "assets/README.txt",                                              # 12 asset tree, wrong root
    "processing/common.py",                                           # 12 repository file
    "assets/models/props/evil.py",                                    # 13 unsupported extension
    "assets/models/props/evil.sh",                                    # 13 unsafe extension
    "assets/models/props/noextension",                                # 13 no extension
    "assets/models/props/crate.glb.exe",                              # 13 disguised
])
def test_rejected_path_shapes(tree: Path, entry: str):
    with pytest.raises(DispatchPathError):
        resolve("model", entry, tree)


def test_animation_kind_rejects_model_root(tree: Path):
    with pytest.raises(DispatchPathError):
        resolve("animation", "assets/models/props/crate.glb", tree)


def test_extension_allowlist_matches_the_pipeline(tree: Path):
    """The parser and `validate_source` must agree on what is processable."""
    for suffix in sorted(SUPPORTED_MODELS):
        name = f"assets/models/props/allowed{suffix}.glb".replace(".glb", suffix)
        target = tree / name
        target.write_bytes(b"x")
        assert resolve("model", name, tree) == [name]
    for suffix in sorted(SUPPORTED_ANIMATIONS):
        name = f"assets/animation/props/allowed{suffix}.glb".replace(".glb", suffix)
        target = tree / name
        target.write_bytes(b"x")
        assert resolve("animation", name, tree) == [name]
    for suffix in ("", ".py", ".sh", ".blend", ".zip", ".txt"):
        bad = f"assets/models/props/rejected{suffix}"
        (tree / bad).write_bytes(b"x")
        with pytest.raises(DispatchPathError):
            resolve("model", bad, tree)


def test_missing_and_non_regular_files_are_rejected(tree: Path):
    (tree / "assets/models/props/dir.glb").mkdir()
    with pytest.raises(DispatchPathError):
        resolve("model", "assets/models/props/nope.glb", tree)
    with pytest.raises(DispatchPathError):
        resolve("model", "assets/models/props/dir.glb", tree)


# --------------------------------------------------------------------------- #
# 14: symlink escape
# --------------------------------------------------------------------------- #

def test_symlink_inside_assets_pointing_outside_is_rejected(tree: Path):
    secret = tree / "outside.glb"
    secret.write_bytes(b"secret")
    link = tree / "assets/models/props/escape.glb"
    link.symlink_to(secret)
    with pytest.raises(DispatchPathError, match="symlink escape"):
        resolve("model", "assets/models/props/escape.glb", tree)


def test_symlink_to_a_directory_outside_is_rejected(tree: Path):
    outside_dir = tree / "outside"
    outside_dir.mkdir()
    (outside_dir / "x.glb").write_bytes(b"x")
    (tree / "assets/models/props/escape.glb").symlink_to(outside_dir / "x.glb")
    with pytest.raises(DispatchPathError):
        resolve("model", "assets/models/props/escape.glb", tree)


def test_dangling_symlink_is_rejected(tree: Path):
    (tree / "assets/models/props/dangling.glb").symlink_to(tree / "missing.glb")
    with pytest.raises(DispatchPathError):
        resolve("model", "assets/models/props/dangling.glb", tree)


def test_symlink_inside_the_same_root_is_allowed(tree: Path):
    """An alias that stays inside the asset root is not an escape."""
    (tree / "assets/models/props/alias.glb").symlink_to(
        tree / "assets/models/props/crate.glb")
    assert resolve("model", "assets/models/props/alias.glb", tree) == [
        "assets/models/props/alias.glb"]


# --------------------------------------------------------------------------- #
# 15: empty / malformed input
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("payload", [
    b"",                                             # empty
    b"\n",                                           # only a newline
    b"   \n",                                        # whitespace only
    b"assets/models/props/crate.glb\n\n",            # blank entry in the middle/end
    b"assets/models/props/crate.glb\0exploit.glb",   # embedded NUL
    b"assets/models/props/crate.glb\r\n",            # CRLF
    b"assets/models/props/crate.glb\x1b[31m",        # ANSI escape
    b"\xff\xfeassets/models/props/crate.glb",        # invalid UTF-8
])
def test_empty_and_malformed_input_is_rejected(tree: Path, payload: bytes):
    proc = run_cli(tree, "model", payload)
    assert proc.returncode == 2, proc.stdout
    assert proc.stdout == b""


def test_non_canonical_path_is_rejected_not_silently_fixed(tree: Path):
    for entry in ("./assets/models/props/crate.glb",
                  "assets//models/props/crate.glb",
                  "assets/models/props/crate.glb/",
                  "assets/models/./props/crate.glb"):
        with pytest.raises(DispatchPathError):
            resolve("model", entry, tree)


# --------------------------------------------------------------------------- #
# CLI contract
# --------------------------------------------------------------------------- #

def test_cli_reports_an_error_annotation_and_nonzero_exit(tree: Path):
    proc = run_cli(tree, "model", b"$(id)")
    assert proc.returncode == 2
    assert proc.stdout == b""
    assert b"::error::" in proc.stderr


def test_cli_rejects_non_utf8_input(tree: Path):
    proc = run_cli(tree, "model", b"assets/models/props/\xff.glb")
    assert proc.returncode == 2
    assert b"UTF-8" in proc.stderr


def test_cli_reads_a_paths_file(tmp_path: Path, tree: Path):
    listing = tmp_path / "paths.txt"
    listing.write_text("assets/models/props/crate.glb\n", encoding="utf-8")
    proc = subprocess.run([sys.executable, str(SCRIPT), "--kind", "model",
                           "--paths-file", str(listing), "--null",
                           "--root", str(tree)], capture_output=True)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == b"assets/models/props/crate.glb\x00"


def test_cli_never_writes_to_stdout_on_failure(tree: Path):
    proc = run_cli(tree, "model", b"assets/models/props/crate.glb\n/etc/passwd")
    assert proc.returncode == 2
    assert proc.stdout == b"", "a partial token list could process half an unsafe request"


def test_input_file_outside_the_repository_is_only_read_as_data(tree: Path):
    """The input file may live in $RUNNER_TEMP; that is not an asset path leak."""
    outside = tree.parent / "runner-temp-paths.txt"
    outside.write_text("assets/models/props/crate.glb\n", encoding="utf-8")
    proc = subprocess.run([sys.executable, str(SCRIPT), "--kind", "model",
                           "--paths-file", str(outside), "--null", "--root", str(tree)],
                          capture_output=True)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == b"assets/models/props/crate.glb\x00"
