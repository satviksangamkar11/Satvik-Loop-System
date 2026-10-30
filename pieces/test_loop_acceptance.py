"""Loop System closure gate: one adversarial acceptance suite. Fakes for the model and the reviewers, the REAL pytest and the REAL file claims.
Scenarios: file race, missing dependency, crashed coder, failed test then repair, test-edit control, timed-out reviewer, call budgets, normal job.
No network, no model call; tmp folders only."""
import json
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import p1_claims as pc
from test_fix_loop_review import FENCE, FILE_MARK, fl, make_root

NL = chr(10)


def reply(module_src, test_src):
    return (FILE_MARK + "pieces/m.py" + NL + FENCE + "python" + NL + module_src + NL + FENCE + NL
            + FILE_MARK + "pieces/test_m.py" + NL + FENCE + "python" + NL + test_src + NL + FENCE + NL)


GOOD_MODULE = "def answer():" + NL + "    return 42"
GOOD_TEST = "from m import answer" + NL + NL + NL + "def test_answer():" + NL + "    assert answer() == 42"


def make_job(tmp_path, **extra):
    root = make_root(tmp_path)
    (root / "pieces" / "m.py").unlink()                                   # the job creates both files
    job = {"name": "j", "spec": "specs/s.md", "files": ["pieces/m.py", "pieces/test_m.py"], "tests": ["pieces/test_m.py"], "reference": [],
           "max_rounds": 3, "review": False, "review_prompt": "docs/RP.md", "fixers": ["nemotron"] * 3}
    job.update(extra)
    (root / "_loop" / "jobs").mkdir(exist_ok=True)
    path = root / "_loop" / "jobs" / "j.json"
    path.write_text(json.dumps(job), encoding="utf-8")
    return root, fl.load_job(path)


def scripted(replies):
    """A fake coder that returns the scripted replies in order (the last one repeats) and counts its calls."""
    state = {"n": 0}

    def caller(system, user):
        state["n"] += 1
        item = replies[min(state["n"] - 1, len(replies) - 1)]
        if isinstance(item, Exception):
            raise item
        return item, {"status": 200, "finish": "stop"}
    return caller, state


def read_status(root):
    return json.loads((root / "_loop" / "j" / "status.json").read_text(encoding="utf-8"))


# 1. successful normal job (real pytest) -------------------------------------------------------------------------------------------------------
def test_normal_job_passes_with_exact_call_counts(tmp_path):
    root, job = make_job(tmp_path, review=True, reviewers=["lightning", "codex"])
    caller, state = scripted([reply(GOOD_MODULE, GOOD_TEST)])

    def ready(files, report):
        report.write_text("## Verdict: READY" + NL, encoding="utf-8")
        return {"verdict": "READY"}
    result = fl.run_job(job, root, caller=caller, reviewer={"lightning": ready, "codex": ready}, scrub_keys=[])
    assert result["state"] == "passed" and result["tests_passed"] is True
    assert state["n"] == 1 and result["coder_calls"] == 1 and result["reviewer_calls"] == {"lightning": 1, "codex": 1}
    assert fl.exit_code_for(result["state"], job) == 0
    assert read_status(root)["coder_calls"] == 1
    assert (root / "_loop" / "j" / "original" / "manifest.json").is_file()


# 2. missing dependency (real pytest ImportError) -> three repairs, escalate, originals restored, attempt kept --------------------------------------
def test_missing_dependency_escalates_after_exactly_the_repair_limit_and_restores_originals(tmp_path):
    root, job = make_job(tmp_path)
    broken = reply("import dependency_that_does_not_exist_xyz" + NL + GOOD_MODULE, GOOD_TEST)
    caller, state = scripted([broken])
    result = fl.run_job(job, root, caller=caller, reviewer={}, scrub_keys=[])
    assert result["state"] == "escalate" and fl.exit_code_for("escalate", job) == 3
    assert state["n"] == 4 and result["coder_calls"] == 4                    # round 0 plus exactly three repairs
    assert "dependency_that_does_not_exist_xyz" in (root / "_loop" / "j" / "ESCALATE.md").read_text(encoding="utf-8")
    assert not (root / "pieces" / "m.py").exists() and not (root / "pieces" / "test_m.py").exists()          # originals (nothing) restored
    assert "dependency_that_does_not_exist_xyz" in (root / "_loop" / "j" / "attempt" / "pieces" / "m.py").read_text(encoding="utf-8")


