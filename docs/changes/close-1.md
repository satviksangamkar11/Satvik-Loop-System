# close-1

Closing branch: brings WP3 (launcher and merge queue), package E (shared limits), and the launcher wiring onto main.

## Step 1: WP3 (PR #7)
- Applies PR #7's delta over 8027d82 (launcher, merge queue, tests, wp3.md). PR #7 is based on an older main, so its diff against current main would delete files from PR #12; only its own additions are taken.
- `SANDBOX_SLOTS = 4` in `scripts/source/launch.py` (owner decision).
- Tests: `pieces/test_wp3_launch.py`, `pieces/test_wp3_merge_queue.py`: 29 passed.
