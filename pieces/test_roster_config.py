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
import p1b_routing as p1b  # noqa: E402

ROLE_NAMES = ("writer_a", "writer_b", "reviewer_a", "reviewer_b", "repair", "planner")
SETTING_KEYS = ("temperature", "reasoning", "max_tokens", "timeout_s", "sampling")
SENTINELS = {"non_author_writer", "off", "fake"}           # roster values that are not model IDs
PROFILES = ("test", "production")
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
        offenders += [f"{rel}: {needle}" for needle in needles if needle in text]
        offenders += [f"{rel}: {m.group(0)}" for m in FAMILY_ID.finditer(text)]
    assert not offenders, "model ID strings in scripts/source outside config/roster.json:\n" + "\n".join(offenders)


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
        "reviewer_a": "non_author_writer",
        "reviewer_b": "cohere/north-mini-code:free",
        "repair": "off",
        "planner": "fake",
    }
    assert not any(roles[r]["paid"] for r in ROLE_NAMES) and loaded["paid"] == []
