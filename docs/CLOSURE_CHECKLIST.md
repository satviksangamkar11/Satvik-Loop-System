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
1. [ ] **`ah-shape` loop job.** Done when `scripts/source/api_shape.py` is on `origin/main`, and Local agent 1's evidence branch
   `ah-run-2026-11-04` is pushed. If it escalated, read its `results/<job_id>/` (`events.jsonl`, `review_NN.txt`, `test_NN.txt`), fix the spec
   or the test only if the test is wrong, and re-queue with `try_enqueue(..., rerun=True)`.
2. [ ] **The P2 API subagent finishes** on worktree branch `p2-api`, which already has 5+ commits. Done when it reports: every contract route
   is implemented, `pieces/test_api_contract.py` passes, and the full suite shows only the root-only failure.
3. [ ] **Merge `p2-api` onto the working branch,** and merge `origin/main` too, which brings in the loop helpers. Then point the API at
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

## C. Owner-only (not blocking)
- Delete `r72-token-budget` (`5905bda`) and `pilot-loop-status.solo.json`, and remove `wt-pilot-r5`. Review `wt-B-pilot*`.
- Production `reviewer_b`, the paid production run, and the December re-check of free models.
