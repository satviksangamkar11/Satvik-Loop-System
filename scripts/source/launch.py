"""WP3 launcher (Revision 4): unique job ids, one git worktree and branch per job, start limits, stop switch.

Start: check the limits, reserve a job id (loop_home lease, so two launchers never share one), fetch origin/main,
`git worktree add -b job/<id> home/wt/<id> origin/main`, then the sandbox slot and the claims. A refused start leaves no worktree,
no branch and no live lease behind.
Finish: `Launcher.finish` releases the slot, the claims and the lease and records the state. The caller keeps or removes the worktree.

The shared state is WP2's loop_home (lease, claims, registry, gate) under one home folder outside every worktree.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import event_log
import job_history
import limits_config
import loop_home

NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")
RESERVE_BASE = 100            # free daily requests kept back, plus RESERVE_PER_JOB for each running job
RESERVE_PER_JOB = 40
REVIEW_SPEND_CAP_USD = 0.50
PROVIDER_STREAK = 3           # the last 3 jobs all provider-unavailable blocks the start
RUNNING_SCAN = 500            # registry rows looked at when counting running jobs
SANDBOX_SLOTS = 4             # sandbox gate capacity (owner decision: matches the capacity plan): jobs that may hold a sandbox slot at once


def git(args, cwd, check=True):
    """Run git in cwd. Returns the CompletedProcess (stdout and stderr captured)."""
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=check)


def _sandbox_slots():
    """The sandbox gate capacity from config/limits.json (limits_config), read at each start; SANDBOX_SLOTS when unreadable."""
    try:
        return limits_config.sandbox_slots()
    except Exception:
        return SANDBOX_SLOTS


def real_api(home_dir):
    """The WP2 objects for one loop home. `home` is an attribute here; loop_home.home() is the folder the caller passes in."""
    home = Path(home_dir)
    return SimpleNamespace(home=home, lease=loop_home.Lease(home), claims=loop_home.Claims(home),
                           registry=loop_home.Registry(home), gate=loop_home.Gate(home))


def new_job_id(name, exists=lambda job_id: False, now=None):
    """<name>-<YYYYMMDD-HHMM>-<4 hex>. A drawn id that `exists` says is taken is drawn again."""
    if not NAME_RE.match(name):
        raise ValueError(f"bad job name: {name!r}")
    stamp = time.strftime("%Y%m%d-%H%M", time.localtime(time.time() if now is None else now))
    while True:
        job_id = f"{name}-{stamp}-{secrets.token_hex(2)}"
        if not exists(job_id):
            return job_id


def _review_cap_usd():
    """The daily review cap a start is refused at: config/limits.json (limits_config), read at each call; REVIEW_SPEND_CAP_USD when unreadable."""
    try:
        return limits_config.review_usd_daily_cap()
    except Exception:
        return REVIEW_SPEND_CAP_USD


def start_refusal(stop_present, running, recent_states, budget):
    """The first start limit that blocks, or None. recent_states is newest first (registry.recent). budget is the day's figures
    ({free_requests, review_spend_usd}) or None when there is no budget snapshot. The sandbox slot is checked by Launcher.start."""
    if stop_present:
        return "stop-file"
    last = list(recent_states)[:PROVIDER_STREAK]
    if len(last) == PROVIDER_STREAK and all(s == "provider-unavailable" for s in last):
        return "provider-unavailable"
    if budget is None:
        return "no-budget-snapshot" if running >= 1 else None         # without figures, one loop at a time
    if budget["free_requests"] < RESERVE_BASE + RESERVE_PER_JOB * running:
        return "free-requests"
    if budget["review_spend_usd"] >= _review_cap_usd():
        return "review-spend"
    return None


def stop_requested(home_dir):
    return (Path(home_dir) / "STOP").exists()


def counter_lines(home_dir) -> list[str]:
    """The registry counters as "name = count" lines, sorted by name. loop_status shows these lines (step 8)."""
    counts = loop_home.Registry(home_dir).counters()
    return [f"{name} = {counts[name]}" for name in sorted(counts)]


def run_rounds(api, job_id, gen, rounds, do_round, restore, home_dir):
    """Round loop with the stop switch. The check sits before every round, so a call in flight always finishes.
    When home/STOP exists, restore() puts the job's files back and the loop returns "stopped"."""
    for i in range(rounds):
        if stop_requested(home_dir):
            restore()
            return "stopped"
        api.lease.beat(job_id, gen)
        do_round(i)
    return "done"


