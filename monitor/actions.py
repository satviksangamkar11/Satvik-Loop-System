"""The actions of the API that change state: validate and queue a job (with its stage table frozen beside it), start background loops, STOP,
edit a future stage of a running job, and plan a job (the planner) and approve it. Each action is one function; the routes in api.py call
them through store.respond, so a repeat with the same Idempotency-Key runs nothing again."""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from . import config_api
import fix_loop
import launch
import limits_config
import loop_home
import openrouter_call as oc
import plan_job
import stage_config as sc
from . import views
from .store import ApiError, atomic_write_json, read_json, repo, home as satvik_home

MAX_PLAN_FILES = plan_job.MAX_FILES
LOOP_MAX = 4


# ---------------------------------------------------------------- jobs

def _rel_job_path(path):
    """A job file path relative to the repo root, refused when it escapes it (the engine's own path rule)."""
    if not isinstance(path, str) or not path.strip():
        raise ApiError(400, "invalid-job", "job_path is required", {"problems": ["job_path: required"]})
    try:
        fix_loop._validate_path(path, repo(), "job_path")
    except ValueError as exc:
        raise ApiError(400, "invalid-job", str(exc), {"problems": [str(exc)]})
    return repo() / path


def _load(path):
    try:
        return fix_loop.load_job(path)
    except FileNotFoundError:
        raise ApiError(400, "invalid-job", "job file not found", {"problems": [f"{path}: no such file"]})
    except ValueError as exc:
        raise ApiError(400, "invalid-job", "job file refused", {"problems": [str(exc)]})


def validate_job(body):
    """POST /jobs/validate: {"job": {...}} (written to a temp file for the engine's loader) or {"job_path"}. {valid, problems}."""
    if isinstance(body.get("job"), dict):
        home = satvik_home()
        folder = home / "api" / "validate"
        folder.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix="job-", suffix=".json", dir=str(folder))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(body["job"], fh)
            _load(Path(tmp))
        finally:
            os.unlink(tmp)
    elif isinstance(body.get("job_path"), str):
        _load(_rel_job_path(body["job_path"]))
    else:
        raise ApiError(400, "invalid-job", "send job or job_path", {"problems": ["job or job_path: required"]})
    return {"valid": True, "problems": []}


def _profile_name():
    return oc.active_profile()


def freeze_stages(changes, paid_ceiling_usd=None):
    """The stage table to freeze with a queued job: the active profile's roster as it stands now, with `changes` on top, and the job's
    approved paid ceiling (paid_ceiling_usd, I44: the most USD its paid calls may spend; None when none is approved). Returns the snapshot
    (stage_config.snapshot) or raises ApiError (400 with the problems, or 422 for a table the engine refuses)."""
    name = _profile_name()
    changes = config_api.clean_changes(name, changes) if changes else changes
    if changes:
        problems = config_api.authoritative_problems(name, changes)     # I45: the same validator as POST /stages/validate
        if problems:
            raise ApiError(400, problems[0]["code"], problems[0]["message"], {"problems": problems})
    try:
        data = sc._profile_data(name)[1]
        data["stages"] = sc._merge(sc._stages_block(data), changes or {})
        snap, _sha = sc.snapshot(data, paid_ceiling_usd=paid_ceiling_usd)
    except (oc.RosterRefused, sc.StageRefused) as exc:
        raise ApiError(422, getattr(exc, "code", "roster-refused"), str(exc))
    return snap


def approve_paid(changes, paid):
    """The job's approved paid ceiling in USD (I44). A job whose stages include a paid model, or that states a paid budget above zero, is
    always validated (config_api.validate_budget): an answer that is not valid (a price is unknown) refuses the job, and a worst case above
    the budget or the job review ceiling refuses it too. A job with neither is approved for 0."""
    name = _profile_name()
    if paid <= 0 and not config_api._paid_stages(name, changes or {}):
        return 0.0
    answer = config_api.validate_budget(name, changes or {}, paid)
    if not answer["valid"]:
        raise ApiError(422, "budget-unknown", "the paid worst case cannot be priced (an unknown price or model): the job is not queued",
                       {"problems": answer["problems"]})
    return float(answer["approved_usd"])


