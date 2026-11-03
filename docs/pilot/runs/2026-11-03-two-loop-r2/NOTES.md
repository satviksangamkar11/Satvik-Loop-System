# Two-loop test r2, 2026-11-03: full run

Result: all three jobs merged on origin/main, in order.

| job | job id | change doc | index |
|---|---|---|---|
| tl-roman | tl-roman-20261103-2214-6a93 | 7cd5010 | da825fb |
| tl-slug | tl-slug-20261103-2214-bc59 | c9d18bb | 890018a |
| tl-index | tl-index-20261103-2220-94a5 | cfe0e2e | 2b80095 |

origin/main at publish: 2b80095. tl-roman merged before tl-slug; tl-slug merged before tl-index (the runbook's order rule).

## What this run did differently

- Sandbox image rebuilt from tools/sandbox (3705c89); selftest returned []. The earlier merge failure (no yaml module in the sandbox) did not recur.
- Old tl-slug and tl-roman queue items and two stale leases from the first run were removed or released before section 3.
- Each loop copied its job's `_loop` folder (status.json, lineage.json, *.md, history/) after the job passed and before the merge. The copies are in job-evidence/. The original copies were not kept in the first run, so this is new evidence.
- The stop switch was tested: with STOP present, the loop started nothing, `started` stayed at 4 and tl-index stayed queued. STOP was then removed.

## Gaps in this evidence

- job-evidence/<job>/_loop/openrouter/ (OpenRouter key snapshots) was excluded from this publish. The copies contain key labels, which the secret scan flagged. The originals remain in satvik_loop_home.
- Name-keyed registry rows (tl-slug, tl-roman, tl-index) still read `passed`. Merge results are recorded only under the job-id key. Section 8 item 1 should read the job-id rows.

## Counters and budget

- counter:started 5, finished:passed 5, finished:merged 3, finished:tests-failed 2 (both from run 1), queue-wait 3.
- 429 retries by model: xingchen 16, gemma-31b 3, gemma-26b 2.
- Ledger: review_spent 0. Free requests in the ledger: see ledger-limits.json (shared by the whole satvik_loop_home).
- Opus: the two `"model": "opus"` entries in config/roster.json (lines 90 and 99) sit in the production block. The test profile has none, and no Opus call appears in these runs.
