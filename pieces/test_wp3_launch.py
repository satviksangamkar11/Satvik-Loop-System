"""WP3 launcher tests (Revision 4): job ids, one worktree and branch per job, start limits, stop switch. Real git on temp repos; WP2 is a fake."""
import os
import re
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import launch
from wp3_fakes import FakeApi, commit_in, git, make_repos

JOB_ID = re.compile(r"^[a-z][a-z0-9_-]*-\d{8}-\d{4}-[0-9a-f]{4}$")
OK_SNAPSHOT = {"free_requests": 1000, "running": 0, "review_spend_usd": 0.0, "recent_states": []}


def make_launcher(tmp_path, **snapshot):
    origin, repo = make_repos(tmp_path)
    api = FakeApi(tmp_path / "home")
    values = dict(OK_SNAPSHOT, **snapshot)
    launcher = launch.Launcher(repo, api, snapshot=lambda: dict(values))
    return SimpleNamespace(origin=origin, repo=repo, api=api, launcher=launcher, home=api.home())


def worktrees(env):
    wt_dir = env.home / "wt"
    return sorted(os.listdir(wt_dir)) if wt_dir.exists() else []


def job_branches(env):
    return sorted(git(env.repo, "for-each-ref", "--format=%(refname:short)", "refs/heads/job/").splitlines())


# ---------------------------------------------------------------- job ids
def test_job_ids_match_the_pattern_and_are_unique():
    issued = set()
    for _ in range(300):
        job_id = launch.new_job_id("pilot", exists=issued.__contains__)
        issued.add(job_id)
    assert len(issued) == 300
    assert all(JOB_ID.match(j) for j in issued)


def test_job_id_carries_the_name_and_the_minute():
    now = 1793000000.0
    job_id = launch.new_job_id("pilot-a", now=now)
    assert job_id.startswith("pilot-a-" + time.strftime("%Y%m%d-%H%M", time.localtime(now)) + "-")
    assert JOB_ID.match(job_id)


def test_a_taken_job_id_is_drawn_again(monkeypatch):
    draws = iter(["ab12", "cd34"])
    monkeypatch.setattr(launch.secrets, "token_hex", lambda n: next(draws))
    job_id = launch.new_job_id("pilot", exists=lambda j: j.endswith("-ab12"))
    assert job_id.endswith("-cd34")


@pytest.mark.parametrize("name", ["../x", "Pilot", "", "a b", "x" * 41])
def test_a_bad_job_name_is_refused(name):
    with pytest.raises(ValueError):
        launch.new_job_id(name)


# ---------------------------------------------------------------- one worktree and branch per job
def test_each_job_gets_its_own_worktree_and_branch(tmp_path):
    env = make_launcher(tmp_path)
    a = env.launcher.start("alpha")
    b = env.launcher.start("beta")
    assert a["ok"] and b["ok"]
    assert a["job_id"] != b["job_id"]
    main = git(env.repo, "rev-parse", "origin/main")
    for job in (a, b):
        wt = Path(job["worktree"])
        assert wt == env.home / "wt" / job["job_id"]
        assert wt.is_dir()
        assert git(wt, "rev-parse", "--abbrev-ref", "HEAD") == f"job/{job['job_id']}"
        assert git(wt, "rev-parse", "HEAD") == main == job["base"]
        assert Path(job["job_dir"]) == wt / "_loop" / job["job_id"]
    assert job_branches(env) == sorted([f"job/{a['job_id']}", f"job/{b['job_id']}"])
    assert worktrees(env) == sorted([a["job_id"], b["job_id"]])


def test_a_started_job_holds_a_lease_claims_its_paths_and_is_recorded_running(tmp_path):
    env = make_launcher(tmp_path)
    job = env.launcher.start("alpha", ["pieces/x.py"])
    assert env.api.lease.held == {job["job_id"]: job["gen"]}
    assert env.api.claims.owner == {"pieces/x.py": job["job_id"]}
    assert env.api.registry.lookup(job["job_id"])["state"] == "running"


