"""P1b routing (final roster, 2026-11-02): the production ladder (Dots, Xing, then Opus repairs at most twice), the GLM + North panel, the backup
rule, FIX FIRST evidence, the review ceiling outcome, job checks and deadlines, driven by scripted fake models. No network, no model call."""
import json
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "source"))
import openrouter_call as oc  # noqa: E402
import p1b_routing as p1b  # noqa: E402

DOTS = XING = NORTH = GLM = None      # set per test by the profile fixture below (the roster is read per test, never at import)
ANTHROPIC = p1b.ANTHROPIC
JOB = {"files": ["pieces/calc.py", "pieces/test_calc.py"], "deadline_min": 60}
READY = "Looks right.\n## Verdict: READY"
SPEC_LINES = ["Adds two prices.", "Returns an int.", "The total must include tax.", "Empty input returns 0.", "Prices are not negative."]
CHECK_LINES = ["CHECK 1: every spec clause (SPEC n) has code and a test.", "CHECK 2: no code special-cases test values.",
               "CHECK 3: edge cases are handled.", "CHECK 4: nothing changed outside the spec.", "CHECK 5: no security or data-loss risk."]
PACKAGE = {"files": {"pieces/calc.py": 40, "pieces/test_calc.py": 40}, "spec_count": 5, "spec": SPEC_LINES, "checks": CHECK_LINES}
FIX_NO_EVIDENCE = "This is wrong somewhere.\n## Verdict: FIX FIRST"
FIX_WITH_EVIDENCE = "pieces/calc.py:12 breaks SPEC 3.2 (sum must include tax)\n## Verdict: FIX FIRST"
FIX_MIXED = "pieces/calc.py:12 breaks SPEC 3: the sum leaves out tax. Also the tests are weak.\nThe naming feels off to me.\n## Verdict: FIX FIRST"


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class FakeModels:
    """Scripted calls keyed by (model_id, mode). Each queued item is a reply, or an exception to raise (ModelFailed, BadReply, BudgetStop)."""

    def __init__(self, script, tests):
        self.script = {key: list(items) for key, items in script.items()}
        self.tests_results = list(tests)
        self.log, self.on_tests = [], None
        self.package = PACKAGE
        self.fixes = []                 # the review findings staged for each repair call, in order
        self.contract = None            # (test ids, Dots files, Xing files) of the contract check, when one was made

    def call(self, model_id, mode, files):
        self.log.append((mode, model_id))
        queue = self.script.get((model_id, mode))
        if not queue:
            raise AssertionError(f"unscripted call {mode} {model_id}")
        item = queue.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    def tests(self, files):
        if self.on_tests:
            self.on_tests()
        if not self.tests_results:
            raise AssertionError("no test result left")
        return self.tests_results.pop(0)

    def restore(self):
        self.log.append(("restore", None))

    def write(self, reply):
        self.written = getattr(self, "written", []) + [dict(reply)]

    def contract_check(self, test_ids, dots_files, xing_files):
        self.contract = (list(test_ids), dict(dots_files), dict(xing_files))

    def stage_fix(self, findings):
        self.fixes.append(list(findings))

    def calls(self, model_id=None, mode=None):
        return [entry for entry in self.log if (model_id is None or entry[1] == model_id) and (mode is None or entry[0] == mode)]


def green():
    return p1b.Result(True, frozenset(), frozenset(), False)


def red(tests=("t1",), files=("pieces/calc.py",), systemic=False):
    return p1b.Result(False, frozenset(tests), frozenset(files), systemic)


def full(text="x = 1\n"):
    return {"pieces/calc.py": text, "pieces/test_calc.py": "def test(): pass\n"}


def run(script, tests, job=JOB, clock=None):
    models = FakeModels(script, tests)
    clock = clock or Clock()
    outcome = p1b.walk(job, models, p1b.Lineage(), clock)
    return outcome, models


def never_writes_glm(models):
    """GLM never receives a write or a repair call (it is only a reviewer)."""
    assert models.calls(GLM, "write") == [] and models.calls(GLM, "repair") == []


