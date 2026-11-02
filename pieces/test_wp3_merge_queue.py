"""WP3 merge queue tests (Revision 4): one job at a time, rebase, suite, push only if main is the tested commit (at most 3 repeats).
Real git on temp repos; the suite is a fake that records what it was asked to test. No Docker, no model call."""
from pathlib import Path
from types import SimpleNamespace

import launch
import merge_queue
from wp3_fakes import FakeApi, commit_in, git, make_repos, push_to_main

OK_SNAPSHOT = {"free_requests": 1000, "running": 0, "review_spend_usd": 0.0, "recent_states": []}


def make_env(tmp_path, hook=None, suite_ok=True):
    origin, repo = make_repos(tmp_path)
    api = FakeApi(tmp_path / "home")
    launcher = launch.Launcher(repo, api, snapshot=lambda: dict(OK_SNAPSHOT))
    env = SimpleNamespace(root=tmp_path, origin=origin, repo=repo, api=api, launcher=launcher, runs=[], hook=hook)

    def run_suite(wt):
        # record the main the job was rebased onto, and whether the merge lease is held while the suite runs
        env.runs.append((Path(wt).name, git(repo, "rev-parse", "origin/main"), "merge" in api.lease.held))
        if env.hook:
            env.hook(env, Path(wt))
        return suite_ok, "fake suite"

    env.queue = merge_queue.MergeQueue(repo, api, launcher, run_suite)
    return env


def start(env, name, fname, text, claim=True):
    job = env.launcher.start(name, [fname] if claim else [])
    assert job["ok"], job
    commit_in(job["worktree"], fname, text, msg=f"{name} work")
    env.queue.submit(job["job_id"], job["gen"])
    return job


def move_main(env, name):
    push_to_main(env.origin, env.root, name, f"moved by {name}\n")


def test_two_jobs_merge_in_order_and_the_second_is_retested_on_the_moved_main(tmp_path):
    env = make_env(tmp_path)
    a = start(env, "alpha", "a.txt", "A\n")
    b = start(env, "beta", "b.txt", "B\n")
    out = env.queue.drain()

    assert out == [(a["job_id"], "merged"), (b["job_id"], "merged")]
    assert [name for name, _, _ in env.runs] == [a["job_id"], b["job_id"]]
    assert all(locked for _, _, locked in env.runs)                       # both suites ran under the merge lease
    assert env.runs[1][1] == git(env.origin, "rev-parse", "main~1")      # beta was tested on main with alpha already in it
    files = git(env.origin, "ls-tree", "--name-only", "main").split()
    assert {"a.txt", "b.txt", "base.txt"} <= set(files)
    assert env.api.registry.lookup(a["job_id"])["state"] == "merged"
    assert env.api.registry.lookup(b["job_id"])["state"] == "merged"
    assert not (env.api.home_dir / "wt" / a["job_id"]).exists()          # worktrees removed after the merge
    assert not (env.api.home_dir / "wt" / b["job_id"]).exists()
    assert env.api.claims.owner == {} and env.api.lease.held == {}        # claims and leases released


def test_a_main_that_moves_during_the_suite_is_retested_and_then_merged(tmp_path):
    env = make_env(tmp_path, hook=lambda e, wt: move_main(e, "other") if len(e.runs) == 1 else None)
    a = start(env, "alpha", "a.txt", "A\n")
    assert env.queue.drain() == [(a["job_id"], "merged")]
    assert len(env.runs) == 2                                              # tested, main moved, tested again
    assert env.runs[1][1] == git(env.origin, "rev-parse", "main~1")       # the second test ran on the moved main
    assert env.api.registry.lookup(a["job_id"])["result"]["attempts"] == 2


def test_a_main_that_keeps_moving_stops_after_three_repeats_without_a_push(tmp_path):
    env = make_env(tmp_path, hook=lambda e, wt: move_main(e, f"other{len(e.runs)}"))
    before = git(env.origin, "rev-parse", "main")
    a = start(env, "alpha", "a.txt", "A\n")
    assert env.queue.drain() == [(a["job_id"], "main-moved")]
    assert len(env.runs) == 4                                              # the first try plus three repeats
    assert "a.txt" not in git(env.origin, "ls-tree", "--name-only", "main").split()
    assert git(env.origin, "rev-parse", "main") != before                  # only the other writer moved main
    assert env.api.registry.lookup(a["job_id"])["state"] == "main-moved"


def test_a_conflict_with_main_ends_merge_conflict_and_keeps_the_worktree(tmp_path):
    env = make_env(tmp_path)
    a = start(env, "alpha", "shared.txt", "from alpha\n")
    b = start(env, "beta", "shared.txt", "from beta\n", claim=False)
    out = env.queue.drain()

    assert out == [(a["job_id"], "merged"), (b["job_id"], "merge-conflict")]
    assert [name for name, _, _ in env.runs] == [a["job_id"]]              # beta never reached the suite
    wt_b = Path(b["worktree"])
    assert wt_b.is_dir()                                                   # kept for the owner to resolve
    assert git(wt_b, "status", "--porcelain") == ""                        # the failed rebase was aborted
    assert env.api.registry.lookup(b["job_id"])["state"] == "merge-conflict"
    assert env.api.lease.held == {}


def test_a_failing_suite_ends_tests_failed_and_nothing_is_pushed(tmp_path):
    env = make_env(tmp_path, suite_ok=False)
    before = git(env.origin, "rev-parse", "main")
    a = start(env, "alpha", "a.txt", "A\n")
    assert env.queue.drain() == [(a["job_id"], "tests-failed")]
    assert git(env.origin, "rev-parse", "main") == before
