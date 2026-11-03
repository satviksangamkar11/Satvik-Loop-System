# Satvik Loop System

**Status: FROZEN (2026-10-30) — only owner-authorized bug fixes from here.** See `docs/LOOP_CLOSURE_REPORT.md` for the closure evidence.

**Revision 4 (2026-11-03):** the production loop is closed on the test profile. The pilot passed, 2 loops merged 3 jobs in order, and 4 loops ran 4 jobs at once and merged them (`fl-top` escalated; rerun pending). All runs cost $0 and made 0 Opus calls. As built: `docs/ARCHITECTURE_REVISION_4.md`. UI contracts: `docs/UI_ENGINE_HANDOFF.md`. Day log: `docs/STATUS_2026-11-03.md`.

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
- `scripts/source/openrouter_call.py`: the OpenRouter adapter (P1a): the only path to OpenRouter (owner roster, free ledger, Z.AI-pinned GLM review budget, probe)
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
  `budget` must be exactly `{free_requests: 40, review_usd: 0.05, paid_usd: 0}` (`p1b_routing.check_production_job`; any other value is refused,
  including the old `{free_requests: 40, paid_usd: 0.15}`); `deadline_min` is 60 or 120; `files` at most 800 lines each and 1,500 in total
  (`check_job_size`, checked before the sandbox runs). The legacy fields (`reviewers` as a list, `min_valid_reviews`) are refused for production.
- **Ladder.** Dots writes; a localized red (at most 3 failing tests in at most 2 files, not systemic) gets one Dots repair. Otherwise the files
  are restored and a fresh write goes to Xing (every job file required), then to North only if Xing is down; a red fresh Xing write gets one
  Xing repair. Then the repair step: at most 2 repair calls (`REPAIR_CALLS`), each one isolated Opus call (see the final roster section), only
  when the gate passes (the same failing tests, and the job deadline fits a repair call: `REPAIR_TIMEOUT_S` 1800 s). While the repair step is
  off (the default) the job escalates with "Opus repair unavailable" and no call is made.
- **Reviewers.** The panel from `p1b_routing.panel_for`: GLM first, then North. If North is down, the free writer that did not write the code
  takes that slot (`backup_writer`). A GLM failure in review ends the job `provider-unavailable`; there is no substitute reviewer. Each file
  keeps `{last, origin}` in `_loop/<job>/lineage.json`.
- **Tests decide.** A green run is the job's tests plus the locked acceptance tests passing in the sandbox, with the locked files unchanged. The
  files that passed are hashed; the review hashes and the final hash must equal that hash, or the state is `integrity-mismatch`.
- **States.** `passed`, `review-open` (a reviewer's FIX FIRST with evidence; the FIX FIRST repair loop is not built yet, so the report is kept in
  `REVIEW_FIX.md`), `escalate`, `blocked`, `provider-unavailable`, `deadline`, `acceptance-tampered`, `integrity-mismatch`, `job-too-large`.
  Anything that did not pass is restored to the originals, the authorized candidate files are kept under `candidate/`, and the reason is
  written to `ESCALATE.md` (or `EXCEPTION.md` for an unexpected error). See the pre-pilot section below.
- **Tests.** `pieces/test_p1b_production_run.py` runs Dots, Xing, a repair through a fake claude, then the GLM and North review end to end through the real Docker sandbox with scripted
  models (`pieces/prod_harness.py`); it skips with the reason when the sandbox is not available. Nothing calls the network or the paid model.
- **Not done.** The FIX FIRST repair loop; the frozen-test rule of the legacy loop is not applied to the P1b ladder (the locked acceptance tests
  are the protection); no live run.

## Pre-pilot review fixes (2026-11-02)

Five fixes from the pre-pilot bug review. Each was written as a failing test first (`pieces/test_pre_pilot_review.py`, no Docker, fake sandbox).

- **A fresh writer gets no earlier pytest output.** `restore()` clears the output a writer sees. Every test run's output is kept under
  `_loop/<job>/history/test_NN.txt`, never in a prompt.
- **Nothing unaccepted stays in the checkout.** Every state other than `passed` (`review-open`, `integrity-mismatch`, `escalate`, `exception`
  and the rest) snapshots the job's own files to `candidate/`, restores the originals, and writes its reason. An unexpected exception is caught,
  so it is a state (`exception`), not a crash.
