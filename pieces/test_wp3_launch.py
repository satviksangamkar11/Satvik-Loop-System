"""WP3 launcher tests (Revision 4): job ids, one worktree and branch per job, start limits, stop switch.
Real git on temp repos and the real WP2 loop_home objects (home_dir is a temp folder). No model call."""
import os
import re
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import launch
from wp3_repos import git, live_leases, make_repos

JOB_ID = re.compile(r"^[a-z][a-z0-9_-]*-\d{8}-\d{4}-[0-9a-f]{4}$")
OK_BUDGET = {"free_requests": 1000, "review_spend_usd": 0.0}


def make_launcher(tmp_path, budget=OK_BUDGET, slots=launch.SANDBOX_SLOTS):
    origin, repo = make_repos(tmp_path)
    api = launch.real_api(tmp_path / "home")
    box = {"budget": None if budget is None else dict(budget)}   # tests change box["budget"] to move the figures
    launcher = launch.Launcher(repo, api, budget=lambda: box["budget"], sandbox_slots=slots)
    return SimpleNamespace(origin=origin, repo=repo, api=api, launcher=launcher, box=box, home=api.home)


def worktrees(env):
    wt_dir = env.home / "wt"
    return sorted(os.listdir(wt_dir)) if wt_dir.exists() else []


def job_branches(env):
    return sorted(git(env.repo, "for-each-ref", "--format=%(refname:short)", "refs/heads/job/").splitlines())


def seed_history(api, states):
    """Finished job rows, oldest first (the newest one is last)."""
    for i, state in enumerate(states):
        job = f"hist-{i}"
        gen = api.lease.acquire(job)
        api.registry.record(job, job, gen, state, "base", None)
        api.lease.release(job, gen)


def assert_nothing_started(env):
    assert worktrees(env) == [] and job_branches(env) == [] and live_leases(env.home) == []


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


def test_a_job_id_whose_lease_is_live_is_drawn_again(tmp_path, monkeypatch):
    env = make_launcher(tmp_path)
    env.api.lease.acquire("taken-id")                        # another launcher reserved this id
    ids = iter(["taken-id", "fresh-id"])
    monkeypatch.setattr(launch, "new_job_id", lambda name, exists=None: next(ids))
    job = env.launcher.start("alpha")
    assert job["ok"] and job["job_id"] == "fresh-id"


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
    assert job["ok"]
    assert live_leases(env.home) == [job["job_id"]]
    assert env.api.claims.is_live("pieces/x.py")
    row = env.api.registry.lookup(job["job_id"])
    assert row["state"] == "running" and row["gen"] == job["gen"]


def test_a_second_job_cannot_claim_a_path_the_first_holds(tmp_path):
    env = make_launcher(tmp_path)
    a = env.launcher.start("alpha", ["x.py"])
    b = env.launcher.start("beta", ["x.py"])
    assert a["ok"] and not b["ok"] and b["reason"] == "claim-conflict" and b["conflicts"] == ["x.py"]
    assert worktrees(env) == [a["job_id"]]
    assert job_branches(env) == [f"job/{a['job_id']}"]
    assert live_leases(env.home) == [a["job_id"]]


# ---------------------------------------------------------------- start limits (pure rules)
def test_start_refusal_blocks_at_each_limit():
    assert launch.start_refusal(True, 0, [], OK_BUDGET) == "stop-file"
    assert launch.start_refusal(False, 1, [], {"free_requests": 139, "review_spend_usd": 0.0}) == "free-requests"
    assert launch.start_refusal(False, 0, [], {"free_requests": 99, "review_spend_usd": 0.0}) == "free-requests"
    assert launch.start_refusal(False, 0, [], {"free_requests": 1000, "review_spend_usd": 0.50}) == "review-spend"
    assert launch.start_refusal(False, 0, ["provider-unavailable"] * 3, OK_BUDGET) == "provider-unavailable"


def test_start_refusal_allows_at_the_edges():
    assert launch.start_refusal(False, 1, [], {"free_requests": 140, "review_spend_usd": 0.0}) is None   # exactly 100 + 40
    assert launch.start_refusal(False, 0, [], {"free_requests": 100, "review_spend_usd": 0.49}) is None
    assert launch.start_refusal(False, 0, ["done", "provider-unavailable", "provider-unavailable"], OK_BUDGET) is None   # newest first
    assert launch.start_refusal(False, 0, ["provider-unavailable", "provider-unavailable"], OK_BUDGET) is None


