"""I40: the merge queue is durable. A submitted job is on disk before anything else, a crash after the push is reconciled with origin/main
(recorded merged, never pushed again), a crash before the push is retried from the start, and a git error does not stop the drainer.
Real git on temp repos; the suite is a fake. No Docker, no model call."""
import subprocess
from pathlib import Path
from types import SimpleNamespace

import launch
import merge_queue
from wp3_repos import commit_in, git, make_repos

OK_BUDGET = {"free_requests": 1000, "review_spend_usd": 0.0}


class SimulatedCrash(BaseException):
    """Stands in for the process dying: BaseException, so the drainer's own error handling does not catch it."""


def make_env(tmp_path, suite=None):
    origin, repo = make_repos(tmp_path)
    api = launch.real_api(tmp_path / "home")
    launcher = launch.Launcher(repo, api, budget=lambda: dict(OK_BUDGET))
    env = SimpleNamespace(root=tmp_path, origin=origin, repo=repo, api=api, launcher=launcher, runs=[])

    def run_suite(wt):
        env.runs.append(Path(wt).name)
        if suite is not None:
            suite(env, Path(wt))
        return True, "fake suite"

    env.run_suite = run_suite
    env.queue = merge_queue.MergeQueue(repo, api, launcher, run_suite)
    return env


def start(env, name, fname, text):
    job = env.launcher.start(name, [fname])
    assert job["ok"], job
    commit_in(job["worktree"], fname, text, msg=f"{name} work")
    env.queue.submit(job["job_id"], job["gen"])
    return job


def restart(env):
    """A new queue object over the same home, as after a loop restart."""
    return merge_queue.MergeQueue(env.repo, env.api, env.launcher, env.run_suite)


def test_a_submitted_job_is_on_disk_before_any_drain(tmp_path):
    env = make_env(tmp_path)
    a = start(env, "alpha", "a.txt", "A\n")
    entry = env.queue.folder / f"{a['job_id']}.json"
    assert entry.is_file()                                     # durable before the drain
    assert [e["job_id"] for e in restart(env).pending] == [a["job_id"]]
    assert restart(env).drain() == [(a["job_id"], "merged")]
    assert restart(env).pending == []


def test_a_crash_after_the_push_is_reconciled_and_the_job_is_not_pushed_again(tmp_path):
    env = make_env(tmp_path)
    a = start(env, "alpha", "a.txt", "A\n")
    real_finish = env.launcher.finish
    calls = []

    def dying_finish(*args, **kwargs):
        calls.append(args[2])
        if len(calls) == 1:
            raise SimulatedCrash("power cut after the push")
        return real_finish(*args, **kwargs)

    env.launcher.finish = dying_finish
    try:
        env.queue.drain()
    except SimulatedCrash:
        pass
    else:
        raise AssertionError("the crash did not happen")
    pushed_main = git(env.origin, "rev-parse", "main")
    assert "a.txt" in git(env.origin, "ls-tree", "--name-only", "main").split()
    entry = restart(env).pending[0]
    assert entry["state"] == "pushed" and entry["head"]          # the push is on record

    env.launcher.finish = real_finish
    runs_before = len(env.runs)
    assert restart(env).drain() == [(a["job_id"], "merged")]
    assert len(env.runs) == runs_before                          # reconciled: no suite run, so nothing was pushed again
    assert git(env.origin, "rev-parse", "main") == pushed_main
    assert env.api.registry.lookup(a["job_id"])["state"] == "merged"
    assert restart(env).pending == []                            # done


def test_a_crash_before_the_push_is_retried_from_the_start(tmp_path):
    def die_in_suite(env, wt):
        if len(env.runs) == 1:
            raise SimulatedCrash("power cut during the suite")

    env = make_env(tmp_path, suite=die_in_suite)
    before = git(env.origin, "rev-parse", "main")
    a = start(env, "alpha", "a.txt", "A\n")
    try:
        env.queue.drain()
    except SimulatedCrash:
        pass
    assert git(env.origin, "rev-parse", "main") == before         # nothing reached main
    assert restart(env).drain() == [(a["job_id"], "merged")]
    assert len(env.runs) == 2                                    # tried again, the suite ran again
    assert "a.txt" in git(env.origin, "ls-tree", "--name-only", "main").split()


def test_a_git_error_does_not_stop_the_drainer(tmp_path):
    def broken_once(env, wt):
        if len(env.runs) == 1:
            raise subprocess.CalledProcessError(128, ["git", "fetch"], stderr="remote hung up")

    env = make_env(tmp_path, suite=broken_once)
    a = start(env, "alpha", "a.txt", "A\n")
    b = start(env, "beta", "b.txt", "B\n")
    out = env.queue.drain()                                      # no exception escapes
    assert out == [(a["job_id"], "retry"), (b["job_id"], "merged")]
    assert env.queue.drain() == [(a["job_id"], "merged")]
    files = git(env.origin, "ls-tree", "--name-only", "main").split()
    assert {"a.txt", "b.txt"} <= set(files)


def test_repeated_git_errors_end_the_entry_as_merge_error(tmp_path):
    def always_broken(env, wt):
        raise subprocess.CalledProcessError(128, ["git", "push"], stderr="boom")

    env = make_env(tmp_path, suite=always_broken)
    a = start(env, "alpha", "a.txt", "A\n")
    results = [env.queue.drain() for _ in range(merge_queue.MAX_ERRORS)]
    assert results[-1] == [(a["job_id"], "merge-error")]
    assert restart(env).pending == []
    assert env.api.registry.lookup(a["job_id"])["state"] == "merge-error"