def queue_job(body):
    """POST /jobs: validate the job and its stages, freeze the resolved stage table with the queued job (queue/<file>.stages, written
    before the job file appears), and return stage_snapshot {sha256, revision}. 409 duplicate-pending or duplicate-running, 422
    already-completed (rerun=true runs it again). Not while STOP is on."""
    home = satvik_home()
    if (home / "STOP").exists():
        raise ApiError(409, "stop-on", "STOP is on: no new starts")
    path = _rel_job_path(body.get("job_path"))
    rerun = body.get("rerun", False)
    if not isinstance(rerun, bool):
        raise ApiError(400, "bad-request", "rerun is true or false")
    paid = body.get("paid_budget_usd", 0)
    if isinstance(paid, bool) or not isinstance(paid, (int, float)) or paid < 0:
        raise ApiError(400, "bad-budget", "paid_budget_usd is a number of at least 0")
    _load(path)
    changes = body.get("stages") or {}
    if isinstance(changes, list):
        changes = config_api._as_changes(changes)
    if not isinstance(changes, dict):
        raise ApiError(400, "bad-stages", "stages is an object of {stage: {model, effort, settings}}")
    changes = config_api.clean_changes(_profile_name(), changes)
    if "stage_revision" in body:
        current = config_api._roster_revision()
        if body["stage_revision"] != current:
            raise ApiError(409, "stale-revision", f"the stage settings are at revision {current}", {"revision": current})
    ceiling = approve_paid(changes, paid)                # validated whenever paid calls are possible (I44); 0 when none
    snap = freeze_stages(changes, paid_ceiling_usd=ceiling)   # the approved ceiling is frozen with the job's stages
    res = fix_loop.try_enqueue(path, home, rerun=rerun, stages=snap)
    if res["queued"]:
        return 201, {"queued": True, "queue_file": Path(res["path"]).name, "stage_snapshot": sc.snapshot_evidence(snap)}
    reason = res["reason"]
    if reason == "already-completed":
        raise ApiError(422, reason, "this job already passed; send rerun=true to run it again", {"job": res.get("job")})
    raise ApiError(409, reason, "the same job is already queued" if reason == "duplicate-pending" else "the same job is running",
                   {"job": res.get("job")})


# ---------------------------------------------------------------- loops

def running_count(home):
    return sum(1 for v in views.views_for(home) if v.get("derived_state") in views.RUNNING)


def spawn_loop(home, repo_root):
    """One background loop process (monitor.loops): it takes queued jobs until the queue is empty or STOP is on."""
    log_dir = Path(home) / "api" / "loops"
    log_dir.mkdir(parents=True, exist_ok=True)
    log = open(log_dir / f"loop-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}-{time.time_ns() % 100000}.log", "ab")
    subprocess.Popen([sys.executable, "-m", "monitor.loops"], cwd=str(repo_root), env=dict(os.environ), stdin=subprocess.DEVNULL,
                     stdout=log, stderr=subprocess.STDOUT, start_new_session=True)


def start_loops(count):
    """POST /loops/start {loops: 1-4}: refused with the engine's start_refusal codes (422, details.start_refusal) before any process starts.
    202 {started: n} at once; the loops run in the background."""
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= LOOP_MAX:
        raise ApiError(400, "bad-loops", f"loops is a whole number from 1 to {LOOP_MAX}")
    home = satvik_home()
    if (home / "STOP").exists():
        raise ApiError(409, "stop-on", "STOP is on: no new starts")
    queued = fix_loop.queue_dir(home)
    if not (queued.is_dir() and any(queued.glob("*.json"))):
        raise ApiError(422, "queue-empty", "the queue is empty: queue a job first")
    running = running_count(home)
    recent = [r["state"] for r in loop_home.Registry(home).recent(launch.PROVIDER_STREAK)]
    budget = fix_loop.ledger_budget()
    reason = launch.start_refusal(False, running, recent, budget)
    if reason is None and len(views._gate_holders(home).get("sandbox", [])) >= limits_config.sandbox_slots():
        reason = "no-sandbox-slot"
    if reason is not None:
        details = {"start_refusal": reason}
        if reason == "free-requests":
            details.update(free_left=budget["free_requests"], needed=launch.RESERVE_BASE + launch.RESERVE_PER_JOB * running)
        raise ApiError(422, reason, f"a loop cannot start: {reason}", details)
    for _ in range(count):
        spawn_loop(home, repo())
    return 202, {"started": count}


def set_stop(on):
    """POST /system/stop {on}: creates or removes SATVIK_HOME/STOP. A call in flight is not interrupted."""
    if not isinstance(on, bool):
        raise ApiError(400, "bad-request", "on is true or false")
    home = satvik_home()
    flag = home / "STOP"
    if on:
        flag.touch(exist_ok=True)
    else:
        flag.unlink(missing_ok=True)
    return {"stop": flag.exists()}


