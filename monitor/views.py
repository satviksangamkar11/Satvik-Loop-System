"""The read models of docs/API_CONTRACT.md section 2, built from the engine's files and the engine's own readers (job_history, event_log,
stage_config, shared_limits, model_catalog). Nothing is invented: a value the files do not hold is None, and a stage that never ran is
"not-triggered" (or "pending" for a queued job, which has run nothing yet). Read only: no function here writes a file."""
import io
import json
import re
import subprocess
import time
import zipfile
from pathlib import Path

import event_log
import fix_loop
import job_history
import launch
import limits_config
import loop_home
import model_catalog as mc
import openrouter_call as oc
import sandbox_runner
import shared_limits
import stage_config as sc
from .store import ApiError, read_json, repo, home as satvik_home

RUNNING = ("running", "waiting", "waiting-unknown", "stale-warning")
WAITING = ("waiting", "waiting-unknown")
END_STATES = frozenset({"passed", "review-open", "escalate", "needs_review", "blocked", "provider-unavailable", "budget-exceeded",
                        "quota-exhausted", "deadline", "integrity-mismatch", "acceptance-tampered", "exception", "invalid-spec",
                        "job-too-large", "sandbox-unavailable", "acceptance-invalid", "refused", "stopped"})
END_MAP = {"error": "exception"}          # the launcher's finish for a run that raised (launch.Launcher.finish "error")
MERGE_STATES = frozenset({"merged", "merge-conflict", "tests-failed", "push-failed", "main-moved", "merge-error", "pending", "none"})
WAIT_WHYS = frozenset({"model-slot", "account-pace", "429-backoff", "retry-backoff"})
STAGE_ORDER = sc.STAGE_NAMES            # the ten configurable stages, in the contract's order


# ---------------------------------------------------------------- small helpers

def iso(epoch):
    """ISO-8601 UTC for an epoch time, None for a missing one."""
    if epoch is None:
        return None
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(float(epoch)))
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def now_iso():
    return iso(time.time())


def _read(path):
    return read_json(path)


def job_ids(home):
    """Every job id the files or the registry know (not the queue)."""
    rows = job_history._registry_rows(home)
    return job_history._known_ids(home, rows)


def queued_ids(home):
    folder = Path(home) / "queue"
    return sorted(p.stem for p in folder.glob("*.json")) if folder.is_dir() else []


def job_exists(home, job_id):
    return job_id in job_ids(home) or (Path(home) / "queue" / f"{job_id}.json").is_file()


def _job_dir(home, job_id, name):
    """The job's folder: the worktree's while it exists, else the archive's (results/<job_id>/<name>), else None."""
    if not name:
        return None
    for base in (Path(home) / "wt" / job_id / "_loop" / name, Path(home) / "results" / job_id / name):
        if (base / "status.json").is_file() or (base / "stage_snapshot.json").is_file():
            return base
    return None


def _job_file(home, job_id, name):
    """The job file a run used (its spec and files), or None."""
    if not name:
        return None
    for path in (Path(home) / "wt" / job_id / "_loop" / f"{name}.job.json", Path(home) / "results" / job_id / f"{name}.job.json"):
        data = _read(path)
        if isinstance(data, dict):
            return data
    return None


# ---------------------------------------------------------------- one job, in one dict

def facts(home, view):
    """Everything the summary and the detail need about one job view (job_history.job_view / list_jobs): its status.json, its events
    summary, its job folder and its frozen stage snapshot. A queued job has no events; its snapshot is its queue sidecar."""
    if view.get("queue_id"):
        qfile = Path(home) / "queue" / f"{view['queue_id']}.json"
        job = _read(qfile)
        return {"status": None, "summary": job_history.summarize([]), "name": view.get("name"), "dir": None, "snapshot": fix_loop.read_stages(qfile),
                "job": job if isinstance(job, dict) else None, "queued": True}
    job_id = view["job_id"]
    events = event_log.read_job_events(home, job_id)
    status = job_history._status(home, job_id)
    name = view.get("name") or (status or {}).get("name")
    jdir = _job_dir(home, job_id, name)
    snap = sc.read_snapshot(jdir) if jdir else None
    return {"status": status, "summary": job_history.summarize(events), "name": name, "dir": jdir, "snapshot": snap,
            "job": _job_file(home, job_id, name), "queued": False}


