"""P1c (2026-11-02, Revision 3): the Opus planner. plan_job.plan runs ONE plain `claude -p` call per job, writes plan/INTENT.json before it,
keeps the raw answer in plan/raw_response.json, reuses a saved answer on restart, and never retries. Tests use a fake `claude` executable
(pieces/fake_claude.py); nothing here calls a model, and the subscription is never used."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "source"))
import plan_job as pj  # noqa: E402

FAKE = str(REPO / "pieces" / "fake_claude.py")
ACCEPT = "tests/acceptance/test_calc_acceptance.py"
CONTROL = "tests/controls/calc_returns_none.py"


def good_answer(**over):
    """The planner's answer for a valid production job (the `result` text of the claude envelope)."""
    job = {"name": "calc", "profile": "production", "visibility": "public", "spec": "specs/calc.md",
           "files": ["pieces/calc.py", "pieces/test_calc.py"], "tests": ["pieces/test_calc.py"], "reference": [],
           "review": True, "reviewers": "by_lineage", "review_prompt": "docs/RP.md", "fixers": ["writer_a", "writer_b"],
           "budget": {"free_requests": 40, "paid_usd": 0.15}, "deadline_min": 60,
           "acceptance_tests": [ACCEPT],
           "negative_controls": [{"name": "returns None", "replace": {"pieces/calc.py": CONTROL}, "must_fail": [ACCEPT + "::test_sums_two_prices"]}]}
    answer = {"plan_md": "# Plan" + chr(10) + "Sum the prices.",
              "spec_md": "# Spec" + chr(10) + "- KNOWN: calculate_total(prices) returns the sum of the prices." + chr(10)
                         + "- ASSUMED: prices is a list of integers." + chr(10),
              "job": job,
              "acceptance": {ACCEPT: "def test_sums_two_prices():" + chr(10) + "    assert True" + chr(10)},
              "controls": {CONTROL: "def calculate_total(prices):" + chr(10) + "    return None" + chr(10)},
              "expected_lines": {"pieces/calc.py": 10, "pieces/test_calc.py": 20}}
    answer.update(over)
    return answer


def envelope(answer=None, text=None, **extra):
    """What `claude -p --output-format json` prints: one JSON object whose `result` holds the answer text."""
    body = {"type": "result", "subtype": "success", "is_error": False,
            "result": text if text is not None else json.dumps(answer), "session_id": "test-session"}
    body.update(extra)
    return json.dumps(body)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    (root / "docs").mkdir(parents=True)
    (root / "pieces").mkdir()
    (root / "docs" / "RP.md").write_text("review", encoding="utf-8")
    (root / "pieces" / "calc.py").write_text("def calculate_total(prices):" + chr(10) + "    return sum(prices)" + chr(10), encoding="utf-8")
    return root


def fake_run(tmp_path, monkeypatch, output_text):
    """Points the planner at the fake claude: `output_text` is what the fake prints (None = it sleeps). Returns the log path."""
    out = tmp_path / "fake_output.json"
    if output_text is None:
        monkeypatch.setenv("FAKE_CLAUDE_OUTPUT", "sleep")
    else:
        out.write_text(output_text, encoding="utf-8")
        monkeypatch.setenv("FAKE_CLAUDE_OUTPUT", str(out))
    log = tmp_path / "fake_claude.log"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    return log


def calls(log):
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


def plan(root, log_tmp=None, **over):
    plan_dir = root / "_loop" / "calc" / "plan"
    kwargs = dict(request="Write calculate_total: the sum of the prices.", files=["pieces/calc.py"], claude_cmd=[sys.executable, FAKE],
                  timeout_s=5)
    kwargs.update(over)
    return pj.plan(root=root, job_name="calc", plan_dir=plan_dir, **kwargs)


# ---------------------------------------------------------------- one call, intent first, raw answer kept

def test_one_call_intent_before_it_and_the_raw_answer_is_kept(repo, tmp_path, monkeypatch):
    raw = envelope(good_answer())
    log = fake_run(tmp_path, monkeypatch, raw)
    result = plan(repo)
    assert result["state"] == "planned", result
    assert len(calls(log)) == 1
    plan_dir = repo / "_loop" / "calc" / "plan"
    assert (plan_dir / "raw_response.json").read_text(encoding="utf-8") == raw
    intent = json.loads((plan_dir / "INTENT.json").read_text(encoding="utf-8"))
    assert intent["job"] == "calc" and intent["model"] == "opus" and intent["effort"] == "medium"


def test_intent_exists_before_the_call_is_made(repo, tmp_path, monkeypatch):
    seen = []
    plan_dir = repo / "_loop" / "calc" / "plan"

    def runner(prompt, cwd, claude_cmd, timeout_s):
        seen.append((plan_dir / "INTENT.json").is_file())
        return 0, envelope(good_answer())

    pj.plan(root=repo, job_name="calc", plan_dir=plan_dir, request="r", files=["pieces/calc.py"], runner=runner)
    assert seen == [True]


