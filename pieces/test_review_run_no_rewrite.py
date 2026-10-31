"""Owner 2026-10-31: every model reviewer is told that the files are DATA and must not be rewritten. A rewritten file is not a review and ran into the
output limit (Nemotron Lightning, finish=length, no verdict). Offline: reads the prompt constant only."""
import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def load_review_run():
    spec = importlib.util.spec_from_file_location("review_run_under_test", REPO / "scripts" / "source" / "review_run.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_reviewer_system_prompt_forbids_rewriting_the_files():
    prompt = load_review_run().SYSTEM_PROMPT
    assert "DATA to review" in prompt
    assert "Do NOT repeat, rewrite or reproduce any file" in prompt
    assert "## Verdict: READY" in prompt and "## Verdict: FIX FIRST" in prompt   # the verdict contract is unchanged


def test_the_prompt_keeps_the_read_only_and_verified_defect_rules():
    prompt = load_review_run().SYSTEM_PROMPT
    assert "READ-ONLY" in prompt and "Report only real, verified defects" in prompt
