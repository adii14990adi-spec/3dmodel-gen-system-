"""Guard the ingestion wiring in the processing workflows.

These tests read the real workflow YAML, render its ``${{ … }}`` expressions for
a given event, and execute the resolve/process shell locally.

Two properties are enforced throughout:

* **no expressions inside shell code** — every user- or event-controlled value
  (``inputs.paths`` above all) is delivered through ``env:``, so bash only ever
  sees it as data;
* **the wiring is real** — the script that is executed is the one in the YAML,
  not a restatement of it. Earlier regressions caught here: a dropped
  ``id: changes`` (processing silently skipped) and a double-escaped
  ``printf '--all\\\\0'``.
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
ALL_WORKFLOWS = sorted((ROOT / ".github/workflows").glob("*.yml"))

LIST_NAMES = {"model": "model-changes.bin", "animation": "animation-changes.bin"}

# ``${{ <expr> }}`` inside a run block is the vulnerability class this module
# exists to prevent, so the pattern is used as an assertion as well.
EXPRESSION = re.compile(r"\$\{\{(.*?)\}\}")


def load(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


def steps(path: Path) -> list[dict]:
    return load(path)["jobs"]["process"]["steps"]


def all_steps(path: Path) -> list[tuple[str, dict]]:
    wf = load(path)
    return [(job_name, step)
            for job_name, job in wf["jobs"].items()
            for step in job.get("steps", [])]


def step_named(path: Path, prefix: str) -> dict:
    for step in steps(path):
        if str(step.get("name", "")).startswith(prefix):
            return step
    raise AssertionError(f"step starting with {prefix!r} not found in {path.name}")


# --------------------------------------------------------------------------- #
# rendering / execution helpers
# --------------------------------------------------------------------------- #

def render(value: str, *, event: str = "", all_input: str = "", paths: str = "",
           before: str = "", sha: str = "", count: str = "", listing: str = "",
           run_id: str = "12345") -> str:
    """Substitute the expressions the processing workflows use."""
    table = {
        "github.event_name": event,
        "inputs.all": all_input,
        "inputs.paths": paths,
        "github.event.before": before,
        "github.sha": sha,
        "steps.changes.outputs.count": count,
        "steps.changes.outputs.list": listing,
        "github.run_id": run_id,
    }

    def substitute(match: re.Match) -> str:
        expression = match.group(1).strip()
        assert expression in table, f"unhandled expression: {expression!r}"
        return table[expression]

    return EXPRESSION.sub(substitute, value)


def step_environment(step: dict, **ctx) -> dict[str, str]:
    return {key: render(str(value), **ctx) for key, value in (step.get("env") or {}).items()}


def base_env(tmp_path: Path) -> dict[str, str]:
    return {
        "RUNNER_TEMP": str(tmp_path),
        "GITHUB_OUTPUT": str(tmp_path / "github_output"),
        "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md"),
        "PYTHONPATH": str(ROOT),
        "PATH": f"{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}",
    }


def run_step(step: dict, tmp_path: Path, **ctx) -> tuple[subprocess.CompletedProcess, dict]:
    """Execute the step's real run block with its declared env, like a runner."""
    output = tmp_path / "github_output"
    output.touch()
    env = dict(os.environ, **base_env(tmp_path), **step_environment(step, **ctx))
    proc = subprocess.run(["bash", "-c", step["run"]], cwd=ROOT, capture_output=True,
                          text=True, env=env)
    values = dict(line.split("=", 1) for line in output.read_text().splitlines() if "=" in line)
    return proc, values


def run_script(script: str, tmp_path: Path, **env_extra) -> subprocess.CompletedProcess:
    output = tmp_path / "github_output"
    output.touch()
    env = dict(os.environ, **base_env(tmp_path), **env_extra)
    return subprocess.run(["bash", "-c", script], cwd=ROOT, capture_output=True,
                          text=True, env=env)


def git_head() -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                          text=True, check=True).stdout.strip()


# --------------------------------------------------------------------------- #
# workflow structure
# --------------------------------------------------------------------------- #

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


@pytest.mark.parametrize("path", ALL_WORKFLOWS, ids=lambda p: p.name)
def test_no_expression_is_interpolated_into_shell(path: Path):
    """Bash must never receive a ``${{ … }}`` expression: pass it via env instead."""
    offenders = []
    for job_name, step in all_steps(path):
        run = step.get("run") or ""
        for line in run.splitlines():
            if "${{" in line:
                offenders.append(f"{path.name}:{job_name}:{step.get('name')}: {line.strip()}")
    assert not offenders, "expressions in run blocks:\n" + "\n".join(offenders)