# ---- 1. production job file: role names only, the owner's budget and deadlines

GOOD_JOB = {"name": "calc", "profile": "production", "visibility": "public", "files": ["pieces/calc.py"],
            "fixers": ["writer_a", "writer_a", "writer_b", "writer_b"], "reviewers": "by_lineage",
            "budget": {"free_requests": 40, "review_usd": 0.05, "paid_usd": 0}, "deadline_min": 60}


def test_a_good_production_job_passes():
    p1b.check_production_job(GOOD_JOB)


@pytest.mark.parametrize("budget", [
    {"free_requests": 40, "review_usd": 0.05, "paid_usd": 0.15},      # the old paid ceiling is refused
    {"free_requests": 40, "review_usd": 0.15, "paid_usd": 0},
    {"free_requests": 41, "review_usd": 0.05, "paid_usd": 0},
    {"free_requests": 40, "review_usd": 0.05},                        # a field missing
    {"free_requests": 40, "review_usd": 0.05, "paid_usd": 0, "extra": 1},
    {"free_requests": 40, "review_usd": 0.05, "paid_usd": False},     # False is not 0
    {"free_requests": 40, "review_usd": True, "paid_usd": 0},
])
def test_any_budget_other_than_the_owner_values_is_refused_at_load(budget):
    with pytest.raises(p1b.JobRefused):
        p1b.check_production_job(dict(GOOD_JOB, budget=budget))


@pytest.mark.parametrize("change", [
    {"fixers": ["writer_a", "z-ai/glm-5.3-flash"]}, {"fixers": ["writer_c"]}, {"fixers": ["reviewer_a"]},
    {"reviewers": ["cohere/north-mini-code:free"]}, {"reviewers": "by_model"}, {"visibility": "private"},
    {"deadline_min": 30}, {"deadline_min": 180},
])
def test_job_settings_outside_the_owner_values_are_refused(change):
    with pytest.raises(p1b.JobRefused):
        p1b.check_production_job(dict(GOOD_JOB, **change))


@pytest.mark.parametrize("deadline", [60, 120])
def test_the_two_deadlines_are_allowed(deadline):
    p1b.check_production_job(dict(GOOD_JOB, deadline_min=deadline))


# ---- 2. job size: 1,500 editable lines per unit, 800 per file

def test_a_unit_of_1500_lines_in_files_of_800_or_less_passes():
    p1b.check_job_size({"a.py": 800, "b.py": 700})


def test_a_file_over_800_lines_is_refused():
    with pytest.raises(p1b.JobTooLarge, match="800"):
        p1b.check_job_size({"a.py": 801})


def test_a_unit_over_1500_lines_is_refused():
    with pytest.raises(p1b.JobTooLarge, match="1500"):
        p1b.check_job_size({"a.py": 800, "b.py": 701})


# ---- 3. evidence for FIX FIRST and the verdict line

@pytest.mark.parametrize("text, ok", [
    ("pieces/calc.py:12 breaks SPEC 3.2", True),
    ("pieces/calc.py:12 is wrong", False),
    ("SPEC 3.2 says tax applies", False),
    ("pieces/missing.py:12 breaks SPEC 3", False),
    ("pieces/calc.py:99 breaks SPEC 3", False),
    ("pieces/calc.py:0 breaks SPEC 3", False),
    ("pieces/calc.py:12 breaks SPEC 99", False),
    ("pieces/calc.py:12 breaks CHECK 7", False),
    ("pieces/calc.py:12 breaks CHECK 2", True),
])
def test_fix_first_needs_a_valid_file_line_and_a_valid_spec_or_check(text, ok):
    assert p1b.evidence_ok(text, PACKAGE) is ok


def test_the_file_line_and_the_spec_or_check_must_be_on_the_same_line():
    assert p1b.evidence_ok("pieces/calc.py:12 is wrong\nSPEC 3 says so", PACKAGE) is False


