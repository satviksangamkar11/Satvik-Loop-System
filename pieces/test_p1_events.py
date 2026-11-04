"""P1 events (2026-11-03): the job's events.jsonl (event_log), history.db as a rebuildable index of the files (job_history), and the reconciled
job view. Fakes only: no network, no model call, no Docker."""
import json
import os
import sqlite3
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
import job_history as jh  # noqa: E402
import loop_home  # noqa: E402
import merge_queue as mq  # noqa: E402
import plan_job  # noqa: E402
import openrouter_call as oc  # noqa: E402
import p1b_production as pm  # noqa: E402
import p1b_routing as p1b  # noqa: E402
import prod_harness as ph  # noqa: E402
from test_fix_loop_wiring import JOB, FakeMerge, env_for  # noqa: E402
from test_p1b_production_run import ACC_ID, ORIG_TEST, SUM_CODE, FakeClient, FakeSandbox, wp_h_root, wp_h_run  # noqa: E402
from test_r3_keep_green import FINDING, GREEN_CODE, ContentSandbox, production, red_repair_run  # noqa: E402

NL = "\n"
GREEN_TWO = "def calculate_total(prices):" + NL + "    return sum(prices)  # rounded" + NL


@pytest.fixture(autouse=True)
def _production_roster(profile):
    profile("production")


def role(name):
    return oc.roster_state()["ROLES"][name]


def job_events(root):
    return event_log.read_events(root / "_loop" / "calc" / "events.jsonl")


def stages(events):
    return [e["stage"] for e in events if e["type"] == "attempt-start"]


# ---------------------------------------------------------------- 1. the events of a scripted ladder run

def test_a_red_fix_first_repair_run_writes_its_events_in_order(tmp_path):
    root, _client, result = red_repair_run(tmp_path)
    assert result["state"] == "review-open"
    events = job_events(root)
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))       # monotonic, from 1
    assert [e["type"] for e in events] == ["attempt-start", "attempt-end", "tests", "attempt-start", "attempt-end", "review",
                                           "attempt-start", "attempt-end", "tests", "restore-green", "finish"]
    assert stages(events) == ["writer_a_write", "reviewer_b", "review_fix_repair"]
    assert all(e["job_id"] == "calc" and e["ts"].endswith("Z") for e in events)  # a solo run: the job folder's name
    start, end = events[0], events[1]
    assert start["role"] == "writer_a" and start["mode"] == "write" and start["model"] == role("writer_a") and start["via"] == "openrouter"
    assert end["attempt_id"] == start["attempt_id"] and end["failed"] is False and end["status"] == 200
    assert end["prompt_tokens"] == 100 and end["completion_tokens"] == 40 and end["reasoning_tokens"] == 8 and end["finish_reason"] == "stop"
    assert events[2]["green"] is True and events[8]["green"] is False and events[8]["failing"] == [ACC_ID]
    assert events[5]["verdict"] == "FIX FIRST" and events[5]["evidence_ok"] is True and events[5]["reviewer"] == role("reviewer_b")
    assert events[-1]["state"] == "review-open" and "left the tests red" in events[-1]["reason"]


def test_a_stage_that_runs_twice_has_two_attempts_and_a_stage_never_run_has_none(tmp_path):
    root = wp_h_root(tmp_path)
    client = FakeClient([ph.file_reply(("pieces/calc.py", GREEN_CODE), ("pieces/test_calc.py", ORIG_TEST)),   # Dots write: green
                         FINDING,                                                                            # North: FIX FIRST
                         ph.file_reply(("pieces/calc.py", GREEN_TWO)),                                       # Dots FIX FIRST repair: green
                         ph.READY, ph.READY])                                                                # North, then GLM
    result = wp_h_run(root, client, ContentSandbox({}))
    assert result["state"] == "passed", result
    seen = stages(job_events(root))
    assert seen == ["writer_a_write", "reviewer_b", "review_fix_repair", "reviewer_b", "reviewer_a"]
    for never in ("planner", "writer_a_repair", "writer_b_write", "writer_b_repair", "fallback_write", "specialist_repair", "reviewer_reask"):
        assert never not in seen


