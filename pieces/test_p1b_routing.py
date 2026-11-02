"""P1b routing (2026-11-02): the production ladder, lineage, reviewer panels, FIX FIRST evidence, the GLM gate, job checks and deadlines,
driven by scripted fake models. No network, no model call. The owner's roster IDs are read from the adapter, so a wrong roster fails here."""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "source"))
import openrouter_call as oc  # noqa: E402
import p1b_routing as p1b  # noqa: E402

DOTS, XING, NORTH, GLM = oc.ROLES["writer_a"], oc.ROLES["writer_b"], oc.ROLES["reviewer"], oc.ROLES["repair"]
GEMMA31, GEMMA26 = oc.REVIEWER_BACKUP
JOB = {"files": ["pieces/calc.py", "pieces/test_calc.py"], "deadline_min": 60}
READY = "Looks right.\n## Verdict: READY"
PACKAGE = {"files": {"pieces/calc.py": 40, "pieces/test_calc.py": 40}, "spec_count": 5}
FIX_NO_EVIDENCE = "This is wrong somewhere.\n## Verdict: FIX FIRST"
FIX_WITH_EVIDENCE = "pieces/calc.py:12 breaks SPEC 3.2 (sum must include tax)\n## Verdict: FIX FIRST"


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class FakeModels:
    """Scripted calls keyed by (model_id, mode). Each queued item is a reply dict/str, or an exception to raise (ModelFailed)."""

    def __init__(self, script, tests, paid_ok=True):
        self.script = {key: list(items) for key, items in script.items()}
        self.tests_results = list(tests)
        self.paid_ok = paid_ok
        self.log, self.on_tests = [], None
        self.package = PACKAGE                  # what the production runner sets on each review call: the files shown and the spec count

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

    def paid_fits(self):
        return self.paid_ok

    def calls(self, model_id=None, mode=None):
        return [entry for entry in self.log if (model_id is None or entry[1] == model_id) and (mode is None or entry[0] == mode)]


def green():
    return p1b.Result(True, frozenset(), frozenset(), False)


def red(tests=("t1",), files=("pieces/calc.py",), systemic=False):
    return p1b.Result(False, frozenset(tests), frozenset(files), systemic)


def full(text="x = 1\n"):
    return {"pieces/calc.py": text, "pieces/test_calc.py": "def test(): pass\n"}


def run(script, tests, paid_ok=True, enable_gemma=False, monkeypatch=None):
    if enable_gemma and monkeypatch is not None:
        monkeypatch.setattr(oc, "REVIEWER_BACKUP_ENABLED", True)
    models = FakeModels(script, tests, paid_ok)
    clock = Clock()
    outcome = p1b.walk(JOB, models, p1b.Lineage(), clock)
    return outcome, models


# ---- 1. production job file: role names only, owner settings

GOOD_JOB = {"name": "calc", "profile": "production", "visibility": "public", "files": ["pieces/calc.py"],
            "fixers": ["writer_a", "writer_a", "writer_b", "writer_b"], "reviewers": "by_lineage",
            "budget": {"free_requests": 40, "paid_usd": 0.15}, "deadline_min": 60}


def test_a_good_production_job_passes():
    p1b.check_production_job(GOOD_JOB)


@pytest.mark.parametrize("change", [
    {"fixers": ["writer_a", "z-ai/glm-5.3-flash"]}, {"fixers": ["writer_c"]}, {"reviewers": ["cohere/north-mini-code:free"]},
    {"reviewers": "by_model"}, {"visibility": "private"}, {"budget": {"free_requests": 41, "paid_usd": 0.15}},
    {"budget": {"free_requests": 40, "paid_usd": 0.10}}, {"deadline_min": 30}, {"deadline_min": 180},
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
    ("pieces/missing.py:12 breaks SPEC 3", False),      # a file that is not in the package
    ("pieces/calc.py:99 breaks SPEC 3", False),         # a line past the end of the file (40 lines)
    ("pieces/calc.py:0 breaks SPEC 3", False),
    ("pieces/calc.py:12 breaks SPEC 99", False),        # a spec number past the requirement count (5)
    ("pieces/calc.py:12 breaks CHECK 7", False),        # only CHECK 1 to 5 exist
    ("pieces/calc.py:12 breaks CHECK 2", True),
])
def test_fix_first_needs_a_valid_file_line_and_a_valid_spec_or_check(text, ok):
    assert p1b.evidence_ok(text, PACKAGE) is ok


