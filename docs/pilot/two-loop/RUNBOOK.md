# Two-loop test (closure step 6): runbook for the owner's Windows PC

Run this only after the pilot passes (pilot-loop-status `passed`, 2 READY, matching hashes, $0, 0 Opus calls) and loop_status.py is on main.

The three jobs are `tl-slug`, `tl-roman` and `tl-index`. Their specs, locked tests and controls are in this folder, under `tests/acceptance/` and `tests/controls/` (files prefixed `tl_`). Each job is a production job with `budget` `{"free_requests": 40, "review_usd": 0, "paid_usd": 0}`, so it runs only under `SATVIK_PROFILE=test`. Under `production` every one of them is refused by `load_job` (the review budget must be 0.05 there).

**Merge order.** `tl-index` imports `tl_slug`, so `tl-slug` must be merged before `tl-index` starts. `tl-roman` is independent. The sections below run `tl-slug` and `tl-roman` in parallel, then `tl-index` alone.

## 0. Read first: blocker and what the command line can do

**Blocker found while preparing this runbook: fixed (commit 9160bdf).** A passed job's new files used to stay uncommitted: the only commit was the change doc (`fix_loop.write_change_doc`), so `merge_queue` pushed no code. Now a passed job's own files go into the same commit as its change doc (`pieces/test_fix_loop_wiring.py::test_step7_a_passed_job_commits_its_own_files_with_the_change_doc`). Section 8 item 2 checks it on the real run.

**Command-line surface, checked against the code (no flag below is invented):**

| Need | Real entry point | Status |
|---|---|---|
| Test the three jobs | `python -m pytest pieces\test_two_loop_jobs.py -p no:cacheprovider -q` | Real. No model call. |
| Validate one job file | `python scripts\source\fix_loop.py --dry-run --job <job.json> --root .` | Real. Runs `load_job` and builds the prompt. No model call. |
| One job, no launcher | `python scripts\source\fix_loop.py --solo --job <job.json> --root .` | Real. No claims, no ledger, no worktree, no merge. Not used for the two-loop test. |
| Two loops with file claims | `python pieces\p1_claims.py run <job.json> .` | Real. Claims and the shared key ledger, but both loops write into the same checkout and nothing is merged. Not used for the two-loop test. |
| Start jobs through the launcher | none | `launch.py` has no command line (no argparse, no `__main__`). The launcher is reached only through `fix_loop.run_launched`, so sections 4 and 7 use Python here-strings. |
| Queue the jobs | none | `fix_loop.enqueue` and `fix_loop.take_next` are Python functions. Sections 3 and 7 use here-strings. |
| Merge | none | `merge_queue.py` has no command line. `MergeQueue.drain()` runs inside the loop. |
| Stop switch | a file | `SATVIK_HOME\STOP`. Honoured by the launcher path only: checked before each start and before each round of `run_launched`. `--solo` and `p1_claims.py` do not check it. |
| Counters | none | `launch.counter_lines(home)` is Python. `loop_status.py` (pilot step 5) is not on main, so it cannot print them either. |
| Claims, leases, registry, shared ledger | JSON files | Read with `Get-Content` (section 5). |
| `--request` / `--approve` | `fix_loop.py` | Not usable: the planner is `fake` under the test profile, so planning is refused. |

Known gaps are listed in section 9. The here-strings were rehearsed in a scratch repo with a fake model and a fake Docker suite, which exercised the queue, launcher, claims, merge and stop switch. The real model calls and the Docker suite were not run.

## 1. Environment (every new PowerShell window)

```powershell
$env:OPENROUTER_FREE_KEY = (Get-Content "D:\aai work\loop_p0\secrets\openrouter_paid_key.txt" -Raw).Trim()
$env:SATVIK_HOME = "D:\aai work\satvik_loop_home"
$env:SATVIK_PROFILE = "test"
$env:PATH = "C:\Program Files\Docker\Docker\resources\bin;" + $env:PATH
```

