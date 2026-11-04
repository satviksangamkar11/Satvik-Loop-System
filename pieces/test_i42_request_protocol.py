"""I42: enqueue dedupe and dequeue/start are one protocol: pending -> claimed -> running, each step under the existing locks. The launcher's
request check is registry.begin (atomic, under home/lock), not a lookup followed by a record; a start refused after its request began
puts the request row back. No model call."""
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import fix_loop as fl
import launch
import loop_home
from wp3_repos import live_leases, make_repos

OK_BUDGET = {"free_requests": 1000, "review_spend_usd": 0.0}


def env_for(tmp_path):
    origin, repo = make_repos(tmp_path)
    api = launch.real_api(tmp_path / "home")
    launcher = launch.Launcher(repo, api, budget=lambda: dict(OK_BUDGET), wait_s=0)
    return SimpleNamespace(repo=repo, api=api, launcher=launcher, home=api.home)


def job_file(tmp_path, name="alpha"):
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps({"name": name, "files": ["base.txt"]}), encoding="utf-8")
    return path


def dead_pid():
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def worktrees(env):
    folder = env.home / "wt"
    return sorted(folder.iterdir()) if folder.exists() else []


def test_an_identical_job_that_a_live_loop_has_claimed_is_a_duplicate(tmp_path):
    env = env_for(tmp_path)
    src = job_file(tmp_path)
    fl.enqueue(src, env.home)
    assert fl.take_next(env.home) is not None                 # claimed by this live process
    res = fl.try_enqueue(src, env.home)
    assert res["queued"] is False and res["reason"] == "duplicate-claimed" and res["job"].endswith("-alpha.json")


def test_a_claim_of_a_dead_owner_does_not_block_a_new_enqueue(tmp_path):
    env = env_for(tmp_path)
    src = job_file(tmp_path)
    fl.enqueue(src, env.home)
    taken = fl.take_next(env.home)
    fl.claim_file(taken).write_text(json.dumps({"state": "claimed", "pid": dead_pid(), "start": None}), encoding="utf-8")
    assert fl.try_enqueue(src, env.home)["queued"] is True


def test_a_racing_launcher_cannot_start_a_request_that_another_live_job_runs(tmp_path):
    """The lookup a launcher made before another one began would say no run is live. The atomic check in registry.begin still refuses."""
    env = env_for(tmp_path)
    gen = env.api.lease.acquire("pilot-old")
    env.api.registry.begin("pilot", "pilot-old", gen, "base")
    env.api.registry.lookup = lambda key: None                # the stale read a racing start made
    res = env.launcher.start("pilot", [], key="pilot")
    assert res == {"ok": False, "reason": "request-running", "job": "pilot-old"}
    assert live_leases(env.home) == ["pilot-old"]              # nothing of the refused start is left behind
    assert worktrees(env) == []


def test_a_started_request_has_its_running_row_from_the_atomic_begin(tmp_path):
    env = env_for(tmp_path)
    res = env.launcher.start("pilot", ["base.txt"], key="pilot")
    assert res["ok"] is True
    row = env.api.registry.lookup("pilot")
    assert row["state"] == "running" and row["job"] == res["job_id"] and row["gen"] == res["gen"]


def test_a_start_refused_after_its_request_began_puts_the_previous_row_back(tmp_path):
    env = env_for(tmp_path)
    old = env.api.lease.acquire("pilot-old")
    env.api.registry.record("pilot", "pilot-old", old, "passed", "base-old", {"round": 3})
    env.api.lease.release("pilot-old", old)
    holder = env.api.lease.acquire("holder")
    assert env.api.claims.take("holder", holder, ["base.txt"])[0]     # the file is busy: the start is refused after begin
    res = env.launcher.start("pilot", ["base.txt"], key="pilot")
    assert res["reason"] == "claim-conflict"
    row = env.api.registry.lookup("pilot")
    assert row["state"] == "passed" and row["job"] == "pilot-old" and row["result"] == {"round": 3}
    assert worktrees(env) == [] and live_leases(env.home) == ["holder"]


def test_a_refused_request_with_no_earlier_row_leaves_none(tmp_path):
    env = env_for(tmp_path)
    holder = env.api.lease.acquire("holder")
    assert env.api.claims.take("holder", holder, ["base.txt"])[0]
    assert env.launcher.start("pilot", ["base.txt"], key="pilot")["reason"] == "claim-conflict"
    assert env.api.registry.lookup("pilot") is None
