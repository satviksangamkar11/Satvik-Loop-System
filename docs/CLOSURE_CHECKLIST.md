# Control Center closure checklist (backend + frontend integration)

Last updated: 2026-11-04 04:00 IST. The working branch is `claude/satvik-loop-handoff-2gvtcm`; `main` is updated through PRs. Every item has a
"done when" line. Do the items in order, skip none, and mark each one `[x]` only with its evidence.

**Rules:**
- Work goes to Haiku 5.5 high subagents only.
- Local work goes to Local agent 1 (`session_016GQpGGyAsk3SytLChxhEfL`), or Local agent 2 (`session_016GQpGKnhg52NCaYuiekaQw`) when agent 1's
  context is full.
- No other sessions. No paid calls (`SATVIK_PROFILE=test`, $0). No UI redesign.

## A. Already done (evidence)
- [x] **P1 events, stage snapshots, model catalog:** PR #38 → `main` `8a1fe46`. Suite: 1164 passed, plus the root-only failure.
- [x] **API contract v1 and fixtures:** `docs/API_CONTRACT.md`, `monitor/fixtures/` (in #38).
- [x] **UI repaired:** branch `4b70f67`. `npm ci`, typecheck, lint and build pass; 42/42 unit, 12/12 e2e (fixture server); verify fails
  closed; no invented values.
- [x] **Loop-built helpers:** `api_budget.py` (`c85ebe0`) and `api_idempotency.py` (`c5a9267`) on `main`, merged by the loop itself at $0.

## B. Remaining
1. [x] **`ah-shape` loop job.** Done: all 3 helpers on main (`999668e`), evidence on `ah-run-2026-11-04` (`ec62c18`). Done when `scripts/source/api_shape.py` is on `origin/main`, and Local agent 1's evidence branch
   `ah-run-2026-11-04` is pushed. If it escalated, read its `results/<job_id>/` (`events.jsonl`, `review_NN.txt`, `test_NN.txt`), fix the spec
   or the test only if the test is wrong, and re-queue with `try_enqueue(..., rerun=True)`.
2. [ ] **The P2 API subagent finishes** on worktree branch `p2-api`, which already has 5+ commits. Done when it reports: every contract route
   is implemented, `pieces/test_api_contract.py` passes, and the full suite shows only the root-only failure.
3. [x] **Done 2026-11-04 (`8dd7228`):** full Docker suite 1275 passed, 4 skipped, only the root-only failure; the API uses the real helpers (`1166ec5`). **Merge `p2-api` onto the working branch,** and merge `origin/main` too, which brings in the loop helpers. Then point the API at
   `scripts/source/api_budget.py`, `api_idempotency.py` and `api_shape.py`, and remove any local stubs. Done when the full Docker suite
   passes: `python -m pytest pieces -p no:cacheprovider -q` gives only the root-only failure.
4. [ ] **Contract sync.** `docs/API_CONTRACT.md` and `monitor/fixtures/` must include:
   - `GET /system/limits.revision`;
   - `GET /jobs` → `{jobs, index_ok}` plus `fixtures/jobs.json`;
   - `GET /system/health.profile`.
   Remove `ui/BACKEND_GAPS.md` or bring it up to date. Done when the UI contract tests and the API contract tests both read the same
   fixtures and pass.
5. [ ] **Real integration.**
   - Start `python -m uvicorn monitor.api:app --port 8765` with a temporary `SATVIK_HOME` seeded from one real archived job (copy a
     `results/<job_id>/` from an evidence branch).
   - Run the UI e2e in LIVE mode against it instead of `tests/fake_fastapi_server.mjs`: add a Playwright project or config that targets the
     real API.
   - Fix every mismatch in the API or the UI normalizer, never by editing fixtures to hide a bug.
   - Done when all 7 pages render real data, a failed verify shows not-verified, STOP toggles, a duplicate queue shows 409, and
     `npm run test:e2e` passes against the real API.
6. [ ] **One PR to `main`** with the API, the UI repair, the integration fixes and the contract sync. Done when it is merged and pinned to its
   head SHA.
7. [ ] **Windows check (Local agent 2):** a fresh worktree of `main`;
   - run `pip install -r monitor/requirements.txt`, then `python -m uvicorn monitor.api:app --port 8765` with the real `SATVIK_HOME`;
   - in `ui/`, run `npm ci` and then `npm run dev:live`;
   - open each of the 7 pages, take screenshots, and push them to branch `windows-ui-check-2026-11-04`.
   - Done when every page shows real data, with no console errors.
8. [ ] **Docs:** add an "As integrated" section to `docs/ARCHITECTURE_REVISION_4.md` (API and UI), tick this checklist, and record a STATUS
   entry. Done when merged.

## D. Issues register (every known issue; none may be skipped)
| # | Issue | Fix / owner | Done when |
|---|---|---|---|
| I1 | The archive copies `_loop/.gitignore` (`*`, written by `event_log`), so published evidence folders are git-ignored. Found in `ah-run-2026-11-04` | Add `.gitignore` to `launch.ARCHIVE_SKIP`, plus a test (item 3) | A test proves `results/<id>/.gitignore` is absent |
| I2 | The stages response has no **required contract** per stage, so the UI cannot filter which models are eligible (the UI repair noted it) | Add `required_contract` to each `GET /stages` row, and a fixture (item 4) | The UI shows only models whose required contract is `verified` |
| I3 | Planner calls emit no events (`plan_job` is not hooked) | `plan_job` emits `attempt-start` / `attempt-end` with stage `planner` (item 3) | A test sees planner events |
| I4 | Backup-writer review stage names are best effort (`reviewer_b` until a READY, then `reviewer_a`) | Record the real slot in `_review` / the events (item 3) | A test of a backup review shows the correct stage |
| I5 ✅ | Lease leak (fixed in #36) not yet confirmed in a real run. **Confirmed 2026-11-04:** 6 of 6 ah-* leases have `released: true` (branch `i5-lease-check`) | Check the `ah-run` leases: each job's own client lease is `released: true` | Confirmed from the evidence |
| I6 | `ui/BACKEND_GAPS.md` is out of date | Remove it or rewrite it after the integration (item 4) | No stale gap file |
| I7 ✅ | `npm ci` reports 5 moderate vulnerabilities | **Done 2026-11-04:** `npm audit --omit=dev` → 0 vulnerabilities (runtime clean). All 5 moderate are dev-only: `minimatch` under eslint and typescript-eslint, lint tooling only, not in the built app | Recorded |
| I8 | System limits were locked in the UI until `GET /system/limits` had a `revision` | The API adds `revision`; the UI enables the PUT (item 5) | The System page saves limits live, and a 409 shows on a stale revision |
| I9 | Plan approval became one `approve_assumed` boolean (per-item boxes removed) | Keep (matches the contract); document it (item 8) | In the contract notes |
| I10 | Decisions to document: the fallback-write settings merge, the `writers-share-model` rule, `waiting-unknown` / `stale-warning` semantics | `ARCHITECTURE_REVISION_4.md` "As integrated" (item 8) | Documented |
| I11 | Replies from the PC agents to this session fail ("not found") | Known platform limit; git branches are the report channel | Documented (no fix possible here) |
| I12 | The p2-api commits are only in this container's local worktree | Merge onto the pushed working branch as soon as the API agent reports (item 3) | On GitHub |
| I13 | The cloud suite has one root-only failure (`test_plan_job.py::…_read_only`) | Environmental; must pass on Windows (item 7) | Passes in the Windows suite run |
| I14 | Production `reviewer_b` is North, which doesn't work | Owner decision (section C) | — |
| I15 | `PUT /system/limits job_review_usd` is stored, but the engine's production gate still requires exactly 0.05, so a different value would not apply (found in the API report) | The API refuses any job_review_usd other than the engine's accepted value with `not-applied-by-engine`, until the engine reads it; the UI shows that reason | A test of the refusal; the UI shows the reason |
| I16 | Claude Code models report contracts as `verified` from the fixture, not from a measured probe: a false verification, the exact class of the UI audit's critical finding | Report `unverified` (with `text_validated` true) until a real `POST /models/verify` result is recorded for that model and contract | A test: a fresh Claude Code entry shows `unverified` |
| I17 | The API assumes a single uvicorn worker (a process-wide lock plus the idempotency store) | Document it, and pin `--workers 1` in the run command and in `monitor/requirements.txt` notes | In `API_CONTRACT.md` section 0 and the run command |
| I18 | Two `test_stage_config` tests (`test_with_default_stages_every_openrouter_request_body_is_byte_for_byte_todays[test/production]`) take exactly 60.01 s each: they wait out the real 60 s pace window instead of using a fake clock | Give the test client a fake clock/sleep, or a fresh ledger per request, so no real minute wait happens | Both run in under 2 s |

## C. Owner-only (not blocking)
- Delete `r72-token-budget` (`5905bda`) and `pilot-loop-status.solo.json`, and remove `wt-pilot-r5`. Review `wt-B-pilot*`.
- Production `reviewer_b`, the paid production run, and the December re-check of free models.
