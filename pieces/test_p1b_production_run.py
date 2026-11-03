"""P1b wiring (final roster, 2026-11-02): one production job through fix_loop.run_job, with the real Docker sandbox and fake OpenRouter models
for writer_a (Dots), writer_b (Xing), reviewer_a (GLM, reviews only) and reviewer_b (North). The Opus repair runs only through a fake `claude`
(pieces/fake_claude.py), and only where a test switches it on; by default it is off and no process starts. No network and no paid call.

The real sandbox needs Docker and the sandbox image. Without them the end-to-end tests are skipped with the reason (never a silent pass)."""
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "source"))
import fix_loop as fl  # noqa: E402
import opus_repair as opus  # noqa: E402
import openrouter_call as oc  # noqa: E402
import p1b_production as pm  # noqa: E402
import p1b_routing as p1b  # noqa: E402
import sandbox_runner as sr  # noqa: E402
import prod_harness as ph  # noqa: E402
import p0_gates as gates  # noqa: E402
from test_p0_acceptance import make_prod_root, CONSTANT_CODE, CONSTANT_TEST, GOOD_CODE, GOOD_TEST  # noqa: E402

NL = "\n"
DOTS = XING = NORTH = GLM = None      # set per test by the profile fixture below (the roster is read per test, never at import)
ANTHROPIC = p1b.ANTHROPIC
FAKE_CLAUDE = REPO / "pieces" / "fake_claude.py"
# Xing's wrong code: its own test agrees with it (passes), the locked acceptance tests fail on the same two tests Dots fails.
XING_CODE = "def calculate_total(prices):" + NL + "    return sum(prices[:-1])" + NL
XING_TEST = "from pieces.calc import calculate_total" + NL + "def test_calculate_total():" + NL + "    assert calculate_total([20, 30]) == 20" + NL
BUDGET = {"free_requests": 40, "review_usd": 0.05, "paid_usd": 0}


SANDBOX_PROBLEMS = sr.DockerSandbox(image=sr.DEFAULT_IMAGE, include=["pieces", "tests"]).selftest()
needs_docker = pytest.mark.skipif(bool(SANDBOX_PROBLEMS),
                                  reason="the real sandbox is not available here: " + "; ".join(SANDBOX_PROBLEMS)[:200])


def p1b_job(**over):
    """A production job in the P1b shape: role names for fixers, reviewers by lineage, the owner's budget and deadline."""
    job = {"name": "calc", "profile": "production", "visibility": "public", "spec": "specs/s.md",
           "files": ["pieces/calc.py", "pieces/test_calc.py"], "tests": ["pieces/test_calc.py"], "reference": [],
           "fixers": ["writer_a", "writer_b"], "reviewers": "by_lineage",
           "budget": dict(BUDGET), "deadline_min": 60,
           "review": True,
           "acceptance_tests": ["tests/acceptance/test_calc_acceptance.py"],
           "negative_controls": [
               {"name": "returns None", "replace": {"pieces/calc.py": "tests/controls/calc_returns_none.py"},
                "must_fail": ["tests/acceptance/test_calc_acceptance.py::test_sums_two_prices"]},
               {"name": "returns a constant", "replace": {"pieces/calc.py": "tests/controls/calc_returns_constant.py"},
                "must_fail": ["tests/acceptance/test_calc_acceptance.py::test_sums_three_prices"]}]}
    job.update(over)
    return job


def load(root, job):
    path = root / "_loop" / "jobs" / (job["name"] + ".json")
    path.write_text(json.dumps(job), encoding="utf-8")
    return fl.load_job(path)


def script_red_red_red():
    """Dots writes the constant (its own test agrees) and repairs it the same way; Xing writes its wrong sum and repairs it the same way; the
    Opus repair (fake claude) supplies the correct code; then GLM and North review READY."""
    return {
        DOTS: [ph.file_reply(("pieces/calc.py", CONSTANT_CODE), ("pieces/test_calc.py", CONSTANT_TEST)),
               ph.file_reply(("pieces/calc.py", CONSTANT_CODE), ("pieces/test_calc.py", CONSTANT_TEST))],
        XING: [ph.file_reply(("pieces/calc.py", XING_CODE), ("pieces/test_calc.py", XING_TEST)),
               ph.file_reply(("pieces/calc.py", XING_CODE), ("pieces/test_calc.py", XING_TEST))],
        GLM: [ph.READY],
        NORTH: [ph.READY],
    }