# ---------------------------------------------------------------- a running job's future stage

def patch_stage(job_id, stage, change, revision):
    """PATCH /loops/{id}/stages: changes one stage a running job has not started (a new snapshot revision for that job only)."""
    home = satvik_home()
    view = views._view(home, job_id)
    if view.get("queue_id") or view["derived_state"].split(":")[0] not in views.RUNNING:
        raise ApiError(409, "job-not-running", "only a running job's future stages can change")
    name = view.get("name")
    jdir = home / "wt" / job_id / "_loop" / str(name)
    if not (jdir / sc.SNAPSHOT_FILE).is_file():
        raise ApiError(409, "job-not-running", "the job's worktree has no stage table: it has finished")
    snap = sc.read_snapshot(jdir)
    if isinstance(revision, bool) or not isinstance(revision, int):
        raise ApiError(400, "bad-revision", "revision is a whole number")
    if revision != snap["revision"]:
        raise ApiError(409, "stale-revision", f"the job's stage table is at revision {snap['revision']}", {"revision": snap["revision"]})
    if not isinstance(change, dict):
        raise ApiError(400, "bad-stages", "change is an object of model, via, effort and settings")
    name_of_profile = sc.profile_of(snap)["name"]
    res = sc.apply_future_stage(jdir, stage, change,
                                validate=lambda changes: config_api.authoritative_problems(name_of_profile, changes))   # I45

    if res["ok"]:
        return {"ok": True, "revision": res["revision"]}
    code = res["code"]
    if code in ("stage-started", "no-snapshot"):
        raise ApiError(409, code, res["message"], {"stage": stage})
    if code == "unknown-stage":
        raise ApiError(400, code, res["message"], {"stage": stage})
    raise ApiError(422, code, res["message"], {"stage": stage})


# ---------------------------------------------------------------- plans (production only: the planner is a paid call)

PLANNER_HOOK = {"run": None}           # tests replace the planner call; None: plan_job.plan


def _plans_dir(home):
    return Path(home) / "api" / "plans"


def _plan_record(home, plan_id):
    if not plan_id or "/" in plan_id or "\\" in plan_id or plan_id.startswith("."):
        raise ApiError(404, "unknown-plan", f"no plan {plan_id}")
    rec = read_json(_plans_dir(home) / f"{plan_id}.json")
    if rec is None:
        raise ApiError(404, "unknown-plan", f"no plan {plan_id}")
    return rec


def _save_plan(home, rec):
    atomic_write_json(_plans_dir(home) / f"{rec['plan_id']}.json", rec)


def _run_planner(home, rec, root):
    """Runs plan_job.plan for one record and stores its answer. Called on a thread (the planner can take minutes)."""
    plan_dir = root / "_loop" / rec["name"] / "plan"
    try:
        run = PLANNER_HOOK["run"] or plan_job.plan
        planned = run(root=root, job_name=rec["name"], plan_dir=plan_dir, request=rec["request"], files=rec["files"])
        state, sha, assumed, reason = planned["state"], planned.get("job_sha256"), planned.get("assumed") or [], planned.get("reason")
        package_sha = planned.get("plan_sha256")
        if state == "planned":
            try:
                fix_loop.load_job(plan_dir / "job.json")
            except ValueError as exc:
                state, reason = "job-invalid", str(exc)
    except Exception as exc:                                  # the planner failed: the record says so, the process does not die
        state, sha, assumed, reason, package_sha = "planner-failed", None, [], f"{type(exc).__name__}", None
    rec = dict(read_json(_plans_dir(home) / f"{rec['plan_id']}.json", rec), state=state, job_sha256=sha, plan_sha256=package_sha,
               assumed=assumed, reason=reason, plan_dir=str(plan_dir))
    _save_plan(home, rec)