# 3. crashed coder (exceptions, empty replies) ---------------------------------------------------------------------------------------------------
def test_a_crashing_coder_is_retried_then_recovers(tmp_path):
    root, job = make_job(tmp_path)
    caller, state = scripted([RuntimeError("coder process died"), "", reply(GOOD_MODULE, GOOD_TEST)])
    result = fl.run_job(job, root, caller=caller, reviewer={}, scrub_keys=[])
    assert result["state"] in ("passed", "passed-no-review") and result["tests_passed"] is True
    assert state["n"] == 3 and result["coder_calls"] == 3
    assert (root / "pieces" / "m.py").read_text(encoding="utf-8").startswith("def answer")


def test_a_coder_that_always_crashes_escalates_within_the_budget(tmp_path):
    root, job = make_job(tmp_path, max_rounds=1)
    caller, state = scripted([RuntimeError("down")])
    result = fl.run_job(job, root, caller=caller, reviewer={}, scrub_keys=[])
    assert result["state"] == "escalate" and state["n"] == 2 and result["coder_calls"] == 2


# 4. failed test then repair ---------------------------------------------------------------------------------------------------------------------
def test_a_failed_test_is_fed_back_and_repaired_without_touching_the_test(tmp_path):
    root, job = make_job(tmp_path)
    wrong = reply("def answer():" + NL + "    return 41", GOOD_TEST)
    caller, state = scripted([wrong, reply(GOOD_MODULE, GOOD_TEST)])
    result = fl.run_job(job, root, caller=caller, reviewer={}, scrub_keys=[])
    assert result["state"] in ("passed", "passed-no-review") and state["n"] == 2
    assert not (root / "_loop" / "j" / "test_edits").exists()                # the test was never edited


# 5. test-edit control -----------------------------------------------------------------------------------------------------------------------------
def test_a_test_bug_repair_is_allowed_recorded_and_diffed(tmp_path):
    root, job = make_job(tmp_path)
    buggy_test = "from m import answer" + NL + NL + NL + "def test_answer():" + NL + "    table = {}" + NL + "    assert table['k'] == answer()"
    fixed_test = "from m import answer" + NL + NL + NL + "def test_answer():" + NL + "    table = {'k': 42}" + NL + "    assert table['k'] == answer()"
    caller, _ = scripted([reply(GOOD_MODULE, buggy_test), reply(GOOD_MODULE, fixed_test)])
    result = fl.run_job(job, root, caller=caller, reviewer={}, scrub_keys=[])
    assert result["state"] in ("passed", "passed-no-review")
    repaired = [r for r in read_status(root)["rounds"] if r.get("tests_repaired")]
    assert repaired and repaired[0]["tests_repaired"] == ["pieces/test_m.py"]
    diffs = list((root / "_loop" / "j" / "test_edits").glob("*.diff"))
    assert len(diffs) == 1 and "table = {'k': 42}" in diffs[0].read_text(encoding="utf-8")        # the edit is separately reviewable


def test_a_test_edit_that_weakens_the_test_is_rejected(tmp_path):
    root, job = make_job(tmp_path, max_rounds=2)
    buggy_test = "from m import answer" + NL + NL + NL + "def test_answer():" + NL + "    table = {}" + NL + "    assert table['k'] == answer()"
    weakened = "from m import answer" + NL + NL + NL + "def test_answer():" + NL + "    assert True"
    caller, _ = scripted([reply(GOOD_MODULE, buggy_test), reply(GOOD_MODULE, weakened)])
    result = fl.run_job(job, root, caller=caller, reviewer={}, scrub_keys=[])
    assert result["state"] == "escalate"                                       # the weakened test never reached the disk
    assert not (root / "_loop" / "j" / "test_edits").exists()
    assert any("frozen" in (r.get("reason") or "") for r in read_status(root)["rounds"])


