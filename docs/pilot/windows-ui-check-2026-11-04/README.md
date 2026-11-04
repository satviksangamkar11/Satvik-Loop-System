# Windows UI check, 2026-11-04

Engine 96efdef (origin/main, PR #39 merged). Machine: Windows, Python 3.14, Node 24. Profile: test.

## Results

| Step | Result |
|---|---|
| I13: full `pytest pieces` | 1317 passed, 3 skipped, 1 failed (`pieces/test_p1_events.py::test_rebuild_index_gives_the_same_rows_and_a_corrupt_index_changes_no_answer`, see below). Tail in `pytest_tail.txt`. |
| I13: `test_the_plan_folder_holds_the_plan_spec_job_and_files_and_the_job_is_read_only` | Passes on Windows (run on its own: 1 passed). |
| I19: `POST /api/models/verify`, Claude Code `haiku`, effort `high` | `{"result":"pass","verified_at":"2026-11-04T06:22:40Z"}`, HTTP 200. Saved in `verify_haiku_high.txt`. The peer did not name a contract; I used `reviewer_verdict`. |
| UI: `npm ci`, `npm run dev:live` | Installed and running on `localhost:5173`, connected to the API on `127.0.0.1:8765` (header shows LIVE / CONNECTED). |
| UI: 7 pages | All returned 200 and rendered. Screenshots in `screens/`. Console errors: 0 on every page (`console_errors.json`). |

## Findings

1. **`test_p1_events.py` fails on Windows.** The test raises `PermissionError: [WinError 32]` when it deletes `history.db` in its temp folder, because another process still holds the file open. This is a Windows file-lock issue in the test's cleanup, not an assertion failure. It is outside the I13 target, which passes.
2. **Recent Jobs shows "Unknown" for older jobs.** On the dashboard, the `tl-*`, `fl-*` and `ah-*` rows from before 2026-11-04 show Unknown for end state, tests, verdict and finish time. The three `ah-*` jobs from today show real values. Their data is likely missing from the older job records, not a UI error, but I did not check this.
3. **`config/models.json` changed in the worktree.** The verify call recorded the haiku result into the catalog. That file is not in this commit, so the catalog change is not on this branch.

## Files

- `screens/*.png`: full-page screenshots of `/dashboard`, `/jobs/new`, `/loops`, `/models`, `/stages`, `/results`, `/system`
- `console_errors.json`: per-page HTTP status and console errors
- `verify_haiku_high.txt`: the verify response
- `pytest_tail.txt`: the pytest tail and the I13 target run
- `api_server.log`: the API server log
