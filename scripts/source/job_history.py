"""Job history and the reconciled job view (P1 extension, 2026-11-03), for a UI backend to read. Stdlib only.

The files are the source of truth: `_loop/<job>/events.jsonl`, status.json and history/ while a job runs, and SATVIK_HOME/results/<job_id>/
(events.jsonl, status.json, merge.json) once it is archived. history.db is only an index of them: every row is derived from those files,
rebuild_index(home) deletes it and builds it again, and the readers (job_view, list_jobs) answer from the files, so a missing, locked or
corrupt index changes no answer; they say index_ok false then.

History: SATVIK_HOME/history.db (SQLite, WAL), with four tables:
  events    one row per event line, keyed by (job_id, seq), so ingesting the same line twice keeps one row
  attempts  one row per model call (attempt-start joined with its attempt-end; an attempt still in flight has no end yet)
  jobs      one row per job: name, base, branch, start, the last event and where the events were read from (worktree or archive)
  results   one row per job: the run's finish (state, reason), the last tests and review, and the merge outcome
index_job(home, job_id) reads the job's events.jsonl (event_log) from its worktree while it runs and from results/<job_id>/ once archived,
and rebuilds the job's derived rows from every event it holds. Each ingest is one short IMMEDIATE transaction with a busy timeout, so any
number of processes can index at once. launch.Launcher.finish indexes after the archive, merge_queue after record_merge, and
sync_running(home) indexes every running job (for the API to call on a poll).

The job view: job_view(home, job_id) is one dict built from the registry row, the lease (held, stale, pid alive), status.json (state,
waiting) and the events. Its derived_state is one of:
  queued                 a pending file in queue/ (its id is the queue file's name without .json)
  running                the job runs and nothing below applies
  waiting                status.json (or an open wait-start event) says why it waits: `why` is model-slot, account-pace, 429-backoff ...
  waiting-unknown        the job waits but nothing says why (no reason is made up)
  stale-warning          the job should be running, but its lease heartbeat is older than the normal cadence (STALE_WARN_S) while its
                         pid is alive, or its liveness is unknown (no lease, a released lease, or a dead pid with a recent heartbeat)
  orphan                 the existing takeover rule only: the lease is stale (heartbeat older than loop_home.STALE_S) AND its pid is dead
  finished:<state>       the run ended (registry row, else the finish event, else a terminal status.json)
  merged:<state>         the merge ended (merge event, merge.json or the registry row): merged, merge-conflict, tests-failed, ...
list_jobs(home, filter) lists the jobs the home knows of, filtered by running, queued, failed or finished."""
import json
import logging
import sqlite3
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import event_log
import loop_home

log = logging.getLogger("satvik.job_history")
DB_NAME = "history.db"
BUSY_S = 30.0
STALE_WARN_S = 3 * loop_home.HEARTBEAT_S      # three missed heartbeats: the job is late, though not yet an orphan
SCAN = 500                                    # registry rows looked at (launch.RUNNING_SCAN)
RUNNING_STATES = ("running", "waiting", "waiting-unknown", "stale-warning")
SCHEMA = """
CREATE TABLE IF NOT EXISTS events (job_id TEXT NOT NULL, seq INTEGER NOT NULL, ts TEXT, type TEXT, data TEXT NOT NULL,
                                   PRIMARY KEY (job_id, seq));
CREATE TABLE IF NOT EXISTS attempts (job_id TEXT NOT NULL, attempt_id TEXT NOT NULL, stage TEXT, role TEXT, mode TEXT, model TEXT, via TEXT,
                                     effort TEXT, started_ts TEXT, ended_ts TEXT, status INTEGER, failed INTEGER, prompt_tokens INTEGER,
                                     completion_tokens INTEGER, reasoning_tokens INTEGER, finish_reason TEXT, seconds REAL, error TEXT,
                                     PRIMARY KEY (job_id, attempt_id));
CREATE TABLE IF NOT EXISTS jobs (job_id TEXT PRIMARY KEY, name TEXT, base TEXT, branch TEXT, started_ts TEXT, last_seq INTEGER,
                                 last_type TEXT, last_ts TEXT, source TEXT);
CREATE TABLE IF NOT EXISTS results (job_id TEXT PRIMARY KEY, finish_state TEXT, finish_reason TEXT, finish_ts TEXT, tests_green INTEGER,
                                    failing TEXT, review_verdict TEXT, merge_state TEXT, merge_head TEXT, merge_attempts INTEGER, merge_ts TEXT);
CREATE INDEX IF NOT EXISTS attempts_stage ON attempts (stage);
"""