KEEP_WORKTREE = {"passed", "merge-conflict"}      # passed waits for the merge queue; merge-conflict stays for the owner
ARCHIVE_SKIP = ("openrouter", ".gitignore")        # never archived: the OpenRouter key snapshots (_loop/openrouter) and _loop/.gitignore (`*`, event_log),
                                                   # which would make the published evidence git-ignored
_copytree = shutil.copytree                        # a hook for tests (an archive copy that fails)


def archive_job(home, job_id):
    """Copies the job's evidence (wt/<job_id>/_loop, without ARCHIVE_SKIP) to results/<job_id>/: first into a temporary folder, then one
    rename, so a reader sees all of it or none. Repeat-safe: an existing archive is kept. Returns "archived", "kept", "none" (no _loop to
    keep) or "failed: <why>"; it never raises, so finishing a job is never blocked by it."""
    src = Path(home) / "wt" / job_id / "_loop"
    dest = Path(home) / "results" / job_id
    if dest.exists():
        return "kept"
    if not src.is_dir():
        return "none"
    tmp = dest.with_name(f".tmp-{job_id}-{os.getpid()}-{secrets.token_hex(4)}")
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        _copytree(src, tmp, ignore=shutil.ignore_patterns(*ARCHIVE_SKIP))
        os.rename(tmp, dest)
        return "archived"
    except Exception as exc:                       # any failure: the caller keeps the worktree, so the evidence is never lost
        shutil.rmtree(tmp, ignore_errors=True)
        return f"failed: {type(exc).__name__}: {exc}"[:300]


def record_merge(home, job_id, state, base, result=None):
    """results/<job_id>/merge.json: the merge outcome (state, base, head, attempts, tail), separate from the execution evidence, written
    atomically. Returns False when it cannot be written (the merge outcome is still in the registry)."""
    folder = Path(home) / "results" / job_id
    try:
        folder.mkdir(parents=True, exist_ok=True)
        tmp = folder / f".merge.json.{os.getpid()}.tmp"
        tmp.write_text(json.dumps({"job_id": job_id, "state": state, "base": base, **(result or {})}, indent=2), encoding="utf-8")
        os.replace(tmp, folder / "merge.json")
        return True
    except OSError:
        return False