@pytest.mark.parametrize("kind", sorted(WORKFLOWS))
def test_dispatch_paths_are_delivered_through_the_environment(kind: str):
    path = WORKFLOWS[kind]
    resolve = step_named(path, "Resolve changed")
    assert resolve["env"]["INPUT_PATHS"] == "${{ inputs.paths }}"
    assert "inputs.paths" not in resolve["run"]
    # the untrusted value must not reach bash word-splitting either
    assert "$INPUT_PATHS" in resolve["run"]
    assert "for p in" not in resolve["run"]


@pytest.mark.parametrize("kind", sorted(WORKFLOWS))
def test_resolve_step_uses_the_secure_parser(kind: str):
    resolve = step_named(WORKFLOWS[kind], "Resolve changed")
    run = resolve["run"]
    assert "scripts/dispatch_paths.py" in run
    assert f"--kind {kind}" in run
    assert '--paths-file "$PATHS_FILE"' in run
    assert "--null" in run


# --------------------------------------------------------------------------- #
# resolve step behaviour
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("kind", sorted(WORKFLOWS))
def test_resolve_step_emits_real_nul_token_for_all_true(kind: str, tmp_path: Path):
    step = step_named(WORKFLOWS[kind], "Resolve changed")
    proc, values = run_step(step, tmp_path, event="workflow_dispatch", all_input="true")
    assert proc.returncode == 0, proc.stderr
    assert values["count"] == "1", proc.stdout + proc.stderr
    assert Path(values["list"]).read_bytes() == b"--all\x00", "must be a NUL token, not the text \\0"


@pytest.mark.parametrize("kind", sorted(WORKFLOWS))
def test_resolve_step_reports_zero_for_empty_diff(kind: str, tmp_path: Path):
    step = step_named(WORKFLOWS[kind], "Resolve changed")
    head = git_head()
    proc, values = run_step(step, tmp_path, event="push", before=head, sha=head)
    assert proc.returncode == 0, proc.stderr
    assert values["count"] == "0"
    assert Path(values["list"]).read_bytes() == b""


@pytest.mark.parametrize("kind", sorted(WORKFLOWS))
def test_resolve_step_requests_full_processing_for_zero_sha(kind: str, tmp_path: Path):
    step = step_named(WORKFLOWS[kind], "Resolve changed")
    proc, values = run_step(step, tmp_path, event="push", before="0" * 40, sha="0" * 40)
    assert proc.returncode == 0, proc.stderr
    assert values["count"] == "1"
    assert Path(values["list"]).read_bytes() == b"--all\x00"


@pytest.mark.parametrize("kind", sorted(WORKFLOWS))
def test_resolve_step_fails_fast_when_the_list_cannot_be_written(kind: str, tmp_path: Path):
    """set -euo pipefail must abort the step instead of reporting an empty change set."""
    step = step_named(WORKFLOWS[kind], "Resolve changed")
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("x")
    env = dict(os.environ, **base_env(tmp_path))
    env["RUNNER_TEMP"] = str(blocker)
    env.update(step_environment(step, event="push", before="HEAD~1", sha="HEAD"))
    proc = subprocess.run(["bash", "-c", step["run"]], cwd=ROOT, capture_output=True,
                          text=True, env=env)
    assert proc.returncode != 0, "a failing resolve step must fail the workflow"


@pytest.mark.parametrize("kind,asset", [
    ("model", "assets/models/props/crate.glb"),
    ("animation", "assets/animation/locomotion/walk.bvh"),
])
def test_resolve_step_accepts_a_valid_dispatch_path(kind: str, asset: str, tmp_path: Path):
    """The dispatch input is newline-delimited, so one path produces one token."""
    step = step_named(WORKFLOWS[kind], "Resolve changed")
    proc, values = run_step(step, tmp_path, event="workflow_dispatch", paths=asset)
    assert proc.returncode == 0, proc.stderr
    assert Path(values["list"]).read_bytes() == asset.encode() + b"\x00"


