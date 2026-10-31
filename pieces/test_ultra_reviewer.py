"""Owner 2026-10-31: Nemotron 3 Ultra is a reviewer with thinking ON at high effort (it takes the place of Lightning when Lightning gives no verdict).
Offline: checks the request profile and the reviewer's call; no model is called."""
import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "source"))
import fix_loop as fl  # noqa: E402

ULTRA = "nvidia/nemotron-3-ultra-550b-a55b"


def load_review_run():
    spec = importlib.util.spec_from_file_location("review_run_ultra_test", REPO / "scripts" / "source" / "review_run.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_ultra_request_has_thinking_on_at_high_effort_and_keeps_its_budget():
    rr = load_review_run()
    body = rr.apply_review_profile({"model": ULTRA, "messages": [{"role": "user", "content": "x"}], "max_tokens": 64000, "stream": True}, "high")
    assert body["chat_template_kwargs"]["enable_thinking"] is True
    assert body["reasoning_effort"] == "high"
    assert body["max_tokens"] == 64000 and body["model"] == ULTRA and body["stream"] is True


def test_the_other_reviewers_still_have_thinking_off():
    rr = load_review_run()
    body = rr.apply_review_profile({"model": "nvidia/nemotron-3.5-lightning-30b-a3b", "messages": [], "max_tokens": 16000}, "medium")
    assert body["chat_template_kwargs"]["enable_thinking"] is False
    assert "reasoning_effort" not in body


def test_ultra_is_a_known_reviewer_name_in_the_job_schema():
    assert "ultra" in fl.JOB_SCHEMA["properties"]["reviewers"]["items"]["enum"]


def test_the_ultra_reviewer_calls_review_run_with_ultra_thinking_and_high_effort(tmp_path, monkeypatch):
    seen = {}

    class FakeReviewRun:
        @staticmethod
        def run_review(**kw):
            seen.update(kw)
            return {"verdict": "READY"}

    monkeypatch.setattr(fl, "_load_module_by_path", lambda name, path: FakeReviewRun)
    reviewers = fl._default_reviewers_factory(tmp_path, "docs/RP.md")
    assert "ultra" in reviewers
    report = tmp_path / "r.md"
    assert reviewers["ultra"]([], report) == {"verdict": "READY"}
    assert seen["model"] == ULTRA and seen["effort"] == "high" and seen["max_tokens"] == 64000



def test_the_result_reports_the_thinking_setting_the_request_used(tmp_path):
    rr = load_review_run()
    prompt = REPO / "docs" / "REVIEW_PROMPT.md"
    target = REPO / "pieces" / "test_ultra_reviewer.py"

    def fake_caller(system, user):
        return ("## Verdict: READY" + chr(10), {"status": 200, "finish": "stop"})

    ultra = rr.run_review(model=ULTRA, prompt_file=prompt, files=[target], out_path=tmp_path / "u.md", root=REPO,
                          max_tokens=64000, idle_s=None, caller=fake_caller, effort="high")
    light = rr.run_review(model="nvidia/nemotron-3.5-lightning-30b-a3b", prompt_file=prompt, files=[target], out_path=tmp_path / "l.md",
                          root=REPO, max_tokens=16000, idle_s=None, caller=fake_caller, effort="medium")
    assert ultra["thinking"] is True and ultra["verdict"] == "READY"
    assert light["thinking"] is False
