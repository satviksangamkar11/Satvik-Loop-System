"""Job queue command line (two-loop pilot): enqueue job files, drain the queue through the launcher and the merge queue, print the counters.

Every command works on the loop home SATVIK_HOME. It must be set and lie outside every git worktree (loop_home.home), because two
loops share it. The queue is SATVIK_HOME/queue (fix_loop.queue_dir); a taken job file moves to SATVIK_HOME/queue/taken.

  python scripts/source/loop_queue.py enqueue JOB.json [JOB.json ...]
      Check every file with fix_loop.load_job and refuse any that is not profile=production (as fix_loop does without --legacy).
      If one is refused, nothing is queued (exit 2). Otherwise each is copied into the queue in the order given (fix_loop.enqueue),
      and one line "queued <queue file name>" is printed per file.

  python scripts/source/loop_queue.py drain [--root DIR] [--claim-wait-s S] [--lock-wait-s S]
      Run queued jobs one after another, oldest first, until the queue is empty. For each job:
        1. the stop switch (SATVIK_HOME/STOP) is checked before the take; when it is on, the drain ends (exit 1);
        2. the oldest job file is taken (fix_loop.take_next) and loaded; a file that does not load is "invalid" and the drain goes on;
        3. the job runs in its own worktree and branch (fix_loop.run_launched -> launch.Launcher, budget fix_loop.ledger_budget);
        4. the merge queue is drained (merge_queue.MergeQueue.drain, sandbox suite merge_queue.default_suite).
      A start the launcher refuses (any limit, claim conflict, request running) runs nothing: the job file goes back to the queue
      under its name, so it keeps its place, and the drain ends (exit 3). Start it again once the limit clears.
      A job whose run raises is finished as "error" by run_launched; the traceback goes to stderr and the drain goes on.
      The stop switch does not stop a job in flight between rounds here: run_job checks it before each round and the job ends "stopped"
      with its files restored. A job that already passed is still merged in step 4.
      Prints "job <queue file> <state> [reason]" per job, "merge <job id> <state>" per merge, then the counter lines.
      --root DIR        the repository the worktrees branch from and the merge pushes from (default: the current folder).
      --claim-wait-s S  wait up to S seconds for a conflicting job (another loop) to release a claimed file before the start is
                        refused (launch.Launcher wait_s; default 0: refuse at once).
      --lock-wait-s S   a LockTimeout on the loop home's locks is retried every LOCK_PAUSE_S seconds for up to S seconds in all
                        (default 3600: another loop's merge holds home/merge.lock for its whole suite). It is retried only where a
                        retry is safe: a start before the job's run began, and a merge drain (a merge cut off mid-way is put back at the
                        front of the merge queue while its registry row still says passed). Past the window the drain ends (exit 4)
                        and lists the passed jobs that were not merged; their worktrees stay. A LockTimeout inside a run is an error.

  python scripts/source/loop_queue.py counters
      Print the registry counters, one "name = count" line each, sorted by name (launch.counter_lines).

Exit codes: 0 done (queue empty, every passed job merged or finished), 1 stopped by the stop switch, 2 usage, home or invalid job file,
3 a start was refused (the job is back in the queue), 4 a LockTimeout outlasted --lock-wait-s.
A job's own outcome (tests-failed, merge-conflict, ...) does not change the exit code; the lines and the counters show it.
Stdlib plus the loop's own modules. No model call here: the job's run (fix_loop.run_job) makes them.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import traceback
from pathlib import Path

import fix_loop
import launch
import loop_home
import merge_queue

LOCK_PAUSE_S = 5.0
LOCK_WAIT_S = 3600.0
EXIT_DONE, EXIT_STOPPED, EXIT_USAGE, EXIT_REFUSED, EXIT_LOCK = 0, 1, 2, 3, 4


class GaveUp(Exception):
    """A LockTimeout lasted longer than the retry window."""


def load_production(path):
    """fix_loop.load_job, and the job must be profile=production. Any refusal is a ValueError (a file that is not YAML too)."""
    import yaml
    try:
        job = fix_loop.load_job(path)
    except yaml.YAMLError as exc:
        raise ValueError(f"not a JSON/YAML job file: {exc}") from exc
    if job.get("profile") != "production":
        raise ValueError("not profile=production: the queue runs production jobs only")
    return job


def real_run(root):
    """The production run: fix_loop.run_job in the job's worktree, with the scrub keys fix_loop.main loads (none when unavailable)."""
    try:
        scrub_keys = fix_loop._load_module_by_path("draft_source", Path(root) / "scripts/source/draft_source.py").nt.load_keys()
    except Exception:
        scrub_keys = []

    def run(job, wt, **kwargs):
        return fix_loop.run_job(job, wt, scrub_keys=scrub_keys, **kwargs)
    return run


class Retry:
    """Calls fn again while it raises loop_home.LockTimeout, every pause_s, until wait_s has passed since the first try (GaveUp)."""

    def __init__(self, api, wait_s, pause_s=LOCK_PAUSE_S, sleep=time.sleep, clock=time.monotonic):
        self.api, self.wait_s, self.pause_s, self.sleep, self.clock = api, wait_s, pause_s, sleep, clock

    def __call__(self, fn, may_retry=lambda: True):
        end = self.clock() + self.wait_s
        while True:
            try:
                return fn()
            except loop_home.LockTimeout as exc:
                if not may_retry():
                    raise
                if self.clock() >= end:
                    raise GaveUp(str(exc)) from exc
                try:
                    self.api.registry.bump("lock-retry")
                except loop_home.LockTimeout:
                    pass                                # the count is best effort; the retry is not
                print(f"lock busy, retrying in {self.pause_s:g}s: {exc}", file=sys.stderr)
                self.sleep(self.pause_s)