class Launcher:
    def __init__(self, repo, api, budget, sandbox_slots=None, wait_s=0.0, poll_s=0.5, sleep=time.sleep):
        """budget() -> {free_requests, review_spend_usd} for today, or None when the ledger has no figures yet.
        sandbox_slots: None (default) reads config/limits.json at each start, so an edit applies to new starts only; a number fixes it.
        wait_s: a claim conflict is retried every poll_s seconds for up to wait_s (queued-claim wait); 0 refuses at once."""
        self.repo = Path(repo)
        self.api = api
        self.budget = budget
        self.sandbox_slots = sandbox_slots
        self.wait_s, self.poll_s, self.sleep = wait_s, poll_s, sleep
        self._slots = {}
        self._keys = {}

    def start(self, name, rel_paths=(), key=None):
        res = self._start(name, rel_paths, key)
        self.api.registry.bump("started" if res["ok"] else "refused:" + res["reason"])
        if res["ok"]:                                      # the job's first event, in the folder its run writes (_loop/<name>)
            event_log.emit(Path(res["worktree"]) / "_loop" / name, "job-start", job_id=res["job_id"], name=name, base=res["base"],
                           branch=res["branch"], gen=res["gen"], key=key or res["job_id"])
        return res

    def _start(self, name, rel_paths=(), key=None):
        home = self.api.home
        recent = [r["state"] for r in self.api.registry.recent(PROVIDER_STREAK)]
        reason = start_refusal(stop_requested(home), self._running(), recent, self.budget())
        if reason:
            return {"ok": False, "reason": reason}
        if key is not None:                                # the request key: a live run under it blocks a second start
            prev = self.api.registry.lookup(key)
            if prev and prev.get("state") == "running" and self.api.lease.held(prev["job"]):
                return {"ok": False, "reason": "request-running", "job": prev["job"]}
        job_id, gen = self._reserve(name)
        wt, branch = home / "wt" / job_id, f"job/{job_id}"
        try:
            git(["fetch", "-q", "origin", "main"], self.repo)
            base = git(["rev-parse", "origin/main"], self.repo).stdout.strip()
            git(["worktree", "add", "-q", "-b", branch, str(wt), base], self.repo)
        except Exception:
            self.api.lease.release(job_id, gen)
            raise
        slots = self.sandbox_slots if self.sandbox_slots is not None else _sandbox_slots()
        slot = self.api.gate.slot("sandbox", gen, timeout_s=0, capacity=slots)
        try:
            slot.__enter__()
        except TimeoutError:                       # the gate raises TimeoutError when no slot frees within timeout_s
            return self._undo(job_id, wt, gen, {"ok": False, "reason": "no-sandbox-slot"})
        taken, conflicts = self._take_claims(job_id, gen, rel_paths)
        if not taken:
            slot.__exit__(None, None, None)
            return self._undo(job_id, wt, gen, {"ok": False, "reason": "claim-conflict", "conflicts": conflicts})
        self._slots[job_id] = slot
        self._keys[job_id] = key or job_id                 # no request key: the row is keyed by job id, as before
        self.api.registry.record(key or job_id, job_id, gen, "running", base, None)
        return {"ok": True, "job_id": job_id, "gen": gen, "worktree": str(wt), "branch": branch,
                "base": base, "job_dir": str(wt / "_loop" / job_id)}

    def _take_claims(self, job_id, gen, rel_paths):
        """Claims are all or nothing, so a retry is safe. Waits up to wait_s for a conflicting job to release."""
        deadline = time.monotonic() + self.wait_s
        waited = False
        while True:
            taken, conflicts = self.api.claims.take(job_id, gen, list(rel_paths))
            if taken or time.monotonic() >= deadline:
                return taken, conflicts
            if not waited:                                     # once per job that waits on a claim, not per poll
                waited = True
                self.api.registry.bump("queue-wait")
            self.sleep(self.poll_s)

    def finish(self, job_id, gen, state, base=None, result=None):
        """Archive the job's evidence (archive_job), release the sandbox slot, the claims and the lease, and record the state. The registry
        row is written before the lease goes. The worktree is removed unless the state keeps it (KEEP_WORKTREE) or the archive failed: then
        the worktree stays so the evidence is not lost, everything else is still released, and the row says archive_failed. The state is
        never changed by the archive. The finish event goes in before the archive (a merge state has its own merge event instead), and
        the job is indexed into history.db after it."""
        if state not in event_log.MERGE_STATES:
            self._finish_event(job_id, state, result)
        archive = archive_job(self.api.home, job_id)
        job_history.safe_index(self.api.home, job_id)
        result = dict(result or {}, archive=archive)
        if archive.startswith("failed"):
            result["archive_failed"] = True
        slot = self._slots.pop(job_id, None)
        if slot is not None:
            slot.__exit__(None, None, None)
        self.api.claims.release(job_id, gen)
        self.api.registry.record(self._keys.pop(job_id, job_id), job_id, gen, state, base, result)
        self.api.lease.release(job_id, gen)
        if state not in KEEP_WORKTREE and not result.get("archive_failed"):
            git(["worktree", "remove", "--force", str(self.api.home / "wt" / job_id)], self.repo, check=False)
        self.api.registry.bump("finished:" + state)
        return state

    def _finish_event(self, job_id, state, result):
        """The finish event, unless the run already wrote one with this state (p1b _finish). The reason is the result's, else the
        status.json reason when that file ends in the same state."""
        folder = event_log.job_dir_for(self.api.home, job_id)
        if folder is None:
            return
        events = event_log.read_events(folder / event_log.FILE)
        if events and events[-1].get("type") == "finish" and events[-1].get("state") == state:
            return
        reason = (result or {}).get("reason")
        if reason is None:
            try:
                status = json.loads((folder / "status.json").read_text(encoding="utf-8"))
                reason = status.get("reason") if status.get("state") == state else None
            except (OSError, ValueError, AttributeError):
                pass
        event_log.emit(folder, "finish", job_id=job_id, state=state, reason=reason)

    def _running(self):
        """Jobs whose latest registry row says running and whose lease is not stale (a crashed job stops counting once its heartbeat is stale)."""
        rows = self.api.registry.recent(RUNNING_SCAN)
        return len({r["job"] for r in rows if r["state"] == "running" and not self.api.lease.stale(r["job"])})

    def _reserve(self, name):
        """Draw an id and take its lease. A live lease with that id (another launcher just took it) means: draw again."""
        home = self.api.home
        while True:
            job_id = new_job_id(name, exists=lambda j: self.api.registry.lookup(j) is not None or (home / "wt" / j).exists())
            try:
                return job_id, self.api.lease.acquire(job_id)
            except loop_home.LeaseHeld:
                continue

    def _undo(self, job_id, wt, gen, refusal):
        self.api.lease.release(job_id, gen)
        git(["worktree", "remove", "--force", str(wt)], self.repo)
        git(["branch", "-D", f"job/{job_id}"], self.repo)
        return refusal
