# Satvik Loop Control Center: API contract v1 (for the front end)

This is the fixed boundary between the front end and the backend. The front end builds against it with the sample JSON in `monitor/fixtures/`, and
the backend (`monitor/api.py`, FastAPI, P2) implements it exactly, checked by contract tests against these fixtures. Engine internals may change;
these shapes do not without a version bump. The engine facts behind each field are in `docs/UI_ENGINE_HANDOFF.md`.

## 0. Conventions
- **Base URL:** `http://127.0.0.1:8765/api`, local only, no auth, CORS limited to the dev origin you give. Everything is JSON in UTF-8.
- **Formats:** times are ISO-8601 UTC (`2026-11-03T17:05:54Z`); money is USD as numbers; tokens are integers; unknown is `null`, never a guessed
  value.
- **Errors:** always `{"error": {"code": "<stable-code>", "message": "<human text>", "details": {…}}}`, with HTTP 400 (validation), 404
  (unknown id), 409 (state conflict or duplicate), 422 (refused by a rule), 503 (backend or engine unavailable).
- **Mutations:** send the header `Idempotency-Key: <uuid>`. A repeat with the same key returns the first response and does nothing again.
  This is how a double click stays safe.
- **Updates:** by polling; there is no push in v1.
  | What | Poll every |
  |---|---|
  | `GET /overview` | 5 s |
  | `GET /jobs` | 5 s |
  | `GET /loops/{id}` | 3 s |
  | `GET /loops/{id}/events?after=<seq>` | 2 s (returns only new events) |
  | everything else | on page load, or after an action |
- **Index health:** every list response has `"index_ok": true|false`. With `false`, the data came straight from the files (slower, but still
  correct); show a small notice, not an error.

**Running the backend:** `pip install -r monitor/requirements.txt` (fastapi, uvicorn, httpx), then from the repo root
`SATVIK_HOME=<folder outside every worktree> python -m uvicorn monitor.api:app --port 8765`. The base URL is `http://127.0.0.1:8765/api`.
`SATVIK_UI_ORIGIN` (comma-separated) sets the CORS origins; the default is the two local dev origins on port 5173.

## 1. Enums (closed sets: show only these)
- `derived_state` (a job, as the UI shows it): `queued`, `running`, `waiting`, `waiting-unknown`, `stale-warning`, `orphan`, `finished`,
  `merged`.
  - `stale-warning` means the heartbeat is late but the process may still be alive: show a warning, never "dead".
  - `orphan` means only when the lease is stale **and** its pid is dead.
- `end_state` (with `finished`/`merged`): `passed`, `review-open`, `escalate`, `needs_review`, `blocked`, `provider-unavailable`,
  `budget-exceeded`, `quota-exhausted`, `deadline`, `integrity-mismatch`, `acceptance-tampered`, `exception`, `invalid-spec`, `job-too-large`,
  `sandbox-unavailable`, `acceptance-invalid`, `refused`, `stopped`.
- `merge_state`: `merged`, `merge-conflict`, `tests-failed`, `push-failed`, `main-moved`, `pending`, `none`.
- `wait_why`: `model-slot`, `account-pace`, `429-backoff`, `retry-backoff`.
- `stage` (the configurable types): `planner`, `writer_a_write`, `writer_a_repair`, `writer_b_write`, `writer_b_repair`, `reviewer_b`,
  `reviewer_a`, `reviewer_reask`, `specialist_repair`, `review_fix_repair`.
  - `fallback_write` is a runtime stage on the timeline only (Reviewer B writing). It is never a settings row.
- `attempt_status`: `running`, `ok`, `failed`, `rejected` (the reply could not be used).
- `via`: `openrouter`, `claude_code`.
- `effort` (OpenRouter): `off`, `low`, `medium`, `high`. `effort` (Claude Code): `low`, `medium`, `high`, `xhigh`, `max`. **Show only the
  efforts a model's `capabilities` list.**
- `contract`: `planner_json`, `writer_file_blocks`, `repair_edit_blocks`, `repair_whole_file`, `reviewer_verdict`, `native_structured_output`.
- `contract_state`: `verified`, `failed`, `unverified`, `not-applicable`. A model is eligible for a stage only when that stage's contract is `verified`.
  - Claude Code models use `text_validated` instead: the reply is checked with the same text parsers, and there is never `native_structured_output`.