@pytest.mark.parametrize("kind", sorted(WORKFLOWS))
def test_resolve_step_supports_multiple_dispatch_paths(kind: str, tmp_path: Path):
    step = step_named(WORKFLOWS[kind], "Resolve changed")
    assets = ["assets/models/props/crate.glb", "assets/models/props/invalid.glb"]
    if kind == "animation":
        assets = ["assets/animation/locomotion/walk.bvh",
                  "assets/animation/locomotion/run.bvh"]
    proc, values = run_step(step, tmp_path, event="workflow_dispatch",
                            paths="\n".join(assets))
    assert proc.returncode == 0, proc.stderr
    assert Path(values["list"]).read_bytes() == b"".join(a.encode() + b"\x00" for a in assets)


@pytest.mark.parametrize("payload", [
    "assets/models/props/crate.glb; touch {marker}",
    "$(touch {marker})",
    "`touch {marker}`",
    "assets/models/props/crate.glb\ntouch {marker}",
    "../../../etc/passwd",
    "/etc/passwd",
    "assets/animation/locomotion/walk.bvh",
    "assets/models/props/crate.glb\n\n",
])
def test_resolve_step_rejects_malicious_dispatch_input(payload: str, tmp_path: Path):
    """A hostile dispatch input fails the job and leaves no trace behind."""
    step = step_named(WORKFLOWS["model"], "Resolve changed")
    marker = tmp_path / "pwned"
    attack = payload.format(marker=marker)
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}

    proc, values = run_step(step, tmp_path, event="workflow_dispatch", paths=attack)

    assert proc.returncode != 0, f"hostile input was accepted: {attack!r}"
    assert not marker.exists(), f"payload executed a command: {attack!r}"
    assert values.get("count") in (None, "", "0"), "a rejected input must not process anything"
    listed = tmp_path / LIST_NAMES["model"]
    assert listed.read_bytes() == b"", "no tokens may survive a rejected input"
    # The step may create its own scratch files; nothing else may appear or change.
    own_files = {LIST_NAMES["model"], f"{LIST_NAMES['model']}.input", "github_output"}
    after = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    assert set(after) - own_files == set(before) - own_files
    assert {k: v for k, v in after.items() if k in before} == before


@pytest.mark.parametrize("kind", sorted(WORKFLOWS))
def test_process_step_consumes_the_list_with_xargs_null(kind: str):
    step = step_named(WORKFLOWS[kind], "Process ")
    assert "xargs -0 -r" in step["run"]
    assert "$CHANGE_LIST" in step["run"]
    assert step["env"]["CHANGE_LIST"] == "${{ steps.changes.outputs.list }}"
    assert step["if"] == "steps.changes.outputs.count != '0'"


