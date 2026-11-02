# WP1: pilot loop_status (Revision 4)

## Status
Stopped before the pilot run. The PRs are open; the merges were blocked by the auto-mode classifier ("Merge Without Review"), so main does not yet include the fixes and the pilot has not run.

## Steps
| Step | What | Result |
|---|---|---|
| 0 | Opus guard: a real Claude command is refused under pytest or `SATVIK_NO_OPUS=1`, except the fake claude | PR #9 (branch `opus-guard`, commit 42d5f81), open, not merged |
| 0a | Sandbox kill waits until no container with the name is listed (flake fix; the timed-out container test failed in full-suite runs and passed alone) | PR #8 (branch `sandbox-kill-wait`, commit 74ce9d3), open, not merged. Full pieces suite on this branch: 588 passed, 3 skipped |
| 1 | Loader asserts `MODULE.is_file()` before import | Branch `pilot-loop-status` (commit on top of abe0332), PR open, not merged. The 8 locked tests fail with AssertionError on the baseline; with the empty stub, `test_rows_order_and_fields` and `test_main_output` fail with AssertionError |
| 2 | Pilot job file | `docs/pilot/pilot-loop-status.json`; passes `production_job_problems` and `check_production_job` |
| 3 | Probe, ledger, pilot run | Not run (blocked on the merges) |

## Decisions
- OPUS_ENABLED stays False. The owner's earlier instruction to flip it to True was replaced by a later instruction; the flip was reverted before any commit.
- The p6 test that checked the switch is False was not changed; it still passes.
- A test that stubs subprocess for `run_claude` now passes the fake claude path, because the Opus guard refuses a bare `claude` command under pytest.

## Open
- Merge PRs #8, #9 and the pilot PR, in that order (the pilot PR needs the Opus guard before the pilot can run safely).
- Then run the probe (Dots, Xing, North), read the ledger entry for the extra request, and run the pilot per the owner's steps.