def _end_state(view, x):
    derived, _, tail = view["derived_state"].partition(":")
    if derived == "finished":
        end = END_MAP.get(tail, tail)
    elif derived == "merged":
        end = (view.get("finish") or {}).get("state") or (x["status"] or {}).get("state")
        end = END_MAP.get(end, end)
    else:
        return None
    return end if end in END_STATES else None


def _merge_state(view, x, derived, tail, end):
    if derived == "merged" and tail in MERGE_STATES:
        return tail
    state = (view.get("merge") or {}).get("state")
    if state in MERGE_STATES:
        return state
    if derived == "finished" and end == "passed":
        return "pending"               # passed: waiting for the merge queue
    return "none"


def _waiting(view, x, derived):
    if derived != "waiting":
        return None
    waiting = (x["status"] or {}).get("waiting")
    if not isinstance(waiting, dict):
        waiting = x["summary"]["open_wait"] or {}
    why = waiting.get("why")
    return {"why": why if why in WAIT_WHYS else None, "since": waiting.get("since"), "model": waiting.get("model")}


def summary(home, view, x=None):
    """A JobSummary (contract section 2) for one job view, or None for a view that is not a job (derived "unknown")."""
    x = facts(home, view) if x is None else x
    raw = view["derived_state"]
    if raw == "unknown":
        return None
    derived, _, tail = raw.partition(":")
    if derived not in ("queued", "running", "waiting", "waiting-unknown", "stale-warning", "orphan", "finished", "merged"):
        return None
    end = _end_state(view, x)
    open_attempts = [a for a in x["summary"]["attempts"] if a.get("status") is None]
    active = open_attempts[-1] if open_attempts else None
    lease = view.get("lease") or {}
    heartbeat = None
    if lease.get("exists") and not lease.get("released") and lease.get("heartbeat_age_s") is not None:
        heartbeat = int(round(lease["heartbeat_age_s"]))
    budget = ((x["status"] or {}).get("evidence") or {}).get("budget") or {}
    finish = view.get("finish") or {}
    # The finish event, the merge outcome, else the registry row's `finished` (the time the job's last record was written: the merge outcome
    # for a merged job). That row is written for every job, so a job whose folders lack the events still has its time.
    finished_at = finish.get("ts") or (view.get("merge") or {}).get("ts") or iso((view.get("registry") or {}).get("finished"))
    return {
        "job_id": view.get("job_id") or view.get("queue_id"),
        "name": view.get("name") or x["name"],
        "derived_state": derived,
        "end_state": end,
        "merge_state": _merge_state(view, x, derived, tail, end),
        "active_stage": active.get("stage") if active else None,
        "active_model": active.get("model") if active else None,
        "waiting": _waiting(view, x, derived),
        "heartbeat_age_s": heartbeat,
        "started": view.get("started") or None,
        "finished": finished_at if derived in ("finished", "merged") else None,
        "free_requests_used": budget.get("free_requests_used"),
        "review_usd_spent": budget.get("review_usd_spent"),
    }


def _lease_detail(home, job_id):
    lease = _read(Path(home) / "leases" / f"{job_id}.json")
    if not isinstance(lease, dict):
        return {"held": False, "pid": None, "pid_alive": None, "heartbeat": None, "generation": None}
    alive = loop_home.pid_alive(lease.get("pid", 0))
    return {"held": (not lease.get("released")) and alive, "pid": lease.get("pid"), "pid_alive": alive,
            "heartbeat": iso(lease.get("heartbeat")), "generation": lease.get("generation")}


