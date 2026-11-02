# Satvik Loop System

**Status: FROZEN (2026-10-30) — only owner-authorized bug fixes from here.** See `docs/LOOP_CLOSURE_REPORT.md` for the closure evidence.

An automatic code-writing loop. A spec goes in. Tested, reviewed code comes out. The supervisor only watches.

## How one job runs

1. **Write.** The coder reads the spec and writes the module and its test file (round 0). A job names its coder in `fixers`: `nemotron` (Nemotron 3 Ultra 550B, `nvidia/nemotron-3-ultra-550b-a55b`) or `haiku` (Claude Haiku 5.5 at high effort, called through the Claude CLI with no tools and no MCP servers; no API key). Since 2026-10-31 the Step 4 repair runs on Haiku.
2. **Test.** pytest runs on the job's test files.
3. **Fix.** If tests fail, the real pytest output goes back to the same coder named in `fixers`.
   - Tests that exist on disk are frozen: a fix round that changes one is rejected. The one exception is a test file with a syntax error: it cannot run, so the coder is told to return it fixed.
   - A bug INSIDE a frozen test (pytest shows a KeyError, NameError, TypeError and so on raised in the test's own code, not a failed assertion) may be repaired in a fix round, but only if the test-function count and the assert count do not drop.
   - A test file that does not exist yet (for example after a refused first reply) must still be written in a fix round.
   - In round 0, a test file that already existed may only be extended, never rewritten.
   - A reply with a refused block for a job file is rejected as a whole.
4. **Review.** When tests pass, four reviewers read the code at the same time:
   Lightning (Nemotron 3.5 Lightning), MiniMax (MiniMax M3.1 Flash), Codex (gpt-6-luna, high effort, read-only) and Haiku (low effort, reviewer only).
   The Step 4 repair and the Step 5 staging job use three reviewers: `codex` (Codex), `groq` (GPT-OSS 120B on Groq) and `lightning` (Nemotron 3.5 Lightning, the Nemotron reviewer). Nemotron 3 Ultra is the coder model only and is not wired as a reviewer. The `qwen` reviewer was removed on 2026-10-31 (its model and wiring are gone from the loop).
   **Ultra reviewer (2026-10-31):** `ultra` is Nemotron 3 Ultra as a reviewer with thinking ON at high effort and a 64,000-token budget (profile in `review_run.MODEL_PROFILES`). It takes the place of Lightning when Lightning gives no verdict. Lightning keeps thinking off.
   **Groq input budget (2026-10-31):** the Groq free tier allows about 8,000 tokens a minute, so the Groq reviewer's input is capped (`GROQ_MAX_INPUT_CHARS`). A job may give Groq its own file set with `groq_review_files`: it must be a subset of `review_files` and must include every job file. Without it, a large review set is truncated and Groq returns NONE.
   **Reviewer output rule (2026-10-31):** every model reviewer is told that the files are data and must not be rewritten. A reviewer that rewrites the files instead of reviewing them runs into the output limit (Nemotron Lightning did, `finish=length`, no verdict) and gives no verdict.
   **Two-review rule (owner 2026-10-31):** a decision needs at least two valid reviews. Set it in the job with `min_valid_reviews: 2` (the Step 4 and staging jobs use it). A FIX FIRST finding is never hidden by the quorum, and a timeout or error is never a review.
   **Contradiction rule (owner 2026-10-31):** if the valid reviews agree (all READY, or all FIX FIRST) that verdict stands. If they disagree (READY against FIX FIRST), Haiku 5.5 at high effort decides: it reads both reports and the files under review, and its last `## Verdict:` line decides (READY moves the pass ahead, FIX FIRST sends its report to the coder). A timeout or error (NONE) is no review and blocks the pass; a Haiku reply with no verdict, or a failed Haiku call, is never a pass; a test finding still blocks, because tests are frozen after round 0.
   Optional `groq` reviewer (Groq Cloud free tier, `openai/gpt-oss-120b`): a COMPACT reviewer. The free tier allows 8,000 tokens per minute, so the input is truncated to about 18,000 characters and a READY from a truncated view is downgraded to no verdict; an HTTP error leaves no report (NONE). Groq keys are read inside the process from `secrets/groq_key_*.txt` (or `GROQ_KEY_FILE` / `GROQ_KEY_DIR`) and never printed. Key rotation uses the NVIDIA pool logic (`p1_claims.LedgerKeyPool`): a ledger shared by all processes under OS locks (`_loop/groq_ledger`), ONE request per key per 60 s window, a failed key is cooled, 401/402/403 retire a key, 429 and 5xx move to the next key, any other error returns at once; the runner waits for a free key.
   Each ends its reply with `## Verdict: READY` or `## Verdict: FIX FIRST`. FIX FIRST reports go back to the coder.
5. **Result.** The job ends in one of these states:

| State | Meaning | Exit code |
|---|---|---|
| `passed` | tests pass, all reviewers READY | 0 |
| `passed-no-review` | tests pass, a required review gave no verdict | 5 |
| `review-open` | tests pass, FIX FIRST findings left when the rounds ran out (the last passing version is kept) | 1 |
| `needs_review` | three consecutive failed repairs with the IDENTICAL failing-test set: the job stops at once (the call budget limits cost, this limits repeated attempts at one defect). The last passing version is restored, the failing attempt is kept under `attempt/`, `NEEDS_REVIEW.md` lists the failing tests and the last pytest output. A pass resets the count; a job that never passed ends as `escalate` after the same three repairs. | 3 |
| `test-review-open` | tests pass, but a reviewer's FIX FIRST names a TEST file (tests are frozen after round 0, so the coder cannot repair it): the finding goes to `TEST_FINDINGS.md` for the observer, the coder is not called, and the job is never READY while a test finding is open | 1 |
| `invalid-spec` | pre-launch check failed (control characters such as a backspace, an unreadable spec, or contradictory test-edit permissions): no coder call, `INVALID_SPEC.md` | 1 |
| `escalate` | tests never passed: originals restored, `ESCALATE.md` written, Sonnet 5.5 writes `SONNET_DIAGNOSIS.md` | 3 |
| claim conflict | another job holds one of the files | 4 |
| reviewer CLI down | the Codex or Haiku CLI did not answer the start-up check; no coder call was made | 6 |

Warnings (owner 2026-10-30): module lines that look written for a test (`HACK`, `test compatibility`, `for the test`) are recorded in `WARNINGS.md` and in the round record. They never reject a reply; contract tests and code inspection decide acceptance.

## Parallel jobs

`pieces/p1_claims.py` is the launcher. Several jobs run at the same time:

- **File claims.** Each job locks its job file, its `_loop/<name>` folder, its files and its tests (OS byte-range lock). Two jobs cannot touch the same file. A dead process releases its lock by itself.
- **Shared key ledger.** `_loop/key_ledger.json` spreads calls over all API keys. Each key stays within the rate limit (10 calls per key per minute). A failed request cools that key for 1 minute and the next key is used.
- **Escalation.** `pieces/p1_escalate.py` asks Sonnet 5.5 (medium, read-only, no tools) for a short diagnosis: `CAUSE: SPEC`, `TEST` or `CODER`.
- Every result is appended to `docs/LOOP_LOG.md`.

## A job file

```json
{
 "name": "p1_example",
 "spec": "specs/example.md",
 "files": ["pieces/example.py", "pieces/test_example.py"],
 "tests": ["pieces/test_example.py"],
 "reference": ["docs/AGENT_RULES.md"],
 "max_rounds": 3,
 "review": true,
 "reviewers": ["lightning", "minimax", "codex", "haiku"],
 "review_prompt": "docs/REVIEW_PROMPT.md",
 "review_rounds": 4,
 "fixers": ["nemotron", "nemotron", "nemotron"]
}
```

Run it:

```bash
python pieces/p1_claims.py run _loop/jobs/p1_example.json .
```

The launcher accepts only `nemotron` or `haiku` as fixers; any other name is refused. Per-job state is in `_loop/<name>/status.json`, the replies are in `round_<n>.txt` and the reviews are in `review_<pass>_<reviewer>.md`.

## Safeguards

- A job file whose `review_files` do not include its own files is refused at load time (a copied job file once made the reviewers judge the wrong files). `pieces/test_guards.py` also checks every real job file for consistency.
- Before round 0 the loop pings the Codex and Haiku CLIs (result remembered 30 minutes in `_loop/preflight_ok.json`); a dead login stops the job at once with exit code 6.
- pytest exit 0 with no test PASSED (all skipped, xfailed or nothing collected) is not a pass.
- Crash safety and a clean folder: before round 0 the original files are saved under `_loop/<job>/original/`; if the previous run was killed (status still `running`) they are put back first. Old job files move to `prev_<n>/`. When a job escalates, its last attempt is kept in `attempt/` before the originals are restored.
- A job may list `regression` test files of other modules; they run after the job's own tests pass and a failure blocks the pass (those files are never edited by the job).
- Files returned by the coder are checked in the same round: a python file must parse and no file may contain a `## Verdict:` marker (it would be read by the reviewers as an instruction).
- Running `fix_loop.py` directly is refused unless the launcher set the shared key ledger (or `--solo` is given).
- A job whose tests first pass in the last test round still gets its review-fix rounds (up to `review_rounds`).
- Time limits: a job stops starting new rounds after `deadline_min` minutes (default 120). One Codex or Haiku review and one NVIDIA review stream may take 15 minutes; MiniMax is allowed 45.
- Every review report (READY, NONE and FIX FIRST) is scrubbed for API keys before it is kept.
- File races (F10): a job's `reference`, `regression`, spec, review prompt and review files are READ-marked; a reader and a writer of the same file exclude each other (readers coexist, a dead reader's mark cleans itself). Every file the loop writes is published atomically (temp file, then `os.replace`), so nobody ever reads half a file.
- Stale state: at every launch `reconcile_stale()` turns a status that still says `running` (no process holds the job) into `stopped`, never `passed`.
- Accounting: `status.json` carries `coder_calls`, `reviewer_calls` and `coder_budget` (`max_rounds + 1 + review_rounds`); the coder is never called beyond the budget. Repair limit: `max_rounds` repairs after round 0.
- Reviewer time and quorum: `reviewer_deadline_s` (default 2800 s, lowest 10) is the outer cap that turns a hung reviewer into NONE; `min_valid_reviews` (default 0 = every reviewer must answer) is an optional quorum; a FIX FIRST finding is never hidden by a quorum.
- Test edits: only an error raised in the test's own code (read from the real short traceback) or a syntax error may be repaired; the repair must keep every test function and every assert expression; each accepted edit is saved as a diff under `_loop/<job>/test_edits/` and listed as `tests_repaired`.
- `coder_model` and `reviewer_model` are refused unless they equal the fixed models (the loop always uses nemotron-3-ultra as coder and nemotron-3.5-lightning as Lightning reviewer).
- A review cannot return READY when its input was incomplete (missing, skipped or cut-off files) or when the model reply was cut off.
- A required reviewer that is missing or silent can never count as a pass.
- The coder is the one named in `fixers` (Nemotron or Haiku). Codex, Groq, Lightning, MiniMax and Haiku-as-reviewer only review. Haiku is also the tie-breaker when two reviewers contradict each other.
- API keys are read from a folder outside the repository (`NVIDIA_KEYS_DIR`, default `<repo parent>/secrets/nvidia`). Keys are scrubbed from every stored reply and report. No key is in this repository.
- Output goes to existing folders only. The loop does not create project folders.
- `pieces/test_guards.py` checks the recurring coder mistakes mechanically: swallowed `raise` inside `try/except`, Codex run through pipes, folder creation, a forbidden word, and the offline-only transcription switch.

