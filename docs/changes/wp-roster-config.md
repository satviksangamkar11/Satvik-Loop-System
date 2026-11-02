# Package D: roster config (2026-11-02)

Base: main 8027d82. Branch: wp-roster-config.

## What changed

- `config/roster.json` (new): `schema_version` 1, two profiles, `test` and `production`. Each profile has the six roles `writer_a`, `writer_b`,
  `reviewer_a`, `reviewer_b`, `repair`, `planner`. Each role has `model`, `provider` (pinning, or null), `paid`, `pricing` (USD per token, paid
  OpenRouter roles only, or null) and `settings` (`temperature`, `reasoning`, `max_tokens`, `timeout_s`, `sampling`; writers also carry
  `retry_max_tokens` and `retry_timeout_s`).
- Profile choice: `SATVIK_PROFILE`, default `test`. The loader (`openrouter_call.load_profile`) refuses an unknown profile, a malformed
  roster, and any paid role under `test`. Refusal happens while the roster loads, so no client exists and no request can be sent.
- `openrouter_call.py`: `ROLES`, `ALLOWED`, `PAID`, `PINS`, `PRICING`, `SETTINGS` and `ROLE_SPECS` are read from the active profile. The
  per-model constants (LIMITS, XING_SAMPLING, GLM_SETTINGS, GLM_MAX_TOKENS, GLM prices, Z.AI pin, REVIEWER_BACKUP and its enable flag) are gone;
  their values now sit in the roster. Settings are looked up by the resolved model. Retry values apply to writer calls only, as before.
- `p1b_routing.py`: `panel_for(origin, unavailable, specs)` builds the panel from the reviewer roles, free reviewer first and the paid or
  `non_author_writer` slot last. A slot takes the free writer that did not write the code when its model is the author, is down, or is the
  `non_author_writer` role. A paid slot is never replaced. `Lineage.save` writes `"schema_version": 1` at the top level; `load` reads a file
  without a version as version 1 and refuses any other version.
- `fix_loop.py` (job schema only): `coder_model` and `reviewer_model` are removed from the job schema and from `load_job`. Both defaults were
  literal nemotron model IDs. A job that names either field is now refused by the schema.
- Tests: `pieces/test_roster_config.py` (new) holds the roster-validity, no-model-ID, panel-author, test-paid-refusal, production-unchanged and
  default-profile tests. `test_openrouter_call.py`, `test_openrouter_raw_errors.py`, `test_p1b_routing.py`, `test_loop_acceptance.py` and
  `prod_harness.py` are updated to the roster. The adapter tests set `SATVIK_PROFILE=production` before import, because they exercise the paid
  reviewer.

## Decisions

- Sentinel roster values are not model IDs: `non_author_writer` (test reviewer_a), `off` (test repair), `fake` (test planner). Repair and
  planner are paid `opus` in production, but they are not OpenRouter roles and are not in `ALLOWED` or `PAID`.
- The disabled Gemma backups are dropped. They were not in the production roster in the owner's list, and `REVIEWER_BACKUP_ENABLED` was off.
- The probe calls only `writer_a`, `writer_b` and `reviewer_b`, as before. The probe's `text` limits are gone, so the probe's timeout is now the
  role's `timeout_s` (1200 s for writers, 600 s for North). The probe passes `max_tokens=256` explicitly, so its token cap is unchanged.
- Under the test profile, if North is down and the only other free writer is already on the panel, the review is refused with
  `provider-unavailable`. The author is never filled in.

## Verification

- Full suite from the worktree: see the PR body for the counts.
- No paid call and no Opus call were made. No key value was read or printed.

## Not done in this package (needs other files)

- `status.json` `schema_version`: the status writers are in `scripts/source/p1b_production.py` (two writes) and `scripts/source/fix_loop.py`
  (the status dict and the invalid-spec writes). Neither is owned by package D.
- `test_no_model_id_string_appears_in_scripts_source_outside_the_roster` is red until these files are fixed by their owners: `coder_run.py`
  (`nvidia/nemotron`), `codex_review.py` (`gpt-6-luna`), `draft_source.py` (`deepseek-ai/deepseek-v4.1-flash`, `z-ai/glm-5.3`), `nemotron_code.py`
  (`nvidia/nemotron-3-ultra-550b-a55b`), `review_run.py` (two nemotron IDs), `opus_repair.py` and `plan_job.py` (`"opus"`), and the legacy
  loop in `fix_loop.py` outside the job schema (`claude-haiku-5-5`, `openai/gpt-oss-120b`, nemotron IDs, `gpt-6-luna`). The `openai/v1` hit in
  `fix_loop.py` is a URL path, not a model ID; the scan will need an exception for it or a narrower pattern.
- `opus_repair.py` `MODEL = "opus"` should read the roster's `repair` role. It is not owned here.
- Running production jobs now needs `SATVIK_PROFILE=production` in the environment, because the default is `test`.
