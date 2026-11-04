# Windows rerun after the Codex fixes (PR #43), 2026-11-04

Engine: origin/main at 5817b4e (PR #43 merged). Fresh detached worktree. Machine: Windows 11, Python 3.14, Node 24.
Profile: test. No model calls, no paid calls. Docker Desktop was running.

## Results

| Step | Result |
|---|---|
| `pip install -r monitor/requirements.txt` | Installed. |
| `python -m pytest pieces -p no:cacheprovider -q` | **1480 passed, 4 skipped, 6 failed** (385s). Tail: `pytest_tail.txt`. Failure lines: `pytest_failures.txt`. |
| `npm ci` (ui/) | Installed (`ui_npm_ci.txt`). |
| `npm run typecheck` | Passed (`ui_typecheck.txt`). |
| `npm run test:unit` | 68 passed, 3 files (`ui_unit_tail.txt`). |
| `npm run test:e2e` | 12 passed (LIVE-mode suite, 3 viewports) on the override config below (`ui_e2e_tail.txt`). |

The 6 pytest failures are not 0, so this rerun does not meet the "expect 0 failures" target.

## Pytest failures (all reproduced on this Windows machine)

1. **Reused-pid tests (5):** `test_i41_queue_claims::test_a_reused_pid_is_a_dead_owner`, `test_i57_clock_and_pid::test_a_lease_whose_pid_was_reused_is_not_held`, `::test_a_gate_slot_held_by_a_reused_pid_is_free`, `::test_a_paid_hold_of_a_reused_pid_is_released_as_spent`, and `test_i56_bounds_and_checkpoint::test_restore_green_puts_the_green_files_back_from_disk_when_memory_is_gone` (see 3 below for the last one).
   - Cause: `scripts/source/loop_home.py:139` `process_start` reads only `/proc/<pid>/stat` (Linux). On Windows it returns `None`.
   - `owner_alive` (`loop_home.py:150`) then treats a live pid with no start identity as alive: `if now is None or ...: return ...` (`loop_home.py:158`).
   - Result: on Windows a reused pid still counts as its old owner. The I41/I57 protection is Linux-only, though the tests run unconditionally on Windows.
   - Not fixed in this run (verification only). Suggested fix: a Windows start identity (e.g. `GetProcessTimes` via ctypes) in `process_start`, or mark these tests as Linux-only with the gap written down.

2. **`test_plan_job::test_a_symlink_into_secrets_is_refused`:** `OSError [WinError 1314] A required privilege is not held`. This account cannot create symlinks (Developer Mode off / no SeCreateSymbolicLink). Environment, not behaviour. The test has no skip for this case, so the symlink refusal is not verified on this machine.

3. **Restore-green (real Windows bug):** `scripts/source/p1b_production.py:244-253` `restore_files` writes with `Path.write_text(text, encoding="utf-8")`. On Windows that writes `\r\n`. `read_green_checkpoint` and `_texts_hash` (`p1b_production.py:260-268`) and `p0_gates.combined_hash` (`p0_gates.py:75`) hash the bytes, so the restored files do not match `tested_hash`. `restore_green` then raises `"the restored files are not the files of the last green run"` (`p1b_production.py:1039`).
   - Impact: a FIX FIRST repair that turns the tests red cannot be undone on Windows for multi-line files. This is the recovery path for the red-repair case.
   - Suggested fix: write with `newline=""` / `write_bytes(text.encode("utf-8"))` in `restore_files`, so bytes match the hash.

## e2e note (test harness, not app)

The committed `ui/playwright.config.ts` polls `http://127.0.0.1:5173`. On this machine Vite listens only on `localhost` (`::1`), so the webServer step timed out (`Timed out waiting 60000ms from config.webServer`).

To run the suite I used an untracked override, `ui/playwright.rerun.config.ts`, which only changes the 5173 url and `baseURL` to `http://localhost:5173`, and set `FAKE_API_ORIGIN=http://localhost:5173` so the fake backend's CORS origin matches. The override is not committed. The specs and the other config are unchanged.

Command used: `FAKE_API_ORIGIN=http://localhost:5173 npx playwright test --config playwright.rerun.config.ts`

Not verified: the 4 skipped pytest tests were not identified; the sandbox/Docker tests were run with Docker up, but this rerun did not check which tests hit the sandbox.

## Housekeeping

Before the e2e run I stopped two leftover processes from the earlier B7 check that held ports 8765 and 5173 (`uvicorn` from `wt-b7` and `npm run dev:live`). Nothing else was stopped.
