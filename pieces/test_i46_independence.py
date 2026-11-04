"""I46 (2026-11-04): independence follows every contributor, not the fresh writer only. The lineage records the ACTUAL served model identity of each
contributor (writers, the specialist repair step, the review-fix repair), and the review panel excludes every one of them.

The audit's case: the same Claude model is the specialist repairer and a reviewer; the repair step runs, then that model reviews its own repair.
No model call: scripted fake models (the ladder and the panel are the real code), and one fake Claude output read by the real repair step."""
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "source"))
import openrouter_call as oc  # noqa: E402
import p1b_production as pm  # noqa: E402
import p1b_routing as p1b  # noqa: E402

NL = "\n"
READY = "Looks right." + NL + "## Verdict: READY"
JOB = {"files": ["pieces/calc.py", "pieces/test_calc.py"], "deadline_min": 60, "acceptance_tests": []}
ANTHROPIC = p1b.ANTHROPIC


@pytest.fixture
def roles(profile):
    profile("production")
    return oc.roster_state()["ROLES"]


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def green():
    return p1b.Result(True, frozenset(), frozenset(), False)


def red(tests=("t1",), systemic=False):
    return p1b.Result(False, frozenset(tests), frozenset(["pieces/calc.py"]), systemic)


def full(text="x = 1" + NL):
    return {"pieces/calc.py": text, "pieces/test_calc.py": "def test(): pass" + NL}


class ServedModels:
    """Scripted models for the real ladder. `served` maps a model (or the repair step) to the identities its call reports as served."""

    def __init__(self, script, tests, served=None):
        self.script = {key: list(items) for key, items in script.items()}
        self.tests_results = list(tests)
        self.served = dict(served or {})
        self.log, self.slots, self.written = [], [], []

    def call(self, model_id, mode, files, slot=None):
        self.log.append((mode, model_id))
        if mode == "review":
            self.slots.append((model_id, slot))
        queue = self.script.get((model_id, mode))
        if not queue:
            raise AssertionError(f"unscripted call {mode} {model_id}")
        item = queue.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    def served_identities(self, model):
        return list(self.served.get(model, [model]))

    def tests(self, files):
        if not self.tests_results:
            raise AssertionError("no test result left")
        return self.tests_results.pop(0)

    def write(self, reply):
        self.written.append(dict(reply))

    def restore(self):
        pass

    def restore_green(self):
        pass

    def stage_fix(self, findings):
        pass

    def reject(self, model_id, mode, reason):
        pass

    def contract_check(self, test_ids, dots_files, xing_files):
        pass

    def calls(self, model_id=None, mode=None):
        return [entry for entry in self.log if (model_id is None or entry[1] == model_id) and (mode is None or entry[0] == mode)]


def test_the_repair_step_served_as_the_reviewer_model_is_never_its_own_reviewer(roles):
    """The audit's case. XING writes fresh; the tests stay red; the repair step (ANTHROPIC) runs and is served as NORTH, the model that is also
    reviewer_b. Before I46 the panel was GLM and NORTH (NORTH reviews the code it repaired). Now NORTH is excluded, the free backup takes its
    slot, and GLM is still the paid second reviewer."""
    dots, xing, north, glm = roles["writer_a"], roles["writer_b"], roles["reviewer_b"], roles["reviewer_a"]
    models = ServedModels({(dots, "write"): [full()], (xing, "write"): [full()], (xing, "repair"): [full("x = 4" + NL)],
                           (ANTHROPIC, "repair"): [full("x = 5" + NL)], (dots, "review"): [READY], (glm, "review"): [READY]},
                          [red(systemic=True), red(("t1", "t2")), red(), green()], served={ANTHROPIC: [north]})
    lineage = p1b.Lineage()
    outcome = p1b.walk(JOB, models, lineage, Clock())
    assert outcome.status == "passed", outcome
    assert models.calls(north, "review") == [], "the repairer's model reviewed its own repair"
    assert models.slots == [(dots, "reviewer_b"), (glm, "reviewer_a")], models.slots
    row = lineage.get("pieces/calc.py")
    assert row["origin"] == xing and row["last"] == ANTHROPIC and row["chain"][-1] == "anthropic"     # the chain names the step, as before
    assert north in lineage.served("pieces/calc.py") and xing in lineage.served("pieces/calc.py")     # the served identities are recorded


def test_the_writer_is_excluded_as_well_as_every_repairer_model(roles):
    """The same run with the repair step served as the OTHER free writer (DOTS): DOTS wrote nothing here but repaired the code, so it is a
    contributor and cannot review; NORTH, the origin's panel slot, is free and reviews."""
    dots, xing, north, glm = roles["writer_a"], roles["writer_b"], roles["reviewer_b"], roles["reviewer_a"]
    models = ServedModels({(dots, "write"): [full()], (xing, "write"): [full()], (xing, "repair"): [full("x = 4" + NL)],
                           (ANTHROPIC, "repair"): [full("x = 5" + NL)], (north, "review"): [READY], (glm, "review"): [READY]},
                          [red(systemic=True), red(("t1", "t2")), red(), green()], served={ANTHROPIC: [dots]})
    outcome = p1b.walk(JOB, models, p1b.Lineage(), Clock())
    assert outcome.status == "passed", outcome
    assert models.calls(dots, "review") == []
    assert models.slots == [(north, "reviewer_b"), (glm, "reviewer_a")], models.slots