# ---------------------------------------------------------------- the database

def connect(home):
    """A connection to home/history.db in WAL mode, autocommit (each writer opens its own short transaction), busy timeout BUSY_S."""
    path = Path(home) / DB_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=BUSY_S, isolation_level=None)
    try:                                      # a corrupt file raises here: the connection must not stay open (Windows keeps the file locked)
        conn.execute(f"PRAGMA busy_timeout = {int(BUSY_S * 1000)}")
        for _ in range(50):                   # two processes creating the file at once: the WAL switch can lose once, then it is set
            try:
                conn.execute("PRAGMA journal_mode = WAL")
                break
            except sqlite3.OperationalError:
                time.sleep(0.05)
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.executescript(SCHEMA)
    except BaseException:
        conn.close()
        raise
    return conn


@contextmanager
def _transaction(home):
    conn = connect(home)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
    finally:
        conn.close()


def _check(job_id):
    if not isinstance(job_id, str) or not loop_home.JOB_ID_RE.match(job_id):
        raise ValueError(f"bad job id {job_id!r}")


def _read_json(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):             # missing, or caught mid-write: read again on the next poll
        return None
    return data if isinstance(data, dict) else None


def _merge_json(home, job_id):
    return _read_json(Path(home) / "results" / job_id / "merge.json")


def index_job(home, job_id):
    """Ingests the job's events (worktree and archive) and rebuilds its attempts, jobs and results rows. Returns the number of new event
    rows: 0 when every line was there already."""
    _check(job_id)
    home = Path(home)
    events = event_log.read_job_events(home, job_id)
    merge = _merge_json(home, job_id)
    if not events and merge is None:
        return 0
    archived = any((home / "results" / job_id).glob("*/" + event_log.FILE))
    with _transaction(home) as conn:
        new = 0
        for event in events:
            cur = conn.execute("INSERT OR IGNORE INTO events (job_id, seq, ts, type, data) VALUES (?, ?, ?, ?, ?)",
                               (job_id, event["seq"], event.get("ts"), event.get("type"), json.dumps(event)))
            new += cur.rowcount
        held = [json.loads(data) for (data,) in conn.execute("SELECT data FROM events WHERE job_id = ? ORDER BY seq", (job_id,))]
        _store(conn, job_id, summarize(held), merge, "archive" if archived else "worktree")
    return new


def safe_index(home, job_id):
    """index_job for the engine's hooks (finish, merge): never raises, so the history never blocks a finish. Returns the count or None."""
    try:
        return index_job(home, job_id)
    except Exception:                         # sqlite, disk, a bad id: the events file still holds everything
        return None


def _store(conn, job_id, s, merge, source):
    for a in s["attempts"]:
        conn.execute("INSERT OR REPLACE INTO attempts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (job_id, a["attempt_id"], a.get("stage"), a.get("role"), a.get("mode"), a.get("model"), a.get("via"), a.get("effort"),
                      a.get("started_ts"), a.get("ended_ts"), a.get("status"), None if a.get("failed") is None else int(a["failed"]),
                      a.get("prompt_tokens"), a.get("completion_tokens"), a.get("reasoning_tokens"), a.get("finish_reason"),
                      a.get("seconds"), a.get("error")))
    start = s["start"] or {}
    conn.execute("INSERT OR REPLACE INTO jobs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                 (job_id, start.get("name"), start.get("base"), start.get("branch"), start.get("ts"), s["last_seq"], s["last_type"],
                  s["last_ts"], source))
    finish, tests, review = s["finish"] or {}, s["tests"] or {}, s["review"] or {}
    merged = s["merge"] or merge or {}
    conn.execute("INSERT OR REPLACE INTO results VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                 (job_id, finish.get("state"), finish.get("reason"), finish.get("ts"),
                  None if tests.get("green") is None else int(tests["green"]), json.dumps(tests.get("failing")) if tests else None,
                  review.get("verdict"), merged.get("state"), merged.get("head"), merged.get("attempts"), merged.get("ts")))