def test_an_assertion_change_is_never_a_test_bug_repair(tmp_path):
    root, job = make_job(tmp_path, max_rounds=2)
    wrong_expectation = "from m import answer" + NL + NL + NL + "def test_answer():" + NL + "    assert answer() == 43"
    easier = "from m import answer" + NL + NL + NL + "def test_answer():" + NL + "    assert answer() == 42"
    caller, _ = scripted([reply(GOOD_MODULE, wrong_expectation), reply(GOOD_MODULE, easier)])
    result = fl.run_job(job, root, caller=caller, reviewer={}, scrub_keys=[])
    assert result["state"] == "escalate"                                       # an AssertionError is a wrong expectation: the supervisor decides, not the coder


# 6. timed-out reviewer, quorum -------------------------------------------------------------------------------------------------------------------
RELEASE = threading.Event()                      # the hung reviewers wait on this; the tests set it at the end so no thread outlives them


def hanging(files, report):
    RELEASE.wait(30)
    return {"verdict": "READY"}


def ready(files, report):
    report.write_text("## Verdict: READY" + NL, encoding="utf-8")
    return {"verdict": "READY"}


def test_a_hung_reviewer_times_out_and_blocks_the_pass_by_default(tmp_path):
    root, job = make_job(tmp_path, review=True, reviewers=["lightning", "codex"])
    job["reviewer_deadline_s"] = 0.5                 # inherent real-clock test: a deadline can only be proven by letting it pass (AGENT_RULES exception)
    started = time.monotonic()
    caller, _ = scripted([reply(GOOD_MODULE, GOOD_TEST)])
    try:
        result = fl.run_job(job, root, caller=caller, reviewer={"lightning": hanging, "codex": ready}, scrub_keys=[])
    finally:
        RELEASE.set()
    assert time.monotonic() - started < 20                                     # the job did not wait for the hung reviewer
    assert result["state"] == "passed-no-review" and fl.exit_code_for(result["state"], job) == 5
    reasons = [r["reason"] for r in result["rounds"] if str(r["round"]).startswith("review")]
    assert any("timeout" in x for x in reasons)


def test_a_minimum_of_valid_reviews_lets_a_job_pass_despite_one_timeout(tmp_path):
    root, job = make_job(tmp_path, review=True, reviewers=["lightning", "codex"], min_valid_reviews=1)
    job["reviewer_deadline_s"] = 0.5
    caller, _ = scripted([reply(GOOD_MODULE, GOOD_TEST)])
    try:
        result = fl.run_job(job, root, caller=caller, reviewer={"lightning": hanging, "codex": ready}, scrub_keys=[])
    finally:
        RELEASE.set()
    assert result["state"] == "passed" and fl.exit_code_for(result["state"], job) == 0


def test_a_quorum_never_hides_a_fix_first_finding(tmp_path):
    root, job = make_job(tmp_path, review=True, reviewers=["lightning", "codex"], min_valid_reviews=1, max_rounds=1, review_rounds=1)

    def fix_first(files, report):
        report.write_text("MED - x - y" + NL + "## Verdict: FIX FIRST" + NL, encoding="utf-8")
        return {"verdict": "FIX FIRST"}
    caller, _ = scripted([reply(GOOD_MODULE, GOOD_TEST)])
    result = fl.run_job(job, root, caller=caller, reviewer={"lightning": fix_first, "codex": ready}, scrub_keys=[])
    assert result["state"] == "review-open"


def test_the_quorum_cannot_exceed_the_reviewers(tmp_path):
    root, job = make_job(tmp_path)
    job["min_valid_reviews"] = 3
    job["reviewers"] = ["lightning"]
    path = root / "_loop" / "jobs" / "bad.json"
    path.write_text(json.dumps({**job, "name": "bad"}), encoding="utf-8")
    with pytest.raises(ValueError, match="min_valid_reviews"):
        fl.load_job(path)


# 7. call budgets at the boundaries ---------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("max_rounds", [1, 2, 3])
def test_the_repair_limit_is_exact(tmp_path, max_rounds):
    root, job = make_job(tmp_path, max_rounds=max_rounds)
    caller, state = scripted([reply("def answer():" + NL + "    return 0", GOOD_TEST)])
    result = fl.run_job(job, root, caller=caller, reviewer={}, scrub_keys=[])
    assert result["state"] == "escalate" and state["n"] == max_rounds + 1 and result["coder_calls"] == max_rounds + 1
    assert result["coder_calls"] <= result["coder_budget"]