- **A killed worker is recovered on the next launch.** The job writes `status.json` with state `running` before its first file write. The next
  `run_job` finds `running` with the originals saved and restores them before any model call (`fix_loop._prepare_job_folder`). The abandoned
  candidate is not used: the restart's first writer sees the originals only. (A kill cannot be handled inside the process; this recovery is what
  covers SIGKILL.)
- **Budget (final roster, 2026-11-02).** The production budget is exactly `{free_requests: 40, review_usd: 0.05, paid_usd: 0}`, passed to the
  `JobBudget` by `client_for(root, job)`. The old `paid_usd` 0.15 form is refused. The GLM reviews stop at `review_usd`.
- **One key for both roles.** With only one key file or variable, the free and the paid roles use it. The paid allowlist (`resolve`) and the
  dollar budget still apply; an unlisted paid model is refused before any request.
- **Suite.** Three `test_guards` checks read files that belong to the Reverse-Video project and are absent from this repository (`p3_audio_evidence.py`,
  `docs/AGENT_RULES.md`, and the real job files). They skip with that reason and run in full wherever the file exists. Nothing else was skipped.

## Review package and FIX FIRST evidence (pre-pilot patch, 2026-11-02)

Patch Steps 1 to 3 (`p1b_production.build_package`, `p1b_routing.evidence_ok` and `_review_one`); tests in `pieces/test_p1b_review_package.py`
and `pieces/test_p1b_routing.py`.

- **One request per reviewer, nine sections in this order.** 1 SPEC (every non-heading spec line, numbered `SPEC n`; `KNOWN:` and `ASSUMED:`
  lines included). 2 DIFF (each job file against its original; `NEW FILE`, `DELETED`). 3 FILES (implementation files in full, with line numbers;
  an unchanged one is marked `(unchanged)`). 4 TESTS (`[JOB TEST]` or `[LOCKED ACCEPTANCE]`, with line numbers). 5 RESULTS (every expected test
  as passed, failed, skipped or missing, from the JUnit file of the last green run). 6 STATIC WARNINGS (advisory: a literal that job code and a
  test both compare against; never decides a job). 7 CONTEXT (direct imports and reference files first, then callers including test callers,
  smallest first). 8 OMITTED (every optional file left out, by name). 9 CHECKS (`CHECK 1` to `CHECK 5`, then the verdict line).
- **Size.** Sections 1 to 6 and 9 are mandatory. If they do not fit `PACKAGE_LIMIT` (100,000 characters) the job ends `blocked` with the reason
  "review package exceeds the prompt limit", before any reviewer call, and the originals are restored. Only CONTEXT is cut.
- **Never read.** Anything that resolves outside the repo or into `_loop/`, `_moss/`, `secrets/`, `.git/`, a `.env` file or a key file, including
  symlinks and Windows junctions into those places.
