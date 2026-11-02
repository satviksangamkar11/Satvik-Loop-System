"""P0 (2026-11-02): locked acceptance tests and per-test pytest verdicts.

The coder's own tests are evidence only. A production job passes only when the controller-owned acceptance tests pass, every expected acceptance test
was collected and passed (no skip, no xfail, no collection error, nothing missing), the acceptance files did not change, and the acceptance tests were
first shown to FAIL against a known-wrong implementation (negative control) or the buggy baseline.

These tests use a LOCAL executor (plain pytest in a throw-away copy) so the gate logic is tested on any machine. The Docker boundary itself is tested in
test_p0_sandbox.py. The local executor is test-only: production never runs generated code without the sandbox."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "source"))
import fix_loop as fl  # noqa: E402
import p0_gates as gates  # noqa: E402
import sandbox_runner as sr  # noqa: E402
from test_fix_loop_review import FENCE, FILE_MARK  # noqa: E402

NL = "\n"


class LocalExecutor:
    """Test double with the DockerSandbox interface: copies the repo to a temp dir, applies overlays, runs real pytest with JUnit XML. NOT a sandbox."""

    def __init__(self):
        self.runs = []

    def selftest(self):
        return []

    def run(self, root, tests, overlay=None, readonly=(), timeout_s=120):
        self.runs.append({"tests": list(tests), "overlay": dict(overlay or {})})
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "work"
            shutil.copytree(root, work, ignore=shutil.ignore_patterns("_loop", "_moss", "secrets", ".git", "__pycache__"))
            for rel, src in (overlay or {}).items():
                dst = work / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, dst)
            junit = Path(tmp) / "junit.xml"
            ini = Path(tmp) / "p0.ini"
            ini.write_text(sr.CONTROLLER_INI, encoding="utf-8")
            cmd = [sys.executable, "-m", "pytest", "-c", str(ini), "--rootdir", str(work), "-p", "no:cacheprovider", "--runxfail", "-q", "--tb=short",
                   "-rfE", "--junitxml", str(junit)] + list(tests)
            env = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST")}
            proc = subprocess.run(cmd, cwd=work, capture_output=True, text=True, timeout=timeout_s, env=env)
            xml = junit.read_text(encoding="utf-8") if junit.is_file() else None
            return sr.RunResult(proc.returncode, proc.stdout + proc.stderr, xml, False)


# ---------------------------------------------------------------- JUnit verdict (real pytest output, no fakes)

def _junit_for(tmp_path, files, select=None):
    root = tmp_path / "r"
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    tests = select or sorted(files)
    res = LocalExecutor().run(root, tests)
    expected = gates.expected_test_ids(root, sorted(r for r in files if Path(r).name.startswith("test_")))
    return res, expected


def test_all_passing_tests_are_accepted(tmp_path):
    res, exp = _junit_for(tmp_path, {"tests/test_a.py": "import pytest" + NL + "def test_one():" + NL + "    assert 1 == 1" + NL
                                     + "@pytest.mark.parametrize('x', [1, 2])" + NL + "def test_two(x):" + NL + "    assert x" + NL
                                     + "class TestC:" + NL + "    def test_m(self):" + NL + "        assert True" + NL})
    assert gates.verify_junit(res.junit, exp, res.returncode, res.timed_out) == []


def test_a_skipped_test_is_rejected_even_when_pytest_exits_zero(tmp_path):
    res, exp = _junit_for(tmp_path, {"tests/test_a.py": "import pytest" + NL + "def test_ok():" + NL + "    assert True" + NL
                                     + "def test_skips():" + NL + "    pytest.skip('later')" + NL})
    assert res.returncode == 0
    problems = gates.verify_junit(res.junit, exp, res.returncode, res.timed_out)
    assert any("skipped" in p for p in problems)


def test_an_xfail_marked_test_is_run_and_its_failure_rejected(tmp_path):
    res, exp = _junit_for(tmp_path, {"tests/test_a.py": "import pytest" + NL + "@pytest.mark.xfail" + NL + "def test_x():" + NL + "    assert False" + NL})
    problems = gates.verify_junit(res.junit, exp, res.returncode, res.timed_out)
    assert problems


def test_a_deselected_or_missing_test_is_rejected(tmp_path):
    files = {"tests/test_a.py": "def test_one():" + NL + "    assert True" + NL + "def test_two():" + NL + "    assert True" + NL}
    res, exp = _junit_for(tmp_path, files, select=["tests/test_a.py::test_one"])
    problems = gates.verify_junit(res.junit, exp, res.returncode, res.timed_out)
    assert any("test_two" in p and "missing" in p for p in problems)


def test_a_collection_error_is_rejected(tmp_path):
    res, exp = _junit_for(tmp_path, {"tests/test_a.py": "import no_such_module_xyz" + NL + "def test_one():" + NL + "    assert True" + NL})
    assert gates.verify_junit(res.junit, exp, res.returncode, res.timed_out)


def test_missing_or_broken_junit_is_never_a_pass():
    assert gates.verify_junit(None, {"t.py": {"test_a"}}, 0, False)
    assert gates.verify_junit("<not xml", {"t.py": {"test_a"}}, 0, False)
    assert gates.verify_junit('<testsuites><testsuite tests="0"/></testsuites>', {"t.py": {"test_a"}}, 0, False)
    assert gates.verify_junit('<testsuites/>', {}, 0, False)                     # zero tests proves nothing


def test_a_nonzero_exit_or_timeout_is_never_a_pass(tmp_path):
    res, exp = _junit_for(tmp_path, {"tests/test_a.py": "def test_one():" + NL + "    assert True" + NL})
    assert gates.verify_junit(res.junit, exp, 1, False)
    assert gates.verify_junit(res.junit, exp, 0, True)


# ---------------------------------------------------------------- production job validation

def make_prod_root(tmp_path):
    root = tmp_path / "repo"
    for d in ("docs", "specs", "pieces", "_loop/jobs", "tests/acceptance", "tests/controls"):
        (root / d).mkdir(parents=True, exist_ok=True)
    (root / "docs" / "RP.md").write_text("review", encoding="utf-8")
    (root / "specs" / "s.md").write_text("calculate_total(prices) returns the sum of the prices.", encoding="utf-8")
    (root / "tests" / "acceptance" / "test_calc_acceptance.py").write_text(
        "from pieces.calc import calculate_total" + NL
        + "def test_sums_two_prices():" + NL + "    assert calculate_total([20, 30]) == 50" + NL
        + "def test_sums_three_prices():" + NL + "    assert calculate_total([1, 2, 3]) == 6" + NL
        + "def test_empty_is_zero():" + NL + "    assert calculate_total([]) == 0" + NL, encoding="utf-8")
    (root / "tests" / "controls" / "calc_returns_none.py").write_text("def calculate_total(prices):" + NL + "    return None" + NL, encoding="utf-8")
    (root / "tests" / "controls" / "calc_returns_constant.py").write_text("def calculate_total(prices):" + NL + "    return 50" + NL, encoding="utf-8")
    return root


def prod_job(**over):
    job = {"name": "calc", "profile": "production", "spec": "specs/s.md", "files": ["pieces/calc.py", "pieces/test_calc.py"], "tests": ["pieces/test_calc.py"],
           "reference": [], "max_rounds": 1, "review_rounds": 1, "review": True, "reviewers": ["lightning", "codex"], "review_prompt": "docs/RP.md",
           "fixers": ["nemotron"] * 3, "acceptance_tests": ["tests/acceptance/test_calc_acceptance.py"],
           "negative_controls": [
               {"name": "returns None", "replace": {"pieces/calc.py": "tests/controls/calc_returns_none.py"},
                "must_fail": ["tests/acceptance/test_calc_acceptance.py::test_sums_two_prices"]},
               {"name": "returns a constant", "replace": {"pieces/calc.py": "tests/controls/calc_returns_constant.py"},
                "must_fail": ["tests/acceptance/test_calc_acceptance.py::test_sums_three_prices"]}]}
    job.update(over)
    return job


def write_job(root, job):
    p = root / "_loop" / "jobs" / (job["name"] + ".json")
    p.write_text(json.dumps(job), encoding="utf-8")
    return p


def test_a_valid_production_job_loads(tmp_path):
    root = make_prod_root(tmp_path)
    job = fl.load_job(write_job(root, prod_job()))
    assert job["profile"] == "production"


@pytest.mark.parametrize("over,needle", [
    ({"acceptance_tests": []}, "acceptance_tests"),
    ({"negative_controls": [], "acceptance_baseline_fail": []}, "negative control"),
    ({"files": ["pieces/calc.py", "pieces/test_calc.py", "tests/acceptance/test_calc_acceptance.py"]}, "coder-editable"),
    ({"files": ["pieces/calc.py", "pieces/test_calc.py", "tests/acceptance/conftest.py"]}, "conftest"),
    ({"files": ["pieces/calc.py", "pieces/test_calc.py", "pytest.ini"]}, "pytest.ini"),
    ({"files": ["pieces/calc.py", "pieces/test_calc.py", "pieces/pathlib.py"]}, "shadows"),
    ({"files": ["pieces/calc.py", "pieces/test_calc.py", "sitecustomize.py"]}, "shadows"),
    ({"files": ["pieces/calc.py", "pieces/test_calc.py", "tests/controls/calc_returns_none.py"]}, "coder-editable"),
    ({"negative_controls": [{"name": "x", "replace": {"pieces/calc.py": "tests/controls/calc_returns_none.py"},
                             "must_fail": ["tests/acceptance/other.py::test_a"]}]}, "not an acceptance test"),
    ({"sandbox_include": ["_moss"]}, "_moss"),
    ({"regression": ["tests/regress/test_old.py"], "files": ["pieces/calc.py", "pieces/test_calc.py", "tests/regress/test_old.py"]}, "coder-editable"),
])
def test_unsafe_production_jobs_are_refused(tmp_path, over, needle):
    root = make_prod_root(tmp_path)
    with pytest.raises(ValueError, match=needle):
        fl.load_job(write_job(root, prod_job(**over)))


# ---------------------------------------------------------------- the false-pass regression, end to end through run_job

CONSTANT_CODE = "def calculate_total(prices):" + NL + "    return 100" + NL
CONSTANT_TEST = "from pieces.calc import calculate_total" + NL + "def test_calculate_total():" + NL + "    assert calculate_total([20, 30]) == 100" + NL
GOOD_CODE = "def calculate_total(prices):" + NL + "    return sum(prices)" + NL
GOOD_TEST = "from pieces.calc import calculate_total" + NL + "def test_calculate_total():" + NL + "    assert calculate_total([20, 30]) == 50" + NL


def coder_reply(code, test):
    return (FILE_MARK + "pieces/calc.py" + NL + FENCE + "python" + NL + code + FENCE + NL
            + FILE_MARK + "pieces/test_calc.py" + NL + FENCE + "python" + NL + test + FENCE + NL)


def ready(files, report):
    report.write_text("## Verdict: READY" + NL, encoding="utf-8")
    return {"verdict": "READY"}


def run_prod(root, code, test, sandbox=None, reviewers=None, **over):
    job = fl.load_job(write_job(root, prod_job(**over)))
    calls = []

    def coder(s, u):
        calls.append(1)
        return coder_reply(code, test), {"status": 200}

    result = fl.run_job(job, root, caller=coder, reviewer=reviewers or {"lightning": ready, "codex": ready}, scrub_keys=[],
                        sandbox=sandbox or LocalExecutor())
    return result, calls


def test_constant_return_with_matching_coder_tests_is_rejected(tmp_path):
    """The reported false pass: the implementation returns 100 and the coder's own test expects 100. The coder test passes; the locked
    acceptance tests fail; the job must not pass."""
    root = make_prod_root(tmp_path)
    result, calls = run_prod(root, CONSTANT_CODE, CONSTANT_TEST)
    assert calls, "the coder was called"
    assert result["state"] != "passed"
    assert result["tests_passed"] is False
    assert not (root / "pieces" / "calc.py").exists()          # originals restored: the file did not exist before the job


def test_a_correct_implementation_passes_with_evidence(tmp_path):
    root = make_prod_root(tmp_path)
    result, _ = run_prod(root, GOOD_CODE, GOOD_TEST)
    assert result["state"] == "passed", result
    ev = result["evidence"]
    assert ev["tested_hash"] == ev["reviewed_hash"] == ev["final_hash"]
    assert set(ev["acceptance_lock"]) >= {"tests/acceptance/test_calc_acceptance.py", "tests/controls/calc_returns_none.py"}


def test_acceptance_tests_that_cannot_fail_stop_the_job_before_any_coder_call(tmp_path):
    root = make_prod_root(tmp_path)
    (root / "tests" / "acceptance" / "test_calc_acceptance.py").write_text(
        "def test_sums_two_prices():" + NL + "    assert True" + NL + "def test_sums_three_prices():" + NL + "    assert True" + NL, encoding="utf-8")
    result, calls = run_prod(root, GOOD_CODE, GOOD_TEST)
    assert result["state"] == "acceptance-invalid"
    assert calls == []


def test_a_baseline_regression_test_must_fail_on_the_buggy_baseline(tmp_path):
    root = make_prod_root(tmp_path)
    (root / "pieces" / "calc.py").write_text(GOOD_CODE, encoding="utf-8")         # the "bug" is already absent: the regression proves nothing
    result, calls = run_prod(root, GOOD_CODE, GOOD_TEST, negative_controls=[],
                             acceptance_baseline_fail=["tests/acceptance/test_calc_acceptance.py::test_sums_two_prices"])
    assert result["state"] == "acceptance-invalid"
    assert calls == []


def test_a_real_baseline_failure_is_accepted_as_the_sensitivity_proof(tmp_path):
    root = make_prod_root(tmp_path)
    (root / "pieces" / "calc.py").write_text("def calculate_total(prices):" + NL + "    return sum(prices[1:])" + NL, encoding="utf-8")   # the bug
    result, calls = run_prod(root, GOOD_CODE, GOOD_TEST, negative_controls=[],
                             acceptance_baseline_fail=["tests/acceptance/test_calc_acceptance.py::test_sums_two_prices"])
    assert result["state"] == "passed", result


def test_existing_valid_invariants_may_already_pass_on_the_baseline(tmp_path):
    """Only the tests named in must_fail / acceptance_baseline_fail must fail; test_empty_is_zero passes on the buggy baseline and that is fine."""
    root = make_prod_root(tmp_path)
    (root / "pieces" / "calc.py").write_text("def calculate_total(prices):" + NL + "    return sum(prices[1:])" + NL, encoding="utf-8")
    result, _ = run_prod(root, GOOD_CODE, GOOD_TEST, negative_controls=[],
                         acceptance_baseline_fail=["tests/acceptance/test_calc_acceptance.py::test_sums_two_prices"])
    assert result["state"] == "passed"


def test_a_coder_test_that_skips_is_not_a_pass(tmp_path):
    root = make_prod_root(tmp_path)
    skip_test = "import pytest" + NL + "def test_calculate_total():" + NL + "    pytest.skip('no time')" + NL
    result, _ = run_prod(root, GOOD_CODE, skip_test)
    assert result["state"] != "passed"


def test_an_acceptance_file_changed_during_the_job_voids_the_pass(tmp_path):
    root = make_prod_root(tmp_path)
    acc = root / "tests" / "acceptance" / "test_calc_acceptance.py"

    def tamper(files, report):
        acc.write_text(acc.read_text(encoding="utf-8") + "# changed" + NL, encoding="utf-8")
        report.write_text("## Verdict: READY" + NL, encoding="utf-8")
        return {"verdict": "READY"}

    result, _ = run_prod(root, GOOD_CODE, GOOD_TEST, reviewers={"lightning": tamper, "codex": ready})
    assert result["state"] == "acceptance-tampered"


def test_production_stops_when_the_sandbox_is_unavailable(tmp_path):
    root = make_prod_root(tmp_path)

    class Broken(LocalExecutor):
        def selftest(self):
            return ["docker daemon not reachable"]

    result, calls = run_prod(root, GOOD_CODE, GOOD_TEST, sandbox=Broken())
    assert result["state"] == "sandbox-unavailable"
    assert calls == []