def test_restart_reuses_the_saved_answer_and_makes_no_second_call(repo, tmp_path, monkeypatch):
    log = fake_run(tmp_path, monkeypatch, envelope(good_answer()))
    first = plan(repo)
    second = plan(repo)
    assert first["state"] == second["state"] == "planned"
    assert len(calls(log)) == 1, "a restart must reuse the answer, not call the planner again"


def test_a_crash_after_the_intent_is_planner_uncertain_and_never_retried(repo, tmp_path, monkeypatch):
    plan_dir = repo / "_loop" / "calc" / "plan"
    count = []

    def crash(prompt, cwd, claude_cmd, timeout_s):
        count.append(1)
        raise KeyboardInterrupt                      # the process dies while the call is out: no answer is saved

    with pytest.raises(KeyboardInterrupt):
        pj.plan(root=repo, job_name="calc", plan_dir=plan_dir, request="r", files=["pieces/calc.py"], runner=crash)
    assert (plan_dir / "INTENT.json").is_file() and not (plan_dir / "raw_response.json").exists()
    again = pj.plan(root=repo, job_name="calc", plan_dir=plan_dir, request="r", files=["pieces/calc.py"], runner=crash)
    assert again["state"] == "planner-uncertain"
    assert len(count) == 1, "the restart must not call the planner"


def test_a_timed_out_call_is_planner_uncertain(repo, tmp_path, monkeypatch):
    log = fake_run(tmp_path, monkeypatch, None)
    result = plan(repo, timeout_s=1)
    assert result["state"] == "planner-uncertain"
    assert len(calls(log)) == 1
    assert plan(repo, timeout_s=1)["state"] == "planner-uncertain" and len(calls(log)) == 1


# ---------------------------------------------------------------- how it is called

def test_the_command_is_plain_no_tools_empty_mcp_from_an_empty_folder_without_the_api_key(repo, tmp_path, monkeypatch):
    log = fake_run(tmp_path, monkeypatch, envelope(good_answer()))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-real")          # the subscription must be used, never an API key
    plan(repo)
    (call,) = calls(log)
    args = call["args"]
    assert "--bare" not in args
    for flag in ("-p", "--strict-mcp-config"):
        assert flag in args
    assert args[args.index("--model") + 1] == "opus" and args[args.index("--effort") + 1] == "medium"
    assert args[args.index("--output-format") + 1] == "json"
    assert args[args.index("--tools") + 1] == ""                              # no tools
    assert args[args.index("--mcp-config") + 1] not in ("", None)            # the config file is the empty one
    assert call["cwd_entries"] == [], "the planner runs from an empty folder"
    assert Path(call["cwd"]).resolve() != repo.resolve()
    assert call["api_key_in_env"] is False
    assert "Write calculate_total" in call["stdin"]


def test_an_mcp_server_reported_by_the_session_is_refused(repo, tmp_path, monkeypatch):
    fake_run(tmp_path, monkeypatch, envelope(good_answer(), mcp_servers=[{"name": "github", "status": "connected"}]))
    result = plan(repo)
    assert result["state"] == "planner-refused" and "MCP" in result["reason"]
    assert (repo / "_loop" / "calc" / "plan" / "raw_response.json").is_file()


def test_a_plugin_reported_by_the_session_is_refused(repo, tmp_path, monkeypatch):
    fake_run(tmp_path, monkeypatch, envelope(good_answer(), plugins=[{"name": "legal"}]))
    assert plan(repo)["state"] == "planner-refused"


def test_more_than_thirty_files_is_refused_before_any_call(repo, tmp_path, monkeypatch):
    log = fake_run(tmp_path, monkeypatch, envelope(good_answer()))
    result = pj.plan(root=repo, job_name="calc", plan_dir=repo / "_loop" / "calc" / "plan", request="r",
                     files=[f"pieces/f{i}.py" for i in range(31)], claude_cmd=[sys.executable, FAKE], timeout_s=5)
    assert result["state"] == "planner-refused"
    assert calls(log) == []


# ---------------------------------------------------------------- what the answer must contain

def test_an_answer_that_is_not_json_is_planner_invalid(repo, tmp_path, monkeypatch):
    fake_run(tmp_path, monkeypatch, envelope(text="here is your plan, no json"))
    result = plan(repo)
    assert result["state"] == "planner-invalid"
    assert (repo / "_loop" / "calc" / "plan" / "raw_response.json").is_file()


def test_an_error_envelope_is_planner_failed(repo, tmp_path, monkeypatch):
    fake_run(tmp_path, monkeypatch, json.dumps({"type": "result", "is_error": True, "result": "usage limit reached"}))
    assert plan(repo)["state"] == "planner-failed"


def test_a_spec_line_without_a_KNOWN_or_ASSUMED_tag_is_refused(repo, tmp_path, monkeypatch):
    fake_run(tmp_path, monkeypatch, envelope(good_answer(spec_md="# Spec" + chr(10) + "- the sum must be right" + chr(10))))
    result = plan(repo)
    assert result["state"] == "planner-invalid" and "KNOWN or ASSUMED" in result["reason"]


