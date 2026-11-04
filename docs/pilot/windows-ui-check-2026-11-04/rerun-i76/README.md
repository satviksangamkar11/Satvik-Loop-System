# Windows rerun after I76 (PR #46), 2026-11-04

Engine: origin/main at 70655dd (PR #46 merged, 2716a1d). Fresh detached worktree. Machine: Windows 11, Python 3.14, Node 24.
Profile: test. No model, paid or Opus calls. Docker Desktop running. Secrets not touched.

## Results

| Step | Result |
|---|---|
| `pip install -r monitor/requirements.txt` | Installed. |
| `python -m pytest pieces -p no:cacheprovider -q -rs` | **1525 passed, 7 skipped, 0 failed** (395s, exit 0). Tail: `pytest_tail.txt`. Skip reasons: `pytest_failures_and_skips.txt`. |
| `pieces/test_groq_files.py::test_the_groq_reviewer_receives_only_its_own_file_set` | Passes (inside the full run with 0 failures). |
| `npm ci` (ui/) | Installed (`ui_npm_ci.txt`). |
| `npm run typecheck` | Passed (`ui_typecheck.txt`). |
| `npm run test:unit` | 68 passed, 3 files (`ui_unit_tail.txt`). |
| `npm run test:e2e` | 12 passed (`ui_e2e_tail.txt`). |
| `npm run test:e2e:real` | 21 passed (`ui_e2e_real_tail.txt`). |

Target met: 0 pytest failures, and all UI steps pass.

## Skips (7)

- `test_i68_process_identity.py:69` and `:78`, `test_batch_a_paid.py:308`: real `/proc` checks, Linux-only by design. The Windows identity path is covered by fake-kernel32 tests, which run.
- `test_plan_job.py:573`: this account cannot create symlinks (WinError 1314), so the symlink refusal is not verified on this machine.
- `test_guards.py:103`, `:117`, `:128`: these files belong to the Reverse-Video project and are not in this repository.

## Files

- `pytest_tail.txt`: last 40 lines of the full pytest run.
- `pytest_failures_and_skips.txt`: failure messages and every skip reason.
- `ui_npm_ci.txt`, `ui_typecheck.txt`, `ui_unit_tail.txt`: UI install, typecheck, unit.
- `ui_e2e_tail.txt`, `ui_e2e_real_tail.txt`: e2e (committed config) and real-backend e2e.