def test_the_verdict_is_the_last_line():
    assert p1b.verdict_of(READY) == "READY" and p1b.verdict_of(FIX_WITH_EVIDENCE) == "FIX FIRST"
    assert p1b.verdict_of("## Verdict: READY\nthanks") is None


def test_pytest_output_fed_back_is_trimmed_to_its_last_32000_characters():
    assert p1b.trim_tail("a" * 40000) == "a" * 32000


# ---- 4. panels: GLM and North, the backup is the free writer that did not write the code

def test_every_panel_is_north_then_glm_unless_north_wrote_or_is_down():
    assert p1b.panel_for(DOTS) == [NORTH, GLM]
    assert p1b.panel_for(XING) == [NORTH, GLM]


def test_north_wrote_the_code_so_the_backup_is_the_free_writer_that_did_not_and_north_is_never_on_the_panel():
    assert p1b.panel_for(NORTH) == [DOTS, GLM]


def test_north_down_means_the_free_writer_that_did_not_write_the_code_reviews_before_glm():
    assert p1b.panel_for(DOTS, unavailable={NORTH}) == [XING, GLM]
    assert p1b.panel_for(XING, unavailable={NORTH}) == [DOTS, GLM]


def test_no_second_independent_reviewer_is_provider_unavailable():
    with pytest.raises(oc.ProviderUnavailable):
        p1b.panel_for(NORTH, unavailable={DOTS, XING})
    with pytest.raises(oc.ProviderUnavailable):
        p1b.panel_for(DOTS, unavailable={NORTH, XING})


@pytest.mark.parametrize("role", ["writer_a", "writer_b", "reviewer_b"])     # the role names; the models are read inside the test
def test_the_code_writer_is_never_on_its_own_panel(role, profile):
    origin = oc.roster_state()["ROLES"][role]
    assert origin not in p1b.panel_for(origin)


def test_glm_is_never_a_writer_origin():
    with pytest.raises(p1b.Blocked):
        p1b.panel_for(GLM)


def test_mixed_origin_is_blocked():
    lineage = p1b.Lineage()
    lineage.set("pieces/calc.py", DOTS, DOTS)
    lineage.set("pieces/test_calc.py", XING, XING)
    with pytest.raises(p1b.Blocked, match="mixed"):
        p1b.panel_for_files(lineage, ["pieces/calc.py", "pieces/test_calc.py"])


# ---- 5. the ladder, scripted

def test_round_zero_green_goes_to_review_with_dots_only_and_the_glm_north_panel():
    outcome, models = run({(DOTS, "write"): [full()], (GLM, "review"): [READY], (NORTH, "review"): [READY]}, [green()])
    assert outcome.status == "passed"
    assert models.calls(mode="write") == [("write", DOTS)]
    never_writes_glm(models)


def test_localized_red_goes_to_one_dots_repair_then_green():
    outcome, models = run({(DOTS, "write"): [full()], (DOTS, "repair"): [full("x = 2\n")],
                           (GLM, "review"): [READY], (NORTH, "review"): [READY]}, [red(), green()])
    assert outcome.status == "passed"
    assert [m for m in models.log if m[0] == "repair"] == [("repair", DOTS)]


def test_systemic_red_skips_dots_repair_and_xing_writes_fresh():
    outcome, models = run({(DOTS, "write"): [full()], (XING, "write"): [full()], (GLM, "review"): [READY], (NORTH, "review"): [READY]},
                          [red(systemic=True), green()])
    assert outcome.status == "passed"
    assert ("repair", DOTS) not in models.log and ("restore", None) in models.log


def test_xing_fresh_starts_from_restored_originals_before_its_write():
    outcome, models = run({(DOTS, "write"): [full()], (DOTS, "repair"): [full("x = 2\n")], (XING, "write"): [full()],
                           (GLM, "review"): [READY], (NORTH, "review"): [READY]}, [red(), red(), green()])
    order = [entry for entry in models.log if entry[0] in ("restore", "write", "repair")]
    assert order == [("write", DOTS), ("repair", DOTS), ("restore", None), ("write", XING)]
    assert outcome.status == "passed"


