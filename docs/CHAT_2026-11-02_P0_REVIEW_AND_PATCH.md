# Chat record: Satvik Loop review, closure checklist and P0 patch (2026-11-02)

Source: https://claude.ai/share/af29ca01-287a-4130-87fd-b00dc5f168a4
Saved from the shared snapshot. Attached files (the review document and `satvik-loop-p0.patch`) are hidden in the share and are NOT included here.

---

## 1. Owner

> review the proposed architecture as well as each and individual pressure test, make a checklist of all the points that you seem alarming, and then design a architecture from scratch. Now we have a $10 top up of open router. That does mean we have unlocked 1,000 requests per day. Also, we have money that doesn't mean we want to spend money. If there is a need, we can, but if not, then no need. Pressure test each and everyone. Report, study them properly and suggest the architecture.

## 1. Claude

The full review, the 38-point alarm checklist and the new architecture are ready in "Satvik Loop — Review, Alarm Checklist & Clean-Slate Architecture" (Claude Doc).

Biggest finding: the real limits are the 1,000 free requests per day and wall-clock time, not money. The Inkling Small free endpoint keeps code for training, so use it only on code you'd be happy to make public.

---

## 2. Owner

> Do you agree pressure test — https://github.com/satviksangamkar11/Satvik-Loop-System

## 2. Claude

Mostly yes. Checked against commit 4ce4bd2: the loop already has most of the controls the document called "missing". But the review missed the most important bug.

**Where it's right (confirmed in fix_loop.py)**
- Limits exist: deadline_min 120, max_rounds 3 + review_rounds 4 (max 8 coder calls). Reviewer timeout 2,800 s; timeout counts as NONE, never READY.
- REPEAT_LIMIT = 3 (line 288), path validation (blocks `..` and absolute paths), duplicate FILE: refusal, atomic writes, crash recovery.
- "Extend, don't rebuild" is right. Use the document as a red-team checklist, not a blueprint.
- Six mandatory mutants is too rigid; a repro test for every finding is too strict for security/concurrency; making DeepSeek wait for all four free attempts wastes time (saves ~$0.02).

**What it missed (real quorum bypass)**
1. Haiku tie-breaker can approve with a reviewer missing: judge READY (lines 1141–1145) clears `any_missing`, `all_ready` becomes true via `_judge_ready`. READY + FIX FIRST + timeout ends "passed".
2. `passed-no-review` returns exit 0 unless the job sets `review: true`.
3. `min_valid_reviews` can quietly lower quorum; no minimum enforced.
4. Pytest passes if one test passes and the rest are skipped; no per-test check.
5. No sandbox: pytest runs with full environment (API keys visible), in the real checkout; timeout kills parent but not children.
6. Coder still writes tests in round 0 (appending allowed, lines 960–965) — original false-pass hole still open.

**Pushback**
- No new state machine, but pull the review decision (~80 lines of flags) into one small function with a truth table and tests: two READY passes, anything else doesn't, no tie-breaker can make up for a missing review.
- DeepSeek: escalate early only when two different free models failed on the same confirmed defect.
- Reviewer evidence: static evidence (file, line, spec clause) OK for a free repair; require a failing test before a paid repair.

**Order:** fix bugs 1–3; close bug 6; per-test checks + sandbox (4–5); then OpenRouter, Opus, DeepSeek. Steps 1–3 need no model calls.

---

## 3. Owner

