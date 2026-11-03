"""R3 (2026-11-03): a FIX FIRST repair that turns the tests red never throws away the code that passed every test: the last green code is put
back and the job ends review-open with the findings (not escalate). Reviewer replies are saved as history/review_NN.txt, as writer replies are,
including a reply with no verdict line. Fakes only: no network, no model call, no Docker."""
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "source"))
sys.path.insert(0, str(REPO / "pieces"))
import nemotron_code as nc  # noqa: E402
import openrouter_call as oc  # noqa: E402
import p0_gates as gates  # noqa: E402
import p1b_production as pm  # noqa: E402
import p1b_routing as p1b  # noqa: E402
import prod_harness as ph  # noqa: E402
from test_p1b_production_run import ACC_ID, ORIG_CODE, ORIG_TEST, FakeClient, FakeSandbox, load, p1b_job, wp_h_root, wp_h_run, wp_h_status  # noqa: E402

NL = "\n"
GREEN_CODE = "def calculate_total(prices):" + NL + "    return sum(prices)" + NL
RED_CODE = "def calculate_total(prices):" + NL + "    return sum(prices) + 1  # BROKEN" + NL
FINDING = "- pieces/calc.py:2 [SPEC 1]: the sum is not rounded, so the total is wrong." + NL + "## Verdict: FIX FIRST"


@pytest.fixture(autouse=True)
def _production_roster(profile):
    profile("production")


def role(name):
    return oc.roster_state()["ROLES"][name]


def production(root, client_, scrub_keys=()):
    """The production models object on a wp_h root, with an (empty) acceptance lock so review calls can run."""
    job = load(root, p1b_job())
    history = root / "_loop" / "calc" / "history"
    history.mkdir(parents=True, exist_ok=True)
    models = pm.Production(job, root, client_, FakeSandbox({}), protected=[], acceptance_lock=gates.hash_paths(root, []), originals={},
                           parse_files=nc.parse_files, check_path=nc.check_path, content_problem=lambda rel, content: None,
                           history=history, job_dir=root / "_loop" / "calc", scrub_keys=scrub_keys)
    return models, history


class ContentSandbox(FakeSandbox):
    """Every expected test passes, except the locked acceptance test while pieces/calc.py holds the BROKEN marker (a negative control still
    fails every test, as the parent does)."""

    def run(self, root, targets, readonly=(), timeout_s=600, overlay=None):
        broken = "BROKEN" in (Path(root) / "pieces" / "calc.py").read_text(encoding="utf-8")
        self.failing = {ACC_ID: "assert 51 == 50"} if broken else {}
        return super().run(root, targets, readonly=readonly, timeout_s=timeout_s, overlay=overlay)


# ---------------------------------------------------------------- 1. a red FIX FIRST repair keeps the green candidate

def red_repair_run(tmp_path):
    root = wp_h_root(tmp_path)
    client = FakeClient([ph.file_reply(("pieces/calc.py", GREEN_CODE), ("pieces/test_calc.py", ORIG_TEST)),   # Dots write: green
                         FINDING,                                                                            # North: FIX FIRST with evidence
                         ph.file_reply(("pieces/calc.py", RED_CODE))])                                       # Dots repair: red
    result = wp_h_run(root, client, ContentSandbox({}))
    return root, client, result


def test_a_fix_first_repair_that_turns_the_tests_red_ends_review_open_not_escalate(tmp_path):
    root, client, result = red_repair_run(tmp_path)
    assert result["state"] == "review-open", result
    assert client.calls == [role("writer_a"), role("reviewer_b"), role("writer_a")]      # the red code is never reviewed
    status = wp_h_status(root)
    assert status["state"] == "review-open" and status["review_verdict"] == "FIX FIRST" and status["tests_passed"] is True
    assert "left the tests red" in status["reason"] and ACC_ID in status["reason"] and "the last green code is kept" in status["reason"]
    assert status["failing_tests"] == []                                                 # the kept code fails no test
    assert status["findings"] == ["pieces/calc.py:2 | SPEC 1: calculate_total(prices) returns the sum of the prices. | "
                                  "the sum is not rounded, so the total is wrong."]
    assert [s["mode"] for s in status["steps"]][-2:] == ["tests", "restore-green"]
    job_dir = root / "_loop" / "calc"
    assert not (job_dir / "ESCALATE.md").exists()