def drain_merges(mq, api, retry, say):
    """MergeQueue.drain under the retry. A drain cut off after it popped a job: that job goes back to the front when its registry row
    still says passed (the merge did not finish; a retry rebases and tests again), and is reported from its row when it did finish."""
    def once():
        if not mq.pending:                              # nothing passed: do not wait on another loop's merge lock
            return []
        before = list(mq.pending)
        try:
            return mq.drain()
        except loop_home.LockTimeout:
            popped = before[:len(before) - len(mq.pending)]
            back, done = [], []
            for job_id, gen in popped:
                try:
                    row = api.registry.lookup(job_id) or {}
                except loop_home.LockTimeout:
                    row = {}
                state = row.get("state", "passed")
                (back if state == "passed" else done).append(((job_id, gen), state))
            mq.pending[:0] = [item for item, _ in back]
            for (job_id, _), state in done:
                say(f"merge {job_id} {state}")
            raise
    for job_id, state in retry(once):
        say(f"merge {job_id} {state}")


def _put_back(taken: Path, home: Path):
    os.replace(taken, fix_loop.queue_dir(home) / taken.name)


def drain(home, repo, run=None, suite=None, loader=None, claim_wait_s=0.0, lock_wait_s=LOCK_WAIT_S, sleep=time.sleep,
          clock=time.monotonic, say=print) -> int:
    """The drain command. run(job, wt, **kwargs) and suite(wt) default to the production run and the sandbox suite; loader to
    load_production. Returns the exit code."""
    home, repo = Path(home), Path(repo).resolve()
    api = launch.real_api(home)
    launcher = launch.Launcher(repo, api, budget=fix_loop.ledger_budget, wait_s=claim_wait_s, sleep=sleep)
    mq = merge_queue.MergeQueue(repo, api, launcher, run_suite=suite or merge_queue.default_suite)
    run = run or real_run(repo)
    load = loader or load_production
    retry = Retry(api, lock_wait_s, sleep=sleep, clock=clock)
    code = EXIT_DONE
    try:
        while True:
            if launch.stop_requested(home):
                say("stop switch is on: no further job is taken")
                code = EXIT_STOPPED
                break
            path = fix_loop.take_next(home)
            if path is None:
                break
            try:
                job = load(path)
            except (ValueError, OSError) as exc:
                say(f"job {path.name} invalid {exc}")
                continue
            began = []

            def started_run(job_, wt, **kwargs):
                began.append(True)
                return run(job_, wt, **kwargs)

            try:
                res = retry(lambda: fix_loop.run_launched(job, repo, api, launcher, merge_queue=mq, run=started_run),
                            may_retry=lambda: not began)
            except GaveUp:
                _put_back(path, home)                   # the run never began: the job keeps its place
                raise
            except Exception as exc:                    # run_launched already finished the job as "error"
                traceback.print_exc()
                say(f"job {path.name} error {type(exc).__name__}: {exc}")
                res = None
            if res is not None:
                say(" ".join(x for x in ("job", path.name, res["state"], str(res.get("reason") or "")) if x))
                if res["state"] == "refused":
                    _put_back(path, home)
                    code = EXIT_REFUSED
                    break
            drain_merges(mq, api, retry, say)
    except GaveUp as exc:
        say(f"lock timeout outlasted --lock-wait-s: {exc}")
        for job_id, _ in mq.pending:
            say(f"unmerged {job_id} (passed; its worktree stays)")
        code = EXIT_LOCK
    for line in launch.counter_lines(home):
        say(line)
    return code


def main(argv=None, run=None, suite=None, loader=None) -> int:
    parser = argparse.ArgumentParser(prog="loop_queue.py", description="Job queue for the loops (see the module docstring).")
    sub = parser.add_subparsers(dest="command", required=True)
    p_enq = sub.add_parser("enqueue", help="check and queue job files")
    p_enq.add_argument("jobs", nargs="+", metavar="JOB.json")
    p_drain = sub.add_parser("drain", help="run queued jobs until the queue is empty")
    p_drain.add_argument("--root", default=".", help="the repository the worktrees branch from (default: the current folder)")
    p_drain.add_argument("--claim-wait-s", type=float, default=0.0, help="seconds to wait for a claimed file (default 0)")
    p_drain.add_argument("--lock-wait-s", type=float, default=LOCK_WAIT_S, help=f"seconds a LockTimeout is retried (default {LOCK_WAIT_S:g})")
    sub.add_parser("counters", help="print the registry counters")
    args = parser.parse_args(argv)
    try:
        home = loop_home.home(loops=2)                  # shared by two loops: SATVIK_HOME is required
    except loop_home.LoopHomeError as exc:
        print(exc, file=sys.stderr)
        return EXIT_USAGE

    if args.command == "counters":
        for line in launch.counter_lines(home):
            print(line)
        return EXIT_DONE
    if args.command == "enqueue":
        load = loader or load_production
        problems = []
        for job in args.jobs:
            try:
                load(job)
            except (ValueError, OSError) as exc:
                problems.append(f"{job}: {exc}")
        if problems:
            print("nothing queued:\n  " + "\n  ".join(problems), file=sys.stderr)
            return EXIT_USAGE
        for job in args.jobs:
            print(f"queued {fix_loop.enqueue(Path(job), home).name}")
        return EXIT_DONE
    return drain(home, args.root, run=run, suite=suite, loader=loader, claim_wait_s=args.claim_wait_s, lock_wait_s=args.lock_wait_s)


if __name__ == "__main__":
    raise SystemExit(main())
