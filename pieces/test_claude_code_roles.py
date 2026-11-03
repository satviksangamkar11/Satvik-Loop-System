"""Any roster role on any model (2026-11-03): config/roster.json is the one place a role's model is chosen. A role says how it is called
("via": "openrouter", the default, or "claude_code" with a model and an effort). The test profile allows a Claude Code role only as haiku at
effort high; production allows any Claude Code model and effort, with the owner's switch still gating opus. Every Claude Code call is the one
isolated runner (claude_code.run), passes the same reply checks as an OpenRouter reply, is recorded the same way with its via, and is never
on the OpenRouter ledger. The review panel stays independent.

Fakes only: the fake claude (pieces/fake_claude.py), injected runners, a spy in place of claude_code.run, and fake transports. No network, no
model call, never a real `claude` process, no Docker."""
import itertools
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "source"))
sys.path.insert(0, str(REPO / "pieces"))
import claude_code as cc  # noqa: E402
import fix_loop as fl  # noqa: E402
import openrouter_call as oc  # noqa: E402
import opus_repair as opus  # noqa: E402
import p0_gates as gates  # noqa: E402
import p1b_production as pm  # noqa: E402
import p1b_routing as p1b  # noqa: E402
import plan_job as pj  # noqa: E402
import prod_harness as ph  # noqa: E402
import shared_limits as sl  # noqa: E402
from test_p0_acceptance import GOOD_CODE, GOOD_TEST  # noqa: E402
from test_p1b_production_run import FakeSandbox, load, p1b_job, wp_h_root  # noqa: E402
from test_p1b_routing import FakeModels, JOB, full, green  # noqa: E402
from test_plan_job import good_answer, repo  # noqa: E402,F401  (repo is a fixture)

FAKE = REPO / "pieces" / "fake_claude.py"
FILES = ["pieces/calc.py", "pieces/test_calc.py"]
FILE_TEXT = "FILE: pieces/calc.py\n```python\nx = 1\n```\n"
READY = "No finding.\n## Verdict: READY"
NO_SETTINGS = {"temperature": None, "reasoning": None, "max_tokens": None, "timeout_s": None, "sampling": None, "max_tokens_cap": None}
ALL_PAIRS = list(itertools.product(cc.MODELS, cc.EFFORTS))


def claude_role(model, effort, paid=False):
    """A roster entry for a role on Claude Code."""
    return {"via": "claude_code", "model": model, "effort": effort, "provider": None, "paid": paid, "pricing": None, "settings": dict(NO_SETTINGS)}


