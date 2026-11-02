# WP-H: needs_review, quota-exhausted, CONTRACT_CHECK.md

Date: 2026-11-02. Base: main 3673941.

## What changed

- `scripts/source/p1b_routing.py`
  - needs_review: the same non-empty failing-test set in 3 of the free writers' test runs in a row (`STREAK`) sets a flag. The ladder goes on: a repair step still runs while one is left (Opus, at most twice). When no repair step is left, or none can run (the gate refuses), the job ends `needs_review` with the failing ids in the outcome and in status.json `failing_tests`. If Opus is unavailable the job ends escalate with the reason given (for example "Opus repair unavailable"). Originals are restored as for any non-PASS job.
  - quota-exhausted: `QuotaStop` (raised when the adapter raises `QuotaExhausted`) ends the ladder at once. No further model call is made.
  - Contract check: when Dots and Xing both fail the same locked acceptance test and their written code differs, the models get `contract_check(...)`, which writes CONTRACT_CHECK.md. The ladder does not stop.
  - `Outcome` gains a `failing` field (default empty).
- `scripts/source/p1b_production.py`
  - `Production._ask` maps `QuotaExhausted` to `QuotaStop`. `run` maps a `QuotaExhausted` from `begin_job` to `quota-exhausted`.
  - `Production.contract_check` writes `CONTRACT_CHECK.md` in the job folder: per test id, the full assertion message from the last JUnit run, and a line labelled "likely SPEC line (guessed by word overlap)" (the requirement line with the most shared words; `no SPEC line matches` when none shares a word). The SPEC link is text only and never decides pass or fail.
  - status.json carries `failing_tests` (needs_review) and `contract_check` (path to CONTRACT_CHECK.md, whenever the file was written, whatever the end state). `schema_version: 1` is kept in both writes.
- Tests: `pieces/test_p1b_routing.py` and `pieces/test_p1b_production_run.py`.

## Tests

Run from the worktree, with the profile unset and with `SATVIK_PROFILE=production`:
`python -m pytest pieces/test_p1b*.py pieces/test_pre_pilot_review.py -p no:cacheprovider -q`