def test_the_full_red_ladder_names_each_writer_stage_and_a_failed_specialist_attempt(tmp_path):
    root = wp_h_root(tmp_path)
    reply = ph.file_reply(("pieces/calc.py", SUM_CODE), ("pieces/test_calc.py", ORIG_TEST))
    result = wp_h_run(root, FakeClient([reply] * 4), FakeSandbox({ACC_ID: "assert 0 == 50"}))
    assert result["state"] == "escalate"
    events = job_events(root)
    assert stages(events) == ["writer_a_write", "writer_a_repair", "writer_b_write", "writer_b_repair", "specialist_repair"]
    last = [e for e in events if e["type"] == "attempt-end"][-1]
    assert last["failed"] is True and "RepairFailed" not in last.get("error", "") and last["error"]    # the call's own failure message
    assert [e["type"] for e in events if e["type"] in ("attempt-start", "attempt-end")] == ["attempt-start", "attempt-end"] * 5


# ---------------------------------------------------------------- 2. attempts and waits pair up, on exceptions too

def test_a_failed_call_ends_its_attempt_with_the_redacted_error(tmp_path):
    root = wp_h_root(tmp_path)
    secret = "sk-or-v1-SECRETKEYABC123"
    attempt = {"model": role("writer_a"), "status": 503, "message": f"upstream down {secret}"}
    models, _ = production(root, FakeClient([([attempt], oc.ProviderUnavailable("HTTP 503")), RuntimeError("boom")]))
    with pytest.raises(p1b.ModelFailed):
        models.call(role("writer_a"), "write", ["pieces/calc.py"])
    with pytest.raises(RuntimeError):
        models.call(role("writer_a"), "write", ["pieces/calc.py"])
    events = job_events(root)
    assert [e["type"] for e in events] == ["attempt-start", "attempt-end"] * 2
    first, second = events[1], events[3]
    assert first["failed"] is True and first["status"] == 503 and secret not in first["error"] and "upstream down" in first["error"]
    assert second["failed"] is True and "boom" in second["error"] and second["requests"] == 0


def test_wait_events_pair_up_even_when_the_wait_raises(tmp_path):
    root = wp_h_root(tmp_path)
    client = ph.client(tmp_path, ph.Provider({}))
    models, _ = production(root, client)
    with pytest.raises(RuntimeError):
        with client.waiting("model-slot", "m1"):
            raise RuntimeError("the slot wait was cut short")
    models._set_waiting({"why": "account-pace", "model": "m2"})
    models._set_waiting({"why": "429-backoff", "model": "m2"})      # a new wait replaces the open one: the open one ends first
    models._set_waiting(None)
    models._set_waiting(None)                                       # an end with no wait open writes nothing
    waits = [(e["type"], e["why"], e["model"]) for e in job_events(root)]
    assert waits == [("wait-start", "model-slot", "m1"), ("wait-end", "model-slot", "m1"),
                     ("wait-start", "account-pace", "m2"), ("wait-end", "account-pace", "m2"),
                     ("wait-start", "429-backoff", "m2"), ("wait-end", "429-backoff", "m2")]


def test_a_wait_left_open_at_the_end_of_the_run_gets_its_wait_end_before_the_finish(tmp_path):
    root = wp_h_root(tmp_path)
    models, _ = production(root, FakeClient([]))
    models._set_waiting({"why": "retry-backoff", "model": "m"})
    job = fl.load_job(root / "_loop" / "jobs" / "calc.json")
    pm._finish(job, root, root / "_loop" / "calc", {}, [], models, p1b.Outcome("exception", "x"), "", {})
    assert [e["type"] for e in job_events(root)] == ["wait-start", "wait-end", "finish"]


