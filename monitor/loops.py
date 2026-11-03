"""One background loop (POST /loops/start spawns these: `python -m monitor.loops` from the repo root). The same loop as
docs/pilot/two-loop/RUNBOOK.md section 4: take the next queued job, run it through the launcher with the stage table frozen when it was
queued, then drain the merge queue. It stops when the queue is empty or STOP is on. Nothing here is a model call of its own."""
import sys
import time
from pathlib import Path

import fix_loop
import launch
import loop_home
import merge_queue


def run(home, repo):
    api = launch.real_api(home)
    launcher = launch.Launcher(repo, api, budget=fix_loop.ledger_budget, wait_s=0)
    mq = merge_queue.MergeQueue(repo, api, launcher)
    while not launch.stop_requested(home):
        path = fix_loop.take_next(home)
        if path is None:
            break
        try:
            job = fix_loop.load_job(path)
        except ValueError as exc:
            print(f"{path.name}: invalid job: {exc}", flush=True)
            continue
        frozen = fix_loop.read_stages(path)               # the stage table frozen at queue time (None for a job queued without one)
        res = fix_loop.run_launched(job, repo, api, launcher, merge_queue=mq, stage_snapshot=frozen)
        print(job["name"], res["state"], res.get("reason"), flush=True)
        while mq.pending:
            try:
                print("merge", mq.drain(), flush=True)
            except loop_home.LockTimeout:
                time.sleep(20)                            # the other loop holds the merge lock: the pending merge is kept
    print("stop switch on" if launch.stop_requested(home) else "queue empty", flush=True)


def main(argv=None):
    repo = Path.cwd().resolve()
    home = loop_home.home(loops=2)
    run(home, repo)
    return 0


if __name__ == "__main__":
    sys.exit(main())