def file_roster():
    return json.loads(oc.ROSTER_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def roster(tmp_path, monkeypatch):
    """roster({(profile, role): spec, ...}) writes a copy of config/roster.json with those roles replaced and makes the adapter read it (the
    per-profile cache starts empty). Returns the file's path."""
    def use(changes):
        data = file_roster()
        for (profile, role), spec in changes.items():
            data["profiles"][profile]["roles"][role] = spec
        path = tmp_path / "roster.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        monkeypatch.setattr(oc, "ROSTER_PATH", path)
        monkeypatch.setattr(oc, "_ROSTER_STATE", {})
        return path
    return use


class Runner:
    """An injected runner (claude_code.run's first five arguments): records each run and answers with a claude envelope around `text`."""

    def __init__(self, text, code=0, is_error=False):
        self.text, self.code, self.is_error, self.runs = text, code, is_error, []

    def __call__(self, prompt, cwd, claude_cmd, timeout_s, env):
        self.runs.append({"prompt": prompt, "cwd_entries": os.listdir(cwd), "timeout_s": timeout_s, "env": env})
        return self.code, json.dumps({"type": "result", "is_error": self.is_error, "result": self.text,
                                      "usage": {"input_tokens": 12, "output_tokens": 34}}), ""


@pytest.fixture
def spy_run(monkeypatch):
    """Replaces claude_code.run (the one isolated runner): records the model, effort, system prompt and output cap of each call, starts no
    process, and answers with `spy.reply` in a claude envelope."""
    class Spy:
        reply, seen = FILE_TEXT, []

    spy = Spy()
    spy.seen = []

    def fake_run(prompt, cwd, claude_cmd, timeout_s, env, *, model, effort, system_prompt=None, max_output_tokens=cc.MAX_OUTPUT_TOKENS):
        spy.seen.append({"model": model, "effort": effort, "system_prompt": system_prompt, "max_output_tokens": max_output_tokens,
                         "prompt": prompt, "cwd_entries": os.listdir(cwd), "env": dict(env)})
        return 0, json.dumps({"type": "result", "is_error": False, "result": spy.reply}), ""

    monkeypatch.setattr(cc, "run", fake_run)
    return spy


@pytest.fixture
def no_process(monkeypatch):
    """Any process start fails the test."""
    def refuse(*args, **kwargs):
        raise AssertionError("a process was started")
    monkeypatch.setattr(subprocess, "run", refuse)


class RecordingClient:
    """Stands in for the OpenRouter client: records (role, kind) for each call and answers with the queued texts."""

    def __init__(self, *texts):
        self.texts, self.calls, self.requests, self.failed_attempts, self.retry_log = list(texts), [], [], [], []
        self.budget = oc.JobBudget(40, 0.05)

    def call(self, role, system, user, kind="text", effort=None, **kwargs):
        self.calls.append((role, kind))
        self.requests.append({"model": oc.resolve(role), "status": 200, "failed": False, "seconds": 0.5, "finish_reason": "stop"})
        return self.texts.pop(0), {"model": oc.resolve(role)}


def production(root, client, claude_ask=None, repair_ask=None):
    job = load(root, p1b_job())
    history = root / "_loop" / "calc" / "history"
    history.mkdir(parents=True, exist_ok=True)
    models = pm.Production(job, root, client, FakeSandbox({}), protected=[], acceptance_lock=gates.hash_paths(root, []), originals={},
                           parse_files=lambda text: ({"pieces/calc.py": "x = 1\n"} if oc.FILE_BLOCK.search(text) else {}, []),
                           check_path=lambda rel: None, content_problem=lambda rel, content: None, history=history,
                           job_dir=root / "_loop" / "calc", repair_ask=repair_ask, claude_ask=claude_ask)
    return models, history


def with_runner(runner):
    """A claude_ask for Production: claude_code.ask_role with an injected runner."""
    return lambda role, system, user, kind, raw_sink=None: cc.ask_role(role, system, user, kind, raw_sink=raw_sink, runner=runner)


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


# ---------------------------------------------------------------- 1. one place: the roster says how each role is called

def test_todays_roster_names_repair_and_planner_as_claude_code_opus_at_medium_and_the_rest_as_openrouter(profile):
    profile("production")
    state = oc.roster_state()
    assert state["VIA"] == {"writer_a": "openrouter", "writer_b": "openrouter", "reviewer_a": "openrouter", "reviewer_b": "openrouter",
                            "repair": "claude_code", "planner": "claude_code"}
    for role in ("repair", "planner"):
        assert (state["ROLE_SPECS"][role]["model"], state["ROLE_SPECS"][role]["effort"]) == ("opus", "medium")
    test = oc.roster_state("test")
    assert set(test["VIA"].values()) == {"openrouter"}
    assert test["ROLES"]["reviewer_a"] == "non_author_writer" and test["ROLES"]["reviewer_b"] == "nvidia/nemotron-3.5-super-vl:free"
    assert test["ROLES"]["repair"] == "off" and test["ROLES"]["planner"] == "fake"


def test_a_role_with_no_via_is_an_openrouter_role():
    for profile in ("test", "production"):
        for role, spec in file_roster()["profiles"][profile]["roles"].items():
            if "via" not in spec:
                assert oc.via_of(spec) == "openrouter" and oc.roster_state(profile)["VIA"][role] == "openrouter"


def test_an_unknown_via_is_refused_when_the_roster_loads(roster):
    path = roster({("production", "writer_a"): dict(file_roster()["profiles"]["production"]["roles"]["writer_a"], via="api")})
    with pytest.raises(oc.RosterRefused, match="via"):
        oc.load_roster_file(path)


@pytest.mark.parametrize("change,why", [
    ({"model": "gpt-9"}, "model must be one of"),
    ({"effort": "extreme"}, "effort"),
    ({"effort": None}, "effort"),
    ({"provider": {"name": "Z.AI", "slug": "z-ai"}}, "provider"),
    ({"pricing": {"prompt": 1e-07, "completion": 1e-07}}, "pricing"),
    ({"model": "off"}, "not a model"),
])
def test_a_claude_code_role_needs_a_cli_model_and_effort_and_no_openrouter_fields(roster, change, why):
    path = roster({("production", "repair"): dict(claude_role("sonnet", "high"), **change)})
    with pytest.raises(oc.RosterRefused, match=why):
        oc.load_roster_file(path)


def test_an_effort_on_an_openrouter_role_is_refused(roster):
    path = roster({("production", "writer_a"): dict(file_roster()["profiles"]["production"]["roles"]["writer_a"], effort="high")})
    with pytest.raises(oc.RosterRefused, match="effort belongs to a claude_code role"):
        oc.load_roster_file(path)


def test_the_repair_and_planner_modules_name_no_model_and_build_no_claude_command():
    for name in ("opus_repair.py", "plan_job.py"):
        text = (REPO / "scripts" / "source" / name).read_text(encoding="utf-8")
        assert not any(f'"{m}"' in text for m in cc.MODELS), name
        assert "MODEL, EFFORT" not in text and "--strict-mcp-config" not in text and "subprocess.run(" not in text, name


def test_changing_the_roster_alone_changes_every_role_model(roster, profile, spy_run, repo):
    profile("production")
    roster({("production", "repair"): claude_role("sonnet", "low"), ("production", "planner"): claude_role("fable", "xhigh"),
            ("production", "writer_a"): claude_role("haiku", "max")})
    assert opus.repair_model() == ("sonnet", "low")
    opus.ask("repair this", runner=None)
    spy_run.reply = json.dumps(good_answer())
    pj.plan(root=repo, job_name="calc", plan_dir=repo / "_loop" / "calc" / "plan", request="r", files=["pieces/calc.py"],
            claude_cmd=[sys.executable, str(FAKE)])
    spy_run.reply = FILE_TEXT
    cc.ask_role("writer_a", "system", "user", "writer")
    assert [(s["model"], s["effort"]) for s in spy_run.seen] == [("sonnet", "low"), ("fable", "xhigh"), ("haiku", "max")]


# ---------------------------------------------------------------- 2. the test profile: Claude Code only as haiku at effort high

@pytest.mark.parametrize("model,effort", [pair for pair in ALL_PAIRS if pair != cc.TEST_ONLY])
def test_under_the_test_profile_any_other_claude_code_model_or_effort_is_refused_when_the_roster_loads(roster, model, effort):
    path = roster({("test", "repair"): claude_role(model, effort)})
    with pytest.raises(oc.RosterRefused, match="test profile allows a claude_code role only as haiku at effort high"):
        oc.load_roster_file(path)


@pytest.mark.parametrize("role", oc.ROLE_NAMES)
def test_under_the_test_profile_haiku_at_high_loads_for_every_role_and_sonnet_does_not(roster, role):
    path = roster({("test", role): claude_role("haiku", "high")})
    assert oc.load_profile("test", path=path)[role]["model"] == "haiku"
    path = roster({("test", role): claude_role("sonnet", "high")})
    with pytest.raises(oc.RosterRefused):
        oc.load_profile("test", path=path)


def test_a_refused_test_roster_makes_no_call_of_any_kind(roster, profile, no_process, repo):
    profile("test")
    roster({("test", "writer_a"): claude_role("opus", "high"), ("test", "repair"): claude_role("haiku", "medium")})
    runner = Runner(FILE_TEXT)
    with pytest.raises(oc.RosterRefused):
        cc.ask_role("writer_a", "system", "user", "writer", runner=runner)
    with pytest.raises(opus.RepairFailed, match="refused"):
        opus.ask("prompt", runner=runner, enabled=True)
    planned = pj.plan(root=repo, job_name="calc", plan_dir=repo / "_loop" / "calc" / "plan", request="r", files=["pieces/calc.py"], runner=runner)
    assert planned["state"] == "planner-refused"
    assert runner.runs == []


def test_the_test_profile_still_refuses_a_paid_role_and_keeps_review_usd_zero(roster):
    path = roster({("test", "writer_a"): claude_role("haiku", "high", paid=True)})
    with pytest.raises(oc.RosterRefused, match="paid"):
        oc.load_profile("test", path=path)
    assert p1b.budget_for("test")["review_usd"] == 0


# ---------------------------------------------------------------- 3. production: any model and effort; the switch still gates opus

@pytest.mark.parametrize("model,effort", ALL_PAIRS)
def test_under_production_every_claude_code_model_and_effort_loads(roster, model, effort):
    path = roster({("production", "repair"): claude_role(model, effort), ("production", "writer_a"): claude_role(model, effort)})
    roles = oc.load_profile("production", path=path)
    assert (roles["writer_a"]["model"], roles["writer_a"]["effort"]) == (model, effort)


@pytest.mark.parametrize("role", oc.ROLE_NAMES)
def test_under_production_any_role_may_be_claude_code(roster, profile, role):
    profile("production")
    roster({("production", role): claude_role("sonnet", "max")})
    assert oc.roster_state()["VIA"][role] == "claude_code"
    with pytest.raises(oc.ModelRefused, match="not called through OpenRouter"):
        oc.resolve(role)                                      # never sent to OpenRouter
    assert "sonnet" not in oc.ALLOWED | oc.PAID and "sonnet" not in oc.SETTINGS
    if role in oc.WRITER_ROLES + oc.REVIEWER_ROLES:
        assert role not in oc.probe_roles()


def test_the_owner_switch_gates_opus_and_only_opus(roster, profile, monkeypatch):
    profile("production")
    roster({("production", "writer_a"): claude_role("opus", "high"), ("production", "writer_b"): claude_role("sonnet", "high")})
    runner = Runner(FILE_TEXT)
    with pytest.raises(cc.ClaudeFailed, match="switch"):
        cc.ask_role("writer_a", "system", "user", "writer", runner=runner)
    assert runner.runs == []
    assert cc.ask_role("writer_b", "system", "user", "writer", runner=runner) == FILE_TEXT      # sonnet: not gated
    monkeypatch.setattr(opus, "OPUS_ENABLED", True)
    assert cc.ask_role("writer_a", "system", "user", "writer", runner=runner) == FILE_TEXT
    assert len(runner.runs) == 2


def test_a_repair_on_sonnet_runs_with_the_switch_off_and_opus_does_not(roster, profile, spy_run):
    profile("production")
    with pytest.raises(opus.RepairFailed, match="switched off"):
        opus.ask("prompt")                                    # today's roster: opus, switch off
    assert spy_run.seen == []
    roster({("production", "repair"): claude_role("sonnet", "low")})
    assert opus.ask("prompt") == FILE_TEXT
    (seen,) = spy_run.seen
    assert (seen["model"], seen["effort"], seen["system_prompt"], seen["max_output_tokens"]) == ("sonnet", "low", opus.REPAIR_SYSTEM, 32000)


def test_a_real_claude_never_starts_under_pytest_for_a_writer_or_reviewer_role(roster, profile, monkeypatch, no_process):
    profile("production")
    roster({("production", "writer_a"): claude_role("sonnet", "high"), ("production", "reviewer_b"): claude_role("opus", "high")})
    monkeypatch.setattr(opus, "OPUS_ENABLED", True)
    monkeypatch.setattr(cc, "CLAUDE_CMD", ["claude"])
    for role, kind in (("writer_a", "writer"), ("reviewer_b", "reviewer")):
        with pytest.raises(cc.ClaudeFailed, match="refused under pytest"):
            cc.ask_role(role, "system", "user", kind)


def test_satvik_no_opus_refuses_a_real_claude_and_the_fake_claude_is_the_one_exception(monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("SATVIK_NO_OPUS", raising=False)
    assert cc.real_call_refused(["claude"]) is False
    monkeypatch.setenv("SATVIK_NO_OPUS", "1")
    assert cc.real_call_refused(["claude"]) is True
    assert cc.real_call_refused([sys.executable, str(FAKE)]) is False
    assert opus.real_call_refused is cc.real_call_refused


# ---------------------------------------------------------------- 4. one isolated runner for every Claude Code call

def test_a_writer_on_claude_code_is_isolated_exactly_like_the_repair(roster, profile, tmp_path, monkeypatch):
    profile("production")
    roster({("production", "writer_a"): claude_role("sonnet", "high")})
    log, output = tmp_path / "fake.log", tmp_path / "out.json"
    output.write_text(json.dumps({"type": "result", "is_error": False, "result": FILE_TEXT}), encoding="utf-8")
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    monkeypatch.setenv("FAKE_CLAUDE_OUTPUT", str(output))
    monkeypatch.setattr(cc, "CLAUDE_CMD", [sys.executable, str(FAKE)])
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "OPENROUTER_FREE_KEY", "OPENROUTER_PAID_KEY"):
        monkeypatch.setenv(name, "sk-test-not-real")
    assert cc.ask_role("writer_a", "THE SYSTEM PROMPT", "THE USER PROMPT", "writer") == FILE_TEXT
    (run,) = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    args = run["args"]
    assert args[:8] == ["-p", "--model", "sonnet", "--effort", "high", "--output-format", "json", "--tools"] and args[8] == ""
    assert args[args.index("--mcp-config") + 1] and "--strict-mcp-config" in args and "--no-session-persistence" in args
    assert args[args.index("--system-prompt") + 1] == "THE SYSTEM PROMPT"
    assert run["stdin"] == "THE USER PROMPT" and run["cwd_entries"] == []
    assert run["guarded_env"] == [] and run["api_key_in_env"] is False


def test_repair_and_planner_go_through_the_same_runner_with_the_roster_model(profile, spy_run, monkeypatch, tmp_path):
    profile("production")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")
    opus.ask("repair prompt", enabled=True)
    pj.claude_runner("plan prompt", str(tmp_path), [sys.executable, str(FAKE)], 5)
    repair, planner = spy_run.seen
    assert (repair["model"], repair["effort"], repair["max_output_tokens"]) == ("opus", "medium", 32000)
    assert (planner["model"], planner["effort"], planner["system_prompt"], planner["max_output_tokens"]) == ("opus", "medium", None, None)
    assert repair["cwd_entries"] == [] and "ANTHROPIC_API_KEY" not in repair["env"] and "ANTHROPIC_API_KEY" not in planner["env"]


# ---------------------------------------------------------------- 5. the same reply checks and records; nothing on the OpenRouter ledger

def test_a_writer_on_claude_code_passes_the_writer_check_and_writes_the_same_record_with_its_via(roster, profile, tmp_path):
    profile("production")
    roster({("production", "writer_a"): claude_role("sonnet", "high")})
    root = wp_h_root(tmp_path)
    client = RecordingClient()
    runner = Runner(FILE_TEXT)
    models, history = production(root, client, claude_ask=with_runner(runner))
    reply = models.call("sonnet", "write", FILES)
    assert reply == {"pieces/calc.py": "x = 1\n"} and len(runner.runs) == 1
    record = read(history / "call_01.json")
    assert record["model"] == "sonnet" and record["via"] == "claude_code" and record["mode"] == "write"
    assert record["status"] is None and record["failed"] is False and record["prompt_tokens"] == 12 and record["completion_tokens"] == 34
    assert (history / "reply_01.txt").read_text(encoding="utf-8") == FILE_TEXT
    assert client.calls == [] and client.requests == [] and client.budget.free_used == 0     # not on the OpenRouter ledger


def test_a_writer_reply_on_claude_code_with_no_file_block_is_a_failed_call_on_record(roster, profile, tmp_path):
    profile("production")
    roster({("production", "writer_a"): claude_role("sonnet", "high")})
    root = wp_h_root(tmp_path)
    models, history = production(root, RecordingClient(), claude_ask=with_runner(Runner("I would rather explain the fix in words.")))
    with pytest.raises(p1b.ModelFailed, match="FILE"):
        models.call("sonnet", "write", FILES)
    failed = read(history / "failed_call_01.json")
    assert failed["model"] == "sonnet" and failed["call"] == "write" and failed["status"] is None
    record = read(history / "call_01.json")
    assert record["via"] == "claude_code" and record["failed"] is True and record["reply_file"] == "history/reply_01.txt"


@pytest.mark.parametrize("text,ok", [(READY, True), ("Looks fine to me.", False), ("## Verdict: READY\nAnd one more thing.", False)])
def test_a_reviewer_on_claude_code_needs_the_final_verdict_line(roster, profile, tmp_path, text, ok):
    profile("production")
    roster({("production", "reviewer_b"): claude_role("haiku", "high")})
    root = wp_h_root(tmp_path)
    models, history = production(root, RecordingClient(), claude_ask=with_runner(Runner(text)))
    if ok:
        assert models.call("haiku", "review", FILES) == READY
    else:
        with pytest.raises(p1b.ModelFailed, match="Verdict"):
            models.call("haiku", "review", FILES)
    record = read(history / "call_01.json")
    assert record["via"] == "claude_code" and record["mode"] == "review" and record["failed"] is (not ok)
    assert (history / "review_01.txt").read_text(encoding="utf-8") == text       # kept even when it has no verdict line


def test_an_error_flag_or_a_usage_limit_on_claude_code_is_a_failed_call_and_the_model_is_down(roster, profile, tmp_path):
    profile("production")
    roster({("production", "writer_a"): claude_role("sonnet", "high")})
    root = wp_h_root(tmp_path)
    for runner in (Runner("boom", is_error=True), Runner("Claude usage limit reached.")):
        models, history = production(root, RecordingClient(), claude_ask=with_runner(runner))
        with pytest.raises(p1b.ModelFailed):
            models.call("sonnet", "write", FILES)


def test_an_openrouter_call_record_says_via_openrouter(profile, tmp_path):
    profile("production")
    root = wp_h_root(tmp_path)
    models, history = production(root, RecordingClient(FILE_TEXT))
    models.call(oc.ROLES["writer_a"], "write", FILES)
    assert read(history / "call_01.json")["via"] == "openrouter"


def test_a_job_with_a_claude_code_writer_passes_end_to_end_and_only_the_openrouter_reviews_are_on_the_ledger(roster, profile, tmp_path, monkeypatch):
    """fix_loop.run_job with the fake claude as writer_a (sonnet at high), the fake OpenRouter transport for North and GLM, and the fake sandbox."""
    profile("production")
    roster({("production", "writer_a"): claude_role("sonnet", "high")})
    home = tmp_path / "satvik_home"
    home.mkdir()
    monkeypatch.setenv("SATVIK_HOME", str(home))
    log, output = tmp_path / "fake.log", tmp_path / "out.json"
    output.write_text(json.dumps({"type": "result", "is_error": False,
                                  "result": ph.file_reply(("pieces/calc.py", GOOD_CODE), ("pieces/test_calc.py", GOOD_TEST))}), encoding="utf-8")
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    monkeypatch.setenv("FAKE_CLAUDE_OUTPUT", str(output))
    monkeypatch.setattr(cc, "CLAUDE_CMD", [sys.executable, str(FAKE)])
    north, glm = oc.ROLES["reviewer_b"], oc.ROLES["reviewer_a"]
    provider = ph.Provider({north: [ph.READY], glm: [ph.READY]})
    root = wp_h_root(tmp_path)
    result = fl.run_job(load(root, p1b_job()), root, sandbox=FakeSandbox({}), openrouter=ph.client(tmp_path, provider, budget=oc.JobBudget(40, 0.05)),
                        scrub_keys=[])
    assert result["state"] == "passed", result
    assert provider.models() == [north, glm]                      # the writer never reached OpenRouter
    assert len(log.read_text(encoding="utf-8").splitlines()) == 1
    history = root / "_loop" / "calc" / "history"
    assert [read(history / f"call_0{n}.json")["via"] for n in (1, 2, 3)] == ["claude_code", "openrouter", "openrouter"]
    assert read(root / "_loop" / "calc" / "lineage.json")["pieces/calc.py"]["origin"] == "sonnet"
    assert result["evidence"]["budget"]["free_requests_used"] == 1     # North only (GLM is paid); the Claude Code writer reserves nothing
    assert sl.usage(home)["free_today"] == 1


# ---------------------------------------------------------------- 6. the review panel stays independent; the sentinels still work

def _specs(**roles):
    specs = {role: dict(spec) for role, spec in oc.load_profile("production").items()}
    specs.update(roles)
    return specs


def test_a_claude_code_reviewer_on_the_same_model_as_the_author_is_never_on_its_panel(profile):
    profile("production")
    specs = _specs(writer_a=claude_role("sonnet", "high"), reviewer_b=claude_role("sonnet", "low"))
    xing, glm = specs["writer_b"]["model"], specs["reviewer_a"]["model"]
    assert p1b.panel_for("sonnet", specs=specs) == [xing, glm]           # the author's own model is replaced by the writer that did not write it
    assert p1b.panel_for(xing, specs=specs) == ["sonnet", glm]
    authors = ["sonnet", xing]
    for origin in authors:
        for down in [(), *[(m,) for m in authors]]:
            try:
                panel = p1b.panel_for(origin, unavailable=down, specs=specs)
            except oc.ProviderUnavailable:
                continue
            assert origin not in panel and len(set(panel)) == len(panel)


def test_the_test_profile_panel_keeps_the_non_author_writer_and_nemotron_with_a_claude_code_writer(roster, profile):
    profile("test")
    roster({("test", "writer_a"): claude_role("haiku", "high")})
    roles = oc.ROLES
    assert roles["reviewer_a"] == "non_author_writer" and roles["reviewer_b"] == "nvidia/nemotron-3.5-super-vl:free"
    assert p1b.panel_for("haiku") == [roles["reviewer_b"], roles["writer_b"]]
    assert p1b.panel_for(roles["writer_b"]) == [roles["reviewer_b"], "haiku"]


def test_a_paid_claude_code_reviewer_is_never_replaced_and_its_failure_has_no_substitute(roster, profile):
    profile("production")
    roster({("production", "reviewer_a"): claude_role("opus", "high", paid=True)})
    dots, north = oc.ROLES["writer_a"], oc.ROLES["reviewer_b"]
    assert p1b.panel_for(dots) == [north, "opus"]
    models = FakeModels({(dots, "write"): [full()], (north, "review"): [READY], ("opus", "review"): [p1b.ModelFailed("switched off")]}, [green()])
    outcome = p1b.walk(JOB, models, p1b.Lineage(), lambda: 0.0)
    assert outcome.status == "provider-unavailable" and "paid review" in outcome.reason
    assert models.calls(mode="review") == [("review", north), ("review", "opus")]       # no backup took the paid slot


def test_repair_off_makes_no_call_and_escalates_with_the_fixed_reason(roster, profile, tmp_path):
    profile("production")
    roster({("production", "repair"): dict(file_roster()["profiles"]["test"]["roles"]["repair"])})     # model off, as under test
    runner = Runner(FILE_TEXT)
    with pytest.raises(opus.RepairFailed, match="no repair step"):
        opus.ask("prompt", runner=runner, enabled=True)
    assert runner.runs == []
    models, _ = production(wp_h_root(tmp_path), RecordingClient())
    with pytest.raises(p1b.ModelFailed) as caught:
        models.call(p1b.ANTHROPIC, "repair", FILES)
    assert caught.value.reason == opus.UNAVAILABLE


def test_a_planner_that_is_off_or_fake_refuses_with_no_call(roster, profile, repo):
    profile("production")
    runner = Runner("{}")
    roster({("production", "planner"): dict(file_roster()["profiles"]["test"]["roles"]["repair"])})     # model off
    result = pj.plan(root=repo, job_name="calc", plan_dir=repo / "_loop" / "calc" / "plan", request="r", files=["pieces/calc.py"], runner=runner)
    assert result["state"] == "planner-refused" and result["reason"] == "the production profile has no planner (planner=off)"
    assert runner.runs == []


# ---------------------------------------------------------------- 7. repair and planner on OpenRouter; planner on haiku under test

def test_a_repair_role_on_openrouter_is_one_adapter_repair_call(roster, profile, tmp_path, no_process):
    profile("production")
    xing_spec = file_roster()["profiles"]["production"]["roles"]["writer_b"]
    roster({("production", "repair"): dict(xing_spec)})
    client = RecordingClient(FILE_TEXT)
    models, history = production(wp_h_root(tmp_path), client)
    assert models.call(p1b.ANTHROPIC, "repair", FILES) == {"pieces/calc.py": "x = 1\n"}
    assert client.calls == [("repair", "repair")]
    assert read(history / "call_01.json")["via"] == "openrouter" and models.repair_calls == 1


def test_a_planner_on_openrouter_is_one_checked_text_call_kept_as_the_planner_answer(roster, profile, repo):
    profile("production")
    roster({("production", "planner"): dict(file_roster()["profiles"]["production"]["roles"]["writer_b"])})
    client = RecordingClient(json.dumps(good_answer()))
    plan_dir = repo / "_loop" / "calc" / "plan"
    result = pj.plan(root=repo, job_name="calc", plan_dir=plan_dir, request="r", files=["pieces/calc.py"], client=client)
    assert result["state"] == "planned", result
    assert client.calls == [("planner", "text")]
    intent = read(plan_dir / "INTENT.json")
    assert intent["via"] == "openrouter" and intent["model"] == oc.ROLES["planner"] and intent["effort"] is None
    assert read(plan_dir / "raw_response.json")["via"] == "openrouter"


def test_a_haiku_planner_under_the_test_profile_runs_through_the_isolated_runner(roster, profile, spy_run, repo):
    profile("test")
    roster({("test", "planner"): claude_role("haiku", "high")})
    answer = good_answer()
    answer["job"]["budget"]["review_usd"] = 0                     # the test profile's budget
    spy_run.reply = json.dumps(answer)
    plan_dir = repo / "_loop" / "calc" / "plan"
    result = pj.plan(root=repo, job_name="calc", plan_dir=plan_dir, request="r", files=["pieces/calc.py"], claude_cmd=[sys.executable, str(FAKE)])
    assert result["state"] == "planned", result
    assert [(s["model"], s["effort"]) for s in spy_run.seen] == [("haiku", "high")]
    intent = read(plan_dir / "INTENT.json")
    assert (intent["via"], intent["model"], intent["effort"]) == ("claude_code", "haiku", "high")
