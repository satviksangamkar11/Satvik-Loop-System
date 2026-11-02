# WP2: shared loop home, leases, fencing, claims, registry

Branch `wp2-loop-home` from `origin/main` (ff5a289). Files: `scripts/source/loop_home.py` (new), `pieces/p1_claims.py`, `pieces/test_loop_home.py` (new), `pieces/test_p1_claims.py`, this file. No model call, no network.

## What it does

All state two loops could share lives in one folder, the loop home (`SATVIK_HOME`, or a single loop's `<repo>/_loop` fallback):

- `lock`: one OS byte-range lock (`msvcrt` on Windows, `fcntl` elsewhere). Every read and write of shared state takes it. A thread lock sits in front, because POSIX locks do not exclude threads of one process.
- `leases/<job>.json`: pid, started, heartbeat, generation, released. A lease TAKEOVER (same job id again) needs a heartbeat older than 300 s AND a dead pid. `pid_alive` uses `OpenProcess`/`GetExitCodeProcess` on Windows, never `os.kill`, which terminates there.
- `merge.lock`: `merge_lock()`, one merge at a time across loops. It is a separate file, so a merge never blocks state writes.
- `claims.json`: repo-relative keys, each with `[{job, gen, mode}]`. Mode `w` is exclusive, mode `r` shares with other readers. `take` is all or nothing. A claim is held while its job's lease is not released AND its pid is alive, so a killed job frees its claims at once (the 5-minute rule governs only the lease takeover). Files outside the repo keep one identity (`external/<hash>/<name>`).
- `registry.json`: request key -> job, gen, state, base, result, started, finished. `recent(n)` gives the last n records, newest first.
- `gates.json`: gate name -> holders (pid, gen, token). Holders with a dead pid are pruned.
- Fencing: `fenced_write`, `registry.begin/record` and `claims.take/release` raise `Fenced` when the writer's generation is lower than the stored one. `lease.release` returns False for a stale generation. `lease.beat` raises `LeaseLost` when the lease is gone or its generation changed.
- Job ids: `new_job_id(name)` gives `<slug>-<YYYYMMDD-HHMM>-<4 hex>` and reserves it under the lock.

## Interface (what WP3 builds on)

```
home(loops=1, fallback=None, worktrees=()) -> Path      # HomeRequired when >1 loop and unset; refused inside a worktree
lease.acquire(job_id) -> gen    lease.beat(job_id, gen)    lease.release(job_id, gen) -> bool    lease.stale(job_id) -> bool
lease.keep_alive(job_id, gen, every_s=30) -> stop()     # daemon heartbeat thread
claims.take(job_id, gen, rel_paths, reads=()) -> (ok, conflicts)    claims.release(job_id, gen) -> bool    claims.is_live(rel) -> bool
registry.lookup(key)    registry.begin(key, job_id, gen, base) -> "run" | "tests-only"    registry.record(key, job_id, gen, state, base, result)
registry.recent(n) -> [{key, job, state, started, finished}]
merge_lock(home_dir=None, timeout_s=30)                 # home/merge.lock
gate.slot(name, gen, timeout_s, capacity=None)           # context manager; raises TimeoutError when no slot frees in time
fenced_write(path, data, job_id, gen, home_dir=None)
```

The module-level `lease`, `claims`, `registry`, `gate` use `SATVIK_HOME`. Each class also takes an explicit home folder, which the tests use.

## Changes in pieces/p1_claims.py

- `ClaimStore(directory, repo=None)`: the claims now go to the loop home, keyed by repo-relative path. A claim is one job lease plus its claims, and it starts the heartbeat. A second claim under the same job id raises `ClaimConflict`. A file outside the repo keeps one identity (`external/<hash>/<name>`), so two stores still conflict on it. `repo` defaults to the parent of a `_loop` folder, otherwise to the folder itself. Reader lock files of the old per-file design are removed when their lock is free.
- `run_job` takes a unique job id (`new_job_id`). The status folder claim stays `_loop/<name>`, so two launches of one job name still conflict.
- `reconcile_stale` marks a `running` job `stopped` only when no live claim holds its folder.
- A job with no `spec` no longer reads the repo folder itself (it used to pass `repo/""`).
- Removed: the per-file claim lock files. `KeyLedger`, `FileLock` and the launcher are unchanged. `canonical_path` is kept. `test_loop_races.py` and `test_loop_acceptance.py` pass unedited.

## Decisions beyond the package

- `take` and `begin` were added to the interface, and `take` has a `reads` argument. The existing reader/writer rule in the acceptance tests needs it.
- Claim liveness is the holder's pid, not the heartbeat. The 5-minute rule governs lease takeover only, so a killed loop frees its claims at once, as the old OS locks did.
- `registry.begin` is the "repeat of a request" rule: same key and same base after a pass gives "tests-only". A different base gives "run". A running request whose owner is live raises `RequestRunning`.
- `gate.slot` raises `TimeoutError` (no custom class). It is the simple version: a capacity (default 1, or `CAPACITY[name]`) with dead-holder pruning. No pacing, no ledger.
- A corrupt shared file raises `LoopHomeError` and is not overwritten. A corrupt claims file therefore stops the launch until a person looks at it.

## Tests

- `pieces/test_loop_home_contract.py` (6): the shared contract WP3 also runs. `gate.slot` raises `TimeoutError`; `acquire` raises `LeaseHeld` while live; `beat` raises `LeaseLost` for a missing lease and for a changed generation; `merge_lock` is `home/merge.lock` and excludes another process; `registry.recent(n)` is newest first.
- `pieces/test_loop_home.py` (26), real child processes: home rules; job ids; pid liveness; takeover only when stale AND dead; a stale writer refused while a fresh one wins; two writers at once (40 writes each, the stale one all refused); claims conflicting across processes, all or nothing; a dead process frees its claim at once; 4 processes x 10 claims losing nothing; readers vs writer; registry fencing and `tests-only`; `RequestRunning` with a live owner and a dead one; gate timeout and pruning; the lock excluding another process; the heartbeat moving forward.
- `pieces/test_p1_claims.py`: the claim tests were updated and extended for the new rules (repo-relative keys, one identity for external files, a same job id twice conflicts, a dead child's claim frees at once while its job id waits for staleness). One reconcile test was added.
- Red before the change: `test_loop_home*.py` failed at collection (`ModuleNotFoundError: loop_home`). Before the `p1_claims` change, 4 of the updated claim tests failed (4 failed, 29 passed). The first full run after the rewire showed 7 failures: `test_loop_races.py` (external identity, legacy reader files, dead-reader claim) and two of my tests. All are fixed. No existing test was edited except `test_p1_claims.py` and the new files, which are mine.
- Full suite, final commit: 624 passed, 3 skipped (the 3 documented skips), exit 0. In the baseline on `origin/main`, `test_p0_sandbox.py::test_a_hung_test_with_child_processes_is_killed_with_its_container` failed because a sandbox container survived its timeout. It passed in this run. The cause is environmental, and this package does not change it.

## Known gaps

- Windows only: the POSIX `fcntl` path and `os.kill`-based liveness are written but not run here.
- Not wired: the launcher refusal for more than one loop (WP3 calls `home(loops=n)`). The heartbeat starts in `ClaimStore.claim`.
- `merge_lock` exists, but nothing calls it yet (WP3 or the coordinator takes it around merges).
- The status folder is still `_loop/<name>`, not `_loop/<job_id>`. WP3 can switch it.
- With `SATVIK_HOME` set in the environment, `run_job` uses that home by default. `test_loop_acceptance.py` assumes no such variable, so it breaks if the variable is set on a machine that runs the tests.
