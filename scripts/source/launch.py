"""WP3 launcher (Revision 4): unique job ids, one git worktree and branch per job, start limits, stop switch.

Start: check the limits, draw a job id, fetch origin/main, `git worktree add -b job/<id> home/wt/<id> origin/main`, then take the
lease, the sandbox slot and the claims. A refused start leaves no worktree and no branch behind.
Finish: `Launcher.finish` releases the slot, the claims and the lease and records the state. The worktree is kept or removed by the caller.

`api` is the WP2 interface (home, lease, claims, registry, gate). Tests pass an in-memory fake.
"""
from __future__ import annotations

import re
import secrets
import subprocess
import time
from pathlib import Path

NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")
RESERVE_BASE = 100            # free daily requests kept back, plus RESERVE_PER_JOB for each running job
RESERVE_PER_JOB = 40
REVIEW_SPEND_CAP_USD = 0.50
PROVIDER_STREAK = 3           # the last 3 jobs all provider-unavailable blocks the start


def git(args, cwd, check=True):
    """Run git in cwd. Returns the CompletedProcess (stdout and stderr captured)."""
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=check)


def new_job_id(name, exists=lambda job_id: False, now=None):
    """<name>-<YYYYMMDD-HHMM>-<4 hex>. A drawn id that `exists` says is taken is drawn again."""
    if not NAME_RE.match(name):
        raise ValueError(f"bad job name: {name!r}")
    stamp = time.strftime("%Y%m%d-%H%M", time.localtime(time.time() if now is None else now))
    while True:
        job_id = f"{name}-{stamp}-{secrets.token_hex(2)}"
        if not exists(job_id):
            return job_id


def start_refusal(free_requests, running, review_spend_usd, recent_states, stop_present):
    """The first start limit that blocks, or None. The sandbox slot is checked by Launcher.start."""
    if stop_present:
        return "stop-file"
    if free_requests < RESERVE_BASE + RESERVE_PER_JOB * running:
        return "free-requests"
    if review_spend_usd >= REVIEW_SPEND_CAP_USD:
        return "review-spend"
    last = list(recent_states)[-PROVIDER_STREAK:]
    if len(last) == PROVIDER_STREAK and all(s == "provider-unavailable" for s in last):
        return "provider-unavailable"
    return None


def stop_requested(home_dir):
    return (Path(home_dir) / "STOP").exists()


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


class Launcher:
    def __init__(self, repo, api, snapshot):
        """snapshot() -> {free_requests, running, review_spend_usd, recent_states}: today's figures, read by the caller from the ledger."""
        self.repo = Path(repo)
        self.api = api
        self.snapshot = snapshot
        self._slots = {}

    def start(self, name, rel_paths=()):
        home = Path(self.api.home())
        reason = start_refusal(stop_present=stop_requested(home), **self.snapshot())
        if reason:
            return {"ok": False, "reason": reason}
        job_id = new_job_id(name, exists=lambda j: self.api.registry.lookup(j) is not None or (home / "wt" / j).exists())
        wt, branch = home / "wt" / job_id, f"job/{job_id}"
        git(["fetch", "-q", "origin", "main"], self.repo)
        base = git(["rev-parse", "origin/main"], self.repo).stdout.strip()
        git(["worktree", "add", "-q", "-b", branch, str(wt), base], self.repo)
        gen = self.api.lease.acquire(job_id)
        slot = self.api.gate.slot("sandbox", gen, timeout_s=0)
        try:
            slot.__enter__()
        except TimeoutError:                       # the gate raises TimeoutError when no slot is free within timeout_s
            return self._undo(job_id, wt, gen, {"ok": False, "reason": "no-sandbox-slot"})
        taken, conflicts = self.api.claims.take(job_id, gen, list(rel_paths))
        if not taken:
            slot.__exit__(None, None, None)
            return self._undo(job_id, wt, gen, {"ok": False, "reason": "claim-conflict", "conflicts": conflicts})
        self._slots[job_id] = slot
        self.api.registry.record(job_id, job_id, gen, "running", base, None)
        return {"ok": True, "job_id": job_id, "gen": gen, "worktree": str(wt), "branch": branch,
                "base": base, "job_dir": str(wt / "_loop" / job_id)}

    def finish(self, job_id, gen, state, base=None, result=None):
        """Release the sandbox slot, the claims and the lease, and record the state. The registry row is written before the lease goes."""
        slot = self._slots.pop(job_id, None)
        if slot is not None:
            slot.__exit__(None, None, None)
        self.api.claims.release(job_id, gen)
        self.api.registry.record(job_id, job_id, gen, state, base, result)
        self.api.lease.release(job_id, gen)
        return state

    def _undo(self, job_id, wt, gen, refusal):
        self.api.lease.release(job_id, gen)
        git(["worktree", "remove", "--force", str(wt)], self.repo)
        git(["branch", "-D", f"job/{job_id}"], self.repo)
        return refusal
