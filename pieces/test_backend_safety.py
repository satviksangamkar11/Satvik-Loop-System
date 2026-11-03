"""Backend safety (2026-11-03, after the 4-loop test): every terminal job's evidence is archived before its worktree is removed, an archive that
fails never blocks the finish, the merge outcome is kept apart, the queue refuses active duplicates only, the job reports real waits, and a
job's client releases its own lease. Fakes only: no network, no model call, no Docker."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "source"))
sys.path.insert(0, str(REPO / "pieces"))
import event_log  # noqa: E402
import fix_loop as fl  # noqa: E402
import launch  # noqa: E402
import loop_home  # noqa: E402
import merge_queue as mq  # noqa: E402
import openrouter_call as oc  # noqa: E402
from test_fix_loop_wiring import JOB, FakeMerge, env_for  # noqa: E402


def writes_evidence(state):
    """A fake run that leaves a status.json and a key snapshot in the job's _loop, as the real run does."""
    def run(job, wt, **kwargs):
        folder = Path(wt) / "_loop" / job["name"]
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "status.json").write_text(json.dumps({"name": job["name"], "state": state}), encoding="utf-8")
        (Path(wt) / "_loop" / "openrouter").mkdir(parents=True, exist_ok=True)
        (Path(wt) / "_loop" / "openrouter" / "key_snapshot_free.json").write_text("{}", encoding="utf-8")
        return {"name": job["name"], "state": state, "round": 1}
    return run


def registry_row(env, job_id):
    """The job's registry row: rows are keyed by the request key (the job name), and the row names the job id."""
    row = json.loads((env.home / "registry.json").read_text(encoding="utf-8"))[JOB["name"]]
    assert row["job"] == job_id
    return row


# ---------------------------------------------------------------- 1. archive before cleanup, every terminal outcome

@pytest.mark.parametrize("state", ["passed", "escalate", "review-open", "exception"])
def test_every_terminal_job_is_archived_without_the_key_snapshots(tmp_path, state):
    env = env_for(tmp_path)
    res = fl.run_launched(JOB, env.repo, env.api, env.launcher, merge_queue=FakeMerge(), run=writes_evidence(state))
    archived = env.home / "results" / res["job_id"]
    assert json.loads((archived / JOB["name"] / "status.json").read_text(encoding="utf-8"))["state"] == state
    assert not (archived / "openrouter").exists()
    assert registry_row(env, res["job_id"])["result"]["archive"] == "archived"
    assert (env.home / "wt" / res["job_id"]).exists() == (state == "passed")      # passed waits for the merge; the rest are removed


def test_a_failed_job_keeps_its_evidence_after_its_worktree_is_removed(tmp_path):
    env = env_for(tmp_path)
    res = fl.run_launched(JOB, env.repo, env.api, env.launcher, merge_queue=FakeMerge(), run=writes_evidence("escalate"))
    assert not (env.home / "wt" / res["job_id"]).exists()
    assert (env.home / "results" / res["job_id"] / JOB["name"] / "status.json").is_file()


def test_an_archive_failure_keeps_the_worktree_and_still_releases_everything(tmp_path, monkeypatch):
    env = env_for(tmp_path)

    def broken(*args, **kwargs):
        raise OSError("disk full")
    monkeypatch.setattr(launch, "_copytree", broken)
    res = fl.run_launched(JOB, env.repo, env.api, env.launcher, merge_queue=FakeMerge(), run=writes_evidence("escalate"))
    row = registry_row(env, res["job_id"])
    assert row["state"] == "escalate"                                              # the real terminal state, unchanged
    assert row["result"]["archive_failed"] is True and "disk full" in row["result"]["archive"]
    assert (env.home / "wt" / res["job_id"] / "_loop" / JOB["name"] / "status.json").is_file()   # the evidence stays where it was
    assert not env.api.lease.held(res["job_id"])                                    # lease released
    claims = json.loads((env.home / "claims.json").read_text(encoding="utf-8"))["claims"]
    assert not any(e["job"] == res["job_id"] for entries in claims.values() for e in entries)    # claims released
    assert env.launcher._slots == {}                                                # sandbox slot released
    assert not list((env.home / "results").glob(".tmp-*"))                          # no half-written archive