Check the key file name before the run. Its name says `paid`, but its value goes into `OPENROUTER_FREE_KEY`. Confirm it holds the free key. Never print the key, never copy it, and never touch `secrets\`.

Run every command from the repo root of a fresh worktree of `origin/main` (after this branch and the blocker fix are merged). Do not run from `D:\aai work\loop_p0`. Never put `SATVIK_HOME` inside a worktree (`loop_home` refuses that).

```powershell
Set-Location "<repo root of the fresh worktree>"
```

## 2. Preflight

```powershell
git status --short
git log --oneline -1 origin/main
python -m pytest pieces\test_two_loop_jobs.py -p no:cacheprovider -q
python scripts\source\fix_loop.py --dry-run --job docs\pilot\two-loop\tl-slug.json --root .
python scripts\source\fix_loop.py --dry-run --job docs\pilot\two-loop\tl-roman.json --root .
python scripts\source\fix_loop.py --dry-run --job docs\pilot\two-loop\tl-index.json --root .
```

Expected: `git status` is empty. Pytest reports `11 passed`. Each dry run prints `dry-run: prompt size <n> chars` and exits 0.

## 3. Queue phase 1: tl-slug and tl-roman

`tl-index` is not queued yet (section 7).

```powershell
@'
import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / "scripts" / "source"))
import fix_loop, loop_home
home = loop_home.home(loops=2)
for name in ["tl-slug", "tl-roman"]:
    print(fix_loop.enqueue(Path("docs/pilot/two-loop") / (name + ".json"), home).name)
'@ | python -
```

Expected: two file names printed, one per job, now in `SATVIK_HOME\queue`.

## 4. Start two loops in parallel (one PowerShell window each)

In each of two windows: run the environment block (section 1), `Set-Location` to the repo root, then run the same loop. The loop takes the next queued job, runs it in its own worktree through the launcher (claims, sandbox slot, lease), and drains the merge queue after each job. It stops when the queue is empty or the stop switch is on.

```powershell
@'
import sys, time
from pathlib import Path
repo = Path.cwd()
sys.path.insert(0, str(repo / "scripts" / "source"))
import fix_loop, launch, loop_home, merge_queue
home = loop_home.home(loops=2)
api = launch.real_api(home)
launcher = launch.Launcher(repo, api, budget=fix_loop.ledger_budget, wait_s=0)
mq = merge_queue.MergeQueue(repo, api, launcher)
while not launch.stop_requested(home):
    path = fix_loop.take_next(home)
    if path is None:
        break
    job = fix_loop.load_job(path)
    res = fix_loop.run_launched(job, repo, api, launcher, merge_queue=mq)
    print(job["name"], res["state"], res.get("reason"), flush=True)
    while mq.pending:
        try:
            print("merge", mq.drain(), flush=True)
        except loop_home.LockTimeout:
            time.sleep(20)
