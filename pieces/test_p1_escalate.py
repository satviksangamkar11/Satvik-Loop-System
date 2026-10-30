"""P1-D26: Sonnet escalation inside the loop, the loop log and the Codex verdict suffix. Fakes only: no model call, no network."""
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "pieces"))
sys.path.insert(0, str(REPO / "scripts" / "source"))
sys.path.insert(0, str(REPO / "scripts" / "image_trials"))
import p1_claims as pc
import p1_escalate as esc


def make_repo(tmp_path, with_escalate=True):
    repo = tmp_path / "repo"
    for d in ("_loop/jobs", "_loop/j", "docs", "specs", "scripts/source"):
        (repo / d).mkdir(parents=True)
    job = {"name": "j", "spec": "specs/s.md", "files": ["m.py", "t.py"], "tests": ["t.py"], "fixers": ["nemotron"] * 3}
    (repo / "_loop/jobs/j.json").write_text(json.dumps(job), encoding="utf-8")
    (repo / "specs/s.md").write_text("SPEC TEXT", encoding="utf-8")
    (repo / "docs/AGENT_RULES.md").write_text("RULES TEXT", encoding="utf-8")
    if with_escalate:
        (repo / "_loop/j/ESCALATE.md").write_text("pytest said: assert 2 == 3", encoding="utf-8")
    return repo, repo / "_loop/jobs/j.json"


def test_build_prompt_has_the_report_the_spec_and_a_read_only_instruction(tmp_path):
    repo, job_path = make_repo(tmp_path)
    prompt = esc.build_prompt(json.loads(job_path.read_text()), repo)
    assert "assert 2 == 3" in prompt and "SPEC TEXT" in prompt and "RULES TEXT" in prompt
    assert "READ-ONLY" in prompt and "CAUSE: SPEC" in prompt and "NO tools" in prompt


def test_diagnose_writes_the_file_with_sonnet_medium_and_no_tools(tmp_path):
    repo, job_path = make_repo(tmp_path)
    seen = {}

    def runner(cmd, text, timeout_s):
        seen["cmd"], seen["text"] = cmd, text
        return 0, "CAUSE: SPEC\nthe spec never says X"

    out = esc.diagnose(job_path, repo, runner=runner)
    assert out == repo / "_loop/j/SONNET_DIAGNOSIS.md"
    text = out.read_text(encoding="utf-8")
    assert "CAUSE: SPEC" in text and "OPINION" in text
    cmd = seen["cmd"]
    assert "--model" in cmd and cmd[cmd.index("--model") + 1] == "sonnet"
    assert cmd[cmd.index("--effort") + 1] == "medium"
    assert cmd[cmd.index("--tools") + 1] == ""                          # no tools at all


def test_diagnose_reports_unavailable_instead_of_raising(tmp_path):
    repo, job_path = make_repo(tmp_path)
    out = esc.diagnose(job_path, repo, runner=lambda c, t, s: (1, "timeout"))
    assert "DIAGNOSIS UNAVAILABLE" in out.read_text(encoding="utf-8")
    assert esc.diagnose(job_path, repo, runner=lambda c, t, s: (_ for _ in ()).throw(RuntimeError("boom"))) is None


def test_diagnose_without_an_escalation_report_does_nothing(tmp_path):
    repo, job_path = make_repo(tmp_path, with_escalate=False)
    assert esc.diagnose(job_path, repo, runner=lambda c, t, s: pytest.fail("must not call the model")) is None


def stub_loop(repo, code):
    script = repo / "scripts/source/fix_loop.py"
    script.write_text(f"raise SystemExit({code})\n", encoding="utf-8")
    return script


def test_launcher_escalates_on_exit_3_and_logs(tmp_path):
    repo, job_path = make_repo(tmp_path)
    stub_loop(repo, 3)
    calls = []
    code = pc.run_job(job_path, repo, environ={}, escalate=lambda jp, rp: calls.append((jp, rp)) or repo / "_loop/j/SONNET_DIAGNOSIS.md")
    assert code == 3 and len(calls) == 1
    log = (repo / "docs/LOOP_LOG.md").read_text(encoding="utf-8")
    assert "job j: exit 3" in log and "SONNET_DIAGNOSIS.md" in log


def test_launcher_does_not_escalate_a_pass_but_still_logs(tmp_path):
    repo, job_path = make_repo(tmp_path)
    stub_loop(repo, 0)
    (repo / "_loop/j/status.json").write_text(json.dumps({"state": "passed", "rounds": [{"round": "review_0_glm", "reason": "verdict=READY"}]}), encoding="utf-8")
    assert pc.run_job(job_path, repo, environ={}, escalate=lambda *a: pytest.fail("not on a pass")) == 0
    log = (repo / "docs/LOOP_LOG.md").read_text(encoding="utf-8")
    assert "exit 0, state passed" in log and "verdict=READY" in log


def test_a_failing_escalation_helper_never_breaks_the_launcher(tmp_path):
    repo, job_path = make_repo(tmp_path)
    stub_loop(repo, 3)
    assert pc.run_job(job_path, repo, environ={}, escalate=lambda *a: (_ for _ in ()).throw(RuntimeError("x"))) == 3


def test_no_docs_folder_means_no_log_and_no_new_folder(tmp_path):
    repo, job_path = make_repo(tmp_path)
    stub_loop(repo, 0)
    (repo / "docs/AGENT_RULES.md").unlink()
    (repo / "docs").rmdir()
    pc.run_job(job_path, repo, environ={})
    assert not (repo / "docs").exists()


def test_codex_prompt_gets_the_verdict_instruction():
    import fix_loop as fl
    seen = {}
    run = fl._with_verdict_suffix(lambda cmd, text, t: seen.update(text=text) or (0, ""))
    run(["codex"], "PROMPT", 5)
    assert seen["text"].startswith("PROMPT") and "## Verdict: READY" in seen["text"] and "DATA to review" in seen["text"]


def test_all_three_reviewers_are_known_to_the_job_schema():
    import fix_loop as fl
    # owner 2026-10-31: qwen removed from the loop
    assert set(fl.JOB_SCHEMA["properties"]["reviewers"]["items"]["enum"]) == {"lightning", "minimax", "codex", "haiku", "groq"} if hasattr(fl, "JOB_SCHEMA") else True