@pytest.fixture
def fake_claude(tmp_path, monkeypatch):
    """Switches the Opus repair on, with the fake claude as the executable; `reply(text)` sets the FILE text it returns."""
    log, output = tmp_path / "fake_claude.log", tmp_path / "fake_claude_out.json"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    monkeypatch.setenv("FAKE_CLAUDE_OUTPUT", str(output))
    monkeypatch.setattr(opus, "CLAUDE_CMD", [sys.executable, str(FAKE_CLAUDE)])
    monkeypatch.setattr(opus, "OPUS_ENABLED", True)

    class Fake:
        def reply(self, text):
            output.write_text(json.dumps({"type": "result", "is_error": False, "result": text}), encoding="utf-8")

        def runs(self):
            return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []

    return Fake()


@needs_docker
def test_a_job_red_red_red_then_opus_repairs_it_to_green_with_glm_and_north_ready(tmp_path, fake_claude):
    root = make_prod_root(tmp_path)
    job = load(root, p1b_job())
    provider = ph.Provider(script_red_red_red())
    fake_claude.reply(ph.file_reply(("pieces/calc.py", GOOD_CODE), ("pieces/test_calc.py", GOOD_TEST)))
    result = fl.run_job(job, root, openrouter=ph.client(tmp_path, provider, budget=oc.JobBudget(40, 0.05)), scrub_keys=[])

    # the free calls: Dots writes and repairs, Xing writes fresh and repairs; then the reviewers: North first, GLM second (only after North says READY)
    assert provider.models() == [DOTS, DOTS, XING, XING, NORTH, GLM]
    # the Dots repair gets the failing tests from JUnit, never the raw pytest output
    dots_repair_user = [request for model, request in provider.calls if model == DOTS][1]["messages"][1]["content"]
    assert "FAILING TESTS" in dots_repair_user and "PYTEST OUTPUT" not in dots_repair_user
    assert len(fake_claude.runs()) == 1                       # the Opus repair is the one fake claude run
    assert result["state"] == "passed", result
    assert result["review_verdict"] == "READY"
    ev = result["evidence"]
    assert ev["tested_hash"] == ev["final_hash"] == ev["reviewed_hash"]
    assert (root / "pieces" / "calc.py").read_text(encoding="utf-8") == GOOD_CODE
    assert ev["budget"]["repair_calls"] == 1 and ev["budget"]["free_requests_used"] == 5   # Dots 2, Xing 2, North 1 (GLM is paid, not free)
    assert ev["budget"]["review_usd_spent"] <= 0.05

    lineage = json.loads((root / "_loop" / "calc" / "lineage.json").read_text(encoding="utf-8"))
    assert lineage["pieces/calc.py"]["origin"] == XING and lineage["pieces/calc.py"]["last"] == ANTHROPIC
    assert lineage["pieces/calc.py"]["chain"][-1] == "anthropic"


@needs_docker
def test_the_review_call_to_glm_is_pinned_to_z_ai_with_no_fallbacks(tmp_path):
    root = make_prod_root(tmp_path)
    job = load(root, p1b_job())
    provider = ph.Provider({DOTS: [ph.file_reply(("pieces/calc.py", GOOD_CODE), ("pieces/test_calc.py", GOOD_TEST))],
                            GLM: [ph.READY], NORTH: [ph.READY]})
    fl.run_job(job, root, openrouter=ph.client(tmp_path, provider), scrub_keys=[])
    glm_bodies = [request for model, request in provider.calls if model == GLM]
    assert glm_bodies, "GLM was asked to review"
    for request in glm_bodies:
        assert request["provider"] == {"order": ["z-ai"], "only": ["z-ai"], "allow_fallbacks": False}


@needs_docker
def test_with_opus_off_a_red_job_escalates_with_the_fixed_reason_and_no_non_docker_process_starts(tmp_path, monkeypatch):
    root = make_prod_root(tmp_path)
    job = load(root, p1b_job())
    started = []
    real_run = subprocess.run

    def watch(args, *rest, **kwargs):
        first = args[0] if isinstance(args, (list, tuple)) and args else str(args)
        if not Path(str(first)).name.lower().startswith("docker"):
            started.append(first)
        return real_run(args, *rest, **kwargs)

    monkeypatch.setattr(subprocess, "run", watch)
    provider = ph.Provider(script_red_red_red())
    result = fl.run_job(job, root, openrouter=ph.client(tmp_path, provider), scrub_keys=[])
    assert result["state"] == "escalate", result
    assert json.loads((root / "_loop" / "calc" / "status.json").read_text(encoding="utf-8"))["reason"] == "Opus repair unavailable"
    assert started == [], "a non-docker process started: Opus is off by default"
    assert provider.models() == [DOTS, DOTS, XING, XING]
    assert not any(model == GLM for model, _ in provider.calls)


