"""Stage configuration (Control Center P1, 2026-11-03): the ten stage types resolved from config/roster.json, the frozen per-job snapshot
(_loop/<job>/stage_snapshot.json), and apply_future_stage. Today's roster must give exactly today's calls.

Fakes only: a recording client, the scripted OpenRouter transport (prod_harness), a spy in place of claude_code.run, and the fake sandbox. No
network, no real claude, no Docker."""
import copy
import json
import os
import sys
import threading
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "source"))
sys.path.insert(0, str(REPO / "pieces"))
import claude_code as cc  # noqa: E402
import openrouter_call as oc  # noqa: E402
import p0_gates as gates  # noqa: E402
import p1b_production as pm  # noqa: E402
import p1b_routing as p1b  # noqa: E402
import prod_harness as ph  # noqa: E402
import stage_config as sc  # noqa: E402
from test_p0_acceptance import GOOD_CODE, GOOD_TEST  # noqa: E402
from test_p1b_production_run import FakeSandbox, p1b_job, wp_h_root  # noqa: E402

FILE_TEXT = "FILE: pieces/calc.py\n```python\nx = 1\n```\n"
READY = "No finding.\n## Verdict: READY"
TEST_BUDGET = {"free_requests": 40, "review_usd": 0, "paid_usd": 0}


