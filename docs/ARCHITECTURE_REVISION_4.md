# Satvik Loop: Architecture Revision 4 (as built, 3 Nov 2026)

This revision records what the code on `main` (`b96063e`) does, against Revision 3 (2 Nov). It adds no new design. The UI contracts are in
`docs/UI_ENGINE_HANDOFF.md`.

## Built as Revision 3 described
- **P0 gates:**
  - calibration with negative controls (an assertion failure is required);
  - a per-test JUnit verdict;
  - an acceptance lock, which ends `acceptance-tampered` if broken;
  - a review gate on green code only;
  - a Docker sandbox: no network, user 65534, all capabilities dropped, read-only root, `--ipc none`, limits, no fallback;
  - tested = reviewed = delivered hash.
- **P1a adapter:**
  - role keys, with the roster as the allow-list;
  - GLM pinned to Z.ai with no fallbacks;
  - raw errors kept, the `/api/v1/key` snapshot saved;
  - three kinds of 429 told apart;
  - reply checks, redaction, the `GPG_KEY` check;
  - the live daily free cap from `free_model_daily_requests.limit` (fallback 1000).
- **P1b routing:**
  - the fixed ladder, clean restore before a fresh write;
  - per-file lineage and a panel chosen by lineage;
  - the FIX FIRST evidence rule with one re-ask;
  - `CONTRACT_CHECK.md`;
  - job size (1,500 lines per unit / 800 per file), the deadline, `needs_review`.
- **P1c planner:** `claude -p` with no tools or MCP; `INTENT.json` and `raw_response.json` make it exactly-once; ASSUMED lines need approval;
  `planner-uncertain`. It is refused under the test profile.
- **P2 parallel:**
  - one shared ledger (15 requests a minute, the daily cap, a $0.50 daily review cap);
  - file claims, leases with heartbeat and takeover, fencing;
  - the registry with counters, refusal of a running duplicate;
  - one worktree per job, a merge queue with a full sandbox suite, the STOP switch.

## Changed by owner decision (not gaps)
| Revision 3 | Now |
|---|---|
| GLM is the paid repairer ($0.15 a job) | GLM is the paid **reviewer**, production only ($0.05 a job, $0.50 a day). Repair is a Claude Code role (Opus, switched off). Test has no paid calls |
| Reviewer North, backup Gemma | Test: `reviewer_b` Nemotron 3.5 Super, `reviewer_a` the non-author writer. Gemma dropped (never answered). Production `reviewer_b` is still North (owner choice pending) |
| writer_b Xing | Test: Nemotron 3 Ultra (Xing got 429s from the shared pool). Production: Xing |
| "NVIDIA left out" | Allowed in testing only |
| Fixed model families | Any role is any OpenRouter model or any Claude Code model (opus, sonnet, haiku, fable; low to max). It is changed in `config/roster.json` only. The test profile allows only haiku at high among Claude Code roles. A new model gets its own one-call gate |
| Shared lessons; registry reuse of a passed request | Not built, by decision |
| `DISPUTE.md` (GLM as repairer) | Not built: that role no longer exists |
| Bake-off pilot | Replaced by one real pilot job |
| 4 review passes | 2 FIX FIRST repair rounds and 2 repair calls per job |

## Added on 3 Nov
- **Green code is kept:** a FIX FIRST repair that turns the tests red restores the last green code and ends `review-open` with its findings.
  Reviewer replies are saved as `history/review_NN.txt`.
- **Repairs use SEARCH/REPLACE edits,** with one re-ask and then a whole-file fallback. The repair digest holds the assertion, stdout, stderr
  and the test source.
- **Launched jobs commit their own files,** and the merge check runs in the sandbox. The image has `git`, `pyyaml` and `jsonschema`, and the copy
  holds `config/`, `tests/` and `docs/`.
- **Every terminal job is archived** to `SATVIK_HOME/results/<job_id>/`. The merge outcome goes to `merge.json`. An archive failure never
  blocks a finish.
- **Waits are reported:** `status.json.waiting` (`model-slot`, `account-pace`, `429-backoff`, `retry-backoff`).
- **Queue dedupe:** active duplicates are refused (`try_enqueue`), and `rerun=True` runs a completed job again.
- **The client releases its own lease** at job end.

## Evidence (3 Nov, test profile, $0, 0 Opus calls)
| Run | Result | Evidence |
|---|---|---|
| Pilot r5 (`f7db576`) | `passed`: Dots wrote it, READY from Nemotron 3.5 Super and Nemotron 3 Ultra, all three hashes `43d11ff3…`, 3 free requests | branch `pilot-run-r5` |
| 2-loop r2 (`3705c89`) | 3 merged in order (`tl-roman`, `tl-slug`, `tl-index`), STOP honoured | branch `two-loop-run-2026-11-03-r2` |
| 4-loop (`9625f24`) | 4 parallel jobs merged: `fl-clamp`, `fl-dedupe`, `fl-chunk`, `fl-wordcount`. Four leases live at once (snapshot 22:36:55Z), no claim conflict, STOP honoured. **`fl-top` escalated**; its evidence was lost, which led to the archive fix (#36) | branch `four-loop-run-2026-11-03` |
| Docker suite on `main` | 1029 passed, 4 skipped, 1 failed. The failure is the known root-only `test_plan_job.py::…_read_only` in the cloud container | — |

## Open
- `fl-top` re-run, now with the archive, to see why it escalated.
- Owner cleanup: the `r72-token-budget` branch (`5905bda`), `pilot-loop-status.solo.json`, `wt-pilot-r5`. `wt-B-pilot*` need a review first.
- **Control Center:**
  - P1: the frozen per-job roster and stage configuration;
  - P2–P3: the local API, events and UI;
  - P4: a concurrent pressure test.
- **Deferred:** production `reviewer_b`, the paid production run, legacy cleanup, streaming abort, a launcher CLI, the December re-check (Dots'
  free access ends 31 Dec).