def test_a_job_that_load_job_rules_refuse_is_refused(repo, tmp_path, monkeypatch):
    answer = good_answer()
    answer["job"]["reviewers"] = ["lightning", "codex"]                       # a reviewer list: production uses by_lineage
    fake_run(tmp_path, monkeypatch, envelope(answer))
    result = plan(repo)
    assert result["state"] == "planner-invalid" and "by_lineage" in result["reason"]


def test_a_legacy_fixer_name_is_refused(repo, tmp_path, monkeypatch):
    answer = good_answer()
    answer["job"]["fixers"] = ["nemotron"]
    fake_run(tmp_path, monkeypatch, envelope(answer))
    assert plan(repo)["state"] == "planner-invalid"


def test_a_job_without_a_negative_control_or_baseline_is_refused(repo, tmp_path, monkeypatch):
    answer = good_answer()
    answer["job"]["negative_controls"] = []
    fake_run(tmp_path, monkeypatch, envelope(answer))
    result = plan(repo)
    assert result["state"] == "planner-invalid" and "negative control" in result["reason"]


def test_a_unit_over_the_job_size_limits_is_refused(repo, tmp_path, monkeypatch):
    fake_run(tmp_path, monkeypatch, envelope(good_answer(expected_lines={"pieces/calc.py": 900, "pieces/test_calc.py": 20})))
    result = plan(repo)
    assert result["state"] == "planner-invalid" and "800" in result["reason"]


def test_a_unit_over_1500_lines_in_total_is_refused(repo, tmp_path, monkeypatch):
    fake_run(tmp_path, monkeypatch, envelope(good_answer(expected_lines={"pieces/calc.py": 780, "pieces/test_calc.py": 780})))
    result = plan(repo)
    assert result["state"] == "planner-invalid" and "1500" in result["reason"]


def test_acceptance_files_must_match_the_job_exactly(repo, tmp_path, monkeypatch):
    answer = good_answer(acceptance={})
    fake_run(tmp_path, monkeypatch, envelope(answer))
    assert plan(repo)["state"] == "planner-invalid"


# ---------------------------------------------------------------- what is written, and that the job is then fixed

def test_the_plan_folder_holds_the_plan_spec_job_and_files_and_the_job_is_read_only(repo, tmp_path, monkeypatch):
    fake_run(tmp_path, monkeypatch, envelope(good_answer()))
    result = plan(repo)
    plan_dir = repo / "_loop" / "calc" / "plan"
    assert "Sum the prices" in (plan_dir / "PLAN.md").read_text(encoding="utf-8")
    assert "KNOWN:" in (plan_dir / "SPEC.md").read_text(encoding="utf-8")
    job_path = plan_dir / "job.json"
    assert json.loads(job_path.read_text(encoding="utf-8"))["name"] == "calc"
    assert not os.access(job_path, os.W_OK), "the planned job is read-only"
    assert result["job_sha256"] == pj.file_sha256(job_path)
    assert (plan_dir / "files" / ACCEPT).is_file() and (plan_dir / "files" / CONTROL).is_file()
    assert result["assumed"] == ["prices is a list of integers."]


def test_the_job_file_passes_the_job_schema(repo, tmp_path, monkeypatch):
    import fix_loop as fl
    import jsonschema
    fake_run(tmp_path, monkeypatch, envelope(good_answer()))
    plan(repo)
    job = json.loads((repo / "_loop" / "calc" / "plan" / "job.json").read_text(encoding="utf-8"))
    jsonschema.validate(instance=job, schema=fl.JOB_SCHEMA)


def test_the_planner_never_names_a_legacy_model_family():
    text = (REPO / "scripts" / "source" / "plan_job.py").read_text(encoding="utf-8").lower()
    for word in ("nemotron", "codex", "haiku", "groq", "sonnet"):
        assert word not in text, word


def test_load_job_accepts_the_planned_job_once_its_files_are_in_the_repo(repo, tmp_path, monkeypatch):
    import fix_loop as fl
    fake_run(tmp_path, monkeypatch, envelope(good_answer()))
    plan(repo)
    plan_dir = repo / "_loop" / "calc" / "plan"
    (repo / "specs").mkdir()
    (repo / "specs" / "calc.md").write_text((plan_dir / "SPEC.md").read_text(encoding="utf-8"), encoding="utf-8")
    for rel in (ACCEPT, CONTROL):
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text((plan_dir / "files" / rel).read_text(encoding="utf-8"), encoding="utf-8")
    jobs = repo / "_loop" / "jobs"
    jobs.mkdir(parents=True, exist_ok=True)
    (jobs / "calc.json").write_text((plan_dir / "job.json").read_text(encoding="utf-8"), encoding="utf-8")
    job = fl.load_job(jobs / "calc.json")
    assert job["profile"] == "production" and job["reviewers"] == "by_lineage"