@needs_docker
def test_a_production_job_can_never_reach_a_legacy_model(tmp_path, monkeypatch):
    """Nemotron, Codex, Haiku, Groq and Sonnet are the legacy path. A production run must not call any of them: the legacy factories are
    replaced by functions that fail, and every subprocess other than docker fails."""
    root = make_prod_root(tmp_path)
    job = load(root, p1b_job())
    legacy = ["_default_caller_factory", "_haiku_cli_caller_factory", "_default_reviewers_factory", "_default_codex_runner", "_groq_runner",
              "_haiku_runner", "_ping_cli", "_default_judge_factory"]

    def refuse(*args, **kwargs):
        raise AssertionError("a legacy model path was reached from a production job")

    for name in legacy:
        if hasattr(fl, name):
            monkeypatch.setattr(fl, name, refuse)
    real_popen = subprocess.Popen                   # subprocess.run starts processes through Popen: the guard sits there

    def only_docker(args, *rest, **kwargs):
        first = args[0] if isinstance(args, (list, tuple)) and args else str(args)
        if Path(str(first)).name.lower().startswith("docker"):
            return real_popen(args, *rest, **kwargs)
        raise AssertionError(f"a production job started a non-docker process: {first}")

    monkeypatch.setattr(subprocess, "Popen", only_docker)
    # Dots writes correct code, so the reviewers are North, then GLM
    provider = ph.Provider({DOTS: [ph.file_reply(("pieces/calc.py", GOOD_CODE), ("pieces/test_calc.py", GOOD_TEST))],
                            GLM: [ph.READY], NORTH: [ph.READY]})
    result = fl.run_job(job, root, openrouter=ph.client(tmp_path, provider), scrub_keys=[])
    assert result["state"] == "passed", result
    assert provider.models() == [DOTS, NORTH, GLM]
    assert set(provider.models()) <= {DOTS, XING, NORTH, GLM}


def test_a_production_run_refuses_legacy_callers_and_legacy_fixers(tmp_path):
    root = make_prod_root(tmp_path)
    job = load(root, p1b_job())
    with pytest.raises(ValueError, match="legacy"):
        fl.run_job(job, root, caller=lambda s, u: ("", {}), scrub_keys=[])
    with pytest.raises(ValueError):
        fl.load_job(_write(root, p1b_job(name="calc_bad", fixers=["nemotron"])))


def test_a_production_job_with_any_other_budget_is_refused_at_load(tmp_path):
    root = make_prod_root(tmp_path)
    with pytest.raises(ValueError):
        fl.load_job(_write(root, p1b_job(name="calc_budget", budget={"free_requests": 40, "review_usd": 0.15, "paid_usd": 0})))


def _write(root, job):
    path = root / "_loop" / "jobs" / (job["name"] + ".json")
    path.write_text(json.dumps(job), encoding="utf-8")
    return path


def test_the_production_runner_names_no_legacy_model_family():
    """Static: the P1b runner, routing and adapter modules contain no legacy model family name (Nemotron, Codex, Haiku, Groq, Sonnet)."""
    for name in ("p1b_production.py", "p1b_routing.py", "openrouter_call.py"):
        text = (REPO / "scripts" / "source" / name).read_text(encoding="utf-8").lower()
        for word in ("nemotron", "codex", "haiku", "groq", "sonnet"):
            assert word not in text, f"{name} names {word}"


def test_a_production_job_gets_no_legacy_review_prompt(tmp_path):
    """docs/REVIEW_PROMPT.md is the legacy HIGH/MED/LOW prompt; a production job must not inherit it as its review prompt."""
    job = p1b_job()
    job.pop("review_prompt", None)
    (tmp_path / "_loop" / "jobs").mkdir(parents=True)
    assert load(tmp_path, job)["review_prompt"] is None


@needs_docker
def test_a_named_review_prompt_never_reaches_a_production_reviewer(tmp_path):
    """A production job that names the legacy docs/REVIEW_PROMPT.md is ignored: its text reaches no review request."""
    root = make_prod_root(tmp_path)
    (root / "docs").mkdir(exist_ok=True)
    (root / "docs" / "REVIEW_PROMPT.md").write_text("LEGACY-MARKER-PROMPT do not send", encoding="utf-8")
    job = load(root, p1b_job(review_prompt="docs/REVIEW_PROMPT.md"))
    provider = ph.Provider({DOTS: [ph.file_reply(("pieces/calc.py", GOOD_CODE), ("pieces/test_calc.py", GOOD_TEST))],
                            GLM: [ph.READY], NORTH: [ph.READY]})
    fl.run_job(job, root, openrouter=ph.client(tmp_path, provider), scrub_keys=[])
    assert provider.calls, "the reviewers were called"
    assert not any("LEGACY-MARKER-PROMPT" in json.dumps(request) for _, request in provider.calls)