print("stop switch on" if launch.stop_requested(home) else "queue empty", flush=True)
print("\n".join(launch.counter_lines(home)), flush=True)
'@ | python -
```

Expected: each window prints a line per job (`tl-slug passed None`, then `merge [(<job id>, 'merged')]`). Both loops run at the same time. `tl-slug` and `tl-roman` touch different files, so their claims never conflict. Each window ends with `queue empty` and the counter lines (`started = 2`, `finished:merged = 2` in total).

A job that prints `refused` was not run. Its queue file moved to `SATVIK_HOME\queue\taken`. Copy it back into `SATVIK_HOME\queue` and run the loop again.

`LockTimeout` means the other loop has held the merge lock for 30 s. The loop waits and drains again. The pending merge is kept, because `drain()` raises before it removes anything.

## 5. Watch the counters, claims and shared ledger

Run these from any window while the loops run:

```powershell
Get-ChildItem "$env:SATVIK_HOME\queue" -Filter *.json          # jobs still waiting
Get-ChildItem "$env:SATVIK_HOME\leases"                         # one lease per running job: job_id, pid, heartbeat, generation, released
Get-Content "$env:SATVIK_HOME\claims.json"                      # per repo-relative file: job, gen, mode ("w"); each running job holds only its own files
Get-Content "$env:SATVIK_HOME\registry.json"                    # latest row per job name and per job id, plus counter:* rows
Get-Content "$env:SATVIK_HOME\ledger\limits.json"               # shared ledger: day, free_used, review_spent (millionths of a USD), minute
```

The job id is in `registry.json`. A running job's status is in its worktree:

```powershell
Get-Content "$env:SATVIK_HOME\wt\<job id>\_loop\<job name>\status.json"
```

`ledger\limits.json` does not exist until the first model call of the run.

Counters, in the same form the loops print them:

```powershell
@'
import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / "scripts" / "source"))
import launch, loop_home
print("\n".join(launch.counter_lines(loop_home.home(loops=2))))
'@ | python -
```

Expected while running: `started` counts the jobs begun, and `finished:passed` plus `finished:merged` count the jobs that have finished. No `refused:claim-conflict` line appears, because the three jobs' files are disjoint.

## 6. Phase 2 precheck: tl-slug must be merged

Do not queue `tl-index` until `tl-slug` shows `merged`:

```powershell
@'
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / "scripts" / "source"))
import loop_home
rows = json.loads((loop_home.home(loops=2) / "registry.json").read_text(encoding="utf-8"))
print({name: rows.get(name, {}).get("state") for name in ["tl-slug", "tl-roman"]})
'@ | python -
```

Expected: `{'tl-slug': 'merged', 'tl-roman': 'merged'}`. If `tl-slug` shows `passed` or no state, wait for its loop to drain. Do not continue on `passed` alone: the merge is what puts `tl_slug.py` on `origin/main`.

## 7. Phase 2: test the stop switch, then tl-index

Queue `tl-index` with the stop switch on. The loop checks the switch before it takes a job and before each start, so it must start nothing:

```powershell
@'
import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / "scripts" / "source"))
import fix_loop, loop_home
home = loop_home.home(loops=2)
print(fix_loop.enqueue(Path("docs/pilot/two-loop/tl-index.json"), home).name)
'@ | python -
New-Item -ItemType File -Force "$env:SATVIK_HOME\STOP" | Out-Null
```

Run the loop from section 4 in one window. Expected: it prints `stop switch on`, `started` does not change, and `SATVIK_HOME\queue` still holds the `tl-index` file.

Clear the switch and run the loop again (section 4, one window). Expected: `tl-index passed None`, then `merge [(<job id>, 'merged')]`, then `queue empty`.

Optional, not required: set the switch while a job is running in phase 1. The running job stops before its next round, its files are restored and its worktree is removed. It does not merge and stays in `queue\taken`. Copy the file back to `queue` to run it again.

## 8. Check the result

Expected end state: three jobs merged, `tl-slug` before `tl-index`. `tl-roman` may merge before or after `tl-slug`, because the two loops drain in parallel.

```powershell
Get-Content "$env:SATVIK_HOME\registry.json"                    # tl-slug, tl-roman, tl-index: state "merged"; seq shows the order
git fetch -q origin main
git log --oneline -8 origin/main                                # three "change doc: <job id> (passed)" commits
git ls-tree --name-only origin/main scripts/source/ | Select-String "tl_"   # must list tl_slug.py, tl_roman.py, tl_index.py
Select-String -Path config\roster.json -Pattern '"model": "opus"'  # the matches must all sit inside the "production" block
Get-Content "$env:SATVIK_HOME\ledger\limits.json"               # review_spent must be 0 (millionths of a USD)
```

Done when all of these hold:

1. The three jobs are `merged` in the registry, and `tl-slug` merged before `tl-index`.
2. `origin/main` contains `scripts/source/tl_slug.py`, `scripts/source/tl_roman.py` and `scripts/source/tl_index.py`. (This failed before commit 9160bdf; it must pass now.)
3. `review_spent` is 0. The test roster has no paid role (`load_profile` refuses one under the test profile).
4. No Opus call. The test roster has none: `repair` is `off` and `planner` is `fake`, so the `"opus"` matches in `config\roster.json` must all be in the `production` block. The per-call history lives in each worktree, and a worktree is removed when its job merges. To keep that evidence, copy `wt\<job id>\_loop` while the job is `passed`, before its merge.

The owner's "$0, 0 Opus calls" check is items 3 and 4.

## 9. Known gaps and limits

1. **Fixed:** candidate files were not committed (section 0); fixed in 9160bdf.
2. No command line for the launcher, queue, merge queue or counters. The here-strings call the real functions. A follow-up task is queued to add a small command line, with tests.
3. `tl-index` depends on `tl-slug` on main. The launcher claims only writes, not the `reference` read, so the order is enforced by this runbook, not by claims.
4. Merges are in-memory per process (`MergeQueue.pending`). If a loop process dies after a job passes and before its merge, the job stays `passed` in the registry and no command re-submits it. Re-run the job from its queue file (copy it back from `queue\taken`).
5. The merge check (`merge_queue.default_suite`) runs `pieces` only. It does not run `tests/acceptance/`. The job's own run does run its acceptance tests, in the Docker sandbox.
6. `LockTimeout` after 30 s when two loops merge at once. Section 4 handles it.
7. A start refused by the stop switch or the budget removes the job file from `queue` (it sits in `queue\taken`). Copy it back to re-run.
8. The `p1_claims.py` route is not used: no worktree, no branch, no merge, and it ignores the stop switch.
9. `ledger\limits.json` appears only after the first model call of the run.
