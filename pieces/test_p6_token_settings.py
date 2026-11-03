"""Patch 6 (2026-11-02): token settings, review order, planner budget, Opus switch. Written to FAIL on the code as it stood, then the fix made them
pass. Every test uses fakes (the scripted OpenRouter transport, scripted models, a fake subprocess): no network and no paid call.

Covers: North reviews first and GLM only after North says READY; the GLM request (reasoning effort high and excluded, temperature 0.2,
max_tokens 16000, no reasoning cap, no truncation retry above 16000) and the free requests unchanged; the reviewer prompt (at most 5 one-line
findings, one example, verdict line last); the repair input (failing tests only, identical failures merged, 6,000 characters); the Opus call
(repair-only system prompt, no session persistence, 32000 output tokens, 900 s, 15 minutes needed, switch on only after the probe); the
planner budget."""
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "source"))
sys.path.insert(0, str(REPO / "pieces"))
import openrouter_call as oc  # noqa: E402
import opus_repair as opus  # noqa: E402
import p1b_production as pp  # noqa: E402
import p1b_routing as p1b  # noqa: E402
import plan_job as pj  # noqa: E402
from test_openrouter_call import Fake, client, ok, paid_fake, REVIEW_OK, WRITER_OK  # noqa: E402
import test_p1b_routing as tp  # noqa: E402
from test_p1b_routing import READY, FIX_WITH_EVIDENCE, FIX_NO_EVIDENCE, full, green, red, run  # noqa: E402

DOTS = XING = NORTH = GLM = None      # set per test by the fixture at the end of this file

NL = "\n"


# ---------------------------------------------------------------- 1. review order: North first, GLM only after North says READY

def test_the_panel_is_north_then_glm():
    assert p1b.panel_for(DOTS) == [NORTH, GLM]
    assert p1b.panel_for(XING) == [NORTH, GLM]


def test_when_north_wrote_the_code_the_backup_free_writer_reviews_first_then_glm():
    assert p1b.panel_for(NORTH) == [DOTS, GLM]


def test_when_north_is_down_the_free_writer_that_did_not_write_the_code_takes_its_place_before_glm():
    assert p1b.panel_for(DOTS, unavailable={NORTH}) == [XING, GLM]
    assert p1b.panel_for(XING, unavailable={NORTH}) == [DOTS, GLM]


def test_glm_is_called_after_north_says_ready_and_not_before():
    outcome, models = run({(DOTS, "write"): [full()], (NORTH, "review"): [READY], (GLM, "review"): [READY]}, [green()])
    assert outcome.status == "passed"
    assert [entry for entry in models.log if entry[0] == "review"] == [("review", NORTH), ("review", GLM)]


def test_a_valid_north_fix_first_is_repaired_twice_then_ends_review_open_and_glm_is_never_called():
    outcome, models = run({(DOTS, "write"): [full()], (NORTH, "review"): [FIX_WITH_EVIDENCE] * 3,
                           (DOTS, "repair"): [full("x = 2\n"), full("x = 3\n")]}, [green(), green(), green()])
    assert outcome.status == "review-fix" and NORTH in outcome.reason
    assert models.calls(GLM) == []


def test_an_unsupported_north_fix_first_is_re_asked_once_and_glm_is_never_called():
    outcome, models = run({(DOTS, "write"): [full()], (NORTH, "review"): [FIX_NO_EVIDENCE],
                           (NORTH, "review-reask"): [FIX_NO_EVIDENCE], (GLM, "review"): [READY]}, [green()])
    assert outcome.status == "review-fix"
    assert models.calls(GLM) == []


def test_a_failed_north_is_replaced_by_the_backup_writer_and_glm_comes_after_it():
    outcome, models = run({(DOTS, "write"): [full()], (NORTH, "review"): [p1b.ModelFailed("HTTP 429")], (XING, "review"): [READY],
                           (GLM, "review"): [READY]}, [green()])
    assert outcome.status == "passed"
    assert [entry for entry in models.log if entry[0] == "review"] == [("review", NORTH), ("review", XING), ("review", GLM)]


def test_a_glm_failure_after_north_ready_is_not_a_pass():
    outcome, _ = run({(DOTS, "write"): [full()], (NORTH, "review"): [READY], (GLM, "review"): [p1b.ModelFailed("GLM down")]}, [green()])
    assert outcome.status == "provider-unavailable" and "GLM" in outcome.reason


