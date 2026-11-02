# WP3: launcher and merge queue (Revision 4)

## What is in it
- `scripts/source/launch.py`: job ids, one worktree and branch per job, start limits, stop switch. Uses WP2's `loop_home` (lease, claims, registry, gate) through `real_api(home)`.
- `scripts/source/merge_queue.py`: one job at a time into `main`, under `home/merge.lock` and the lease `merge`.
- `pieces/test_wp3_launch.py`, `pieces/test_wp3_merge_queue.py`: 29 tests on the real `loop_home`, against temp git repos. `pieces/wp3_repos.py` holds the git and lease-file helpers.

## Behaviour
- Job id: `<name>-<YYYYMMDD-HHMM>-<4 hex>`. A drawn id is drawn again when the registry or a worktree folder has it, or when its lease is live (`LeaseHeld`).
- Start, in this order:
  1. Refusals: `stop-file` (`home/STOP` exists); `provider-unavailable` (the newest 3 registry rows, from `registry.recent(3)`, are all `provider-unavailable`); then the budget.
  2. Budget: with figures, free requests must be >= 100 + 40 per running job, and review spend < $0.50. With no figures (`budget()` returns None), at most one job runs at a time (`no-budget-snapshot`).
  3. Running jobs are counted from `registry.recent`: rows with state `running` and a lease that is not stale.
  4. Then `git fetch origin main` and `git worktree add -b job/<id> home/wt/<id> origin/main`, the lease, the sandbox slot (`gate.slot("sandbox", gen, timeout_s=0, capacity=SANDBOX_SLOTS)`, which refuses with `no-sandbox-slot`), claims (`claim-conflict`), and the registry row `running`.
  A refusal after the worktree exists removes the worktree, the branch and the lease again.
- Stop switch: `run_rounds` checks `home/STOP` before every round. When set, it calls `restore()` and returns `stopped`, so the call in flight finishes first. Each round beats the job's lease.
- Merge: under `home/merge.lock` and the lease `merge`, per job: fetch, rebase onto `origin/main` (conflict: `merge-conflict`, rebase aborted, worktree kept), run the suite (acceptance files, then `pieces`), and push `HEAD:main` only if `origin/main` is still the tested commit. If main moved, test again from the new main. 3 tries in all (the first try and 2 retries), then `main-moved`. A suite failure ends `tests-failed`. Success: `merged`, worktree removed, claims and leases released.
- `Launcher.finish` releases the sandbox slot, the claims and the lease, and records the state. The registry row is written before the lease goes.

## Decisions to confirm
- `SANDBOX_SLOTS = 2`: the number of jobs that may hold a sandbox slot at once. Chosen here; WP2's gate default is 1.
- The budget is passed as `budget()`, returning `{free_requests, review_spend_usd}` or None. The ledger that produces it is not built yet (WP4).
- `loop_home.home()` is called by the caller, not by the launcher. Production should use `home(loops=2, worktrees=[repo])`, so `SATVIK_HOME` must be set.
- `fenced_write` is not used: `registry.record` already fences by generation.
- `default_suite` (Docker) is not run by any test; the tests inject a fake suite.

## Not verified
- No loop calls `Launcher` or `MergeQueue` yet.
- Stale merge leases: a crashed merger keeps `leases/merge.json` live for up to 5 minutes (WP2 takeover rule) before a new merge can start.
