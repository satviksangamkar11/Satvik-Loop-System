"""P0 (2026-11-02): the review acceptance decision.

Bug reproduced here: with reviewers READY + FIX FIRST + NONE, the Haiku tie-breaker's READY cleared the missing review and the job ended `passed`.
Rules fixed by P0:
- a judge never stands in for a missing, invalid or timed-out review (every profile);
- production: acceptance needs every required reviewer to give a valid READY, at least two distinct reviewers, and the judge cannot accept at all;
- legacy: the owner's tie-breaker rule stays, but only when the review quorum is really met.
Fakes only: no model call."""
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "source"))
import fix_loop as fl  # noqa: E402
import p0_gates as gates  # noqa: E402
from test_fix_loop_review import FENCE, FILE_MARK, make_root  # noqa: E402

R, F, N = "READY", "FIX FIRST", "NONE"


# ---------------------------------------------------------------- truth table of the pure decision function

PRODUCTION_TABLE = [
    # verdicts of the required reviewers, judge verdict, coder-fixable FIX FIRST?, test findings?, expected decision
    ({"a": R, "b": R}, None, False, False, "accept"),
    ({"a": R, "b": R, "c": R}, None, False, False, "accept"),
    ({"a": R, "b": N}, None, False, False, "blocked"),          # a timed-out review blocks
    ({"a": R, "b": None}, None, False, False, "blocked"),       # an unavailable reviewer blocks
    ({"a": R, "b": "garbage"}, None, False, False, "blocked"),  # an invalid verdict blocks
    ({"a": R, "b": N}, R, False, False, "blocked"),             # a judge never replaces a missing review
    ({"a": R, "b": F}, None, True, False, "fix"),
    ({"a": R, "b": F}, R, True, False, "fix"),                  # production: the judge cannot overrule a blocking finding
    ({"a": R, "b": F, "c": N}, R, True, False, "fix"),          # THE reported bypass: READY + FIX FIRST + NONE
    ({"a": F, "b": F}, None, True, False, "fix"),
    ({"a": R, "b": F}, None, False, True, "test-findings"),     # FIX FIRST about a frozen test only
    ({"a": R}, None, False, False, "blocked"),                  # one reviewer is never production quorum
    ({}, None, False, False, "blocked"),
]


@pytest.mark.parametrize("verdicts,judge,fix,test_findings,expected", PRODUCTION_TABLE)
def test_production_truth_table(verdicts, judge, fix, test_findings, expected):
    got = gates.decide_review(verdicts, list(verdicts), judge=judge, coder_fix_first=fix, test_findings=test_findings, production=True)
    assert got == expected


LEGACY_TABLE = [
    ({"a": R, "b": R}, None, False, False, 0, "accept"),
    ({"a": R, "b": N}, None, False, False, 0, "blocked"),               # default quorum = every reviewer
    ({"a": R, "b": F}, R, True, False, 0, "accept"),                    # owner rule 2026-10-31 kept: a complete panel's disagreement goes to the judge
    ({"a": R, "b": F}, F, True, False, 0, "fix"),
    ({"a": R, "b": F}, None, True, False, 0, "fix"),                    # judge gave no verdict
    ({"a": R, "b": F, "c": N}, R, True, False, 0, "fix"),               # THE bypass: the judge may not fill the missing review
    ({"a": R, "b": F, "c": N}, R, True, False, 2, "accept"),            # an explicit lower quorum is met by two valid reviews
    ({"a": R, "b": N, "c": N}, R, False, False, 2, "blocked"),          # quorum 2 not met, judge cannot fill it
    ({"a": R, "b": F}, R, True, True, 0, "fix"),                        # a judge never clears a test finding: coder findings go to repair first
    ({"a": R, "b": F}, R, False, True, 0, "test-findings"),             # only a test finding left: it blocks, the judge cannot clear it
]


@pytest.mark.parametrize("verdicts,judge,fix,test_findings,min_valid,expected", LEGACY_TABLE)
def test_legacy_truth_table(verdicts, judge, fix, test_findings, min_valid, expected):
    got = gates.decide_review(verdicts, list(verdicts), judge=judge, coder_fix_first=fix, test_findings=test_findings,
                              min_valid=min_valid, production=False)
    assert got == expected