def test_review_fix_rounds_and_reviewer_calls_stay_inside_the_budget(tmp_path):
    root, job = make_job(tmp_path, review=True, reviewers=["lightning"], max_rounds=1, review_rounds=2)
    calls = {"n": 0}

    def fix_first(files, report):
        calls["n"] += 1
        report.write_text("MED - x - y" + NL + "## Verdict: FIX FIRST" + NL, encoding="utf-8")
        return {"verdict": "FIX FIRST"}
    caller, state = scripted([reply(GOOD_MODULE, GOOD_TEST)])
    result = fl.run_job(job, root, caller=caller, reviewer={"lightning": fix_first}, scrub_keys=[])
    assert result["state"] == "review-open"
    assert calls["n"] == 2 == result["reviewer_calls"]["lightning"]            # exactly review_rounds passes
    assert state["n"] == 3 == result["coder_calls"] <= result["coder_budget"] == 4


def test_a_deadline_stops_the_next_round(tmp_path, monkeypatch):
    root, job = make_job(tmp_path, deadline_min=1)
    clock = []
    monkeypatch.setattr(fl.time, "monotonic", lambda: (clock.append(1), 0.0 if len(clock) == 1 else 999.0)[1])
    caller, state = scripted([reply("def answer():" + NL + "    return 0", GOOD_TEST)])
    result = fl.run_job(job, root, caller=caller, reviewer={}, scrub_keys=[])
    assert result["state"] == "escalate" and state["n"] == 1


# 8. misleading fields and dead states ------------------------------------------------------------------------------------------------------------
def test_unsupported_model_fields_are_refused_not_ignored(tmp_path):
    root, job = make_job(tmp_path)
    for field in ("coder_model", "reviewer_model"):
        bad = {**job, "name": "bad", field: "some/other-model"}
        path = root / "_loop" / "jobs" / "bad.json"
        path.write_text(json.dumps(bad), encoding="utf-8")
        with pytest.raises(ValueError, match="not configurable"):
            fl.load_job(path)
    ok = {**job, "name": "ok", "coder_model": "nvidia/nemotron-3-ultra-550b-a55b"}
    (root / "_loop" / "jobs" / "ok.json").write_text(json.dumps(ok), encoding="utf-8")
    assert fl.load_job(root / "_loop" / "jobs" / "ok.json")["coder_model"] == "nvidia/nemotron-3-ultra-550b-a55b"


def test_there_is_no_unreachable_review_missing_state():
    assert "review-missing" not in fl.exit_code_for.__doc__
    assert fl.exit_code_for("review-missing", {"review": True}) == 1                   # an unknown state is simply 'anything else'
    for state, code in (("passed", 0), ("passed-no-review", 5), ("escalate", 3), ("review-open", 1), ("running", 1)):
        assert fl.exit_code_for(state, {"review": True}) == code


# 9. file race through the launcher ----------------------------------------------------------------------------------------------------------------
def test_a_job_cannot_start_while_another_job_rewrites_its_reference_file(tmp_path):
    repo = tmp_path / "repo"
    for sub in ("_loop/jobs", "pieces", "specs", "docs"):
        (repo / sub).mkdir(parents=True)
    (repo / "specs" / "s.md").write_text("spec", encoding="utf-8")
    (repo / "pieces" / "shared.py").write_text("x = 1", encoding="utf-8")
    writer = {"name": "writer", "spec": "specs/s.md", "files": ["pieces/shared.py"], "tests": ["pieces/shared.py"], "reference": [], "fixers": ["nemotron"]}
    reader = {"name": "reader", "spec": "specs/s.md", "files": ["pieces/other.py"], "tests": ["pieces/other.py"], "reference": ["pieces/shared.py"], "fixers": ["nemotron"]}
    (repo / "_loop" / "jobs" / "writer.json").write_text(json.dumps(writer), encoding="utf-8")
    (repo / "_loop" / "jobs" / "reader.json").write_text(json.dumps(reader), encoding="utf-8")
    store = pc.ClaimStore(repo / "_loop")
    held = store.claim([repo / "_loop" / "jobs" / "writer.json", repo / "pieces" / "shared.py"], "writer")
    try:
        with pytest.raises(pc.ClaimConflict):
            pc.run_job("_loop/jobs/reader.json", repo, loop_script=str(tmp_path / "never_runs.py"), loop_log=False)
    finally:
        held.release()


