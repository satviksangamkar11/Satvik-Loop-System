"""Pre-pilot bug review (2026-11-02). Each test was written to FAIL on the code as it stood, then the fix made it pass.
No Docker and no network: FakeSandbox decides the tests (every test passes only when pieces/calc.py is GOOD_CODE) and the scripted OpenRouter
transport (prod_harness) answers every model call. Covered: a fresh writer never sees the previous attempt's pytest output; every non-PASS state
puts the originals back and keeps the authorized candidate; a worker killed after one candidate write is recovered on the next launch; the owner's budget is exact;
with the repair step off no GLM request is made; one key serves both roles and an unlisted paid model is refused."""
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
import opus_repair  # noqa: E402
import p1b_production as pp  # noqa: E402
import prod_harness as ph  # noqa: E402
import sandbox_runner as sr  # noqa: E402
from test_p0_acceptance import make_prod_root, prod_job, CONSTANT_CODE, CONSTANT_TEST, GOOD_CODE, GOOD_TEST  # noqa: E402

NL = "\n"
DOTS, XING, NORTH, GLM = oc.ROLES["writer_a"], oc.ROLES["writer_b"], oc.ROLES["reviewer_b"], oc.ROLES["reviewer_a"]
ORIGINAL_CODE = "def calculate_total(prices):" + NL + "    return 0" + NL       # the job file before the run: the originals
XING_CODE = "def calculate_total(prices):" + NL + "    return sum(prices[:-1])" + NL
XING_TEST = "from pieces.calc import calculate_total" + NL + "def test_calculate_total():" + NL + "    assert calculate_total([20, 30]) == 20" + NL
FIX_FIRST = "pieces/calc.py:2 does not do what SPEC 1 asks." + NL + "## Verdict: FIX FIRST" + NL
ONE_KEY = "sk-or-v1-ONEKEYTEST0000000000"


class FakeSandbox:
    """Stands in for the Docker sandbox. Every collected test passes when pieces/calc.py (or the overlay that replaces it) is GOOD_CODE and fails
    with an assertion otherwise. The failure output ends with the last line of the code it ran, so a test can tell whose output reached a writer."""

    def __init__(self):
        self.runs = 0

    def selftest(self):
        return []

    def run(self, root, tests, overlay=None, readonly=None, timeout_s=600):
        self.runs += 1
        root = Path(root)
        source = overlay["pieces/calc.py"] if overlay and "pieces/calc.py" in overlay else root / "pieces" / "calc.py"
        code = Path(source).read_text(encoding="utf-8") if Path(source).is_file() else ""
        good = code == GOOD_CODE
        files = sorted({t.split("::")[0] for t in tests})
        expected = gates.expected_test_ids(root, files)
        cases = []
        for rel in files:
            module = rel[:-3].replace("/", ".")
            for tid in sorted(expected[rel] or []):
                if good:
                    cases.append(f'<testcase classname="{module}" name="{tid}" file="{rel}"/>')
                else:
                    cases.append(f'<testcase classname="{module}" name="{tid}" file="{rel}">'
                                 '<failure message="assert calculate_total([1, 2]) == 3">trace</failure></testcase>')
        last = code.strip().splitlines()[-1] if code.strip() else "(empty)"
        output = "" if good else "TRACE-OF " + last
        xml = '<testsuites><testsuite name="fake">' + "".join(cases) + "</testsuite></testsuites>"
        return sr.RunResult(returncode=0 if good else 1, output=output, junit=xml, timed_out=False)


def with_original(root):
    (root / "pieces" / "calc.py").write_text(ORIGINAL_CODE, encoding="utf-8")
    return root


def write_job(root, job):
    path = root / "_loop" / "jobs" / (job["name"] + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(job), encoding="utf-8")
    return path


def load(root, job):
    return fl.load_job(write_job(root, job))


def status_of(root):
    return json.loads((root / "_loop" / "calc" / "status.json").read_text(encoding="utf-8"))