def stages_of(view, x, derived):
    """The ten stages with status and attempts. A stage the job started (a marker, a step or a call record) is "running" while its call is
    open, else "ran". A stage never started is "not-triggered" once the job has begun, and "pending" while the job is still queued."""
    snap = x["snapshot"]
    started = set()
    if snap is not None:
        started = sc.started_stages(x["dir"], snap) if x["dir"] else set()     # no folder yet: nothing has started
    attempts = x["summary"]["attempts"]
    out = []
    for stage in STAGE_ORDER:
        mine = [a for a in attempts if a.get("stage") == stage]
        open_call = any(a.get("status") is None for a in mine)
        if open_call:
            status = "running"
        elif mine:
            status = "ran"
        elif stage in started:
            status = "running" if derived in RUNNING else "ran"
        elif derived == "queued":
            status = "pending"
        else:
            status = "not-triggered"
        out.append({"stage": stage, "status": status, "attempts": len(mine)})
    return out


def detail(home, view, x=None):
    """A JobDetail: the summary plus the job's spec, files, reason, tests, review, snapshot, stages, lease, budget, hashes and archive."""
    x = facts(home, view) if x is None else x
    base = summary(home, view, x)
    if base is None:
        return None
    status = x["status"] or {}
    evidence = status.get("evidence") or {}
    job = x["job"] or {}
    job_id = base["job_id"]
    snap_ev = sc.snapshot_evidence(x["snapshot"]) if x["snapshot"] is not None else None
    budget = evidence.get("budget")
    budget_out = None if budget is None else {k: budget.get(k) for k in ("free_requests_used", "free_requests", "review_usd_spent", "review_usd",
                                                                          "repair_calls")}
    registry = view.get("registry") or {}
    result = registry.get("result") or {}
    archived = (Path(home) / "results" / job_id).is_dir() if not x["queued"] else False
    passed = base["derived_state"] in ("merged",) or (base["derived_state"] == "finished" and base["end_state"] == "passed")
    out = dict(base)
    out.update({
        "profile": status.get("profile") or (job.get("profile") if job else None),
        "spec": job.get("spec"),
        "files": job.get("files"),
        "reason": status.get("reason"),
        "tests_passed": status.get("tests_passed"),
        "failing_tests": status.get("failing_tests"),
        "review_verdict": status.get("review_verdict"),
        "findings": status.get("findings") if status else None,
        "stage_snapshot": snap_ev,
        "stages": stages_of(view, x, base["derived_state"]),
        "lease": None if x["queued"] else _lease_detail(home, job_id),
        "budget": budget_out,
        "hashes": {"tested": evidence.get("tested_hash"), "reviewed": evidence.get("reviewed_hash"),
                   "delivered": evidence.get("final_hash") if passed else None},
        "archive": {"available": archived, "archive_failed": bool(result.get("archive_failed")) if result else False} if not x["queued"] else
                   {"available": False, "archive_failed": False},
    })
    if view.get("merge"):
        merged = view["merge"]
        out["merge"] = {"merge_state": merged.get("state"), "head": merged.get("head"), "attempts": merged.get("attempts")}
    return out


def job_list(home, filter_=None):
    """{"jobs": [JobSummary], "index_ok": bool} for the filter (all, running, queued, finished, failed)."""
    listing = job_history.list_jobs(home, filter_)
    jobs = []
    for view in listing["jobs"]:
        row = summary(home, view)
        if row is not None:
            jobs.append(row)
    return {"jobs": jobs, "index_ok": listing["index_ok"]}


def views_for(home):
    """Every job view (queued and known), once: the list the overview counts."""
    return job_history.list_jobs(home, None)["jobs"]


# ---------------------------------------------------------------- events

ATTEMPT_FIELDS = ("attempt_id", "stage", "role", "mode", "model", "via", "effort", "status", "prompt_tokens", "completion_tokens",
                  "reasoning_tokens", "finish_reason", "seconds", "error")


def _is_http_success(code):
    return isinstance(code, int) and not isinstance(code, bool) and 200 <= code < 300