def file_roster():
    return json.loads(oc.ROSTER_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def roster(tmp_path, monkeypatch):
    """roster(edit) writes a copy of config/roster.json changed by edit(data), points the adapter at it and empties the per-profile cache.
    Calling it again rewrites the same copy (a roster edit)."""
    path = tmp_path / "roster_copy.json"
    path.write_text(oc.ROSTER_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(oc, "ROSTER_PATH", path)
    monkeypatch.setattr(oc, "_ROSTER_STATE", {})

    def use(edit=None):
        data = json.loads(path.read_text(encoding="utf-8"))
        if edit is not None:
            edit(data)
        path.write_text(json.dumps(data), encoding="utf-8")
        oc._ROSTER_STATE.clear()
        return path
    return use


def stages_of(data, profile):
    return data["profiles"][profile]["stages"]


def roles_of(profile):
    return file_roster()["profiles"][profile]["roles"]


def codes(problems):
    return [p["code"] for p in problems]


# ---------------------------------------------------------------- recording client and job runner

class StageClient:
    """Stands in for the OpenRouter client: records (role, kind, effort, stage) of each call and answers a FILE block or READY. on_call(client,
    role, kind, stage) runs inside the call (the call is then in flight)."""

    def __init__(self, on_call=None):
        self.calls, self.requests, self.failed_attempts, self.retry_log = [], [], [], []
        self.budget = oc.JobBudget(40, 0)
        self.on_call = on_call

    def begin_job(self):
        pass

    def set_job_deadline(self, seconds):
        pass

    def call(self, role, system, user, kind="text", effort=None, stage=None):
        self.calls.append({"role": role, "kind": kind, "effort": effort, "stage": copy.deepcopy(stage)})
        if self.on_call is not None:
            self.on_call(self, role, kind, stage)
        model = stage["model"] if stage else oc.resolve(role)
        self.requests.append({"model": model, "status": 200, "failed": False, "seconds": 0.1, "finish_reason": "stop"})
        return (FILE_TEXT if kind in ("writer", "repair") else READY), {"model": model}


def parse(text):
    if "FILE:" not in text:
        return {}, []
    return {"pieces/calc.py": GOOD_CODE, "pieces/test_calc.py": GOOD_TEST}, []


def run_job(root, client):
    job = p1b_job(budget=dict(TEST_BUDGET))
    originals = {rel: (root / rel).read_text(encoding="utf-8") if (root / rel).is_file() else None for rel in job["files"]}
    return pm.run(job, root, root / "_loop" / "calc", sandbox=FakeSandbox({}), protected=[], acceptance_lock=gates.hash_paths(root, []),
                  originals=originals, evidence={}, client=client, parse_files=parse, check_path=lambda rel: None,
                  content_problem=lambda rel, content: None, scrub_keys=[])


def status(root):
    return json.loads((root / "_loop" / "calc" / "status.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------- 1. the defaults are today's roster, call for call

@pytest.mark.parametrize("profile", ["test", "production"])
def test_every_default_stage_resolves_to_its_role_and_todays_effort_and_settings(profile):
    data = file_roster()
    block, roles = data["profiles"][profile], data["profiles"][profile]["roles"]
    table = sc.resolve_stages(profile)
    assert tuple(table) == sc.STAGE_NAMES and len(table) == 10
    for stage, (role, mode) in sc.STAGES.items():
        entry = table[stage]
        assert set(entry) == {"model", "via", "effort", "settings", "model_selectable", "source"}
        if stage in sc.INHERITED:
            assert entry["model"] is None and entry["model_selectable"] is False
            continue
        spec = roles[role]
        assert entry["model"] == spec["model"] and entry["via"] == oc.via_of(spec) and entry["model_selectable"] is True
        want = dict(spec["settings"])
        if mode == "repair" and oc.via_of(spec) == "openrouter":
            want["max_tokens"] = block["repair_max_tokens"]          # what a repair call sends today (openrouter_call.repair_max_tokens)
        assert dict(entry["settings"]) == want, stage
        if oc.via_of(spec) == "claude_code":
            assert entry["effort"] == spec["effort"], stage
        else:
            assert entry["effort"] == (block["effort_by_mode"].get(mode) if mode else None), stage


@pytest.mark.parametrize("profile", ["test", "production"])
def test_the_ladder_roles_of_a_default_snapshot_are_the_rosters_role_models(profile):
    snap, sha = sc.snapshot(profile)
    assert len(sha) == 64 and snap["revision"] == 1 and snap["roster_revision"] == file_roster()["revision"]
    view = sc.ladder_roles(snap)
    state = oc.roster_state(profile)
    assert {role: spec["model"] for role, spec in view.items()} == state["ROLES"]
    assert {role: spec["paid"] for role, spec in view.items()} == {role: spec["paid"] for role, spec in state["ROLE_SPECS"].items()}
    for origin in (state["ROLES"]["writer_a"], state["ROLES"]["writer_b"]):
        assert p1b.panel_for(origin, specs=view) == p1b.panel_for(origin, specs=state["ROLE_SPECS"])


CALLS = [("writer_a", "writer", "write"), ("writer_a", "repair", "repair"), ("writer_b", "writer", "write"),
         ("writer_b", "repair", "repair"), ("reviewer_b", "reviewer", "review"), ("reviewer_b", "reviewer", "review-reask"),
         ("reviewer_b", "writer", "write"),             # the fallback write: writer_b is down
         ("writer_b", "reviewer", "review"),            # a writer in a review slot (non_author_writer or the backup)
         ("writer_a", "reviewer", "review-reask")]


@pytest.mark.parametrize("profile", ["test", "production"])
def test_with_default_stages_every_openrouter_request_body_is_byte_for_byte_todays(profile, tmp_path, monkeypatch):
    monkeypatch.setenv("SATVIK_PROFILE", profile)
    monkeypatch.setenv("SATVIK_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    data = sc.profile_of(sc.snapshot(profile)[0])
    cases = list(CALLS) + ([("reviewer_a", "reviewer", "review")] if profile == "production" else [])
    for role, kind, mode in cases:
        model = oc.roster_state()["ROLES"][role]
        reply = FILE_TEXT if kind in ("writer", "repair") else READY
        bodies = []
        for use_stage in (False, True):
            provider = ph.Provider({model: [reply]})
            client = ph.client(tmp_path / f"{role}-{mode}-{use_stage}", provider, budget=oc.JobBudget(40, 0.05))
            if use_stage:
                _stages, spec = sc.dispatch(data, role, mode)
                client.call(role, "S", "U", kind=kind, effort=spec["effort"], stage=spec)
            else:
                client.call(role, "S", "U", kind=kind, effort=oc.effort_for_mode(mode))
            bodies.append([body for _model, body in provider.calls])
        assert bodies[0] == bodies[1], (role, kind, mode)


def test_a_default_job_writes_its_snapshot_and_makes_todays_calls(tmp_path):
    root = wp_h_root(tmp_path)
    client = StageClient()
    result = run_job(root, client)
    assert result["state"] == "passed", result
    roles = oc.roster_state()["ROLES"]
    assert [(c["role"], c["kind"], c["effort"]) for c in client.calls] == [
        ("writer_a", "writer", "high"), ("reviewer_b", "reviewer", "low"), ("writer_b", "reviewer", "low")]   # test: reviewer_a is the non-author writer
    assert [c["stage"]["model"] for c in client.calls] == [roles["writer_a"], roles["reviewer_b"], roles["writer_b"]]
    job_dir = root / "_loop" / "calc"
    snap = json.loads((job_dir / "stage_snapshot.json").read_text(encoding="utf-8"))
    assert snap["revision"] == 1 and set(snap["resolved"]) == set(sc.STAGE_NAMES)
    assert status(root)["evidence"]["stage_snapshot"] == sc.snapshot_evidence(snap)
    assert set(status(root)["evidence"]["stage_snapshot"]) == {"sha256", "revision"}
    assert json.loads((job_dir / "stage_started.json").read_text(encoding="utf-8"))["started"].keys() == {"writer_a_write", "reviewer_b", "reviewer_a"}


def test_a_production_object_without_a_snapshot_still_reads_the_roster():
    models = pm.Production.__new__(pm.Production)
    assert models.stage_file is None and models.ladder_roles() is None and models._stage_call("m", "write") is None
    assert p1b._m("writer_a") == oc.roster_state()["ROLES"]["writer_a"]


# ---------------------------------------------------------------- 2. inheritance rules

def test_an_override_replaces_only_the_keys_it_names(roster):
    def edit(data):
        stages_of(data, "test")["writer_a_write"] = {"settings": {"temperature": 0.7}}
        stages_of(data, "test")["reviewer_b"] = {"effort": "medium"}
    roster(edit)
    table = sc.resolve_stages("test")
    roles = roles_of("test")
    assert table["writer_a_write"]["settings"]["temperature"] == 0.7
    assert dict(table["writer_a_write"]["settings"], temperature=roles["writer_a"]["settings"]["temperature"]) == roles["writer_a"]["settings"]
    assert table["writer_a_write"]["model"] == roles["writer_a"]["model"] and table["writer_a_write"]["effort"] == "high"
    assert table["writer_a_repair"]["settings"]["temperature"] == roles["writer_a"]["settings"]["temperature"]    # another stage of the role keeps the role's
    assert table["reviewer_b"]["effort"] == "medium" and table["reviewer_b"]["source"]["effort"] == "stage"
    assert table["writer_a_write"]["source"]["settings"].endswith("+ stage")


def test_the_resolved_table_is_read_only():
    table = sc.resolve_stages("test")
    with pytest.raises(TypeError):
        table["writer_a_write"]["settings"]["temperature"] = 1.0
    with pytest.raises(TypeError):
        table["planner"] = {}


def test_the_fallback_write_is_reviewer_b_model_with_writer_b_write_settings_and_effort(roster):
    data = sc.profile_of(sc.snapshot("test")[0])
    roles = data["roles"]
    _stages, today = sc.dispatch(data, "reviewer_b", "write")
    assert today["model"] == roles["reviewer_b"]["model"] and today["effort"] == data["effort_by_mode"]["write"]
    assert today["settings"] == roles["reviewer_b"]["settings"]           # unchanged roster: exactly the call made today
    assert _stages == ("writer_b_write", "reviewer_b")

    def edit(data):
        stages_of(data, "test")["writer_b_write"] = {"effort": "medium", "settings": {"max_tokens": 50000, "temperature": 0.3}}
    roster(edit)
    data = sc.profile_of(sc.snapshot("test")[0])
    _stages, spec = sc.dispatch(data, "reviewer_b", "write")
    assert spec["model"] == roles["reviewer_b"]["model"] and spec["role"] == "reviewer_b" and spec["stage"] == "writer_b_write"
    assert spec["effort"] == "medium" and spec["settings"]["max_tokens"] == 50000 and spec["settings"]["temperature"] == 0.3
    assert spec["settings"]["timeout_s"] == roles["reviewer_b"]["settings"]["timeout_s"]


def test_reviewer_reask_inherits_the_triggering_reviewers_model(roster):
    def edit(data):
        stages_of(data, "test")["reviewer_reask"] = {"effort": "medium", "settings": {"max_tokens": 9000}}
    roster(edit)
    data = sc.profile_of(sc.snapshot("test")[0])
    roles = data["roles"]
    for trigger in ("reviewer_b", "writer_a", "writer_b"):
        stages, spec = sc.dispatch(data, trigger, "review-reask")
        assert stages == ("reviewer_reask",) and spec["model"] == roles[trigger]["model"] and spec["role"] == trigger
        assert spec["effort"] == "medium" and spec["settings"]["max_tokens"] == 9000
        assert spec["settings"]["temperature"] == roles[trigger]["settings"]["temperature"]
    assert sc.resolve_stages("test")["reviewer_reask"]["model_selectable"] is False


def test_review_fix_repair_inherits_the_current_writer_by_lineage(roster):
    data = sc.profile_of(sc.snapshot("test")[0])
    roles = data["roles"]
    for writer in ("writer_a", "writer_b"):
        stages, spec = sc.dispatch(data, writer, "repair", fix=True)
        assert stages == ("review_fix_repair",) and spec["model"] == roles[writer]["model"]
        assert spec["settings"]["max_tokens"] == data["repair_max_tokens"] and spec["effort"] == data["effort_by_mode"]["repair"]
    stages, spec = sc.dispatch(data, "reviewer_b", "repair", fix=True)                # reviewer_b wrote the code (the fallback write)
    assert stages == ("review_fix_repair",) and spec["model"] == roles["reviewer_b"]["model"]
    stages, spec = sc.dispatch(data, "repair", "repair", fix=True)                    # the repair step wrote it last
    assert spec["model"] == roles["repair"]["model"] and spec["role"] == "repair"
    other = roles["writer_a"]["model"] + "-repairer"

    def edit(data):
        stages_of(data, "test")["writer_a_repair"] = {"model": other}
        stages_of(data, "test")["review_fix_repair"] = {"effort": "medium"}
    roster(edit)
    data = sc.profile_of(sc.snapshot("test")[0])
    _stages, spec = sc.dispatch(data, "writer_a", "repair", fix=True)
    assert spec["model"] == other and spec["effort"] == "medium"
    assert sc.dispatch(data, "writer_a", "repair")[0] == ("writer_a_repair",)
    assert sc.ladder_roles(sc.snapshot("test")[0])["writer_a"]["model"] == roles["writer_a"]["model"]   # lineage still names the write stage's model


def test_a_writer_in_a_review_slot_is_its_write_stage_model_at_the_review_effort():
    data = sc.profile_of(sc.snapshot("test")[0])
    stages, spec = sc.dispatch(data, "writer_b", "review")
    assert stages == ("reviewer_a",) and spec["model"] == data["roles"]["writer_b"]["model"]     # test: reviewer_a is non_author_writer
    assert spec["effort"] == data["effort_by_mode"]["review"] and spec["settings"] == data["roles"]["writer_b"]["settings"]


def test_the_specialist_and_planner_stages_inherit_their_roles(profile):
    profile("production")
    table = sc.resolve_stages("production")
    roles = roles_of("production")
    for stage, role in (("specialist_repair", "repair"), ("planner", "planner")):
        assert (table[stage]["model"], table[stage]["via"], table[stage]["effort"]) == (roles[role]["model"], "claude_code", roles[role]["effort"])


# ---------------------------------------------------------------- 3. every validation code

def _with_stage(profile, stage, override):
    return sc.validate({stage: override}, profile)


@pytest.mark.parametrize("profile,stage,override,code", [
    ("test", "writer_c", {}, "unknown-stage"),
    ("test", "writer_a_write", {"provider": None}, "unknown-key"),
    ("test", "writer_a_write", {"settings": {"colour": 1}}, "unknown-key"),
    ("test", "writer_a_write", "not an object", "bad-stages"),
    ("test", "reviewer_reask", {"model": "any/model:free"}, "model-not-selectable"),
    ("test", "review_fix_repair", {"model": "any/model:free"}, "model-not-selectable"),
    ("test", "review_fix_repair", {"via": "claude_code"}, "model-not-selectable"),
    ("test", "writer_a_write", {"model": ""}, "bad-model"),
    ("test", "writer_a_write", {"via": "api"}, "bad-via"),
    ("test", "writer_a_write", {"effort": "extreme"}, "bad-effort"),
    ("test", "writer_a_write", {"via": "claude_code", "model": "haiku"}, "bad-effort"),      # a Claude Code stage needs an effort
    ("test", "reviewer_reask", {"effort": "extreme"}, "bad-effort"),
    ("production", "writer_a_write", {"via": "claude_code", "model": "gpt-9", "effort": "high"}, "role-rules"),
    ("production", "reviewer_a", {"via": "claude_code", "model": "sonnet", "effort": "high"}, "role-rules"),   # GLM's pin and prices
    ("test", "writer_a_write", {"via": "claude_code", "model": "sonnet", "effort": "high"}, "test-profile-claude-code"),
    ("test", "writer_a_write", {"via": "claude_code", "model": "haiku", "effort": "low"}, "test-profile-claude-code"),
    ("production", "reviewer_a", {"model": "z-ai/another-model"}, "paid-model-override"),
])
def test_each_stage_rule_has_its_code(profile, stage, override, code):
    problems = _with_stage(profile, stage, override)
    assert code in codes(problems), problems
    assert all(set(p) == {"code", "stage", "message"} and p["code"] in sc.CODES for p in problems)


def test_the_panel_rules_have_their_codes():
    test, production = roles_of("test"), roles_of("production")
    assert codes(_with_stage("test", "reviewer_b", {"model": test["writer_a"]["model"]})) == ["reviewer-is-writer"]
    assert codes(_with_stage("test", "reviewer_b", {"model": test["writer_b"]["model"]})) == ["reviewer-is-writer"]
    assert "reviewers-same-model" in codes(_with_stage("production", "reviewer_b", {"model": production["reviewer_a"]["model"]}))
    assert codes(_with_stage("test", "writer_a_repair", {"model": test["writer_b"]["model"]})) == ["writers-share-model"]
    assert _with_stage("test", "reviewer_a", {"effort": "medium"}) == []      # the non_author_writer sentinel is never a conflict


def test_a_paid_role_under_the_test_profile_has_its_code():
    data = sc.profile_of(sc.snapshot("test")[0])
    glm = roles_of("production")["reviewer_a"]
    data["roles"]["reviewer_b"].update(paid=True, provider=glm["provider"], pricing=glm["pricing"])
    assert "test-profile-paid" in codes(sc.validate({}, data))


def test_allowed_changes_validate_clean():
    assert sc.validate({}, "test") == [] and sc.validate({}, "production") == []
    assert _with_stage("test", "writer_a_write", {"via": "claude_code", "model": "haiku", "effort": "high"}) == []
    assert _with_stage("production", "specialist_repair", {"model": "sonnet", "effort": "low"}) == []
    assert _with_stage("test", "reviewer_reask", {"effort": "medium", "settings": {"max_tokens": 9000}}) == []


def test_resolve_stages_raises_a_roster_refusal_with_the_code(roster):
    roster(lambda data: stages_of(data, "test").update(reviewer_b={"model": roles_of("test")["writer_a"]["model"]}))
    with pytest.raises(sc.StageRefused) as caught:
        sc.resolve_stages("test")
    assert isinstance(caught.value, oc.RosterRefused) and caught.value.code == "reviewer-is-writer" and caught.value.stage == "reviewer_b"
    roster(lambda data: data["profiles"]["test"].update(stages=["not", "a", "block"]))
    with pytest.raises(sc.StageRefused) as caught:
        sc.resolve_stages("test")
    assert caught.value.code == "bad-stages"


def test_a_bad_roster_revision_has_its_code_and_the_plain_roster_refusal_keeps_its_own(roster):
    roster(lambda data: data.update(revision="two"))
    with pytest.raises(oc.RosterRefused) as caught:
        oc.load_roster_file()
    assert caught.value.code == "bad-revision"
    assert oc.RosterRefused("x").code == "roster-refused"


def test_a_refused_stage_table_stops_the_job_before_any_call(roster, tmp_path):
    roster(lambda data: stages_of(data, "test").update(reviewer_b={"model": roles_of("test")["writer_a"]["model"]}))
    root = wp_h_root(tmp_path)
    client = StageClient()
    result = run_job(root, client)
    assert result["state"] == "refused" and client.calls == []
    assert "reviewer-is-writer" in status(root)["reason"]


# ---------------------------------------------------------------- 4. the snapshot is the job's own

def test_two_concurrent_jobs_with_different_snapshots_stay_isolated(roster, tmp_path):
    roster()
    roles = roles_of("test")
    other = "vendor/other-writer:free"
    barrier, first_in = threading.Barrier(2, timeout=30), threading.Event()

    def meet(client, role, kind, stage):
        if len(client.calls) == 1:
            first_in.set()
            barrier.wait()                                    # both jobs are in their first call at once

    clients = {"a": StageClient(meet), "b": StageClient(meet)}
    roots = {name: wp_h_root(tmp_path / name) for name in clients}
    results, errors = {}, []

    def job(name):
        try:
            results[name] = run_job(roots[name], clients[name])
        except BaseException as exc:                           # noqa: BLE001 - reported below
            errors.append(exc)

    one = threading.Thread(target=job, args=("a",))
    one.start()
    assert first_in.wait(30)
    roster(lambda data: stages_of(data, "test").update(writer_a_write={"model": other, "settings": {"temperature": 0.7}}))
    two = threading.Thread(target=job, args=("b",))
    two.start()
    one.join(60)
    two.join(60)
    assert not errors, errors
    assert results["a"]["state"] == results["b"]["state"] == "passed", results
    a, b = clients["a"].calls, clients["b"].calls
    assert (a[0]["stage"]["model"], a[0]["stage"]["settings"]["temperature"]) == (roles["writer_a"]["model"], 0.1)
    assert (b[0]["stage"]["model"], b[0]["stage"]["settings"]["temperature"]) == (other, 0.7)
    assert not any(other in json.dumps(call) for call in a)
    lineage = {name: json.loads((roots[name] / "_loop" / "calc" / "lineage.json").read_text(encoding="utf-8")) for name in roots}
    assert lineage["a"]["pieces/calc.py"]["origin"] == roles["writer_a"]["model"] and lineage["b"]["pieces/calc.py"]["origin"] == other
    assert status(roots["a"])["evidence"]["stage_snapshot"]["sha256"] != status(roots["b"])["evidence"]["stage_snapshot"]["sha256"]


def test_a_roster_edit_during_a_job_changes_none_of_its_later_calls(roster, tmp_path):
    roster()
    roles = roles_of("test")

    def edit_once(client, role, kind, stage):
        if len(client.calls) == 1:
            def edit(data):
                data["profiles"]["test"]["roles"]["reviewer_b"]["settings"]["temperature"] = 0.9
                stages_of(data, "test")["reviewer_b"] = {"effort": "high"}
                data["profiles"]["test"]["effort_by_mode"]["review"] = "medium"
            roster(edit)

    root = wp_h_root(tmp_path)
    client = StageClient(edit_once)
    assert run_job(root, client)["state"] == "passed"
    assert oc.roster_state()["ROLE_SPECS"]["reviewer_b"]["settings"]["temperature"] == 0.9     # the edit is live for roster readers
    review = client.calls[1]
    assert review["role"] == "reviewer_b" and review["effort"] == "low"
    assert review["stage"]["model"] == roles["reviewer_b"]["model"] and review["stage"]["settings"]["temperature"] == 0.1
    assert client.calls[2]["effort"] == "low"                                                  # the non-author review too


def test_a_real_client_call_uses_the_stage_settings_not_the_roster_read_now(roster, tmp_path, monkeypatch):
    monkeypatch.setenv("SATVIK_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    roster(lambda data: stages_of(data, "test").update(writer_a_write={"settings": {"temperature": 0.4, "max_tokens": 1234}}))
    _stages, spec = sc.dispatch(sc.profile_of(sc.snapshot("test")[0]), "writer_a", "write")
    roster(lambda data: data["profiles"]["test"]["roles"]["writer_a"]["settings"].update(temperature=0.95, reasoning={"max_tokens": 1}))
    model = roles_of("test")["writer_a"]["model"]
    provider = ph.Provider({model: [FILE_TEXT]})
    client = ph.client(tmp_path, provider)
    client.call("writer_a", "S", "U", kind="writer", effort=spec["effort"], stage=spec)
    body = provider.calls[0][1]
    assert body["temperature"] == 0.4 and body["max_tokens"] == 1234 and body["reasoning"] == {"effort": "high"}
    assert client.requests[-1]["reasoning_over_cap"] is None


def test_a_claude_code_stage_and_the_repair_step_use_the_snapshot(roster, profile, tmp_path, monkeypatch):
    profile("production")

    def edit(data):
        stages_of(data, "production")["writer_a_write"] = {"via": "claude_code", "model": "sonnet", "effort": "high"}
        stages_of(data, "production")["specialist_repair"] = {"model": "sonnet", "effort": "low"}
    roster(edit)
    seen = []

    def fake_run(prompt, cwd, claude_cmd, timeout_s, env, *, model, effort, system_prompt=None, max_output_tokens=cc.MAX_OUTPUT_TOKENS):
        seen.append((model, effort))
        return 0, json.dumps({"type": "result", "is_error": False, "result": FILE_TEXT}), ""
    monkeypatch.setattr(cc, "run", fake_run)
    root = wp_h_root(tmp_path)
    job_dir = root / "_loop" / "calc"
    (job_dir / "history").mkdir(parents=True)
    snap, _sha = sc.snapshot("production")
    stage_file = sc.write_snapshot(job_dir, snap)
    roster(lambda data: stages_of(data, "production").update(writer_a_write={"via": "claude_code", "model": "fable", "effort": "max"},
                                                             specialist_repair={"model": "fable", "effort": "max"}))
    job = p1b_job()
    models = pm.Production(job, root, StageClient(), FakeSandbox({}), protected=[], acceptance_lock=None, originals={}, parse_files=parse,
                           check_path=lambda rel: None, content_problem=lambda rel, content: None, history=job_dir / "history", job_dir=job_dir)
    models.stage_file = stage_file
    assert models.ladder_roles()["writer_a"]["model"] == "sonnet"
    models.call("sonnet", "write", job["files"])
    models.call(p1b.ANTHROPIC, "repair", job["files"])
    assert seen == [("sonnet", "high"), ("sonnet", "low")]
    record = json.loads((job_dir / "history" / "call_01.json").read_text(encoding="utf-8"))
    assert record["model"] == "sonnet" and record["via"] == "claude_code"


def test_ask_role_takes_a_spec_and_refuses_one_that_is_not_claude_code():
    seen = []

    def runner(prompt, cwd, claude_cmd, timeout_s, env):
        seen.append(timeout_s)
        return 0, json.dumps({"type": "result", "is_error": False, "result": FILE_TEXT}), ""
    spec = {"via": "claude_code", "model": "haiku", "effort": "high", "settings": {"timeout_s": 77}}
    assert cc.ask_role("writer_a", "s", "u", "writer", runner=runner, spec=spec) == FILE_TEXT and seen == [77]
    with pytest.raises(cc.ClaudeFailed):
        cc.ask_role("writer_a", "s", "u", "writer", runner=runner, spec=dict(spec, via="openrouter"))


# ---------------------------------------------------------------- 5. apply future stage

def _fresh_job(tmp_path, profile="test"):
    job_dir = tmp_path / "_loop" / "calc"
    snap, _sha = sc.snapshot(profile)
    sc.write_snapshot(job_dir, snap)
    return job_dir


def test_apply_future_stage_accepts_a_stage_that_has_not_started(tmp_path):
    job_dir = _fresh_job(tmp_path)
    assert sc.apply_future_stage(job_dir, "reviewer_b", {"settings": {"temperature": 0.3}, "effort": "medium"}) == {"ok": True, "revision": 2}
    snap = sc.read_snapshot(job_dir)
    assert snap["revision"] == 2 and snap["resolved"]["reviewer_b"]["settings"]["temperature"] == 0.3
    assert snap["changes"] == [{"revision": 2, "stage": "reviewer_b", "change": {"settings": {"temperature": 0.3}, "effort": "medium"}}]
    assert sc.apply_future_stage(job_dir, "reviewer_reask", {"effort": "medium"}) == {"ok": True, "revision": 3}
    assert not list(job_dir.glob(".*.tmp"))


def test_apply_future_stage_refuses_a_started_stage(tmp_path):
    job_dir = _fresh_job(tmp_path)
    sc.begin_call(job_dir, "writer_a", "write")
    refused = sc.apply_future_stage(job_dir, "writer_a_write", {"settings": {"temperature": 0.5}})
    assert refused["ok"] is False and refused["code"] == "stage-started"
    assert sc.read_snapshot(job_dir)["revision"] == 1


@pytest.mark.parametrize("record,stage", [
    ({"steps": [{"model": "WRITER_A", "mode": "write", "note": "2 file(s) returned"}]}, "writer_a_write"),
    ({"steps": [{"model": "WRITER_A", "mode": "failed", "call": "write", "status": 503}]}, "writer_a_write"),
    ({"history": {"model": "REVIEWER_B", "mode": "review"}}, "reviewer_b"),
    ({"history": {"model": "REVIEWER_B", "mode": "review-reask"}}, "reviewer_reask"),
    ({"steps": [{"model": "anthropic", "mode": "repair"}]}, "specialist_repair"),
    ({"steps": [{"model": "REVIEWER_B", "mode": "review"}, {"model": "WRITER_A", "mode": "repair"}]}, "review_fix_repair"),
    ({"steps": [{"model": "REVIEWER_B", "mode": "write"}]}, "reviewer_b"),          # the fallback write: reviewer_b's model authored code
])
def test_a_stage_with_a_step_or_a_call_record_has_started(tmp_path, record, stage):
    job_dir = _fresh_job(tmp_path)
    names = {"WRITER_A": roles_of("test")["writer_a"]["model"], "REVIEWER_B": roles_of("test")["reviewer_b"]["model"]}
    text = json.dumps(record)
    for key, value in names.items():
        text = text.replace(key, value)
    record = json.loads(text)
    if "steps" in record:
        (job_dir / "status.json").write_text(json.dumps({"state": "running", "steps": record["steps"]}), encoding="utf-8")
    if "history" in record:
        (job_dir / "history").mkdir()
        (job_dir / "history" / "call_01.json").write_text(json.dumps(record["history"]), encoding="utf-8")
    assert stage in sc.started_stages(job_dir)
    change = {"effort": "medium"} if stage not in ("specialist_repair",) else {"settings": {"max_tokens": 9000}}
    assert sc.apply_future_stage(job_dir, stage, change)["code"] == "stage-started"


def test_apply_future_stage_refuses_inherited_models_bad_tables_and_unknown_jobs(tmp_path):
    job_dir = _fresh_job(tmp_path)
    for stage, change in (("reviewer_reask", {"model": "any/model:free"}), ("review_fix_repair", {"model": "any/model:free"}),
                          ("review_fix_repair", {"via": "claude_code"})):
        assert sc.apply_future_stage(job_dir, stage, change)["code"] == "model-not-selectable"
    refused = sc.apply_future_stage(job_dir, "reviewer_b", {"model": roles_of("test")["writer_a"]["model"]})
    assert refused["ok"] is False and refused["code"] == "reviewer-is-writer"
    assert sc.apply_future_stage(job_dir, "writer_c", {"effort": "low"})["code"] == "unknown-stage"
    assert sc.apply_future_stage(job_dir, "reviewer_b", {"effort": "extreme"})["code"] == "bad-effort"
    assert sc.apply_future_stage(tmp_path / "nothing", "reviewer_b", {"effort": "low"})["code"] == "no-snapshot"
    assert sc.read_snapshot(job_dir)["revision"] == 1


def test_a_change_during_an_in_flight_call_is_refused_for_that_stage_and_picked_up_by_the_next(tmp_path):
    root = wp_h_root(tmp_path)
    job_dir = root / "_loop" / "calc"
    answers = []

    def during_write(client, role, kind, stage):
        if len(client.calls) == 1:                         # the writer_a write is in flight
            answers.append(sc.apply_future_stage(job_dir, "writer_a_write", {"settings": {"temperature": 0.5}}))
            answers.append(sc.apply_future_stage(job_dir, "reviewer_b", {"settings": {"temperature": 0.3}}))

    client = StageClient(during_write)
    assert run_job(root, client)["state"] == "passed"
    assert answers[0]["ok"] is False and answers[0]["code"] == "stage-started"
    assert answers[1] == {"ok": True, "revision": 2}
    assert client.calls[0]["stage"]["revision"] == 1 and client.calls[0]["stage"]["settings"]["temperature"] == 0.1
    review = client.calls[1]
    assert review["role"] == "reviewer_b" and review["stage"]["revision"] == 2 and review["stage"]["settings"]["temperature"] == 0.3
    evidence = status(root)["evidence"]["stage_snapshot"]
    assert evidence == sc.snapshot_evidence(sc.read_snapshot(job_dir)) and evidence["revision"] == 2


def test_concurrent_changes_to_one_job_each_get_their_own_revision(tmp_path):
    job_dir = _fresh_job(tmp_path)
    answers = []
    threads = [threading.Thread(target=lambda n=n: answers.append(sc.apply_future_stage(job_dir, "reviewer_b", {"settings": {"max_tokens": 1000 + n}})))
               for n in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert sorted(a["revision"] for a in answers) == [2, 3, 4, 5, 6, 7]
    assert sc.read_snapshot(job_dir)["revision"] == 7 and len(sc.read_snapshot(job_dir)["changes"]) == 6
    assert os.path.exists(job_dir / sc.LOCK_FILE)