def test_the_archive_leaves_out_the_loop_gitignore_and_keeps_the_evidence(tmp_path):
    """event_log writes _loop/.gitignore (`*`) beside the job's events. The archive must not copy it: a published evidence folder with that
    file is git-ignored in its entirety."""
    home = tmp_path / "home"
    folder = home / "wt" / "job-1" / "_loop" / "calc"
    event_log.emit(folder, "job-start", name="calc", base="a" * 40, branch="job/job-1", gen=1, key="calc")
    (folder / "status.json").write_text("{}", encoding="utf-8")
    assert (folder.parent / ".gitignore").read_text(encoding="utf-8") == "*\n"        # the file the archive must not copy
    assert launch.archive_job(home, "job-1") == "archived"
    assert not (home / "results" / "job-1" / ".gitignore").exists()
    assert (home / "results" / "job-1" / "calc" / "events.jsonl").is_file()
    assert (home / "results" / "job-1" / "calc" / "status.json").is_file()


def test_archiving_twice_keeps_the_first_archive(tmp_path):
    home = tmp_path / "home"
    folder = home / "wt" / "job-1" / "_loop" / "calc"
    folder.mkdir(parents=True)
    (folder / "status.json").write_text("first", encoding="utf-8")
    assert launch.archive_job(home, "job-1") == "archived"
    (folder / "status.json").write_text("second", encoding="utf-8")
    assert launch.archive_job(home, "job-1") == "kept"
    assert (home / "results" / "job-1" / "calc" / "status.json").read_text(encoding="utf-8") == "first"
    assert launch.archive_job(home, "no-such-job") == "none"


def test_the_merge_outcome_is_written_apart_from_the_execution_evidence(tmp_path):
    env = env_for(tmp_path)
    res = fl.run_launched(JOB, env.repo, env.api, env.launcher, merge_queue=FakeMerge(), run=writes_evidence("passed"))

    class Queue(mq.MergeQueue):
        pass
    queue = Queue(env.repo, env.api, env.launcher, run_suite=lambda wt: (True, "suite ok"))
    gen = json.loads((env.home / "leases" / f"{res['job_id']}.json").read_text(encoding="utf-8"))["generation"]
    queue._finish(res["job_id"], gen, "merged", "base123", {"head": "abc", "attempts": 1})
    merge = json.loads((env.home / "results" / res["job_id"] / "merge.json").read_text(encoding="utf-8"))
    assert merge["state"] == "merged" and merge["head"] == "abc" and merge["base"] == "base123"
    assert json.loads((env.home / "results" / res["job_id"] / JOB["name"] / "status.json").read_text(encoding="utf-8"))["state"] == "passed"


# ---------------------------------------------------------------- 2. queue dedupe of active work only

def job_file(tmp_path, name="calc"):
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps({"name": name, "files": ["a.py"]}), encoding="utf-8")
    return path


def test_enqueue_still_returns_a_path(tmp_path):
    assert isinstance(fl.enqueue(job_file(tmp_path), tmp_path / "home"), Path)


def test_two_identical_requests_at_once_leave_one_pending_job(tmp_path):
    home, job = tmp_path / "home", job_file(tmp_path)
    code = ("import sys; sys.path.insert(0, sys.argv[1]); import fix_loop as fl; from pathlib import Path; "
            "print(fl.try_enqueue(Path(sys.argv[2]), Path(sys.argv[3]))['queued'])")
    procs = [subprocess.Popen([sys.executable, "-c", code, str(REPO / "scripts" / "source"), str(job), str(home)],
                              stdout=subprocess.PIPE, text=True) for _ in range(2)]
    outs = sorted(p.communicate(timeout=60)[0].strip() for p in procs)
    assert outs == ["False", "True"]
    assert len(list((home / "queue").glob("*.json"))) == 1


def test_a_running_job_is_refused_and_a_finished_old_taken_file_is_not_active(tmp_path):
    home, job = tmp_path / "home", job_file(tmp_path)
    taken = Path(fl.enqueue(job, home))
    (home / "queue" / "taken").mkdir()
    os.replace(taken, home / "queue" / "taken" / taken.name)                  # claimed by a loop
    gen = loop_home.Lease(home).acquire("calc-1")
    loop_home.Registry(home).record("calc", "calc-1", gen, "running", "base", None)
    assert fl.try_enqueue(job, home) == {"queued": False, "reason": "duplicate-running", "job": "calc-1"}
    loop_home.Registry(home).record("calc", "calc-1", gen, "escalate", "base", None)   # it finished; its taken file stays behind
    loop_home.Lease(home).release("calc-1", gen)
    assert fl.try_enqueue(job, home)["queued"] is True                         # an old taken file is history, not active work


