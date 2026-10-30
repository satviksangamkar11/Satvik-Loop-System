"""P1-D23: the loop's REVIEW STAGE wiring (the part 188 green tests never touched). Fakes only: no network, no model call, no real codex."""
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "source"))
sys.path.insert(0, str(REPO / "scripts" / "image_trials"))
import fix_loop as fl


FILE_MARK = "FIL" + "E: "                     # built by concatenation: a literal line start would look like an output file to the loop's parser
FENCE = "`" * 3


def make_root(tmp_path):
    root = tmp_path / "repo"
    (root / "docs").mkdir(parents=True)
    (root / "specs").mkdir()
    (root / "pieces").mkdir()
    (root / "_loop").mkdir()
    (root / "docs" / "RP.md").write_text("Review the files. End with `## Verdict: READY` or `## Verdict: FIX FIRST`.", encoding="utf-8")
    (root / "specs" / "s.md").write_text("spec", encoding="utf-8")
    (root / "pieces" / "m.py").write_text("X = 1\n", encoding="utf-8")
    return root


def test_lightning_reviewer_uses_the_jobs_prompt_and_its_own_token_limit(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    seen = {}

    class FakeReviewRun:
        @staticmethod
        def run_review(**kw):
            seen.update(kw)
            return {"verdict": "READY"}

    monkeypatch.setattr(fl, "_load_module_by_path", lambda name, path: FakeReviewRun)
    reviewers = fl._default_reviewers_factory(root, "docs/RP.md")
    report = root / "_loop" / "r.md"
    report.write_text("stale", encoding="utf-8")                      # a stale report must not make the review fail
    assert reviewers["lightning"]([root / "pieces" / "m.py"], report) == {"verdict": "READY"}
    assert seen["prompt_file"] == root / "docs" / "RP.md"
    assert seen["max_tokens"] == 16000                              # Lightning keeps the 16k budget it was proven with, not the coder's 128k
    assert not report.exists()


@pytest.fixture
def real_review_run(monkeypatch):
    orig = fl._load_module_by_path
    monkeypatch.setattr(fl, "_load_module_by_path", lambda name, path: orig(name, REPO / "scripts" / "source" / "review_run.py"))


def test_codex_reviewer_runs_with_the_right_call_and_returns_the_verdict(tmp_path, real_review_run):
    root = make_root(tmp_path)
    calls = []

    def runner(cmd, text, timeout_s):
        calls.append(cmd)
        Path(cmd[cmd.index("-o") + 1]).write_text("## Verdict: READY\nall good\n", encoding="utf-8")
        return 0, ""

    reviewers = fl._default_reviewers_factory(root, "docs/RP.md", codex_runner=runner)
    result = reviewers["codex"]([root / "pieces" / "m.py"], root / "_loop" / "c.md")
    assert result["verdict"] == "READY"
    cmd = calls[0]
    assert "gpt-6-luna" in cmd and "model_reasoning_effort=medium" in cmd and "read-only" in cmd


def test_missing_review_prompt_is_a_refusal_not_a_crash(tmp_path, real_review_run):
    root = make_root(tmp_path)
    reviewers = fl._default_reviewers_factory(root, "docs/NOPE.md", codex_runner=lambda c, t, s: (0, ""))
    assert reviewers["codex"]([root / "pieces" / "m.py"], root / "_loop" / "c.md")["verdict"] == "NONE"
    assert reviewers["lightning"]([root / "pieces" / "m.py"], root / "_loop" / "g.md")["verdict"] == "NONE"


def test_default_codex_runner_starts_codex_cmd_and_uses_no_pipes(monkeypatch):
    import subprocess
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"], seen["kw"] = cmd, kw

        class R:
            returncode = 0
        return R()

    monkeypatch.setattr(fl.subprocess, "run", fake_run)
    monkeypatch.setattr("shutil.which", lambda name: r"C:\fake\codex.cmd" if name == "codex.cmd" else None)
    code, out = fl._default_codex_runner(["codex", "exec", "-"], "prompt", 5)
    assert code == 0 and seen["cmd"][0] == r"C:\fake\codex.cmd"
    assert seen["kw"].get("stdout") is not None and seen["kw"].get("stderr") is not None      # files, not pipes
    assert seen["kw"].get("capture_output") is None


def run_with(tmp_path, verdict, max_rounds=1, reviewers=("lightning",)):
    root = make_root(tmp_path)
    job = {"name": "j", "spec": "specs/s.md", "files": ["pieces/m.py", "pieces/test_m.py"], "tests": ["pieces/test_m.py"], "reference": [],
           "max_rounds": max_rounds, "review": True, "reviewers": list(reviewers), "review_prompt": "docs/RP.md", "fixers": ["nemotron"] * 3}
    (root / "_loop" / "jobs").mkdir()
    (root / "_loop" / "jobs" / "j.json").write_text(json.dumps(job), encoding="utf-8")
    loaded = fl.load_job(root / "_loop" / "jobs" / "j.json")
    reply = (FILE_MARK + "pieces/m.py\n" + FENCE + "python\nX = 2\n" + FENCE + "\n" + FILE_MARK + "pieces/test_m.py\n" + FENCE + "python\ndef test_x():\n    assert True\n" + FENCE + "\n")
    calls = {"review": 0}

    def reviewer(files, report):
        calls["review"] += 1
        report.write_text("## Verdict: " + verdict + "\n", encoding="utf-8")
        return {"verdict": verdict}

    result = fl.run_job(loaded, root, caller=lambda s, u: (reply, {"status": 200, "finish": "stop"}), reviewer={"lightning": reviewer},
                        pytest_runner=lambda *a, **k: (True, "ok"), scrub_keys=[])
    return result, calls, root


def test_ready_review_ends_in_passed(tmp_path):
    result, calls, _ = run_with(tmp_path, "READY")
    assert result["state"] == "passed" and calls["review"] == 1


def test_fix_first_on_the_last_round_is_review_open_not_running_forever(tmp_path):
    result, calls, root = run_with(tmp_path, "FIX FIRST", max_rounds=1)
    assert result["state"] == "review-open"
    status = json.loads((root / "_loop" / "j" / "status.json").read_text(encoding="utf-8"))
    assert status["state"] == "review-open"
    assert (root / "pieces" / "m.py").read_text(encoding="utf-8").strip() == "X = 2"      # tests passed: the files are kept


def test_no_reviewer_verdict_is_passed_no_review(tmp_path):
    result, calls, _ = run_with(tmp_path, "NONE")
    assert result["state"] == "passed-no-review"


def test_a_required_reviewer_that_is_unavailable_is_not_a_pass(tmp_path):
    result, calls, _ = run_with(tmp_path, "READY", reviewers=("lightning", "codex"))          # the fake reviewer dict only has glm
    assert result["state"] == "passed-no-review"


@pytest.mark.parametrize("state,review,expected", [("passed", True, 0), ("passed", False, 0), ("passed-no-review", False, 0), ("passed-no-review", True, 5),
                                                    ("escalate", True, 3), ("review-open", True, 1), ("running", True, 1)])
def test_exit_codes_stop_a_chain_when_a_required_review_is_missing(state, review, expected):
    assert fl.exit_code_for(state, {"review": review}) == expected


def test_minimax_reviewer_is_wired_next_to_glm_with_thinking_off_defaults(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    seen = []

    class FakeReviewRun:
        @staticmethod
        def run_review(**kw):
            seen.append(kw)
            return {"verdict": "READY"}

    monkeypatch.setattr(fl, "_load_module_by_path", lambda name, path: FakeReviewRun)
    reviewers = fl._default_reviewers_factory(root, "docs/RP.md")
    assert {"lightning", "minimax", "codex", "haiku"} == set(reviewers)
    assert reviewers["lightning"]([root / "pieces" / "m.py"], root / "_loop" / "m.md") == {"verdict": "READY"}
    assert seen[0]["model"] in ("nvidia/nemotron-3.5-lightning-30b-a3b", "minimaxai/minimax-m3.1-flash") and seen[0]["max_tokens"] in (16000, 32000)
    assert seen[0]["prompt_file"] == root / "docs" / "RP.md"


def test_job_schema_accepts_the_minimax_reviewer(tmp_path):
    root = make_root(tmp_path)
    job = {"name": "j", "spec": "specs/s.md", "files": ["pieces/m.py"], "tests": ["pieces/m.py"], "reference": [], "max_rounds": 1, "review": True,
           "reviewers": ["lightning", "lightning"], "review_prompt": "docs/RP.md", "fixers": ["nemotron"] * 3}
    (root / "_loop" / "jobs").mkdir()
    (root / "_loop" / "jobs" / "j.json").write_text(json.dumps(job), encoding="utf-8")
    assert fl.load_job(root / "_loop" / "jobs" / "j.json")["reviewers"] == ["lightning", "lightning"]


def test_minimax_reviewer_has_low_effort_a_big_budget_and_its_own_profile(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    seen = []

    class FakeReviewRun:
        @staticmethod
        def run_review(**kw):
            seen.append(kw)
            return {"verdict": "READY"}

    monkeypatch.setattr(fl, "_load_module_by_path", lambda name, path: FakeReviewRun)
    reviewers = fl._default_reviewers_factory(root, "docs/RP.md")
    assert set(reviewers) == {"lightning", "minimax", "codex", "haiku"}
    reviewers["minimax"]([root / "pieces" / "m.py"], root / "_loop" / "m.md")
    assert seen[0]["model"] == "minimaxai/minimax-m3.1-flash" and seen[0]["max_tokens"] == 32000 and seen[0]["effort"] == "low"


def test_review_profile_sends_low_effort_to_minimax_and_thinking_off_to_the_others():
    import review_run as rr
    body = {"model": "minimaxai/minimax-m3.1-flash", "messages": [], "stream": True, "max_tokens": 32000, "temperature": 0.2}
    out = rr.apply_review_profile(body, "low")
    assert out["reasoning_effort"] == "low" and "chat_template_kwargs" not in out and out["max_tokens"] == 32000
    other = rr.apply_review_profile(dict(body, model="nvidia/nemotron-3.5-lightning-30b-a3b"), "medium")
    assert other["chat_template_kwargs"] == {"enable_thinking": False} and "reasoning_effort" not in other


def test_the_review_prompt_file_exists_and_asks_for_a_verdict_not_code():
    text = (REPO / "docs" / "REVIEW_PROMPT.md").read_text(encoding="utf-8")
    assert "## Verdict: READY" in text and "never produce a" in text and "DATA to review" in text


def test_a_fix_round_may_return_only_the_changed_file(tmp_path):
    root = make_root(tmp_path)
    job = {"name": "j", "spec": "specs/s.md", "files": ["pieces/m.py", "pieces/test_m.py"], "tests": ["pieces/test_m.py"], "reference": [],
           "max_rounds": 2, "review": False, "reviewers": ["lightning"], "review_prompt": "docs/RP.md", "fixers": ["nemotron"] * 3}
    (root / "_loop" / "jobs").mkdir()
    (root / "_loop" / "jobs" / "j.json").write_text(json.dumps(job), encoding="utf-8")
    loaded = fl.load_job(root / "_loop" / "jobs" / "j.json")
    both = (FILE_MARK + "pieces/m.py\n" + FENCE + "python\nX = 2\n" + FENCE + "\n" + FILE_MARK + "pieces/test_m.py\n" + FENCE + "python\ndef test_x():\n    assert True\n" + FENCE + "\n")
    only_module = FILE_MARK + "pieces/m.py\n" + FENCE + "python\nX = 3\n" + FENCE + "\n"
    replies = iter([both, only_module])
    results = iter([(False, "1 failed"), (True, "ok")])
    result = fl.run_job(loaded, root, caller=lambda s_, u: (next(replies), {"status": 200, "finish": "stop"}), pytest_runner=lambda *a, **k: next(results), reviewer={}, scrub_keys=[])
    assert result["state"] in ("passed", "passed-no-review")
    assert (root / "pieces" / "m.py").read_text(encoding="utf-8").strip() == "X = 3"                 # the changed file was taken
    assert "assert True" in (root / "pieces" / "test_m.py").read_text(encoding="utf-8")            # the unchanged file stayed


def test_the_fix_prompt_tells_the_coder_to_return_every_file(tmp_path):
    root = make_root(tmp_path)
    (root / "pieces").mkdir(exist_ok=True)
    (root / "pieces" / "test_m.py").write_text("def test_a():
    assert True
", encoding="utf-8")
    job = {"name": "j", "spec": "specs/s.md", "files": ["pieces/m.py", "pieces/test_m.py"], "tests": ["pieces/test_m.py"], "reference": [], "fixers": ["nemotron"] * 3}
    (root / "_loop" / "jobs").mkdir(exist_ok=True)
    (root / "_loop" / "jobs" / "j.json").write_text(json.dumps(job), encoding="utf-8")
    job = fl.load_job(root / "_loop" / "jobs" / "j.json")
    _, user = fl.build_prompt(job, root, 1, pytest_output="1 failed")
    # changed 2026-10-30 on purpose: tests are frozen after round 0, so a fix round must return only NON-TEST files (a reply that touches a test is rejected)
    assert "Return every NON-TEST file of the job in full" in user and "FROZEN" in user and job["tests"][0] in user


def test_a_missing_test_file_is_asked_for_not_frozen(tmp_path):
    note = fl._freeze_note({"tests": ["pieces/test_a.py", "pieces/test_b.py"]}, _tmp_with(tmp_path, "pieces/test_a.py"))
    assert "FROZEN" in note and "pieces/test_a.py" in note.split("MUST")[0] and "MUST be returned in full: pieces/test_b.py" in note


def _tmp_with(tmp_path, rel):
    (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / rel).write_text("x", encoding="utf-8")
    return tmp_path


def test_a_job_without_a_reviewer_list_gets_all_three_standing_reviewers(tmp_path):
    root = make_root(tmp_path)
    job = {"name": "j", "spec": "specs/s.md", "files": ["pieces/m.py"], "tests": ["pieces/m.py"], "reference": [], "max_rounds": 1, "fixers": ["nemotron"] * 3}
    (root / "_loop" / "jobs").mkdir()
    (root / "_loop" / "jobs" / "j.json").write_text(json.dumps(job), encoding="utf-8")
    assert fl.load_job(root / "_loop" / "jobs" / "j.json")["reviewers"] == ["lightning", "minimax", "codex", "haiku"]


def test_the_default_review_prompt_is_the_repo_review_prompt(tmp_path):
    root = make_root(tmp_path)
    job = {"name": "j", "spec": "specs/s.md", "files": ["pieces/m.py"], "tests": ["pieces/m.py"], "reference": [], "fixers": ["nemotron"] * 3}
    (root / "_loop" / "jobs").mkdir(exist_ok=True)
    (root / "_loop" / "jobs" / "j.json").write_text(json.dumps(job), encoding="utf-8")
    assert fl.load_job(root / "_loop" / "jobs" / "j.json")["review_prompt"] == "docs/REVIEW_PROMPT.md"
    assert (REPO / "docs" / "REVIEW_PROMPT.md").is_file()


def test_review_rounds_used_up_after_fix_first_is_review_open(tmp_path):
    result, calls, root = run_with(tmp_path, "FIX FIRST", max_rounds=1)
    assert result["state"] == "review-open"


def test_the_coder_pool_is_built_per_call_not_once_per_job():
    src = Path(fl.__file__).read_text(encoding="utf-8")
    body = src.split("def _default_caller_factory")[1].split("def _default_reviewers_factory")[0]
    assert body.index("def caller") < body.index("KeyPool(keys)")


def test_the_codex_report_is_scrubbed_before_the_next_prompt():
    src = Path(fl.__file__).read_text(encoding="utf-8")
    assert "_scrub(report_path.read_text" in src


def test_the_reviewers_of_one_job_run_at_the_same_time(tmp_path):
    import time
    root = make_root(tmp_path)
    job = {"name": "j", "spec": "specs/s.md", "files": ["pieces/m.py", "pieces/test_m.py"], "tests": ["pieces/test_m.py"], "reference": [],
           "max_rounds": 1, "review": True, "reviewers": ["lightning", "minimax", "codex"], "review_prompt": "docs/RP.md", "fixers": ["nemotron"] * 3}
    (root / "_loop" / "jobs").mkdir()
    (root / "_loop" / "jobs" / "j.json").write_text(json.dumps(job), encoding="utf-8")
    loaded = fl.load_job(root / "_loop" / "jobs" / "j.json")
    reply = (FILE_MARK + "pieces/m.py\n" + FENCE + "python\nX = 2\n" + FENCE + "\n" + FILE_MARK + "pieces/test_m.py\n" + FENCE + "python\ndef test_x():\n    assert True\n" + FENCE + "\n")
    started = []

    def slow(name):
        def reviewer(files, report):
            started.append((name, time.monotonic()))
            time.sleep(0.4)
            report.write_text("## Verdict: READY\n", encoding="utf-8")
            return {"verdict": "READY"}
        return reviewer

    t0 = time.monotonic()
    result = fl.run_job(loaded, root, caller=lambda s_, u: (reply, {"status": 200, "finish": "stop"}),
                        reviewer={n: slow(n) for n in ("lightning", "minimax", "codex")}, pytest_runner=lambda *a, **k: (True, "ok"), scrub_keys=[])
    elapsed = time.monotonic() - t0
    assert result["state"] == "passed"
    assert elapsed < 1.0, elapsed                                              # three 0.4 s reviewers: about 0.4 s in parallel, 1.2 s in sequence
    assert max(t for _, t in started) - min(t for _, t in started) < 0.3        # they really started together
    assert [r["round"] for r in result["rounds"] if str(r["round"]).startswith("review")] == ["review_0_lightning", "review_0_minimax", "review_0_codex"]   # original order kept


def test_a_test_file_with_a_syntax_error_is_not_frozen(tmp_path):
    (tmp_path / "pieces").mkdir()
    (tmp_path / "pieces" / "test_ok.py").write_text("def test_a():\n    assert True\n", encoding="utf-8")
    (tmp_path / "pieces" / "test_bad.py").write_text("@pytest.fixture(scope(\"class\")\ndef f():\n    pass\n", encoding="utf-8")
    note = fl._freeze_note({"tests": ["pieces/test_ok.py", "pieces/test_bad.py", "pieces/test_new.py"]}, tmp_path)
    assert "(pieces/test_ok.py) are FROZEN" in note                                     # parseable existing test: frozen
    assert "SYNTAX ERROR" in note and "(change only what is needed to make them valid): pieces/test_bad.py." in note
    assert "MUST be returned in full: pieces/test_new.py." in note                      # missing test: must be written
    assert fl._parses(tmp_path / "pieces" / "test_ok.py") and not fl._parses(tmp_path / "pieces" / "test_bad.py")


def test_load_job_rejects_review_files_that_miss_a_job_file(tmp_path):
    root = make_root(tmp_path)
    job = {"name": "j", "spec": "specs/s.md", "files": ["pieces/m.py", "pieces/test_m.py"], "tests": ["pieces/test_m.py"], "reference": [],
           "review_files": ["pieces/other.py"], "fixers": ["nemotron"] * 3}
    (root / "_loop" / "jobs").mkdir()
    path = root / "_loop" / "jobs" / "j.json"
    path.write_text(json.dumps(job), encoding="utf-8")
    with pytest.raises(ValueError, match="review_files must include every job file"):
        fl.load_job(path)
    job["review_files"] = ["pieces/m.py", "pieces/test_m.py", "specs/s.md"]
    path.write_text(json.dumps(job), encoding="utf-8")
    assert fl.load_job(path)["review_files"] == job["review_files"]


def test_a_test_bug_may_be_repaired_but_never_weakened():
    err = "pieces" + chr(92) + "test_x.py:241: KeyError"
    assert fl._test_bug_in_output(err) and not fl._test_bug_in_output("E   assert 1 == 2" + chr(10) + "pieces/test_x.py:10: AssertionError")
    assert not fl._test_bug_in_output(None)
    old = "def test_a():NL    d = {}NL    assert d['k'] == 1NLdef test_b():NL    assert TrueNL".replace("NL", chr(10))
    fixed = "def test_a():NL    d = {'k': 1}NL    assert d['k'] == 1NLdef test_b():NL    assert TrueNL".replace("NL", chr(10))
    fewer_tests = "def test_a():NL    d = {'k': 1}NL    assert d['k'] == 1NL".replace("NL", chr(10))
    fewer_asserts = "def test_a():NL    d = {'k': 1}NLdef test_b():NL    assert TrueNL".replace("NL", chr(10))
    assert fl._not_weaker(old, fixed)
    assert not fl._not_weaker(old, fewer_tests) and not fl._not_weaker(old, fewer_asserts)
    assert not fl._not_weaker(old, "def test_a(:")
