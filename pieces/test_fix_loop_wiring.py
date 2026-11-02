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
    sl = fl._sibling_module("shared_limits")
    budget = fl.ledger_budget()
    assert budget == {"free_requests": sl.FREE_DAILY_CAP, "review_spend_usd": 0.0}      # free_requests is what is left today


def ledger_env(tmp_path, monkeypatch):
    """A launcher whose budget is the real ledger (fix_loop.ledger_budget) under SATVIK_HOME, on temp repos. No model call."""
    home = tmp_path / "home"
    monkeypatch.setenv("SATVIK_HOME", str(home))
    origin, repo = make_repos(tmp_path)
    api = launch.real_api(home)
    launcher = launch.Launcher(repo, api, budget=fl.ledger_budget, wait_s=0.0, poll_s=0.01)
    return SimpleNamespace(repo=repo, api=api, launcher=launcher, home=home)


def test_step4_a_fresh_day_lets_the_launcher_start(tmp_path, monkeypatch):
    env = ledger_env(tmp_path, monkeypatch)
    res = env.launcher.start("alpha", ["base.txt"])
    assert res["ok"], res


def test_step4_a_day_at_the_free_cap_refuses_the_start_with_free_requests(tmp_path, monkeypatch):
    env = ledger_env(tmp_path, monkeypatch)
    sl = fl._sibling_module("shared_limits")
    monkeypatch.setattr(sl, "FREE_DAILY_CAP", 2)                # two sends fill the day's cap
    gen = env.api.lease.acquire("filler")
    sl.reserve_free("filler", gen)
    sl.reserve_free("filler", gen)
    assert fl.ledger_budget()["free_requests"] == 0
    assert env.launcher.start("alpha") == {"ok": False, "reason": "free-requests"}


# ---------------------------------------------------------------- step 5: stop switch, files restored
def test_step5_stop_switch_before_a_round_stops_the_job_and_restores_its_files(tmp_path):
    env = env_for(tmp_path)
    job = {"name": "pilot", "files": ["base.txt", "new.txt"]}
    seen = {}
    real_finish = env.launcher.finish

    def finish_spy(job_id, gen, state, base=None, result=None):
        seen["base"] = (Path(env.home / "wt" / job_id) / "base.txt").read_text(encoding="utf-8")
        seen["new_exists"] = (Path(env.home / "wt" / job_id) / "new.txt").exists()
        seen["state"] = state
        return real_finish(job_id, gen, state, base, result)

    env.launcher.finish = finish_spy

    def run(job, wt, stop_check=None, **kwargs):
        (Path(wt) / "base.txt").write_text("half-written by a round\n", encoding="utf-8")
        (Path(wt) / "new.txt").write_text("new\n", encoding="utf-8")
        (env.home / "STOP").write_text("", encoding="utf-8")       # the operator flips the switch
        stop_check()                                               # the round loop's check before the next round
        raise AssertionError("the round must not start")

    res = fl.run_launched(job, env.repo, env.api, env.launcher, run=run)
    assert res["state"] == "stopped"
    assert seen == {"base": "base\n", "new_exists": False, "state": "stopped"}
    assert live_leases(env.home) == []


def test_step5_without_the_switch_the_check_does_nothing_and_the_job_finishes(tmp_path):
    env = env_for(tmp_path)
    calls = []

    def run(job, wt, stop_check=None, **kwargs):
        stop_check()                                               # switch off: no raise
        calls.append("round")
        return {"name": job["name"], "state": "passed", "round": 0}

    res = fl.run_launched(JOB, env.repo, env.api, env.launcher, merge_queue=FakeMerge(), run=run)
    assert res["state"] == "passed" and calls == ["round"]


def test_step5_restore_files_checks_out_tracked_and_removes_new(tmp_path):
    env = env_for(tmp_path)
    wt = tmp_path / "wt-direct"
    git(env.repo, "worktree", "add", "-q", "-b", "job/direct", str(wt), "origin/main")
    (wt / "base.txt").write_text("changed\n", encoding="utf-8")
    (wt / "new.txt").write_text("new\n", encoding="utf-8")
    fl.restore_files(wt, ["base.txt", "new.txt"])
    assert (wt / "base.txt").read_text(encoding="utf-8") == "base\n"
    assert not (wt / "new.txt").exists()


# ---------------------------------------------------------------- step 6: job queue in SATVIK_HOME/queue/
def write_job(tmp_path, name):
    path = tmp_path / f"{name}.json"
    path.write_text(name, encoding="utf-8")
    return path


def json_loader(path):
    """Fake loader: the file holds the job name; the job is otherwise minimal (run_launched only needs name and files)."""
    return {"name": Path(path).read_text(encoding="utf-8"), "files": ["base.txt"]}


def test_step6_queue_is_taken_oldest_first_and_each_job_once(tmp_path):
    env = env_for(tmp_path)
    for n in ("first", "second", "third"):
        fl.enqueue(write_job(tmp_path, n), env.home)
    taken = [fl.take_next(env.home).name for _ in range(3)]
    assert [t.split("-", 2)[2] for t in taken] == ["first.json", "second.json", "third.json"]
    assert fl.take_next(env.home) is None
    assert sorted(os.listdir(env.home / "queue" / "taken")) == sorted(taken)


