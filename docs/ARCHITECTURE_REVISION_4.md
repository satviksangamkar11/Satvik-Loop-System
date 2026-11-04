# Satvik Loop: Architecture Revision 4 (as built, 3 Nov 2026)

This revision records what the code on `main` (`b96063e`) does, against Revision 3 (2 Nov). It adds no new design. The UI contracts are in
`docs/UI_ENGINE_HANDOFF.md`.

## Built as Revision 3 described
- **P0 gates:**
  - calibration with negative controls (an assertion failure is required);
  - a per-test JUnit verdict;
  - an acceptance lock, which ends `acceptance-tampered` if broken;
  - a review gate on green code only;
  - a Docker sandbox: no network, user 65534, all capabilities dropped, read-only root, `--ipc none`, limits, no fallback;
  - tested = reviewed = delivered hash.
- **P1a adapter:**
  - role keys, with the roster as the allow-list;
  - GLM pinned to Z.ai with no fallbacks;
  - raw errors kept, the `/api/v1/key` snapshot saved;
  - three kinds of 429 told apart;
  - reply checks, redaction, the `GPG_KEY` check;
  - the live daily free cap from `free_model_daily_requests.limit` (fallback 1000).
- **P1b routing:**
  - the fixed ladder, clean restore before a fresh write;
  - per-file lineage and a panel chosen by lineage;
  - the FIX FIRST evidence rule with one re-ask;
  - `CONTRACT_CHECK.md`;
  - job size (1,500 lines per unit / 800 per file), the deadline, `needs_review`.
- **P1c planner:** `claude -p` with no tools or MCP; `INTENT.json` and `raw_response.json` make it exactly-once; ASSUMED lines need approval;
  `planner-uncertain`. It is refused under the test profile.
- **P2 parallel:**
  - one shared ledger (15 requests a minute, the daily cap, a $0.50 daily review cap);
  - file claims, leases with heartbeat and takeover, fencing;
  - the registry with counters, refusal of a running duplicate;
  - one worktree per job, a merge queue with a full sandbox suite, the STOP switch.

## Changed by owner decision (not gaps)
| Revision 3 | Now |
|---|---|
| GLM is the paid repairer ($0.15 a job) | GLM is the paid **reviewer**, production only ($0.05 a job, $0.50 a day). Repair is a Claude Code role (Opus, switched off). Test has no paid calls |
| Reviewer North, backup Gemma | Test: `reviewer_b` Nemotron 3.5 Super, `reviewer_a` the non-author writer. Gemma dropped (never answered). Production `reviewer_b` is still North (owner choice pending) |
| writer_b Xing | Test: Nemotron 3 Ultra (Xing got 429s from the shared pool). Production: Xing |
| "NVIDIA left out" | Allowed in testing only |
| Fixed model families | Any role is any OpenRouter model or any Claude Code model (opus, sonnet, haiku, fable; low to max). It is changed in `config/roster.json` only. The test profile allows only haiku at high among Claude Code roles. A new model gets its own one-call gate |
| Shared lessons; registry reuse of a passed request | Not built, by decision |
| `DISPUTE.md` (GLM as repairer) | Not built: that role no longer exists |
| Bake-off pilot | Replaced by one real pilot job |
| 4 review passes | 2 FIX FIRST repair rounds and 2 repair calls per job |

## Added on 3 Nov
- **Green code is kept:** a FIX FIRST repair that turns the tests red restores the last green code and ends `review-open` with its findings.
  Reviewer replies are saved as `history/review_NN.txt`.
- **Repairs use SEARCH/REPLACE edits,** with one re-ask and then a whole-file fallback. The repair digest holds the assertion, stdout, stderr
  and the test source.
- **Launched jobs commit their own files,** and the merge check runs in the sandbox. The image has `git`, `pyyaml` and `jsonschema`, and the copy
  holds `config/`, `tests/` and `docs/`.
- **Every terminal job is archived** to `SATVIK_HOME/results/<job_id>/`. The merge outcome goes to `merge.json`. An archive failure never
  blocks a finish.
- **Waits are reported:** `status.json.waiting` (`model-slot`, `account-pace`, `429-backoff`, `retry-backoff`).
- **Queue dedupe:** active duplicates are refused (`try_enqueue`), and `rerun=True` runs a completed job again.
- **The client releases its own lease** at job end.