def test_xing_fresh_reply_missing_a_job_file_escalates_without_testing_it():
    outcome, models = run({(DOTS, "write"): [full()], (DOTS, "repair"): [full()],
                           (XING, "write"): [{"pieces/calc.py": "x = 3\n"}]}, [red(), red()])
    assert outcome.status == "escalate" and "missing" in outcome.reason
    assert ("restore", None) in models.log


def test_xing_repairs_once_after_its_own_red():
    outcome, models = run({(DOTS, "write"): [full()], (DOTS, "repair"): [full()], (XING, "write"): [full()],
                           (XING, "repair"): [full("x = 4\n")], (GLM, "review"): [READY], (NORTH, "review"): [READY]},
                          [red(), red(("t2",)), red(), green()])
    assert outcome.status == "passed"
    assert [m for m in models.log if m[0] == "repair"] == [("repair", DOTS), ("repair", XING)]


def test_dots_down_on_its_first_call_hands_the_round_to_xing_and_dots_does_not_review():
    outcome, models = run({(DOTS, "write"): [p1b.ModelFailed("upstream down")], (XING, "write"): [full()],
                           (GLM, "review"): [READY], (NORTH, "review"): [READY]}, [green()])
    assert outcome.status == "passed"
    assert models.calls(mode="write") == [("write", DOTS), ("write", XING)]
    assert models.calls(DOTS, "review") == []


def test_north_writes_the_fresh_round_only_when_xing_is_down_and_then_is_not_on_the_panel():
    outcome, models = run({(DOTS, "write"): [full()], (XING, "write"): [p1b.ModelFailed("down")], (NORTH, "write"): [full()],
                           (GLM, "review"): [READY], (DOTS, "review"): [READY]}, [red(systemic=True), green()])
    assert outcome.status == "passed"
    assert models.calls(mode="write") == [("write", DOTS), ("write", XING), ("write", NORTH)]
    assert models.calls(NORTH, "review") == []
    assert models.calls(DOTS, "review") == [("review", DOTS)]


# ---- 6. Opus repairs: red, red, red, then Opus green; at most two; behind the gate

def test_red_red_red_then_opus_repairs_to_green_with_the_glm_north_panel_and_lineage():
    outcome, models = run({(DOTS, "write"): [full()], (XING, "write"): [full()], (XING, "repair"): [full("x = 4\n")],
                           (ANTHROPIC, "repair"): [full("x = 5\n")], (GLM, "review"): [READY], (NORTH, "review"): [READY]},
                          [red(systemic=True), red(("t1", "t2")), red(), green()])
    assert outcome.status == "passed", outcome
    assert models.calls(ANTHROPIC, "repair") == [("repair", ANTHROPIC)]
    assert models.calls(GLM, "review") == [("review", GLM)] and models.calls(NORTH, "review") == [("review", NORTH)]
    never_writes_glm(models)


def test_opus_repair_keeps_the_origin_and_adds_anthropic_to_the_lineage():
    lineage = p1b.Lineage()
    models = FakeModels({(DOTS, "write"): [full()], (XING, "write"): [full()], (XING, "repair"): [full("x = 4\n")],
                         (ANTHROPIC, "repair"): [full("x = 5\n")], (GLM, "review"): [READY], (NORTH, "review"): [READY]},
                        [red(systemic=True), red(("t1", "t2")), red(), green()])
    outcome = p1b.walk(JOB, models, lineage, Clock())
    assert outcome.status == "passed"
    row = lineage.get("pieces/calc.py")
    assert row["last"] == ANTHROPIC and row["origin"] == XING
    assert row["chain"][-1] == "anthropic"


def test_opus_is_called_at_most_twice_and_a_third_repair_is_never_attempted():
    outcome, models = run({(DOTS, "write"): [full()], (XING, "write"): [full()], (XING, "repair"): [full()],
                           (ANTHROPIC, "repair"): [full(), full()]}, [red(systemic=True), red(("t1", "t2")), red(), red(("t1", "t2")), red()])
    assert outcome.status == "escalate" and "repair calls used up" in outcome.reason
    assert len(models.calls(ANTHROPIC, "repair")) == 2