def test_a_second_job_cannot_claim_a_path_the_first_holds(tmp_path):
    env = make_launcher(tmp_path)
    a = env.launcher.start("alpha", ["x.py"])
    b = env.launcher.start("beta", ["x.py"])
    assert a["ok"] and not b["ok"] and b["reason"] == "claim-conflict" and b["conflicts"] == ["x.py"]
    assert worktrees(env) == [a["job_id"]]
    assert job_branches(env) == [f"job/{a['job_id']}"]


# ---------------------------------------------------------------- start limits
@pytest.mark.parametrize("kwargs, reason", [
    (dict(free_requests=139, running=1), "free-requests"),           # needs 100 + 40 per running job
    (dict(free_requests=99), "free-requests"),
    (dict(review_spend_usd=0.50), "review-spend"),
    (dict(recent_states=["provider-unavailable"] * 3), "provider-unavailable"),
    (dict(recent_states=["done", "provider-unavailable", "provider-unavailable", "provider-unavailable"]), "provider-unavailable"),
])
def test_each_start_limit_blocks_the_start(tmp_path, kwargs, reason):
    env = make_launcher(tmp_path, **kwargs)
    refusal = env.launcher.start("alpha")
    assert refusal == {"ok": False, "reason": reason}
    assert worktrees(env) == [] and job_branches(env) == [] and env.api.lease.held == {}


def test_a_full_sandbox_blocks_the_start_and_leaves_nothing_behind(tmp_path):
    env = make_launcher(tmp_path)
    env.api.gate.busy.add("sandbox")
    assert env.launcher.start("alpha") == {"ok": False, "reason": "no-sandbox-slot"}
    assert worktrees(env) == [] and job_branches(env) == [] and env.api.lease.held == {}


def test_the_stop_file_blocks_a_new_start(tmp_path):
    env = make_launcher(tmp_path)
    env.home.mkdir(parents=True)
    (env.home / "STOP").write_text("stop", encoding="utf-8")
    assert env.launcher.start("alpha") == {"ok": False, "reason": "stop-file"}
    assert worktrees(env) == []


@pytest.mark.parametrize("kwargs", [
    dict(free_requests=140, running=1, review_spend_usd=0.0, recent_states=[]),   # exactly 100 + 40 * 1
    dict(free_requests=100, running=0, review_spend_usd=0.49, recent_states=[]),
    dict(free_requests=500, running=0, review_spend_usd=0.0, recent_states=["provider-unavailable"] * 2 + ["done"]),
    dict(free_requests=500, running=0, review_spend_usd=0.0, recent_states=["provider-unavailable"] * 2),
])
def test_limits_allow_a_start_at_their_edges(kwargs):
    assert launch.start_refusal(stop_present=False, **kwargs) is None


# ---------------------------------------------------------------- stop switch
def test_a_stop_file_stops_the_loop_before_its_next_round_and_restores_the_files(tmp_path):
    api = FakeApi(tmp_path / "home")
    api.home_dir.mkdir()
    gen = api.lease.acquire("job-x")
    job_file = tmp_path / "job.txt"
    job_file.write_text("original", encoding="utf-8")
    seen = []

    def do_round(i):
        seen.append(i)
        job_file.write_text(f"round {i}", encoding="utf-8")
        if i == 1:
            (api.home_dir / "STOP").write_text("stop", encoding="utf-8")   # set while round 1 is in its call

    state = launch.run_rounds(api, "job-x", gen, 5, do_round, lambda: job_file.write_text("original", encoding="utf-8"), api.home_dir)
    assert state == "stopped"
    assert seen == [0, 1]                                  # round 1 finished its call, round 2 never started
    assert job_file.read_text(encoding="utf-8") == "original"


def test_without_a_stop_file_every_round_runs(tmp_path):
    api = FakeApi(tmp_path / "home")
    api.home_dir.mkdir()
    gen = api.lease.acquire("job-x")
    seen = []
    restored = []
    state = launch.run_rounds(api, "job-x", gen, 3, seen.append, lambda: restored.append(True), api.home_dir)
    assert state == "done" and seen == [0, 1, 2] and restored == []
