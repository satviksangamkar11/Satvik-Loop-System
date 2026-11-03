"""loop_queue command line: enqueue, drain (stop switch before each take, merge drain after each job, LockTimeout retried), counters.
Real git on temp repos and the real WP2 loop home under SATVIK_HOME. The run and the merge suite are fakes. No Docker, no model call."""
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import fix_loop
import launch
import loop_home
import loop_queue as lq
import merge_queue
from wp3_repos import git, live_leases, make_repos


def fake_loader(path):
    """The queued file holds {"name", "files"}: all that run_launched needs."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def make_env(tmp_path, monkeypatch, suite_ok=True):
    origin, repo = make_repos(tmp_path)
    home = tmp_path / "home"
    monkeypatch.setenv("SATVIK_HOME", str(home))
    env = SimpleNamespace(tmp=tmp_path, origin=origin, repo=repo, home=home, runs=[], suites=[], lines=[], hook=None, clock=[0.0])

    def run(job, wt, **kwargs):
        env.runs.append(job["name"])
        if env.hook:
            env.hook(env, job)
        for rel in job["files"]:
            (Path(wt) / rel).write_text(f"{job['name']} was here\n", encoding="utf-8")
        return {"name": job["name"], "state": "passed", "round": 1}

    def suite(wt):
        env.suites.append(Path(wt).name)
        return suite_ok, "fake suite"

    def sleep(s):
        env.clock[0] += s

    env.run, env.suite, env.sleep = run, suite, sleep
    env.drain = lambda **kw: lq.drain(home, repo, run=kw.pop("run", run), suite=suite, loader=fake_loader, sleep=sleep,
                                      clock=lambda: env.clock[0], say=env.lines.append, **kw)
    return env


def job_file(env, name, files=None):
    path = env.tmp / f"{name}.json"
    path.write_text(json.dumps({"name": name, "files": files or [f"{name}.txt"]}), encoding="utf-8")
    return path


def queue(env, *names):
    for name in names:
        fix_loop.enqueue(job_file(env, name), env.home)


def queued(env):
    folder = env.home / "queue"
    return sorted(p.name.split("-", 2)[2] for p in folder.glob("*.json")) if folder.exists() else []


def on_main(env):
    return git(env.origin, "ls-tree", "--name-only", "main").split()


def lines_starting(env, word):
    return [x for x in env.lines if x.startswith(word + " ")]


# ---------------------------------------------------------------- enqueue
def test_enqueue_queues_every_file_in_the_order_given(tmp_path, monkeypatch, capsys):
    env = make_env(tmp_path, monkeypatch)
    files = [str(job_file(env, n)) for n in ("beta", "alpha")]
    assert lq.main(["enqueue", *files], loader=fake_loader) == 0
    out = capsys.readouterr().out.splitlines()
    assert [x.split()[0] for x in out] == ["queued", "queued"]
    assert [x.split("-", 2)[2] for x in out] == ["beta.json", "alpha.json"]
    assert [fix_loop.take_next(env.home).name.split("-", 2)[2] for _ in range(2)] == ["beta.json", "alpha.json"]


def test_enqueue_with_one_invalid_file_queues_nothing(tmp_path, monkeypatch, capsys):
    env = make_env(tmp_path, monkeypatch)
    bad = env.tmp / "bad.json"
    bad.write_text("{}", encoding="utf-8")                                # the real loader: fails the job schema
    not_yaml = env.tmp / "notes.md"
    not_yaml.write_text("# notes\n\n**bold** text\n", encoding="utf-8")   # yaml raises its own error, not ValueError
    assert lq.main(["enqueue", str(job_file(env, "good")), str(bad), str(not_yaml)]) == 2
    err = capsys.readouterr().err
    assert "bad.json" in err and "notes.md" in err
    assert queued(env) == []


def test_load_production_refuses_a_legacy_job(monkeypatch):
    monkeypatch.setattr(fix_loop, "load_job", lambda path: {"name": "x", "profile": "legacy"})
    with pytest.raises(ValueError, match="production"):
        lq.load_production("x.json")
    monkeypatch.setattr(fix_loop, "load_job", lambda path: {"name": "x", "profile": "production"})
    assert lq.load_production("x.json")["profile"] == "production"


def test_every_command_needs_satvik_home(monkeypatch, capsys):
    monkeypatch.delenv("SATVIK_HOME", raising=False)
    assert lq.main(["counters"]) == 2
    assert "SATVIK_HOME" in capsys.readouterr().err


def test_every_flag_is_documented_in_the_module_docstring():
    for word in ("enqueue", "drain", "counters", "--root", "--claim-wait-s", "--lock-wait-s", "SATVIK_HOME/STOP"):
        assert word in lq.__doc__, word


# ---------------------------------------------------------------- drain
def test_drain_runs_and_merges_each_job_in_order_then_prints_the_counters(tmp_path, monkeypatch, capsys):
    env = make_env(tmp_path, monkeypatch)
    queue(env, "alpha", "beta")
    code = lq.main(["drain", "--root", str(env.repo)], run=env.run, suite=env.suite, loader=fake_loader)
    out = capsys.readouterr().out.splitlines()

    assert code == 0
    assert env.runs == ["alpha", "beta"]
    assert [x.split()[0] for x in out[:4]] == ["job", "merge", "job", "merge"]   # each job is merged before the next is taken
    assert all(x.endswith((" passed", " merged")) for x in out[:4])
    assert {"alpha.txt", "beta.txt"} <= set(on_main(env))
    assert git(env.origin, "show", "main:alpha.txt") == "alpha was here"      # the job's own edit reached main
    assert "finished:merged = 2" in out and "started = 2" in out              # the counter lines end the output
    assert out[4:] == sorted(out[4:])
    assert queued(env) == [] and live_leases(env.home) == []
    assert not any((env.home / "wt").iterdir())                                # merged worktrees removed


def test_stop_switch_before_the_first_take_runs_nothing(tmp_path, monkeypatch):
    env = make_env(tmp_path, monkeypatch)
    queue(env, "alpha")
    (env.home / "STOP").write_text("", encoding="utf-8")
    assert env.drain() == lq.EXIT_STOPPED
    assert env.runs == [] and queued(env) == ["alpha.json"]


def test_stop_switch_set_during_a_job_lets_it_merge_and_takes_no_other(tmp_path, monkeypatch):
    env = make_env(tmp_path, monkeypatch)
    env.hook = lambda e, job: (e.home / "STOP").write_text("", encoding="utf-8")
    queue(env, "alpha", "beta")
    assert env.drain() == lq.EXIT_STOPPED
    assert env.runs == ["alpha"] and env.suites != []
    assert "alpha.txt" in on_main(env)
    assert queued(env) == ["beta.json"]


def test_a_refused_start_puts_the_job_back_in_its_place_and_ends_the_drain(tmp_path, monkeypatch):
    env = make_env(tmp_path, monkeypatch)
    queue(env, "alpha", "beta")
    before = sorted(p.name for p in (env.home / "queue").glob("*.json"))
    monkeypatch.setattr(fix_loop, "ledger_budget", lambda: {"free_requests": 0, "review_spend_usd": 0.0})
    assert env.drain() == lq.EXIT_REFUSED
    assert env.runs == []
    assert sorted(p.name for p in (env.home / "queue").glob("*.json")) == before   # same names: alpha is still first
    assert any(x.endswith("refused free-requests") for x in lines_starting(env, "job"))


def test_an_invalid_job_file_is_reported_and_the_drain_goes_on(tmp_path, monkeypatch):
    env = make_env(tmp_path, monkeypatch)
    queue(env, "alpha")
    (env.home / "queue" / "99999999999999999999-1-zz.json").write_text("not json", encoding="utf-8")
    assert env.drain() == 0
    assert env.runs == ["alpha"]
    assert any(" invalid " in x for x in lines_starting(env, "job"))


def test_a_run_that_raises_is_an_error_and_the_drain_goes_on(tmp_path, monkeypatch):
    env = make_env(tmp_path, monkeypatch)

    def run(job, wt, **kwargs):
        if job["name"] == "alpha":
            env.runs.append("alpha")
            raise RuntimeError("boom")
        return env.run(job, wt, **kwargs)

    queue(env, "alpha", "beta")
    assert env.drain(run=run) == 0
    assert env.runs == ["alpha", "beta"]
    jobs = lines_starting(env, "job")
    assert "error RuntimeError: boom" in jobs[0] and jobs[1].endswith(" passed")
    assert env.suites != [] and "beta.txt" in on_main(env) and "alpha.txt" not in on_main(env)


def test_no_merge_drain_is_tried_when_nothing_passed(tmp_path, monkeypatch):
    env = make_env(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(merge_queue.MergeQueue, "drain", lambda self: calls.append(1) or [])
    queue(env, "alpha")
    assert env.drain(run=lambda job, wt, **kw: {"name": job["name"], "state": "tests-failed", "round": 1}) == 0
    assert calls == []


# ---------------------------------------------------------------- LockTimeout
def test_a_lock_timeout_before_the_run_is_retried(tmp_path, monkeypatch):
    env = make_env(tmp_path, monkeypatch)
    real = fix_loop.run_launched
    fails = [1]

    def flaky(*args, **kwargs):
        if fails:
            fails.pop()
            raise loop_home.LockTimeout("home/lock busy")
        return real(*args, **kwargs)

    monkeypatch.setattr(fix_loop, "run_launched", flaky)
    queue(env, "alpha")
    assert env.drain() == 0
    assert env.runs == ["alpha"] and "alpha.txt" in on_main(env)
    assert "lock-retry = 1" in env.lines
    assert env.clock[0] == lq.LOCK_PAUSE_S


def test_a_lock_timeout_inside_the_run_is_not_retried(tmp_path, monkeypatch):
    env = make_env(tmp_path, monkeypatch)

    def run(job, wt, **kwargs):
        env.runs.append(job["name"])
        raise loop_home.LockTimeout("busy during the run")

    queue(env, "alpha")
    assert env.drain(run=run) == 0
    assert env.runs == ["alpha"]                                              # never run twice
    assert "error LockTimeout" in lines_starting(env, "job")[0]
    assert live_leases(env.home) == []                                        # finished as error


def test_a_merge_lock_timeout_is_retried_until_the_merge_goes_through(tmp_path, monkeypatch):
    env = make_env(tmp_path, monkeypatch)
    real = merge_queue.MergeQueue.drain
    fails = [1, 1]

    def busy(self):
        if fails:
            fails.pop()
            raise loop_home.LockTimeout("merge.lock held by the other loop")
        return real(self)

    monkeypatch.setattr(merge_queue.MergeQueue, "drain", busy)
    queue(env, "alpha")
    assert env.drain() == 0
    assert "alpha.txt" in on_main(env) and "lock-retry = 2" in env.lines


def test_a_merge_cut_off_mid_way_is_put_back_and_merged(tmp_path, monkeypatch):
    env = make_env(tmp_path, monkeypatch)
    real = merge_queue.MergeQueue._merge
    fails = [1]

    def cut(self, *args):
        if fails:
            fails.pop()
            raise loop_home.LockTimeout("busy mid-merge")
        return real(self, *args)

    monkeypatch.setattr(merge_queue.MergeQueue, "_merge", cut)
    queue(env, "alpha")
    assert env.drain() == 0
    merges = lines_starting(env, "merge")
    assert len(merges) == 1 and merges[0].endswith(" merged")
    assert "alpha.txt" in on_main(env) and live_leases(env.home) == []


def test_a_merge_lock_that_stays_busy_ends_the_drain_and_lists_the_unmerged_job(tmp_path, monkeypatch):
    env = make_env(tmp_path, monkeypatch)

    def busy(self):
        raise loop_home.LockTimeout("merge.lock held")

    monkeypatch.setattr(merge_queue.MergeQueue, "drain", busy)
    queue(env, "alpha", "beta")
    assert env.drain(lock_wait_s=12) == lq.EXIT_LOCK
    assert env.runs == ["alpha"]                                              # no further job is taken
    unmerged = lines_starting(env, "unmerged")
    assert len(unmerged) == 1 and "alpha" in unmerged[0]
    job_id = unmerged[0].split()[1]
    assert (env.home / "wt" / job_id).is_dir()                                # the passed worktree stays
    assert queued(env) == ["beta.json"]


def test_a_start_lock_that_stays_busy_puts_the_job_back(tmp_path, monkeypatch):
    env = make_env(tmp_path, monkeypatch)

    def busy(*args, **kwargs):
        raise loop_home.LockTimeout("home/lock held")

    monkeypatch.setattr(fix_loop, "run_launched", busy)
    queue(env, "alpha")
    assert env.drain(lock_wait_s=12) == lq.EXIT_LOCK
    assert env.runs == [] and queued(env) == ["alpha.json"]


# ---------------------------------------------------------------- counters
def test_counters_prints_the_sorted_counter_lines(tmp_path, monkeypatch, capsys):
    env = make_env(tmp_path, monkeypatch)
    api = launch.real_api(env.home)
    api.registry.bump("started")
    api.registry.bump("finished:merged", 2)
    assert lq.main(["counters"]) == 0
    assert capsys.readouterr().out.splitlines() == ["finished:merged = 2", "started = 1"]