def test_a_syntax_error_test_repair_cannot_delete_tests_or_asserts(tmp_path):
    broken = tmp_path / "test_m.py"
    broken.write_text("from m import answer" + NL + NL + NL + "def test_answer(:" + NL + "    assert answer() == 42" + NL, encoding="utf-8")
    deleted = "from m import answer" + NL + NL + NL + "def test_other():" + NL + "    pass" + NL
    faithful = "from m import answer" + NL + NL + NL + "def test_answer():" + NL + "    assert answer() == 42" + NL
    renamed = "from m import answer" + NL + NL + NL + "def test_renamed():" + NL + "    assert answer() == 42" + NL
    assert fl._test_change_forbidden(broken, deleted, None) is True            # the test and its assert would vanish
    assert fl._test_change_forbidden(broken, renamed, None) is True            # the test function would disappear under a new name
    assert fl._test_change_forbidden(broken, faithful, None) is False          # a faithful syntax repair is accepted
    assert fl._test_change_forbidden(broken, broken.read_text(encoding="utf-8"), None) is False


def test_a_reply_with_a_syntax_error_never_reaches_the_disk(tmp_path):
    root, job = make_job(tmp_path, max_rounds=1)
    broken = reply(GOOD_MODULE, "def test_answer(:" + NL + "    assert True")
    caller, _ = scripted([broken])
    result = fl.run_job(job, root, caller=caller, reviewer={}, scrub_keys=[])
    assert result["state"] == "escalate"
    assert any("SyntaxError at line" in (r.get("reason") or "") for r in read_status(root)["rounds"])


def test_the_launcher_reads_every_referenced_file_and_creates_its_state_folder(tmp_path):
    repo = tmp_path / "repo"
    for sub in ("pieces", "specs", "docs"):
        (repo / sub).mkdir(parents=True)                                     # note: NO _loop folder yet
    (repo / "specs" / "s.md").write_text("spec", encoding="utf-8")
    (repo / "docs" / "RP.md").write_text("prompt", encoding="utf-8")
    (repo / "pieces" / "regr.py").write_text("x = 1", encoding="utf-8")
    job = {"name": "j", "spec": "specs/s.md", "files": ["pieces/a.py"], "tests": ["pieces/a.py"], "reference": [], "regression": ["pieces/regr.py"],
           "review_prompt": "docs/RP.md", "fixers": ["nemotron"]}
    (repo / "job.json").write_text(json.dumps(job), encoding="utf-8")
    script = tmp_path / "noop.py"
    script.write_text("pass" + NL, encoding="utf-8")
    seen = {}
    real_claim = pc.ClaimStore.claim

    def spy(self, paths, job_name="", reads=()):
        seen["reads"] = [Path(r).name for r in reads]
        return real_claim(self, paths, job_name, reads=reads)
    pc.ClaimStore.claim = spy
    try:
        code = pc.run_job("job.json", repo, loop_script=str(script), loop_log=False)
    finally:
        pc.ClaimStore.claim = real_claim
    assert code == 0 and (repo / "_loop").is_dir()
    assert {"regr.py", "s.md", "RP.md"} <= set(seen["reads"])


def test_a_loop_crash_prints_its_traceback(tmp_path, capsys):
    repo = tmp_path / "repo"
    for sub in ("_loop", "specs"):
        (repo / sub).mkdir(parents=True)
    (repo / "specs" / "s.md").write_text("spec", encoding="utf-8")
    job = {"name": "j", "spec": "specs/s.md", "files": ["pieces/a.py"], "tests": ["pieces/a.py"], "reference": [], "fixers": ["nemotron"]}
    (repo / "job.json").write_text(json.dumps(job), encoding="utf-8")
    script = tmp_path / "boom.py"
    script.write_text("raise RuntimeError('loop exploded')" + NL, encoding="utf-8")
    assert pc.run_job("job.json", repo, loop_script=str(script), loop_log=False) == 1
    assert "loop exploded" in capsys.readouterr().err


# 9. repeated identical failure: three failed repairs with the same failing-test set stop the job (owner bug fix 2026-10-30) ---------------------------
BAD_MODULE = "def answer():" + NL + "    return 41"


