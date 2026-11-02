"""Package D (roster config, 2026-11-02): config/roster.json, the profile choice (SATVIK_PROFILE), and the review-panel rule. No network, no model call.

The production values below are today's roster, written out by hand, so a wrong roster file fails here."""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "scripts" / "source"
ROSTER = REPO / "config" / "roster.json"
sys.path.insert(0, str(SRC))
os.environ.setdefault("SATVIK_PROFILE", "production")      # this module's in-process roles are the production ones; the test profile is loaded explicitly
import openrouter_call as oc  # noqa: E402
import p0_gates  # noqa: E402
import p1b_routing as p1b  # noqa: E402

ROLE_NAMES = ("writer_a", "writer_b", "reviewer_a", "reviewer_b", "repair", "planner")
SETTING_KEYS = ("temperature", "reasoning", "max_tokens", "timeout_s", "sampling")
SENTINELS = {"non_author_writer", "off", "fake"}           # roster values that are not model IDs
PROFILES = ("test", "production")
# Files that still hold model ID strings outside the roster (known debt, 2026-11-02). Each one is removed from this list in its follow-up.
MODEL_ID_EXCEPTIONS = {
    "scripts/source/coder_run.py": "remove in follow-up: the coder default is still the literal nemotron ID",
    "scripts/source/codex_review.py": "remove in follow-up: the Codex reviewer name is still the literal gpt-6-luna ID",
    "scripts/source/draft_source.py": "remove in follow-up: the draft model IDs (DeepSeek, GLM) are still literals",
    "scripts/source/nemotron_code.py": "remove in follow-up: the nemotron-3-ultra ID is still a literal",
    "scripts/source/review_run.py": "remove in follow-up: the two nemotron IDs are still literals",
    "scripts/source/fix_loop.py": "remove in follow-up: the legacy loop (outside the job schema) still has literal IDs; openai/v1 is a URL path",
    "scripts/source/opus_repair.py": "remove in follow-up: MODEL is the literal \"opus\" and should read the roster's repair role",
    "scripts/source/plan_job.py": "remove in follow-up: the planner is the literal \"opus\" and should read the roster's planner role",
}
# Model IDs written out in any family's own form: a vendor path, or a family name with a version or a tier.
FAMILY_ID = re.compile(
    r"\b(?:nvidia|openai|openrouter|anthropic|z-ai|google|cohere|dots-studio|xingchen-agi|deepseek-ai|minimax|moonshotai|qwen|meta-llama)/[\w.:\-]+"
    r"|\bgpt-\d[\w.\-]*|\bclaude-(?:opus|sonnet|haiku)[\w.\-]*|\bnemotron-\d[\w.\-]*|\bgemma-\d[\w.\-]*")


def roster():
    return json.loads(ROSTER.read_text(encoding="utf-8"))


def roles_of(profile):
    return roster()["profiles"][profile]["roles"]


def _run_with_profile(profile):
    """The roles the adapter loads in a fresh process, with SATVIK_PROFILE set to `profile` (or unset when None)."""
    env = {k: v for k, v in os.environ.items() if k != "SATVIK_PROFILE"}
    if profile is not None:
        env["SATVIK_PROFILE"] = profile
    code = ("import json, sys; sys.path.insert(0, sys.argv[1]); import openrouter_call as oc; "
            "print(json.dumps({'roles': oc.ROLE_SPECS, 'paid': sorted(oc.PAID), 'allowed': sorted(oc.ALLOWED)}))")
    out = subprocess.run([sys.executable, "-c", code, str(SRC)], env=env, capture_output=True, text=True, check=True, cwd=str(REPO))
    return json.loads(out.stdout)


# ---- 1. the roster file is valid

