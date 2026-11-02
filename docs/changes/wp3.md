# WP3: launcher and merge queue (Revision 4)

## What is in it
- `scripts/source/launch.py`: job ids, one worktree and branch per job, start limits, stop switch.
- `scripts/source/merge_queue.py`: one job at a time into `main`, under the merge lease.
- `pieces/test_wp3_launch.py`, `pieces/test_wp3_merge_queue.py`: 29 tests. `pieces/wp3_fakes.py` holds the in-memory WP2 stand-in and temp-git helpers.

## Behaviour
- Job id: `<name>-<YYYYMMDD-HHMM>-<4 hex>`. A drawn id that is already taken (registry row or worktree folder) is drawn again.
- Start: limits are checked first (free requests >= 100 + 40 per running job; review spend < $0.50; not the last 3 jobs all `provider-unavailable`; `home/STOP` absent). Then `git fetch origin main`, `git worktree add -b job/<id> home/wt/<id> origin/main`, lease, sandbox slot (`gate.slot("sandbox", gen, timeout_s=0)`), claims, registry row `running`. A refusal at any step removes the worktree and branch again.
- Stop switch: `run_rounds` checks `home/STOP` before every round. When set, it calls `restore()` and returns `stopped`, so the call in flight finishes first.
- Merge: under lease `merge`, per job: fetch, rebase onto `origin/main` (conflict: `merge-conflict`, rebase aborted, worktree kept), suite (acceptance files, then `pieces`), push `HEAD:main` only if `origin/main` is still the tested commit. If main moved, repeat from the rebase, at most 3 repeats (4 tries in all, then `main-moved`). A suite failure ends `tests-failed`. Success: `merged`, worktree removed, claims and lease released.

## Interpretations to confirm
- "At most 3 times" read as 3 repeats after the first try.
- A set `home/STOP` also blocks new starts, not only running loops.
- Budget figures and recent job states come from a `snapshot()` callable supplied by the caller. The WP2 interface has no way to list recent registry rows.
- `fenced_write` is not used: `registry.record` already carries the generation.

## Assumed WP2 contract (not yet checked against the real module)
- `gate.slot(...)` raises `TimeoutError` when no slot is free within `timeout_s`.
- `lease.acquire(key)` raises when a fresh lease is held; `lease.beat` raises once the lease is lost.
- `claims.take` returns `(False, conflicts)` and takes nothing on conflict.
- `registry.record` upserts by key.
- The merge lock's file name (`home/merge.lock`) depends on how WP2 keys leases; the merge code uses the key `merge`.

## Not verified
- The real WP2 module (not merged yet). All tests use the fake.
- `default_suite` (Docker `DockerSandbox`) is not run by any test; the tests inject a fake suite.
- Nothing calls `Launcher` or `MergeQueue` from a loop yet; the loop wiring is for the coordinator.
