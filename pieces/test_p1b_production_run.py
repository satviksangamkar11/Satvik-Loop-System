"""P1b wiring (2026-11-02): one production job through fix_loop.run_job, with the real Docker sandbox and fake OpenRouter models for writer_a
(Dots), writer_b (Xing), reviewer (North) and repair (GLM). Every model call goes through openrouter_call by role; the locked acceptance tests
decide; reviewers are chosen by lineage. No network and no paid call: the transport is scripted (prod_harness).

The real sandbox needs Docker and the sandbox image. Without them the end-to-end tests are skipped with the reason (never a silent pass)."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "source"))
import fix_loop as fl  # noqa: E402
import openrouter_call as oc  # noqa: E402
import sandbox_runner as sr  # noqa: E402
import prod_harness as ph  # noqa: E402
from test_p0_acceptance import make_prod_root, CONSTANT_CODE, CONSTANT_TEST, GOOD_CODE, GOOD_TEST  # noqa: E402

NL = "\n"
DOTS, XING, NORTH, GLM = oc.ROLES["writer_a"], oc.ROLES["writer_b"], oc.ROLES["reviewer"], oc.ROLES["repair"]
# Xing's wrong code: its own test agrees with it (passes), the locked acceptance tests fail on the same two tests Dots fails.
XING_CODE = "def calculate_total(prices):" + NL + "    return sum(prices[:-1])" + NL
XING_TEST = "from pieces.calc import calculate_total" + NL + "def test_calculate_total():" + NL + "    assert calculate_total([20, 30]) == 20" + NL


SANDBOX_PROBLEMS = sr.DockerSandbox(image=sr.DEFAULT_IMAGE, include=["pieces", "tests"]).selftest()
needs_docker = pytest.mark.skipif(bool(SANDBOX_PROBLEMS),
                                  reason="the real sandbox is not available here: " + "; ".join(SANDBOX_PROBLEMS)[:200])


def p1b_job(**over):
    """A production job in the P1b shape: role names for fixers, reviewers by lineage, the owner's budget and deadline."""
    job = {"name": "calc", "profile": "production", "visibility": "public", "spec": "specs/s.md",
           "files": ["pieces/calc.py", "pieces/test_calc.py"], "tests": ["pieces/test_calc.py"], "reference": [],
           "fixers": ["writer_a", "writer_b"], "reviewers": "by_lineage",
           "budget": {"free_requests": 40, "paid_usd": 0.15}, "deadline_min": 60,
           "review": True, "review_prompt": "docs/RP.md",
           "acceptance_tests": ["tests/acceptance/test_calc_acceptance.py"],
           "negative_controls": [
               {"name": "returns None", "replace": {"pieces/calc.py": "tests/controls/calc_returns_none.py"},
                "must_fail": ["tests/acceptance/test_calc_acceptance.py::test_sums_two_prices"]},
               {"name": "returns a constant", "replace": {"pieces/calc.py": "tests/controls/calc_returns_constant.py"},
                "must_fail": ["tests/acceptance/test_calc_acceptance.py::test_sums_three_prices"]}]}
    job.update(over)
    return job


def load(root, job):
    path = root / "_loop" / "jobs" / (job["name"] + ".json")
    path.write_text(json.dumps(job), encoding="utf-8")
    return fl.load_job(path)


def script_for_dots_xing_glm():
    """Dots writes the constant (its own test agrees) and repairs it the same way; Xing writes its wrong sum and repairs it the same way; GLM
    writes the correct code; then Dots and North review READY."""
    return {
        DOTS: [ph.file_reply(("pieces/calc.py", CONSTANT_CODE), ("pieces/test_calc.py", CONSTANT_TEST)),
               ph.file_reply(("pieces/calc.py", CONSTANT_CODE), ("pieces/test_calc.py", CONSTANT_TEST)),
               ph.READY],
        XING: [ph.file_reply(("pieces/calc.py", XING_CODE), ("pieces/test_calc.py", XING_TEST)),
               ph.file_reply(("pieces/calc.py", XING_CODE), ("pieces/test_calc.py", XING_TEST))],
        GLM: [ph.file_reply(("pieces/calc.py", GOOD_CODE), ("pieces/test_calc.py", GOOD_TEST))],
        NORTH: [ph.READY],
    }


