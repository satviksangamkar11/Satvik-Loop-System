# Package F: protected control files

Date: 2026-11-02. Base: main 8027d82.

## What changed

- `scripts/source/p0_gates.py`: `production_job_problems` now refuses any production job whose `files` include one of the loop's own control files, with the reason `job edits the loop's own control files`.
- Protected list (`PROTECTED_CONTROL_FILES`): `scripts/source/fix_loop.py`, `scripts/source/p1b_*.py`, `scripts/source/openrouter_call.py`, `scripts/source/sandbox_runner.py`, `scripts/source/p0_gates.py`, `scripts/source/opus_repair.py`, `scripts/source/plan_job.py`, `scripts/source/loop_home.py`, `scripts/source/launch.py`, `scripts/source/merge_queue.py`, `scripts/source/shared_limits.py`, `config/roster.json`, `tools/sandbox/*`.
- Path normalisation (`normal_repo_path`): backslashes become `/`, `.` and `..` are resolved, doubled and leading slashes are dropped, case is folded, and trailing dots and spaces are dropped from each part (Windows ignores them). Matching is by fnmatch on the normalised path.
- Tests: `pieces/test_p0_protected_files.py` (new). Each protected path is refused, 12 path variations are refused, 7 similar but unprotected paths are accepted, and the pilot job `docs/pilot/pilot-loop-status.json` is still accepted.

## Known limits

- Windows 8.3 short names (e.g. `FIX_LO~1.PY`) and NTFS alternate data streams (`fix_loop.py::$DATA`) are not normalised. The check is string-based. Add them when job paths can carry them.
- The check covers only `files`. Negative-control and `sandbox_include` entries are not checked against the list here.

## Tests

Run from the worktree with `python -m pytest pieces -p no:cacheprovider -q`. Result is recorded in the PR description.
