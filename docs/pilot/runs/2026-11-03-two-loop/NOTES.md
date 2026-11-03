# Two-loop test, 2026-11-03: partial evidence

Result: not complete. Both jobs passed their own runs, but both merges failed the merge check, so nothing was merged.

- origin/main stayed at 5de3b0c. No tl_ file is on main.
- tl-index was not queued (RUNBOOK section 6 waits for tl-slug to merge).
- The stop-switch test (section 7) was not run.

## Cause

`merge_queue.default_suite` runs the `pieces/test_*acceptance*.py` files inside the Docker sandbox first. The sandbox image has no `yaml` module, so collection fails on `pieces/test_loop_acceptance.py` and `pieces/test_p0_acceptance.py` with `No module named 'yaml'`. The same tests pass on the host. The pieces suite on origin/main gives 963 passed, 3 skipped. The sandbox selftest returned no problems. See `merge-suite-check-on-main.log`.

## Gaps in this evidence

- The per-job `status.json`, `lineage.json`, `*.md` and `history/` were not copied while the jobs were `passed` (RUNBOOK section 8 item 4). A failed merge removes the job worktree, so those files are gone. What survives: the registry rows (including the sandbox failure tails), claims, leases, counters, and the loop outputs.
- The name-keyed registry rows for tl-slug and tl-roman still read `passed`. The merge failure is recorded only under the job-id key. The section 6 check reads the name rows, so it would show `passed` rather than `merged` or `tests-failed`.

## Counters and budget

- Counters: started 2, finished:passed 2, finished:tests-failed 2, queue-wait 1.
- 429 retries by model: xingchen 16, gemma-31b 3, gemma-26b 2.
- Ledger: free_used 62 (shared by the whole satvik_loop_home, including earlier pilots), review_spent 0.
- Opus: the two `"model": "opus"` entries in config/roster.json (lines 90 and 99) sit in the production block. The test profile has none.
