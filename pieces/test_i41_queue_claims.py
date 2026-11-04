"""I41: a queued job's claim is durable. A taken job records its owner (pid and process start); a refusal for a busy resource or a limit goes
back to the queue; any other refusal goes to queue/refused/ with the reason in the registry; a dead owner's claim is recovered (a claimed job
goes back to the queue, a running one is refused and never run again on its own); a reused pid is a dead owner. No model call."""
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import fix_loop as fl
import launch
import loop_home
from wp3_repos import make_repos

OK_BUDGET = {"free_requests": 1000, "review_spend_usd": 0.0}


def env_for(tmp_path):
    origin, repo = make_repos(tmp_path)
    api = launch.real_api(tmp_path / "home")
    launcher = launch.Launcher(repo, api, budget=lambda: dict(OK_BUDGET), wait_s=0)
    return SimpleNamespace(repo=repo, api=api, launcher=launcher, home=api.home)


def enqueue(tmp_path, env, name, files=("base.txt",)):
    src = tmp_path / f"{name}.src.json"
    src.write_text(json.dumps({"name": name, "files": list(files)}), encoding="utf-8")
    return Path(fl.enqueue(src, env.home))


def loader(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def fake_run(state):
    def run(job, wt, **kwargs):
        return {"name": job["name"], "state": state, "round": 1}
    return run


def dead_pid():
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def write_claim(taken, **data):
    fl.claim_file(taken).write_text(json.dumps(data), encoding="utf-8")


def refused_names(env):
    folder = env.home / "queue" / "refused"
    return sorted(os.listdir(folder)) if folder.exists() else []


def test_a_taken_job_records_its_owner_pid_and_process_start(tmp_path):
    env = env_for(tmp_path)
    enqueue(tmp_path, env, "alpha")
    taken = fl.take_next(env.home)
    claim = json.loads(fl.claim_file(taken).read_text(encoding="utf-8"))
    assert claim["state"] == "claimed"
    assert claim["pid"] == os.getpid()
    assert claim["start"] == loop_home.process_start(os.getpid())


def test_a_busy_resource_refusal_puts_the_job_back_in_the_queue_and_the_next_start_runs_it(tmp_path):
    env = env_for(tmp_path)
    queued = enqueue(tmp_path, env, "alpha")
    gen = env.api.lease.acquire("holder")                    # a live job holds the file
    assert env.api.claims.take("holder", gen, ["base.txt"])[0]
    out = fl.drain_queue(env.api, env.launcher, run=fake_run("passed"), loader=loader)
    assert out == [(queued.name, "refused")]
    assert (env.home / "queue" / queued.name).is_file()      # back in the queue, not lost in taken/
    assert not (env.home / "queue" / "taken" / queued.name).exists()
    assert refused_names(env) == []
    env.api.claims.release("holder", gen)
    assert fl.drain_queue(env.api, env.launcher, run=fake_run("passed"), loader=loader) == [(queued.name, "passed")]


def test_a_job_that_cannot_run_goes_to_refused_with_its_reason_in_the_registry(tmp_path):
    env = env_for(tmp_path)
    queued = enqueue(tmp_path, env, "alpha")

    def bad_loader(path):
        raise ValueError("schema")

    assert fl.drain_queue(env.api, env.launcher, run=fake_run("passed"), loader=bad_loader) == [(queued.name, "invalid")]
    assert refused_names(env) == [queued.name]
    row = env.api.registry.lookup(f"refused:{queued.name}")
    assert row["state"] == "refused" and row["result"]["reason"].startswith("invalid: schema")
    assert fl.take_next(env.home) is None                    # nothing takes it again


def test_a_finished_job_keeps_a_done_claim_and_is_never_recovered(tmp_path):
    env = env_for(tmp_path)
    queued = enqueue(tmp_path, env, "alpha")
    assert fl.drain_queue(env.api, env.launcher, run=fake_run("passed"), loader=loader) == [(queued.name, "passed")]
    claim = json.loads(fl.claim_file(env.home / "queue" / "taken" / queued.name).read_text(encoding="utf-8"))
    assert claim["state"] == "done"
    assert fl.take_next(env.home) is None
    assert (env.home / "queue" / "taken" / queued.name).is_file()


def test_a_claimed_job_whose_owner_died_is_recovered_to_the_queue(tmp_path):
    env = env_for(tmp_path)
    queued = enqueue(tmp_path, env, "alpha")
    taken = fl.take_next(env.home)
    write_claim(taken, state="claimed", pid=dead_pid(), start=None, updated=0)
    again = fl.take_next(env.home)                           # the orphan is recovered, and this drainer takes it
    assert again is not None and again.name == queued.name
    assert json.loads(fl.claim_file(again).read_text(encoding="utf-8"))["pid"] == os.getpid()


def test_a_reused_pid_is_a_dead_owner(tmp_path):
    env = env_for(tmp_path)
    queued = enqueue(tmp_path, env, "alpha")
    taken = fl.take_next(env.home)
    write_claim(taken, state="claimed", pid=os.getpid(), start="not-this-process", updated=0)   # our pid, another start identity
    again = fl.take_next(env.home)
    assert again is not None and again.name == queued.name


def test_a_running_job_whose_owner_died_is_refused_and_never_run_again(tmp_path):
    env = env_for(tmp_path)
    queued = enqueue(tmp_path, env, "alpha")
    taken = fl.take_next(env.home)
    write_claim(taken, state="running", pid=dead_pid(), start=None, updated=0)
    assert fl.take_next(env.home) is None                    # its paid calls may have run: not taken again on its own
    assert refused_names(env) == [queued.name]
    assert env.api.registry.lookup(f"refused:{queued.name}")["result"]["reason"] == "owner-died-running"


def test_an_unreadable_claim_is_refused_not_guessed_at(tmp_path):
    env = env_for(tmp_path)
    queued = enqueue(tmp_path, env, "alpha")
    taken = fl.take_next(env.home)
    fl.claim_file(taken).write_text("{not json", encoding="utf-8")
    assert fl.take_next(env.home) is None
    assert refused_names(env) == [queued.name]
    assert env.api.registry.lookup(f"refused:{queued.name}")["result"]["reason"] == "claim-unreadable"


def test_a_start_that_is_refused_for_good_reason_is_not_requeued(tmp_path):
    """A refusal with no retry reason (here an approved plan that no longer matches) is refused, not put back to run later."""
    env = env_for(tmp_path)
    queued = enqueue(tmp_path, env, "alpha")
    taken = fl.take_next(env.home)
    res = fl.run_launched({"name": "alpha", "files": ["base.txt"]}, env.repo, env.api, env.launcher, run=fake_run("passed"), claim=taken,
                          plan={"plan_dir": str(tmp_path / "nowhere"), "job_sha256": "x", "plan_sha256": "y"})
    assert res["state"] == "refused"
    assert fl.settle_taken(taken, env.home, res) == "refused"
    assert refused_names(env) == [queued.name]
