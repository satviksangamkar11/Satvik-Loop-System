# Windows rerun after the CRLF fixes (PR #45, I73-I75), 2026-11-04

Engine: origin/main at 8c0cbec (PR #45 merged, ab13cdf). Fresh detached worktree. Machine: Windows 11, Python 3.14, Node 24.
Profile: test. No model, paid or Opus calls. Docker Desktop running. Secrets not touched.

## Results

| Step | Result |
|---|---|
| `pip install -r monitor/requirements.txt` | Installed. |
| `python -m pytest pieces -p no:cacheprovider -q -rs` | **1518 passed, 7 skipped, 1 failed** (390s). Tail: `pytest_tail.txt`. Failure and skip lines: `pytest_failures_and_skips.txt`. |
| `test_p1b_review_package::test_a_provider_failure_before_the_first_write_is_a_clean_stop` | **Passes.** |
| `test_pre_pilot_review::test_a_worker_killed_after_one_candidate_write_is_restored_by_the_next_launch` | **Passes.** |
| `npm ci` (ui/) | Installed (`ui_npm_ci.txt`). |
| `npm run typecheck` | Passed (`ui_typecheck.txt`). |
| `npm run test:unit` | 68 passed, 3 files (`ui_unit_tail.txt`). |
| `npm run test:e2e` (committed config) | **12 passed** (`ui_e2e_tail.txt`). |
| `npm run test:e2e:real` | **21 passed** (`ui_e2e_real_tail.txt`). |

Target was 0 pytest failures. There is 1 failure, so this run does not meet the target.

## The failure (1)

**`pieces/test_groq_files.py::test_the_groq_reviewer_receives_only_its_own_file_set`** ends in `escalate` instead of `passed`. The job's status says: `the coder tried to change frozen or pre-existing tests (only appending is allowed in round 0): ['pieces/test_m.py']`.

- The test writes `pieces/test_m.py` with `write_text`, so on Windows it is CRLF on disk: `b'def test_m():\r\n    assert True\r\n'`.
- The coder's reply has the same text in LF (`def test_m():\n    assert True\n`).
- The round-0 "append only" check at `scripts/source/fix_loop.py:1099` is `content.startswith(original_contents[rel].rstrip())`. The original keeps its `\r\n` bytes, so an LF reply never matches, and the job escalates.
- Effect on Windows: any pre-existing test file with CRLF line endings will escalate in round 0 whenever the coder re-states it with LF, which the coder cannot control. This is a real production gap, not only a test-fixture issue. Linux passes because the test file is LF there.
- Suggested fix (not applied here, verification only): compare line endings in the round-0 prefix check (normalise both sides, or compare exact bytes after the reply is encoded the same way as the file), and add a Windows CRLF case to the test.

## Skips (7)

- `test_i68_process_identity.py:69` and `:78`, `test_batch_a_paid.py:308`: real `/proc` checks, Linux-only by design. The Windows path is covered by the fake-kernel32 tests, which run.
- `test_plan_job.py:573`: this account cannot create symlinks (WinError 1314), so the symlink refusal is not verified here.
- `test_guards.py:103`, `:117`, `:128`: the files belong to the Reverse-Video project and are not in this repository.

## Files

- `pytest_tail.txt`: last 40 lines of the full pytest run.
- `pytest_failures_and_skips.txt`: failure messages and every skip reason.
- `ui_npm_ci.txt`, `ui_typecheck.txt`, `ui_unit_tail.txt`: UI install, typecheck, unit.
- `ui_e2e_tail.txt`, `ui_e2e_real_tail.txt`: e2e (committed config) and real-backend e2e.