def test_a_torn_last_line_is_skipped_and_never_joins_the_next_event(tmp_path):
    folder = tmp_path / "_loop" / "job"
    event_log.append_event(folder, {"type": "job-start", "job_id": "job-1"})
    with open(folder / "events.jsonl", "ab") as fh:
        fh.write(b'{"seq": 2, "ts": "x", "ty')                     # a crash mid-write
    event_log.append_event(folder, {"type": "finish", "state": "error"})
    events = event_log.read_events(folder / "events.jsonl")
    assert [(e["seq"], e["type"], e["job_id"]) for e in events] == [(1, "job-start", "job-1"), (2, "finish", "job-1")]
    assert (tmp_path / "_loop" / ".gitignore").read_text(encoding="utf-8") == "*\n"
    with pytest.raises(ValueError):
        event_log.append_event(folder, {"type": "made-up"})


# ---------------------------------------------------------------- 3. history.db: idempotent, from the archive, rebuildable

def writes_events(state):
    """A fake run that leaves a status.json and the events a run writes, in the job's _loop folder."""
    def run(job, wt, **kwargs):
        folder = Path(wt) / "_loop" / job["name"]
        event_log.emit(folder, "attempt-start", attempt_id="a-01", stage="writer_a_write", role="writer_a", mode="write", model="m",
                       via="openrouter", effort=None)
        event_log.emit(folder, "attempt-end", attempt_id="a-01", status=200, failed=False, prompt_tokens=5, completion_tokens=7,
                       reasoning_tokens=None, finish_reason="stop", seconds=0.1)
        event_log.emit(folder, "tests", green=state == "passed", failing=[] if state == "passed" else ["t::x"])
        (folder / "status.json").write_text(json.dumps({"name": job["name"], "state": state, "reason": "why not"}), encoding="utf-8")
        event_log.emit(folder, "finish", state=state, reason="why not")
        return {"name": job["name"], "state": state, "round": 1}
    return run


def rows(home):
    conn = sqlite3.connect(str(Path(home) / jh.DB_NAME))
    try:
        return {t: sorted(conn.execute(f"SELECT * FROM {t}").fetchall()) for t in ("events", "attempts", "jobs", "results")}
    finally:
        conn.close()


def test_index_job_is_idempotent_and_reads_the_archive_after_the_worktree_is_gone(tmp_path):
    env = env_for(tmp_path)
    res = fl.run_launched(JOB, env.repo, env.api, env.launcher, merge_queue=FakeMerge(), run=writes_events("escalate"))
    job_id = res["job_id"]
    assert not (env.home / "wt" / job_id).exists()                         # removed after the archive
    events = event_log.read_events(env.home / "results" / job_id / JOB["name"] / "events.jsonl")
    assert [e["type"] for e in events] == ["job-start", "attempt-start", "attempt-end", "tests", "finish"]   # one finish: the run's
    assert all(e["job_id"] == job_id for e in events)
    before = rows(env.home)                                                # Launcher.finish indexed it already
    assert len(before["events"]) == 5 and len(before["attempts"]) == 1
    assert jh.index_job(env.home, job_id) == 0 and jh.index_job(env.home, job_id) == 0
    assert rows(env.home) == before
    assert before["jobs"][0][-1] == "archive"
    view = jh.job_view(env.home, job_id)
    assert view["derived_state"] == "finished:escalate" and view["source"] == "archive" and view["index_ok"] is True
    assert view["attempts"][0]["prompt_tokens"] == 5 and view["finish"]["reason"] == "why not"