def test_a_completed_job_runs_again_only_with_rerun(tmp_path):
    home, job = tmp_path / "home", job_file(tmp_path)
    loop_home.Registry(home).record("calc", "calc-1", 1, "merged", "base", None)
    assert fl.try_enqueue(job, home)["reason"] == "already-completed"
    assert fl.try_enqueue(job, home, rerun=True)["queued"] is True
    assert fl.try_enqueue(job, home, rerun=True)["reason"] == "duplicate-pending"


# ---------------------------------------------------------------- 3. real waits, owned by the job

def client(tmp_path, transport=None):
    return oc.OpenRouter({"free": "sk-or-v1-FREETEST000000000000", "paid": "sk-or-v1-PAIDTEST000000000000"}, tmp_path / "ledger",
                         transport=transport or (lambda *a, **k: (500, {}, b"")), sleep=lambda s: None, rand=lambda: 0.0)


def test_a_wait_is_set_when_it_begins_and_cleared_when_it_ends_even_on_an_error(tmp_path):
    built, seen = client(tmp_path), []
    built.on_wait = seen.append
    with built.waiting("account-pace", "m"):
        assert seen[-1]["why"] == "account-pace" and seen[-1]["model"] == "m" and seen[-1]["since"]
    assert seen[-1] is None
    with pytest.raises(RuntimeError):
        with built.waiting("429-backoff", "m"):
            raise RuntimeError("boom")
    assert seen[-1] is None


def test_the_model_slot_wait_clears_when_the_slot_is_acquired_before_the_request(tmp_path, monkeypatch):
    monkeypatch.setenv("SATVIK_HOME", str(tmp_path / "home"))
    built, seen, at_send = client(tmp_path), [], []
    built.on_wait = seen.append
    built.set_job("job-1", 1)

    def post(*args, **kwargs):
        at_send.append(list(seen))
        return 200, {}, b"{}"
    monkeypatch.setattr(built, "_post", post)
    built._send("dots-studio/dots-3-note-preview:free", "free", "s", "u", 10, False, 30)       # the model slot wraps _post here
    assert at_send and at_send[0][0]["why"] == "model-slot" and at_send[0][-1] is None   # set, then cleared before the send


def test_a_retry_backoff_reports_its_wait(tmp_path):
    built, seen = client(tmp_path), []
    built.on_wait = seen.append
    built._wait_before_retry("m", 0, 2.0, "HTTP 429 rate limited upstream", 2)
    built._wait_before_retry("m", 0, 2.0, "HTTP 503", 2)
    assert [e["why"] for e in seen if e] == ["429-backoff", "retry-backoff"] and seen[-1] is None


def test_the_job_writes_its_waiting_state_and_a_heartbeat_never_erases_it(tmp_path, profile):
    profile("production")                                             # the r3 helper's job carries the production budget
    from test_r3_keep_green import production, wp_h_root
    from test_p1b_production_run import FakeClient
    root = wp_h_root(tmp_path)
    fake = FakeClient([])
    models, _ = production(root, fake)
    status = root / "_loop" / "calc" / "status.json"
    models._set_waiting({"why": "model-slot", "since": "t", "model": "m"})
    assert json.loads(status.read_text(encoding="utf-8"))["waiting"]["why"] == "model-slot"
    models._step("sandbox", "tests", "green")                         # another heartbeat while still waiting
    assert json.loads(status.read_text(encoding="utf-8"))["waiting"]["why"] == "model-slot"
    models._set_waiting(None)
    assert json.loads(status.read_text(encoding="utf-8"))["waiting"] is None


# ---------------------------------------------------------------- the job's own lease is released

def test_a_client_releases_the_lease_it_took_and_never_one_it_did_not(tmp_path, monkeypatch):
    monkeypatch.setenv("SATVIK_HOME", str(tmp_path / "home"))
    built = client(tmp_path)
    built.own_job("calc", tmp_path / "fallback")
    job_id, gen = built.shared
    assert loop_home.Lease(tmp_path / "home").held(job_id)
    assert built.release_job() is True and not loop_home.Lease(tmp_path / "home").held(job_id)
    assert built.release_job() is False                               # safe to call twice
    other = client(tmp_path)
    other.set_job(job_id, gen)                                        # a launcher-owned job: not this client's lease
    assert other.release_job() is False