def test_the_verdict_is_the_last_line():
    assert p1b.verdict_of(READY) == "READY" and p1b.verdict_of(FIX_WITH_EVIDENCE) == "FIX FIRST"
    assert p1b.verdict_of("## Verdict: READY\nthanks") is None


def test_pytest_output_fed_back_is_trimmed_to_its_last_32000_characters():
    text = "a" * 40000
    assert p1b.trim_tail(text) == "a" * 32000


# ---- 4. reviewer panels by lineage (identity, never role names)

@pytest.mark.parametrize("author, origin, panel", [
    (DOTS, None, [XING, NORTH]),
    (XING, None, [DOTS, NORTH]),
    (GLM, DOTS, [XING, NORTH]),
    (GLM, XING, [DOTS, NORTH]),
])
def test_the_panel_follows_the_lineage_table(author, origin, panel):
    assert p1b.panel_for(author, origin=origin) == panel


def test_a_model_is_never_on_its_own_panel(monkeypatch):
    monkeypatch.setattr(oc, "REVIEWER_BACKUP_ENABLED", True)
    for author in (DOTS, XING, NORTH, GLM):
        assert author not in p1b.panel_for(author, origin=DOTS if author == GLM else None)


def test_north_as_writer_gets_dots_and_gemma_only_when_gemma_is_enabled(monkeypatch):
    with pytest.raises(oc.ProviderUnavailable, match="Gemma"):
        p1b.panel_for(NORTH)
    monkeypatch.setattr(oc, "REVIEWER_BACKUP_ENABLED", True)
    assert p1b.panel_for(NORTH) == [DOTS, GEMMA31]


def test_a_down_reviewer_is_replaced_by_gemma_when_enabled(monkeypatch):
    with pytest.raises(oc.ProviderUnavailable):
        p1b.panel_for(DOTS, unavailable={NORTH})
    monkeypatch.setattr(oc, "REVIEWER_BACKUP_ENABLED", True)
    assert p1b.panel_for(DOTS, unavailable={NORTH}) == [XING, GEMMA31]


def test_dots_down_means_xing_writes_and_north_plus_gemma_review(monkeypatch):
    monkeypatch.setattr(oc, "REVIEWER_BACKUP_ENABLED", True)
    assert p1b.panel_for(XING, unavailable={DOTS}) == [NORTH, GEMMA31]


def test_mixed_lineage_is_blocked():
    lineage = p1b.Lineage()
    lineage.set("pieces/calc.py", DOTS, DOTS)
    lineage.set("pieces/test_calc.py", XING, XING)
    with pytest.raises(p1b.Blocked, match="mixed"):
        p1b.panel_for_files(lineage, ["pieces/calc.py", "pieces/test_calc.py"])


# ---- 5. the ladder, scripted

def test_round_zero_green_goes_to_review_with_dots_only():
    outcome, models = run({(DOTS, "write"): [full()], (XING, "review"): [READY], (NORTH, "review"): [READY]}, [green()])
    assert outcome.status == "passed"
    assert models.calls(mode="write") == [("write", DOTS)]


def test_localized_red_goes_to_one_dots_repair_then_green():
    outcome, models = run({(DOTS, "write"): [full()], (DOTS, "repair"): [full("x = 2\n")],
                           (XING, "review"): [READY], (NORTH, "review"): [READY]}, [red(), green()])
    assert outcome.status == "passed"
    assert [m for m in models.log if m[0] == "repair"] == [("repair", DOTS)]


def test_systemic_red_skips_dots_repair_and_xing_writes_fresh(monkeypatch):
    outcome, models = run({(DOTS, "write"): [full()], (XING, "write"): [full()], (DOTS, "review"): [READY], (NORTH, "review"): [READY]},
                          [red(systemic=True), green()])
    assert outcome.status == "passed"
    assert ("repair", DOTS) not in models.log and ("restore", None) in models.log