- `reasoning_mode`: `optional` (on/off verified), `mandatory` (it cannot be turned off), `unsupported`, `unknown`. Show only the switch states the mode allows.
- `start_refusal`: `stop-file`, `provider-unavailable`, `free-requests`, `review-spend`, `request-running`, `no-sandbox-slot`, `claim-conflict`,
  `no-budget-snapshot`.

## 2. Read models

### JobSummary (in lists)
```json
{"job_id": "fl-top-20261103-2309-7d15", "name": "fl-top", "derived_state": "merged", "end_state": "passed", "merge_state": "merged",
 "active_stage": null, "active_model": null, "waiting": null, "heartbeat_age_s": null, "started": "2026-11-03T17:39:10Z",
 "finished": "2026-11-03T17:43:02Z", "free_requests_used": 3, "review_usd_spent": 0.0}
```

### JobDetail (`GET /loops/{id}`)
JobDetail is a JobSummary plus these fields:
```json
{"profile": "test", "spec": "docs/pilot/four-loop/FL_TOP_SPEC.md", "files": ["scripts/source/fl_top.py", "pieces/test_fl_top.py"],
 "reason": "READY from: nvidia/nemotron-3.5-super-vl:free, nvidia/nemotron-3-ultra-550b-a55b:free",
 "tests_passed": true, "failing_tests": [], "review_verdict": "READY", "findings": [],
 "stage_snapshot": {"sha256": "…", "revision": 1},
 "stages": [{"stage": "writer_a_write", "status": "ran", "attempts": 1}, {"stage": "writer_a_repair", "status": "not-triggered", "attempts": 0}],
 "lease": {"held": false, "pid": 1234, "pid_alive": false, "heartbeat": "2026-11-03T17:43:00Z", "generation": 3},
 "budget": {"free_requests_used": 3, "free_requests": 40, "review_usd_spent": 0.0, "review_usd": 0, "repair_calls": 0},
 "hashes": {"tested": "43d1…", "reviewed": "43d1…", "delivered": "43d1…"},
 "archive": {"available": true, "archive_failed": false}}
```
`stages[].status` is one of `ran`, `running`, `not-triggered`, `pending`. **All ten stages are always listed**: a planner that was never requested is `not-triggered`.

### Attempt event (`GET /loops/{id}/events`)
```json
{"seq": 7, "ts": "2026-11-03T17:40:12Z", "type": "attempt-end", "attempt_id": "a3", "stage": "reviewer_b", "role": "reviewer_b",
 "mode": "review", "model": "nvidia/nemotron-3.5-super-vl:free", "via": "openrouter", "effort": "low", "status": "ok",
 "prompt_tokens": 10099, "completion_tokens": 420, "reasoning_tokens": 300, "finish_reason": "stop", "seconds": 41.2, "error": null}
```
- **Event types:**
  - `job-start`
  - `attempt-start`, `attempt-end`
  - `wait-start` / `wait-end` (`why`, `model`)
  - `tests` (`green`, `failing[]`)
  - `review` (`reviewer`, `verdict`, `evidence_ok`)
  - `restore-green`
  - `finish` (`end_state`, `reason`)
  - `merge` (`merge_state`, `head`, `attempts`)
- **Rendering rules:**
  - A stage repeated N times appears N times.
  - A stage that never ran has **no** events.
  - The response is `{"events": [...], "next_after": <seq>, "index_ok": true}`.

### Overview (`GET /overview`)
```json
{"loops_running": 2, "queue_depth": 1, "jobs_waiting": 1, "active_errors": 0, "stop": false,
 "free_today": {"used": 87, "cap": 1000, "cap_source": "live"}, "pace": {"sent_last_60s": 4, "limit": 15},
 "review_usd_today": {"spent": 0.0, "cap": 0.5}, "pending_reviews": 1, "sandbox_slots": {"used": 2, "total": 4},
 "updated": "2026-11-03T17:45:00Z", "index_ok": true}
```

### Model (`GET /models`)
Each entry also has `endpoint` (provider slug, or null), `contracts.<contract> = contract_state`, and `reasoning_mode`. A stage's `max_tokens` must not exceed the model's `max_output_tokens`: `/stages/validate` refuses it with `over-max-output`.
```json
{"model_id": "nvidia/nemotron-3-ultra-550b-a55b:free", "via": "openrouter", "hosting_provider": null, "paid": false,
 "price_in_usd_per_mtok": 0, "price_out_usd_per_mtok": 0, "context_tokens": 262144, "max_output_tokens": 32768,
 "capabilities": {"reasoning_on_off": true, "efforts": ["off", "low", "medium", "high"], "token_budget": true,
                  "temperature": {"min": 0, "max": 2}, "structured_outputs": null,
                  "contracts": {"writer_file_blocks": true, "repair_edit_blocks": null, "reviewer_verdict": true}},
 "verified_at": "2026-11-03T16:00:00Z", "metadata_at": "2026-11-03T15:00:00Z"}
```
- A Claude Code model has `"via": "claude_code"`, `efforts` from `low` to `max`, and every price `null`. Show "subscription", never a dollar
  figure.