def test_an_opus_failure_escalates_with_the_fixed_reason_and_no_other_model_takes_over():
    outcome, models = run({(DOTS, "write"): [full()], (XING, "write"): [full()], (XING, "repair"): [full()],
                           (ANTHROPIC, "repair"): [p1b.ModelFailed("usage limit reached", reason="Opus repair unavailable")]}, [red(systemic=True), red(("t1", "t2")), red()])
    assert outcome.status == "escalate" and outcome.reason == "Opus repair unavailable"
    assert models.calls(GLM, "review") == [] and models.calls(mode="write") == [("write", DOTS), ("write", XING)]


def test_a_repair_failure_without_a_named_reason_escalates_with_the_generic_reason():
    outcome, _ = run({(DOTS, "write"): [full()], (XING, "write"): [full()], (XING, "repair"): [full()],
                      (ANTHROPIC, "repair"): [p1b.ModelFailed("usage limit reached")]}, [red(systemic=True), red(("t1", "t2")), red()])
    assert outcome.status == "escalate" and outcome.reason == "repair unavailable"


def test_an_opus_reply_with_no_files_escalates_as_a_bad_reply():
    outcome, _ = run({(DOTS, "write"): [full()], (XING, "write"): [full()], (XING, "repair"): [full()],
                      (ANTHROPIC, "repair"): [p1b.BadReply("the reply has no FILE blocks")]}, [red(systemic=True), red(("t1", "t2")), red()])
    assert outcome.status == "escalate" and "no FILE blocks" in outcome.reason


def test_the_opus_gate_needs_a_confirmed_defect_both_free_writers_failing_on_the_same_test_and_a_deadline_that_fits():
    # Dots and Xing fail on different tests: no Opus call
    outcome, models = run({(DOTS, "write"): [full()], (XING, "write"): [full()], (XING, "repair"): [full()]},
                          [red(("t1",), systemic=True), red(("t2",)), red(("t2",))])
    assert outcome.status == "escalate" and "repair gate" in outcome.reason
    assert models.calls(ANTHROPIC) == []


def test_the_opus_gate_refuses_a_call_the_deadline_cannot_hold():
    clock = Clock()
    models = FakeModels({(DOTS, "write"): [full()], (XING, "write"): [full()], (XING, "repair"): [full()]}, [red(systemic=True), red(("t1", "t2")), red()])
    models.on_tests = lambda: setattr(clock, "now", 2800.0) if len(models.tests_results) == 1 else None      # 800 s left: under the 900 s repair call
    outcome = p1b.walk(JOB, models, p1b.Lineage(), clock)
    assert outcome.status == "escalate" and "deadline" in outcome.reason
    assert models.calls(ANTHROPIC) == []


def test_the_walk_stops_between_rounds_at_the_deadline():
    models = FakeModels({(DOTS, "write"): [full()]}, [red()])
    clock = Clock()
    models.on_tests = lambda: setattr(clock, "now", 3700.0)
    outcome = p1b.walk(JOB, models, p1b.Lineage(), clock)
    assert outcome.status == "deadline"
    assert models.calls(mode="repair") == []


# ---- 7. review: GLM first, North second, one backup, a GLM READY always required

def test_a_valid_fix_first_is_repaired_by_the_current_writer_then_tested_and_reviewed_again_and_passes():
    outcome, models = run({(DOTS, "write"): [full()], (NORTH, "review"): [FIX_WITH_EVIDENCE, READY], (DOTS, "repair"): [full("x = 2\n")],
                           (GLM, "review"): [READY]}, [green(), green()])
    assert outcome.status == "passed", outcome
    assert models.calls(DOTS, "repair") == [("repair", DOTS)]
    assert models.calls(NORTH, "review") == [("review", NORTH), ("review", NORTH)]     # the panel is recomputed and North reviews the repaired code
    assert models.calls(GLM, "review") == [("review", GLM)]
    assert len(models.fixes) == 1
    assert models.fixes[0] == ["pieces/calc.py:12 | SPEC 3: The total must include tax. | breaks (sum must include tax)"]
    never_writes_glm(models)