def test_null_tokens_survive_xargs(tmp_path: Path):
    """The exact xargs invocation keeps spaces, quotes and unicode intact."""
    names = ["assets/models/props/with space.glb",
             "assets/models/props/quote'and$dollar.glb",
             "assets/models/props/ünïcode.glb"]
    listing = tmp_path / "list.bin"
    listing.write_bytes(b"".join(name.encode() + b"\0" for name in names))
    proc = subprocess.run(
        ["bash", "-c", "xargs -0 -r python3 -c \"import sys;print(chr(10).join(sys.argv[1:]))\" "
                       f"< \"{listing}\""],
        capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.splitlines() == names


# --------------------------------------------------------------------------- #
# push detection through the real resolve step
# --------------------------------------------------------------------------- #

def make_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    for args in (["init", "-q", "-b", "main"],
                 ["config", "user.email", "ci@example.com"],
                 ["config", "user.name", "CI"],
                 ["config", "commit.gpgsign", "false"]):
        subprocess.run(["git", *args], cwd=path, check=True, capture_output=True)
    (path / "assets/models/props").mkdir(parents=True)
    (path / "assets/animation/locomotion").mkdir(parents=True)
    return path


def commit(repo: Path, message: str) -> str:
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", message], cwd=repo, check=True, capture_output=True)
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.mark.parametrize("kind,changed,ignored", [
    ("model", "assets/models/props/with space ünïcode.glb", "assets/animation/locomotion/a.bvh"),
    ("animation", "assets/animation/locomotion/with space ünïcode.bvh",
     "assets/models/props/a.glb"),
])
def test_push_detection_reports_only_the_matching_asset(kind: str, changed: str,
                                                        ignored: str, tmp_path: Path):
    repo = make_repo(tmp_path / "repo")
    (repo / "assets/models/props/base.glb").write_bytes(b"x")
    (repo / "assets/animation/locomotion/base.bvh").write_bytes(b"x")
    base = commit(repo, "base")
    (repo / changed).write_bytes(b"x")
    (repo / ignored).write_bytes(b"x")
    head = commit(repo, "add assets")

    step = step_named(WORKFLOWS[kind], "Resolve changed")
    env = dict(os.environ, **base_env(tmp_path), **step_environment(
        step, event="push", before=base, sha=head))
    env.update({"GIT_DIR": str(repo / ".git"), "GIT_WORK_TREE": str(repo)})
    proc = subprocess.run(["bash", "-c", step["run"]], cwd=ROOT, capture_output=True,
                          text=True, env=env)
    assert proc.returncode == 0, proc.stderr
    output = dict(line.split("=", 1) for line in
                  (tmp_path / "github_output").read_text().splitlines() if "=" in line)
    assert output["count"] == "1"
    assert Path(output["list"]).read_bytes() == changed.encode() + b"\x00"


def test_deletion_only_push_is_skipped(tmp_path: Path):
    """Deleting an asset must not hand a missing path to the pipeline."""
    repo = make_repo(tmp_path / "repo")
    (repo / "assets/models/props/gone.glb").write_bytes(b"x")
    base = commit(repo, "add")
    (repo / "assets/models/props/gone.glb").unlink()
    head = commit(repo, "delete")

    step = step_named(WORKFLOWS["model"], "Resolve changed")
    env = dict(os.environ, **base_env(tmp_path), **step_environment(
        step, event="push", before=base, sha=head))
    env.update({"GIT_DIR": str(repo / ".git"), "GIT_WORK_TREE": str(repo)})
    proc = subprocess.run(["bash", "-c", step["run"]], cwd=ROOT, capture_output=True,
                          text=True, env=env)
    assert proc.returncode == 0, proc.stderr
    output = dict(line.split("=", 1) for line in
                  (tmp_path / "github_output").read_text().splitlines() if "=" in line)
    assert output["count"] == "0"
    assert Path(output["list"]).read_bytes() == b""


# --------------------------------------------------------------------------- #
# Pages publication rules
# --------------------------------------------------------------------------- #
PAGES = ROOT / ".github/workflows/pages.yml"
ANDROID = ROOT / ".github/workflows/android-build.yml"


def test_pages_publishes_automatically_only_from_main():
    workflow = yaml.safe_load(PAGES.read_text())
    guard = workflow["jobs"]["deploy"]["if"]

    assert "github.ref == 'refs/heads/main'" in guard
    # A branch under review may be published, but only by asking for it explicitly.
    assert "workflow_dispatch" in guard and "inputs.deploy == true" in guard

    inputs = workflow[True]["workflow_dispatch"]["inputs"]  # `on:` parses as True
    assert inputs["deploy"]["type"] == "boolean"
    assert inputs["deploy"]["default"] is False


def test_pages_deploy_job_verifies_the_live_site_and_the_apk_url():
    workflow = yaml.safe_load(PAGES.read_text())
    steps = {step.get("name"): step for step in workflow["jobs"]["deploy"]["steps"]}

    assert any(name and "Verify the deployed catalog over HTTPS" in name for name in steps)
    cross_check = next(step for name, step in steps.items()
                       if "Cross-check the deployed URL" in (name or ""))
    assert "steps.deployment.outputs.page_url" in cross_check["env"]["PAGES_URL"]
    # The step greps android/gradle.properties for the property the APK ships.
    assert "pagesBaseUrl" in cross_check["run"]
    assert "android/gradle.properties" in cross_check["run"]


def test_android_workflow_runs_the_live_smoke_test_and_tolerates_an_outage():
    workflow = yaml.safe_load(ANDROID.read_text())
    step = next(s for s in workflow["jobs"]["build"]["steps"]
                if "Live catalog smoke test" in (s.get("name") or ""))

    assert "https://adii14990adi-spec.github.io/3dmodel-gen-system-" in \
        step["env"]["ASSET_STUDIO_LIVE_BASE_URL"]
    assert "LiveCatalogSmokeTest" in step["run"]
    # A Pages outage must warn, not block the APK: only a failed test run exits non-zero.
    assert 'if [ "$code" != "200" ]' in step["run"]
    assert "::warning title=Live catalog unavailable::" in step["run"]