- **Any capability that is absent or `null` is not selectable.**

### Stage settings (`GET /stages?profile=test`)
```json
{"profile": "test", "revision": 4, "stages": [
 {"stage": "writer_b_write", "model": "nvidia/nemotron-3-ultra-550b-a55b:free", "via": "openrouter", "model_selectable": true,
  "effort": "high", "settings": {"temperature": 0.1, "max_tokens": 40000, "reasoning": {"max_tokens": 8000}}, "source": "role:writer_b"},
 {"stage": "reviewer_reask", "model": null, "model_selectable": false, "inherits": "triggering reviewer", "effort": "low", "settings": {}}]}
```

### Results (`GET /results/{id}`)
```json
{"job_id": "…", "end_state": "escalate", "merge_state": "none", "tests": {"passed": false, "failing": [{"id": "tests/acceptance/x.py::test_a",
 "message": "assert 0 == 3", "file": "tests/acceptance/x.py", "line": 41}]}, "reviews": [{"reviewer": "…", "verdict": "FIX FIRST",
 "findings": ["scripts/source/m.py:12 | SPEC 3: … | …"], "file": "history/review_02.txt"}], "diff_available": true,
 "artifacts": ["status.json", "lineage.json", "history/call_01.json", "ESCALATE.md"], "branch": "job/…", "merge": null}
```

### System (`GET /system/health`, `GET /system/limits`)
- **`health`:**
  ```json
  {"backend": "ok", "engine_version": "<main sha>", "docker": "ok|down", "sandbox_image": "ok|missing", "satvik_home": "<path>",
   "index_ok": true, "keys": [{"alias": "free", "present": true}, {"alias": "paid", "present": false}], "stop": false}
  ```
  Key values are never returned.
- **`limits`:**
  ```json
  {"leases": [{"job_id": "…", "held": true, "heartbeat_age_s": 4, "pid_alive": true}], "claims": [{"path": "…", "job_id": "…"}],
   "gates": [{"gate": "nemotron", "holders": 1, "capacity": 1}], "ledger": {"free_used": 87, "free_cap": 1000, "review_spent_usd": 0.0}}
  ```

## 3. Actions