LEAK = "LEAKMARKER42XYZ"       # a key the adapter itself does not know: only the runner's scrub can hide it


class ReviewersRateLimited(ph.Provider):
    """North and Xing (the backup) answer HTTP 429 with a key echoed in the error text: the status must not carry it."""

    def __call__(self, method, url, headers, body, timeout):
        if method == "POST" and url.endswith("/chat/completions") and json.loads(body)["model"] in (NORTH, XING):
            self.http.append((method, url, headers))
            self.calls.append((json.loads(body)["model"], json.loads(body)))
            return 429, {}, json.dumps({"error": {"message": "upstream shared pool, echoed key " + LEAK}}).encode()
        return super().__call__(method, url, headers, body, timeout)


@needs_docker
def test_a_key_in_a_reviewer_error_is_redacted_in_status_json_and_in_the_result(tmp_path):
    root = make_prod_root(tmp_path)
    job = load(root, p1b_job())
    provider = ReviewersRateLimited({DOTS: [ph.file_reply(("pieces/calc.py", GOOD_CODE), ("pieces/test_calc.py", GOOD_TEST))],
                                     GLM: [ph.READY]})
    result = fl.run_job(job, root, openrouter=ph.client(tmp_path, provider), scrub_keys=[LEAK])
    assert result["state"] == "provider-unavailable", result
    status_text = (root / "_loop" / "calc" / "status.json").read_text(encoding="utf-8")
    assert "shared pool" in status_text
    assert LEAK not in status_text and "[REDACTED]" in status_text
    assert LEAK not in json.dumps(result)


@pytest.fixture(autouse=True)
def _production_profile(profile):
    """Every test in this file runs on the production roster (the paid GLM reviewer lives there). Set per test, never at import: the role
    names below are read from the roster here, after the profile is set."""
    global DOTS, XING, NORTH, GLM
    profile("production")
    roles = oc.roster_state()["ROLES"]
    DOTS, XING, NORTH, GLM = roles["writer_a"], roles["writer_b"], roles["reviewer_b"], roles["reviewer_a"]


# ---- WP-H (2026-11-02): needs_review, quota-exhausted and CONTRACT_CHECK.md, with fakes only (no Docker, no network, no model call)

ORIG_CODE = "def calculate_total(prices):" + NL + "    return 0" + NL
ORIG_TEST = "def test_placeholder():" + NL + "    pass" + NL
ACC_ID = "tests/acceptance/test_calc_acceptance.py::test_sums_two_prices"
SUM_CODE = "def calculate_total(prices):" + NL + "    return sum(prices[:-1])" + NL     # Xing's wrong code in these tests
DOTS_CODE = "def calculate_total(prices):" + NL + "    return 50" + NL                  # Dots' wrong code in these tests


class FakeClient:
    """Stands in for the OpenRouter client: `queue` holds reply texts in call order, and an exception item is raised instead."""

    def __init__(self, queue):
        self.queue, self.calls, self.retry_log = list(queue), [], []
        self.budget = oc.JobBudget(40, 0.05)
        self.failed_attempts = []
        self.requests = []              # every request the adapter sent: a fixed success (100 prompt, 40 completion, 8 reasoning tokens, 1.5 s)

    def begin_job(self):
        pass

    def set_job_deadline(self, seconds):
        pass

    def call(self, role, system, user, kind, effort=None):
        self.calls.append(oc.ROLES[role])
        item = self.queue.pop(0)
        if isinstance(item, tuple):             # (attempts, error): the adapter's failed attempts for this call, then its error
            attempts, error = item
            self.failed_attempts.extend(attempts)
            self.requests.extend(dict(a, failed=True, seconds=0.0) for a in attempts)
            raise error
        if isinstance(item, BaseException):
            raise item
        self.requests.append({"model": oc.ROLES[role], "status": 200, "failed": False, "seconds": 1.5, "finish_reason": "stop",
                              "usage": {"prompt_tokens": 100, "completion_tokens": 40}, "reasoning_tokens": 8})
        return item, {}


