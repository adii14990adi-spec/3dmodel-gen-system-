"""Guard the ingestion wiring in the processing workflows.

These tests read the real workflow YAML, render the ``${{ … }}`` expressions for
a given event, and execute the resolve/process shell locally. They caught two
real regressions during Step 02:

* a dropped ``id: changes`` made ``steps.changes.outputs.count`` evaluate to
  null (which compares equal to '0'), so processing was silently skipped;
* a double-escaped ``printf '--all\\0'`` wrote the literal text ``\\0`` instead
  of a NUL byte.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from processing.common import ROOT

WORKFLOWS = {
    "model": ROOT / ".github/workflows/asset-processing.yml",
    "animation": ROOT / ".github/workflows/animation-processing.yml",
}


def load(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


def steps(path: Path) -> list[dict]:
    return load(path)["jobs"]["process"]["steps"]


def step_named(path: Path, prefix: str) -> dict:
    for step in steps(path):
        if str(step.get("name", "")).startswith(prefix):
            return step
    raise AssertionError(f"step starting with {prefix!r} not found in {path.name}")


def render(script: str, *, event: str, all_input: str = "", paths: str = "",
           before: str = "", sha: str = "") -> str:
    return (script
            .replace("${{ github.event_name }}", event)
            .replace("${{ inputs.all }}", all_input)
            .replace("${{ inputs.paths }}", paths)
            .replace("${{ github.event.before }}", before)
            .replace("${{ github.sha }}", sha))


def run_script(script: str, tmp_path: Path) -> tuple[subprocess.CompletedProcess, Path, dict]:
    output = tmp_path / "github_output"
    output.touch()
    env = dict(os.environ, RUNNER_TEMP=str(tmp_path), GITHUB_OUTPUT=str(output),
               PYTHONPATH=str(ROOT), PATH=f"{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}")
    proc = subprocess.run(["bash", "-c", script], cwd=ROOT, capture_output=True,
                          text=True, env=env)
    values = dict(line.split("=", 1) for line in output.read_text().splitlines() if "=" in line)
    return proc, output, values


@pytest.mark.parametrize("kind", sorted(WORKFLOWS))
def test_every_referenced_step_id_is_defined(kind: str):
    """`steps.<id>` must exist, otherwise the expression silently evaluates to null."""
    path = WORKFLOWS[kind]
    job = load(path)["jobs"]["process"]
    defined = {step["id"] for step in job["steps"] if "id" in step}
    referenced = set(re.findall(r"steps\.([A-Za-z0-9_-]+)\.", path.read_text()))
    assert referenced, "workflow should reference its resolve step"
    assert referenced <= defined, f"undefined step id(s): {sorted(referenced - defined)}"


@pytest.mark.parametrize("kind", sorted(WORKFLOWS))
def test_dispatch_inputs_and_shallow_safe_checkout(kind: str):
    path = WORKFLOWS[kind]
    wf = load(path)
    dispatch = wf[True]["workflow_dispatch"]
    assert set(dispatch["inputs"]) == {"all", "paths"}
    checkout = next(s for s in wf["jobs"]["process"]["steps"]
                    if str(s.get("uses", "")).startswith("actions/checkout"))
    assert checkout["with"]["fetch-depth"] == 0, "diffs need full history"


@pytest.mark.parametrize("kind", sorted(WORKFLOWS))
def test_resolve_step_emits_real_nul_token_for_all_true(kind: str, tmp_path: Path):
    step = step_named(WORKFLOWS[kind], "Resolve changed")
    script = render(step["run"], event="workflow_dispatch", all_input="true")
    proc, _, values = run_script(script, tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert values["count"] == "1", proc.stdout + proc.stderr

    list_file = Path(values["list"])
    assert list_file.read_bytes() == b"--all\x00", "must be a NUL token, not the text \\0"


@pytest.mark.parametrize("kind", sorted(WORKFLOWS))
def test_resolve_step_reports_zero_for_empty_diff(kind: str, tmp_path: Path):
    step = step_named(WORKFLOWS[kind], "Resolve changed")
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                          text=True, check=True).stdout.strip()
    script = render(step["run"], event="push", before=head, sha=head)
    proc, _, values = run_script(script, tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert values["count"] == "0"
    assert Path(values["list"]).read_bytes() == b""


@pytest.mark.parametrize("kind", sorted(WORKFLOWS))
def test_resolve_step_requests_full_processing_for_zero_sha(kind: str, tmp_path: Path):
    step = step_named(WORKFLOWS[kind], "Resolve changed")
    script = render(step["run"], event="push", before="0" * 40, sha="0" * 40)
    proc, _, values = run_script(script, tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert values["count"] == "1"
    assert Path(values["list"]).read_bytes() == b"--all\x00"


@pytest.mark.parametrize("kind", sorted(WORKFLOWS))
def test_resolve_step_fails_fast_when_the_list_cannot_be_written(kind: str, tmp_path: Path):
    """set -euo pipefail must abort the step instead of reporting an empty change set."""
    step = step_named(WORKFLOWS[kind], "Resolve changed")
    script = render(step["run"], event="push", before="HEAD~1", sha="HEAD")
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("x")
    env = dict(os.environ, RUNNER_TEMP=str(blocker), GITHUB_OUTPUT=str(tmp_path / "out"),
               PYTHONPATH=str(ROOT),
               PATH=f"{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}")
    proc = subprocess.run(["bash", "-c", script], cwd=ROOT, capture_output=True,
                          text=True, env=env)
    assert proc.returncode != 0, "a failing resolve step must fail the workflow"


@pytest.mark.parametrize("kind", sorted(WORKFLOWS))
def test_process_step_consumes_the_list_with_xargs_null(kind: str):
    step = step_named(WORKFLOWS[kind], "Process ")
    run = step["run"]
    assert "xargs -0 -r" in run
    assert 'steps.changes.outputs.list' in run
    assert step["if"] == "steps.changes.outputs.count != '0'"


def test_null_tokens_survive_xargs(tmp_path: Path):
    """The exact xargs invocation keeps spaces, quotes and unicode intact."""
    names = ["assets/models/props/with space.glb",
             "assets/models/props/quote'and$dollar.glb",
             "assets/models/props/ünïcode.glb"]
    listing = tmp_path / "list.bin"
    listing.write_bytes(b"".join(name.encode() + b"\0" for name in names))
    proc = subprocess.run(
        ["bash", "-c", f'xargs -0 -r python3 -c "import sys;print(chr(10).join(sys.argv[1:]))" '
                       f'< "{listing}"'],
        capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.splitlines() == names
