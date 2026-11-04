# Production smoke run (ps-duration): runbook for the owner's Windows PC

One small production job, run for real on the production profile: `ps-duration` (`docs/pilot/prod-smoke/ps-duration.job.json`). It asks the
loop to write `scripts/prod_smoke/duration_text.py`, a pure-Python `format_duration(seconds)` ("1h 02m 05s").

**What runs, from `config/roster.json` (production block), checked by `stage_config.validate({}, "production")`:**

| Role | Model | Billing |
|---|---|---|
| writer_a (Dots) | `dots-studio/dots-3-note-preview:free` | free |
| writer_b (Xing) | `xingchen-agi/xing4.0-29b-a4b:free` | free |
| reviewer_b (North) | `nvidia/nemotron-3-ultra-550b-a55b:free` | free |
| reviewer_a (GLM) | `z-ai/glm-5.3-flash`, pinned to Z.AI | paid, capped at the job's `review_usd` 0.05 |
| repair | Opus (Claude Code) | **OFF**: `opus_repair.OPUS_ENABLED` is `False` |
| planner | Opus (Claude Code) | **not used**: this runbook queues a job file and never calls `--request` |

The job's budget is `{"free_requests": 40, "review_usd": 0.05, "paid_usd": 0}`. The job is refused under `SATVIK_PROFILE=test` (its budget is
0 there), so the run uses `SATVIK_PROFILE=production` only.

**Two things that stay as they are, and why.** `config/roster.json` still names Opus for `repair` and `planner`, because that is the owner's
production roster and config is not edited by a loop job. Opus is kept from running by two things: the repair step checks `OPUS_ENABLED`
(section 2 confirms it is `False`), and a job-file run never reaches the planner. Do not switch `OPUS_ENABLED` on for this run.

**Merge prerequisite.** The job files live on branch `prod-smoke` until they are merged. Merge that branch to `origin/main` (a reviewed PR) first.
The loop runs in a fresh worktree of `origin/main`, and a passed job's code is merged to `origin/main` by the loop itself.

## 0. One-time setup

```powershell
# Python dependencies for the loop and its tests (monitor/requirements.txt alone does not include pyyaml, jsonschema or pytest)
python -m pip install -r monitor\requirements.txt pytest pyyaml jsonschema

# Docker Desktop must be running first. Then build the sandbox image from main's tools\sandbox (run from the fresh worktree, section 1)
docker info
docker build -t satvik-pytest-sandbox:latest tools\sandbox
```

`docker info` must print a server version. The sandbox image is the one built from `tools/sandbox/Dockerfile`; the loop's `selftest` refuses
any other state (`SANDBOX UNAVAILABLE`, no model call).

## 1. Fresh worktree of origin/main

Run from the main checkout of the repository. Keep the worktree and `SATVIK_HOME` apart: `SATVIK_HOME` must be outside every worktree.

```powershell
git fetch origin main
git worktree add --detach "D:\aai work\prod-smoke-wt" origin/main
Set-Location "D:\aai work\prod-smoke-wt"
git status --short                 # must be empty
git log --oneline -1 origin/main   # the commit the run is made from; record it
```

## 2. Environment (every new PowerShell window)

```powershell
$env:OPENROUTER_FREE_KEY = (Get-Content "D:\aai work\loop_p0\secrets\openrouter_paid_key.txt" -Raw).Trim()
$env:SATVIK_HOME = "D:\aai work\satvik_loop_home_prod"
$env:SATVIK_PROFILE = "production"
$env:PATH = "C:\Program Files\Docker\Docker\resources\bin;" + $env:PATH
Set-Location "D:\aai work\prod-smoke-wt"
```

Never print the key. Never copy it, and never touch `secrets\`. The key file's name says `paid`, but the loader reads one key for both slots
(`openrouter_call.load_keys`): with only `OPENROUTER_FREE_KEY` set, that key serves the free writers and reviewer_b, and also the paid GLM review.
Before the run, confirm that this key belongs to the account you intend to bill the $0.05 review cap to.

Confirm Opus is off, from the fresh worktree:

```powershell
@'
import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / "scripts" / "source"))
import opus_repair
print("OPUS_ENABLED", opus_repair.OPUS_ENABLED)
'@ | python -
```

Expected: `OPUS_ENABLED False`. If it prints `True`, stop: do not run.

## 3. Preflight (no model call)

```powershell
git status --short                                                       # empty
python -m pytest pieces\test_prod_smoke_job.py -p no:cacheprovider -q    # 10 passed
python scripts\source\fix_loop.py --dry-run --job docs\pilot\prod-smoke\ps-duration.job.json --root .   # dry-run: prompt size <n> chars, exit 0
@'
import sys
sys.path.insert(0, "scripts/source")
import fix_loop, stage_config
print("stage_config.validate:", stage_config.validate({}, "production"))   # []
'@ | python -
```

Expected: `git status` is empty, pytest reports `10 passed`, the dry run prints `dry-run: prompt size <n> chars` and exits 0, and the stage check
prints `[]`. Do not continue on any other answer.

## 4. Queue the job

Use `loops=1`. `SATVIK_HOME` is created on first use.

```powershell
@'
import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / "scripts" / "source"))
import fix_loop, loop_home
home = loop_home.home(loops=1)
print(fix_loop.enqueue(Path("docs/pilot/prod-smoke/ps-duration.job.json"), home).name)
'@ | python -
```

Expected: one file name, now in `SATVIK_HOME\queue`.

## 5. Run one loop

Start the loop in one window. It takes the queued job, runs it in its own worktree through the launcher (claims, sandbox slot, lease), drains the
merge queue after the job, and stops when the queue is empty or the stop switch is on. It makes model calls: this is the real run.

```powershell
@'
import sys, time
from pathlib import Path
repo = Path.cwd()
sys.path.insert(0, str(repo / "scripts" / "source"))
import fix_loop, launch, loop_home, merge_queue
home = loop_home.home(loops=1)
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