class FakeSandbox:
    """Runs no pytest: every expected test passes except `failing` (test id -> assertion message); with an overlay (a negative control) every
    expected test fails, as a control must. Returns the same fields the real sandbox returns."""

    def __init__(self, failing):
        self.failing, self.runs = dict(failing), 0

    def selftest(self):
        return []

    def run(self, root, targets, readonly=(), timeout_s=600, overlay=None):
        self.runs += 1
        cases, failed = [], False
        for rel, ids in sorted(gates.expected_test_ids(root, targets).items()):
            for tid in sorted(ids or ()):
                module = rel[:-3].replace("/", ".")
                fid = f"{rel}::{tid}"
                if overlay is not None or fid in self.failing:
                    failed = True
                    msg = self.failing.get(fid, "assert False")
                    cases.append(f'<testcase classname="{module}" name="{tid}" file="{rel}"><failure message="{msg}">E   AssertionError: {msg}</failure></testcase>')
                else:
                    cases.append(f'<testcase classname="{module}" name="{tid}" file="{rel}"/>')
        xml = "<testsuites><testsuite>" + "".join(cases) + "</testsuite></testsuites>"
        return SimpleNamespace(output="pytest output (fake)", junit=xml, returncode=1 if failed else 0, timed_out=False)


def wp_h_root(tmp_path):
    root = make_prod_root(tmp_path)
    (root / "pieces" / "calc.py").write_text(ORIG_CODE, encoding="utf-8")
    (root / "pieces" / "test_calc.py").write_text(ORIG_TEST, encoding="utf-8")
    return root


def wp_h_run(root, client, sandbox):
    job = load(root, p1b_job())
    return fl.run_job(job, root, sandbox=sandbox, openrouter=client, scrub_keys=[])


def wp_h_status(root):
    return json.loads((root / "_loop" / "calc" / "status.json").read_text(encoding="utf-8"))


def test_the_same_failing_set_three_writer_runs_in_a_row_ends_needs_review_after_opus_has_had_its_two_repairs(tmp_path, fake_claude):
    """A streak of three identical writer runs does not stop the ladder while a repair step is left: Opus runs (twice), and only then the job
    ends needs_review with the failing ids in status.json and the originals back."""
    root = wp_h_root(tmp_path)
    fake_claude.reply(ph.file_reply(("pieces/calc.py", SUM_CODE), ("pieces/test_calc.py", ORIG_TEST)))    # Opus returns a red candidate, twice
    reply = ph.file_reply(("pieces/calc.py", SUM_CODE), ("pieces/test_calc.py", ORIG_TEST))
    client = FakeClient([reply, reply, reply, reply])                                                  # Dots write, Dots repair, Xing write, Xing repair
    result = wp_h_run(root, client, FakeSandbox({ACC_ID: "assert 0 == 50"}))
    assert result["state"] == "needs_review", result
    status = wp_h_status(root)
    assert status["state"] == "needs_review" and status["failing_tests"] == [ACC_ID]
    assert status["schema_version"] == 1
    assert len(fake_claude.runs()) == 2
    assert client.calls == [DOTS, DOTS, XING, XING]
    assert (root / "pieces" / "calc.py").read_text(encoding="utf-8") == ORIG_CODE
    assert (root / "pieces" / "test_calc.py").read_text(encoding="utf-8") == ORIG_TEST


def test_a_quota_exhausted_adapter_error_ends_the_job_with_the_reason_and_makes_no_further_call(tmp_path):
    root = wp_h_root(tmp_path)
    client = FakeClient([ph.file_reply(("pieces/calc.py", SUM_CODE), ("pieces/test_calc.py", ORIG_TEST)),
                         oc.QuotaExhausted("free requests for today are used up")])
    result = wp_h_run(root, client, FakeSandbox({ACC_ID: "assert 0 == 50"}))
    assert result["state"] == "quota-exhausted", result
    status = wp_h_status(root)
    assert status["state"] == "quota-exhausted" and "used up" in status["reason"]
    assert client.calls == [DOTS, DOTS] and client.queue == []      # the repair call raised; nothing was called after it
    assert (root / "pieces" / "calc.py").read_text(encoding="utf-8") == ORIG_CODE


def test_dots_and_xing_failing_one_locked_acceptance_test_with_different_code_write_contract_check_and_the_ladder_goes_on(tmp_path):
    """The contract file is written when Dots and Xing fail the same locked assertion with different code. The ladder does not stop: here
    Opus is off, so the job ends escalate with the existing reason, and status.json points at the contract file."""
    root = wp_h_root(tmp_path)
    client = FakeClient([ph.file_reply(("pieces/calc.py", DOTS_CODE), ("pieces/test_calc.py", ORIG_TEST))] * 2
                        + [ph.file_reply(("pieces/calc.py", SUM_CODE), ("pieces/test_calc.py", ORIG_TEST))] * 2)
    result = wp_h_run(root, client, FakeSandbox({ACC_ID: "assert 0 == 50"}))
    assert result["state"] == "escalate", result
    status = wp_h_status(root)
    assert status["reason"] == "Opus repair unavailable"
    assert status["contract_check"].endswith("CONTRACT_CHECK.md") and status["schema_version"] == 1
    text = (root / "_loop" / "calc" / "CONTRACT_CHECK.md").read_text(encoding="utf-8")
    assert ACC_ID in text and "assert 0 == 50" in text
    assert "SPEC 1: calculate_total(prices) returns the sum of the prices." in text
    assert "likely SPEC line (guessed by word overlap)" in text
    assert client.calls == [DOTS, DOTS, XING, XING]
    assert (root / "pieces" / "calc.py").read_text(encoding="utf-8") == ORIG_CODE

