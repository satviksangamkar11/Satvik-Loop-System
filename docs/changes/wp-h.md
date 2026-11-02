# WP-H: needs_review, quota-exhausted, CONTRACT_CHECK.md

Date: 2026-11-02. Base: main 5afdc45.

## What changed

- `scripts/source/p1b_routing.py`
  - `needs_review` (status name `needs_review`): the same non-empty failing-test set in 3 test runs in a row (`STREAK`) ends the ladder. The failing ids go in the outcome and into `status.json` as `failing_tests`. Originals are restored as for any non-PASS job.
  - `quota-exhausted`: `QuotaStop` (raised by the production models when the adapter raises `QuotaExhausted`) ends the ladder at once. No further model call is made.
  - Contract check: after Xing's reply runs, if Dots and Xing both fail the same locked acceptance test and their written code differs, the models get `contract_check(...)` and the job ends `escalate` with a reason starting `CONTRACT_CHECK`. The check runs before the streak check.
  - When the streak and the contract rule fire on the same step, CONTRACT_CHECK.md wins and the job ends escalate.
  - `Outcome` gains a `failing` field (default empty).
- `scripts/source/p1b_production.py`
  - `Production._ask` maps `QuotaExhausted` to `QuotaStop`. `run` maps a `QuotaExhausted` from `begin_job` to `quota-exhausted`.
  - `Production.contract_check` writes `CONTRACT_CHECK.md` in the job folder: per test id, the assertion message from the last JUnit run and the full assertion message, and a SPEC line labelled `likely SPEC line (guessed by word overlap)` (the requirement line with the most shared words; `no SPEC line matches` when none shares a word). The SPEC link is text for the reader only; it never decides a pass or fail..
  - `status.json` carries `failing_tests`.
- Tests: `pieces/test_p1b_routing.py` and `pieces/test_p1b_production_run.py` (new cases at the end; no real model, no Opus, no GLM).

## Behaviour changes to confirm

- The streak rule counts every test run in the ladder. Scripted Opus scenarios that repeated one failing set three times were rewritten to vary the set (they still share `t1` so the Opus gate applies).
- The contract check runs before Opus. When Dots and Xing both fail a locked acceptance test with different code, the Opus repair is no longer reached for that failure. The docker-gated test `test_a_job_red_red_red_...` now asserts this escalation instead of an Opus repair.

## Tests

Run from the worktree: `python -m pytest pieces/test_p1b*.py -p no:cacheprovider -q`.