> Yes, I agree with most of that review. But I would make three corrections before implementation.
>
> 1. The quorum bypass is the immediate correctness bug. A tie-breaker must not convert missing or invalid reviews into approval. Extract the acceptance decision into one small function and test it.
> 2. False-passing tests are more dangerous than the current model selection. Keep acceptance tests under independent control. The coder must not be able to modify them, replace their fixtures, skip them or decide which tests count toward PASS.
> 3. The proposed paid-repair rule is too rigid. Requiring a failing reproduction test before every DeepSeek repair can block genuine security, concurrency and integration defects. Accept precise, independently verified static evidence too.
>
> One distinction: `review: false` is an explicit no-review mode, not an accidental bypass. For production coding jobs, make review mandatory and prevent the planner from disabling it.
>
> **Fast-close implementation checklist** — No new architecture. No new framework. No additional memory system.
>
> - P0 — Fix review acceptance: two valid independent approvals required. Missing review blocks. A judge cannot bypass quorum.
> - P0 — Lock acceptance tests: Opus creates or selects tests before coding. Remove those test paths from coder-writable files.
> - P0 — Reject false passes: use pytest's built-in JUnit XML to check required test cases, skips, xfails and collection errors.
> - P0 — Protect execution: use an existing restricted sandbox. Never run generated code with access to credentials or unrelated work.
> - P1 — Connect OpenRouter: one adapter for GLM Free, Qwen Free, free reviewers and paid DeepSeek.
> - P1 — Connect Opus once: natural-language request → validated plan and job. No Opus calls during repairs.
> - P1 — Reuse repair loop: existing pytest/reviewer feedback → current coder for simple fixes, Qwen fallback, DeepSeek for difficult defects.
> - P1 — Run one adversarial pilot: demonstrate rejection of fake tests and missing reviews, then complete one real coding job.
>
> **Frozen model workflow**
>
> | Stage | Model |
> |---|---|
> | Plan and acceptance criteria | Opus Medium — once |
> | Implement | GLM-5.3 Flash Free |
> | Fallback | Qwen3.8 27B Free |
> | Independent review | Other coder family + Inkling Small Free |
> | Difficult repair | DeepSeek V4.1 Flash 1017 — only paid model |
> | Test, repair dispatch, history | Existing Satvik Loop |
>
> Shortcut: the current schema already permits test files outside the coder's writable files when they live under `tests/`. Use that; protect fixtures and runner config too.
>
> First release: implement in the existing repo, preserve the frozen baseline, stop once adversarial tests and one real end-to-end job pass. No Gatekeeper framework, new state machine, memory database, model-ranking system or dashboard. The only significant dependency is a restricted test environment; if none exists, don't call it safe for unattended production.
>
> Final decision: close the correctness holes first, connect OpenRouter, run the pilot, ship the coding loop. Further engineering requires evidence from actual failures.
>
> Do you agree no shortcut for architecture cause we gonna solve real difficult problem. But we don't want to open it as an individual project as well. We want to close this as fast as possible. Then use it to solve the next gaps of whatever projects comes next.

## 3. Claude

Yes. "No shortcuts" = every gap that can produce a false pass or runaway loop gets closed, not new layers. Four cheap additions, each inside an existing item:

- **Prove locked tests can fail** (Lock acceptance tests): run new acceptance tests once against unchanged code before coding; every test for the new requirement must fail. One pytest run, no model calls.
- **Make the sandbox concrete** (Protect execution): Docker `--network none`, repo read-only, writable candidate copy, no env vars, memory/CPU/process limits. Windows via WSL2. ~40-line wrapper.
- **Request cap in OpenRouter adapter**: per-job ceiling ~40 free requests, check remaining quota first (`GET /api/v1/key`), retry each failed call at most 3 times.
- **Save the plan once, never re-plan on restart** (Connect Opus once): write plan files before the first coder call; reuse on restart.

Flag: Inkling Small Free logs prompts for training — reviewer only for shareable code; name a second free reviewer for everything else.

Order: four P0 items first, offline with fake models.

---

## 4. Owner (P0 brief)