def test_a_stopped_launch_adds_its_own_finish_and_a_merge_lands_in_the_archive(tmp_path):
    env = env_for(tmp_path)
    res = fl.run_launched(JOB, env.repo, env.api, env.launcher, merge_queue=FakeMerge(), run=writes_events("passed"))
    job_id = res["job_id"]
    queue = mq.MergeQueue(env.repo, env.api, env.launcher, run_suite=lambda wt: (True, "suite ok"))
    gen = json.loads((env.home / "leases" / f"{job_id}.json").read_text(encoding="utf-8"))["generation"]
    queue._finish(job_id, gen, "merged", "base123", {"head": "abc", "attempts": 1})
    events = event_log.read_job_events(env.home, job_id)
    assert [e["type"] for e in events][-2:] == ["finish", "merge"]
    assert events[-1]["state"] == "merged" and events[-1]["head"] == "abc" and events[-1]["attempts"] == 1
    assert [e["type"] for e in events].count("finish") == 1                # the launcher saw the run's own passed finish
    assert jh.job_view(env.home, job_id)["derived_state"] == "merged:merged"
    assert rows(env.home)["results"][0][7] == "merged"


def test_a_planned_jobs_view_shows_its_planner_attempt_from_the_plan_folder(tmp_path):
    """I21: plan_job writes the planner's events to <repo>/_loop/<name>/events.jsonl (beside the plan), not to the job's folders. The job view
    reads them through the plan record (api/plans), first in its attempts; the job's own events and finish are unchanged."""
    env = env_for(tmp_path)
    job_id = fl.run_launched(JOB, env.repo, env.api, env.launcher, merge_queue=FakeMerge(), run=writes_events("passed"))["job_id"]
    own_count = len(event_log.read_job_events(env.home, job_id))
    before = jh.job_view(env.home, job_id)
    assert [a["stage"] for a in before["attempts"]] == ["writer_a_write"]          # no plan record yet: no planner attempt
    plan_dir = env.repo / "_loop" / JOB["name"] / "plan"
    with plan_job._planner_attempt(plan_dir, "haiku", "claude_code", "high") as attempt:
        attempt["raw"] = json.dumps({"result": "{}", "is_error": False})
    (env.home / "api" / "plans").mkdir(parents=True)
    (env.home / "api" / "plans" / "p1.json").write_text(json.dumps({"plan_id": "p1", "name": JOB["name"], "state": "planned",
                                                                    "plan_dir": str(plan_dir)}), encoding="utf-8")
    view = jh.job_view(env.home, job_id)
    assert [a["stage"] for a in view["attempts"]] == ["planner", "writer_a_write"]
    planner = view["attempts"][0]
    assert (planner["model"], planner["via"], planner["effort"], planner["status"], planner["failed"]) == ("haiku", "claude_code", "high", "ok", False)
    assert view["stages"] == ["planner", "writer_a_write"]
    assert view["events"]["count"] == own_count                                    # the count is the job's own events
    assert view["finish"] == before["finish"] and view["derived_state"] == before["derived_state"]
    assert jh.job_view(env.home, job_id)["source"] == "archive"
    assert jh.planner_events(env.home, "some-other-job") == []


def test_rebuild_index_gives_the_same_rows_and_a_corrupt_index_changes_no_answer(tmp_path):
    env = env_for(tmp_path)
    ids = [fl.run_launched(JOB, env.repo, env.api, env.launcher, merge_queue=FakeMerge(), run=writes_events(state))["job_id"]
           for state in ("escalate", "review-open")]
    before = rows(env.home)
    views = jh.list_jobs(env.home)
    assert views["index_ok"] is True and {v["job_id"] for v in views["jobs"]} == set(ids)
    for suffix in ("", "-wal", "-shm"):
        try:
            (env.home / (jh.DB_NAME + suffix)).unlink()
        except FileNotFoundError:
            pass
    assert jh.list_jobs(env.home)["index_ok"] is False                    # missing: still answered from the files
    assert sorted(jh.rebuild_index(env.home)) == sorted(ids)
    assert rows(env.home) == before
    assert jh.rebuild_index(env.home) and rows(env.home) == before         # repeat-safe

    for suffix in ("-wal", "-shm"):
        try:
            (env.home / (jh.DB_NAME + suffix)).unlink()
        except FileNotFoundError:
            pass
    (env.home / jh.DB_NAME).write_bytes(b"this is not a database" * 100)
    broken = jh.list_jobs(env.home)
    assert broken["index_ok"] is False
    strip = lambda listing: [{k: v for k, v in view.items() if k not in ("index_ok", "lease")} for view in listing["jobs"]]   # noqa: E731
    assert strip(broken) == strip(views)
    assert jh.job_view(env.home, ids[0])["derived_state"] == "finished:escalate"
    assert jh.safe_index(env.home, ids[0]) is None                         # the hook never raises on a broken index
    jh.rebuild_index(env.home)
    assert rows(env.home) == before and jh.list_jobs(env.home)["index_ok"] is True


