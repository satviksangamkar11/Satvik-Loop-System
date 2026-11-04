"""Production smoke job ps-duration (docs/pilot/prod-smoke): the job loads and validates with the REAL rules under SATVIK_PROFILE=production, with
NO controller-rule waiver (prod_harness is not used). No model call, no network, no Docker. The locked acceptance tests and the negative controls
are checked on wrong code in a throw-away tree, with the loop's own gate (p0_gates.must_fail_problems)."""
import ast
import os
import shutil
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "source"))
import fix_loop as fl  # noqa: E402
import p0_gates as gates  # noqa: E402
import stage_config as sc  # noqa: E402

JOB = REPO / "docs" / "pilot" / "prod-smoke" / "ps-duration.job.json"
SPEC = REPO / "docs" / "pilot" / "prod-smoke" / "SPEC.md"
BUDGET = {"free_requests": 40, "review_usd": 0.05, "paid_usd": 0}


@contextmanager
def under(profile):
    """SATVIK_PROFILE set to `profile` for the block, then restored. Explicit, so the check never depends on what another test module set at import."""
    old = os.environ.get("SATVIK_PROFILE")
    os.environ["SATVIK_PROFILE"] = profile
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("SATVIK_PROFILE", None)
        else:
            os.environ["SATVIK_PROFILE"] = old


def load(profile="production"):
    with under(profile):                    # no controller_rule_waived(): the real directory rule applies
        return fl.load_job(JOB)


def test_the_job_loads_under_production_with_the_real_rules():
    job = load()
    assert job["name"] == "ps-duration" and job["profile"] == "production" and job["visibility"] == "public"
    assert job["budget"] == BUDGET
    assert job["reviewers"] == "by_lineage"
    assert job["fixers"] == ["writer_a", "writer_b"]
    assert job["deadline_min"] == 60
    assert job["spec"] == "docs/pilot/prod-smoke/SPEC.md"
    with under("production"):
        assert fl._p0().production_job_problems(job) == []


def test_the_job_is_refused_under_the_test_profile_budget():
    """review_usd 0.05 is the production budget; the test profile's is exactly 0, so the test profile refuses this job."""
    with pytest.raises(ValueError, match="budget"):
        load(profile="test")


def test_the_job_files_are_not_controller_files_and_do_not_collide():
    job = load()
    for rel in job["files"]:
        assert not gates.is_protected_control_file(rel), rel
    assert not set(job["files"]) & set(job["acceptance_tests"])
    for control in job["negative_controls"]:
        assert set(control["replace"]) <= set(job["files"])
        for src in control["replace"].values():
            assert (REPO / src).is_file(), src


def test_the_roster_stages_validate_under_production():
    """The production stage table (writers Dots and Xing, reviewer_a GLM 5.3 flash paid, reviewer_b Nemotron 3 Ultra free, repair and planner
    Opus through Claude Code) is valid. Opus itself stays off: see test_the_opus_switch_is_off."""
    assert sc.validate({}, "production") == []


def test_the_opus_switch_is_off():
    import opus_repair
    assert opus_repair.OPUS_ENABLED is False


def test_the_spec_has_only_known_lines():
    lines = [line for line in SPEC.read_text(encoding="utf-8").splitlines() if line.strip() and not line.startswith("#")]
    assert lines, "the spec has no requirement lines"
    for line in lines:
        assert line.startswith("- KNOWN: "), line
    assert "ASSUMED" not in SPEC.read_text(encoding="utf-8")


def test_the_acceptance_tests_define_exactly_the_baseline():
    job = load()
    for rel in job["acceptance_tests"]:
        defined = {f"{rel}::{n.name}" for n in ast.parse((REPO / rel).read_text(encoding="utf-8")).body
                   if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")}
        assert defined == set(job["acceptance_baseline_fail"])


def run_acceptance(tmp_path, job, module_src):
    """Copy the acceptance tests into a throw-away tree, write the module (or leave it out when module_src is None), and run pytest there.
    Returns (junit_xml, returncode) for p0_gates.must_fail_problems. The same invocation shape as the sandbox, without Docker."""
    tree = tmp_path / "tree"
    for rel in job["acceptance_tests"]:
        dst = tree / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO / rel, dst)
    if module_src is not None:
        dst = tree / module_src[0]
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO / module_src[1], dst)
    junit = tmp_path / "junit.xml"
    env = dict(os.environ)
    env.pop("PYTEST_ADDOPTS", None)
    env.pop("SATVIK_PROFILE", None)
    cmd = [sys.executable, "-m", "pytest", "--rootdir", str(tree), "-p", "no:cacheprovider", "-q", "--tb=short",
           "-o", "junit_family=xunit1", f"--junitxml={junit}", *job["acceptance_tests"]]
    proc = subprocess.run(cmd, cwd=str(tree), env=env, capture_output=True, text=True, timeout=300)
    xml = junit.read_text(encoding="utf-8") if junit.is_file() else None
    return xml, proc.returncode


@pytest.mark.parametrize("index", range(2))
def test_each_negative_control_fails_its_acceptance_tests(tmp_path, index):
    job = load()
    control = job["negative_controls"][index]
    ((target, src),) = control["replace"].items()
    xml, code = run_acceptance(tmp_path, job, (target, src))
    assert gates.must_fail_problems(xml, control["must_fail"], code, False) == [], control["name"]


def test_the_declared_baseline_fails_without_the_module(tmp_path):
    job = load()
    xml, code = run_acceptance(tmp_path, job, None)
    assert gates.must_fail_problems(xml, job["acceptance_baseline_fail"], code, False) == []
