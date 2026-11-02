# close-1

Closing branch: brings WP3 (launcher and merge queue), package E (shared limits), and the launcher wiring onto main.

## Step 1: WP3 (PR #7)
- Applies PR #7's delta over 8027d82 (launcher, merge queue, tests, wp3.md). PR #7 is based on an older main, so its diff against current main would delete files from PR #12; only its own additions are taken.
- `SANDBOX_SLOTS = 4` in `scripts/source/launch.py` (owner decision).
- Tests: `pieces/test_wp3_launch.py`, `pieces/test_wp3_merge_queue.py`: 29 passed.

## Step 2: package E shared limits (PR #13)
- Applies PR #13's delta over 8027d82 (`scripts/source/shared_limits.py`, `pieces/test_shared_limits.py`, `docs/changes/wp-shared-limits.md`).
- Model slot of 1 for `nvidia/nemotron-3-ultra-550b-a55b:free`, key `nemotron`, next to dots 1, xing 1, north 1, glm 2, opus 1, sandbox 4.
- Tests: `pieces/test_shared_limits.py`: 16 passed (the nemotron case added to `SLOT_CASES`).

## Step 3: shared ledger hook in the OpenRouter client (`scripts/source/openrouter_call.py`)
- `OpenRouter.set_job(job_id, gen)` puts the client on the account ledger. Without it the client keeps its own ledger, so the probe and the older tests are unchanged.
- On the shared ledger, before each request: a free request calls `reserve_free`; a paid request calls `reserve_review`, then `pace(timeout_s)`, then sends and `settle_review`; every send runs inside `model_slot` for its model.
- Waiting counts against the unit deadline: the slot wait is bounded by the time left, and `pace()` takes a `timeout_s` and raises TimeoutError instead of waiting past it.
- Tests: `pieces/test_openrouter_shared.py` (3 new), `pieces/test_openrouter_call.py`, `pieces/test_shared_limits.py`: 125 passed.