def create_plan(body):
    """POST /plans {name, request, files}: 422 planner-refused-in-test under the test profile (its planner is fake: no call). Otherwise one
    planner call on a thread (202 {plan_id, state: planning}); GET /plans/{id} then reports planned, assumed and job_sha256."""
    home = satvik_home()
    name = body.get("name")
    if not isinstance(name, str) or not launch.NAME_RE.match(name):
        raise ApiError(400, "bad-name", "name is a short lowercase job name")
    request = body.get("request")
    if not isinstance(request, str) or not request.strip():
        raise ApiError(400, "bad-request", "request is the plain-language request")
    files = body.get("files", [])
    if not isinstance(files, list) or not all(isinstance(f, str) for f in files):
        raise ApiError(400, "bad-request", "files is a list of repo paths")
    if len(files) > MAX_PLAN_FILES:
        raise ApiError(400, "too-many-files", f"at most {MAX_PLAN_FILES} files may be sent to the planner", {"max": MAX_PLAN_FILES})
    root = repo()
    problem = plan_job.input_problem(root, files, request)              # I55: a secret file, key or credential is never sent to the planner
    if problem:
        raise ApiError(400, "secret-input", problem)
    if _profile_name() == "test":
        raise ApiError(422, "planner-refused-in-test", "test profile: free only, and its planner is fake")
    if (home / "STOP").exists():
        raise ApiError(409, "stop-on", "STOP is on: no new starts")
    conflict = plan_job.intent_conflict(root, name, request, files)      # a plan name is bound to its request (I51)
    if conflict is not None:
        raise ApiError(409, "plan-request-conflict", conflict, {"name": name})
    import uuid
    plan_id = uuid.uuid4().hex
    rec = {"plan_id": plan_id, "name": name, "request": request, "files": files, "state": "planning", "job_sha256": None, "assumed": [],
           "reason": None, "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    _save_plan(home, rec)
    if PLANNER_HOOK.get("sync"):
        _run_planner(home, rec, root)
    else:
        threading.Thread(target=_run_planner, args=(home, rec, root), daemon=True).start()
    return 202, {"plan_id": plan_id, "state": "planning"}


def get_plan(plan_id):
    home = satvik_home()
    rec = _plan_record(home, plan_id)
    return {"plan_id": rec["plan_id"], "state": rec["state"], "assumed": rec.get("assumed") or [], "job_sha256": rec.get("job_sha256"),
            "plan_sha256": rec.get("plan_sha256"), "reason": rec.get("reason")}


def approve_plan(plan_id, job_sha256, approve_assumed):
    """POST /plans/{id}/approve: queues the planned job (its stage table frozen now) and starts one loop. The approval starts the job.
    The approval covers the whole plan: its job file, spec, PLAN.md, expected lines and acceptance and control files are hashed together
    (plan_sha256), and the queued job carries that hash. 409 plan-changed when any of it no longer matches; 422 assumed-not-approved while
    ASSUMED lines are unapproved. The launch checks the package again and installs its spec and files into the worktree before validation."""
    home = satvik_home()
    rec = _plan_record(home, plan_id)
    if rec["state"] != "planned":
        raise ApiError(409, "not-planned", f"the plan is {rec['state']}, not planned")
    plan_dir = Path(rec["plan_dir"])
    try:
        plan_job.read_package(plan_dir, rec.get("plan_sha256"))
    except plan_job.PlanChanged:
        raise ApiError(409, "plan-changed", "the planned job, its spec or its test files changed since it was planned")
    if job_sha256 != rec.get("job_sha256"):
        raise ApiError(409, "plan-changed", "the planned job changed since it was planned")
    if rec.get("assumed") and approve_assumed is not True:
        raise ApiError(422, "assumed-not-approved", "approve the ASSUMED lines first (approve_assumed=true)", {"assumed": rec["assumed"]})
    if (home / "STOP").exists():
        raise ApiError(409, "stop-on", "STOP is on: no new starts")
    declared = (json.loads((plan_dir / "job.json").read_text(encoding="utf-8")).get("budget") or {}).get("review_usd")
    ceiling = approve_paid({}, float(declared) if isinstance(declared, (int, float)) and not isinstance(declared, bool) else 0.0)
    snap = freeze_stages({}, paid_ceiling_usd=ceiling)     # the planned job's own declared review budget is its approval (I44)
    approved_plan = {"plan_dir": str(plan_dir), "job_sha256": rec["job_sha256"], "plan_sha256": rec["plan_sha256"]}
    res = fix_loop.try_enqueue(plan_dir / "job.json", home, stages=snap, plan=approved_plan)
    if not res["queued"]:
        raise ApiError(409 if res["reason"] != "already-completed" else 422, res["reason"], "the planned job is already queued or completed",
                       {"job": res.get("job")})
    approved = {"job_sha256": rec["job_sha256"], "plan_sha256": rec["plan_sha256"], "assumed": rec.get("assumed") or [],
                "approved": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    atomic_write_json(plan_dir / "APPROVED.json", approved)
    rec = dict(rec, state="started", queued_as=Path(res["path"]).name)
    _save_plan(home, rec)
    spawn_loop(home, repo())
    return 202, {"job_id": Path(res["path"]).stem}
