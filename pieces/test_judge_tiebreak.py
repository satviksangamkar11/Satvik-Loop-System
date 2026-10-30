"""Owner rule 2026-10-31: when the reviewers of a pass disagree, a Haiku 5.5 tie-breaker decides (fix or move ahead).
Fakes only: no model call, the tie-breaker is injected as `callers["judge"]`."""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "source"))
import fix_loop as fl  # noqa: E402
from test_fix_loop_review import FENCE, FILE_MARK, make_root  # noqa: E402


def make_job(root, reviewers):
    job = {"name": "j", "spec": "specs/s.md", "files": ["pieces/m.py", "pieces/test_m.py"], "tests": ["pieces/test_m.py"], "reference": [],
           "max_rounds": 1, "review": True, "reviewers": reviewers, "review_prompt": "docs/RP.md", "fixers": ["nemotron"] * 3}
    (root / "_loop" / "jobs").mkdir()
    (root / "_loop" / "jobs" / "j.json").write_text(json.dumps(job), encoding="utf-8")
    return fl.load_job(root / "_loop" / "jobs" / "j.json")


def reply():
    return (FILE_MARK + "pieces/m.py\n" + FENCE + "python\nX = 2\n" + FENCE + "\n"
            + FILE_MARK + "pieces/test_m.py\n" + FENCE + "python\ndef test_x():\n    assert True\n" + FENCE + "\n")


def reviewer(verdict, text):
    def run(files, report):
        report.write_text(text, encoding="utf-8")
        return {"verdict": verdict}
    return run


def run(tmp_path, panel, judge_reply=None):
    root = make_root(tmp_path)
    loaded = make_job(root, list(panel))
    judged = []

    def judge(system, user):
        judged.append((system, user))
        if judge_reply is None:
            return ("", {"status": 200})
        return (judge_reply, {"status": 200})

    result = fl.run_job(loaded, root, caller=lambda s_, u: (reply(), {"status": 200, "finish": "stop"}),
                        reviewer=panel, pytest_runner=lambda *a, **k: (True, "ok"), scrub_keys=[], callers={"judge": judge})
    return result, judged


def mixed_panel():
    return {"lightning": reviewer("READY", "## Verdict: READY\n"),
            "codex": reviewer("FIX FIRST", "## Verdict: FIX FIRST\n- pieces/m.py:1 - defect - fix\n")}


def test_unanimous_panel_does_not_call_the_tiebreaker(tmp_path):
    panel = {"lightning": reviewer("READY", "## Verdict: READY\n"), "codex": reviewer("READY", "## Verdict: READY\n")}
    result, judged = run(tmp_path, panel)
    assert result["state"] == "passed"
    assert judged == []


def test_mixed_panel_is_decided_by_the_tiebreaker_ready_moves_ahead(tmp_path):
    result, judged = run(tmp_path, mixed_panel(), judge_reply="Checked: the finding is a false positive.\n## Verdict: READY\n")
    assert len(judged) >= 1
    assert result["state"] == "passed"
    judge_rounds = [r for r in result["rounds"] if r.get("fixer") == "judge"]
    assert judge_rounds and "judge verdict=READY" in judge_rounds[0]["reason"]


def test_mixed_panel_tiebreaker_fix_first_blocks_acceptance(tmp_path):
    result, judged = run(tmp_path, mixed_panel(), judge_reply="pieces/m.py:1 - real defect - fix it\n## Verdict: FIX FIRST\n")
    assert len(judged) >= 1
    assert result["state"] != "passed"
    assert any(r.get("fixer") == "judge" and "judge verdict=FIX FIRST" in r["reason"] for r in result["rounds"])


def test_a_tiebreaker_reply_without_a_verdict_never_passes(tmp_path):
    result, judged = run(tmp_path, mixed_panel(), judge_reply="I am not sure.")
    assert len(judged) >= 1
    assert result["state"] != "passed"


def test_a_failed_tiebreaker_call_never_passes(tmp_path):
    root = make_root(tmp_path)
    loaded = make_job(root, ["lightning", "codex"])

    def broken(system, user):
        raise RuntimeError("claude CLI unavailable")

    result = fl.run_job(loaded, root, caller=lambda s_, u: (reply(), {"status": 200, "finish": "stop"}),
                        reviewer=mixed_panel(), pytest_runner=lambda *a, **k: (True, "ok"), scrub_keys=[], callers={"judge": broken})
    assert result["state"] != "passed"
    assert any(r.get("fixer") == "judge" and "judge verdict=NONE" in r["reason"] for r in result["rounds"])