def history_text(root):
    folder = root / "_loop" / "calc" / "history"
    return "".join(p.read_text(encoding="utf-8") for p in sorted(folder.glob("*.txt"))) if folder.is_dir() else ""


def prompt_of(provider, model, index=0):
    """The user text of the index-th call to `model`."""
    return [request["messages"][1]["content"] for name, request in provider.calls if name == model][index]


def good_reply():
    return ph.file_reply(("pieces/calc.py", GOOD_CODE), ("pieces/test_calc.py", GOOD_TEST))


def constant_reply():
    return ph.file_reply(("pieces/calc.py", CONSTANT_CODE), ("pieces/test_calc.py", CONSTANT_TEST))


def xing_reply():
    return ph.file_reply(("pieces/calc.py", XING_CODE), ("pieces/test_calc.py", XING_TEST))


@pytest.fixture
def opus(monkeypatch):
    """The repair step, scripted: each repair call pops the next reply text from this list. opus_repair.ask is replaced, so no process starts."""
    replies = []

    def scripted_ask(prompt, *, raw_sink=None, **_unused):
        if not replies:
            raise opus_repair.RepairFailed("no scripted repair reply left")
        text = replies.pop(0)
        if raw_sink is not None:
            raw_sink(json.dumps({"result": text}))
        return text

    monkeypatch.setattr(opus_repair, "ask", scripted_ask)
    return replies


# ---------------------------------------------------------------- 1. a fresh writer never sees the previous attempt's pytest output

def test_a_fresh_xing_write_never_receives_the_pytest_output_of_dots(tmp_path, opus):
    opus.append(good_reply())                                  # the repair step (scripted Opus) turns Xing's second red into green
    root = with_original(make_prod_root(tmp_path))
    job = load(root, prod_job())
    provider = ph.Provider({
        DOTS: [constant_reply(), ph.READY],                    # Dots writes the constant: red on every test
        XING: [xing_reply(), xing_reply()],                    # Xing fresh (red), Xing repair (red)
        GLM: [ph.READY],                                       # the review panel: GLM, then North
        NORTH: [ph.READY],
    })
    result = fl.run_job(job, root, sandbox=FakeSandbox(), openrouter=ph.client(tmp_path, provider), scrub_keys=[])
    assert result["state"] == "passed", result

    fresh = prompt_of(provider, XING, 0)
    assert ORIGINAL_CODE in fresh and "SPEC:" in fresh          # the original files and the spec, and nothing else from the run
    assert "PYTEST OUTPUT" not in fresh
    assert "TRACE-OF" not in fresh and "return 100" not in fresh  # no line of Dots' failure reached Xing

    repair = prompt_of(provider, XING, 1)                       # Xing's own repair gets its own failing tests from JUnit, never Dots' output
    assert "FAILING TESTS" in repair and "PYTEST OUTPUT" not in repair
    assert "assert calculate_total([1, 2]) == 3" in repair       # Xing's own failing assertion
    assert "return 100" not in repair

    assert "TRACE-OF     return 100" in history_text(root)     # the evidence is kept in the job history, not in a prompt


# ---------------------------------------------------------------- 2. every non-PASS state restores the originals and keeps the candidate

def test_review_open_restores_the_originals_and_keeps_the_candidate_for_inspection(tmp_path):
    root = with_original(make_prod_root(tmp_path))
    job = load(root, prod_job())
    provider = ph.Provider({DOTS: [good_reply()], NORTH: [FIX_FIRST]})   # Dots green; North (the first reviewer) gives a FIX FIRST with file:line and SPEC
    result = fl.run_job(job, root, sandbox=FakeSandbox(), openrouter=ph.client(tmp_path, provider), scrub_keys=[])
    assert result["state"] == "review-open", result
    assert (root / "pieces" / "calc.py").read_text(encoding="utf-8") == ORIGINAL_CODE
    assert not (root / "pieces" / "test_calc.py").exists()      # absent before the run, so absent after it
    candidate = root / "_loop" / "calc" / "candidate"
    assert (candidate / "pieces" / "calc.py").read_text(encoding="utf-8") == GOOD_CODE
    assert not (candidate / "tests").exists()                   # only the authorized job files are kept
    assert (root / "_loop" / "calc" / "REVIEW_FIX.md").is_file()
    assert status_of(root)["state"] == "review-open"