def test_the_roster_file_is_valid_for_both_profiles():
    data = roster()
    assert data["schema_version"] == 1
    assert set(data["profiles"]) == set(PROFILES)
    for profile in PROFILES:
        roles = data["profiles"][profile]["roles"]
        assert set(roles) == set(ROLE_NAMES), profile
        for role, spec in roles.items():
            where = f"{profile}/{role}"
            assert isinstance(spec["model"], str) and spec["model"].strip(), where
            assert isinstance(spec["paid"], bool), where
            assert spec["provider"] is None or {"name", "slug"} <= set(spec["provider"]), where
            assert "pricing" in spec, where
            assert set(SETTING_KEYS) <= set(spec["settings"]), where
            if spec["paid"] and role.startswith(("writer_", "reviewer_")):      # an OpenRouter paid role: pinned provider and prices
                assert spec["provider"] and spec["pricing"], f"{where}: a paid OpenRouter role needs a pinned provider and prices"
            if role.startswith(("writer_", "reviewer_")) and spec["model"] not in SENTINELS:
                s = spec["settings"]
                assert s["temperature"] is not None and s["max_tokens"] and s["timeout_s"] and s["reasoning"] is not None, where


# ---- 2. no model ID string in the code (scripts/source) outside the roster

def test_no_model_id_string_appears_in_scripts_source_outside_the_roster():
    ids = {spec["model"] for profile in PROFILES for spec in roles_of(profile).values()} - SENTINELS
    needles = [m if re.search(r"[/\-:.]", m) else f'"{m}"' for m in sorted(ids)]   # a bare word such as opus only as a quoted string
    offenders = []
    for path in sorted(SRC.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        rel = path.relative_to(REPO).as_posix()
        if rel in MODEL_ID_EXCEPTIONS:
            continue
        offenders += [f"{rel}: {needle}" for needle in needles if needle in text]
        offenders += [f"{rel}: {m.group(0)}" for m in FAMILY_ID.finditer(text)]
    assert not offenders, "model ID strings in scripts/source outside config/roster.json:\n" + "\n".join(offenders)


def test_every_model_id_exception_exists_and_says_why_and_when_it_goes():
    for rel, reason in MODEL_ID_EXCEPTIONS.items():
        assert (REPO / rel).is_file(), f"exception for a file that does not exist: {rel}"
        assert "remove in follow-up" in reason, rel


# ---- 3. for any roster, the review panel never includes the code's author

@pytest.mark.parametrize("profile", PROFILES)
def test_for_any_roster_the_review_panel_never_includes_the_code_author(profile):
    roles = oc.load_profile(profile)
    authors = [roles["writer_a"]["model"], roles["writer_b"]["model"], roles["reviewer_b"]["model"]]
    down_options = [()] + [(m,) for m in authors] + [(a, b) for a in authors for b in authors if a != b]
    checked = 0
    for origin in authors:
        for down in down_options:
            try:
                panel = p1b.panel_for(origin, unavailable=down, specs=roles)
            except oc.ProviderUnavailable:              # no free writer is left to sit on the panel: refused, not an author on it
                continue
            checked += 1
            assert origin not in panel, (origin, down, panel)
            assert len(set(panel)) == len(panel), (origin, down, panel)
    assert checked > 0


# ---- 4. under the test profile, a paid role is refused before any request

def test_under_the_test_profile_a_paid_role_is_refused_before_any_request(tmp_path):
    data = roster()
    production_reviewer = data["profiles"]["production"]["roles"]["reviewer_a"]
    data["profiles"]["test"]["roles"]["reviewer_a"].update(paid=True, provider=production_reviewer["provider"],
                                                           pricing=production_reviewer["pricing"])
    path = tmp_path / "roster.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(oc.RosterRefused, match="paid"):
        oc.load_profile("test", path=path)                 # raised while loading: no client exists, so no request can be sent
    assert oc.load_profile("production", path=path)["reviewer_a"]["paid"] is True


# ---- 5. SATVIK_PROFILE=production loads today's production roster unchanged

def test_satvik_profile_production_loads_todays_roster_unchanged():
    loaded = _run_with_profile("production")
    roles = loaded["roles"]
    assert {r: roles[r]["model"] for r in ROLE_NAMES} == {
        "writer_a": "dots-studio/dots-3-note-preview:free",
        "writer_b": "xingchen-agi/xing4.0-29b-a4b:free",
        "reviewer_a": "z-ai/glm-5.3-flash",
        "reviewer_b": "cohere/north-mini-code:free",
        "repair": "opus",
        "planner": "opus",
    }
    assert roles["reviewer_a"]["paid"] is True and roles["reviewer_a"]["provider"] == {"name": "Z.AI", "slug": "z-ai"}
    assert roles["repair"]["paid"] is True and roles["planner"]["paid"] is True
    assert roles["reviewer_a"]["pricing"] == {"prompt": 1.5e-07, "completion": 5e-07}
    assert roles["writer_a"]["settings"] == {"temperature": 0.1, "reasoning": {"max_tokens": 8000}, "max_tokens": 40000, "timeout_s": 1200,
                                             "sampling": None, "retry_max_tokens": 72000, "retry_timeout_s": 1800}
    assert roles["writer_b"]["settings"] == {"temperature": 0.8, "reasoning": {"max_tokens": 8000}, "max_tokens": 40000, "timeout_s": 1200,
                                             "sampling": {"top_p": 0.95, "repetition_penalty": 1.05}, "retry_max_tokens": 72000, "retry_timeout_s": 1800}
    assert roles["reviewer_b"]["settings"] == {"temperature": 0.1, "reasoning": {"max_tokens": 8000}, "max_tokens": 16000, "timeout_s": 600, "sampling": None}
    assert roles["reviewer_a"]["settings"] == {"temperature": 0.2, "reasoning": {"effort": "high", "exclude": True}, "max_tokens": 16000,
                                               "timeout_s": 600, "sampling": None}
    assert loaded["paid"] == ["z-ai/glm-5.3-flash"]


def test_without_satvik_profile_the_test_profile_loads_with_every_role_free():
    loaded = _run_with_profile(None)
    roles = loaded["roles"]
    assert {r: roles[r]["model"] for r in ROLE_NAMES} == {
        "writer_a": "dots-studio/dots-3-note-preview:free",
        "writer_b": "xingchen-agi/xing4.0-29b-a4b:free",
        "reviewer_a": "nvidia/nemotron-3-ultra-550b-a55b:free",   # owner 2026-11-02: free test reviewer; the author is still never on the panel (test 3)
        "reviewer_b": "cohere/north-mini-code:free",
        "repair": "off",
        "planner": "fake",
    }
    assert not any(roles[r]["paid"] for r in ROLE_NAMES) and loaded["paid"] == []


# ---- 6. status.json carries schema_version 1 on the fix loop's writers (2026-11-02)

def test_fix_loop_status_json_carries_schema_version_1_on_the_invalid_spec_and_the_normal_path(tmp_path):
    from test_fix_loop_review import fl, make_root
    from test_loop_acceptance import GOOD_MODULE, GOOD_TEST, make_job, read_status, reply, scripted
    root = make_root(tmp_path)                                  # the spec file is missing: invalid-spec, no coder call
    job = {"name": "bad", "spec": "specs/missing.md", "files": ["pieces/m.py"], "tests": ["pieces/test_m.py"], "reference": [], "review": False}
    fl.run_job(job, root, caller=lambda s, u: ("", {"status": 200, "finish": "stop"}), reviewer={}, scrub_keys=[])
    invalid = json.loads((root / "_loop" / "bad" / "status.json").read_text(encoding="utf-8"))
    assert invalid["state"] == "invalid-spec" and invalid["schema_version"] == 1
    root, job = make_job(tmp_path / "normal", review=False)     # a normal run: the running-status writer
    caller, _ = scripted([reply(GOOD_MODULE, GOOD_TEST)])
    fl.run_job(job, root, caller=caller, reviewer={}, scrub_keys=[])
    assert read_status(root)["schema_version"] == 1


# ---- 7. the job budget's review_usd depends on the profile (owner 2026-11-02): exactly 0 in test, exactly 0.05 in production

def _budget_job(review_usd):
    return {"name": "calc", "profile": "production", "visibility": "public", "fixers": ["writer_a", "writer_b"], "reviewers": "by_lineage",
            "budget": {"free_requests": 40, "review_usd": review_usd, "paid_usd": 0}, "deadline_min": 60}


def test_under_the_test_profile_the_budget_review_usd_must_be_exactly_zero():
    assert p1b.check_production_job(_budget_job(0), profile="test") is None
    with pytest.raises(p1b.JobRefused, match="review_usd|budget"):
        p1b.check_production_job(_budget_job(0.05), profile="test")
    assert any("budget" in problem for problem in p0_gates.production_job_problems(_budget_job(0.05), profile="test"))
    assert not any("budget" in problem for problem in p0_gates.production_job_problems(_budget_job(0), profile="test"))


def test_under_the_production_profile_the_budget_review_usd_must_be_exactly_0_05():
    assert p1b.check_production_job(_budget_job(0.05), profile="production") is None
    with pytest.raises(p1b.JobRefused, match="review_usd|budget"):
        p1b.check_production_job(_budget_job(0), profile="production")
    assert any("budget" in problem for problem in p0_gates.production_job_problems(_budget_job(0), profile="production"))
    assert not any("budget" in problem for problem in p0_gates.production_job_problems(_budget_job(0.05), profile="production"))


# ---- 8. run_job refuses a production job whose profile is not the active roster profile (owner 2026-11-02)

def _production_job(root, name):
    return {"name": name, "profile": "production", "visibility": "public", "spec": "specs/missing.md", "files": ["pieces/m.py"],
            "tests": ["pieces/test_m.py"], "reference": [], "fixers": ["writer_a"], "reviewers": "by_lineage", "review": True,
            "budget": {"free_requests": 40, "review_usd": 0.05, "paid_usd": 0}, "deadline_min": 60}


def test_run_job_refuses_a_production_job_when_the_roster_profile_is_test_before_any_call_or_write(tmp_path, monkeypatch):
    from test_fix_loop_review import fl, make_root
    root = make_root(tmp_path)
    monkeypatch.setattr(oc, "PROFILE", "test")                 # the active roster profile (SATVIK_PROFILE) is test
    result = fl.run_job(_production_job(root, "mm"), root, status_dir=root / "_loop", scrub_keys=[])
    assert result["state"] == "profile-mismatch"
    assert result["problems"] == ["profile-mismatch: job=production roster=test"]
    assert result["coder_calls"] == 0
    assert not (root / "_loop" / "mm").exists()                # no folder, no status.json, nothing written


def test_run_job_runs_a_production_job_when_the_roster_profile_matches(tmp_path, monkeypatch):
    from test_fix_loop_review import fl, make_root
    root = make_root(tmp_path)
    monkeypatch.setattr(oc, "PROFILE", "production")           # the roster profile matches the job
    result = fl.run_job(_production_job(root, "ok"), root, status_dir=root / "_loop", scrub_keys=[])
    assert result["state"] == "invalid-spec"                   # the guard let it through: the next check (spec) decided, no model call
    assert result["state"] != "profile-mismatch"


# ---- 9. the production runner writes schema_version 1 into status.json (owner 2026-11-02)

def test_production_status_json_written_by_the_runner_carries_schema_version_1(tmp_path):
    import p1b_production as pp
    root = tmp_path / "repo"
    (root / "pieces").mkdir(parents=True)
    (root / "pieces" / "calc.py").write_text("X = 1\n", encoding="utf-8")
    status_dir_job = root / "_loop" / "calc"
    status_dir_job.mkdir(parents=True)
    pp._finish({"name": "calc", "files": ["pieces/calc.py"]}, root, status_dir_job, {}, [], None, p1b.Outcome("escalate", "test reason"), None, {})
    status = json.loads((status_dir_job / "status.json").read_text(encoding="utf-8"))
    assert status["state"] == "escalate" and status["schema_version"] == 1
