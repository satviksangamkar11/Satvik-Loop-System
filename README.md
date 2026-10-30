# Satvik Loop System

**Status: READY and FROZEN (2026-10-30).** Only bug fixes from here, each with a failing test first. See `docs/LOOP_CLOSURE_REPORT.md` for the closure evidence.

An automatic code-writing loop. A spec goes in. Tested, reviewed code comes out. The supervisor only watches.

## How one job runs

1. **Write.** The coder (Nemotron 3 Ultra, NVIDIA API) reads the spec and writes the module and its test file (round 0).
2. **Test.** pytest runs on the job's test files.
3. **Fix.** If tests fail, the real pytest output goes back to the coder. Rounds 0 to 3 are all Nemotron.
   - Tests that exist on disk are frozen: a fix round that changes one is rejected. The one exception is a test file with a syntax error: it cannot run, so the coder is told to return it fixed.
   - A bug INSIDE a frozen test (pytest shows a KeyError, NameError, TypeError and so on raised in the test's own code, not a failed assertion) may be repaired in a fix round, but only if the test-function count and the assert count do not drop.
   - A test file that does not exist yet (for example after a refused first reply) must still be written in a fix round.
   - In round 0, a test file that already existed may only be extended, never rewritten.
   - A reply with a refused block for a job file is rejected as a whole.
4. **Review.** When tests pass, four reviewers read the code at the same time:
   Lightning (Nemotron 3.5 Lightning), MiniMax (MiniMax M3.1 Flash), Codex (gpt-6-luna, medium, read-only) and Haiku (low effort, reviewer only).
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

The launcher refuses any job whose fixers are not all `nemotron`. Per-job state is in `_loop/<name>/status.json`, the replies are in `round_<n>.txt` and the reviews are in `review_<pass>_<reviewer>.md`.

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
- Only the Nemotron coder writes code. Codex and Haiku only review.
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
- `tools/codex_change_review.py`: read-only review of a change set (`CODEX_REVIEW_MODEL` sets the Codex model; `REVIEW_WITH=haiku` asks Haiku at low effort instead)
- `docs/REVIEW_PROMPT.md`: the prompt every reviewer receives
- `pieces/test_*.py`: the tests

This repository holds the loop code only. It expects the surrounding project layout: `specs/`, `_loop/jobs/`, `docs/` and `docs/AGENT_RULES.md`.

## Tests

```bash
python -m pytest pieces -p no:cacheprovider -q
```

The loop was reviewed by Codex (gpt-6.1-sol and gpt-6-luna, medium) and tested live with four reviewers. A small job passed in 32 seconds.

## Known limits

- Job files are still written by hand (validated at load time).
- `yaml` and `jsonschema` are third-party dependencies of `fix_loop.py`.

- The coder runs with thinking on (owner rule).
- `pieces/test_guards.py` checks the surrounding Reverse-Video project (modules, rules file, job files); run it inside the full project.

- A re-run on files that already exist cannot rewrite the existing tests (append only), so re-runs need new file names or a wiped state.
- Haiku and Codex need their CLI logins. If one is signed out, its review is empty and the job ends with exit code 5.
- A job launched from a shell that closes right afterwards dies with that shell. Run it as a background task.