def test_integrity_mismatch_restores_the_originals_and_keeps_the_candidate(tmp_path):
    root = with_original(make_prod_root(tmp_path))
    job = load(root, prod_job())

    def reviewer_edits_the_candidate(request):                  # a reviewer that changes the file it reviews
        (root / "pieces" / "calc.py").write_text(CONSTANT_CODE, encoding="utf-8")
        return ph.READY

    provider = ph.Provider({DOTS: [good_reply()], GLM: [reviewer_edits_the_candidate], NORTH: [ph.READY]})
    result = fl.run_job(job, root, sandbox=FakeSandbox(), openrouter=ph.client(tmp_path, provider), scrub_keys=[])
    assert result["state"] == "integrity-mismatch", result
    assert (root / "pieces" / "calc.py").read_text(encoding="utf-8") == ORIGINAL_CODE
    candidate = root / "_loop" / "calc" / "candidate"
    assert (candidate / "pieces" / "calc.py").read_text(encoding="utf-8") == CONSTANT_CODE
    assert status_of(root)["state"] == "integrity-mismatch"


def test_an_exception_mid_write_restores_the_originals_and_writes_a_diagnostic(tmp_path, monkeypatch):
    root = with_original(make_prod_root(tmp_path))
    job = load(root, prod_job())

    def write_one_file_then_fail(self, reply):                  # a filesystem error after the first candidate file
        (self.root / "pieces" / "calc.py").write_text(GOOD_CODE, encoding="utf-8")
        raise OSError("disk full (test)")

    monkeypatch.setattr(pp.Production, "write", write_one_file_then_fail)
    provider = ph.Provider({DOTS: [good_reply()]})
    result = fl.run_job(job, root, sandbox=FakeSandbox(), openrouter=ph.client(tmp_path, provider), scrub_keys=[])
    assert result["state"] == "exception", result
    assert (root / "pieces" / "calc.py").read_text(encoding="utf-8") == ORIGINAL_CODE
    assert status_of(root)["state"] == "exception"
    diagnostic = (root / "_loop" / "calc" / "EXCEPTION.md").read_text(encoding="utf-8")
    assert "OSError" in diagnostic and "disk full (test)" in diagnostic
    assert (root / "_loop" / "calc" / "candidate" / "pieces" / "calc.py").read_text(encoding="utf-8") == GOOD_CODE


# ---------------------------------------------------------------- 3. a worker killed after one candidate write is recovered on the next launch

def child_main(root, job_path):
    """The worker of the kill test: it writes its first candidate file, then exits at once with no cleanup, the same as a kill."""
    def write_one_file_then_die(self, reply):
        rel, text = next(iter(reply.items()))
        (self.root / rel).write_text(text, encoding="utf-8")
        os._exit(9)

    pp.Production.write = write_one_file_then_die
    provider = ph.Provider({DOTS: [good_reply()]})
    fl.run_job(fl.load_job(Path(job_path)), Path(root), sandbox=FakeSandbox(), openrouter=ph.client(Path(root).parent, provider), scrub_keys=[])


def test_a_worker_killed_after_one_candidate_write_is_restored_by_the_next_launch(tmp_path):
    root = with_original(make_prod_root(tmp_path))
    job_path = write_job(root, prod_job())
    code = (f"import sys; sys.path.insert(0, {str(REPO / 'pieces')!r}); import test_pre_pilot_review as t; "
            f"t.child_main({str(root)!r}, {str(job_path)!r})")
    killed = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=300)
    assert killed.returncode == 9, killed.stderr[-2000:]
    assert (root / "pieces" / "calc.py").read_text(encoding="utf-8") == GOOD_CODE    # the abandoned candidate is on disk
    assert status_of(root)["state"] == "running"

    provider = ph.Provider({DOTS: [good_reply()], GLM: [ph.READY], NORTH: [ph.READY]})
    result = fl.run_job(fl.load_job(job_path), root, sandbox=FakeSandbox(), openrouter=ph.client(tmp_path, provider), scrub_keys=[])
    first = prompt_of(provider, DOTS, 0)
    assert ORIGINAL_CODE in first and GOOD_CODE not in first    # the restart began from the original, not the abandoned candidate
    assert result["state"] == "passed", result