## Evidence (3 Nov, test profile, $0, 0 Opus calls)
| Run | Result | Evidence |
|---|---|---|
| Pilot r5 (`f7db576`) | `passed`: Dots wrote it, READY from Nemotron 3.5 Super and Nemotron 3 Ultra, all three hashes `43d11ff3…`, 3 free requests | branch `pilot-run-r5` |
| 2-loop r2 (`3705c89`) | 3 merged in order (`tl-roman`, `tl-slug`, `tl-index`), STOP honoured | branch `two-loop-run-2026-11-03-r2` |
| 4-loop (`9625f24`) | 4 parallel jobs merged: `fl-clamp`, `fl-dedupe`, `fl-chunk`, `fl-wordcount`. Four leases live at once (snapshot 22:36:55Z), no claim conflict, STOP honoured. **`fl-top` escalated**; its evidence was lost, which led to the archive fix (#36) | branch `four-loop-run-2026-11-03` |
| Docker suite on `main` | 1029 passed, 4 skipped, 1 failed. The failure is the known root-only `test_plan_job.py::…_read_only` in the cloud container | — |

## Open
- `fl-top` re-run, now with the archive, to see why it escalated.
- Owner cleanup: the `r72-token-budget` branch (`5905bda`), `pilot-loop-status.solo.json`, `wt-pilot-r5`. `wt-B-pilot*` need a review first.
- **Control Center:**
  - P1: the frozen per-job roster and stage configuration;
  - P2–P3: the local API, events and UI;
  - P4: a concurrent pressure test.
- **Deferred:** production `reviewer_b`, the paid production run, legacy cleanup, streaming abort, a launcher CLI, the December re-check (Dots'
  free access ends 31 Dec).

## As integrated (2026-11-04)

What commit `cc71779` does for the API and the UI, checked against `monitor/`, `scripts/source/` and `ui/`. Where `docs/API_CONTRACT.md`
and the code differ, this section states the code.

### API (`monitor/`)

**Run it** from the repo root, with one worker only:

```
SATVIK_HOME=<folder outside every worktree> python -m uvicorn monitor.api:app --port 8765 --workers 1
```

The base URL is `http://127.0.0.1:8765/api`. CORS origins come from `SATVIK_UI_ORIGIN` (default `http://127.0.0.1:5173` and
`http://localhost:5173`).

**Why one worker.** The mutation lock (`monitor/store.py`, `_MUTATION_LOCK`) and the idempotency store are per process. With two workers,
two processes could write the same config at once, and a repeated `Idempotency-Key` could run twice.

**Environment**
- `SATVIK_HOME`: the loop home (`loop_home.home`), which must be outside every git worktree. Unset or refused, the routes that need it
  answer 503 `engine-unavailable`. `GET /system/health` still answers, with `satvik_home: null`.
- `SATVIK_REPO`: the repo root (`store.repo()`). It defaults to the checkout that holds `monitor/`. Job paths are resolved against it, and
  `engine_version` is `git rev-parse --short HEAD` run in it.
- `SATVIK_PROFILE`: `test` (the default) or `production`, read on each call. Under `test` the planner is refused (422
  `planner-refused-in-test`), paid roles are refused, and Claude Code verification runs only for haiku at high (422 `not-allowed-in-test`).

**Files are the source of truth.** Job views and events are read from the files on each request: `_loop/<job>/` and `wt/<id>/_loop/`
while a job runs, `SATVIK_HOME/results/<job_id>/` once it is archived, and the registry, lease and queue files. `SATVIK_HOME/history.db`
is an index only, and no reader depends on it. `index_ok` is true only when the file exists and answers a read-only query; it is false when
the file is missing, locked or corrupt. A false value changes where the data came from, not the answer. The engine's finish and merge hooks
write the index (`job_history.safe_index`, called from `launch.py` and `merge_queue.py`), and `rebuild_index` deletes and rebuilds it. The
API never builds it.

**Idempotency.** Every mutation that goes through `_run` (queue, start loops, stage edits, plans and their approval, model add, remove and
verify, stage save, limits, STOP) stores its first final response under its `Idempotency-Key`, in `SATVIK_HOME/api/idempotency/`. A repeat
with that key returns the stored response and runs nothing. Responses below 500 are stored, 4xx included; a 5xx is not stored, so a retry
runs again. A mutation sent without a key runs once and is not stored. The three check routes (`/jobs/validate`, `/stages/validate`,
`/budget/validate`) change nothing and take no key.

**Error envelope.** Every error is `{"error": {"code", "message", "details"}}`. A refusal by a rule is an `ApiError` with its own status and
code. A body that does not match the request is 400 `invalid-request`, with `details.problems`. An unknown route is 404 `not-found`, any
other HTTP error is `http-error`, and an unexpected exception is 500 `internal`, whose message is only the exception's type name.

### UI (`ui/`)

- `npm run dev` is the fixture-backed mock. `npm run dev:live` is `vite --mode live`, with `VITE_DATA_MODE=live` from `ui/.env.live`. The
  UI calls `http://127.0.0.1:8765/api` directly, so the API must be running first. Any value other than `mock` is live (`App.tsx`).
- **Fixture e2e:** `npm run test:e2e` runs `playwright.config.ts`. It starts `tests/fake_fastapi_server.mjs` and runs the UI in live mode
  against it: 4 tests on 3 viewports (1440, 768, 320), 12 in all.
- **Real-API e2e:** `npm run test:e2e:real` runs `playwright.live.config.ts`. First `ui/tests/seed_live_home.py` wipes and seeds a temporary
  `SATVIK_HOME` and `SATVIK_REPO`: it copies the archived job `ah-budget-20261104-0344-1119` from `origin/ah-run-2026-11-04` with
  `git show` (fetch that branch first), writes one registry row (`passed`), one expired released lease, and one queued job file. Then it
  starts uvicorn on 8765 with `SATVIK_PROFILE=test`, and Vite in live mode on 5173. No model call is made. 6 tests on the same 3 viewports,
  18 in all.
- **Fail-closed verify.** Only a response `result: "pass"` is shown as verified, and only `fail` as failed. Any other value, or none, is
  unverified (`ui/src/api/normalizer.ts`). After each verify the pool is reloaded, so the stored state is shown.
- **Stage eligibility.** A model is offered for a stage only when its `capabilities.contracts[required_contract]` is `verified` and its
  `verified_at` is set (`ui/src/api/eligibility.ts`). A stage row with no `required_contract` offers no model.
- **Locked rows are not sent.** `queueJob` sends only the rows with `model_selectable === true` (`ui/src/api/client.ts`). A locked row, such
  as `reviewer_a` under the test profile, is left out rather than sent as null.

### Decisions (I9, I10)

**Plan approval (I9).** `POST /plans/{id}/approve` takes one boolean, `approve_assumed`, for all the ASSUMED lines of the plan. If the plan
has any and the value is not `true`, the reply is 422 `assumed-not-approved`, with the lines in `details.assumed`. There are no per-line
boxes. Approval also queues the job and starts a loop, and `job_sha256` must still match the job file (409 `plan-changed`).

**Fallback write settings (I10).** When Reviewer B writes because Writer B is down (`stage_config._fallback`):
- the model and via are Reviewer B's;
- the settings are Reviewer B's role settings for the mode (a repair takes the profile's repair `max_tokens`), then the `reviewer_b` stage's
  settings, then the Writer B stage's settings (`writer_b_write` or `writer_b_repair`). Writer B's overrides win;