def test_step6_drain_runs_every_queued_job_in_order_and_empties_the_queue(tmp_path):
    env = env_for(tmp_path)
    merge = FakeMerge()
    for n in ("alpha", "beta"):
        fl.enqueue(write_job(tmp_path, n), env.home)
    out = fl.drain_queue(env.api, env.launcher, merge_queue=merge, run=fake_run("passed"), loader=json_loader)
    assert [name.split("-", 2)[2] for name, _ in out] == ["alpha.json", "beta.json"]
    assert [state for _, state in out] == ["passed", "passed"]
    assert len(merge.submitted) == 2
    assert fl.take_next(env.home) is None


def test_step6_an_unreadable_job_file_is_reported_and_the_drain_goes_on(tmp_path):
    env = env_for(tmp_path)
    fl.enqueue(write_job(tmp_path, "good"), env.home)
    bad = fl.enqueue(write_job(tmp_path, "bad"), env.home)

    def loader(path):
        if "bad" in Path(path).name:
            raise ValueError("schema")
        return json_loader(path)

    out = fl.drain_queue(env.api, env.launcher, run=fake_run("tests-failed"), loader=loader)
    assert [state for _, state in out] == ["tests-failed", "invalid"]      # good was queued first


# ---------------------------------------------------------------- step 7: docs/changes/<job_id>.md and the README index
def test_step7_a_kept_worktree_carries_its_change_doc_committed_on_the_job_branch(tmp_path):
    env = env_for(tmp_path)
    res = fl.run_launched(JOB, env.repo, env.api, env.launcher, merge_queue=FakeMerge(), run=fake_run("passed"))
    wt = env.home / "wt" / res["job_id"]
    doc = wt / "docs" / "changes" / f"{res['job_id']}.md"
    assert "- State: passed" in doc.read_text(encoding="utf-8")
    assert git(wt, "log", "-1", "--format=%s") == f"change doc: {res['job_id']} (passed)"
    assert git(wt, "status", "--porcelain") == ""


def test_step7_the_index_line_goes_above_the_end_marker_once(tmp_path):
    import merge_queue as mq
    wt = tmp_path / "wt"
    (wt / "docs" / "changes").mkdir(parents=True)
    (wt / "docs" / "changes" / "job-1.md").write_text("# job-1\n", encoding="utf-8")
    (wt / "README.md").write_text("intro\n\n<!-- change-index:start -->\n<!-- change-index:end -->\n", encoding="utf-8")
    assert mq.add_index_line(wt, "job-1") is True
    assert mq.add_index_line(wt, "job-1") is False                  # retry after a rebase: no duplicate
    text = (wt / "README.md").read_text(encoding="utf-8")
    assert text.index("- docs/changes/job-1.md") < text.index("<!-- change-index:end -->")
    assert text.count("- docs/changes/job-1.md") == 1


def test_step7_no_marker_or_no_doc_means_no_index_change(tmp_path):
    import merge_queue as mq
    wt = tmp_path / "wt"
    (wt / "docs" / "changes").mkdir(parents=True)
    (wt / "docs" / "changes" / "job-2.md").write_text("# job-2\n", encoding="utf-8")
    (wt / "README.md").write_text("no markers here\n", encoding="utf-8")
    assert mq.add_index_line(wt, "job-2") is False
    (wt / "README.md").write_text("<!-- change-index:end -->\n", encoding="utf-8")
    assert mq.add_index_line(wt, "job-missing") is False


# ---------------------------------------------------------------- step 8: counters in the registry, shown by loop_status
def test_step8_counters_are_kept_apart_from_the_request_rows(tmp_path):
    env = env_for(tmp_path)
    assert env.api.registry.bump("started") == 1
    assert env.api.registry.bump("started", by=2) == 3
    assert env.api.registry.counters() == {"started": 3}
    assert env.api.registry.recent(10) == []                    # a counter is not a request row


def test_step8_launcher_counts_starts_refusals_and_finishes(tmp_path):
    env = env_for(tmp_path)
    gen = env.api.lease.acquire("pilot-old")
    env.api.registry.record("pilot", "pilot-old", gen, "running", "base", None)
    fl.run_launched(JOB, env.repo, env.api, env.launcher, run=fake_run("passed"))     # refused: request-running
    env.api.lease.release("pilot-old", gen)
    fl.run_launched(JOB, env.repo, env.api, env.launcher, merge_queue=FakeMerge(), run=fake_run("passed"))
    assert env.api.registry.counters() == {"refused:request-running": 1, "started": 1, "finished:passed": 1}


def test_step8_counter_lines_are_sorted_name_equals_count(tmp_path):
    env = env_for(tmp_path)
    env.api.registry.bump("started")
    env.api.registry.bump("finished:passed", by=2)
    assert launch.counter_lines(env.home) == ["finished:passed = 2", "started = 1"]