## Layout

- `scripts/source/fix_loop.py`: the loop (rounds, tests, reviews, states)
- `scripts/source/review_run.py`: one NVIDIA reviewer call and verdict parsing
- `scripts/source/codex_review.py`: the Codex reviewer
- `scripts/source/nemotron_code.py`, `draft_source.py`, `coder_run.py`: coder calls, streaming, parsing of `FILE:` blocks
- `scripts/image_trials/nvidia_trial.py`: the key pool
- `pieces/p1_claims.py`, `pieces/p1_escalate.py`: launcher, claims, ledger, escalation
- `scripts/source/p1b_routing.py` and `scripts/source/p1b_production.py`: the production ladder and its runner (P1b; see the P1b section)
- `scripts/source/openrouter_call.py`: the OpenRouter adapter (P1a): the only path to OpenRouter (owner roster, free ledger, Z.AI-pinned repair budget, probe)
- `tools/codex_change_review.py`: read-only review of a change set (`CODEX_REVIEW_MODEL` sets the Codex model; `REVIEW_WITH=haiku` asks Haiku at low effort instead)
- `docs/REVIEW_PROMPT.md`: the prompt every reviewer receives
- `pieces/test_*.py`: the tests

This repository holds the loop code only. It expects the surrounding project layout: `specs/`, `_loop/jobs/`, `docs/` and `docs/AGENT_RULES.md`.