def test_a_finding_with_no_evidence_never_reaches_the_writer():
    outcome, models = run({(DOTS, "write"): [full()], (NORTH, "review"): [FIX_MIXED, READY], (DOTS, "repair"): [full("x = 2\n")],
                           (GLM, "review"): [READY]}, [green(), green()])
    assert outcome.status == "passed", outcome
    assert len(models.fixes) == 1 and len(models.fixes[0]) == 1               # the valid finding only
    note = "\n".join(models.fixes[0])
    assert "pieces/calc.py:12" in note and "the sum leaves out tax." in note
    assert "naming feels off" not in note and "tests are weak" not in note    # no reviewer opinion, no second sentence, no evidence-free line


def test_a_fix_first_with_no_valid_evidence_is_never_sent_to_the_writer_and_stays_open():
    outcome, models = run({(DOTS, "write"): [full()], (NORTH, "review"): [FIX_NO_EVIDENCE], (NORTH, "review-reask"): [FIX_NO_EVIDENCE]},
                          [green()])
    assert outcome.status == "review-fix" and "without valid evidence" in outcome.reason
    assert models.fixes == [] and models.calls(DOTS, "repair") == []


def test_a_third_fix_first_after_two_repair_rounds_ends_review_open_without_another_repair():
    outcome, models = run({(DOTS, "write"): [full()], (NORTH, "review"): [FIX_WITH_EVIDENCE] * 3,
                           (DOTS, "repair"): [full("x = 2\n"), full("x = 3\n")]}, [green(), green(), green()])
    assert outcome.status == "review-fix" and NORTH in outcome.reason and "rounds used up" in outcome.reason
    assert len(models.fixes) == 2 and len(models.calls(DOTS, "repair")) == 2
    assert models.calls(GLM) == []


def test_a_repair_that_leaves_the_tests_red_escalates_and_is_not_reviewed_again():
    outcome, models = run({(DOTS, "write"): [full()], (NORTH, "review"): [FIX_WITH_EVIDENCE], (DOTS, "repair"): [full("x = 2\n")]},
                          [green(), red()])
    assert outcome.status == "escalate" and "FIX FIRST repair round 1" in outcome.reason
    assert models.calls(NORTH, "review") == [("review", NORTH)]


def test_an_unsupported_fix_first_is_re_asked_once_and_stays_open_and_glm_is_never_asked():
    outcome, models = run({(DOTS, "write"): [full()], (NORTH, "review"): [FIX_NO_EVIDENCE], (NORTH, "review-reask"): [FIX_NO_EVIDENCE],
                           (GLM, "review"): [READY]}, [green()])
    assert outcome.status == "review-fix"
    assert len(models.calls(NORTH, "review-reask")) == 1
    assert models.calls(GLM, "review") == []


def test_a_failed_north_call_goes_to_the_free_writer_that_did_not_write_the_code():
    outcome, models = run({(DOTS, "write"): [full()], (GLM, "review"): [READY], (NORTH, "review"): [p1b.ModelFailed("HTTP 429 shared pool")],
                           (XING, "review"): [READY]}, [green()])
    assert outcome.status == "passed" and f"{XING} (backup for {NORTH})" in outcome.reason


def test_north_failing_with_its_backup_down_is_provider_unavailable_and_keeps_the_error():
    outcome, _ = run({(DOTS, "write"): [p1b.ModelFailed("down")], (XING, "write"): [full()], (GLM, "review"): [READY],
                      (NORTH, "review"): [p1b.ModelFailed("HTTP 429 shared pool")], (DOTS, "review"): [p1b.ModelFailed("down again")]},
                     [green()])
    assert outcome.status == "provider-unavailable" and "HTTP 429 shared pool" in outcome.reason