# ---------------------------------------------------------------- 4. the owner's budget is exact; with the repair step off, a red job makes no GLM request

def test_the_production_budget_is_the_owner_values_and_is_passed_to_the_client(tmp_path):
    root = make_prod_root(tmp_path)
    client = pp.client_for(root, {"budget": {"free_requests": 40, "review_usd": 0.05, "paid_usd": 0}}, keys={"free": ONE_KEY})
    assert client.budget.review_usd == 0.05 and client.budget.free_requests == 40


def test_a_red_job_with_the_repair_step_off_makes_no_glm_request_of_any_kind(tmp_path):
    root = with_original(make_prod_root(tmp_path))
    job = load(root, prod_job())
    provider = ph.Provider({
        DOTS: [constant_reply()],                               # both free writers go red on the same tests
        XING: [xing_reply(), xing_reply()],
    })
    client = ph.client(tmp_path, provider, budget=oc.JobBudget(free_requests=40, review_usd=0.05))
    result = fl.run_job(job, root, sandbox=FakeSandbox(), openrouter=client, scrub_keys=[])
    assert result["state"] == "escalate", result
    assert status_of(root)["reason"] == "Opus repair unavailable"
    assert GLM not in provider.models()
    assert not any("glm" in url for _method, url, _headers in provider.http)   # no GLM request of any kind, not even the price lookup


# ---------------------------------------------------------------- 5. one key for both roles; an unlisted paid model is refused

def test_one_key_file_serves_the_free_and_the_paid_roles_with_the_same_credential(tmp_path):
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    (secrets / "openrouter_free_key.txt").write_text(ONE_KEY, encoding="utf-8")
    keys = oc.load_keys(secrets_dir=secrets, environ={})
    assert keys == {"free": ONE_KEY, "paid": ONE_KEY}

    provider = ph.Provider({DOTS: ["ok"], GLM: [ph.READY]})
    client = oc.OpenRouter(keys, tmp_path / "ledger", transport=provider, sleep=lambda seconds: None, rand=lambda: 0.0)
    client.begin_job()
    client.call("writer_a", "sys", "user", kind="text")         # free roster model
    client.call("reviewer_a", "sys", "user", kind="reviewer")   # paid roster model (z-ai): reviews only
    posts = [headers for method, url, headers in provider.http if method == "POST"]
    assert len(posts) == 2
    assert all(headers["Authorization"] == "Bearer " + ONE_KEY for headers in posts)


def test_separate_key_files_stay_separate(tmp_path):
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    (secrets / "openrouter_free_key.txt").write_text(ONE_KEY, encoding="utf-8")
    (secrets / "openrouter_paid_key.txt").write_text("sk-or-v1-PAIDTEST0000000000", encoding="utf-8")
    keys = oc.load_keys(secrets_dir=secrets, environ={})
    assert keys["free"] == ONE_KEY and keys["paid"] == "sk-or-v1-PAIDTEST0000000000"


def test_an_unlisted_paid_model_is_refused_with_the_one_key_and_no_request_is_made(tmp_path):
    provider = ph.Provider({})
    client = oc.OpenRouter({"free": ONE_KEY, "paid": ONE_KEY}, tmp_path / "ledger", transport=provider,
                           sleep=lambda seconds: None, rand=lambda: 0.0)
    with pytest.raises(oc.ModelRefused):
        client.call("z-ai/glm-6-unlisted", "sys", "user", kind="text")
    assert provider.http == []