# ---- failed calls keep their evidence (2026-11-03): each failed call is a file in history/ and a "failed" step in status.json

def test_a_failed_writer_call_and_a_failed_repair_are_history_files_and_status_steps(tmp_path):
    root = wp_h_root(tmp_path)
    secret = "sk-or-v1-SECRETKEYABC123"
    attempt = {"model": DOTS, "status": 503, "message": f"upstream down {secret} " + "x" * 2500}
    client = FakeClient([([attempt], oc.ProviderUnavailable("HTTP 503")), ph.file_reply(("pieces/calc.py", SUM_CODE), ("pieces/test_calc.py", ORIG_TEST)),
                         ph.file_reply(("pieces/calc.py", SUM_CODE), ("pieces/test_calc.py", ORIG_TEST))])
    wp_h_run(root, client, FakeSandbox({ACC_ID: "assert 0 == 50"}))
    history = root / "_loop" / "calc" / "history"
    first = json.loads((history / "failed_call_01.json").read_text(encoding="utf-8"))
    assert first["model"] == DOTS and first["status"] == 503 and first["call"] == "write"
    assert len(first["message"]) == 2000 and secret not in first["message"] and "[REDACTED]" in first["message"]
    steps = wp_h_status(root)["steps"]
    failed = [s for s in steps if s["mode"] == "failed"]
    assert failed[0]["model"] == DOTS and failed[0]["status"] == 503 and failed[0]["message"] == first["message"]
    assert failed[0]["history"] == "history/failed_call_01.json"
    assert sum(1 for s in steps if s["mode"] in ("write", "repair")) == 2     # the failed attempts do not count as coder calls


def test_a_failed_opus_repair_is_a_failed_call_with_no_status(tmp_path):
    root = wp_h_root(tmp_path)
    job = load(root, p1b_job())
    history = root / "_loop" / "calc" / "history"
    history.mkdir(parents=True)

    def refused(prompt, raw_sink=None):
        raise opus.RepairFailed("the call did not finish in 600 s with key sk-ant-SECRETOPUS1234")

    models = pm.Production(job, root, FakeClient([]), FakeSandbox({}), protected=[], acceptance_lock=None, originals={},
                           parse_files=lambda text: ({}, []), check_path=lambda rel: None, content_problem=lambda rel, content: None,
                           history=history, job_dir=root / "_loop" / "calc", repair_ask=refused)
    with pytest.raises(p1b.ModelFailed):
        models.call(ANTHROPIC, "repair", ["pieces/calc.py"])
    record = json.loads((history / "failed_call_01.json").read_text(encoding="utf-8"))
    assert record["model"] == ANTHROPIC and record["call"] == "repair" and record["status"] is None
    assert "did not finish" in record["message"] and "SECRETOPUS1234" not in record["message"]


def test_a_key_and_a_bearer_token_in_a_failed_reviewer_message_never_reach_status_json(tmp_path):
    root = wp_h_root(tmp_path)
    job = load(root, p1b_job())
    attempts = [{"model": DOTS, "status": 429, "message": "pool echoed " + LEAK + " and Bearer tok.en.123"}]
    client = FakeClient([([attempts[0]], oc.ProviderUnavailable("HTTP 429")), ph.file_reply(("pieces/calc.py", SUM_CODE), ("pieces/test_calc.py", ORIG_TEST))])
    fl.run_job(job, root, sandbox=FakeSandbox({ACC_ID: "assert 0 == 50"}), openrouter=client, scrub_keys=[LEAK])
    text = (root / "_loop" / "calc" / "status.json").read_text(encoding="utf-8")
    assert LEAK not in text and "tok.en.123" not in text
    assert "pool echoed" in text and "[REDACTED]" in text