def test_a_paid_reviewer_that_contributed_blocks_the_panel_and_is_never_called(roles):
    """If the repair step was served as the paid reviewer (GLM), there is no substitute for that slot: the job ends blocked and GLM is not asked."""
    dots, xing, north, glm = roles["writer_a"], roles["writer_b"], roles["reviewer_b"], roles["reviewer_a"]
    models = ServedModels({(dots, "write"): [full()], (xing, "write"): [full()], (xing, "repair"): [full("x = 4" + NL)],
                           (ANTHROPIC, "repair"): [full("x = 5" + NL)]},
                          [red(systemic=True), red(("t1", "t2")), red(), green()], served={ANTHROPIC: [glm]})
    outcome = p1b.walk(JOB, models, p1b.Lineage(), Clock())
    assert outcome.status == "blocked", outcome
    assert "paid reviewer" in outcome.reason
    assert models.calls(glm, "review") == [] and models.calls(north, "review") == []


@pytest.mark.parametrize("contributors, expected", [
    ((), None),                                   # no contributor: the usual panel
    (("NORTH",), "backup"),                       # North contributed: the free writer that did not write takes its slot
])
def test_the_panel_is_north_then_glm_unless_a_contributor_is_on_it(roles, contributors, expected):
    dots, xing, north, glm = roles["writer_a"], roles["writer_b"], roles["reviewer_b"], roles["reviewer_a"]
    extra = {north if c == "NORTH" else c for c in contributors}
    if expected is None:
        assert p1b.panel_for(xing, contributors=extra) == [north, glm]
    else:
        assert p1b.panel_for(xing, contributors=extra) == [dots, glm]
        assert north not in p1b.panel_for(xing, contributors=extra)


def test_no_panel_contains_any_contributor_or_the_origin(roles):
    dots, xing, north, glm = roles["writer_a"], roles["writer_b"], roles["reviewer_b"], roles["reviewer_a"]
    for origin in (dots, xing):
        for extra in ([north], [dots], [xing], [north, dots]):
            try:
                panel = p1b.panel_for(origin, contributors=extra)
            except (p1b.Blocked, oc.ProviderUnavailable):
                continue
            assert not set(panel) & (set(extra) | {origin}), (origin, extra, panel)


def test_the_panel_with_every_writer_a_contributor_is_provider_unavailable_not_a_self_review(roles):
    dots, xing, north = roles["writer_a"], roles["writer_b"], roles["reviewer_b"]
    with pytest.raises(oc.ProviderUnavailable):
        p1b.panel_for(north, contributors=[dots, xing])


def test_the_lineage_file_keeps_the_served_identities_and_loads_them_back(tmp_path):
    lineage = p1b.Lineage()
    lineage.set("pieces/calc.py", ANTHROPIC, "writer-x", ["writer-x", ANTHROPIC], served=["writer-x", "claude-model-y"])
    path = tmp_path / "lineage.json"
    lineage.save(path)
    loaded = p1b.Lineage.load(path)
    assert loaded.get("pieces/calc.py") == {"last": ANTHROPIC, "origin": "writer-x", "chain": ["writer-x", ANTHROPIC]}
    assert loaded.served("pieces/calc.py") == ["writer-x", "claude-model-y"]
    assert loaded.contributors(["pieces/calc.py"]) == {"writer-x", "claude-model-y"}
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 1


def test_a_lineage_row_with_no_recorded_identity_falls_back_to_its_chain(tmp_path):
    lineage = p1b.Lineage()
    lineage.set("pieces/calc.py", "m1", "m1", ["m1"])
    assert lineage.served("pieces/calc.py") == ["m1"]


def test_the_claude_envelope_names_every_model_it_reports_as_served():
    assert pm._envelope_models(json.dumps({"result": "x", "modelUsage": {"claude-a": {}, "claude-b": {}}})) == ["claude-a", "claude-b"]
    assert pm._envelope_models(json.dumps({"model": "claude-c"})) == ["claude-c"]
    assert pm._envelope_models(json.dumps({"result": "no model here"})) == []
    assert pm._envelope_models("not json") == [] and pm._envelope_models(None) == [] and pm._envelope_models("[1]") == []


def test_the_real_repair_step_records_the_model_its_claude_output_reports(tmp_path, roles):
    """Production._repair_step (the real method) reads the served identity from the repair call's output: the requested repair model and every
    model the output reports in modelUsage. The reply is the usual FILE reply, parsed as a writer's is."""
    root = tmp_path / "repo"
    (root / "specs").mkdir(parents=True)
    (root / "specs" / "s.md").write_text("spec", encoding="utf-8")
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    job = {"name": "calc", "files": ["pieces/calc.py"], "tests": ["pieces/test_calc.py"], "acceptance_tests": [], "spec": "specs/s.md"}
    requested = oc.roster_state()["ROLE_SPECS"]["repair"]["model"]
    reported = "claude-served-as-x"
    envelope = json.dumps({"result": "FILE", "modelUsage": {reported: {"inputTokens": 1}}})

    def fake_repair(prompt, raw_sink=None):
        raw_sink(envelope)
        return "reply text"

    client = type("C", (), {"failed_attempts": [], "requests": []})()
    prod = pm.Production(job, root, client, None, protected=[], acceptance_lock={}, originals={}, parse_files=lambda text: ({"pieces/calc.py": "x = 1\n"}, []),
                         check_path=lambda rel: None, content_problem=lambda rel, content: None, job_dir=job_dir, repair_ask=fake_repair)
    reply = prod._repair_step()
    assert reply == {"pieces/calc.py": "x = 1\n"}
    assert prod.served_identities(ANTHROPIC) == sorted({requested, reported})
