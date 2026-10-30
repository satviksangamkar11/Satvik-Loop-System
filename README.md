# Satvik Loop System

An automatic code-writing loop. A spec goes in. Tested, reviewed code comes out. The supervisor only watches.

## How one job runs

1. **Write.** The coder (Nemotron 3 Ultra, NVIDIA API) reads the spec and writes the module and its test file (round 0).
2. **Test.** pytest runs on the job's test files.
3. **Fix.** If tests fail, the real pytest output goes back to the coder. Rounds 0 to 3 are all Nemotron.
   - Tests that exist on disk are frozen: a fix round that changes one is rejected.
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
| `escalate` | tests never passed: originals restored, `ESCALATE.md` written, Sonnet 5.5 writes `SONNET_DIAGNOSIS.md` | 3 |
| claim conflict | another job holds one of the files | 4 |

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
- `tools/codex_change_review.py`: read-only review of a change set (`CODEX_REVIEW_MODEL` sets the model)
- `docs/REVIEW_PROMPT.md`: the prompt every reviewer receives
- `pieces/test_*.py`: the tests

This repository holds the loop code only. It expects the surrounding project layout: `specs/`, `_loop/jobs/`, `docs/` and `docs/AGENT_RULES.md`.

## Tests

```bash
python -m pytest pieces -p no:cacheprovider -q
```

The loop was reviewed by Codex (gpt-6.1-sol and gpt-6-luna, medium) and tested live with four reviewers. A small job passed in 32 seconds.

## Known limits

- A re-run on files that already exist cannot rewrite the existing tests (append only), so re-runs need new file names or a wiped state.
- Haiku and Codex need their CLI logins. If one is signed out, its review is empty and the job ends with exit code 5.
- A job launched from a shell that closes right afterwards dies with that shell. Run it as a background task.
