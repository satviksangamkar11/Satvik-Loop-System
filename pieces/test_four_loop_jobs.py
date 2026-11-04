"""Four-loop jobs (docs/pilot/four-loop, the 4-loop test after closure step 6): each job loads under the test profile, its files are disjoint, and its negative controls and
baseline show that its acceptance tests fail on wrong code. Plain pytest, no Docker: each check runs the acceptance tests in a throw-away tree, and
the verdict uses the loop's own gate (p0_gates.must_fail_problems)."""
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

JOBS = REPO / "docs" / "pilot" / "four-loop"
JOB_FILES = ["fl-clamp.json", "fl-chunk.json", "fl-dedupe.json", "fl-wordcount.json", "fl-top.json"]

# The merged state of job fl-wordcount, as fl-top needs it on main. A plain stand-in: fl-wordcount's own acceptance tests prove the real module.
WORDCOUNT_ON_MAIN = ("import re\n\n\ndef word_counts(text):\n    out = {}\n    for w in re.findall(r'[a-z0-9]+', text.lower()):\n"
                     "        out[w] = out.get(w, 0) + 1\n    return out\n")
STAND_INS = {"fl-top.json": {"scripts/source/fl_wordcount.py": WORDCOUNT_ON_MAIN}}


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


def load(name, profile="test"):
    import prod_harness as ph
    with under(profile), ph.controller_rule_waived():     # historical pilot job (I53: edits scripts/source); see prod_harness
        return fl.load_job(JOBS / name)


def test_each_job_loads_under_the_test_profile():
    for name in JOB_FILES:
        job = load(name)
        assert job["profile"] == "production", name
        assert job["visibility"] == "public", name
        assert job["budget"] == {"free_requests": 40, "review_usd": 0, "paid_usd": 0}, name


def test_each_job_is_refused_under_the_production_profile():
    """The budget is the profile's: review_usd 0 here is exactly what the test profile allows, so production refuses these jobs."""
    for name in JOB_FILES:
        with pytest.raises(ValueError, match="budget"):
            load(name, profile="production")


def test_jobs_touch_disjoint_files_and_fl_top_reads_fl_wordcount():
    jobs = {name: load(name) for name in JOB_FILES}
    owner = {}
    for name, job in jobs.items():
        for rel in job["files"]:
            assert rel not in owner, f"{rel} is in {name} and {owner[rel]}"
            owner[rel] = name
    assert "scripts/source/fl_wordcount.py" in jobs["fl-top.json"]["reference"]
    assert "scripts/source/fl_wordcount.py" in jobs["fl-wordcount.json"]["files"]


def test_each_job_names_every_acceptance_test_in_its_baseline():
    """Every test in a job's acceptance file is in acceptance_baseline_fail, so each one is shown to fail on the missing module."""
    for name in JOB_FILES:
        job = load(name)
        for rel in job["acceptance_tests"]:
            defined = {f"{rel}::{n.name}" for n in ast.parse((REPO / rel).read_text(encoding="utf-8")).body
                       if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")}
            assert defined == set(job["acceptance_baseline_fail"]), name


def run_acceptance(tmp_path, job, module_src, stand_ins):
    """Copy the job's acceptance tests into a throw-away tree, write the module (or leave it out when module_src is None), and run pytest there.
    Returns (junit_xml, returncode) for p0_gates.must_fail_problems."""
    tree = tmp_path / "tree"
    for rel in job["acceptance_tests"]:
        dst = tree / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO / rel, dst)
    for rel, text in stand_ins.items():
        (tree / rel).parent.mkdir(parents=True, exist_ok=True)
        (tree / rel).write_text(text, encoding="utf-8")
    if module_src is not None:
        dst = tree / module_src[0]
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO / module_src[1], dst)
    junit = tmp_path / "junit.xml"
    env = dict(os.environ)
    env.pop("PYTEST_ADDOPTS", None)
    env.pop("SATVIK_PROFILE", None)
    # xunit1 carries the `file` attribute that p0_gates reads; the sandbox's controller ini sets the same family (sandbox_runner.CONTROLLER_INI)
    cmd = [sys.executable, "-m", "pytest", "--rootdir", str(tree), "-p", "no:cacheprovider", "-q", "--tb=short",
           "-o", "junit_family=xunit1", f"--junitxml={junit}", *job["acceptance_tests"]]
    proc = subprocess.run(cmd, cwd=str(tree), env=env, capture_output=True, text=True, timeout=300)
    xml = junit.read_text(encoding="utf-8") if junit.is_file() else None
    return xml, proc.returncode


CASES = [(name, index) for name in JOB_FILES for index in range(len(load(name)["negative_controls"]))]


@pytest.mark.parametrize("name,index", CASES, ids=[f"{n}:{i}" for n, i in CASES])
def test_each_negative_control_fails_its_acceptance_tests(tmp_path, name, index):
    job = load(name)
    control = job["negative_controls"][index]
    ((target, src),) = control["replace"].items()
    xml, code = run_acceptance(tmp_path, job, (target, src), STAND_INS.get(name, {}))
    assert gates.must_fail_problems(xml, control["must_fail"], code, False) == [], control["name"]


@pytest.mark.parametrize("name", JOB_FILES)
def test_each_declared_baseline_failure_fails_without_the_module(tmp_path, name):
    job = load(name)
    xml, code = run_acceptance(tmp_path, job, None, STAND_INS.get(name, {}))
    assert gates.must_fail_problems(xml, job["acceptance_baseline_fail"], code, False) == []