def _attempt_status(e):
    """The contract's attempt_status (ok | failed | rejected) for an attempt-end. The engine's `status` is the HTTP status of the last
    request (a number), so it is not the attempt status: the `failed` flag decides. A call that did not fail is ok. A failed call whose
    HTTP reply arrived with a 2xx (the reply came back but the job could not use it) is rejected; any other failure is failed."""
    if not e.get("failed"):
        return "ok"
    return "rejected" if _is_http_success(e.get("status")) else "failed"


ATTEMPT_FROM_START = ("stage", "role", "mode", "model", "via", "effort")    # the fields an attempt-end may lack: its attempt-start has them


def _event(e, starts=None):
    """One event as the API shows it: the engine's job_id, failed and requests (its request count) are dropped, none is in the contract. An
    attempt event carries every attempt field (null when the engine has none yet); its status is the contract's attempt_status, never the
    HTTP code. starts maps attempt_id to its attempt-start event: an attempt-end takes from it each of ATTEMPT_FROM_START it lacks."""
    out = {k: v for k, v in e.items() if k not in ("job_id", "failed", "requests")}
    if e.get("type") in ("attempt-start", "attempt-end"):
        if e.get("type") == "attempt-end":
            start = (starts or {}).get(e.get("attempt_id")) or {}
            for key in ATTEMPT_FROM_START:
                if out.get(key) is None:
                    out[key] = start.get(key)
            out["status"] = _attempt_status(e)
        for key in ATTEMPT_FIELDS:
            out.setdefault(key, None)
        if e.get("type") == "attempt-start" and out.get("status") is None:
            out["status"] = "running"
    return out


def events(home, job_id, after=0):
    """The attempt events after seq `after`, mapped to the contract's event shape. A stage that never ran has none. An attempt-end is read
    with its attempt-start, which can come before `after`, so the starts are taken from every event, not only the new ones."""
    if not job_exists(home, job_id):
        raise ApiError(404, "unknown-job", f"no job {job_id}")
    rows = event_log.read_job_events(home, job_id)
    starts = {e.get("attempt_id"): e for e in rows if e.get("type") == "attempt-start"}
    new = [_event(e, starts) for e in rows if e["seq"] > after]
    next_after = new[-1]["seq"] if new else after
    return {"events": new, "next_after": next_after, "index_ok": job_history.index_ok(home)}


# ---------------------------------------------------------------- results and diffs

def _review_files(jdir):
    if not jdir:
        return []
    found = []
    for path in (jdir / "history").glob("review_*.txt") if (jdir / "history").is_dir() else []:
        tail = path.stem[len("review_"):]
        if tail.isdigit():
            found.append((int(tail), path))
    return [f"history/{p.name}" for _, p in sorted(found)]


def results(home, job_id):
    """The GET /results/{id} shape. A job that has not finished has no results yet (404 not-finished)."""
    view = _view(home, job_id)
    if view.get("queue_id") or view["derived_state"].split(":")[0] in RUNNING + ("queued",):
        raise ApiError(404, "not-finished", "the job has not finished: its results come after the run ends")
    x = facts(home, view)
    base = summary(home, view, x)
    status = x["status"] or {}
    review_events = [e for e in event_log.read_job_events(home, job_id) if e.get("type") == "review"]
    files = _review_files(x["dir"])
    reviews = []
    last_fix = max((i for i, e in enumerate(review_events) if e.get("verdict") == "FIX FIRST"), default=None)
    for i, e in enumerate(review_events):
        reviews.append({"reviewer": e.get("reviewer"), "verdict": e.get("verdict"), "findings": list(status.get("findings") or [])
                        if i == last_fix else [], "file": files[i] if i < len(files) else None, "evidence_ok": e.get("evidence_ok")})
    failing = []
    for test_id in status.get("failing_tests") or []:
        failing.append({"id": test_id, "message": None, "file": test_id.split("::")[0] if "::" in test_id else None, "line": None})
    name = x["name"]
    artifacts = []
    if x["dir"]:
        for path in sorted(x["dir"].rglob("*")):
            rel = path.relative_to(x["dir"]).as_posix()
            if path.is_file() and not rel.startswith(("candidate/", "original/", "key_snapshots/")):
                artifacts.append(f"{name}/{rel}")
    branch = _branch(home, job_id)
    merge = _read(Path(home) / "results" / job_id / "merge.json")
    return {"job_id": job_id, "end_state": base["end_state"], "merge_state": base["merge_state"],
            "tests": {"passed": status.get("tests_passed"), "failing": failing},
            "reviews": reviews, "diff_available": _diff_base(home, job_id) is not None, "artifacts": artifacts[:200],
            "branch": branch, "merge": merge if isinstance(merge, dict) else None}