def sync_running(home):
    """Indexes the new events of every job whose registry row says running. Returns {job_id: new event rows}."""
    out = {}
    for row in _registry_rows(home):
        if row["state"] == "running" and row.get("job"):
            try:
                out[row["job"]] = index_job(home, row["job"])
            except (ValueError, sqlite3.Error):       # a bad id, or an index to rebuild (rebuild_index): the files still answer
                out[row["job"]] = None
    return out


def file_job_ids(home):
    """Every job id the files know of: the archive (results/) and the worktrees (wt/)."""
    home = Path(home)
    ids = set()
    for folder in (home / "results", home / "wt"):
        if folder.is_dir():
            ids |= {p.name for p in folder.iterdir() if p.is_dir() and not p.name.startswith(".")}
    return {i for i in ids if loop_home.JOB_ID_RE.match(i)}


def rebuild_index(home):
    """Deletes history.db (with its -wal and -shm files) and ingests every job of results/ and wt/ again. The files are the truth, so the
    rebuilt rows are the rows an incremental index holds. Repeat-safe. Returns {job_id: event rows ingested}."""
    home = Path(home)
    for suffix in ("", "-wal", "-shm"):
        try:
            (home / (DB_NAME + suffix)).unlink()
        except FileNotFoundError:
            pass
    return {job_id: index_job(home, job_id) for job_id in sorted(file_job_ids(home))}


def index_state(home):
    """'ok' when history.db exists and answers a read (read-only; nothing is created), 'missing' when there is no file, 'locked' when
    another process holds it busy (it may be healthy: never delete it), 'corrupt' when it is there but does not read as this index."""
    path = Path(home) / DB_NAME
    if not path.is_file():
        return "missing"
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=1.0)
        try:
            conn.execute("SELECT count(*) FROM jobs").fetchone()
            conn.execute("SELECT count(*) FROM events").fetchone()
        finally:
            conn.close()
    except sqlite3.OperationalError as exc:
        return "locked" if "locked" in str(exc).lower() or "busy" in str(exc).lower() else "corrupt"
    except sqlite3.Error:
        return "corrupt"
    return "ok"


def index_ok(home):
    """True when history.db exists and answers a read (read-only; nothing is created). A missing, locked or corrupt index is False."""
    return index_state(home) == "ok"


def ensure_index(home):
    """The API's startup check: when history.db is missing or corrupt, rebuild it from the files (rebuild_index) and create its schema, so
    an empty home gets a usable index too. A healthy index is never touched, and a locked one is left alone (another process may be using
    it). Never raises: a failure is logged and the answer stays as it was. Returns index_state after the check."""
    try:
        state = index_state(home)
        if state in ("ok", "locked"):
            return state
        rebuild_index(home)
        connect(home).close()                     # the schema, also when no job has rows to index
        return index_state(home)
    except Exception:                             # the index never blocks the API: the job views answer from the files
        log.exception("history index check failed for %s; the API continues with index_ok false", home)
        try:
            return index_state(home)
        except Exception:
            return "corrupt"


def attempts(home, job_id=None):
    """The attempts rows (all jobs, or one), oldest first, as dicts."""
    conn = connect(home)
    try:
        conn.row_factory = sqlite3.Row
        sql = "SELECT * FROM attempts" + (" WHERE job_id = ?" if job_id else "") + " ORDER BY job_id, started_ts, attempt_id"
        return [dict(r) for r in conn.execute(sql, (job_id,) if job_id else ())]
    finally:
        conn.close()


