# Satvik Loop: engine handoff for the Control Center UI

> **Status (2026-11-04).** This document is the handoff as it was written on 3 Nov. Sections and rows marked **HISTORICAL** describe gaps
> that are now closed: the API exists (`monitor/`), job stage tables are frozen at queue time, and a running job's future stage can be
> changed (`PATCH /api/loops/{id}/stages`). The current capability and verification matrix is in `docs/ARCHITECTURE_REVISION_4.md`
> ("As integrated", Batch D). Where this document and that matrix differ, the matrix and the code win.

Source of truth: `main` at `b96063e` (after PR #36), 3 Nov 2026. Every finding is marked **[exists]**, **[extend]** (the code exists but needs an addition) or **[none]**
(not supported today). Paths are relative to the repo root, and `S/` means `scripts/source/`.

**The one fact that shapes every page:** the engine has **no HTTP server, no API and no event stream**. Every operation is a Python function or a
CLI flag, and every state is a JSON file on disk. A UI backend must be a new local server that wraps these functions and reads these files
([none] today). Parts B and C say which reads and actions can be wrapped safely as they are.

---

## Part A. Current architecture

### A1. Job lifecycle (production profile)
1. **Plan (optional)** [exists]: `S/plan_job.py: plan()`. One Claude Code call writes `_loop/<job>/plan/` (PLAN.md, SPEC.md with KNOWN/ASSUMED
   lines, job.json, files/, expected_lines.json). `INTENT.json` and `raw_response.json` make it exactly-once. The result is
   `{"state": "planned", "job_sha256", "assumed": [...]}`. Approval is `fix_loop.py --approve JOB`, refused if job.json changed (sha) or while
   ASSUMED lines are unapproved. The planner is `fake` in the test profile, so planning is refused there.
2. **Load and validate** [exists]: `S/fix_loop.py: load_job()`. It runs JOB_SCHEMA (jsonschema), path rules, `p1b_routing.check_production_job`
   and `p0_gates.production_job_problems`. Errors are `ValueError(text)`, with no codes.
3. **Pre-checks** [exists] in `fix_loop.run_job()`: `validate_job_inputs` (→ `invalid-spec`), `check_job_size` (→ `job-too-large`),
   `sandbox.selftest` (→ `sandbox-unavailable`), calibration of negative controls and baseline (→ `acceptance-invalid`), and the acceptance
   lock (`acceptance_lock.json`).
4. **Ladder** [exists]: `S/p1b_routing.py: walk() → _ladder()`. The ladder is fixed:
   - writer_a writes.
   - If tests are red and the failure is localized (≤3 tests, ≤2 files), writer_a repairs.
   - If still red: restore the originals, then writer_b writes fresh. If writer_b is down, reviewer_b writes instead.
   - If red, writer_b repairs.
   - The repair step (role `repair`) runs at most twice, behind `_gate`: both writers failed the same test, and enough deadline is left.
   - Then review (`_review`). Each FIX FIRST goes to the current writer, at most `FIX_ROUNDS=2` times and within `REPAIR_CALLS=2` in all.
   - A repair that turns the tests red restores the last green code (`restore_green`) and ends `review-fix`.
5. **Tests** [exists]: `S/p1b_production.py: Production.tests()` runs the Docker sandbox (`S/sandbox_runner.py: DockerSandbox.run`). The
   verdict is per test, from JUnit (`p0_gates.verify_junit`).
6. **Review** [exists]: `p1b_routing._review`. The panel comes from lineage (`panel_for`). Each slot needs READY. A FIX FIRST without evidence
   gets one re-ask (`review-reask`) and then stays open. A failed or no-verdict slot moves to the backup writer.
7. **Finish** [exists]: `p1b_production._integrity` checks that the tested, reviewed and delivered hashes are equal (else
   `integrity-mismatch`). `_finish` writes `status.json`. Any state other than `passed` restores the originals and keeps `candidate/`.
8. **Launched path (parallel)** [exists]:
   - `fix_loop.run_launched()` runs `launch.Launcher.start` (limits, lease, claims, sandbox slot, worktree on branch `job/<id>`), then
     `run_job` in the worktree, then `write_change_doc`.
   - A passed job commits its own files plus `docs/changes/<id>.md`. Then come `launcher.finish` and `merge_queue.submit`.
   - `MergeQueue.drain()` does the rebase, runs `default_suite` in the sandbox, checks main is unchanged, pushes and removes the worktree.

### A2. Roles, call modes and the ten stage types (engine view)
| # | Stage (call site) | Role | Mode | When it runs |
|---|---|---|---|---|
| 1 | Plan (`plan_job.plan`) | planner | — | `--request` only |
| 2 | First write (`_ladder`, `attempt(writer_a,"write")`) | writer_a | write | always first |
| 3 | First repair | writer_a | repair | red and localized |
| 4 | Fresh write | writer_b | write | writer_a red or down |
| 5 | Fallback fresh write | reviewer_b | write | writer_b down |
| 6 | Second repair | writer_b | repair | the fresh write is red |
| 7 | Repair step (`_repair_step` or the adapter) | repair | repair | gate passed, ≤2 |
| 8 | Review slot 1 | reviewer_b (or the backup writer) | review | tests green |
| 9 | Review slot 2 | reviewer_a (`non_author_writer` = the other writer) | review | slot 1 READY |
| 10 | Re-ask, and FIX FIRST repairs | same reviewer / current writer | review-reask / repair | FIX FIRST without evidence / with evidence |

Repeats are bounded by `REPAIR_CALLS`, `FIX_ROUNDS`, `TRUNCATION_RETRIES`, `MAX_RETRIES`, `BREAKER_LIMIT` (`S/p1b_routing.py`,
`S/openrouter_call.py`).

### A3. Model dispatch
- **The roster** is `config/roster.json`, with two profiles, six roles each, and `effort_by_mode`. Per role it holds `model`, `provider`,
  `paid`, `pricing` and `settings`, plus optional `via` and `effort`. It is loaded and checked by `S/openrouter_call.py: load_roster_file`,
  `_check_role` and `roster_state` [exists].
- **OpenRouter** [exists]: `OpenRouter.call(role, system, user, kind, effort)` → `_post`.
  - Request body: `model`, `messages`, `max_tokens`, `temperature`, then `reasoning` (role setting, or `{"effort": e}` / `{"enabled": false}`
    from `effort_by_mode`), then `sampling`.
  - A paid role is pinned with `provider{order, only, allow_fallbacks:false}`.
- **Claude Code** [exists]: a role with `"via": "claude_code"` goes through `S/claude_code.py: run/ask_role`. It runs `claude -p` isolated,
  with JSON output, no tools, an empty MCP config and an empty folder.
- **Model identity** [exists]: `check_reply` refuses a reply whose `model` field differs from the request. The recorded model is therefore the
  served one. `history/call_NN.json` records `model`, `via`, `mode`, `status`, the tokens, `finish_reason` and `seconds`.

### A4. Capability contract (what the engine knows about a model)
- **Known today:**
  - from the roster: model ID, paid flag, pinned provider (paid only), price per token (paid only), and the settings: temperature,
    reasoning (`{max_tokens}` / `{effort, exclude}`), max_tokens, timeout_s, sampling, retries and cap;
  - from `S/claude_code.py`: Claude Code models (opus, sonnet, haiku, fable) and efforts (low, medium, high, xhigh, max).
- **OpenRouter effort values sent:** off, low, medium, high (`EFFORT_VALUES`).
- **Not known [none]:** context length, provider max output, whether a provider honours the reasoning cap (only measured afterwards:
  `reasoning_over_cap`), structured-output support, free-model prices, and verification freshness. The only live metadata read is the paid
  model's endpoints (`OpenRouter.endpoint()`, price and provider) and `/api/v1/key`.

### A5. Output contracts (text, not JSON schemas)
- **Writer (whole file):** `FILE: <path>` plus a fenced block. `S/nemotron_code.py: parse_files` also accepts `### FILE:` and `**FILE:**`.
- **Repair:** SEARCH/REPLACE blocks under `FILE:` (`S/p1b_production.py: apply_edits`). A refused edit gets one re-ask, then falls back to a
  whole-file write. Both formats are supported.
- **Reviewer:** at most 5 lines of the form `- file:line [SPEC n|CHECK n]: text`, and a last line `## Verdict: READY|FIX FIRST`. Evidence is
  checked by `p1b_routing.evidence_ok`.
- **Planner:** one JSON answer (`plan_md`, `spec_md`, `expected_lines`, job and files), checked in `plan_job`.
- **A failure** is a named exception (`BadReply`, `ReplyRejected`), recorded as `history/failed_call_NN.json`. No structured-output or JSON-mode
  request is sent to any model [none].

### A6. Stage configuration
- Defaults live in `config/roster.json` per profile. They are read at call time, with no cache across processes.
- **Per-job overrides [none]:** a job names roles (`fixers: ["writer_a","writer_b"]`), never models. **HISTORICAL:** a queued job now carries its
  own stage table (`stage_snapshot.json`, revision 1), and `PATCH /api/loops/{id}/stages` changes a stage the job has not started yet.
- **Frozen snapshot per job [none]:** the roster is read live, so editing it during a run changes later calls of a running job. **HISTORICAL:**
  implemented (`scripts/source/stage_config.py` snapshot, 2026-11-04); a roster edit now affects future jobs only.
- **Validation** [exists]: `RosterRefused(text)` at load. The test profile refuses paid roles, and any Claude Code role other than haiku at high.

### A7. Lineage and independence [exists]
- `p1b_routing.Lineage` records, per file, the last writer, the origin and the chain. It is saved as `lineage.json`.
- `panel_for(origin)` never puts the author on its own panel, and needs two distinct models.
- The backup for a failed free slot is `backup_writer`. A paid slot has no substitute.
- reviewer_b also writes when writer_b is down (stage 5).
- Mixed lineage → `blocked`.

### A8. Concurrency [exists]
- Sandbox slots: `launch.SANDBOX_SLOTS = 4`.
- A start needs `free_requests ≥ RESERVE_BASE(100) + 40 × running` (`launch.start_refusal`).
- File claims: `loop_home.Claims` (exclusive writes).
- Leases: one per job, with a heartbeat. A lease is stale when its heartbeat is older than `STALE_S = 300` s **and** its pid is dead.
- Fencing: generation numbers.
- Worktrees: `SATVIK_HOME/wt/<job_id>` on branch `job/<job_id>`.
- Model gates: `S/shared_limits.py: SLOTS` (one call wide per model family; glm 2; any other model gets its own gate, one wide).
- Scheduling: a FIFO queue (`fix_loop.enqueue` / `take_next`). One loop process runs one job at a time.

### A9. Budget and quota [exists]
- Shared ledger: `SATVIK_HOME/ledger/limits.json`, in `S/shared_limits.py`.
- Free requests per day: the live cap from `/api/v1/key` `free_model_daily_requests.limit`, falling back to 1000. The day resets at 05:30 IST.
- Pace: 15 requests a minute account-wide. Free requests per job: 40.
- Review spend: `review_usd` is 0 in test and 0.05 per job in production, with a $0.50 daily cap. A worst-case estimate is reserved before
  each paid call and reconciled afterwards (`review_reservation`, `settle_review`).
- Claude Code: no USD accounting. The Opus switch is `S/opus_repair.py: OPUS_ENABLED` (off).

---

## Part B. UI data contracts (read models)

All of these are files. A UI reads them by polling (no events, [none]).

| Read model | File | Fields | Writer |
|---|---|---|---|
| Job status | `<worktree or root>/_loop/<job>/status.json` | `name, schema_version, state, profile, tests_passed, review_verdict, reason, failing_tests, findings?, steps[{model, mode, note, status?, message?, call?, history?}], coder_calls, reviewer_calls{model:n}, evidence{acceptance_lock, calibration, review_hashes, tested_hash, final_hash, reviewed_hash, budget{free_requests_used, free_requests, review_usd_spent, review_usd, repair_calls, repair_enabled}, openrouter_retries[]}, contract_check?, updated` | `Production._heartbeat` (state `running`, after every step) and `p1b_production._finish` |
| Call record | `.../history/call_NN.json` | `model, via, mode, status, failed, prompt_tokens, completion_tokens, reasoning_tokens, finish_reason, seconds, reply_file, reasoning_file` | `Production._write_call` |
| Failed call | `.../history/failed_call_NN.json` | `model, call, status, message (redacted, ≤2000 chars), finish_reason?, usage?, exit_code?` | `Production._failure` |
| Texts | `history/reply_NN.txt`, `review_NN.txt`, `reasoning_NN.txt`, `test_NN.txt`, `repair_step_NN_raw.json` | scrubbed text | same |
| Lineage | `_loop/<job>/lineage.json` | `{path: {last, origin, chain[]}, schema_version}` | `Lineage.save` |
| Reports | `REVIEW_FIX.md`, `ESCALATE.md`, `EXCEPTION.md`, `CONTRACT_CHECK.md`, `INVALID_SPEC.md`, `JOB_TOO_LARGE.md`, `SANDBOX_UNAVAILABLE.md`, `ACCEPTANCE_INVALID.md`, `candidate/`, `original/`, `prev_N/` | markdown, files | `fix_loop`, `p1b_production` |
| Leases | `SATVIK_HOME/leases/<job_id>.json` | `job_id, pid, started, heartbeat, generation, released` | `loop_home.Lease` |
| Claims | `SATVIK_HOME/claims.json` | `path → [{job, gen, mode}]` | `loop_home.Claims` |
| Registry | `SATVIK_HOME/registry.json` | rows by request key and job id: `{job, gen, state, base, result}`, plus `counter:*` rows | `loop_home.Registry` |
| Counters | `launch.counter_lines(home)` | `started`, `refused:<reason>`, `finished:<state>`, `queue-wait`, `merge-retry`, `429:<model>` | registry |
| Gates | `SATVIK_HOME/gates.json` | `gate → [{pid, gen, token}]` (live holders) | `loop_home.Gate` |
| Ledger | `SATVIK_HOME/ledger/limits.json` | `day, free_used, free_cap, review_reserved, review_spent (micro-USD), minute[]` | `shared_limits` |
| Queue | `SATVIK_HOME/queue/*.json`, `queue/taken/` | job files by arrival | `fix_loop.enqueue` / `take_next` |
| STOP | `SATVIK_HOME/STOP` | the file exists or not | user |
| Change doc | `docs/changes/<job_id>.md` (on main after a merge) | job, base, state, files, round, verdict | `write_change_doc` |
| Results archive [exists, #36] | `SATVIK_HOME/results/<job_id>/` (the job's `_loop`, without the key snapshots) and `results/<job_id>/merge.json` (`job_id, state, base, head?, attempts?, tail?`) | kept after the worktree is removed, for every terminal state | `launch.archive_job` (in `Launcher.finish`), `launch.record_merge` (in `MergeQueue._finish`) |
| Roster | `config/roster.json` | as in A3 | hand edit |

**Job states** (`status.json.state`):
- **During a run:** `running`.
- **Production ends:** `passed`, `review-open`, `escalate`, `needs_review`, `blocked`, `provider-unavailable`, `budget-exceeded`,
  `quota-exhausted`, `deadline`, `integrity-mismatch`, `acceptance-tampered`, `exception`.
- **Before any call:** `invalid-spec`, `job-too-large`, `sandbox-unavailable`, `acceptance-invalid`, `refused`.

**Launcher and merge states** (`registry`, `finish`):
- **Start refused:** `refused` with a reason: `stop-file`, `provider-unavailable`, `no-budget-snapshot`, `free-requests`, `review-spend`,
  `request-running`, `no-sandbox-slot`, `claim-conflict`.
- **Run ended:** `stopped`, `error`.
- **Merge ended:** `merged`, `merge-conflict`, `tests-failed`, `main-moved`.

**Step modes** (`steps[].mode`): `write`, `repair`, `review`, `review-reask`, `tests`, `restore-green`, `failed`.

**Separate outcomes, derived:**
- job = `state`
- tests = `tests_passed`, `failing_tests`, `test_NN.txt`
- review = `review_verdict`, `review_NN.txt`, `findings`
- merge = the registry row state (`merged` …) and `docs/changes`

**Monitoring:**
- **Waits [exists, #36]:** `status.json.waiting = {why, since, model}` or `null`. `why` is `model-slot`, `account-pace`, `429-backoff` or
  `retry-backoff`. It is set when the wait begins and cleared when it ends.
- **No stall detector [none].** Liveness is the lease: a lease heartbeat older than 300 s with a dead pid is an orphan, which `Lease` takes over.
  `waiting` explains a wait; it never proves a process is alive.

---

## Part C. UI action contracts

There are no routes or HTTP codes today. "Call" is the function a local server would wrap.

| Action | Call (location) | Request | Success | Failure | Allowed when | Side effects | Twice | Status |
|---|---|---|---|---|---|---|---|---|
| Validate job | `fix_loop.load_job(path)` | job file | job dict | `ValueError(text)` | any | none | safe | exists (no codes) |
| Dry run | `fix_loop.py --dry-run --job` | job path | prompt size | non-zero exit | any | none | safe | exists |
| Plan | `plan_job.plan(root, name, plan_dir, request, files)` / `--request` | request text, files ≤30 | `{state:"planned", job_sha256, assumed}` | `planner-uncertain`, refused in test | production, planner set | 1 Claude call, writes `plan/` | reuses the saved answer, never calls twice | exists |
| Approve | `fix_loop.py --approve JOB` | job name | the job starts | sha mismatch, ASSUMED unapproved | after Plan | launches | the second start → `request-running` | exists |
| Queue | `fix_loop.try_enqueue(job_path, home, rerun=False)` (#36); `enqueue()` is the raw form | job path | `{queued: true, path}` | `{queued: false, reason: duplicate-pending \| duplicate-running \| already-completed, job}` | any | file in `queue/` | an active duplicate is refused (atomic, `queue/.lock`); a passed or merged job needs `rerun=True` | exists |
| Start / run loop | `run_launched(job, repo, api, launcher, merge_queue)`; loop: `take_next` + `run_launched` + `mq.drain` | job dict | result dict `{state, job_id,…}` | `{state:"refused", reason}` | not STOP, within limits | worktree, lease, claims, model calls, ledger | same key while running → `request-running` | exists (no server) |
| Solo run | `fix_loop.py --solo --job` | path | exit code by state | — | one job | model calls, files in the checkout | not guarded | exists |
| Merge | `MergeQueue.drain()` (automatic after a passed job) | — | `[(job_id, "merged")]` | `merge-conflict`, `tests-failed`, `main-moved` | the job passed and was submitted | rebase, sandbox suite, push to main, worktree removed | `merge.lock` serializes | exists (no manual trigger) |
| Global STOP | create or delete `SATVIK_HOME/STOP` | — | — | — | any | stops new starts and the next round; a running job restores its files → `stopped` | safe | exists |
| Status / Results | read the files in Part B | job / job_id | JSON | missing file | any | none | safe | exists (no API) |
| Download artifacts | read `SATVIK_HOME/results/<job_id>/` (#36) | — | files | `archive_failed` in the registry row: the evidence stays in `wt/<job_id>` | after finish | none | safe | exists (no API) |
| Model verify | `openrouter_call.py probe` (`main()`, `probe()`) | roles or models | probe JSON, one call per role | errors per role | key set | 1 free call each | repeats calls | exists (roster models only) |
| Refresh price | `OpenRouter.endpoint(model)` | paid model | price, provider | ProviderUnavailable | paid roles | 1 GET | safe | extend (paid only, not stored) |
| Change a stage model | edit `config/roster.json` | file | — | `RosterRefused` at the next load | any (no lock) | affects running jobs at their next call | — | **HISTORICAL** (2026-11-04: `PUT /api/stages` with a revision and a frozen table per job) |
| Apply future stage | — | — | — | — | — | — | — | **HISTORICAL**: implemented as `PATCH /api/loops/{id}/stages` (`stage_config.apply_future_stage`, I45 validator) |
| Resume a failed job | — | — | — | — | — | — | — | **none** (re-queue starts over; the registry blocks only running duplicates) |
| Cancel one job | — | — | — | — | — | — | — | **none** (STOP is global) |

---

## Part D. Backend gap map

| Missing for the UI | Where it would go | Phase |
|---|---|---|
| A local HTTP server with read routes for Part B and action routes for Part C, stable error codes, and a health route | new server module wrapping `fix_loop`, `launch`, `merge_queue`, `loop_home`, `shared_limits`, `openrouter_call` | P2 (UI backend) |
| Live events (step start/end, merge stage) | `p1b_production.Production._step` / `_heartbeat`; `merge_queue.drain` (emit a per-job event file) | P1 extension |
| ~~Stall vs waiting states~~ | done in #36 (`status.json.waiting`); liveness stays with the lease | done |
| ~~Frozen roster per job, with revisions~~ (done 2026-11-04) | `p1b_production.run` (copy the resolved roles into `status.json.evidence`), and `openrouter_call.roster_state` (read from the snapshot) | P1 |
| ~~Future-stage edits only~~ (done 2026-11-04) | needs the frozen snapshot above, plus a per-stage override read in `_ladder`; today a roster edit changes running jobs | P1 |
| Model capability metadata (context, max output, structured outputs, free prices, freshness) | `openrouter_call` (`/models` read, stored beside the roster) | P1 |
| Structured-output requests and JSON-schema validation | `openrouter_call._post` / `check_reply`, `p1b_production` parsers (text formats today) | P1 |
| Per-job cancel | `fix_loop.run_launched` stop_check per job id (today only global STOP) | P2 |
| Resume or retry a failed job | the registry and `launch.Launcher.start`; not designed: re-queue only | unsupported |
| ~~Queue dedupe~~ | done in #36 (`fix_loop.try_enqueue`) | done |
| ~~An artifact archive that survives cleanup~~ | done in #36 (`launch.archive_job`, `results/<job_id>/`) | done |
| A manual Merge button | `MergeQueue` (submit and drain on request) | P2 |
| A command line for the launcher and the merge queue | `launch.py` / `merge_queue.py` (today Python snippets, see `docs/pilot/two-loop/RUNBOOK.md`) | P2 |
| API key aliases in health | `openrouter_call.load_keys` (names `free` / `paid` exist; never values) | P2 |

**Safe to expose as-is:** Validate, Dry run, Queue (`try_enqueue`), the run-loop start, Global STOP, the reads of status (with `waiting`),
results, registry, ledger, claims, leases and counters, and Model verify for roster models.

**HISTORICAL (2026-11-04):** a model switch during a run and Apply Future Stage are now implemented (see above). Still not exposed and not in
the engine: Resume, per-job Cancel, a manual Merge.
