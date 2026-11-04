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
    "scripts/source/claude_code.py",
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
    "scripts/source/p1b/nested.py",
    "scripts/source/fix_loop.py.bak",
    "tools/other.py",
    "pieces/test_p0_protected_files.py",
    "docs/config/notes.md",
    "monitorish/x.py",
]

# I53 (2026-11-04): the protected set is by directory, not a hand list. These were lookalikes of the old list; under the directory rule they
# are host-executed controller or configuration paths and are refused.
DIRECTORY_RULE = [
    "scripts/source/fix_loop_notes.py",        # every .py directly in scripts/source/
    "scripts/source/launcher.py",
    "config/roster.yaml",                      # every file under config/
    "config/nested/deep/stages.json",
    "monitor/fixtures/jobs.json",              # every file under monitor/
    "monitor/new_module_not_yet_written.py",
    "tools/sandbox/Dockerfile",                # every file under tools/sandbox/
    "tools/sandbox/new_helper.py",
    "SCRIPTS/SOURCE/Stage_Config.PY",
]


def pilot_job(extra=()):
    """The pilot job's settings as a base for one path under test. Its own host module (scripts/source/loop_status.py, refused by I53's
    directory rule) and the control that replaces it are taken out, so each assertion sees only the problem of the path it names."""
    job = json.loads(PILOT.read_text(encoding="utf-8"))
    job["files"] = ["pieces/test_loop_status.py"] + list(extra)
    job["negative_controls"] = []
    return job


@pytest.mark.parametrize("path", PROTECTED)
def test_each_protected_path_is_refused(path):
    assert gates.production_job_problems(pilot_job([path])) == [f"{path}: {MSG}"]


@pytest.mark.parametrize("path", VARIANTS)
def test_path_variations_of_a_protected_file_are_refused(path):
    assert gates.production_job_problems(pilot_job([path])) == [f"{path}: {MSG}"]


@pytest.mark.parametrize("path", DIRECTORY_RULE)
def test_the_directory_rule_refuses_host_controller_and_config_paths(path):
    assert gates.production_job_problems(pilot_job([path])) == [f"{path}: {MSG}"]


def _repo_controller_files():
    """Every file the host executes or reads as configuration today, found on disk (not a list): scripts/source/*.py, and every file under
    monitor/, config/ and tools/sandbox/."""
    out = [p for p in (REPO / "scripts" / "source").glob("*.py")]
    for folder in ("monitor", "config", "tools/sandbox"):
        out += [p for p in (REPO / folder).rglob("*") if p.is_file() and "__pycache__" not in p.parts]
    return sorted(p.relative_to(REPO).as_posix() for p in out)


def test_every_real_controller_and_config_file_on_disk_is_refused():
    files = _repo_controller_files()
    assert len(files) > 30, "the scan found the loop's own files"
    assert "scripts/source/fix_loop.py" in files and "config/roster.json" in files and "monitor/api.py" in files
    for rel in files:
        assert gates.is_protected_control_file(rel), rel


@pytest.mark.parametrize("path", LOOKALIKES)
def test_similar_but_unprotected_paths_are_not_refused(path):
    assert gates.production_job_problems(pilot_job([path])) == []


def test_the_pilot_job_is_refused_only_for_its_host_module():
    """I53 (2026-11-04): the pilot edits scripts/source/loop_status.py, a host-executed module, so the directory rule refuses it. This is the
    intended result: a controller change goes through a separate trusted workflow, not a loop job. Its other checks still pass."""
    assert gates.production_job_problems(json.loads(PILOT.read_text(encoding="utf-8"))) == [f"scripts/source/loop_status.py: {MSG}"]


@pytest.fixture(autouse=True)
def _production_profile(profile):
    """Every test in this file runs on the production roster. Set per test, never at import."""
    profile("production")
