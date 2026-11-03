# Four-loop test, 2026-11-03: partial evidence

Result: NOT complete. Four of five jobs merged. fl-top escalated, so fl_top.py is not on origin/main and the done criteria are not met.

## Jobs

| job | state | job id | change doc | index |
|---|---|---|---|---|
| fl-clamp | merged | fl-clamp-20261103-2236-aaea | 3915a8c | 1e09eff |
| fl-dedupe | merged | fl-dedupe-20261103-2236-cc8c | 8aeea84 | 0f45eb1 |
| fl-chunk | merged | fl-chunk-20261103-2236-adce | 9fa794d | d7efbf3 |
| fl-wordcount | merged | fl-wordcount-20261103-2236-977a | 5be2456 | 4fdf7ca |
| fl-top | escalate | fl-top-20261103-2248-2ed3 | none | none |

origin/main at publish: 4fdf7ca (`git log --oneline -14` is in git-log-origin-main-14.txt).

## fl-top

- Ended `escalate` after round 7. The registry shows result `{round: 7}`.
- The reason is inferred from the code, not read from a file: `fix_loop.py` writes `max rounds exceeded` when the tests never pass within the round budget.
- Its ESCALATE.md and status.json were in the job worktree, and an escalated job's worktree is removed. The loop copies `_loop` only for passed jobs, so no job evidence survives. The only record is the registry row and its lease file.

## Four at once

- Parallel snapshot taken at 2026-11-03 22:36:55Z (parallel-snapshot/SNAPSHOT.txt): four job leases live together: fl-clamp-…-aaea, fl-chunk-…-adce, fl-dedupe-…-cc8c, fl-wordcount-…-977a.
- The same snapshot holds claims.json with those four jobs' claims, disjoint.
- Each job also left a second unreleased lease from the same process, with no registry row (fl-*-…-ea43, -95b0, -e76d, -ddfb). This is the same leak seen in the two-loop run (see the two-loop NOTES.md). The snapshot keeps all of them.

## Claims and stop switch

- No claim conflict: no `refused:claim-conflict` counter.
- Stop switch: with fl-top queued and STOP present, the loop started nothing (started stayed at 9). STOP was then removed, and fl-top ran.

## Counters and budget

- counter:started 10, finished:merged 7 (3 two-loop + 4 four-loop), finished:passed 9, finished:escalate 1, finished:tests-failed 2 (both from the first two-loop run), queue-wait 9.
- 429 retries by model: xingchen 16, gemma-31b 3, gemma-26b 2.
- Ledger: free_used 87 (shared by the whole satvik_loop_home), review_spent 0.
- Opus: the two `"model": "opus"` entries in config/roster.json (lines 90 and 99) sit in the production block; no test-profile entry.

## Excluded

The OpenRouter key snapshot folders (`_loop/openrouter/`) are not in this publish.