# ---------------------------------------------------------------- what the events say

def summarize(events):
    """The facts of a job's events, in seq order: its start, every attempt (start joined with end), the wait still open, the last tests,
    review, finish and merge, and the last event."""
    s = {"start": None, "attempts": [], "open_wait": None, "tests": None, "review": None, "finish": None, "merge": None,
         "last_seq": None, "last_type": None, "last_ts": None, "stages": []}
    by_id = {}
    for e in events:
        kind = e.get("type")
        s["last_seq"], s["last_type"], s["last_ts"] = e.get("seq"), kind, e.get("ts")
        if kind == "job-start":
            s["start"] = e
        elif kind == "attempt-start":
            row = {k: e.get(k) for k in ("attempt_id", "stage", "role", "mode", "model", "via", "effort")}
            row.update(started_ts=e.get("ts"), ended_ts=None, status=None, failed=None, prompt_tokens=None, completion_tokens=None,
                       reasoning_tokens=None, finish_reason=None, seconds=None, error=None)
            by_id[e.get("attempt_id")] = row
            s["attempts"].append(row)
            s["stages"].append(e.get("stage"))
        elif kind == "attempt-end" and e.get("attempt_id") in by_id:
            row = by_id[e["attempt_id"]]
            row.update({k: e.get(k) for k in ("status", "failed", "prompt_tokens", "completion_tokens", "reasoning_tokens", "finish_reason",
                                              "seconds", "error")}, ended_ts=e.get("ts"))
        elif kind == "wait-start":
            s["open_wait"] = {"why": e.get("why"), "model": e.get("model"), "since": e.get("ts")}
        elif kind == "wait-end":
            s["open_wait"] = None
        elif kind in ("tests", "review", "finish", "merge"):
            s[kind] = e
    return s


# ---------------------------------------------------------------- the reconciled view

def _registry_rows(home):
    try:
        return loop_home.Registry(Path(home)).recent(SCAN)
    except loop_home.LoopHomeError:
        return []


def _registry_row(home, job_id, rows):
    """The newest registry row that names this job id, whole ({job, gen, state, base, result, key, ...}), or None. A request key run again
    names the newer job, so an older job may have no row any more."""
    for row in rows:
        if row.get("job") == job_id:
            full = loop_home.Registry(Path(home)).lookup(row["key"])
            if full and full.get("job") == job_id:
                return dict(full, key=row["key"])
    return None


def _lease(home, job_id, now):
    """{exists, released, pid, pid_alive, heartbeat_age_s, held, stale} from home/leases/<job_id>.json (read only)."""
    lease = _read_json(Path(home) / "leases" / f"{job_id}.json")
    if lease is None:
        return {"exists": False, "released": None, "pid": None, "pid_alive": None, "heartbeat_age_s": None, "held": False, "stale": False}
    alive = loop_home.pid_alive(lease.get("pid", 0))
    released = bool(lease.get("released"))
    age = round(now - float(lease.get("heartbeat", 0.0) or 0.0), 3)
    return {"exists": True, "released": released, "pid": lease.get("pid"), "pid_alive": alive, "heartbeat_age_s": age,
            "held": not released and alive, "stale": not released and age > loop_home.STALE_S and not alive}


def _status(home, job_id):
    """The job's status.json (worktree first, it is the live one; then the archive), or None."""
    for base in reversed(event_log.job_folders(home, job_id)):
        if base.is_dir():
            for path in sorted(base.glob("*/status.json")):
                data = _read_json(path)
                if data is not None:
                    return data
    return None


