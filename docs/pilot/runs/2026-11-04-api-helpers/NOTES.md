# API helpers run, 2026-11-04: three jobs

Result: all three jobs merged on origin/main, each with its api_*.py module.

| job | state | job id | change doc | index |
|---|---|---|---|---|
| ah-budget | merged | ah-budget-20261104-0344-1119 | c85ebe0 | 9173c9d |
| ah-idempotency | merged | ah-idempotency-20261104-0344-978b | c5a9267 | 526bd5a |
| ah-shape | merged | ah-shape-20261104-0344-61c6 | 518fac8 | 999668e |

- origin/main at publish: 999668e (base 8a1fe46). The three jobs have no order rule between them.
- scripts/source/api_budget.py, api_idempotency.py and api_shape.py are on origin/main.
- Each results folder holds events.jsonl (under `<job id>/<job name>/events.jsonl`), plus status.json, lineage.json, history/ and merge.json. The archive kept each job (archive: kept).

## Counters
- This run: started +3 (11 to 14), finished:passed +3, finished:merged +3 (8 to 11). queue-wait is 14 total.
- Cumulative: started 14, finished:merged 11, finished:passed 13, finished:escalate 1 (fl-top, run before this one), finished:tests-failed 2 (both from the first two-loop run), queue-wait 14.
- 429 retries by model: xingchen 16, gemma-31b 3, gemma-26b 2.

## Budget and Opus
- review_spent: 0.
- Ledger free_used: 99 (cumulative across the satvik_loop_home).
- Opus: the two "model": "opus" entries in config/roster.json (lines 95 and 104) sit in the production block, which starts at line 57. The test profile has none.

## Excluded
OpenRouter key snapshot folders (none were in these archives).