def _view(home, job_id):
    if not isinstance(job_id, str) or not job_id:
        raise ApiError(404, "unknown-job", "no such job")
    if not job_exists(home, job_id):
        raise ApiError(404, "unknown-job", f"no job {job_id}")
    try:
        view = job_history.job_view(home, job_id)
    except ValueError:
        raise ApiError(404, "unknown-job", f"no job {job_id}")
    if view["derived_state"] == "unknown":
        raise ApiError(404, "unknown-job", f"no job {job_id}")
    return view


def _branch(home, job_id):
    name = f"job/{job_id}"
    res = subprocess.run(["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{name}"], cwd=str(repo()), capture_output=True, text=True)
    return name if res.returncode == 0 else None


def _diff_base(home, job_id):
    """The base commit a job's diff is taken from: its registry row's base, else the merge's base, else None."""
    reg = job_history._registry_row(home, job_id, job_history._registry_rows(home))
    base = (reg or {}).get("base")
    if not base:
        merge = _read(Path(home) / "results" / job_id / "merge.json") or {}
        base = merge.get("base")
    return base if base and _branch(home, job_id) else None


def diff(home, job_id):
    """{"files": [{path, unified_diff}]} for a finished job, from its base to its branch (read only)."""
    view = _view(home, job_id)
    if view["derived_state"].split(":")[0] in RUNNING + ("queued",):
        raise ApiError(404, "not-finished", "the job has not finished: its diff comes after the run ends")
    base = _diff_base(home, job_id)
    if base is None:
        raise ApiError(404, "no-diff", "this job has no branch or base to diff against")
    branch = f"job/{job_id}"
    names = subprocess.run(["git", "diff", "--name-only", base, branch], cwd=str(repo()), capture_output=True, text=True)
    if names.returncode != 0:
        raise ApiError(404, "no-diff", "the diff could not be read from the repository")
    files = []
    for path in [p for p in names.stdout.splitlines() if p.strip()]:
        text = subprocess.run(["git", "diff", base, branch, "--", path], cwd=str(repo()), capture_output=True, text=True)
        files.append({"path": path, "unified_diff": text.stdout})
    return {"files": files}


def download(home, job_id):
    """A zip of results/<job_id>/ (its entries are under <job_id>/). 404 not-archived when there is no archive."""
    folder = Path(home) / "results" / job_id
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,159}$", job_id) or not folder.is_dir():
        raise ApiError(404, "not-archived", "this job has no archived results")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                zf.write(path, arcname=f"{job_id}/{path.relative_to(folder).as_posix()}")
    return buffer.getvalue()


# ---------------------------------------------------------------- overview, system

def _gate_holders(home):
    table = _read(Path(home) / "gates.json") or {}
    out = {}
    for name, entries in table.items():
        if isinstance(entries, list):
            out[name] = [e for e in entries if isinstance(e, dict) and loop_home.pid_alive(e.get("pid", 0))]
    return out