## Tests

```bash
python -m pytest pieces -p no:cacheprovider -q
```

Test files: `test_loop_acceptance.py`, `test_loop_races.py`, `test_fix_loop_review.py`, `test_guards.py`, `test_p1_claims.py`, `test_p1_escalate.py`, `test_keypool_rule.py` (about 150 tests across the loop files; they must be run with the repository's pieces folder).

The loop was reviewed by Codex (gpt-6.1-sol and gpt-6-luna, medium) and tested live with four reviewers. A small job passed in 32 seconds.

## Known limits

- Job files are still written by hand (validated at load time).
- `yaml` and `jsonschema` are third-party dependencies of `fix_loop.py`.
- The coder runs with thinking on (owner rule).
- `pieces/test_guards.py` checks the surrounding Reverse-Video project (modules, rules file, job files); run it inside the full project.
- A re-run on files that already exist cannot rewrite the existing tests (append only), so re-runs need new file names or a wiped state.
- Haiku and Codex need their CLI logins. If one is signed out, its review is empty and the job ends with exit code 5.
- A job launched from a shell that closes right afterwards dies with that shell. Run it as a background task.
- The spec check's second rule ('existing tests are frozen' but no test file of the job exists) can misfire when the frozen tests belong to other files; worked around by wording.

## Changes after the freeze

| Commit | Change |
|---|---|
| ba3a2e7 | A job stops after three consecutive failed repairs with an IDENTICAL failing-test set (state `needs_review`, `NEEDS_REVIEW.md`, last passing version restored, the failing attempt kept under `attempt/`; a pass resets the count; a job that never passed ends as `escalate`). |
| 9328281 | A reviewer finding that names a test file is routed to `TEST_FINDINGS.md` for the observer, never to the coder (tests are frozen after round 0), and the job ends `test-review-open` and is never READY while one is open; module lines that look written for a test are only a warning (`WARNINGS.md`); a pre-launch spec check rejects control characters (for example a backspace made by a regex written as `\b` in a normal string), an unreadable spec, and contradictory test-edit permissions (state `invalid-spec`, `INVALID_SPEC.md`, no coder call). |
| fcfbf13 | Codex reviewer set to `gpt-6.1-sol` at low effort (later changed to `gpt-6-luna` at high effort by owner decision, see the next row). |
| (owner 2026-10-30) | Codex reviewer set to `gpt-6-luna` at HIGH effort (read-only); job files may list `codex` together with `groq` and `qwen`. |
| (owner 2026-10-31) | Two-review rule: `min_valid_reviews: 2` in the job; Nemotron 3.5 Lightning added as a reviewer for the staging job (three reviewers: codex, groq, lightning). |
| (owner 2026-10-31) | Haiku 5.5 high as coder (job `fixers: ["haiku"]`, through the Claude CLI: `--tools ""`, `--strict-mcp-config` with an empty MCP config, `--no-session-persistence`; a reply counts only with status 200). Haiku also decides when two reviewers contradict. Qwen removed from the loop. Reviewer set of the Step 4 repair: `codex` and `groq`. |
| 7287a89, 1cbab19 | Added the `groq` and `qwen` compact reviewers on Groq Cloud free tier. |
| (P0 2026-11-02) | Production profile (`"profile": "production"`): locked acceptance tests, negative controls, sandboxed pytest with per-test JUnit verdicts, strict two-READY review, tested = reviewed = delivered hash. Judge can no longer stand in for a missing review (every profile). Legacy jobs need `--legacy` / `SATVIK_ALLOW_LEGACY=1`. See "Production jobs (P0)" below. |
| 7ce7465 | Groq key rotation using the NVIDIA pool logic (`p1_claims.LedgerKeyPool`): shared ledger under OS locks (`_loop/groq_ledger`), one request per key per 60 s window, failed key cooled, 401/402/403 retire key, 429/5xx move to next key, other errors return at once, runner waits for a free key. |

## Production jobs (P0, 2026-11-02)

A job with `"profile": "production"` closes the false-pass and execution holes. A job without `profile` is **legacy** and the CLI refuses it unless
`--legacy` is passed (or `SATVIK_ALLOW_LEGACY=1` is set), so the unsafe mode is never the default.

```json
{
 "name": "calc", "profile": "production", "spec": "specs/calc.md",
 "files": ["pieces/calc.py", "pieces/test_calc.py"], "tests": ["pieces/test_calc.py"],
 "reviewers": ["codex", "groq"],
 "acceptance_tests": ["tests/acceptance/test_calc_acceptance.py"],
 "negative_controls": [{"name": "returns None", "replace": {"pieces/calc.py": "tests/controls/calc_returns_none.py"},
                        "must_fail": ["tests/acceptance/test_calc_acceptance.py::test_sums_two_prices"]}],
 "acceptance_baseline_fail": []
}
```

- **Review.** Every required reviewer must give a valid READY, at least two distinct reviewers, no lowered quorum, `review: false` refused. The
  tie-breaker is not used in production; in legacy it may settle READY-vs-FIX FIRST only when the quorum is really met, never fill a missing review.
  One function decides: `p0_gates.decide_review`.
- **Locked acceptance tests.** `acceptance_tests` are controller-owned: never in `files`, mounted read-only, hash-locked with every `conftest.py`
  above them, the root pytest config files, the regression tests and the negative-control files (`_loop/<job>/acceptance_lock.json`). A change at
  any time ends the job `acceptance-tampered` and restores the originals. The coder's own tests are evidence only: a pass needs the acceptance tests.
- **Proof the tests can fail.** Before any coder call: each `negative_controls` entry replaces job files with a known-wrong implementation and its
  `must_fail` tests must fail; each `acceptance_baseline_fail` test (bug regressions) must fail on the unchanged code. Only the named tests must
  fail; valid invariants may pass. A test that is not collected does not count. A `must_fail` test counts only when it fails with an
  `AssertionError` or pytest's own failure (`pytest.fail`, `DID NOT RAISE`); a NameError, AttributeError, ImportError, SyntaxError or setup error
  does not count. Otherwise: `acceptance-invalid`, no coder call.
- **Per-test verdict.** pytest writes JUnit XML; every test defined in the run's files must be collected and pass. A skip, an xfail (run with
  `--runxfail`), an error, a collection error, a missing test, zero tests, a non-zero exit code or a timeout is not a pass.