- the effort is Writer B's stage effort when both stages use the same via. Otherwise a Claude Code Reviewer B keeps its own `reviewer_b` stage
  effort (I23), and an OpenRouter Reviewer B takes the default effort for the mode;
- `review_fix_repair` follows the same rule when its current writer is Reviewer B.

**Writers share a model.** `stage_config._problems` refuses with `writers-share-model` when a writer stage of one writer and a writer stage
of the other resolve to the same model. Two stages of the same writer may share one. Reviewer stages are checked the same way, with
`reviewer-is-writer` and `reviewers-same-model`.

**waiting-unknown and stale-warning** (`scripts/source/job_history.py`, `monitor/views.py`):
- `waiting` needs a reason: `status.json` `waiting.why`, or an open wait event. `why` is one of `model-slot`, `account-pace`,
  `429-backoff` or `retry-backoff`; any other value is shown as null.
- `waiting-unknown`: the job is alive and waiting, and nothing gives a reason. No reason is invented.
- `stale-warning`: the lease is released or missing, or its pid is dead, or its heartbeat is older than three heartbeats (`STALE_WARN_S`,
  90 s). It is a warning, never "dead".
- `orphan`: only when the heartbeat is older than `loop_home.STALE_S` (300 s) and the pid is dead. It is checked before `stale-warning`.

### Known observations (I31 and others)

- **`index_ok: false` and `engine_version: null` on a fresh seeded home.** The seed writes no `history.db`, so `index_ok` stays false until
  an engine finish or merge hook indexes a job, or `rebuild_index` is run by hand. The seeded `SATVIK_REPO` is a plain folder, not a git
  checkout, so `engine_version` is null. Both are expected, and the API answers the same either way.
- **Archives made before the job file was archived have `spec` and `files` null.** The archive keeps `<name>.job.json` from commit `2cc5c96`
  (merged in `2c74b35`). The seeded `ah-budget` archive predates it, so its detail shows null for both.
- **Planner events** are written to the plan folder (`<repo>/_loop/<name>/`, named by the plan record), not to the job's own folders. The
  view reads them there (I21). They are not in `history.db`, which indexes only the job's own events.
- **Claude Code verify records per model and contract, not per effort.** `verified_at` is stored for the model and the contract. The effort
  is used for the probe only, so a pass at one effort is not recorded as tied to that effort.
- **`planner_json` has no Claude Code check.** A Claude Code model reports it `unverified`, so it is never eligible for the planner stage.

### Open items

- **I13:** the root-only test `test_plan_job.py::…_read_only` fails in the cloud container, because root can always write. It must pass
  in the Windows suite (checklist item 7).
- **I14:** production `reviewer_b` is still North, which does not work. This is an owner decision.
- **I19:** the code is done (`44fee49`). A real haiku/high verify on the PC is pending, by Local agent 2.
- **I20:** every OpenRouter catalog contract is still null. Local agent 2 verifies each free catalog model and contract against the real API
  (about 30 free calls) and publishes the results.
- **I11:** replies from the PC agents to this session fail with "not found". Git branches are the report channel; there is no fix here.
