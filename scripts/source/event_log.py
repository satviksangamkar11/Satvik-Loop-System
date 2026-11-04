"""Job events (P1 extension, 2026-11-03): one append-only JSON-lines file per job, `_loop/<job>/events.jsonl`, next to its status.json.

Each line is one event: {seq, ts, job_id, type, ...fields}. seq counts up from 1 in the file, ts is UTC, job_id is the launcher's job id (a
later event without one takes the job_id of the line before it; a solo run with no launcher uses the job folder's name). The job process is
the only writer, and a line is never rewritten. The archive (launch.archive_job) copies `_loop`, so the events outlive the worktree.

Types and their fields:
  job-start      {name, base, branch, gen, key}                       launch.Launcher.start
  attempt-start  {attempt_id, stage, role, mode, model, via, effort}  just before a model call (adapter or Claude Code)
  attempt-end    {attempt_id, status, failed, prompt_tokens, completion_tokens, reasoning_tokens, finish_reason, seconds, error?}
  wait-start     {why, model}  and  wait-end {why, model}             the client's wait hook (Production._set_waiting)
  tests          {green, failing[]}                                   after each sandbox run
  review         {reviewer, verdict, evidence_ok}                     after each review call
  restore-green  {}                                                   the last green code is put back
  finish         {state, reason}                                      the run's end (p1b _finish) or the launcher's
  merge          {state, base, head?, attempts?}                      merge_queue._finish
A stage that never ran has no attempt events: nothing here makes one up. Stdlib only."""
import json
import threading
from datetime import datetime, timezone
from pathlib import Path

FILE = "events.jsonl"
TYPES = ("job-start", "attempt-start", "attempt-end", "wait-start", "wait-end", "tests", "review", "restore-green", "finish", "merge")
STAGES = ("planner", "writer_a_write", "writer_a_repair", "writer_b_write", "writer_b_repair", "reviewer_b", "reviewer_a", "reviewer_reask",
          "specialist_repair", "review_fix_repair", "fallback_write")
MERGE_STATES = frozenset({"merged", "merge-conflict", "tests-failed", "main-moved", "push-failed", "merge-error", "reviewed-bytes-changed"})   # the states only a merge ends in
_LOCK = threading.Lock()            # one appender at a time in this process (the job's lease keeps other processes out)


def now_ts():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse(raw):
    """The events of a file's bytes, in file order. A line that is not a JSON object with an int seq (a torn last line) is skipped."""
    out = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and isinstance(event.get("seq"), int) and not isinstance(event.get("seq"), bool):
            out.append(event)
    return out


def read_events(path):
    """Every readable event of one events.jsonl ([] when there is none)."""
    try:
        return _parse(Path(path).read_bytes())
    except FileNotFoundError:
        return []


def append_event(job_dir, event):
    """Appends one event to <job_dir>/events.jsonl and returns the line as written. The seq is the file's highest plus one. A torn last line
    (a crash mid-write) is closed with a newline first, so it never joins the new line."""
    if event.get("type") not in TYPES:
        raise ValueError(f"unknown event type {event.get('type')!r}")
    folder = Path(job_dir)
    path = folder / FILE
    with _LOCK:
        folder.mkdir(parents=True, exist_ok=True)
        _ignore_loop(folder.parent)
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            raw = b""
        old = _parse(raw)
        job_id = event.get("job_id") or next((e["job_id"] for e in reversed(old) if e.get("job_id")), None) or folder.name
        record = {"seq": max((e["seq"] for e in old), default=0) + 1, "ts": now_ts(), "job_id": job_id, "type": event["type"]}
        record.update((k, v) for k, v in event.items() if k not in record)
        line = json.dumps(record, default=str).encode("utf-8") + b"\n"
        with open(path, "ab") as fh:
            fh.write((b"\n" if raw and not raw.endswith(b"\n") else b"") + line)
            fh.flush()
    return record


def _ignore_loop(loop_dir):
    """A checkout that does not ignore _loop (a test repo) would show the events file as untracked work: `_loop/.gitignore` with `*` hides
    the whole folder. The real repo ignores _loop already, so there it changes nothing."""
    if loop_dir.name == "_loop" and not (loop_dir / ".gitignore").exists():
        try:
            (loop_dir / ".gitignore").write_text("*\n", encoding="utf-8")
        except OSError:
            pass


def emit(job_dir, type_, **fields):
    """append_event for the engine's hooks: never raises, so an event that cannot be written never changes a job's outcome. Returns the
    line written, or None."""
    if job_dir is None:
        return None
    try:
        return append_event(job_dir, dict(fields, type=type_))
    except (OSError, ValueError, TypeError):
        return None


def job_folders(home, job_id):
    """The folders that can hold a job's events, archive first: results/<job_id>/ and wt/<job_id>/_loop/."""
    return [Path(home) / "results" / job_id, Path(home) / "wt" / job_id / "_loop"]


def events_files(home, job_id):
    """Every events.jsonl of the job (archive first, then the worktree)."""
    out = []
    for base in job_folders(home, job_id):
        if base.is_dir():
            out += sorted(p for p in base.glob("*/" + FILE) if p.is_file())
    return out


def job_dir_for(home, job_id):
    """The folder the next event of a launched job goes to: the archived copy once there is one (the worktree may go any moment after
    the archive), else the worktree's. None when neither holds an events file."""
    found = events_files(home, job_id)
    return found[0].parent if found else None


def read_job_events(home, job_id):
    """The job's events by seq, from the archive and the worktree together (the archive is a copy, so a seq in both is one event)."""
    by_seq = {}
    for path in events_files(home, job_id):
        for event in read_events(path):
            if event.get("job_id") == job_id:
                by_seq.setdefault(event["seq"], event)
    return [by_seq[k] for k in sorted(by_seq)]