WORKER = """
import sys
sys.path.insert(0, sys.argv[1])
import job_history
home, ids = sys.argv[2], sys.argv[3].split(",")
for _ in range(15):
    for job_id in ids:
        job_history.index_job(home, job_id)
"""


def test_two_processes_index_the_same_home_at_once_without_a_lost_or_doubled_row(tmp_path):
    home = tmp_path / "home"
    ids = [f"job-{n}" for n in range(6)]
    for n, job_id in enumerate(ids):
        folder = home / "results" / job_id / "calc"
        event_log.append_event(folder, {"type": "job-start", "job_id": job_id, "name": "calc"})
        for k in range(n + 3):
            event_log.append_event(folder, {"type": "tests", "green": k % 2 == 0, "failing": []})
    script = tmp_path / "worker.py"
    script.write_text(WORKER, encoding="utf-8")
    procs = [subprocess.Popen([sys.executable, str(script), str(REPO / "scripts" / "source"), str(home), ",".join(order)],
                              stderr=subprocess.PIPE, text=True) for order in (ids, list(reversed(ids)))]
    for proc in procs:
        _, err = proc.communicate(timeout=120)
        assert proc.returncode == 0, err
    counts = dict(sqlite3.connect(str(home / jh.DB_NAME)).execute("SELECT job_id, count(*) FROM events GROUP BY job_id").fetchall())
    assert counts == {job_id: n + 4 for n, job_id in enumerate(ids)}
    mode = sqlite3.connect(str(home / jh.DB_NAME)).execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal"


# ---------------------------------------------------------------- 4. every derived_state of the job view

def dead_pid():
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def running_job(home, job_id="calc-1", status=None, events=(), pid=None, heartbeat_age=0.0):
    """A job the registry says is running: its lease (this process unless `pid`), status.json and events in its worktree."""
    gen = loop_home.Lease(home).acquire(job_id)
    path = home / "leases" / f"{job_id}.json"
    lease = json.loads(path.read_text(encoding="utf-8"))
    lease["heartbeat"] = time.time() - heartbeat_age
    if pid is not None:
        lease["pid"] = pid
    path.write_text(json.dumps(lease), encoding="utf-8")
    loop_home.Registry(home).record(job_id, job_id, gen, "running", "base", None)
    folder = home / "wt" / job_id / "_loop" / "calc"
    event_log.append_event(folder, {"type": "job-start", "job_id": job_id, "name": "calc"})
    for event in events:
        event_log.append_event(folder, event)
    (folder / "status.json").write_text(json.dumps(dict({"name": "calc", "state": "running"}, **(status or {}))), encoding="utf-8")
    return gen


def state(home, job_id="calc-1"):
    view = jh.job_view(home, job_id)
    return view["derived_state"], view["why"]