def overview(home):
    views = views_for(home)
    rows = [summary(home, v) for v in views]
    rows = [r for r in rows if r is not None]
    usage = shared_limits.usage(home)
    limits = limits_config.load()
    stop = (Path(home) / "STOP").exists()
    sandbox_used = len(_gate_holders(home).get("sandbox", []))
    return {
        "loops_running": sum(1 for r in rows if r["derived_state"] in RUNNING),
        "queue_depth": sum(1 for r in rows if r["derived_state"] == "queued"),
        "jobs_waiting": sum(1 for r in rows if r["derived_state"] in WAITING),
        "active_errors": sum(1 for r in rows if r["derived_state"] == "orphan"),
        "stop": stop,
        "free_today": {"used": usage["free_today"], "cap": usage["free_cap"], "cap_source": "live" if usage["free_cap_source"] == "live" else "fallback"},
        "pace": {"sent_last_60s": usage["sent_last_60s"], "limit": shared_limits.PER_MINUTE},
        "review_usd_today": {"spent": usage["review_spent_usd"], "cap": limits["review_usd_daily_cap"]},
        "pending_reviews": sum(1 for r in rows if r["end_state"] == "review-open"),
        "sandbox_slots": {"used": sandbox_used, "total": limits["sandbox_slots"]},
        "updated": now_iso(),
        "index_ok": job_history.index_ok(home),
    }


def _docker_ok(args):
    try:
        res = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return res.returncode == 0


def docker_status():
    """'ok' or 'down' for the Docker daemon (docker info)."""
    return "ok" if _docker_ok(["info"]) else "down"


def sandbox_image_status():
    """'ok' or 'missing' for the sandbox image (docker image inspect)."""
    return "ok" if _docker_ok(["image", "inspect", sandbox_runner.DEFAULT_IMAGE]) else "missing"


def health(home):
    """GET /system/health: the backend, the engine's version, Docker, the sandbox image, SATVIK_HOME, index health, key aliases (presence
    only, never a value) and STOP. home may be None (SATVIK_HOME unset): the health answer is still given, with satvik_home None."""
    res = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=str(repo()), capture_output=True, text=True)
    version = res.stdout.strip() if res.returncode == 0 and res.stdout.strip() else None
    keys = oc.load_keys()
    return {
        "backend": "ok",
        "profile": oc.active_profile(),
        "engine_version": version,
        "docker": docker_status(),
        "sandbox_image": sandbox_image_status(),
        "satvik_home": str(home) if home is not None else None,
        "index_ok": job_history.index_ok(home) if home is not None else False,
        "keys": [{"alias": "free", "present": "free" in keys}, {"alias": "paid", "present": "paid" in keys}],
        "stop": (Path(home) / "STOP").exists() if home is not None else False,
    }


def limits_view(home):
    """GET /system/limits: leases, claims, gates (live holders against capacity) and the day's ledger."""
    now = time.time()
    leases = []
    lease_dir = Path(home) / "leases"
    for path in sorted(lease_dir.glob("*.json")) if lease_dir.is_dir() else []:
        lease = _read(path) or {}
        job_id = path.stem
        alive = loop_home.pid_alive(lease.get("pid", 0))
        leases.append({"job_id": job_id, "held": (not lease.get("released")) and alive,
                       "heartbeat_age_s": int(round(now - float(lease.get("heartbeat", now)))) if lease.get("heartbeat") is not None else None,
                       "pid_alive": alive})
    claims = []
    table = (_read(Path(home) / "claims.json") or {}).get("claims") or {}
    for path_key, entries in table.items():
        for entry in entries if isinstance(entries, list) else []:
            if isinstance(entry, dict):
                claims.append({"path": path_key, "job_id": entry.get("job")})
    limits = limits_config.load()
    gates = []
    for name, holders in _gate_holders(home).items():
        capacity = limits["sandbox_slots"] if name == "sandbox" else shared_limits.slot_capacity(name)
        gates.append({"gate": name, "holders": len(holders), "capacity": capacity})
    usage = shared_limits.usage(home)
    return {"revision": limits["revision"], "leases": leases, "claims": claims, "gates": gates,
            "ledger": {"free_used": usage["free_today"], "free_cap": usage["free_cap"], "review_spent_usd": usage["review_spent_usd"]}}


def counters(home):
    """The launcher's counters as {name: count}."""
    return loop_home.Registry(home).counters()