def test_a_writer_reply_the_job_cannot_use_is_a_failed_call_with_status_200(tmp_path):
    root = wp_h_root(tmp_path)
    job = load(root, p1b_job())
    history = root / "_loop" / "calc" / "history"
    history.mkdir(parents=True)
    client = FakeClient(["a reply with a refused block"])
    models = pm.Production(job, root, client, FakeSandbox({}), protected=[], acceptance_lock=None, originals={},
                           parse_files=lambda text: ({}, [("pieces/calc.py", "refused")]), check_path=lambda rel: None,
                           content_problem=lambda rel, content: None, history=history, job_dir=root / "_loop" / "calc")
    with pytest.raises(p1b.BadReply):
        models.call(DOTS, "write", ["pieces/calc.py"])
    record = json.loads((history / "failed_call_01.json").read_text(encoding="utf-8"))
    assert record == {"model": DOTS, "call": "write", "status": 200, "message": record["message"]}
    assert "refused block" in record["message"]
    assert models.steps[-1]["mode"] == "failed" and models.steps[-1]["status"] == 200


def test_a_review_with_fix_first_and_no_valid_evidence_is_a_failed_call_too(tmp_path):
    root = wp_h_root(tmp_path)
    job = load(root, p1b_job())
    history = root / "_loop" / "calc" / "history"
    history.mkdir(parents=True)
    bad = "- pieces/calc.py:999 [SPEC 9]: no such line\n## Verdict: FIX FIRST"
    client = FakeClient([bad])
    models = pm.Production(job, root, client, FakeSandbox({}), protected=[], acceptance_lock=gates.hash_paths(root, []), originals={},
                           parse_files=lambda text: ({}, []), check_path=lambda rel: None, content_problem=lambda rel, content: None,
                           history=history, job_dir=root / "_loop" / "calc")
    assert models.call(NORTH, "review", ["pieces/calc.py"]) == bad
    record = json.loads((history / "failed_call_01.json").read_text(encoding="utf-8"))
    assert record["model"] == NORTH and record["call"] == "review" and record["status"] == 200
    assert "FIX FIRST without valid evidence" in record["message"]


FILLER_MARK = "FILLER_LINE_FOR_SIZE_TEST"
FILLER = ("# " + FILLER_MARK + NL) * 300             # a large passing test file that a repair does not need


class PromptClient(FakeClient):
    """FakeClient that also keeps the user text of every call, in order."""

    def __init__(self, queue):
        super().__init__(queue)
        self.users = []

    def call(self, role, system, user, kind, effort=None):
        self.users.append(user)
        return super().call(role, system, user, kind)


def test_a_repair_prompt_is_smaller_than_the_fresh_write_prompt_for_the_same_job(tmp_path):
    root = wp_h_root(tmp_path)
    (root / "pieces" / "test_extra.py").write_text(FILLER, encoding="utf-8")
    job = load(root, p1b_job(files=["pieces/calc.py", "pieces/test_calc.py", "pieces/test_extra.py"],
                             tests=["pieces/test_calc.py", "pieces/test_extra.py"]))
    history = root / "_loop" / "calc" / "history"
    history.mkdir(parents=True)
    client = PromptClient(["fresh reply", "repair reply", "findings reply"])
    models = pm.Production(job, root, client, FakeSandbox({}), protected=[], acceptance_lock=None, originals={},
                           parse_files=lambda text: ({}, []), check_path=lambda rel: None, content_problem=lambda rel, content: None,
                           history=history, job_dir=root / "_loop" / "calc")
    failing = ('<testsuites><testsuite>'
               '<testcase classname="pieces.test_calc" name="test_sums" file="pieces/test_calc.py">'
               '<failure message="AssertionError: assert 0 == 50">x</failure></testcase>'
               '<testcase classname="pieces.test_extra" name="test_pass" file="pieces/test_extra.py"/>'
               '</testsuite></testsuites>')

    models.call(DOTS, "write", job["files"])                  # the fresh write: every job file
    models.last_junit = failing                               # the last run: test_calc.py failed, test_extra.py passed
    models.call(DOTS, "repair", job["files"])                 # a red-test repair: the failing test file, the code, and the digest only
    models.stage_fix(["pieces/calc.py:1 | SPEC 1: Adds two prices. | returns 0"])
    models.call(DOTS, "repair", job["files"])                 # a FIX FIRST repair: the file the finding names only
    fresh, repair, findings = client.users

    assert len(repair) < len(fresh)
    assert FILLER_MARK in fresh and FILLER_MARK not in repair
    assert "pieces/test_calc.py:" in repair and "FAILING TESTS" in repair and "assert 0 == 50" in repair
    assert "pieces/test_extra.py:" not in repair
    assert len(findings) < len(fresh) and FILLER_MARK not in findings and "pieces/test_calc.py:" not in findings
    assert "pieces/calc.py:" in findings and "REVIEW FINDINGS" in findings


# ---- every model call keeps a history/call_NN.json (2026-11-03, Track G2): model, mode, tokens, finish_reason, seconds; a failed one too