def test_xing_fresh_starts_from_restored_originals_before_its_write():
    outcome, models = run({(DOTS, "write"): [full()], (DOTS, "repair"): [full("x = 2\n")], (XING, "write"): [full()],
                           (DOTS, "review"): [READY], (NORTH, "review"): [READY]}, [red(), red(), green()])
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
                           (XING, "repair"): [full("x = 4\n")], (DOTS, "review"): [READY], (NORTH, "review"): [READY]},
                          [red(), red(), red(), green()])
    assert outcome.status == "passed"
    assert [m for m in models.log if m[0] == "repair"] == [("repair", DOTS), ("repair", XING)]


def test_writer_down_on_its_first_call_hands_the_round_to_xing_and_dots_does_not_review(monkeypatch):
    outcome, models = run({(DOTS, "write"): [p1b.ModelFailed("upstream down")], (XING, "write"): [full()],
                           (NORTH, "review"): [READY], (GEMMA31, "review"): [READY]}, [green()],
                          enable_gemma=True, monkeypatch=monkeypatch)
    assert outcome.status == "passed"
    assert models.calls(mode="write") == [("write", DOTS), ("write", XING)]
    assert models.calls(DOTS, "review") == []


def test_glm_gate_blocks_when_the_free_writers_failed_on_different_tests():
    outcome, models = run({(DOTS, "write"): [full()], (DOTS, "repair"): [full()], (XING, "write"): [full()],
                           (XING, "repair"): [full()]}, [red(("t1",)), red(("t1",)), red(("t2",)), red(("t2",))])
    assert outcome.status == "escalate" and "gate" in outcome.reason
    assert models.calls(GLM) == []


def test_glm_gate_blocks_when_the_budget_does_not_fit():
    outcome, models = run({(DOTS, "write"): [full()], (DOTS, "repair"): [full()], (XING, "write"): [full()],
                           (XING, "repair"): [full()]}, [red(), red(), red(), red()], paid_ok=False)
    assert outcome.status == "escalate" and "budget" in outcome.reason
    assert models.calls(GLM) == []


def test_glm_repairs_once_when_it_is_the_first_round_that_goes_green():
    outcome, models = run({(DOTS, "write"): [full()], (DOTS, "repair"): [full()], (XING, "write"): [full()],
                           (XING, "repair"): [full()], (GLM, "repair"): [full()],
                           (DOTS, "review"): [READY], (NORTH, "review"): [READY]}, [red(), red(), red(), red(), green()])
    assert outcome.status == "passed"
    assert len(models.calls(GLM, "repair")) == 1


def test_glm_round_two_can_pass_and_both_rounds_count():
    outcome, models = run({(DOTS, "write"): [full()], (DOTS, "repair"): [full()], (XING, "write"): [full()],
                           (XING, "repair"): [full()], (GLM, "repair"): [full(), full()],
                           (DOTS, "review"): [READY], (NORTH, "review"): [READY]}, [red(), red(), red(), red(), red(), green()])
    assert outcome.status == "passed"
    assert len(models.calls(GLM, "repair")) == 2


def test_two_glm_rounds_then_escalate_with_no_third_call():
    outcome, models = run({(DOTS, "write"): [full()], (DOTS, "repair"): [full()], (XING, "write"): [full()],
                           (XING, "repair"): [full()], (GLM, "repair"): [full(), full()]}, [red()] * 6)
    assert outcome.status == "escalate" and "GLM rounds" in outcome.reason
    assert len(models.calls(GLM, "repair")) == 2


def test_the_deadline_stops_the_walk_between_rounds():
    models = FakeModels({(DOTS, "write"): [full()]}, [red()])
    clock = Clock()
    models.on_tests = lambda: setattr(clock, "now", 3700.0)
    outcome = p1b.walk(JOB, models, p1b.Lineage(), clock)
    assert outcome.status == "deadline"
    assert models.calls(mode="repair") == []


# ---- 6. review verdicts: FIX FIRST evidence, re-ask, backup