def test_a_glm_fix_first_after_north_ready_is_repaired_twice_then_review_open_and_names_glm():
    outcome, _ = run({(DOTS, "write"): [full()], (NORTH, "review"): [READY] * 3, (GLM, "review"): [FIX_WITH_EVIDENCE] * 3,
                      (DOTS, "repair"): [full("x = 2\n"), full("x = 3\n")]}, [green(), green(), green()])
    assert outcome.status == "review-fix" and GLM in outcome.reason


# ---------------------------------------------------------------- 2. request settings: GLM as owner set; free requests unchanged

def test_the_glm_request_carries_high_excluded_reasoning_temperature_0_2_and_16000_tokens(tmp_path):
    fake = paid_fake(ok(REVIEW_OK))
    built, _ = client(tmp_path, fake)
    built.call("reviewer_a", "system", "user", kind="reviewer")
    body = fake.posts()[0]["body"]
    assert body["reasoning"] == {"effort": "high", "exclude": True}
    assert "max_tokens" not in body["reasoning"]               # no reasoning token cap for GLM
    assert body["temperature"] == 0.2 and body["max_tokens"] == 16000


def test_free_requests_keep_their_reasoning_cap_and_temperature(tmp_path):
    fake = Fake()
    fake.on("/chat/completions", ok(WRITER_OK), ok(REVIEW_OK))
    built, _ = client(tmp_path, fake)
    built.call("writer_a", "system", "user", kind="writer")
    built.call("reviewer_b", "system", "user", kind="reviewer")
    assert [p["body"]["reasoning"] for p in fake.posts()] == [{"max_tokens": 8000}, {"max_tokens": 8000}]
    assert [p["body"]["temperature"] for p in fake.posts()] == [0.1, 0.1]


def test_a_glm_reply_cut_off_at_16000_is_not_retried_with_more_tokens(tmp_path):
    fake = paid_fake(ok(REVIEW_OK[:10], finish="length"))              # one reply only: a retry would find no reply left and fail
    built, _ = client(tmp_path, fake)
    with pytest.raises(oc.ReplyRejected, match="cut off"):
        built.call("reviewer_a", "system", "user", kind="reviewer")
    assert [p["body"]["max_tokens"] for p in fake.posts()] == [16000]


# ---------------------------------------------------------------- 3. reviewer prompt: at most 5 one-line findings, one example, verdict last

def test_the_reviewer_prompt_asks_for_at_most_five_one_line_findings_with_no_code_and_no_praise():
    text = pp.REVIEW_SYSTEM
    assert "at most 5 findings" in text
    assert "- file:line [SPEC n|CHECK n]: <what breaks, under 25 words>" in text
    assert "each on ONE line" in text
    assert "No code" in text and "No praise" in text


def test_the_reviewer_prompt_has_one_example_line_under_25_words():
    text = pp.REVIEW_SYSTEM
    examples = re.findall(r"- (\S+:\d+ \[(?:SPEC|CHECK) \d+\]: [^`\n]+)", text)
    assert len(examples) == 1
    assert len(examples[0].split(":", 1)[1].split()) < 25


def test_the_reviewer_prompt_ends_with_the_verdict_line_rule():
    last = [line for line in pp.REVIEW_SYSTEM.splitlines() if line.strip()][-1]
    assert "## Verdict: READY" in last and "## Verdict: FIX FIRST" in last


# ---------------------------------------------------------------- 4. repair input: failing tests from JUnit only, merged, 3,000 characters

TB_SUMS = ("def test_sums():" + NL + ">       assert calculate_total([20, 30]) == 50" + NL + "E       assert 20 == 50" + NL
           + NL + "tests/acceptance/test_calc_acceptance.py:9: AssertionError")
TB_OTHER = ("def test_other():" + NL + ">       assert calculate_total([1]) == 3" + NL + "E       assert 1 == 3" + NL
            + NL + "tests/acceptance/test_calc_acceptance.py:14: AssertionError")


def _case(name, text=None, file="tests/acceptance/test_calc_acceptance.py"):
    if text is None:
        return f'<testcase classname="tests.acceptance.test_calc_acceptance" name="{name}" file="{file}"/>'
    return (f'<testcase classname="tests.acceptance.test_calc_acceptance" name="{name}" file="{file}">'
            f'<failure message="AssertionError">{text}</failure></testcase>')


