"""close-1 steps 4-8: fix_loop runs a job through the launcher (fake run, real git on temp repos, the real WP2 objects). No model call.
Each step adds its own tests under its own prefix: step4_, step5_, step6_, step7_, step8_."""
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import fix_loop as fl
import launch
from wp3_repos import git, live_leases, make_repos

JOB = {"name": "pilot", "files": ["base.txt"]}
OK_BUDGET = {"free_requests": 1000, "review_spend_usd": 0.0}


class FakeMerge:
    def __init__(self):
        self.submitted = []

    def submit(self, job_id, gen):
        self.submitted.append((job_id, gen))


def env_for(tmp_path, wait_s=0.0, sleep=time.sleep):
    origin, repo = make_repos(tmp_path)
    api = launch.real_api(tmp_path / "home")
    launcher = launch.Launcher(repo, api, budget=lambda: dict(OK_BUDGET), wait_s=wait_s, poll_s=0.01, sleep=sleep)
    return SimpleNamespace(repo=repo, api=api, launcher=launcher, home=api.home, origin=origin)


def fake_run(state, calls=None):
    def run(job, wt, **kwargs):
        if calls is not None:
            calls.append((job["name"], Path(wt).is_dir()))
        return {"name": job["name"], "state": state, "round": 1}
    return run


def worktree_dirs(env):
    wt_dir = env.home / "wt"
    return sorted(os.listdir(wt_dir)) if wt_dir.exists() else []


# ---------------------------------------------------------------- step 4: launcher start and finish
def test_step4_request_already_running_refuses_the_start_and_runs_nothing(tmp_path):
    env = env_for(tmp_path)
    gen = env.api.lease.acquire("pilot-old")                    # a live run of the same request
    env.api.registry.record("pilot", "pilot-old", gen, "running", "base", None)
    calls = []
    res = fl.run_launched(JOB, env.repo, env.api, env.launcher, run=fake_run("passed", calls))
    assert res == {"name": "pilot", "state": "refused", "reason": "request-running"}
    assert calls == [] and worktree_dirs(env) == [] and live_leases(env.home) == ["pilot-old"]


def test_step4_queued_claim_waits_for_the_other_job_to_release(tmp_path):
    env = env_for(tmp_path, wait_s=5.0)
    gen_other = env.api.lease.acquire("other")
    assert env.api.claims.take("other", gen_other, ["base.txt"])[0]

    def release_on_first_wait(_seconds):
        env.api.claims.release("other", gen_other)

    env.launcher.sleep = release_on_first_wait
    res = fl.run_launched(JOB, env.repo, env.api, env.launcher, run=fake_run("passed"))
    assert res["state"] == "passed"


def test_step4_claim_conflict_refuses_at_once_without_a_wait(tmp_path):
    env = env_for(tmp_path, wait_s=0.0)
    gen_other = env.api.lease.acquire("other")
    env.api.claims.take("other", gen_other, ["base.txt"])
    res = fl.run_launched(JOB, env.repo, env.api, env.launcher, run=fake_run("passed"))
    assert res["state"] == "refused" and res["reason"] == "claim-conflict"
    assert worktree_dirs(env) == []


def test_step4_passed_job_is_queued_and_kept_while_a_failed_one_loses_its_worktree(tmp_path):
    env = env_for(tmp_path)
    merge = FakeMerge()
    ok = fl.run_launched(JOB, env.repo, env.api, env.launcher, merge_queue=merge, run=fake_run("passed"))
    assert ok["state"] == "passed" and len(merge.submitted) == 1
    assert worktree_dirs(env) == [ok["job_id"]]                 # kept for the merge queue
    bad = fl.run_launched(JOB, env.repo, env.api, env.launcher, merge_queue=merge, run=fake_run("tests-failed"))
    assert bad["state"] == "tests-failed" and len(merge.submitted) == 1
    assert bad["job_id"] not in worktree_dirs(env)              # removed at finish


def test_step4_lease_is_kept_alive_during_the_run_and_stopped_after(tmp_path):
    env = env_for(tmp_path)
    events = []
    real_keep_alive = env.api.lease.keep_alive

    def spy(job_id, gen, every_s=30):
        stop = real_keep_alive(job_id, gen, every_s=every_s)
        events.append("start")

        def stop_and_record():
            stop()
            events.append("stop")
        return stop_and_record

    env.api.lease.keep_alive = spy

    def run(job, wt, **kwargs):
        events.append("run")
        return {"name": job["name"], "state": "tests-failed", "round": 0}

    fl.run_launched(JOB, env.repo, env.api, env.launcher, run=run)
    assert events == ["start", "run", "stop"]


def test_step4_a_run_that_raises_is_finished_as_error_and_its_lease_is_released(tmp_path):
    env = env_for(tmp_path)

    def boom(job, wt, **kwargs):
        raise RuntimeError("model backend broke")

    with pytest.raises(RuntimeError):
        fl.run_launched(JOB, env.repo, env.api, env.launcher, run=boom)
    assert live_leases(env.home) == []
    assert env.api.registry.lookup("pilot")["state"] == "error"


def test_step4_ledger_budget_reads_the_account_ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("SATVIK_HOME", str(tmp_path / "shared"))
    budget = fl.ledger_budget()
    assert budget == {"free_requests": 0, "review_spend_usd": 0.0}