def _derive(reg, lease, status, s, merge, queued):
    """(derived_state, why). The order: a merge outcome, a finish, then the liveness of a job that should be running."""
    merge_state = (s["merge"] or {}).get("state") or (merge or {}).get("state")
    if not merge_state and reg and reg.get("state") in event_log.MERGE_STATES:
        merge_state = reg["state"]
    if merge_state:
        return f"merged:{merge_state}", None
    if reg and reg.get("state") and reg["state"] != "running":
        return f"finished:{reg['state']}", None
    if s["finish"]:
        return f"finished:{s['finish'].get('state')}", None
    status_state = (status or {}).get("state")
    started = bool(reg) or bool(s["start"]) or (lease["exists"] and not lease["released"]) or status_state == "running"
    if not started:
        if status_state:
            return f"finished:{status_state}", None
        return ("queued", None) if queued else ("unknown", None)
    if lease["stale"]:                                           # the one orphan rule (loop_home): stale heartbeat AND dead pid
        return "orphan", None
    if not lease["exists"] or lease["released"] or not lease["pid_alive"] or lease["heartbeat_age_s"] > STALE_WARN_S:
        return "stale-warning", None
    waiting = (status or {}).get("waiting")
    if waiting or s["open_wait"]:
        why = (waiting or {}).get("why") if isinstance(waiting, dict) else None
        why = why or (s["open_wait"] or {}).get("why")
        return ("waiting", why) if why else ("waiting-unknown", None)
    return "running", None


def planner_events(home, name):
    """The planner's attempt events (stage planner) of the planned job `name`. plan_job writes them to <repo>/_loop/<name>/events.jsonl, the
    folder beside the plan (plan_dir is <repo>/_loop/<name>/plan), not to the job's own folders: the launched job's id is only given when it
    starts, after its plan was made, so the view reads the plan folder the plan record names (api/plans/<plan_id>.json, name and plan_dir).
    [] when no plan has that name. Only the view reads these: history.db indexes the job's own events alone, so their seq numbers never mix."""
    if not isinstance(name, str) or not name:
        return []
    out, seen = [], set()
    for path in sorted((Path(home) / "api" / "plans").glob("*.json")):
        rec = _read_json(path) or {}
        plan_dir = rec.get("plan_dir")
        if rec.get("name") != name or not isinstance(plan_dir, str) or not plan_dir:
            continue
        events_path = Path(plan_dir).parent / event_log.FILE
        if events_path in seen:
            continue
        seen.add(events_path)
        out += [e for e in event_log.read_events(events_path)
                if e.get("type") in ("attempt-start", "attempt-end") and e.get("stage") == "planner"]
    return out


def job_view(home, job_id, now=None, _rows=None, _index_ok=None):
    """One reconciled dict for a job id (or a pending queue file's id): derived_state and why, and the facts it was derived from. It reads
    the files only (never history.db), so its answer does not depend on the index; index_ok says whether the index is usable."""
    ok = index_ok(home) if _index_ok is None else _index_ok
    home = Path(home)
    if not isinstance(job_id, str) or not job_id or "/" in job_id or "\\" in job_id or job_id.startswith("."):
        raise ValueError(f"bad job id {job_id!r}")
    now = time.time() if now is None else now
    queue_file = home / "queue" / f"{job_id}.json"
    if queue_file.is_file():
        job = _read_json(queue_file) or {}
        return {"job_id": None, "queue_id": job_id, "name": job.get("name"), "derived_state": "queued", "why": None, "registry": None,
                "lease": None, "status": None, "events": {"count": 0, "last_seq": None, "last_type": None, "last_ts": None},
                "attempts": [], "stages": [], "tests": None, "review": None, "finish": None, "merge": None, "source": "queue",
                "index_ok": ok}
    _check(job_id)
    events = event_log.read_job_events(home, job_id)
    source = "archive" if any((home / "results" / job_id).glob("*/" + event_log.FILE)) else "worktree" if events else None
    status = _status(home, job_id)
    start_event = summarize(events)["start"] or {}
    name = start_event.get("name") or (status or {}).get("name")
    s = summarize(planner_events(home, name) + events)     # the planner's attempts come first: the job's own events stay the last
    rows = _registry_rows(home) if _rows is None else _rows
    reg = _registry_row(home, job_id, rows)
    lease = _lease(home, job_id, now)
    merge = _merge_json(home, job_id)
    derived, why = _derive(reg, lease, status, s, merge, False)
    start = s["start"] or {}
    return {"job_id": job_id, "queue_id": None, "name": start.get("name") or (status or {}).get("name"), "derived_state": derived, "why": why,
            "registry": reg, "lease": lease,
            "status": None if status is None else {k: status.get(k) for k in ("state", "waiting", "reason", "updated")},
            "events": {"count": len(events), "last_seq": s["last_seq"], "last_type": s["last_type"], "last_ts": s["last_ts"]},
            "attempts": s["attempts"], "stages": s["stages"], "tests": _strip(s["tests"]), "review": _strip(s["review"]),
            "finish": _strip(s["finish"]), "merge": _strip(s["merge"]) or merge, "started": start.get("ts"), "source": source,
            "index_ok": ok}