- **FIX FIRST counts only with evidence.** It needs a `file:line` naming a file of the package with a line no greater than the file's length, and
  `SPEC n` (n within the spec's requirement count) or `CHECK n` (n from 1 to 5). A FIX FIRST without it gets one re-ask from the same reviewer.
- **Routing.** A failed call or a reply with no verdict line: the backup takes the slot, with no re-ask. A FIX FIRST still without evidence after
  the re-ask, or a re-ask that answers READY, ends `review-open`; no backup and no later READY overrides it. A valid FIX FIRST ends `review-open`
  (no repair loop yet). PASS needs READY from every reviewer on the panel, and the reason lists the reviewers that returned READY, a backup included.
- **Candidate.** A stop before any write keeps no `candidate/` (it is kept only when a job file on disk differs from its original).
- **Not done.** The repair loop for a valid FIX FIRST.

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

- **Roster (final, owner 2026-11-02).** Roles: `writer_a` = `dots-studio/dots-3-note-preview:free` (Dots), `writer_b` = `xingchen-agi/xing4.0-29b-a4b:free` (Xing), `reviewer_a` = `z-ai/glm-5.3-flash` (GLM, the only paid ID, reviewer only), `reviewer_b` = `cohere/north-mini-code:free` (North). The old `reviewer` and `repair` roles are gone. The reviewer backup
  (`google/gemma-4-31b-it:free`, `google/gemma-4-26b-a4b-it:free`) is disabled: only the probe may call those IDs. Refused before any request:
  `openrouter/free`, a `models` list, a bare paid ID, a free ID without `:free`, a GLM request under any role but `reviewer_a`, a disabled
  backup role, any other ID.
- **Limits.** Writers: `max_tokens` 40000, timeout 1200 s; a retry uses 72000 and 1800 s. Reviewer: 16000 and 600 s. Every request
  sends `reasoning.max_tokens` 8000; the provider does not enforce it, so reasoning tokens are recorded (`reasoning_tokens`), never assumed capped.
  Xing sends temperature 0.2, top_p 0.95, repetition_penalty 1.05.
- **Free quota.** One account-wide ledger (`ledger/limits.json` in the loop home, `shared_limits.py`) under the loop home lock.
  Every attempt is reserved before it is sent, failed attempts included. 15 a minute. `begin_job()` saves each key's full `GET /api/v1/key`
  JSON under `_loop/openrouter/key_snapshots/` and sets the day's free cap from the free key's `free_model_daily_requests.limit`, the account's
  live daily allowance. When that read fails or the field is missing, the cap is the fallback 1000 (`shared_limits.FREE_DAILY_CAP`). The
  summary's `cap_source` says which applied. A live cap stands until the next read. A 429 that names the day is `QuotaExhausted`; a
  per-minute 429 waits `Retry-After`; any other 429 is a provider error, never a sign the day is used up.
- **Paid reviewer (GLM).** The paid key only, reviewer role only (`reviewer_a`). Pinned to the Z.AI endpoint (`provider: {"order": ["z-ai"], "only": ["z-ai"], "allow_fallbacks": false}`); the endpoint and prices are in `docs/p1a_glm_endpoint.json`. A reply from any other provider is unavailable and its spend still counts. The job's paid ceiling is `review_usd` 0.05. Each call reserves its worst case before it is sent (`ceil(chars/3) x 0.15e-6 + max_tokens x 0.50e-6`); a call that would pass the ceiling is refused with nothing sent, and the reservation is settled to the reported cost. A missing Z.AI price or endpoint sends nothing. Nothing in this section has been run live.
- **Replies.** The reply's `model` must equal the request. `finish_reason` must be `stop`. A writer needs a file header (`FILE: path`, `### FILE: path` or `**FILE:** path`); a reviewer needs a
  final `## Verdict: READY` or `## Verdict: FIX FIRST` line. A cut-off write is retried once at the retry limit (72000). A repair call uses the
  profile's `repair_max_tokens` (10000) and never retries at a bigger size.
- **Repair edits.** A writer repair asks for SEARCH/REPLACE blocks under `FILE: path`. A block applies on one exact match of its SEARCH text, or on one
  match after whitespace normalization; zero or several matches refuse the reply. A refused reply is asked again once, with the refusal and a
  6-line example; a second refusal falls back to the whole-file request. Fresh writes stay whole-file (`p1b_production.apply_edits`).
- **Raw replies.** Each writer and repair reply is kept scrubbed in `history/reply_NN.txt`, its reasoning in `history/reasoning_NN.txt`, both linked
  from `history/call_NN.json` as `reply_file` and `reasoning_file`.
- **Retries and breaker.** Three retries after the first attempt (2 s, 8 s, 30 s, plus jitter; `Retry-After` is honoured up to 120 s).
  Two provider errors in a row drop the model for the job. 401 and 402 block the account and are never retried. 403 is a refusal of that
  model only: the model is dropped for the job, and the other roles keep working (`ModelDenied`).
- **Reviewer 429 window (patch, 2026-11-02).** An upstream 429 on a reviewer call (the provider is busy; a daily-limit 429 still stops at once,
  and a per-minute 429 keeps its wait) is retried on its own schedule: 15, 30, 60, 120, 180 s, plus up to 1 s of jitter, or `Retry-After` when
  that is longer. At most 5 retries (about 7 minutes). The window ends before the 600 s review timeout (counted from the call's start): if the
  next wait would pass it, the retries stop. Upstream 429s inside the window do not count toward the breaker; the model is dropped only when the
  window is used up. Before each retry the job's 40 free requests and the account's daily count must leave room, and no retry starts after the
  unit deadline (`set_job_deadline`, set by `p1b_production.run` from `deadline_min`). Every retry is logged in the job's status
  (`evidence.openrouter_retries`: model, attempt, wait in seconds, scrubbed error). Writers keep the old schedule (2 s, 8 s, 30 s): the breaker
  drops a writer after two errors in a row, so its 30 s step is never reached.
- **Private code.** Blocked. `APPROVED_PRIVATE` is empty because no non-training route has been approved.
- **Keys.** `OPENROUTER_FREE_KEY` and `OPENROUTER_PAID_KEY` from the environment, or `secrets/openrouter_free_key.txt` and
  `secrets/openrouter_paid_key.txt`. Keys are never printed or written; error text is redacted.
- **Raw errors.** Every failed attempt is kept in `failed_attempts`: model, HTTP status (`null` for a network error) and the first 500 characters
  of its message. A dropped model's error repeats the last two raw failures. The probe writes them in full for each model.
- **Probe.** `python scripts/source/openrouter_call.py probe --out <file> [--models a,b]` makes one live free call each to writer_a, writer_b,
  reviewer_b (North) and the two Gemma backups (or only the named ones), and records the key state before and after. It never calls GLM or Opus.
  It spends free requests, so run it by hand and only after the owner's go.
- **Results (2026-11-02).** `docs/p1a_roster_probe.json`: writer_a, writer_b and reviewer answered at $0. `docs/p1a_gemma_rerun.json`: both Gemma
  backups returned HTTP 429 "upstream shared pool" (Google AI Studio, "temporarily rate-limited upstream"), the expected result, now recorded
  in full. Gemma stays disabled.
- **Sandbox credential check.** The container probe refuses any environment variable whose name contains a credential word (`KEY`, `TOKEN`,
  `SECRET`, `PASSWORD`, ...), except `GPG_KEY`, which the base image sets. One rule, `sandbox_runner.CREDENTIAL_RULE`, is used on the host and in
  the container (`pieces/test_sandbox_credential_names.py`).
- **Ignored paths.** `.gitignore` keeps `secrets/`, `_loop/` and `.env` out of git.

## Final roster patch (2026-11-02)

Owner's final patch, branch `p1-final-roster`: GLM as reviewer only, an isolated Opus repair step, then the pilot. Every test uses fakes.

- **Roster.** Dots writes (`writer_a`), Xing writes (`writer_b`), GLM reviews only (`reviewer_a`, paid), North reviews (`reviewer_b`).
- **Repair step (Opus).** `scripts/source/opus_repair.py` runs one isolated call per repair: `claude -p --model opus --effort medium --output-format json --tools ""`
  with an empty MCP config, from an empty folder, with no Anthropic key and no OpenRouter variable in its environment. It is OFF by default
  (`OPUS_ENABLED = False`): while off no process starts and the job escalates with "Opus repair unavailable". Tests use `pieces/fake_claude.py`
  or an injected runner; no test runs the real CLI, and the owner has not switched the step on.
- **Budget.** `{free_requests: 40, review_usd: 0.05, paid_usd: 0}`. Each GLM call is checked against its reservation before it is sent.
- **Evidence.** `status.json` carries `evidence.budget`: free requests used, review dollars spent, repair calls, and whether the repair step was on.
  Each repair reply is saved raw, scrubbed, as `repair_step_NN_raw.json` in the job's history folder.
- **Not done and open issues.**
  - The pilot (`pilot-loop-status`, step 6) was not run: its spec, acceptance test and negative control are not in the repo.
  - `scripts/source/plan_job.py` (the planner, left unchanged) still writes the budget `{free_requests: 40, paid_usd: 0.15}` in its prompt; a plan
    with that budget is refused by `check_production_job`. Owner decision needed.
  - The gate needs the job deadline to fit a 1800 s repair call, so in 60-minute units the repair step rarely runs.
  - The planner's Opus runner was not shared with `opus_repair.py`: `plan_job.py` keeps the OpenRouter variables in its environment and was not touched.
  - Flint and Inkling are not in the code; there was nothing to disable.
  - No live run of any path.

## Patch 6: token settings, review order, planner budget, Opus switch (2026-11-02)

Every change below has a failing-first test (`pieces/test_p6_token_settings.py` and the updated P1b tests). Tests use fakes only.

- **Review order.** North reviews first. GLM is called only after North says READY. A valid North FIX FIRST ends the job review-open and GLM is never called. If North fails, the backup free writer takes its place, then GLM (`p1b_routing.panel_for`).
- **GLM request.** `reasoning: {effort: "high", exclude: true}`, `temperature: 0.2`, `max_tokens: 16000`, no `reasoning.max_tokens`. A cut-off GLM reply is not retried with more tokens. Free requests keep their settings (`reasoning.max_tokens: 8000`, temperature 0.1; Xing keeps its sampling).
- **Reviewer prompt.** At most 5 findings, each one line `- file:line [SPEC n|CHECK n]: <under 25 words>`, no code, no praise, one example, the verdict line last (`p1b_production.REVIEW_SYSTEM`).
- **Repair input.** Only the failing tests of the last JUnit run: id, assertion line, captured stdout and stderr, and the source of the test function. Identical failures are merged. Capped at 6,000 characters (`p1b_production.failing_summary`). The raw pytest output is no longer sent to a writer.
- **Opus call.** `--system-prompt` (a repair-only prompt), `--no-session-persistence`, `CLAUDE_CODE_MAX_OUTPUT_TOKENS=32000`, timeout 900 s. A repair runs only when at least 900 s (15 minutes) of the unit deadline are left (`p1b_routing.REPAIR_TIMEOUT_S`).
- **Switch.** `OPUS_ENABLED` stays `False`; the owner decides the production switch. The "Reply OK" check ran on Haiku 5.5 at high effort (`--model haiku --effort high`), not on Opus, once with and once without `--system-prompt` (`input_tokens` 2 in both). `pieces/conftest.py` keeps the switch off during tests, so no test starts the real CLI.
- **Planner budget.** The `plan_job.py` prompt asks for `{free_requests: 40, review_usd: 0.05, paid_usd: 0}`.

## Any model on any role (2026-11-03)

`config/roster.json` is the one place a role's model is chosen. A role says how it is called with `"via"`: `"openrouter"` (the default when
the key is absent) or `"claude_code"`, which also needs `"effort"`. Example: `"repair": {"via": "claude_code", "model": "sonnet", "effort": "high",
"provider": null, "paid": true, "pricing": null, "settings": {...}}`.

- **Claude Code names.** `model` is one of opus, sonnet, haiku, fable; `effort` is one of low, medium, high, xhigh, max
  (`scripts/source/claude_code.py` holds this vocabulary; no role's model is written in code).
- **Test profile.** A Claude Code role is allowed only as haiku at effort high; anything else is refused while the roster loads
  (`RosterRefused`). No paid role and `review_usd` 0, as before.
- **Production.** Any OpenRouter model or any Claude Code model and effort. `opus_repair.OPUS_ENABLED` (off) still gates opus for the
  writers, reviewers and the repair step; the planner is not gated, as before.
- **One runner.** `claude_code.run`: `claude -p`, JSON output, no tools, an empty MCP config with `--strict-mcp-config`, no session saved, an
  empty temporary folder, the `ANTHROPIC_*` and `OPENROUTER*` variables removed, the prompt on stdin. The repair step and the planner use it too.
  Under pytest or with `SATVIK_NO_OPUS=1` no real claude process starts.
- **Same checks and records.** A Claude Code writer or reviewer passes `openrouter_call.check_reply` (FILE blocks; a final verdict line) and is
  one `history/call_NN.json` with `"via": "claude_code"`. It is never on the OpenRouter ledger. The panel still never includes the author's model.
- **Repair or planner on OpenRouter.** A repair role on OpenRouter is one adapter repair call; a planner on OpenRouter is one checked text call
  kept in the claude envelope shape. A paid OpenRouter role still takes reviewer calls only.
- **No code change for a new model.** A model id that no `shared_limits.SLOTS` entry names gets a gate of its own, one call at a time (`shared_limits.OWN_SLOT`). The roster stays the allow-list: the adapter refuses any model it does not name.

## Change index (close-1 step 7)

Each launched job writes `docs/changes/<job_id>.md` on its job branch. The merge queue adds one line per merged job between the markers below.

<!-- change-index:start -->
- docs/changes/tl-roman-20261103-2214-6a93.md
- docs/changes/tl-slug-20261103-2214-bc59.md
- docs/changes/tl-index-20261103-2220-94a5.md
- docs/changes/fl-clamp-20261103-2236-aaea.md
- docs/changes/fl-dedupe-20261103-2236-cc8c.md
- docs/changes/fl-chunk-20261103-2236-adce.md
- docs/changes/fl-wordcount-20261103-2236-977a.md
<!-- change-index:end -->
