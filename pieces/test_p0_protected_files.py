"""Package F (2026-11-02): a production job may not edit the loop's own control files.

production_job_problems refuses any job whose "files" include a protected path, whatever its spelling (./, backslashes, case, doubled
slashes, '..', a leading slash, trailing dots or spaces). The pilot job must still pass. No model call, no network."""
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "source"))
import p0_gates as gates  # noqa: E402

PILOT = REPO / "docs" / "pilot" / "pilot-loop-status.json"
MSG = "job edits the loop's own control files"

PROTECTED = [
    "scripts/source/fix_loop.py",
    "scripts/source/p1b_production.py",
    "scripts/source/p1b_routing.py",
    "scripts/source/openrouter_call.py",
    "scripts/source/sandbox_runner.py",
    "scripts/source/p0_gates.py",
    "scripts/source/opus_repair.py",
    "scripts/source/plan_job.py",
    "scripts/source/loop_home.py",
    "scripts/source/launch.py",
    "scripts/source/merge_queue.py",
    "scripts/source/shared_limits.py",
    "config/roster.json",
    "tools/sandbox/run_check.py",
    "tools/sandbox/sub/deeper.txt",
]

VARIANTS = [
    "./scripts/source/fix_loop.py",
    "scripts\\source\\fix_loop.py",
    "SCRIPTS/Source/Fix_Loop.PY",
    "scripts//source/fix_loop.py",
    "scripts/source/../source/fix_loop.py",
    "/scripts/source/fix_loop.py",
    "scripts/source/fix_loop.py.",
    "scripts/source/fix_loop.py ",
    "scripts\\source\\P1B_ROUTING.py",
    "Config\\Roster.JSON",
    "TOOLS\\Sandbox\\x.py",
    "./tools/sandbox/x.py",
]

LOOKALIKES = [
    "scripts/source/fix_loop_notes.py",
    "scripts/source/launcher.py",
    "scripts/source/p1b/nested.py",
    "scripts/source/fix_loop.py.bak",
    "tools/other.py",
    "config/roster.yaml",
    "pieces/test_p0_protected_files.py",
]


def pilot_job(extra=()):
    job = json.loads(PILOT.read_text(encoding="utf-8"))
    job["files"] = list(job["files"]) + list(extra)
    return job


@pytest.mark.parametrize("path", PROTECTED)
def test_each_protected_path_is_refused(path):
    assert gates.production_job_problems(pilot_job([path])) == [f"{path}: {MSG}"]


@pytest.mark.parametrize("path", VARIANTS)
def test_path_variations_of_a_protected_file_are_refused(path):
    assert gates.production_job_problems(pilot_job([path])) == [f"{path}: {MSG}"]


@pytest.mark.parametrize("path", LOOKALIKES)
def test_similar_but_unprotected_paths_are_not_refused(path):
    assert gates.production_job_problems(pilot_job([path])) == []


def test_the_pilot_job_is_still_accepted():
    assert gates.production_job_problems(json.loads(PILOT.read_text(encoding="utf-8"))) == []


@pytest.fixture(autouse=True)
def _production_profile(profile):
    """Every test in this file runs on the production roster. Set per test, never at import."""
    profile("production")