def _junit(*cases):
    return '<testsuites><testsuite name="x">' + "".join(cases) + "</testsuite></testsuites>"


def test_the_repair_input_lists_only_failing_tests_with_their_assertion_line():
    junit = _junit(_case("test_ok"), _case("test_sums", TB_SUMS), _case("test_other", TB_OTHER))
    text = pp.failing_summary(junit)
    assert "test_ok" not in text
    assert "test_sums" in text and "test_other" in text
    assert "E       assert 20 == 50" in text and "E       assert 1 == 3" in text


def test_identical_failures_are_merged_into_one_entry():
    junit = _junit(_case("test_sums_a", TB_SUMS), _case("test_sums_b", TB_SUMS), _case("test_other", TB_OTHER))
    text = pp.failing_summary(junit)
    assert text.count("E       assert 20 == 50") == 1
    assert "test_sums_a" in text and "test_sums_b" in text


def test_the_repair_input_is_capped_at_6000_characters():
    junit = _junit(*[_case(f"test_{n}", TB_OTHER.replace("assert 1 == 3", f"assert {n} == 3")) for n in range(300)])     # 300 distinct failures
    text = pp.failing_summary(junit)
    assert len(text) <= 6000
    assert len(text) > 5000                                       # the cap is what cut it, not an empty result


def test_the_repair_input_has_no_pytest_output_or_passing_tests():
    junit = _junit(_case("test_ok"), _case("test_sums", TB_SUMS))
    text = pp.failing_summary(junit)
    assert "PASSED" not in text and "test_ok" not in text and "=== " not in text


# ---------------------------------------------------------------- 5. Opus call: repair-only system prompt, no session, 32000 tokens, 900 s

class _Done:
    returncode, stdout, stderr = 0, '{"is_error": false, "result": "FILE: a.py"}', ""


def test_the_repair_call_passes_a_repair_only_system_prompt_no_session_persistence_and_the_32000_output_cap(monkeypatch, tmp_path):
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"], seen["env"] = list(cmd), kwargs["env"]
        return _Done()

    monkeypatch.setattr(subprocess, "run", fake_run)
    opus.run_claude("prompt", str(tmp_path), [sys.executable, str(REPO / "pieces" / "fake_claude.py")], 900, opus.isolated_env())
    cmd = seen["cmd"]
    prompt = cmd[cmd.index("--system-prompt") + 1]
    assert "repair" in prompt.lower() and len(prompt) <= 300
    assert "--no-session-persistence" in cmd
    assert seen["env"]["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "32000"


def test_the_repair_call_times_out_at_900_seconds_and_the_gate_needs_the_same_900_seconds():
    assert opus.TIMEOUT_S == 900
    assert p1b.REPAIR_TIMEOUT_S == 900


def test_the_repair_runs_only_with_at_least_fifteen_minutes_left():
    failing = {DOTS: {"t1"}, XING: {"t1"}}
    assert p1b._gate(red(("t1",)), failing, 900) is None
    assert "deadline" in p1b._gate(red(("t1",)), failing, 899)


def test_the_production_switch_stays_off_because_the_check_ran_on_haiku_and_the_owner_decides():
    source = (REPO / "scripts" / "source" / "opus_repair.py").read_text(encoding="utf-8")
    assert "\nOPUS_ENABLED = False" in source


# ---------------------------------------------------------------- 6. planner budget

def test_the_planner_prompt_asks_for_the_owner_budget():
    assert "budget is {free_requests: 40, review_usd: 0.05, paid_usd: 0}" in pj.INSTRUCTIONS
    assert "paid_usd: 0.15" not in pj.INSTRUCTIONS


@pytest.fixture(autouse=True)
def _production_profile(profile):
    """Production roster per test; the role names are read here, and the helpers imported from test_p1b_routing get the same names."""
    global DOTS, XING, NORTH, GLM
    profile("production")
    roles = oc.roster_state()["ROLES"]
    DOTS, XING, NORTH, GLM = roles["writer_a"], roles["writer_b"], roles["reviewer_b"], roles["reviewer_a"]
    tp.DOTS, tp.XING, tp.NORTH, tp.GLM = DOTS, XING, NORTH, GLM