def test_fix_first_with_evidence_comes_back_as_a_review_fix(monkeypatch):
    outcome, _ = run({(DOTS, "write"): [full()], (XING, "review"): [FIX_WITH_EVIDENCE], (NORTH, "review"): [READY]}, [green()])
    assert outcome.status == "review-fix" and "SPEC 3.2" in outcome.reason


def test_fix_first_without_evidence_is_asked_once_more_and_stays_open_and_no_backup_overrides_it(monkeypatch):
    # 9. unsupported FIX FIRST, re-ask unsupported, a backup READY is queued: the job stays open, the backup is never asked
    outcome, models = run({(DOTS, "write"): [full()], (XING, "review"): [FIX_NO_EVIDENCE], (XING, "review-reask"): [FIX_NO_EVIDENCE],
                           (GEMMA31, "review"): [READY], (NORTH, "review"): [READY]}, [green()], enable_gemma=True, monkeypatch=monkeypatch)
    assert outcome.status == "review-fix"
    assert len(models.calls(XING, "review-reask")) == 1
    assert models.calls(GEMMA31, "review") == []


def test_fix_first_without_evidence_then_ready_on_the_re_ask_stays_open():
    # 10. unsupported FIX FIRST, the re-ask answers READY: still open (a later READY never overrides the finding)
    outcome, models = run({(DOTS, "write"): [full()], (XING, "review"): [FIX_NO_EVIDENCE], (XING, "review-reask"): [READY], (NORTH, "review"): [READY]},
                          [green()])
    assert outcome.status == "review-fix"
    assert models.calls(NORTH, "review") == []


def test_a_failed_reviewer_call_goes_to_the_backup_and_the_outcome_names_it(monkeypatch):
    # 11. the reviewer's call fails, the backup returns READY: passed, and the reason names the backup
    outcome, _ = run({(DOTS, "write"): [full()], (XING, "review"): [p1b.ModelFailed("down")], (GEMMA31, "review"): [READY], (NORTH, "review"): [READY]},
                     [green()], enable_gemma=True, monkeypatch=monkeypatch)
    assert outcome.status == "passed" and GEMMA31 in outcome.reason


def test_a_reply_with_no_verdict_line_goes_to_the_backup_without_a_re_ask(monkeypatch):
    outcome, models = run({(DOTS, "write"): [full()], (XING, "review"): ["looks fine but no verdict"], (GEMMA31, "review"): [READY],
                           (NORTH, "review"): [READY]}, [green()], enable_gemma=True, monkeypatch=monkeypatch)
    assert outcome.status == "passed"
    assert models.calls(XING, "review-reask") == []


def test_no_enabled_backup_for_an_invalid_review_is_provider_unavailable():
    outcome, _ = run({(DOTS, "write"): [full()], (XING, "review"): ["no verdict line here"], (NORTH, "review"): [READY]}, [green()])
    assert outcome.status == "provider-unavailable"


# ---- 7. nothing from the production path imports the legacy models

def test_the_routing_module_names_no_legacy_model_family():
    source = (REPO / "scripts" / "source" / "p1b_routing.py").read_text(encoding="utf-8").lower()
    for word in ("codex", "haiku", "nvidia", "sonnet", "nemotron"):
        assert word not in source


# ---- pre-pilot fix 2026-11-02: the upstream error of a failed reviewer reaches the job's reason

def test_a_failed_reviewer_keeps_its_upstream_error_in_the_outcome_reason():
    outcome, _ = run({(DOTS, "write"): [full()], (XING, "review"): [p1b.ModelFailed("HTTP 429 shared pool")]}, [green()])
    assert outcome.status == "provider-unavailable"
    assert "HTTP 429 shared pool" in outcome.reason


def test_a_failed_reviewer_and_a_failed_backup_both_keep_their_errors(monkeypatch):
    outcome, _ = run({(DOTS, "write"): [full()], (XING, "review"): [p1b.ModelFailed("HTTP 429 A")], (GEMMA31, "review"): [p1b.ModelFailed("HTTP 429 B")]},
                     [green()], enable_gemma=True, monkeypatch=monkeypatch)
    assert outcome.status == "provider-unavailable"
    assert "HTTP 429 A" in outcome.reason and "HTTP 429 B" in outcome.reason