def test_without_glm_ready_there_is_no_pass_even_when_north_says_ready():
    outcome, models = run({(DOTS, "write"): [full()], (GLM, "review"): [p1b.ModelFailed("GLM down")], (NORTH, "review"): [READY]}, [green()])
    assert outcome.status == "provider-unavailable" and "GLM" in outcome.reason
    assert models.calls(NORTH, "review") == [("review", NORTH)]


def test_a_glm_reply_with_no_verdict_line_is_not_a_pass():
    outcome, _ = run({(DOTS, "write"): [full()], (GLM, "review"): ["looks fine but no verdict"], (NORTH, "review"): [READY]}, [green()])
    assert outcome.status == "provider-unavailable"


def test_a_glm_call_over_the_review_ceiling_ends_the_job_as_budget_exceeded_and_nothing_else_is_called():
    outcome, models = run({(DOTS, "write"): [full()], (NORTH, "review"): [READY], (GLM, "review"): [p1b.BudgetStop("review ceiling")]},
                          [green()])
    assert outcome.status == "budget-exceeded"
    assert models.calls(GLM, "review") == [("review", GLM)] and models.log[-1] == ("review", GLM)


def test_a_glm_re_ask_over_the_ceiling_also_ends_the_job_as_budget_exceeded():
    outcome, _ = run({(DOTS, "write"): [full()], (NORTH, "review"): [READY], (GLM, "review"): [FIX_NO_EVIDENCE],
                      (GLM, "review-reask"): [p1b.BudgetStop("ceiling")]}, [green()])
    assert outcome.status == "budget-exceeded"


# ---- 8. nothing in the P1b routing names a model family other than the roster

def test_the_routing_module_names_no_legacy_model_family_and_no_opus():
    source = (REPO / "scripts" / "source" / "p1b_routing.py").read_text(encoding="utf-8").lower()
    for word in ("codex", "haiku", "nvidia", "sonnet", "nemotron", "groq", "opus"):
        assert word not in source, word


# ---- 9. a failed reviewer keeps its upstream error in the job's reason

def test_a_failed_glm_keeps_its_upstream_error_in_the_outcome_reason():
    outcome, _ = run({(DOTS, "write"): [full()], (NORTH, "review"): [READY], (GLM, "review"): [p1b.ModelFailed("HTTP 429 glm pool")]},
                     [green()])
    assert outcome.status == "provider-unavailable"
    assert "HTTP 429 glm pool" in outcome.reason


# ---- 10. the lineage file carries schema_version 1 (2026-11-02)

