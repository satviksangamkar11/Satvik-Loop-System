# Package D: finish (2026-11-02)

Base: main 8027d82. Branch: wp-roster-config. Follows `docs/changes/wp-roster-config.md`, which stays as the package's first write-up.

## Steps

1. **Model-ID exception list.** `pieces/test_roster_config.py` has `MODEL_ID_EXCEPTIONS`, one line of reason per file, each marked
   "remove in follow-up": `coder_run.py`, `codex_review.py`, `draft_source.py`, `nemotron_code.py`, `review_run.py`, `fix_loop.py` (the legacy
   loop, outside the job schema; `openai/v1` is a URL path), `opus_repair.py`, `plan_job.py`. The no-model-ID test skips only these files. A
   second test checks that each listed file exists and carries the marker. The exception is per file, so a new literal ID added to one of
   these files will not be caught until its follow-up removes the entry.
2. **schema_version.** `lineage.json` already carried `"schema_version": 1` (`p1b_routing.py`). `status.json` now carries it in the three
   writers of `fix_loop.py`: the invalid-spec write, `_early_stop`, and `_write_status`. Test:
   `test_fix_loop_status_json_carries_schema_version_1_on_the_invalid_spec_and_the_normal_path`.
   **Not done:** the two `status.json` writes in `scripts/source/p1b_production.py` (the running write at line ~564 and the final write at line
   ~635). That file is not package D's, so it was not edited. A production job's status.json has no `schema_version` until its owner
   adds it there.
3. **Harness and acceptance test.** Both were reverted to `HEAD` (= the package's own version) and tested; both changes are required, so
   they are kept:
   - `pieces/prod_harness.py`: `oc.PAID_PROVIDER_NAME` was removed from `openrouter_call.py` by this package. With the 8027d82 version,
     the production runs end as `exception` (AttributeError). The new line reads the pinned name from `oc.PINS`.
   - `pieces/test_loop_acceptance.py`: `coder_model` and `reviewer_model` are no longer job fields, so the old test (which expects the
     "not configurable" message and accepts a `coder_model` job) fails. The new test expects the schema refusal naming the field.
4. **Production profile.** Documented, not changed. A job with `"profile": "production"` uses the roster of the profile named by
   `SATVIK_PROFILE` when `openrouter_call` is first imported. The default is `test`. Under `test`, `reviewer_a` is `non_author_writer`,
   `repair` is `off` and `planner` is `fake`. No paid call is made in that case, but the job does not run on the production roster.
   **The pilot and any production run need `SATVIK_PROFILE=production` in the environment.** A guard in `fix_loop.run_job` that refuses a
   production job under the test profile is the follow-up if this is not enough.
5. **Full suite.** `python -m pytest pieces -p no:cacheprovider -q`: 636 passed, 3 skipped (the 3 documented skips). Before this change:
   633 passed, 1 failed, 3 skipped.

## Files changed in this step

- `pieces/test_roster_config.py`: exception list, the exception test, the schema_version test.
- `scripts/source/fix_loop.py`: `"schema_version": 1` in the three status writers.
- `docs/changes/wp-d.md`: this file.

## Verification

- No model call, no paid call, no Opus call. No key under `secrets/` was read or printed.
- `pieces/prod_harness.py` and `pieces/test_loop_acceptance.py` are at their HEAD versions (both required, see step 3).