def test_running_and_waiting_states(tmp_path):
    home = tmp_path / "home"
    running_job(home, "a-1")
    running_job(home, "b-1", status={"waiting": {"why": "model-slot", "since": "t", "model": "m"}})
    running_job(home, "c-1", status={"waiting": {"since": "t", "model": "m"}})                       # waits, no reason given
    running_job(home, "d-1", events=[{"type": "wait-start", "why": None, "model": "m"}])             # an open wait with no reason
    running_job(home, "e-1", events=[{"type": "wait-start", "why": "429-backoff", "model": "m"}, {"type": "wait-end", "why": "429-backoff"}])
    assert state(home, "a-1") == ("running", None)
    assert state(home, "b-1") == ("waiting", "model-slot")
    assert state(home, "c-1") == ("waiting-unknown", None)
    assert state(home, "d-1") == ("waiting-unknown", None)
    assert state(home, "e-1") == ("running", None)                                                    # that wait ended


def test_orphan_needs_a_stale_heartbeat_and_a_dead_pid_and_anything_less_is_a_stale_warning(tmp_path):
    home = tmp_path / "home"
    old = loop_home.STALE_S + 60
    running_job(home, "alive-old", heartbeat_age=old)                     # stale heartbeat, pid alive: never an orphan
    running_job(home, "alive-late", heartbeat_age=jh.STALE_WARN_S + 10)   # late beyond the normal cadence
    running_job(home, "dead-fresh", pid=dead_pid())                       # dead pid, fresh heartbeat: not the orphan rule
    running_job(home, "dead-old", pid=dead_pid(), heartbeat_age=old)      # both: the orphan rule
    assert state(home, "alive-old") == ("stale-warning", None)
    assert state(home, "alive-late") == ("stale-warning", None)
    assert state(home, "dead-fresh") == ("stale-warning", None)
    assert state(home, "dead-old") == ("orphan", None)
    view = jh.job_view(home, "dead-old")
    assert view["lease"]["stale"] is True and view["lease"]["pid_alive"] is False
    assert loop_home.Lease(home).stale("dead-old") and not loop_home.Lease(home).stale("alive-old")   # the same rule as the lease


def test_queued_finished_and_merged_states_and_the_list_filters(tmp_path):
    home = tmp_path / "home"
    (home / "queue").mkdir(parents=True)
    (home / "queue" / "0001-1-calc.json").write_text(json.dumps({"name": "calc"}), encoding="utf-8")
    running_job(home, "run-1")
    gen = running_job(home, "esc-1")
    loop_home.Registry(home).record("esc-1", "esc-1", gen, "escalate", "base", None)
    gen = running_job(home, "pass-1")
    loop_home.Registry(home).record("pass-1", "pass-1", gen, "passed", "base", None)
    gen = running_job(home, "merge-1")
    loop_home.Registry(home).record("merge-1", "merge-1", gen, "merged", "base", None)
    gen = running_job(home, "conf-1")
    (home / "results" / "conf-1").mkdir(parents=True)
    (home / "results" / "conf-1" / "merge.json").write_text(json.dumps({"job_id": "conf-1", "state": "merge-conflict"}), encoding="utf-8")
    assert state(home, "0001-1-calc") == ("queued", None)
    assert state(home, "esc-1") == ("finished:escalate", None)
    assert state(home, "pass-1") == ("finished:passed", None)
    assert state(home, "merge-1") == ("merged:merged", None)
    assert state(home, "conf-1") == ("merged:merge-conflict", None)

    def ids(filter):
        return sorted(v["job_id"] or v["queue_id"] for v in jh.list_jobs(home, filter)["jobs"])
    assert ids("running") == ["run-1"]
    assert ids("queued") == ["0001-1-calc"]
    assert ids("finished") == ["conf-1", "esc-1", "merge-1", "pass-1"]
    assert ids("failed") == ["conf-1", "esc-1"]
    assert len(ids(None)) == 6
    assert jh.sync_running(home) == {"run-1": 1, "conf-1": 1}             # registry rows still running: their events are indexed
    with pytest.raises(ValueError):
        jh.list_jobs(home, "everything")