@needs_docker
def test_production_job_runs_dots_then_xing_fresh_then_glm_with_lineage_reviews_and_locked_tests(tmp_path):
    # real Docker sandbox (sandbox=None: run_job builds DockerSandbox), fake models through the real adapter
    root = make_prod_root(tmp_path)
    job = load(root, p1b_job())
    provider = ph.Provider(script_for_dots_xing_glm())
    result = fl.run_job(job, root, openrouter=ph.client(tmp_path, provider), scrub_keys=[])

    # the order of model calls: Dots writes and repairs, Xing writes fresh and repairs, GLM repairs once, then the lineage reviewers
    # (GLM repaired Xing's code, so its panel is Dots and North)
    assert provider.models() == [DOTS, DOTS, XING, XING, GLM, DOTS, NORTH]
    assert result["state"] == "passed", result
    assert result["review_verdict"] == "READY"

    # the locked acceptance tests decided: the GLM reply is the only one that passed them
    ev = result["evidence"]
    assert ev["calibration"] == "passed"
    assert "tests/acceptance/test_calc_acceptance.py" in ev["acceptance_lock"]
    assert ev["tested_hash"] == ev["final_hash"] == ev["reviewed_hash"]

    # the delivered files are the GLM version, byte for byte
    assert (root / "pieces" / "calc.py").read_text(encoding="utf-8") == GOOD_CODE

    # the lineage is saved per file: last writer GLM, and the origin is the fresh writer Xing
    lineage = json.loads((root / "_loop" / "calc" / "lineage.json").read_text(encoding="utf-8"))
    assert lineage["pieces/calc.py"] == {"last": GLM, "origin": XING}
    assert lineage["pieces/test_calc.py"] == {"last": GLM, "origin": XING}


@needs_docker
def test_the_repair_call_is_pinned_to_z_ai_with_no_fallbacks(tmp_path):
    root = make_prod_root(tmp_path)
    job = load(root, p1b_job())
    provider = ph.Provider(script_for_dots_xing_glm())
    fl.run_job(job, root, openrouter=ph.client(tmp_path, provider), scrub_keys=[])
    glm_bodies = [request for model, request in provider.calls if model == GLM]
    assert glm_bodies, "the repair call was made"
    for request in glm_bodies:
        assert request["provider"] == {"order": ["z-ai"], "only": ["z-ai"], "allow_fallbacks": False}


@needs_docker
def test_a_production_job_can_never_reach_a_legacy_model(tmp_path, monkeypatch):
    """Nemotron, Codex, Haiku, Groq and Sonnet are the legacy path. A production run must not call any of them: the legacy factories are
    replaced by functions that fail, every subprocess other than docker fails, and every model named to the transport is a P1b roster model."""
    root = make_prod_root(tmp_path)
    job = load(root, p1b_job())
    legacy = ["_default_caller_factory", "_haiku_cli_caller_factory", "_default_reviewers_factory", "_default_codex_runner", "_groq_runner",
              "_haiku_runner", "_ping_cli", "_default_judge_factory"]

    def refuse(*args, **kwargs):
        raise AssertionError("a legacy model path was reached from a production job")

    for name in legacy:
        if hasattr(fl, name):
            monkeypatch.setattr(fl, name, refuse)
    real_popen = subprocess.Popen                   # subprocess.run starts processes through Popen: the guard sits there

    def only_docker(args, *rest, **kwargs):
        first = args[0] if isinstance(args, (list, tuple)) and args else str(args)
        if Path(str(first)).name.lower().startswith("docker"):
            return real_popen(args, *rest, **kwargs)
        raise AssertionError(f"a production job started a non-docker process: {first}")

    monkeypatch.setattr(subprocess, "Popen", only_docker)
    # Dots writes correct code, so the reviewers are its lineage panel: Xing and North
    provider = ph.Provider({DOTS: [ph.file_reply(("pieces/calc.py", GOOD_CODE), ("pieces/test_calc.py", GOOD_TEST))],
                            XING: [ph.READY], NORTH: [ph.READY]})
    result = fl.run_job(job, root, openrouter=ph.client(tmp_path, provider), scrub_keys=[])
    assert result["state"] == "passed", result
    assert provider.models() == [DOTS, XING, NORTH]
    assert set(provider.models()) <= {DOTS, XING, NORTH, GLM} | set(oc.REVIEWER_BACKUP)


def test_a_production_run_refuses_legacy_callers_and_legacy_fixers(tmp_path):
    root = make_prod_root(tmp_path)
    job = load(root, p1b_job())
    with pytest.raises(ValueError, match="legacy"):
        fl.run_job(job, root, caller=lambda s, u: ("", {}), scrub_keys=[])
    with pytest.raises(ValueError):
        fl.load_job(_write(root, p1b_job(name="calc_bad", fixers=["nemotron"])))


def _write(root, job):
    path = root / "_loop" / "jobs" / (job["name"] + ".json")
    path.write_text(json.dumps(job), encoding="utf-8")
    return path


def test_the_production_runner_names_no_legacy_model_family():
    """Static: the P1b runner and routing modules contain no legacy model family name (Nemotron, Codex, Haiku, Groq, Sonnet)."""
    for name in ("p1b_production.py", "p1b_routing.py"):
        text = (REPO / "scripts" / "source" / name).read_text(encoding="utf-8").lower()
        for word in ("nemotron", "codex", "haiku", "groq", "sonnet"):
            assert word not in text, f"{name} names {word}"


def test_a_production_job_gets_no_legacy_review_prompt(tmp_path):
    """docs/REVIEW_PROMPT.md is the legacy HIGH/MED/LOW prompt; a production job must not inherit it as its review prompt."""
    job = p1b_job()
    del job["review_prompt"]
    (tmp_path / "_loop" / "jobs").mkdir(parents=True)
    assert load(tmp_path, job)["review_prompt"] is None