Expected on success: `ps-duration passed None`, then `merge [(<job id>, 'merged')]`, then `queue empty` and the counter lines.

Other outcomes, and what to do with each:
- `escalate` with `Opus repair unavailable`: a writer failed twice and the repair step is off. This is the expected safe stop. Do not switch Opus on. Record the state and read section 7.
- `refused` or `job-too-large`, `acceptance-invalid`, `sandbox-unavailable`: nothing was merged. The job file sits in `SATVIK_HOME\queue\taken`. Read the reason in `SATVIK_HOME\results\<job id>` before any rerun.
- Stop switch: create `SATVIK_HOME\STOP` to stop the loop before its next start. Remove it to run again.

## 6. Watch (from another window)

```powershell
Get-ChildItem "$env:SATVIK_HOME\queue" -Filter *.json                  # empty once the job is taken
Get-ChildItem "$env:SATVIK_HOME\leases"                                 # one lease while the job runs
Get-Content "$env:SATVIK_HOME\registry.json"                            # state per job name and per job id
Get-Content "$env:SATVIK_HOME\ledger\limits.json"                       # review_spent in millionths of a USD; appears after the first call
```

A running job's status is in its worktree: `Get-Content "$env:SATVIK_HOME\wt\<job id>\_loop\ps-duration\status.json"`.

## 7. Read the results

Results are archived under `SATVIK_HOME\results\<job id>\`. Its folders hold the event log and the merge record, and the per-call history of the
run. Find the job id in `registry.json` first.

```powershell
$job = "<job id from registry.json>"
Get-ChildItem "$env:SATVIK_HOME\results\$job" -Recurse -File | Select-Object FullName, Length
Get-Content "$env:SATVIK_HOME\results\$job\merge.json" -ErrorAction SilentlyContinue
Get-Content "$env:SATVIK_HOME\ledger\limits.json"
```

The per-call history shows, for each call, the model that answered. Confirm that no Opus call appears:

```powershell
Get-ChildItem "$env:SATVIK_HOME\results\$job" -Recurse -File | Select-String -Pattern "opus" -SimpleMatch
```

Expected: no match. A match is not a failure by itself, but read it before going on.

## 8. Secret scan (required before anything is copied or pushed)

```powershell
Get-ChildItem "$env:SATVIK_HOME\results" -Recurse -File | Select-String -Pattern "sk-or","Bearer" -SimpleMatch
```

Expected: no output. If there is any match, stop. Do not copy, commit or push anything. Tell the owner, who decides what to delete.

## 9. Check the result on main

```powershell
git fetch -q origin main
git log --oneline -5 origin/main
git ls-tree --name-only origin/main scripts/prod_smoke/                 # duration_text.py
Get-Content "$env:SATVIK_HOME\ledger\limits.json"                        # review_spent must be <= 50000 (= 0.05 USD)
```

## 10. Evidence branch

Only after section 8 is clean. The evidence goes to a new branch, not to main.

```powershell
$run = "docs\pilot\runs\2026-11-04-prod-smoke"
New-Item -ItemType Directory -Force $run | Out-Null
Copy-Item "$env:SATVIK_HOME\results\$job\*" $run -Recurse
Get-ChildItem $run -Recurse -File | Select-String -Pattern "sk-or","Bearer" -SimpleMatch     # must print nothing
git checkout -b prod-smoke-run-2026-11-04 origin/main
git add docs\pilot\runs\2026-11-04-prod-smoke
git commit -m "prod-smoke run 2026-11-04: ps-duration evidence"
git push -u origin prod-smoke-run-2026-11-04
```

## 11. Done when

1. `ps-duration` is `merged` in `registry.json`, or it stopped at a named safe state (section 5) with its reason recorded.
2. `origin/main` has `scripts/prod_smoke/duration_text.py` (when merged).
3. `review_spent` is at most 50000 (0.05 USD). The GLM review was the only paid call.
4. No Opus call appears in the results (section 7), and `OPUS_ENABLED` was `False` throughout (section 2).
5. The secret scan (section 8) found nothing, before and after the copy.

## 12. Not used in this run

- `fix_loop.py --request` and `--approve` (the planner: Opus through Claude Code, not gated by `OPUS_ENABLED`).
- `p1_claims.py run` (no worktree, no merge; it ignores the stop switch).
- Any edit to `config\`, `monitor\`, `tools\sandbox\` or `scripts\source\*.py` by hand. The job writes only its own two files.