def test_without_a_budget_snapshot_only_one_loop_runs():
    assert launch.start_refusal(False, 0, [], None) is None
    assert launch.start_refusal(False, 1, [], None) == "no-budget-snapshot"


# ---------------------------------------------------------------- start limits (the launcher, real registry and gate)
def test_the_free_requests_limit_counts_running_jobs(tmp_path):
    env = make_launcher(tmp_path)
    assert env.launcher.start("alpha")["ok"]
    env.box["budget"] = {"free_requests": 139, "review_spend_usd": 0.0}
    assert env.launcher.start("beta") == {"ok": False, "reason": "free-requests"}
    assert len(worktrees(env)) == 1


def test_the_review_spend_limit_blocks_a_start(tmp_path):
    env = make_launcher(tmp_path, budget={"free_requests": 1000, "review_spend_usd": 0.50})
    assert env.launcher.start("alpha") == {"ok": False, "reason": "review-spend"}
    assert_nothing_started(env)


def test_three_provider_unavailable_jobs_in_a_row_block_a_start(tmp_path):
    env = make_launcher(tmp_path)
    seed_history(env.api, ["done", "provider-unavailable", "provider-unavailable", "provider-unavailable"])
    assert env.launcher.start("alpha") == {"ok": False, "reason": "provider-unavailable"}
    assert_nothing_started(env)


def test_a_mixed_streak_does_not_block(tmp_path):
    env = make_launcher(tmp_path)
    seed_history(env.api, ["provider-unavailable", "provider-unavailable", "done", "provider-unavailable"])
    assert env.launcher.start("alpha")["ok"]


def test_without_a_budget_snapshot_one_loop_runs_at_a_time(tmp_path):
    env = make_launcher(tmp_path, budget=None)
    a = env.launcher.start("alpha")
    assert a["ok"]
    assert env.launcher.start("beta") == {"ok": False, "reason": "no-budget-snapshot"}
    assert len(worktrees(env)) == 1
    env.launcher.finish(a["job_id"], a["gen"], "stopped")
    assert env.launcher.start("gamma")["ok"]


def test_a_full_sandbox_blocks_the_start_and_leaves_nothing_behind(tmp_path):
    env = make_launcher(tmp_path, slots=1)
    a = env.launcher.start("alpha")
    assert a["ok"]
    assert env.launcher.start("beta") == {"ok": False, "reason": "no-sandbox-slot"}
    assert worktrees(env) == [a["job_id"]]
    assert job_branches(env) == [f"job/{a['job_id']}"]
    assert live_leases(env.home) == [a["job_id"]]


def test_the_stop_file_blocks_a_new_start(tmp_path):
    env = make_launcher(tmp_path)
    env.home.mkdir(parents=True)
    (env.home / "STOP").write_text("stop", encoding="utf-8")
    assert env.launcher.start("alpha") == {"ok": False, "reason": "stop-file"}
    assert_nothing_started(env)


# ---------------------------------------------------------------- stop switch
def test_a_stop_file_stops_the_loop_before_its_next_round_and_restores_the_files(tmp_path):
    api = launch.real_api(tmp_path / "home")
    api.home.mkdir(parents=True)
    gen = api.lease.acquire("job-x")
    job_file = tmp_path / "job.txt"
    job_file.write_text("original", encoding="utf-8")
    seen = []

    def do_round(i):
        seen.append(i)
        job_file.write_text(f"round {i}", encoding="utf-8")
        if i == 1:
            (api.home / "STOP").write_text("stop", encoding="utf-8")   # set while round 1 is in its call

    state = launch.run_rounds(api, "job-x", gen, 5, do_round, lambda: job_file.write_text("original", encoding="utf-8"), api.home)
    assert state == "stopped"
    assert seen == [0, 1]                                  # round 1 finished its call, round 2 never started
    assert job_file.read_text(encoding="utf-8") == "original"


def test_without_a_stop_file_every_round_runs(tmp_path):
    api = launch.real_api(tmp_path / "home")
    api.home.mkdir(parents=True)
    gen = api.lease.acquire("job-x")
    seen = []
    restored = []
    state = launch.run_rounds(api, "job-x", gen, 3, seen.append, lambda: restored.append(True), api.home)
    assert state == "done" and seen == [0, 1, 2] and restored == []