def _strip(event):
    return None if event is None else {k: v for k, v in event.items() if k not in ("job_id", "type")}


def _known_ids(home, rows):
    """The registry's job ids and the files' (archive and worktrees); never the index."""
    ids = {row["job"] for row in rows if row.get("job") and loop_home.JOB_ID_RE.match(row["job"])}
    return ids | file_job_ids(home)


def _failed(derived):
    if derived == "orphan":
        return True
    if derived.startswith("finished:"):
        return derived != "finished:passed"
    if derived.startswith("merged:"):
        return derived != "merged:merged"
    return False


FILTERS = {
    None: lambda d: True,
    "all": lambda d: True,
    "running": lambda d: d in RUNNING_STATES,
    "queued": lambda d: d == "queued",
    "finished": lambda d: d.startswith(("finished:", "merged:")),
    "failed": _failed,
}


def list_jobs(home, filter=None, now=None):
    """{"jobs": [job views], "index_ok": bool}: the jobs the home knows of (registry, worktrees, archive, queue), newest start first, kept by
    `filter`: None or all, running (running, waiting, waiting-unknown, stale-warning), queued, finished (finished:* and merged:*), or failed
    (orphan, and any end other than passed or merged). Read from the files, so the answer is the same with or without the index."""
    if filter not in FILTERS:
        raise ValueError(f"unknown filter {filter!r}: one of {sorted(k for k in FILTERS if k)}")
    home = Path(home)
    rows = _registry_rows(home)
    ok = index_ok(home)
    views = [job_view(home, job_id, now=now, _rows=rows, _index_ok=ok) for job_id in sorted(_known_ids(home, rows))]
    queue = home / "queue"
    if queue.is_dir():
        views += [job_view(home, p.stem, now=now, _index_ok=ok) for p in sorted(queue.glob("*.json"))]
    views = [v for v in views if FILTERS[filter](v["derived_state"])]
    return {"jobs": sorted(views, key=lambda v: (v.get("started") or "", v.get("job_id") or v.get("queue_id") or ""), reverse=True),
            "index_ok": ok}


# ---------------------------------------------------------------- the command line

def main(argv=None):
    """python scripts/source/job_history.py rebuild: deletes history.db and builds it again from the files of SATVIK_HOME, then prints the job
    count. Exit 0 when the index answers a read afterwards, 1 otherwise (SATVIK_HOME unset or refused, or the rebuild failed)."""
    args = sys.argv[1:] if argv is None else list(argv)
    if args != ["rebuild"]:
        print("usage: job_history.py rebuild   (rebuilds history.db under SATVIK_HOME from the job files)", file=sys.stderr)
        return 2
    try:
        home = loop_home.home(loops=1)
    except loop_home.LoopHomeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    try:
        counts = rebuild_index(home)
        connect(home).close()
    except Exception as exc:                  # sqlite, disk: say so, the files still answer
        print(f"error: rebuild failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    ok = index_ok(home)
    print(f"home: {home}")
    print(f"jobs: {len(counts)}")
    print(f"event rows: {sum(n or 0 for n in counts.values())}")
    print(f"index_ok: {str(ok).lower()}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