def fix_first(files, report):
    report.write_text("## Verdict: FIX FIRST" + NL + "HIGH - pieces/m.py - change something", encoding="utf-8")
    return {"verdict": "FIX FIRST"}


def test_failing_test_set_reads_the_pytest_summary_lines():
    out = "x" + NL + "FAILED pieces/test_m.py::test_a - assert 1 == 2" + NL + "ERROR pieces/test_n.py::test_b" + NL + "FAILED pieces/test_m.py::test_a - again" + NL + "2 failed"
    assert fl.failing_test_set(out) == frozenset({"pieces/test_m.py::test_a", "pieces/test_n.py::test_b"})
    assert fl.failing_test_set("") == frozenset() and fl.failing_test_set(None) == frozenset()


def test_three_identical_failed_review_repairs_stop_the_job_as_needs_review(tmp_path):
    root, job = make_job(tmp_path, review=True, reviewers=["lightning"], review_rounds=6)
    caller, state = scripted([reply(GOOD_MODULE, GOOD_TEST)] + [reply(BAD_MODULE, GOOD_TEST)] * 8)
    result = fl.run_job(job, root, caller=caller, reviewer={"lightning": fix_first}, scrub_keys=[])
    assert result["state"] == "needs_review" and fl.exit_code_for("needs_review", job) == 3
    assert state["n"] == 4 and result["coder_calls"] == 4                    # one passing round, then exactly three failed repairs
    assert (root / "pieces" / "m.py").read_text(encoding="utf-8").strip() == GOOD_MODULE        # the last passing version is preserved
    note = (root / "_loop" / "j" / "NEEDS_REVIEW.md").read_text(encoding="utf-8")
    assert "pieces/test_m.py::test_answer" in note and "3" in note
    assert "return 41" in (root / "_loop" / "j" / "attempt" / "pieces" / "m.py").read_text(encoding="utf-8")        # the failing attempt is kept as evidence
    assert read_status(root)["state"] == "needs_review"


def test_identical_failures_of_a_job_that_never_passed_stop_early_and_leave_evidence(tmp_path):
    root, job = make_job(tmp_path, max_rounds=8)
    caller, state = scripted([reply(BAD_MODULE, GOOD_TEST)])
    result = fl.run_job(job, root, caller=caller, reviewer={}, scrub_keys=[])
    assert result["state"] == "escalate" and state["n"] == 4 and result["coder_calls"] == 4          # round 0 plus three repairs, not nine rounds
    assert "pieces/test_m.py::test_answer" in (root / "_loop" / "j" / "NEEDS_REVIEW.md").read_text(encoding="utf-8")


def test_failures_with_different_failing_sets_are_not_stopped_early(tmp_path):
    two = GOOD_TEST + NL + NL + NL + "def test_second():" + NL + "    assert answer() > 0"
    only_a = "def answer():" + NL + "    return -1"                         # fails test_answer and test_second
    only_b = "def answer():" + NL + "    return 42" + NL + NL + "import nonexistent_zz"      # collection error
    root, job = make_job(tmp_path, max_rounds=5)
    caller, state = scripted([reply(only_a, two), reply(only_a, two), reply(only_b, two), reply(only_a, two), reply(only_b, two), reply(GOOD_MODULE, two)])
    result = fl.run_job(job, root, caller=caller, reviewer={}, scrub_keys=[])
    assert state["n"] == 6 and result["tests_passed"] is True


def test_a_pass_resets_the_streak(tmp_path):
    root, job = make_job(tmp_path, review=True, reviewers=["lightning"], review_rounds=6)
    seq = [reply(GOOD_MODULE, GOOD_TEST), reply(BAD_MODULE, GOOD_TEST), reply(BAD_MODULE, GOOD_TEST), reply(GOOD_MODULE, GOOD_TEST),
           reply(BAD_MODULE, GOOD_TEST), reply(BAD_MODULE, GOOD_TEST), reply(GOOD_MODULE, GOOD_TEST)]
    caller, state = scripted(seq)
    result = fl.run_job(job, root, caller=caller, reviewer={"lightning": fix_first}, scrub_keys=[])
    assert result["state"] != "needs_review" and state["n"] >= 7          # two failures, a pass, two failures, a pass: never three in a row