def test_the_kept_candidate_is_the_green_code_and_the_checkout_is_back_to_the_originals(tmp_path):
    root, _, _ = red_repair_run(tmp_path)
    job_dir = root / "_loop" / "calc"
    assert (job_dir / "candidate" / "pieces" / "calc.py").read_text(encoding="utf-8") == GREEN_CODE
    assert (job_dir / "candidate" / "pieces" / "test_calc.py").read_text(encoding="utf-8") == ORIG_TEST
    assert (root / "pieces" / "calc.py").read_text(encoding="utf-8") == ORIG_CODE      # review-open is not passed: the originals go back
    fix = (job_dir / "REVIEW_FIX.md").read_text(encoding="utf-8")
    assert "## Findings" in fix and "pieces/calc.py:2 | SPEC 1" in fix
    lineage = json.loads((job_dir / "lineage.json").read_text(encoding="utf-8"))
    assert lineage["pieces/calc.py"]["chain"] == [role("writer_a")]                     # the green code's lineage, not the undone repair's


def test_the_red_attempt_stays_on_record_in_the_history(tmp_path):
    root, _, _ = red_repair_run(tmp_path)
    history = root / "_loop" / "calc" / "history"
    assert (history / "test_01.txt").is_file() and (history / "test_02.txt").is_file()  # the green run and the red run, both kept
    calls = [json.loads((history / f"call_{n:02d}.json").read_text(encoding="utf-8"))["mode"] for n in (1, 2, 3)]
    assert calls == ["write", "review", "repair"]                                       # the undone repair's call stays on record


def test_restore_green_puts_back_the_files_and_the_reviews_of_the_green_run(tmp_path):
    root = wp_h_root(tmp_path)
    provider = ph.Provider({role("reviewer_b"): [ph.READY]})
    models, _ = production(root, ph.client(tmp_path, provider))
    models.sandbox = ContentSandbox({})
    models.write({"pieces/calc.py": GREEN_CODE})
    assert models.tests(["pieces/calc.py"]).green
    models.call(role("reviewer_b"), "review", ["pieces/calc.py"])
    reviewed = list(models.candidate_reviews)
    models.write({"pieces/calc.py": RED_CODE})
    assert not models.tests(["pieces/calc.py"]).green and models.candidate_reviews == []
    models.restore_green()
    assert (root / "pieces" / "calc.py").read_text(encoding="utf-8") == GREEN_CODE
    assert models.candidate_reviews == reviewed and models.last_junit == models.green_junit
    assert models.steps[-1] == {"model": "checkout", "mode": "restore-green", "note": "the last green code is back on disk"}


def test_restore_green_before_any_green_run_refuses(tmp_path):
    models, _ = production(wp_h_root(tmp_path), FakeClient([]))
    with pytest.raises(AssertionError):
        models.restore_green()


# ---------------------------------------------------------------- 2. reviewer replies are saved as history/review_NN.txt

def test_a_reviewer_reply_is_saved_scrubbed_as_review_NN_and_linked_from_the_call_record(tmp_path):
    root = wp_h_root(tmp_path)
    reply = "Key " + ph.FREE_KEY + " seen." + NL + "## Verdict: READY"
    provider = ph.Provider({role("reviewer_b"): [reply]})
    models, history = production(root, ph.client(tmp_path, provider), scrub_keys=[ph.FREE_KEY])
    models.call(role("reviewer_b"), "review", ["pieces/calc.py"])
    record = json.loads((history / "call_01.json").read_text(encoding="utf-8"))
    assert record["mode"] == "review" and record["reply_file"] == "history/review_01.txt"
    text = (history / "review_01.txt").read_text(encoding="utf-8")
    assert text.endswith("## Verdict: READY") and ph.FREE_KEY not in text and "[REDACTED]" in text
    assert not (history / "reply_01.txt").exists()


def test_a_reviewer_reply_with_no_verdict_line_is_still_saved(tmp_path):
    root = wp_h_root(tmp_path)
    provider = ph.Provider({role("reviewer_b"): ["I looked at the files and they seem mostly fine, but"]})
    models, history = production(root, ph.client(tmp_path, provider))
    with pytest.raises(p1b.ModelFailed):
        models.call(role("reviewer_b"), "review", ["pieces/calc.py"])
    record = json.loads((history / "call_01.json").read_text(encoding="utf-8"))
    assert record["reply_file"] == "history/review_01.txt"
    assert (history / "review_01.txt").read_text(encoding="utf-8") == "I looked at the files and they seem mostly fine, but"


def test_a_writer_reply_keeps_its_reply_NN_name(tmp_path):
    root = wp_h_root(tmp_path)
    provider = ph.Provider({role("writer_a"): [ph.file_reply(("pieces/calc.py", GREEN_CODE))]})
    models, history = production(root, ph.client(tmp_path, provider))
    models.call(role("writer_a"), "write", ["pieces/calc.py"])
    assert json.loads((history / "call_01.json").read_text(encoding="utf-8"))["reply_file"] == "history/reply_01.txt"