def test_every_model_call_writes_call_NN_failed_or_not(tmp_path):
    root = wp_h_root(tmp_path)
    attempt = {"model": DOTS, "status": 503, "message": "upstream down"}
    client = FakeClient([([attempt], oc.ProviderUnavailable("HTTP 503")), ph.file_reply(("pieces/calc.py", SUM_CODE), ("pieces/test_calc.py", ORIG_TEST)),
                         ph.file_reply(("pieces/calc.py", SUM_CODE), ("pieces/test_calc.py", ORIG_TEST))])
    wp_h_run(root, client, FakeSandbox({ACC_ID: "assert 0 == 50"}))
    history = root / "_loop" / "calc" / "history"
    first = json.loads((history / "call_01.json").read_text(encoding="utf-8"))
    assert first == {"model": DOTS, "mode": "write", "status": 503, "failed": True, "prompt_tokens": None, "completion_tokens": None,
                     "reasoning_tokens": None, "finish_reason": None, "seconds": 0.0, "reply_file": None, "reasoning_file": None}
    second = json.loads((history / "call_02.json").read_text(encoding="utf-8"))
    assert second["model"] == XING and second["mode"] == "write" and second["status"] == 200 and second["failed"] is False
    assert second["prompt_tokens"] == 100 and second["completion_tokens"] == 40 and second["reasoning_tokens"] == 8
    assert second["finish_reason"] == "stop" and second["seconds"] == 1.5


def test_a_failed_call_with_no_message_text_keeps_finish_reason_keys_usage_and_reasoning_lengths(tmp_path):
    root = wp_h_root(tmp_path)
    facts = {"finish_reason": "stop", "usage": {"prompt_tokens": 9, "completion_tokens": 4}, "message_keys": ["reasoning", "role"],
             "reasoning_len": 9, "reasoning_content_len": None}
    attempt = {"model": DOTS, "status": 200, "message": "malformed reply: no message text", "facts": facts}
    client = FakeClient([([attempt], oc.ProviderUnavailable("malformed reply: no message text")),
                         ph.file_reply(("pieces/calc.py", SUM_CODE), ("pieces/test_calc.py", ORIG_TEST)),
                         ph.file_reply(("pieces/calc.py", SUM_CODE), ("pieces/test_calc.py", ORIG_TEST))])
    wp_h_run(root, client, FakeSandbox({ACC_ID: "assert 0 == 50"}))
    record = json.loads((root / "_loop" / "calc" / "history" / "failed_call_01.json").read_text(encoding="utf-8"))
    assert record["finish_reason"] == "stop" and record["usage"] == {"prompt_tokens": 9, "completion_tokens": 4}
    assert record["message_keys"] == ["reasoning", "role"] and record["reasoning_len"] == 9 and record["reasoning_content_len"] is None


def test_a_repair_call_writes_a_call_record_with_no_usage(tmp_path):
    root = wp_h_root(tmp_path)
    job = load(root, p1b_job())
    history = root / "_loop" / "calc" / "history"
    history.mkdir(parents=True)

    def refused(prompt, raw_sink=None):
        raise opus.RepairFailed("the call did not finish in 600 s")

    models = pm.Production(job, root, FakeClient([]), FakeSandbox({}), protected=[], acceptance_lock=None, originals={},
                           parse_files=lambda text: ({}, []), check_path=lambda rel: None, content_problem=lambda rel, content: None,
                           history=history, job_dir=root / "_loop" / "calc", repair_ask=refused)
    with pytest.raises(p1b.ModelFailed):
        models.call(ANTHROPIC, "repair", ["pieces/calc.py"])
    record = json.loads((history / "call_01.json").read_text(encoding="utf-8"))
    assert record["model"] == ANTHROPIC and record["mode"] == "repair" and record["failed"] is True and record["status"] is None
    assert record["prompt_tokens"] is None and record["finish_reason"] is None and record["seconds"] >= 0


def test_a_running_job_rewrites_its_status_json_with_progress_and_no_secret(tmp_path):
    """R1: status.json is rewritten after every step, with state running, the counts so far, and the scrub applied."""
    job_dir = tmp_path / "calc"
    job_dir.mkdir()
    fake = SimpleNamespace(job={"name": "calc"}, job_dir=job_dir, scrub_keys=("sk-secret-key-123",), steps=[])
    fake.steps.append({"model": "dots", "mode": "write", "note": "3 file(s) returned"})
    fake.steps.append({"model": "north", "mode": "review", "note": "sk-secret-key-123 in a note"})
    pm.Production._heartbeat(fake)
    text = (job_dir / "status.json").read_text(encoding="utf-8")
    status = json.loads(text)
    assert status["state"] == "running" and status["coder_calls"] == 1 and status["reviewer_calls"] == {"north": 1}
    assert "sk-secret-key-123" not in text

