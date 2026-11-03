# Four-loop test: runbook for the owner's Windows PC

Run this after the two-loop test has merged (`tl-slug`, `tl-roman`, `tl-index` on main). It is the two-loop runbook with four loops: the
commands are the same entry points as `docs/pilot/two-loop/RUNBOOK.md`, which was run successfully on 2026-11-03 (r2). Read section 0 and section 9 of
that runbook for the command-line surface and the known gaps.

The five jobs are `fl-clamp`, `fl-chunk`, `fl-dedupe`, `fl-wordcount` and `fl-top`. Their specs and job files are in this folder, and their locked
tests and controls are under `tests/acceptance/` and `tests/controls/` (files prefixed `fl_`). Each is a production job with budget
`{"free_requests": 40, "review_usd": 0, "paid_usd": 0}`, so it runs only under `SATVIK_PROFILE=test`.

**What this tests beyond two loops.**
- Four jobs run at the same time. That is four sandbox slots (`launch.SANDBOX_SLOTS` = 4), four leases and four claim sets.
- Each model's gate is one call wide, so the writers queue for it: expect `queue-wait` counts.
- The account pace is 15 requests a minute across all four loops.
- The free-request reserve: a fourth start needs `100 + 40 * 3 = 220` free requests left today.
- Four merges are queued at once: each merge re-tests on the moved main.

**Merge order.** `fl-top` imports `fl_wordcount`, so `fl-wordcount` must be merged before `fl-top` starts. The other four are independent.

## 1. Environment (every new PowerShell window)

```powershell
$env:OPENROUTER_FREE_KEY = (Get-Content "D:\aai work\loop_p0\secrets\openrouter_paid_key.txt" -Raw).Trim()
$env:SATVIK_HOME = "D:\aai work\satvik_loop_home"
$env:SATVIK_PROFILE = "test"
$env:PATH = "C:\Program Files\Docker\Docker\resources\bin;" + $env:PATH
Set-Location "<repo root of a fresh worktree of origin/main>"
```

Never print the key. Never touch `secrets\`. Do not run from `D:\aai work\loop_p0`. The sandbox image must be the one built from
`tools/sandbox/Dockerfile` on main (`docker build -t satvik-pytest-sandbox:latest tools/sandbox`).

## 2. Preflight

```powershell
git status --short
git log --oneline -1 origin/main
python -m pytest pieces\test_four_loop_jobs.py -p no:cacheprovider -q
foreach ($j in "fl-clamp","fl-chunk","fl-dedupe","fl-wordcount","fl-top") { python scripts\source\fix_loop.py --dry-run --job docs\pilot\four-loop\$j.json --root . }
```

Expected: an empty `git status`, `14 passed`, and five dry runs that each print `dry-run: prompt size <n> chars`.

## 3. Queue phase 1: the four independent jobs

```powershell
@'
import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / "scripts" / "source"))
import fix_loop, loop_home
home = loop_home.home(loops=4)
for name in ["fl-clamp", "fl-chunk", "fl-dedupe", "fl-wordcount"]:
    print(fix_loop.enqueue(Path("docs/pilot/four-loop") / (name + ".json"), home).name)
'@ | python -
```

## 4. Start four loops in parallel (four windows, or four background processes)

Run the loop from two-loop RUNBOOK section 4 in each of four windows, with `loop_home.home(loops=4)` in place of `loops=2`. Each loop takes the
next job, runs it in its own worktree and drains the merge queue after it. Before a job's merge, copy `wt\<job id>\_loop` for the evidence (it
is removed at the merge).

## 5. Watch

These are the commands from two-loop RUNBOOK section 5, with `loops=4`. While all four run:
- `leases\` shows four live leases.
- `claims.json` shows four disjoint claim sets.
- The counters show `started = 4` and no `refused:claim-conflict`.
- `queue-wait` may grow, because the writers share one gate per model.

## 6. Phase 2 precheck: fl-wordcount must be merged

```powershell
@'
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / "scripts" / "source"))
import loop_home
rows = json.loads((loop_home.home(loops=4) / "registry.json").read_text(encoding="utf-8"))
print({n: rows.get(n, {}).get("state") for n in ["fl-clamp", "fl-chunk", "fl-dedupe", "fl-wordcount"]})
'@ | python -
```

Expected: all four `merged`. Do not continue until `fl-wordcount` is `merged`.

## 7. Phase 2: the stop switch, then fl-top

Queue `fl-top` (`docs/pilot/four-loop/fl-top.json`) and set `SATVIK_HOME\STOP`. Run one loop: it must print `stop switch on` and start nothing.
Then remove `STOP` and run one loop again: `fl-top passed`, then `merged`, then `queue empty`. The commands are the same as in two-loop RUNBOOK section 7.

## 8. Check the result

```powershell
git fetch -q origin main
git log --oneline -12 origin/main
git ls-tree --name-only origin/main scripts/source/ | Select-String "fl_"   # fl_clamp, fl_chunk, fl_dedupe, fl_wordcount, fl_top
Get-Content "$env:SATVIK_HOME\ledger\limits.json"                          # review_spent 0
```

Done when all of these hold:
1. All five jobs are `merged` in the registry, with `fl-wordcount` before `fl-top`.
2. Their five modules are on `origin/main`.
3. Four jobs ran at the same time: four leases at once, and `started` reaches 4 before any job finished.
4. There was no claim conflict.
5. The stop switch was honoured.
6. `review_spent` is 0, and there were no Opus calls (the test roster has none).