- **Sandbox.** `scripts/source/sandbox_runner.py`: Docker, `--network none`, non-root, `--cap-drop ALL`, `no-new-privileges`, read-only root,
  CPU/memory/PID limits, a disposable copy of the needed folders (never `_moss/`, `_loop/`, `secrets/`, `.git`, key or `.env` files), no host
  environment, no Docker socket, controller-owned pytest config (`-c`), `python -I`. A timeout kills the whole container. Before round 0 a
  confinement self-test runs; if Docker, the image or confinement fails the job ends `sandbox-unavailable` with no coder call. There is no
  unrestricted fallback. Build the image: `docker build -t satvik-pytest-sandbox:latest tools/sandbox` (add the project's test dependencies).
- **Evidence.** `result["evidence"]`: `tested_hash == reviewed_hash == final_hash`, the acceptance lock and each review decision. Code that changed
  while it was reviewed ends `integrity-mismatch` and the tested version is restored.

New states (exit code 1): `acceptance-invalid`, `acceptance-tampered`, `sandbox-unavailable`, `integrity-mismatch`.

## Production run through P1b (2026-11-02)

A job with `profile: production` runs one unit through `p1b_routing.walk()`, and every model call goes through `openrouter_call` by role.
`fix_loop.run_job` dispatches it to `scripts/source/p1b_production.py` after the P0 setup (sandbox self-test, locked acceptance tests,
calibration). The legacy loop (nemotron, codex, haiku, groq, minimax, lightning) is not reached by a production job; `caller`, `callers`
and `reviewer` are refused for production, and a static test checks that the P1b modules name no legacy model family.

- **Job shape.** `fixers` are the role names `writer_a` and `writer_b`; `reviewers` is the string `by_lineage`; `visibility` is `public`;
  `budget` is `{free_requests: 40, paid_usd: 0.15}`; `deadline_min` is 60 or 120; `files` at most 800 lines each and 1,500 in total
  (`check_job_size`, checked before the sandbox runs). The legacy fields (`reviewers` as a list, `min_valid_reviews`) are refused for production.
- **Ladder.** Dots writes; a localized red (at most 3 failing tests in at most 2 files, not systemic) gets one Dots repair; otherwise the files
  are restored and Xing writes fresh (every job file required), with one Xing repair if still red; then the GLM gate: at most 2 GLM rounds,
  only for a defect both free writers failed on the same test, and only while the budget fits.
- **Reviewers.** The lineage panel from `p1b_routing.panel_for`: code last written by Dots is reviewed by Xing and North; by Xing, by Dots and
  North; after a GLM repair the origin decides. Each file keeps `{last, origin}` in `_loop/<job>/lineage.json`.
- **Tests decide.** A green run is the job's tests plus the locked acceptance tests passing in the sandbox, with the locked files unchanged. The
  files that passed are hashed; the review hashes and the final hash must equal that hash, or the state is `integrity-mismatch`.
- **States.** `passed`, `review-open` (a reviewer's FIX FIRST with evidence; the FIX FIRST repair loop is not built yet, so the report is kept in
  `REVIEW_FIX.md`), `escalate`, `blocked`, `provider-unavailable`, `deadline`, `acceptance-tampered`, `integrity-mismatch`, `job-too-large`.
  Anything that did not pass is restored to the originals and explained in `ESCALATE.md`.
- **Tests.** `pieces/test_p1b_production_run.py` runs the Dots, Xing, GLM order end to end through the real Docker sandbox with scripted
  models (`pieces/prod_harness.py`); it skips with the reason when the sandbox is not available. Nothing calls the network or the paid model.
- **Not done.** The FIX FIRST repair loop; the frozen-test rule of the legacy loop is not applied to the P1b ladder (the locked acceptance tests
  are the protection); no live run.

## Opus planner (P1c, 2026-11-02)

`scripts/source/plan_job.py` plans one job with a single Opus call. Nothing calls it live yet: the tests use `pieces/fake_claude.py`.

- **One call per job.** `plan/INTENT.json` is written before the call; the raw answer goes straight to `plan/raw_response.json`. A restart with a
  saved answer parses it and makes no call. An intent with no saved answer is `planner-uncertain`; it is never retried automatically. A timed-out
  call is `planner-uncertain` too.
- **The call.** `claude -p --model opus --effort medium --output-format json --tools "" --mcp-config <empty file> --strict-mcp-config`, from an
  empty temporary folder, prompt on stdin, `ANTHROPIC_API_KEY` removed from its environment, never `--bare`. The flags were checked against the installed
  `claude --help` (no model call): `-p`, `--output-format json`, `--tools ""`, `--mcp-config`, `--strict-mcp-config`, `--effort`, `--model`.
- **Refused.** Any MCP server or plugin reported by the session; more than 30 files sent; an answer that fails the production rules
  (`check_production_job`, `production_job_problems`, the job schema); a spec line without a `KNOWN:` or `ASSUMED:` tag; acceptance files that are
  not exactly the job's acceptance tests; a missing negative control source; a unit over 800 lines per file or 1,500 in total.
- **Output.** `plan/PLAN.md`, `plan/SPEC.md`, `plan/job.json` (read-only, its SHA-256 is returned), `plan/expected_lines.json`, and the acceptance and
  control files under `plan/files/`. The result lists the ASSUMED lines.
- **Not done.** The loop does not start from a planned job yet; the owner's approval of ASSUMED lines is not a gate in code yet; no live run.

## OpenRouter adapter (P1a, 2026-11-02)

`scripts/source/openrouter_call.py` is the only path to OpenRouter. A production job calls it through `p1b_production.py` (see the P1b section).
Every reply is checked before it counts, and no failure falls back to another model or key.

- **Roster (owner, 2026-11-02).** Roles: `writer_a` = `dots-studio/dots-3-note-preview:free`, `writer_b` = `xingchen-agi/xing4.0-29b-a4b:free`,
  `reviewer` = `cohere/north-mini-code:free`, `repair` = `z-ai/glm-5.3-flash` (the only paid ID). The reviewer backup
  (`google/gemma-4-31b-it:free`, `google/gemma-4-26b-a4b-it:free`) is disabled: only the probe may call those IDs. Refused before any request:
  `openrouter/free`, a `models` list, a bare paid ID, a free ID without `:free`, a disabled backup role, any other ID.
- **Limits.** Writer and repair: `max_tokens` 40000, timeout 1200 s; a retry uses 72000 and 1800 s. Reviewer: 16000 and 600 s. Every request
  sends `reasoning.max_tokens` 8000; the provider does not enforce it, so reasoning tokens are recorded (`reasoning_tokens`), never assumed capped.
  Xing sends temperature 0.8, top_p 0.95, repetition_penalty 1.05.
- **Free quota.** One account-wide ledger (`_loop/openrouter/openrouter_free_ledger.json`) under the same `key_ledger.lock` as the key ledger.
  Every attempt is reserved before it is sent, failed attempts included. 15 a minute. `begin_job()` saves each key's full `GET /api/v1/key`
  JSON under `_loop/openrouter/key_snapshots/` and uses `free_model_daily_requests` (limit, remaining) when present; when absent it records that
  and uses the local cap of 1000 for a paid-tier key (`is_free_tier` false) or 50 otherwise. A 429 that names the day is `QuotaExhausted`; a
  per-minute 429 waits `Retry-After`; any other 429 is a provider error, never a sign the day is used up.
- **Repair (paid).** The paid key only. Pinned to the Z.AI endpoint (`provider: {"order": ["z-ai"], "only": ["z-ai"], "allow_fallbacks": false}`); the endpoint
  and prices are in `docs/p1a_glm_endpoint.json`. The cheapest-endpoint choice is removed. A reply from any other provider is unavailable and its
  spend still counts. Limits per job: 2 logical repair rounds, 4 physical paid attempts (retries included), $0.15 ceiling. Each attempt reserves
  its estimate before it is sent; a missing Z.AI price or endpoint sends nothing. Nothing in this section has been run live.
- **Replies.** The reply's `model` must equal the request. `finish_reason` must be `stop`. A writer needs a `FILE:` block; a reviewer needs a
  final `## Verdict: READY` or `## Verdict: FIX FIRST` line. A cut-off reply is retried once at the retry limit (writer and repair: 72000).
- **Retries and breaker.** Three retries after the first attempt (2 s, 8 s, 30 s, plus jitter; `Retry-After` is honoured up to 120 s).
  Two provider errors in a row drop the model for the job. 401 and 402 block the account and are never retried. 403 is a refusal of that
  model only: the model is dropped for the job, and the other roles keep working (`ModelDenied`).
- **Private code.** Blocked. `APPROVED_PRIVATE` is empty because no non-training route has been approved.
- **Keys.** `OPENROUTER_FREE_KEY` and `OPENROUTER_PAID_KEY` from the environment, or `secrets/openrouter_free_key.txt` and
  `secrets/openrouter_paid_key.txt`. Keys are never printed or written; error text is redacted.
- **Raw errors.** Every failed attempt is kept in `failed_attempts`: model, HTTP status (`null` for a network error) and the first 500 characters
  of its message. A dropped model's error repeats the last two raw failures. The probe writes them in full for each model.
- **Probe.** `python scripts/source/openrouter_call.py probe --out <file> [--models a,b]` makes one live free call each to writer_a, writer_b,
  reviewer and the two Gemma backups (or only the named ones), and records the key state before and after. It never calls the repair model.
  It spends free requests, so run it by hand and only after the owner's go.
- **Results (2026-11-02).** `docs/p1a_roster_probe.json`: writer_a, writer_b and reviewer answered at $0. `docs/p1a_gemma_rerun.json`: both Gemma
  backups returned HTTP 429 "upstream shared pool" (Google AI Studio, "temporarily rate-limited upstream"), the expected result, now recorded
  in full. Gemma stays disabled.
- **Sandbox credential check.** The container probe refuses any environment variable whose name contains a credential word (`KEY`, `TOKEN`,
  `SECRET`, `PASSWORD`, ...), except `GPG_KEY`, which the base image sets. One rule, `sandbox_runner.CREDENTIAL_RULE`, is used on the host and in
  the container (`pieces/test_sandbox_credential_names.py`).
- **Ignored paths.** `.gitignore` keeps `secrets/`, `_loop/` and `.env` out of git.
