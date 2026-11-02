"""P1b review package (2026-11-02, pre-pilot patch Steps 1 and 3). Written to FAIL on the code as it stood, then the fix made them pass.

Covers: the nine-section review package (order, SPEC numbering, DIFF, FILES, TESTS, RESULTS from the JUnit file of the last green run, advisory
STATIC WARNINGS, CONTEXT, OMITTED, CHECKS); the prompt-limit stop; the excluded-path rules (_loop, _moss, secrets, .git, .env, symlinks out of the
repo); a planted special-case caught by a reviewer that reads only the package; a static warning that never fails a job; and a provider failure
before the first write. No Docker, no network: a scripted sandbox and the scripted OpenRouter transport (prod_harness)."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "source"))
sys.path.insert(0, str(REPO / "pieces"))
import fix_loop as fl  # noqa: E402
import openrouter_call as oc  # noqa: E402
import p0_gates as gates  # noqa: E402
import p1b_production as pp  # noqa: E402
import p1b_routing as p1b  # noqa: E402
import prod_harness as ph  # noqa: E402
import sandbox_runner as sr  # noqa: E402
from test_p0_acceptance import make_prod_root, prod_job, GOOD_TEST  # noqa: E402
from test_pre_pilot_review import ORIGINAL_CODE, load, with_original, status_of  # noqa: E402

NL = "\n"
DOTS = XING = NORTH = GLM = None      # set per test by the profile fixture below (the roster is read per test, never at import)
PLANTED = "def calculate_total(prices):" + NL + "    if prices == [20, 30]:" + NL + "        return 50" + NL + "    return sum(prices)" + NL
WARN_CODE = "def calculate_total(prices):" + NL + "    if sum(prices) == 50:" + NL + "        return 50" + NL + "    return sum(prices)" + NL
CLEAN_CODE = "def calculate_total(prices):" + NL + "    return sum(prices)" + NL


class SpecialSandbox:
    """A normal run: every collected test passes. An overlay run (a negative control): every test fails with an AssertionError."""

    def __init__(self):
        self.runs = 0
        self.normal_runs = 0            # the job's own test runs (the negative-control calibration runs are overlays)

    def selftest(self):
        return []

    def run(self, root, tests, overlay=None, readonly=None, timeout_s=600):
        self.runs += 1
        self.normal_runs += 0 if overlay else 1
        root = Path(root)
        files = sorted({t.split("::")[0] for t in tests})
        expected = gates.expected_test_ids(root, files)
        cases = []
        for rel in files:
            module = rel[:-3].replace("/", ".")
            for tid in sorted(expected[rel] or []):
                if overlay:
                    cases.append(f'<testcase classname="{module}" name="{tid}" file="{rel}">'
                                 '<failure message="AssertionError: planted">x</failure></testcase>')
                else:
                    cases.append(f'<testcase classname="{module}" name="{tid}" file="{rel}"/>')
        xml = '<testsuites><testsuite name="fake">' + "".join(cases) + "</testsuite></testsuites>"
        return sr.RunResult(returncode=1 if overlay else 0, output="", junit=xml, timed_out=False)


class DownClient:
    """Every OpenRouter call is refused as a provider outage, before any write."""
    budget = oc.JobBudget(free_requests=40, review_usd=0.05)
    retry_log = []
    failed_attempts = []

    def begin_job(self):
        return None

    def set_job_deadline(self, seconds):
        return None

    def call(self, name, system, user, **kwargs):
        raise oc.ProviderUnavailable("provider down for this test")


def run_e2e(tmp_path, provider=None, sandbox=None, client=None, **job_over):
    root = with_original(make_prod_root(tmp_path))
    job = load(root, prod_job(**job_over))
    result = fl.run_job(job, root, sandbox=sandbox or SpecialSandbox(),
                        openrouter=client or ph.client(tmp_path, provider), scrub_keys=[])
    return root, result


def section_text(text, number, name):
    """The text of one section: from its header to the next '## ' header."""
    head = f"## {number}. {name}"
    start = text.index(head)
    rest = text[start + len(head):]
    end = rest.find(NL + "## ")
    return rest if end < 0 else rest[:end]


def package_of(root, job, junit=None, limit=None, originals=None):
    originals = originals if originals is not None else {"pieces/calc.py": ORIGINAL_CODE, "pieces/test_calc.py": None}
    return pp.build_package(root, job, originals, junit, limit=limit)


# ---------------------------------------------------------------- 1. nine sections, in order, the spec once

def test_the_package_has_the_nine_sections_in_order_and_the_spec_once(tmp_path):
    root = with_original(make_prod_root(tmp_path))
    text, info = package_of(root, prod_job())
    headers = ["## 1. SPEC", "## 2. DIFF", "## 3. FILES", "## 4. TESTS", "## 5. RESULTS", "## 6. STATIC WARNINGS",
               "## 7. CONTEXT", "## 8. OMITTED", "## 9. CHECKS"]
    positions = [text.index(h) for h in headers]
    assert positions == sorted(positions)
    assert text.count("calculate_total(prices) returns the sum of the prices.") == 1
    assert "SPEC 1: calculate_total(prices) returns the sum of the prices." in text
    assert info["spec_count"] == 1


# ---------------------------------------------------------------- 2. RESULTS: passed, failed, skipped, missing from the JUnit file

def test_results_lists_passed_failed_skipped_and_missing_tests_from_the_junit_file():
    junit = ('<testsuites><testsuite name="x">'
             '<testcase classname="pieces.test_calc" name="test_a" file="pieces/test_calc.py"/>'
             '<testcase classname="pieces.test_calc" name="test_b" file="pieces/test_calc.py"><failure message="AssertionError: x">t</failure></testcase>'
             '<testcase classname="pieces.test_calc" name="test_c" file="pieces/test_calc.py"><skipped/></testcase>'
             '</testsuite></testsuites>')
    text = pp.results_section(junit, {"pieces/test_calc.py": {"test_a", "test_b", "test_c", "test_d"}})
    assert "pieces/test_calc.py::test_a: passed" in text
    assert "pieces/test_calc.py::test_b: failed" in text
    assert "pieces/test_calc.py::test_c: skipped" in text
    assert "pieces/test_calc.py::test_d: missing" in text


# ---------------------------------------------------------------- 3. an unchanged job file is marked in FILES

def test_an_unchanged_job_file_is_marked_unchanged_in_the_files_section(tmp_path):
    root = with_original(make_prod_root(tmp_path))
    text, _ = package_of(root, prod_job())
    files = section_text(text, 3, "FILES")
    assert "pieces/calc.py (unchanged)" in files
    assert "ORIGINAL" not in files and "return 0" in files          # the file in full, with its line numbers
    assert "   2 | " in files


# ---------------------------------------------------------------- 4. tight limit: imports and references before callers, omitted named

def _tight_root(base, callers):
    root = with_original(make_prod_root(base))
    (root / "pieces" / "calc.py").write_text("from pieces.helpers import tax" + NL + CLEAN_CODE, encoding="utf-8")
    (root / "pieces" / "helpers.py").write_text("def tax(x):" + NL + "    return x * 0" + NL + "# helper" + NL * 2, encoding="utf-8")
    (root / "docs" / "ref.md").write_text("reference note about the sum" + NL * 2, encoding="utf-8")
    if callers:
        (root / "pieces" / "caller_a.py").write_text("from pieces.calc import calculate_total" + NL + ("# filler line" + NL) * 150, encoding="utf-8")
        (root / "pieces" / "caller_b.py").write_text("from pieces.calc import calculate_total" + NL + ("# filler line" + NL) * 250, encoding="utf-8")
    return root


def test_with_a_tight_limit_imports_and_references_are_kept_before_callers_and_omitted_callers_are_named(tmp_path):
    job = prod_job(reference=["docs/ref.md"])
    base_root = _tight_root(tmp_path / "base", callers=False)
    base_text, _ = package_of(base_root, job, originals={"pieces/calc.py": ORIGINAL_CODE, "pieces/test_calc.py": None})
    root = _tight_root(tmp_path / "full", callers=True)
    text, info = pp.build_package(root, job, {"pieces/calc.py": ORIGINAL_CODE, "pieces/test_calc.py": None}, None,
                                  limit=len(base_text) + 1000)
    assert "pieces/helpers.py" in info["kept"] and "docs/ref.md" in info["kept"]
    assert "pieces/caller_a.py" in info["omitted"] and "pieces/caller_b.py" in info["omitted"]
    omitted = section_text(text, 8, "OMITTED")
    assert "pieces/caller_a.py" in omitted and "pieces/caller_b.py" in omitted
    assert len(text) <= len(base_text) + 1000


# ---------------------------------------------------------------- 5. a mandatory section that does not fit: blocked, no reviewer call, originals back

def test_a_package_over_the_prompt_limit_blocks_the_job_before_any_reviewer_call(tmp_path, monkeypatch):
    monkeypatch.setattr(pp, "PACKAGE_LIMIT", 500)
    provider = ph.Provider({DOTS: [ph.file_reply(("pieces/calc.py", CLEAN_CODE), ("pieces/test_calc.py", GOOD_TEST))]})
    root, result = run_e2e(tmp_path, provider)
    assert result["state"] == "blocked", result
    assert status_of(root)["reason"] == "review package exceeds the prompt limit"
    assert NORTH not in provider.models() and XING not in provider.models()
    assert (root / "pieces" / "calc.py").read_text(encoding="utf-8") == ORIGINAL_CODE
    assert not (root / "pieces" / "test_calc.py").exists()


# ---------------------------------------------------------------- 6. excluded folders and files never appear

def test_files_under_loop_moss_secrets_git_and_env_never_appear_even_when_they_import_a_job_file(tmp_path):
    root = with_original(make_prod_root(tmp_path))
    (root / "pieces" / "caller.py").write_text("from pieces.calc import calculate_total" + NL + "CALLER_MARK = 1" + NL, encoding="utf-8")
    for folder in ("_loop", "_moss", "secrets", ".git"):
        (root / folder).mkdir(parents=True, exist_ok=True)
        (root / folder / "peek.py").write_text("from pieces.calc import calculate_total" + NL + "PEEK_MARK = 1" + NL, encoding="utf-8")
    (root / ".env").write_text("SECRET_TOKEN=abc" + NL, encoding="utf-8")
    text, _ = package_of(root, prod_job())
    assert "PEEK_MARK" not in text and "SECRET_TOKEN" not in text
    assert "_loop" not in section_text(text, 7, "CONTEXT") and "secrets/" not in text and ".git/" not in text
    assert "CALLER_MARK" in text                                    # positive control: an ordinary caller does appear


# ---------------------------------------------------------------- 7. symlinks into _loop or outside the repo never appear

def _link_dir(link, target):
    """A directory link without admin rights: a symlink where the OS allows one, else a Windows junction (mklink /J needs no privilege)."""
    try:
        os.symlink(target, link, target_is_directory=True)
        return True
    except OSError:
        pass
    if os.name == "nt":
        done = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)
        return done.returncode == 0
    return False


def test_a_symlink_into_loop_or_outside_the_repo_never_appears(tmp_path):
    root = with_original(make_prod_root(tmp_path))
    (root / "_loop" / "peek_dir").mkdir()
    (root / "_loop" / "peek_dir" / "secret_peek.py").write_text("from pieces.calc import calculate_total" + NL + "LOOP_MARK = 1" + NL, encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "elsewhere.py").write_text("from pieces.calc import calculate_total" + NL + "OUTSIDE_MARK = 1" + NL, encoding="utf-8")
    linked = _link_dir(root / "pieces" / "link_loop", root / "_loop" / "peek_dir") and _link_dir(root / "pieces" / "link_outside", outside)
    assert linked, "could not create a directory link (symlink or junction) for the test"
    text, _ = package_of(root, prod_job())
    assert "LOOP_MARK" not in text and "OUTSIDE_MARK" not in text
    assert "link_loop" not in text and "link_outside" not in text


# ---------------------------------------------------------------- 12. a planted special-case is caught by a reviewer that reads only the package

def test_a_planted_special_case_is_sent_back_to_dots_as_a_check_2_repair_and_the_repaired_code_passes(tmp_path):
    def glm_reviews(request):
        user = request["messages"][1]["content"]
        if "prices == [20, 30]" in user:
            return "pieces/calc.py:2 special-cases the test value [20, 30]: breaks CHECK 2 and SPEC 1" + NL + "## Verdict: FIX FIRST" + NL
        return ph.READY

    provider = ph.Provider({
        DOTS: [ph.file_reply(("pieces/calc.py", PLANTED), ("pieces/test_calc.py", GOOD_TEST)),      # the write
               ph.file_reply(("pieces/calc.py", CLEAN_CODE), ("pieces/test_calc.py", GOOD_TEST))],   # the FIX FIRST repair
        NORTH: [glm_reviews, glm_reviews],                       # the first reviewer (North): FIX FIRST on the planted code, READY on the repair
        GLM: [ph.READY],
    })
    root, result = run_e2e(tmp_path, provider)
    assert result["state"] == "passed", result
    repair = [request["messages"][1]["content"] for name, request in provider.calls if name == DOTS][1]
    assert "CHECK 2: no code special-cases test values." in repair and "pieces/calc.py:2 |" in repair
    assert provider.models()[-1] == GLM                       # GLM is called only after North says READY


# ---------------------------------------------------------------- 13. a static warning alone never fails a job

def test_a_static_warning_alone_never_fails_a_job(tmp_path):
    provider = ph.Provider({
        DOTS: [ph.file_reply(("pieces/calc.py", WARN_CODE), ("pieces/test_calc.py", GOOD_TEST))],
        GLM: [ph.READY],
        NORTH: [ph.READY],
    })
    root, result = run_e2e(tmp_path, provider)
    assert result["state"] == "passed", result
    warnings = section_text(provider_prompt(provider, NORTH), 6, "STATIC WARNINGS")
    assert "pieces/calc.py:2" in warnings                           # the warning was there and advisory


def provider_prompt(provider, model):
    return [request["messages"][1]["content"] for name, request in provider.calls if name == model][0]


# ---------------------------------------------------------------- 14. a provider failure before the first write: a clean stop

def test_a_provider_failure_before_the_first_write_is_a_clean_stop(tmp_path):
    sandbox = SpecialSandbox()
    root, result = run_e2e(tmp_path, sandbox=sandbox, client=DownClient())
    assert result["state"] == "provider-unavailable", result
    assert sandbox.normal_runs == 0                              # no job test run: the stop came before the first write
    assert (root / "pieces" / "calc.py").read_text(encoding="utf-8") == ORIGINAL_CODE
    assert not (root / "pieces" / "test_calc.py").exists()
    assert (root / "_loop" / "calc" / "status.json").is_file()
    assert (root / "_loop" / "calc" / "ESCALATE.md").is_file()
    assert not (root / "_loop" / "calc" / "candidate").exists()


# ---------------------------------------------------------------- 15. a reviewer rate-limited upstream: the 429 window (patch 2026-11-02)

class Rate429Transport:
    """Wraps the scripted provider: the first `limited` POSTs to the first reviewer (GLM) get an upstream 429; every other request goes to the provider."""

    def __init__(self, provider, limited):
        self.provider, self.left, self.reviewer_posts = provider, limited, 0

    def __call__(self, method, url, headers, body, timeout):
        if method == "POST" and json.loads(body)["model"] == GLM:
            self.reviewer_posts += 1
            if self.left > 0:
                self.left -= 1
                error = {"error": {"code": 429, "message": "Provider returned error", "metadata": {"raw": "rate-limited upstream"}}}
                return 429, {}, json.dumps(error).encode("utf-8")
        return self.provider(method, url, headers, body, timeout)


def window_client(tmp_path, transport):
    """The real adapter with a fake transport and a recorded (instant) sleep."""
    sleeps = []
    built = oc.OpenRouter({"free": ph.FREE_KEY, "paid": ph.PAID_KEY}, tmp_path / "ledger", transport=transport, sleep=sleeps.append,
                          rand=lambda: 0.0)
    return built, sleeps


def test_reviewer_rate_limited_upstream_three_times_then_ready_passes_and_the_status_lists_the_retries(tmp_path):
    provider = ph.Provider({
        DOTS: [ph.file_reply(("pieces/calc.py", CLEAN_CODE), ("pieces/test_calc.py", GOOD_TEST))],
        GLM: [ph.READY],
        NORTH: [ph.READY],
    })
    transport = Rate429Transport(provider, limited=3)
    client, sleeps = window_client(tmp_path, transport)
    root, result = run_e2e(tmp_path, client=client)
    assert result["state"] == "passed", result
    assert sleeps == [15.0, 30.0, 60.0] and transport.reviewer_posts == 4
    status = json.loads((root / "_loop" / "calc" / "status.json").read_text(encoding="utf-8"))
    retries = status["evidence"]["openrouter_retries"]
    assert [r["wait_s"] for r in retries] == [15.0, 30.0, 60.0]
    assert all(r["model"] == GLM for r in retries)


def test_reviewer_rate_limited_on_every_call_is_provider_unavailable_with_the_originals_restored(tmp_path):
    provider = ph.Provider({
        DOTS: [ph.file_reply(("pieces/calc.py", CLEAN_CODE), ("pieces/test_calc.py", GOOD_TEST))],
        GLM: [ph.READY],
        NORTH: [ph.READY],
    })
    transport = Rate429Transport(provider, limited=99)
    client, sleeps = window_client(tmp_path, transport)
    root, result = run_e2e(tmp_path, client=client)
    assert result["state"] == "provider-unavailable", result
    assert (root / "pieces" / "calc.py").read_text(encoding="utf-8") == ORIGINAL_CODE
    assert sleeps == [15.0, 30.0, 60.0, 120.0, 180.0] and transport.reviewer_posts == 6
    status = json.loads((root / "_loop" / "calc" / "status.json").read_text(encoding="utf-8"))
    assert "rate-limited upstream" in status["reason"]
    assert len(status["evidence"]["openrouter_retries"]) == 5


@pytest.fixture(autouse=True)
def _production_profile(profile):
    """Every test in this file runs on the production roster (the paid GLM reviewer lives there). Set per test, never at import: the role
    names below are read from the roster here, after the profile is set."""
    global DOTS, XING, NORTH, GLM
    profile("production")
    roles = oc.roster_state()["ROLES"]
    DOTS, XING, NORTH, GLM = roles["writer_a"], roles["writer_b"], roles["reviewer_b"], roles["reviewer_a"]