| UI action | Route | Request | Success | Errors | Allowed when | Side effects | Status |
|---|---|---|---|---|---|---|---|
| Validate job | `POST /jobs/validate` | `{"job": {...}}` or `{"job_path": "..."}` | `{"valid": true, "problems": []}` | 400 `invalid-job` (`details.problems[]`) | always | none | v1 |
| Queue job | `POST /jobs` | `{"job_path": "...", "rerun": false, "stages": {"<stage>": {"model", "effort", "settings"}}?, "stage_revision": n, "paid_budget_usd": 0}`. The backend validates the stages, then **freezes** the resolved snapshot atomically with the queued job. That snapshot is used at start, never the defaults read at that later time. Response: `stage_snapshot {sha256, revision}` | 201 `{"queued": true, "queue_file": "..."}` | 409 `duplicate-pending` / `duplicate-running`, 422 `already-completed` (offer "Run again" → `rerun: true`) | not STOP | file in queue | v1 |
| Start loops | `POST /loops/start` | `{"loops": 1-4}` | 202 `{"started": n}` | 409 `stop-on`, 422 a `start_refusal` code | queue not empty | background loop processes | v1 |
| Plan | `POST /plans` | `{"name", "request", "files": [≤30]}` | 202 `{"plan_id", "state": "planning"}`, later `GET /plans/{id}` → `{"state": "planned", "assumed": [...], "job_sha256"}` | 422 `planner-refused-in-test`, 409 `planner-uncertain` | production profile | **1 Opus call (paid)** | v1 (blocked under test) |
| Approve plan | `POST /plans/{id}/approve` | `{"job_sha256", "approve_assumed": true}` | 202 `{"job_id"}` | 409 `plan-changed`, 422 `assumed-not-approved` | planned | **approval also starts the job** | v1 |
| Edit future stage | `PATCH /loops/{id}/stages` | `{"stage", "change": {...}, "revision"}` | `{"ok": true, "revision": n}` | 409 `stage-started` / `stale-revision`, 422 a validation code, `model-not-selectable` | the stage hasn't started | a new snapshot revision for that job only | v1 (after P1) |
| Search the OpenRouter catalog | `GET /models/search?q=` | — | `{"results": [Model…]}` (not yet in the pool) | 503 | key present | 1 GET | v1 |
| Add a model to the pool | `POST /models` | `{"model_id"}` | 201 Model (contracts `unverified`) | 409 `already-in-pool`, 422 `pool-full` (max 20), `paid-without-pin` | always | edits `config/models.json` | v1 |
| Remove a model | `DELETE /models/{id}` | — | 204 | 409 `model-in-use` (`details.stages[]`), 422 `pool-min` (min 10) | not used by any stage | edits `config/models.json` | v1 |
| Test reasoning | `POST /models/verify-reasoning` | `{"model_id", "efforts": [...]}` | `{"reasoning_mode", "efforts_verified": [...]}` | 503 | key present | ≤1 free call per effort | v1 |
| Validate paid budget | `POST /budget/validate` | `{"stages": {...}, "paid_budget_usd"}` | `{"valid", "worst_case_usd", "problems": []}` | 400 `budget-over-cap`, 422 `paid-in-test` | always | none | v1 |
| Update limits | `PUT /system/limits` | `{"revision", "sandbox_slots"?: 1-4, "review_usd_daily_cap"?, "job_review_usd"?}` | `{"revision": n+1}` | 409 `stale-revision`, 422 `out-of-range` | no job running for slots | edits config; applies to new starts only | v1 |
| Validate stage settings | `POST /stages/validate` | `{"profile", "changes": [...]}` | `{"valid": true, "problems": []}` | 400 with `problems[{code, stage, message}]` | always | none | v1 |
| Save stage settings | `PUT /stages` | `{"profile", "revision", "changes": [...]}` | `{"revision": n+1}` | 409 `stale-revision`, 400 validation | always | affects **future jobs only**; running jobs keep their snapshot | v1 |
| Verify model | `POST /models/verify` | `{"model_id", "contract"}` | `{"result": "pass\|fail", "verified_at"}` | 422 `not-in-catalog`, 503 provider | key present | 1 free call | v1 |
| Refresh prices and metadata | `POST /models/refresh` | `{}` | `{"updated": n, "metadata_at"}` | 503 | key present | 1 GET to OpenRouter | v1 |
| Global STOP | `POST /system/stop` | `{"on": true\|false}` | `{"stop": true}` | — | always | no new starts, no next round. **A model call in flight is not interrupted** | v1 |
| Download results | `GET /results/{id}/download` | — | a zip of `results/<job_id>/` | 404 `not-archived` | after finish | none | v1 |
| Diff | `GET /results/{id}/diff` | — | `{"files": [{"path", "unified_diff"}]}` | 404 | after finish | none | v1 |
| Change an in-flight model, Cancel one job, Resume a failed job, Manual merge | — | — | — | — | **never** | — | **not offered: no button** |

## 4. UI rules that come from the engine
0. **`model_selectable` is profile-specific.** It comes from the response, never hard-coded: Reviewer A is a fixed sentinel only in the test profile, and Re-ask and Review-fix always inherit their model.
1. **Global STOP is soft.** A running model call finishes. Show "stopping after the current step".
2. **Approving a plan starts the job.** Say so on the button.
3. **The merge is automatic** after a pass. Show `merge_state`; there is no Merge button.
4. **Settings saved on Stage Settings apply to new jobs.** A running job changes only through "Edit future stage", and only for stages that
   haven't started.
5. **The timeline shows only recorded events.** Stages with no events are "not triggered". `fallback_write` appears when Reviewer B wrote.
6. **A paid model or the Opus planner** is shown as paid. The test profile refuses paid: grey it out, with the reason
   `test profile: free only`.
7. **Waiting with no reason** is `waiting-unknown`. A late heartbeat is `stale-warning`. Only `orphan` means dead.

## 5. Fixtures (sample responses in `monitor/fixtures/`)
`running_job.json`, `completed_job.json`, `failed_review_job.json`, `quota_refusal.json`, `untriggered_stage_job.json`,
`recovered_merge_job.json`, `overview.json`, `models.json`, `stages.json`, `results_escalate.json`, `system_health.json`, `error_examples.json`.
