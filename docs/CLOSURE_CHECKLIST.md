# Control Center closure checklist (backend + frontend integration)

Last updated: 2026-11-04 04:00 IST. The working branch is `claude/satvik-loop-handoff-2gvtcm`; `main` is updated through PRs. Every item has a
"done when" line. Do the items in order, skip none, and mark each one `[x]` only with its evidence.

**Rules:**
- Work goes to Haiku 5.5 subagents only: medium effort for every new submission from 2026-11-04 (B5 and issues-i19 were already running at high).
- Local work goes to Local agent 2 (`session_016GQpGKnhg52NCaYuiekaQw`) only. **Do not use Local agent 1** (`session_016GQpGGyAsk3SytLChxhEfL`), owner's instruction 2026-11-04.
- No other sessions. No UI redesign.
- **No paid calls and no Opus calls until the very end**, anywhere: no paid OpenRouter model (GLM etc.), no Opus in the loop (production repair and planner stay unused), no paid verify. `SATVIK_PROFILE=test`, $0; only free models and Haiku.

## A. Already done (evidence)
- [x] **P1 events, stage snapshots, model catalog:** PR #38 → `main` `8a1fe46`. Suite: 1164 passed, plus the root-only failure.
- [x] **API contract v1 and fixtures:** `docs/API_CONTRACT.md`, `monitor/fixtures/` (in #38).
- [x] **UI repaired:** branch `4b70f67`. `npm ci`, typecheck, lint and build pass; 42/42 unit, 12/12 e2e (fixture server); verify fails
  closed; no invented values.
- [x] **Loop-built helpers:** `api_budget.py` (`c85ebe0`) and `api_idempotency.py` (`c5a9267`) on `main`, merged by the loop itself at $0.

## B. Remaining

**9. [x] Done (`3f96af5`: all 27 fixed or fail-closed with documented limits; suite 1485 passed + root-only, twice; UI 68 unit, 12 + 21 e2e).** **Codex read-only audit (GPT 6.1 Sol, medium; `docs/pilot/codex-review-2026-11-04/`, reviewed `810b0f3`).** 27 findings registered as I36–I62. Done when each is fixed with a test or explicitly documented, and the suite passes.

1. [x] **`ah-shape` loop job.** Done: all 3 helpers on main (`999668e`), evidence on `ah-run-2026-11-04` (`ec62c18`). Done when `scripts/source/api_shape.py` is on `origin/main`, and Local agent 1's evidence branch
   `ah-run-2026-11-04` is pushed. If it escalated, read its `results/<job_id>/` (`events.jsonl`, `review_NN.txt`, `test_NN.txt`), fix the spec
   or the test only if the test is wrong, and re-queue with `try_enqueue(..., rerun=True)`.
2. [x] **Done (`1166ec5`, merged `8dd7228`): 51 contract tests.** **The P2 API subagent finishes** on worktree branch `p2-api`, which already has 5+ commits. Done when it reports: every contract route
   is implemented, `pieces/test_api_contract.py` passes, and the full suite shows only the root-only failure.
3. [x] **Done 2026-11-04 (`8dd7228`):** full Docker suite 1275 passed, 4 skipped, only the root-only failure; the API uses the real helpers (`1166ec5`). **Merge `p2-api` onto the working branch,** and merge `origin/main` too, which brings in the loop helpers. Then point the API at
   `scripts/source/api_budget.py`, `api_idempotency.py` and `api_shape.py`, and remove any local stubs. Done when the full Docker suite
   passes: `python -m pytest pieces -p no:cacheprovider -q` gives only the root-only failure.
4. [x] **Done (`7d9949e`, merged `2c74b35`): both suites read monitor/fixtures; revision, {jobs, index_ok} + jobs.json, profile present; BACKEND_GAPS.md removed.** **Contract sync.** `docs/API_CONTRACT.md` and `monitor/fixtures/` must include:
   - `GET /system/limits.revision`;
   - `GET /jobs` → `{jobs, index_ok}` plus `fixtures/jobs.json`;
   - `GET /system/health.profile`.
   Remove `ui/BACKEND_GAPS.md` or bring it up to date. Done when the UI contract tests and the API contract tests both read the same
   fixtures and pass.
5. [x] **Done 2026-11-04 (`6b2a0ec`): real-API e2e 18/18 (3 viewports x 6), fixture e2e 12/12, unit 51/51, typecheck and lint clean. Failed verify is fixture-only (a real verify needs a key: I20).** **Real integration.**
   - Start `python -m uvicorn monitor.api:app --port 8765` with a temporary `SATVIK_HOME` seeded from one real archived job (copy a
     `results/<job_id>/` from an evidence branch).
   - Run the UI e2e in LIVE mode against it instead of `tests/fake_fastapi_server.mjs`: add a Playwright project or config that targets the
     real API.
   - Fix every mismatch in the API or the UI normalizer, never by editing fixtures to hide a bug.
   - Done when all 7 pages render real data, a failed verify shows not-verified, STOP toggles, a duplicate queue shows 409, and
     `npm run test:e2e` passes against the real API.
6. [x] **Done: PR #39 merged → `main` `96efdef`, pinned to head `c4dc55f`.** **One PR to `main`** with the API, the UI repair, the integration fixes and the contract sync. Done when it is merged and pinned to its
   head SHA.
7. [x] **Done: Windows rerun on main `f8c4b73`: 1325 passed, 3 skipped, 0 failed (`5bebf88`).** **Ran (`68097a5`): 7 pages render live data, 0 console errors; suite 1317 passed, 1 failed (I34).** **Windows check (Local agent 2):** a fresh worktree of `main`;
   - run `pip install -r monitor/requirements.txt`, then `python -m uvicorn monitor.api:app --port 8765` with the real `SATVIK_HOME`;
   - in `ui/`, run `npm ci` and then `npm run dev:live`;
   - open each of the 7 pages, take screenshots, and push them to branch `windows-ui-check-2026-11-04`.
   - Done when every page shows real data, with no console errors.
8. [x] **Merged in #39 (`96efdef`). Written (`69c5312`): As integrated + `docs/STATUS_2026-11-04.md`** **Docs:** add an "As integrated" section to `docs/ARCHITECTURE_REVISION_4.md` (API and UI), tick this checklist, and record a STATUS
   entry. Done when merged.

## D. Issues register (every known issue; none may be skipped)
| # | Issue | Fix / owner | Done when |
|---|---|---|---|
| I1 ✅ (`bd6ed97`) | The archive copies `_loop/.gitignore` (`*`, written by `event_log`), so published evidence folders are git-ignored. Found in `ah-run-2026-11-04` | Add `.gitignore` to `launch.ARCHIVE_SKIP`, plus a test (item 3) | A test proves `results/<id>/.gitignore` is absent |
| I2 ✅ (`bd6ed97`) | The stages response has no **required contract** per stage, so the UI cannot filter which models are eligible (the UI repair noted it) | Add `required_contract` to each `GET /stages` row, and a fixture (item 4) | The UI shows only models whose required contract is `verified` |
| I3 ✅ (`bd6ed97`) | Planner calls emit no events (`plan_job` is not hooked) | `plan_job` emits `attempt-start` / `attempt-end` with stage `planner` (item 3) | A test sees planner events |
| I4 ✅ (`bd6ed97`) | Backup-writer review stage names are best effort (`reviewer_b` until a READY, then `reviewer_a`) | Record the real slot in `_review` / the events (item 3) | A test of a backup review shows the correct stage |
| I5 ✅ | Lease leak (fixed in #36) not yet confirmed in a real run. **Confirmed 2026-11-04:** 6 of 6 ah-* leases have `released: true` (branch `i5-lease-check`) | Check the `ah-run` leases: each job's own client lease is `released: true` | Confirmed from the evidence |
| I6 ✅ (`8447113`, deleted) | `ui/BACKEND_GAPS.md` is out of date | Remove it or rewrite it after the integration (item 4) | No stale gap file |
| I7 ✅ | `npm ci` reports 5 moderate vulnerabilities | **Done 2026-11-04:** `npm audit --omit=dev` → 0 vulnerabilities (runtime clean). All 5 moderate are dev-only: `minimatch` under eslint and typescript-eslint, lint tooling only, not in the built app | Recorded |
| I8 ✅ (`08361b1`: System page saves with revision; 409 stale and not-applied-by-engine shown. No job_review_usd input in the UI) | System limits were locked in the UI until `GET /system/limits` had a `revision` | The API adds `revision`; the UI enables the PUT (item 5) | The System page saves limits live, and a 409 shows on a stale revision |
| I9 ✅ (`69c5312`) | Plan approval became one `approve_assumed` boolean (per-item boxes removed) | Keep (matches the contract); document it (item 8) | In the contract notes |
| I10 ✅ (`69c5312`) | Decisions to document: the fallback-write settings merge, the `writers-share-model` rule, `waiting-unknown` / `stale-warning` semantics | `ARCHITECTURE_REVISION_4.md` "As integrated" (item 8) | Documented |
| I11 ✅ documented (As integrated, open items) | Replies from the PC agents to this session fail ("not found") | Known platform limit; git branches are the report channel | Documented (no fix possible here) |
| I12 ✅ (on GitHub, merged in #39) | The p2-api commits are only in this container's local worktree | Merge onto the pushed working branch as soon as the API agent reports (item 3) | On GitHub |
| I13 ✅ (passes on Windows, `68097a5`) | (Cloud 2026-11-04 `e5e7585`: 1323 passed, 4 skipped, root-only only; UI unit 56, fixture e2e 12, real e2e 21.) The cloud suite has one root-only failure (`test_plan_job.py::…_read_only`) | Environmental; must pass on Windows (item 7) | Passes in the Windows suite run |
| I14 ✅ (`170ee78`, owner decision 2026-11-04) | Production `reviewer_b` was North, which doesn't work | Now `nvidia/nemotron-3-ultra-550b-a55b:free`: max_tokens 32000, timeout 1200, reasoning 8000; production validate clean | Done |
| I15 ✅ (`bd6ed97`) | `PUT /system/limits job_review_usd` is stored, but the engine's production gate still requires exactly 0.05, so a different value would not apply (found in the API report) | The API refuses any job_review_usd other than the engine's accepted value with `not-applied-by-engine`, until the engine reads it; the UI shows that reason | A test of the refusal; the UI shows the reason |
| I16 ✅ (`bd6ed97`) | Claude Code models report contracts as `verified` from the fixture, not from a measured probe: a false verification, the exact class of the UI audit's critical finding | Report `unverified` (with `text_validated` true) until a real `POST /models/verify` result is recorded for that model and contract | A test: a fresh Claude Code entry shows `unverified` |
| I17 ✅ (`bd6ed97`) | The API assumes a single uvicorn worker (a process-wide lock plus the idempotency store) | Document it, and pin `--workers 1` in the run command and in `monitor/requirements.txt` notes | In `API_CONTRACT.md` section 0 and the run command |
| I18 ✅ (`bd6ed97`) | Two `test_stage_config` tests (`test_with_default_stages_every_openrouter_request_body_is_byte_for_byte_todays[test/production]`) take exactly 60.01 s each: they wait out the real 60 s pace window instead of using a fake clock | Give the test client a fake clock/sleep, or a fresh ledger per request, so no real minute wait happens | Both run in under 2 s |
| I19 ✅ (`44fee49`; real haiku/high `reviewer_verdict` pass on the PC 06:22:40Z, `68097a5`; recorded in the catalog) | Claude Code models can never become `verified`: `POST /models/verify` refuses them (`not-in-catalog`), and since I16 `/stages/validate` refuses an unverified model, so no Claude Code model can be put on a stage | `POST /models/verify` accepts catalog Claude Code entries and runs the contract probe through `claude_code.ask_role`. The test profile allows only haiku at high, and a real call runs only on the owner's PC | A test with fake claude records a pass; then Local agent 2 verifies haiku/high for real |
| I20 ✅ (recorded in config/models.json `fae923e`; probes `b97bcca`: 7 free models, 19 pass, 0 fail; Xing writer_file_blocks and reviewer_verdict rate-limited 429, nothing recorded) | Every OpenRouter catalog contract is null, so `/stages/validate` refuses every stage change until a probe records passes | Local agent 2 runs `POST /models/verify` for each free catalog model and each contract against the real API (free requests only, about 30 calls), and publishes the results | The catalog shows real `verified`/`failed` states |
| I21 ✅ (`1ae2bd3`: job_view reads the plan folder's events via api/plans) | Planner events go to the repo-root `_loop/<job>/events.jsonl`, but `job_history` / `read_job_events` scan only `results/<id>/` and `wt/<id>/_loop/`, so the API never shows planner events | Make the readers include the plan job's folder (or have `plan_job` write where the job's other events go) | A test: a planned job's view shows its planner attempt |
| I22 ✅ (`556768c`: shared virtual clock across 4 real processes, ~0.06 s) | `test_shared_limits.py::test_pace_sends_at_most_15_in_any_60_seconds_across_processes` takes 60.05 s (a real minute) | Review whether it can use a fake clock across processes; if not, keep it and record it as a by-design timing test | Decided and recorded |
| I23 ✅ (`c339c75`: fallback uses reviewer_b's own stage effort) | Production: validating a Claude Code `reviewer_b` change is refused with `bad-effort` on `review_fix_repair` (an engine rule found by the issues agent) | Investigate in `stage_config.validate`: an inherited-model stage should not fail effort validation because of another stage's model change | A test: a valid Claude Code `reviewer_b` change validates |
| I24 ✅ (`6b2a0ec`) | The B5 integration subagent stopped at the usage limit with uncommitted work in its worktree (`.claude/worktrees/agent-a769e14f201efa8b2`, branch `b5-integration`: seed script, live Playwright config, real-API spec) | Resume it, or commit its work and continue it in a fresh Haiku 5.5 medium subagent on the same worktree | B5 done |
| I25 ✅ (`3b69b15`) | `attempt-end.status` is the raw HTTP code, not ok/failed/rejected (found by B5) | API derives the attempt status (subagent `api-mismatch`) | A test |
| I26 ✅ (`0758e5f`) | `attempt-end` rows lack stage/role/mode/model/via/effort | Fill from the matching attempt-start (`api-mismatch`) | A test |
| I27 ✅ (`83ce7d5`) | A locked stage row sent unchanged (model null) is refused 400 bad-model | Accept it unchanged; a changed model gives model-not-selectable (`api-mismatch`) | A test |
| I28 ✅ (`cd5fca0`) | job_review_usd: in-range values other than 0.05 must give not-applied-by-engine (I15 wording) | `api-mismatch` | A test |
| I29 ✅ (`2cc5c96`; new archives only, the earlier ah-budget archive stays spec null) | Archived job detail has spec/files null: the archive lacks `<name>.job.json` | Archive copies the job file (`api-mismatch`) | A test |
| I30 ✅ (`f54f6a8`) | Contract prose puts contracts/reasoning_mode at Model level; example, fixture and API nest them under capabilities | Fix the prose (`api-mismatch`) | Doc fixed |
| I31 ✅ (`69c5312`) | `index_ok` false and `engine_version` null on a fresh seeded home | Observations: expected (index not built, not a git checkout). Record in As integrated (item 8) | Documented |
| I32 ✅ (`d1c1e08`: startup rebuild + CLI, 8 tests) | Nothing calls `job_history.rebuild_index`: history.db is built only by finish/merge hooks, so a missing or corrupt index never comes back on its own | Rebuild from the files when the index is missing or unreadable (API startup) plus a CLI | A test: delete history.db, start the API, index_ok true |
| I33 ✅ (`49fd9f4`, `e5e7585`) | The PC records verify results only in its local config/models.json; recording them in the repo makes 10 Python tests and 3 real-API e2e tests fail, because they assumed an all-null catalog | Tests build their own catalog (branch `i20-tests`); the real results go to main with them | Suite green with the real catalog on main |
| I34 ✅ cloud (`ead70b4`, `e5e7585`; merged #40 `f8c4b73`); passes on Windows `5bebf88` | Windows: `test_rebuild_index_…corrupt_index…` fails with WinError 32; a sqlite connection to a corrupt history.db is left open, so rebuild cannot delete it (would also break the I32 startup rebuild on Windows) | Close every connection on every path (branch `i34-win`) | A test proves no connection stays open; passes on Windows |
| I35 ✅ (`ac1f22c`: finished from the registry; end state/tests/verdict truly absent for pre-archive jobs, documented) | Dashboard shows Unknown end_state/tests/verdict/finished for pre-archive tl-*/fl-* jobs (Windows check) | Investigate: honest missing data (document) or an unread source (fix) (`i34-win`) | Decided with evidence |

| I36 ✅ (`a41f490`, merged `37bef08`) | Codex (critical): A stage override takes the role's paid flag, provider and prices, not the selected model's (`stage_config.py:399`, `openrouter_call.py:868`) | Batch A | Fixed with a test, or documented as a trust limit |
| I37 ✅ (`81d9f17`, merged `eefcf08`) | Codex (critical): Planner `controls` paths are written unvalidated (absolute or `../` escapes the plan folder); extra controls allowed (`plan_job.py:273`) | Batch B | Fixed with a test, or documented as a trust limit |
| I38 ✅ (`018dd8f`, merged `37bef08`) | Codex (high): Production dispatch drops `stop_check`: STOP does not stop the ladder between steps (`fix_loop.py:976`) | Batch C | Fixed with a test, or documented as a trust limit |
| I39 ✅ (`934e737`, merged `37bef08`) | Codex (high): Merge sandbox lacks `monitor/` (and `ui/` fixtures) and FastAPI/httpx: the full suite fails at merge (`merge_queue.py:39`, Dockerfile) | Batch C | Fixed with a test, or documented as a trust limit |
| I40 ✅ (`1cf4f78`, merged `3f96af5`) | Codex (high): Pending merges are memory-only; crash before/after push loses or double-counts delivery (`merge_queue.py:66`) | Batch C | Fixed with a test, or documented as a trust limit |
| I41 ✅ (`f83952a`, merged `3f96af5`) | Codex (high): Taking a queue file is permanent before start succeeds; refused jobs are not requeued (`fix_loop.py:1689`, `loops.py:23`) | Batch C | Fixed with a test, or documented as a trust limit |
| I42 ✅ (`296313c`, merged `3f96af5`) | Codex (high): Enqueue dedupe and dequeue/start are not one atomic transition; Launcher bypasses `Registry.begin` (`launch.py:206`) | Batch C | Fixed with a test, or documented as a trust limit |
| I43 ✅ (`cb890b7`, merged `37bef08`) | Codex (high): A missing/corrupt stage sidecar silently runs current defaults instead of the frozen snapshot (`fix_loop.py:1636`) | Batch C | Fixed with a test, or documented as a trust limit |
| I44 ✅ (`a41f490`, merged `37bef08`) | Codex (high): `paid_budget_usd` is not persisted or enforced; 0 skips validation; `valid:false` ignored (`actions.py:117`) | Batch A | Fixed with a test, or documented as a trust limit |
| I45 ✅ (`cdabe10`, merged `66332f3`) | Codex (high): Contract verification is enforced only by /stages/validate, not by save, queue or apply_future_stage (`config_api.py:492`) | Batch D | Fixed with a test, or documented as a trust limit |
| I46 ✅ (`56eecc2`, merged `37bef08`) | Codex (high): Independence uses the fresh-write origin only; specialist repair lineage recorded as `anthropic`, not excluded from the panel (`p1b_routing.py:439`) | Batch C | Fixed with a test, or documented as a trust limit |
| I47 ✅ (`62fe47f`, merged `3f96af5`) | Codex (high): Merge does not recheck reviewed hashes or rerun the job's locked acceptance tests after rebase (`merge_queue.py:93`) | Batch C | Fixed with a test, or documented as a trust limit |
| I48 ✅ (`a41f490`, merged `37bef08`) | Codex (high): Pace timestamp is recorded before waiting for the model slot, so real sends can exceed 15/min (`openrouter_call.py:891`) | Batch A | Fixed with a test, or documented as a trust limit |
| I49 ✅ (`a41f490`, merged `37bef08`) | Codex (high): Paid worst case uses chars/3 and roster prices, not live endpoint prices (`openrouter_call.py:495`) | Batch A | Fixed with a test, or documented as a trust limit |
| I50 ✅ (`a41f490`, merged `37bef08`) | Codex (medium): Paid reservations have no per-call identity; crash strands budget; midnight clears holds (`shared_limits.py:87`) | Batch A | Fixed with a test, or documented as a trust limit |
| I51 ✅ (`5dfca43`, merged `eefcf08`) | Codex (high): Planner exactly-once is check-then-write with no lock; intent reuse ignores the request hash (`actions.py:239`, `plan_job.py:94`) | Batch B | Fixed with a test, or documented as a trust limit |
| I52 ✅ (`771ab2e`, merged `eefcf08`) | Codex (high): Plan approval queues only the job JSON, not its spec/tests/controls; approval hash covers job JSON only (`plan_job.py:268`, `actions.py:309`) | Batch B | Fixed with a test, or documented as a trust limit |
| I53 ✅ (`c834a52`, merged `37bef08`) | Codex (high): Protected controller files list is incomplete (omits stage_config, model_catalog, nemotron_code, monitor/) (`p0_gates.py:22`) | Batch C | Fixed with a test, or documented as a trust limit |
| I54 ✅ (`cb9af8d; partial: fail-closed JUnit check, full isolation is a documented trust limit`, merged `3f96af5`) | Codex (high): Sandbox test results are produced in the same process as generated code; JUnit forgery possible (`sandbox_runner.py:123`) | Batch C | Fixed with a test, or documented as a trust limit |
| I55 ✅ (`57ac5e7`, merged `eefcf08`) | Codex (high): Secret filtering inconsistent: planner inputs can include secrets/, planner raw replies unscrubbed, repair evidence scrub list empty (`plan_job.py:162`, `p1b_production.py:812`) | Batch B | Fixed with a test, or documented as a trust limit |
| I56 ✅ (`43e0df4`, merged `3f96af5`) | Codex (high): Green candidate kept only in memory; exceptional repair failure or full disk loses it; no sandbox output/disk quota (`p1b_production.py:583`) | Batch C | Fixed with a test, or documented as a trust limit |
| I57 ✅ (`d96b834`, merged `3f96af5`) | Codex (medium): Wall-clock jumps and PID reuse not handled (`shared_limits.py:82`, `loop_home.py:509`) | Batch C | Fixed with a test, or documented as a trust limit |
| I58 ✅ (`56ccbd7`, merged `66332f3`) | Codex (medium): Windows sharing-violation retries only in some writers; production status rewritten in place (`stage_config.py:534`, `store.py:65`) | Batch D | Fixed with a test, or documented as a trust limit |
| I59 ✅ (`3eb57ae`, merged `66332f3`) | Codex (high): Idempotency record saved after effects; 24 h expiry; corrupt treated as absent (`store.py:100`, `api_idempotency.py:59`) | Batch D | Fixed with a test, or documented as a trust limit |
| I60 ✅ (`f0eade6`, merged `66332f3`) | Codex (medium): Blocking model verify runs inside async handlers under the mutation lock; STOP/health stall (`api.py:263`) | Batch D | Fixed with a test, or documented as a trust limit |
| I61 ✅ (`d549db8, 118acd1`, merged `66332f3`) | Codex (medium): UI sends model without `via`; plan polling stops after ~60 s; transport errors say 'Nothing was changed' (`App.tsx:1485`, `client.ts:101`) | Batch D | Fixed with a test, or documented as a trust limit |
| I62 ✅ (`8b20bb0`, merged `66332f3`) | Codex (low): Docs mix historical gaps and integrated behaviour (handoff forbids implemented future-stage edits; As integrated says catalog is null) | Batch D | Fixed with a test, or documented as a trust limit |

| I63 ✅ (`f407f2c`, merged `3f96af5`) | `test_p0_sandbox.py::test_a_hung_test_with_child_processes_is_killed_with_its_container` failed once under full-suite load (passes alone, 12/12 twice) | Make the timing robust under load (batch C2) | Stable in 3 full runs |
| I64 ✅ (`299cc73`, merged `3f96af5`) | (I52 limit) a planned job's installed spec/acceptance/control files are untracked; `write_change_doc` commits only `job.files`, so the merge does not carry them | Commit them with the change (batch C2) | A test: a merged planned job carries its acceptance tests |
| I65 ✅ (`113f24c`, merged `3f96af5`) | (I55 limit) the legacy non-production `run_job` path scrubs with `for k in scrub_keys` over the `{slot: key}` dict (slot names, not values) | Use `redact.scrub` there too (batch C2) | A test |

| I66 | (I49 consequence) The byte-based worst case is ~3x the old estimate, so fewer paid GLM reviews fit under the $0.05 job review ceiling (p0_gates pins 0.05) | Owner decision: raise the ceiling or accept fewer reviews | Decided |
| I67 | (I53 consequence) Loop jobs can no longer edit `scripts/source/*.py`, `monitor/`, `config/`, `tools/sandbox/`; the pilot jobs under docs/pilot are now refused (historical loaders waived in tests). A controller change needs a trusted workflow that does not exist yet | Owner decision: accept, or design the trusted workflow | Decided |

| I68 | Windows: `process_start` reads only /proc, so reused-pid detection (I41, I57) does nothing on Windows; 4 tests fail there (`b6ada87`) | Windows start identity via ctypes GetProcessTimes (branch `windows-fixes`) | Passes on Windows |
| I69 | Windows: `test_restore_green_puts_the_green_files_back_from_disk_when_memory_is_gone` fails (restored files differ) | Find the Windows cause (newlines/paths) and fix the checkpoint code | Passes on Windows |
| I70 | Windows: `test_a_symlink_into_secrets_is_refused` errors (WinError 1314, no symlink privilege) | Skip only when the OS refuses the symlink | Passes or skips cleanly on Windows |
| I71 | Windows: Vite binds `localhost` (::1) but the Playwright configs poll 127.0.0.1:5173, so e2e times out | Bind 127.0.0.1 explicitly | `npm run test:e2e` passes on Windows unmodified |
| I72 | `npm ci` now reports 11 moderate vulnerabilities (was 5) | Audit runtime vs dev; fix runtime ones | Runtime audit 0 |

## C. Owner-only (not blocking)
- Delete `r72-token-budget` (`5905bda`) and `pilot-loop-status.solo.json`, and remove `wt-pilot-r5`. Review `wt-B-pilot*`.
- Production `reviewer_b`, the paid production run, and the December re-check of free models.
