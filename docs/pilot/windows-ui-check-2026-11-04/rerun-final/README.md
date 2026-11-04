# Final Windows rerun, 2026-11-04

Engine: origin/main at 7deb934 (PR #44 merged, I68-I72). Fresh detached worktree. Machine: Windows 11, Python 3.14, Node 24.
Profile: test. No model, paid or Opus calls. Docker Desktop running. The untracked `ui/playwright.rerun.config.ts` was deleted first.

## Results

| Step | Result |
|---|---|
| `pip install -r monitor/requirements.txt` | Installed. |
| `python -m pytest pieces -p no:cacheprovider -q -rs` | **1490 passed, 7 skipped, 2 failed** (395s). Tail: `pytest_tail.txt`. Failures and skip reasons: `pytest_failures_and_skips.txt`. |
| `npm ci` (ui/) | Installed (`ui_npm_ci.txt`). |
| `npm run typecheck` | Passed (`ui_typecheck.txt`). |
| `npm run test:unit` | 68 passed, 3 files (`ui_unit_tail.txt`). |
| `npm run test:e2e` (committed config, no override) | **12 passed** (`ui_e2e_tail.txt`). |
| `npm run test:e2e:real` | **21 passed** (`ui_e2e_real_tail.txt`). `git fetch origin ah-run-2026-11-04` worked. |

Target was 0 failures. Pytest has 2 failures, so this run does not meet the target.

## What changed from the earlier rerun

- The reused-pid tests now RUN and pass on Windows: `test_i41_queue_claims::test_a_reused_pid_is_a_dead_owner`, `test_i57_clock_and_pid` (lease, gate slot, paid hold), and `test_i56` restore-green. `loop_home.process_start` now uses `GetProcessTimes` on `nt`.
- The restore-green failure is gone: `restore_files` now writes exact bytes (`p1b_production.py:255-264`).
- The e2e localhost issue is gone: the committed config runs with no override.

## Skips (7)

- `test_i68_process_identity.py:69` and `:78`: real `/proc` checks, Linux-only by design. The Windows path is covered by fake-kernel32 tests that run (e.g. `test_a_reused_pid_is_a_dead_owner_on_windows_identity`).
- `test_batch_a_paid.py:308`: real `/proc` check for `test_I50_process_start_identifies_this_process`. Linux-only by design.
- `test_plan_job.py:573`: symlink refusal. This account cannot create symlinks (WinError 1314). The refusal is not verified on this machine.
- `test_guards.py:103`, `:117`, `:128`: the Reverse-Video project owns these files, so they do not exist in this repository.

## Failures (2, both reproduced alone)

Both come from the same class of bug: CRLF on Windows with text-mode reads and writes. The I69 fix covered `restore_files` and `write_green_checkpoint` but missed these sites.

1. **`test_p1b_review_package::test_a_provider_failure_before_the_first_write_is_a_clean_stop`**: the candidate folder is created when no write happened.
   - Cause: `scripts/source/p1b_production.py:325` `_produced` reads with `path.read_text(encoding="utf-8")`. On Windows that converts CRLF to LF. The originals were captured byte-exact (`read_text_exact`), so any CRLF file looks "produced" before a write.
   - Effect: `_snapshot` writes a candidate folder when there is no candidate (`p1b_production.py:1375`).
   - Suggested fix: read with `read_text_exact` in `_produced`.

2. **`test_pre_pilot_review::test_a_worker_killed_after_one_candidate_write_is_restored_by_the_next_launch`**: after the kill, the restart prompt shows doubled newlines and not the original text (the assertion fails at `ORIGINAL_CODE in first`).
   - Cause, in two steps in `scripts/source/fix_loop.py`:
     - `:258` `_prepare_job_folder` saves each original with `dest.write_text(text, encoding="utf-8")`. `text` is the exact bytes decoded, so it already contains `\r\n`. Text mode writes `\r\r\n`, so the saved original is corrupted on disk.
     - `:237` the kill-recovery restore reads the saved original with `read_text`. That turns `\r\r\n` into `\n\n`, and the restored file and the prompt show doubled newlines.
   - Effect: on Windows, a job killed mid-run restarts from a corrupted copy of its originals. This is a production bug, not only a test one.
   - Suggested fix: `dest.write_bytes(text.encode("utf-8"))` at `:258`, and `read_text_exact` at `:237`.

## Not fixed in this run

This run only verifies. Engine code is unchanged from `origin/main` 7deb934. The two failures above need engine fixes first.

## Files

- `pytest_tail.txt`: last 40 lines of the full pytest run.
- `pytest_failures_and_skips.txt`: failure messages and every skip reason.
- `ui_npm_ci.txt`, `ui_typecheck.txt`, `ui_unit_tail.txt`: UI install, typecheck, unit.
- `ui_e2e_tail.txt`, `ui_e2e_real_tail.txt`: e2e (committed config) and real-backend e2e.