def test_the_lineage_file_carries_schema_version_1_and_loads_back(tmp_path):
    lineage = p1b.Lineage()
    lineage.set("pieces/calc.py", DOTS, DOTS, [DOTS])
    path = tmp_path / "lineage.json"
    lineage.save(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["schema_version"] == 1
    assert data["pieces/calc.py"] == {"last": DOTS, "origin": DOTS, "chain": [DOTS]}
    assert p1b.Lineage.load(path).get("pieces/calc.py") == {"last": DOTS, "origin": DOTS, "chain": [DOTS]}


def test_a_lineage_file_with_another_schema_version_is_refused(tmp_path):
    path = tmp_path / "lineage.json"
    path.write_text(json.dumps({"schema_version": 2}), encoding="utf-8")
    with pytest.raises(ValueError, match="schema_version"):
        p1b.Lineage.load(path)


def test_a_lineage_file_written_before_the_version_still_loads(tmp_path):
    path = tmp_path / "lineage.json"
    path.write_text(json.dumps({"pieces/calc.py": {"last": DOTS, "origin": DOTS, "chain": [DOTS]}}), encoding="utf-8")
    assert p1b.Lineage.load(path).get("pieces/calc.py")["origin"] == DOTS


@pytest.fixture(autouse=True)
def _production_profile(profile):
    """Every test in this file runs on the production roster (the paid GLM reviewer lives there). Set per test, never at import: the role
    names below are read from the roster here, after the profile is set."""
    global DOTS, XING, NORTH, GLM
    profile("production")
    roles = oc.roster_state()["ROLES"]
    DOTS, XING, NORTH, GLM = roles["writer_a"], roles["writer_b"], roles["reviewer_b"], roles["reviewer_a"]


# ---- 7. needs_review, quota-exhausted, CONTRACT_CHECK (WP-H)

def test_the_same_failing_set_in_three_writer_runs_in_a_row_ends_needs_review_once_no_repair_is_left():
    outcome, models = run({(DOTS, "write"): [full()], (DOTS, "repair"): [full()], (XING, "write"): [full()], (XING, "repair"): [full()],
                           (ANTHROPIC, "repair"): [full(), full()]}, [red(("t1",))] * 6)
    assert outcome.status == "needs_review", outcome
    assert outcome.failing == ("t1",) and "t1" in outcome.reason
    assert len(models.calls(ANTHROPIC, "repair")) == 2
    assert models.calls(GLM) == [] and models.calls(NORTH) == []


def test_a_failing_set_that_changes_between_runs_is_not_needs_review():
    outcome, _ = run({(DOTS, "write"): [full()], (DOTS, "repair"): [full()], (XING, "write"): [full()], (XING, "repair"): [full()],
                      (ANTHROPIC, "repair"): [full()], (GLM, "review"): [READY], (NORTH, "review"): [READY]},
                     [red(("t1",)), red(("t2",)), red(("t1",)), red(("t1", "t2"), systemic=True), green()])
    assert outcome.status == "passed", outcome


def test_quota_exhausted_on_a_writer_call_ends_the_job_and_makes_no_further_call():
    models = FakeModels({(DOTS, "write"): [p1b.QuotaStop("free requests for today are used up")]}, [])
    outcome = p1b.walk(JOB, models, p1b.Lineage(), Clock())
    assert outcome.status == "quota-exhausted" and "used up" in outcome.reason
    assert models.log == [("write", DOTS)] and models.tests_results == []


def test_quota_exhausted_on_a_review_call_ends_the_job_and_GLM_is_not_asked():
    outcome, models = run({(DOTS, "write"): [full()], (NORTH, "review"): [p1b.QuotaStop("free requests used up")]}, [green()])
    assert outcome.status == "quota-exhausted"
    assert models.calls(GLM, "review") == []


ACC = "tests/acceptance/test_calc_acceptance.py::test_sums_two_prices"
JOB_ACC = dict(JOB, acceptance_tests=["tests/acceptance/test_calc_acceptance.py"])


def test_dots_and_xing_failing_one_locked_acceptance_test_with_different_code_write_the_contract_file_and_the_ladder_goes_on():
    outcome, models = run({(DOTS, "write"): [full("x = 1\n")], (XING, "write"): [full("x = 2\n")], (XING, "repair"): [full("x = 3\n")],
                           (ANTHROPIC, "repair"): [p1b.ModelFailed("usage limit reached", reason="Opus repair unavailable")]},
                          [red((ACC,), systemic=True), red((ACC,)), red((ACC,))], job=JOB_ACC)
    assert outcome.status == "escalate" and outcome.reason == "Opus repair unavailable", outcome
    ids, dots, xing = models.contract
    assert ids == [ACC] and dots["pieces/calc.py"] == "x = 1\n" and xing["pieces/calc.py"] == "x = 3\n"
    assert models.calls(XING, "repair") != []


def test_the_same_locked_failure_with_the_same_code_writes_no_contract_file():
    outcome, models = run({(DOTS, "write"): [full("x = 1\n")], (XING, "write"): [full("x = 1\n")], (XING, "repair"): [full("x = 1\n")],
                           (ANTHROPIC, "repair"): [p1b.ModelFailed("usage limit reached", reason="Opus repair unavailable")]},
                          [red((ACC,), systemic=True), red((ACC,)), red((ACC,))], job=JOB_ACC)
    assert models.contract is None
    assert outcome.reason == "Opus repair unavailable"