> Draft the P0 correctness and safety patch against Satvik Loop at commit 4ce4bd2. Objective: close false-pass and runaway-execution risks quickly, without new infrastructure. Scope: P0 only. No OpenRouter, model changes, Opus planner or memory changes.
>
> **Review acceptance** — one small testable function; two valid independent READY reviews for production; missing/invalid/timed-out blocks; judge can't override missing quorum or unresolved blocking findings; post-repair review must inspect the exact final code; keep explicit no-review mode for legacy/tests but disallow it in production. Truth-table tests incl. READY + FIX FIRST + NONE.
>
> **Locked acceptance tests** — established before coding; outside coder-editable files; protect fixtures, config, runner invocation; compare hashes and required test IDs before/after; bug regressions fail on buggy baseline; new behaviour fails on a known-wrong implementation / negative control; don't require every acceptance test to fail on original code. Regression: constant-return implementation + matching false-passing tests must be rejected.
>
> **Pytest results** — JUnit XML; every mandatory test collected and passed; reject unexpected skips, xfails, deselection, missing tests, collection errors; never accept on exit 0 + one pass.
>
> **Docker execution boundary** — verify Docker exists; disposable sanitized workspace; mount only necessary source + trusted tests (read-only); `--network none`, non-root, `--cap-drop ALL`, `--security-opt no-new-privileges`, CPU/memory/PID limits, read-only root fs; no host secrets, unrelated projects, Docker socket or `_moss/`; no credentials; timeout kills the whole container; adversarial isolation tests. If Docker unavailable or confinement fails, stop safely.
>
> **Testing and delivery** — isolated branch, preserve frozen baseline; failing tests first; smallest changes; keep state machine, locks, parser, memory/history, recovery; run existing offline suite + new adversarial tests; show actual results; report env-dependent tests separately; one patch, changed-file list, test evidence, limitations; don't claim production-safe because Docker starts. Stop at P0. `_moss/` and unrelated projects untouched. Don't be a yes man.

## 4. Claude (result)

Work done: isolated branch, baseline run, failing tests first, then a small gate module, a Docker runner module, wiring in `fix_loop.py` (schema/load_job, production setup in run_job: sandbox self-test, acceptance lock, negative-control calibration; acceptance run + tested-code hash; review stage via `decide_review`, judge skipped in production, review voided if code changed), CLI `--legacy` flag, README section. Fixed along the way: test-fixture mistake; `pythonpath` resolving relative to the ini folder; named includes like `_moss` were copied whole (filter fixed); three regressions vs baseline fixed (coder-fixable findings repaired before a test finding stops the job; decision record moved out of `rounds`).

Delivered as one commit on a local branch → `satvik-loop-p0.patch` (apply with `git am`; checked it applies cleanly to a fresh 4ce4bd2 checkout). Claude could read but not push the repo.

**Results (actual runs)**
- Full suite: 255 passed, 2 skipped, 13 failed. The same 13 fail without the patch (env reasons: `test_guards` needs files not in the repo; claim-lock tests expect Windows lock behaviour). No new failures.
- New P0 tests: 69 passed. Each attack first shown to succeed on old code (or written to pass only if the attack works), then shown blocked.
- Env-dependent: 12 tests in `test_p0_sandbox.py` need Docker + the sandbox image; without them they show skipped, never passed.

**What's blocked now**
- Review: READY + FIX FIRST + NONE with judge READY no longer passes in any mode. Production needs every reviewer READY, ≥2 reviewers, no lowered quorum, no `review: false`. Code changing during review voids the review.
- False passes: constant-return implementation + matching coder test rejected by locked acceptance tests. Weak acceptance tests rejected before any coder call. Skipped/xfailed/missing/deselected tests and collection errors fail.
- Execution: network, host API keys, `_moss/`, `secrets/`, `.env`, writes to trusted tests, root, Docker socket, planted `sitecustomize.py`, fork bombs, hung tests with children — all blocked. Timeout leaves no surviving container.
- Bonus: an existing test failed 5 runs in 6 because a stale `.pyc` let a wrong module pass — a real false-pass path in the old runner, now fixed.

**Behaviour changes needing owner sign-off**
- Judge is off in production: a reviewer's wrong FIX FIRST can't be overruled → job ends `review-open` instead of `passed`.
- Jobs without `"profile": "production"` are refused unless `--legacy` or `SATVIK_ALLOW_LEGACY=1` (includes launcher-started jobs).
- Production jobs need a negative control or a baseline-failing test (P1 Opus planner must write these).

**Remaining limitations**
- Docker tested on Linux with a locally built minimal image (Docker Hub blocked there). Untested on Windows / Docker Desktop — run `test_p0_sandbox.py` locally before trusting it.
- Sandbox image must contain the project's test dependencies (no network at test time).
- If an acceptance test imports a helper from a coder-editable file, that helper isn't protected. Keep acceptance tests self-contained.
- Candidate files are still written into the working checkout; only execution is sandboxed. Originals restored on failure.
- Legacy mode stays weaker by design.

P0 is reviewable, not production-safe. Needs the Windows sandbox-test run and one real job.