# ---------------------------------------------------------------- end to end through run_job (the loop's real review stage)

def _reply():
    return (FILE_MARK + "pieces/m.py\n" + FENCE + "python\nX = 2\n" + FENCE + "\n"
            + FILE_MARK + "pieces/test_m.py\n" + FENCE + "python\ndef test_x():\n    assert True\n" + FENCE + "\n")


def _reviewer(verdict):
    def run(files, report):
        report.write_text("## Verdict: " + verdict + "\n" + ("- pieces/m.py:1 - defect - fix\n" if verdict == F else ""), encoding="utf-8")
        return {"verdict": verdict, "status": "error" if verdict == N else "ok"}
    return run


def _legacy_job(root, reviewers, **extra):
    job = {"name": "j", "spec": "specs/s.md", "files": ["pieces/m.py", "pieces/test_m.py"], "tests": ["pieces/test_m.py"], "reference": [],
           "max_rounds": 1, "review_rounds": 1, "review": True, "reviewers": reviewers, "review_prompt": "docs/RP.md", "fixers": ["nemotron"] * 3, **extra}
    (root / "_loop" / "jobs").mkdir(exist_ok=True)
    (root / "_loop" / "jobs" / "j.json").write_text(json.dumps(job), encoding="utf-8")
    return fl.load_job(root / "_loop" / "jobs" / "j.json")


def test_judge_ready_cannot_fill_a_missing_review_end_to_end(tmp_path):
    """Regression for the reported quorum bypass: READY + FIX FIRST + NONE, judge says READY. Before P0 this ended `passed`."""
    root = make_root(tmp_path)
    job = _legacy_job(root, ["lightning", "codex", "minimax"])
    panel = {"lightning": _reviewer(R), "codex": _reviewer(F), "minimax": _reviewer(N)}
    judge_calls = []

    def judge(system, user):
        judge_calls.append(1)
        return ("The finding is a false positive.\n## Verdict: READY\n", {"status": 200})

    result = fl.run_job(job, root, caller=lambda s, u: (_reply(), {"status": 200}), reviewer=panel,
                        pytest_runner=lambda *a, **k: (True, "ok"), scrub_keys=[], callers={"judge": judge})
    assert result["state"] != "passed"
    assert result["review_verdict"] != "READY"


def test_review_false_is_refused_for_a_production_job(tmp_path):
    root = make_root(tmp_path)
    with pytest.raises(ValueError, match="production"):
        _legacy_job(root, ["lightning", "codex"], profile="production", review=False)


def test_a_lower_quorum_is_refused_for_a_production_job(tmp_path):
    root = make_root(tmp_path)
    with pytest.raises(ValueError, match="production"):
        _legacy_job(root, ["lightning", "codex", "minimax"], profile="production", min_valid_reviews=2)


def test_a_single_reviewer_is_refused_for_a_production_job(tmp_path):
    root = make_root(tmp_path)
    with pytest.raises(ValueError, match="production"):
        _legacy_job(root, ["lightning"], profile="production")


def test_the_cli_refuses_a_non_production_job_without_the_legacy_flag(tmp_path):
    root = make_root(tmp_path)
    _legacy_job(root, ["lightning", "codex"])
    rc = fl.main(["--job", "_loop/jobs/j.json", "--root", str(root), "--solo"])
    assert rc == 2


def test_reviewed_code_must_be_the_tested_code(tmp_path):
    """A reviewer (or anything else) that changes a job file while the review runs voids that review: the code that was tested is not the code that was read."""
    root = make_root(tmp_path)
    job = _legacy_job(root, ["lightning", "codex"])

    def sneaky(files, report):
        (root / "pieces" / "m.py").write_text("X = 999\n", encoding="utf-8")
        report.write_text("## Verdict: READY\n", encoding="utf-8")
        return {"verdict": R}

    result = fl.run_job(job, root, caller=lambda s, u: (_reply(), {"status": 200}), reviewer={"lightning": sneaky, "codex": _reviewer(R)},
                        pytest_runner=lambda *a, **k: (True, "ok"), scrub_keys=[])
    assert result["state"] != "passed"
