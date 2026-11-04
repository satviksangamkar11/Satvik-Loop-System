"""I64: a planned job's installed spec, locked acceptance tests and negative-control sources are committed with its change, so the merge carries
them. Without the installed set (an unplanned job) write_change_doc stages only the job's own files, as before. Real git on temp repos; no model."""
from pathlib import Path

import fix_loop as fl
from wp3_repos import git, make_repos


def job_wt(tmp_path, name):
    (tmp_path / name).mkdir()
    origin, repo = make_repos(tmp_path / name)
    wt = tmp_path / name / f"wt-{name}"
    git(repo, "worktree", "add", "-q", "-b", f"job/{name}", str(wt), "origin/main")
    return repo, wt


def planned_job():
    return {"name": "calc", "files": ["pieces/calc.py"], "spec": "specs/calc.md", "acceptance_tests": ["tests/acc/test_calc_acceptance.py"],
            "negative_controls": [{"name": "returns none", "replace": {"pieces/calc.py": "tests/controls/calc_none.py"},
                                   "must_fail": ["tests/acc/test_calc_acceptance.py::test_sums"]}]}


def write(wt, rel, text):
    path = Path(wt) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def committed(wt):
    return set(git(wt, "ls-tree", "-r", "--name-only", "HEAD").split())


def test_the_installed_plan_files_are_named_by_the_job(tmp_path):
    assert fl.installed_plan_paths(planned_job()) == ["specs/calc.md", "tests/acc/test_calc_acceptance.py", "tests/controls/calc_none.py"]


def test_a_planned_jobs_installed_files_are_in_its_change_commit(tmp_path):
    repo, wt = job_wt(tmp_path, "planned")
    job = planned_job()
    write(wt, "pieces/calc.py", "def calc():\n    return 1\n")
    # what install_plan writes into the worktree (untracked until the change is committed)
    write(wt, "specs/calc.md", "spec\n")
    write(wt, "tests/acc/test_calc_acceptance.py", "def test_sums():\n    assert True\n")
    write(wt, "tests/controls/calc_none.py", "def calc():\n    return None\n")
    fl.write_change_doc(wt, "calc-1", job, {"state": "passed", "round": 1}, "base", installed=fl.installed_plan_paths(job))
    files = committed(wt)
    assert {"specs/calc.md", "tests/acc/test_calc_acceptance.py", "tests/controls/calc_none.py", "pieces/calc.py"} <= files


def test_an_unplanned_job_stages_only_its_own_files(tmp_path):
    repo, wt = job_wt(tmp_path, "unplanned")
    job = planned_job()
    write(wt, "pieces/calc.py", "def calc():\n    return 1\n")
    write(wt, "specs/calc.md", "spec\n")                       # on disk, but not installed by a plan
    fl.write_change_doc(wt, "calc-2", job, {"state": "passed", "round": 1}, "base")
    files = committed(wt)
    assert "pieces/calc.py" in files and "specs/calc.md" not in files
