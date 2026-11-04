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
| Reviewer North, backup Gemma | Test: `reviewer_b` Nemotron 3.5 Super, `reviewer_a` the non-author writer. Gemma dropped (never answered). Production `reviewer_b` is Nemotron 3 Ultra (`nvidia/nemotron-3-ultra-550b-a55b:free`, owner decision 2026-11-04, I14), replacing North. Settings: max_tokens 32000, reasoning max_tokens 8000, timeout 1200 s, temperature 0.1, cap 72000; free, no pinned provider. Reason: Ultra is verbose (about 28K output tokens a task), and North ran to the 16K cap with no verdict twice |
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
write the index (`job_history.safe_index`, called from `launch.py` and `merge_queue.py`), and `rebuild_index` deletes and rebuilds it.
At API startup (I32) the index is checked: when `SATVIK_HOME` resolves and `history.db` is missing or corrupt, `job_history.ensure_index`
rebuilds it from the files and creates its schema, so an empty home gets a usable (empty) index. A healthy index is never touched, and a
locked one is left alone (another process may hold it). The check never raises; a failure is logged and the API starts with `index_ok`
false. With `SATVIK_HOME` unset the API starts with no index. By hand: `python scripts/source/job_history.py rebuild` prints the job count.

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

- **`index_ok: false` and `engine_version: null` on a fresh seeded home (I31; `index_ok` resolved by I32).** The seed writes no
  `history.db`. Since I32 the API builds it at startup when `SATVIK_HOME` is set (an empty index on a home with no jobs, otherwise the
  jobs are indexed from the files), so `index_ok` is true after the first start. Before that start, or with `SATVIK_HOME` unset, it is
  false. The seeded `SATVIK_REPO` is a plain folder, not a git checkout, so `engine_version` is still null. That part is expected, and the
  API answers the same either way.
- **Archives made before the job file was archived have `spec` and `files` null.** The archive keeps `<name>.job.json` from commit `2cc5c96`
  (merged in `2c74b35`). The seeded `ah-budget` archive predates it, so its detail shows null for both.
- **Older merged jobs show Unknown end state, tests and review verdict (I35).** Their folders hold no finish event, status.json or tests or
  review event, so nothing in the files can give them. The registry row is overwritten by the merge outcome (state `merged`, with head
  and attempts), so the run's own end state is gone from it, and the worktree is removed at finish. The view reads these facts only from
  events.jsonl, status.json and results/ (the archive), so it shows Unknown, and it never infers them from the merge. A job whose evidence
  was recovered into results/ (fl-top on the PC) shows them. Its finish time comes from the registry row (I35), which for a merged job is
  the merge time.
- **Planner events** are written to the plan folder (`<repo>/_loop/<name>/`, named by the plan record), not to the job's own folders. The
  view reads them there (I21). They are not in `history.db`, which indexes only the job's own events.
- **Claude Code verify records per model and contract, not per effort.** `verified_at` is stored for the model and the contract. The effort
  is used for the probe only, so a pass at one effort is not recorded as tied to that effort.
- **`planner_json` has no Claude Code check.** A Claude Code model reports it `unverified`, so it is never eligible for the planner stage.

### Paid-call safety (Batch A: I36, I44, I48, I49, I50)

Code: `scripts/source/stage_config.py`, `openrouter_call.py`, `shared_limits.py`, `loop_home.py` (`process_start`), `monitor/actions.py`,
`monitor/config_api.py`. Tests: `pieces/test_batch_a_paid.py` (22 tests; 19 fail on the code before this batch).

- **I36, billing is the model's.** A stage's paid flag, pin and prices come from `config/models.json` and, where the roster also names the
  model, the roster must agree. They are frozen in the snapshot as `billing`. `stage_config` refuses a paid model on a non-reviewer stage
  (`paid-model-stage`), a stage whose role's paid flag differs from its model's (`billing-mismatch`), and a paid model with no pin or prices
  (`unpriced-paid-model`, `unknown-billing` when the sources disagree). `openrouter_call.call` checks the stage against the allow-list before
  any reservation or POST. *Limit:* a model that neither the catalog nor the roster names keeps its role's values in a stage. Such a call is
  refused as not on the allow-list before any send, so nothing is sent.
- **I44, the approved ceiling.** `POST /jobs` with a paid stage, or with `paid_budget_usd` above zero, always runs `validate_budget`. An
  unpriced answer (`valid: false`) refuses the job (422 `budget-unknown`). A paid stage with `paid_budget_usd` 0 is refused (400
  `budget-over-cap`). The approved ceiling, the lower of the budget and the job review ceiling, is frozen as `paid_ceiling_usd` in the stage
  snapshot. Plan approval approves the planned job's declared `budget.review_usd` the same way. Each paid call reserves against the lower of
  the job's review budget and that ceiling. The shared ledger also checks the job's booked spend plus open holds, so a restart does not reset
  it. *Limits:* a direct run with no queued snapshot has no ceiling and keeps its job file's review budget (0.05 in production), as before.
  The validation estimate uses the model's agreed price. The engine reserves at the higher of that and the live endpoint price, so a call
  whose live price is higher is refused by the engine, not sent.
- **I48, the pace.** The account pace is taken inside the model slot, immediately before the POST (`openrouter_call._send`, `_pace`). A free
  request is counted for the day with `reserve_free_day` before the slot. A pace refusal is not a send: nothing is posted, and a paid hold is
  released at nothing. *Limit:* a request holds its model slot while it waits for the account pace, bounded by the unit deadline.
- **I49, the worst case.** The prompt bound is one token per UTF-8 byte plus 512 framing tokens (`prompt_bound_tokens`). It is priced at the
  higher of the roster's and the live endpoint's per-token price. The endpoint price is read again after 900 s, and an unknown price refuses
  the call before any POST. *Consequence:* for ASCII code the bound is about three times the old chars/3 estimate, so fewer paid reviews fit
  under the $0.05 job review ceiling. `test_p1b_review_package` now stops at the sixth reviewer attempt with `budget-exceeded`. *Limit:* the
  one-token-per-byte bound is assumed, not measured against each provider's tokenizer.
- **I50, holds.** Each paid reservation is a hold on `SATVIK_HOME/ledger/limits.json`, with a call id, its owner's pid and process start time
  (Linux; elsewhere the pid alone). Settling it releases that hold. A hold whose owner is dead and older than 300 s, or any hold older than
  3600 s, is released as spent for its whole amount. Open holds survive the 05:30 IST day change, and the spend booked per job id is kept.
  *Limits:* the reconciliation is conservative, not exact. A hold for a call that was never sent is booked at its full amount until it is
  released. The free daily count is never handed back. Without a Linux `/proc`, a reused pid can keep a dead owner's hold open until the
  one-hour limit.

### Batch C, Codex audit (I38, I39, I43, I46, I53; 2026-11-04)

- **I38, STOP in production.** The stop check reaches the P1b runner and is checked before each new model step, each repair step, and
  each retry or wait (the adapter's wait hook). A call already in flight finishes; its reply is kept, and the job ends `stopped` with its
  files restored. Remaining limit: the outer loop still checks STOP only between jobs, and a call in flight runs to its own timeout.
- **I39, merge sandbox.** `SUITE_INCLUDE` holds every folder the tests read by path (`pieces`, `scripts`, `config`, `tests`, `docs`,
  `monitor`, `ui`, `tools`, `specs`). The image pins fastapi 0.162.0, starlette 1.7.0 and httpx 0.28.1, each above its bound in
  `monitor/requirements.txt`. `pieces/test_sandbox_image_deps.py` fails when a suite import or a path is not covered. Remaining limit:
  uvicorn is not pinned (the suite does not import it); the pins are exact versions, so a new suite import needs a new pin.
- **I43, API-queued jobs.** A job queued with its stage table carries `api` in its queue name (`<ns>-api-<pid>-<name>.json`). It is refused
  (not run, reported `refused`) when its table is missing, corrupt, of the wrong shape, or was not moved with it. Remaining limit: the
  refusal is printed and returned by the drainer; it is not written to the registry, so the dashboard does not show it as a failed job. A
  job queued without a table (the CLI path) still reads the roster at start, as before.
- **I46, independence.** Lineage keeps, per job file, the served identity of every contributor (writers, the specialist repair step, the
  review-fix repair) under `served` in `lineage.json`. The panel excludes every contributing model. Claude Code identities are the
  requested model plus the models the CLI reports in `modelUsage`; that report is self-declared by the CLI and is not checked against the
  provider. OpenRouter identities are the model the adapter verified. Lineage files written before this change have no `served` record and
  fall back to their chain.
- **I53, controller paths.** A loop job may not write any `.py` directly in `scripts/source/`, nor any file under `monitor/`, `config/` or
  `tools/sandbox/`. Consequence: the pilot jobs under `docs/pilot/` (they develop `scripts/source` modules) are refused by the validator.
  Their historical loaders run with the rule waived (`prod_harness.controller_rule_waived`). A controller change needs a separate trusted
  workflow, which does not exist yet. Remaining limit: the rule covers the job's `files`, not what a reviewer or model may say about them.

### Batch C2, Codex audit recovery (I40, I41, I42, I47, I54, I56, I57, I63, I64, I65; 2026-11-04)

- **I40, durable merge queue.** Each submitted merge is `SATVIK_HOME/merge/<job_id>.json`, written before anything else. States: pending,
  merging (`head` recorded before the push), pushed, done. On a restart an entry in merging or pushed whose head is an ancestor of
  `origin/main` is recorded merged and not pushed again; any other is tried from the start. A git or I/O error is kept for a retry and, after
  three, finished as `merge-error` (kept for the owner). *Limit:* a crash between the push and the `done` write is reconciled by ancestry only;
  a rewritten `main` that drops the head is retried from the start.
- **I41, claims.** A taken job's claim is `SATVIK_HOME/queue/claims/<file>.claim` (claimed, running, done, refused), with the owner's pid and
  process start. A refusal for a busy resource or a limit goes back to the queue (the drain stops, the next start takes it); other refusals,
  invalid files and approved-plan mismatches go to `queue/refused/`, with the reason in the registry (`refused:<file>`). A claimed job whose
  owner died is requeued; a running one whose owner died is refused and never run again on its own (its paid calls may have run). An
  unreadable claim is refused. *Limit:* a crash between the rename into `taken/` and the claim write leaves a job with no claim, which is
  left in `taken/` for the owner to look at.
- **I42, one protocol.** pending -> claimed (`take_next`, under `queue/.lock`) -> running (`Registry.begin`, atomic under `home/lock`). The
  launcher's request check is `begin`, not a lookup then a record; a start refused after `begin` restores the row it replaced. Enqueue refuses
  an identical job that a live owner has claimed (`duplicate-claimed`).
- **I47, the merge carries the manifest.** A passed job is submitted with its files' reviewed hash and its locked acceptance tests with their
  hashes. After the rebase (and again before the push) the merge refuses (`reviewed-bytes-changed`) if the job's bytes or the locked tests
  differ, and it reruns the job's own acceptance tests before the full pieces suite. *Limit:* the reviewed hash is the one the run recorded;
  a job whose run recorded no evidence uses the worktree's bytes at the pass.
- **I54, report forgery (partly fixed; trust limit).** Generated code runs in the same pytest process that writes the JUnit report, so no
  host-side artifact can be fully independent of it. The controller's check now refuses a report with a case from a file that is not a
  locked test file, or whose declared count differs from its cases (`p0_gates.verify_junit`, tests in `pieces/test_i54_report_forgery.py`).
  **Trust limit:** a forgery that reports exactly the locked cases, all passing, is not detected. Closing it needs the result producer in a
  process the generated code cannot touch, which is a redesign; until then a green run is evidence only as far as the sandbox boundary holds.
- **I56, green checkpoint and bounds.** Each green run's files are written to `<job>/green/checkpoint.json` (atomic, with the tested hash)
  before any repair. `restore_green` falls back to it, and refuses a damaged one. An exception during a run puts the green candidate back
  before the failure snapshot. Sandbox captured output keeps its head and tail (2 MB), a JUnit report over 20 MB is not read, and a writable
  area over 512 MB fails the run. *Limit:* the writable bind and the output are bounded after the container exits, not during the run; the
  run's timeout bounds what a run can write in the meantime.
- **I57, clocks and pids.** The ledger keeps a persisted accounting epoch (`epoch` in `ledger/limits.json`): a clock that jumps back uses the
  latest instant seen, so the day counts reset once. A jump forward is kept too, so spend is never freed by a wrong clock until real time
  catches up. Leases, claims, gate slots and paid holds are alive only as the same process (pid AND process start, `loop_home.owner_alive`).
  *Limit:* without Linux `/proc` the start identity is unknown and the pid alone decides, as before.
- **I63, the hung-test case.** `DockerSandbox(label=...)` tags its containers; the hung-test case counts only its own label and waits up to
  60 s for the kill and removal to finish before asserting that none survived. The timeout and the JUnit checks are unchanged.
- **I64, planned jobs.** A planned job's installed spec, locked acceptance tests and negative-control sources are staged with its change, so
  the merge carries them (`fix_loop.installed_plan_paths`, passed to `write_change_doc`). Unplanned jobs stage only their own files, as before.
- **I65, legacy scrub.** `run_job` normalises its scrub keys with `redact.values_of`, so a `{slot: key}` dict contributes its key values
  (never its slot names), and it scrubs with `redact.scrub` (values and credential shapes). In this tree `fix_loop.main` loads the NVIDIA keys
  as a list; both shapes are handled.

### Open items

- **I13:** the root-only test `test_plan_job.py::…_read_only` fails in the cloud container, because root can always write. It must pass
  in the Windows suite (checklist item 7).
- **I14:** owner decision 2026-11-04: production `reviewer_b` is Nemotron 3 Ultra (`nvidia/nemotron-3-ultra-550b-a55b:free`), replacing
  North, which ran to the 16K cap with no verdict twice. Settings: max_tokens 32000, reasoning max_tokens 8000, timeout 1200 s,
  temperature 0.1, max_tokens_cap 72000 (Ultra is verbose, about 28K output tokens a task; the model's output limit is 65,536). The writers
  stay Dots and Xing, and reviewer_a stays GLM.
- **I19:** the code is done (`44fee49`). A real haiku/high verify on the PC is pending, by Local agent 2.
- **I20:** every OpenRouter catalog contract is still null. Local agent 2 verifies each free catalog model and contract against the real API
  (about 30 free calls) and publishes the results.
- **I11:** replies from the PC agents to this session fail with "not found". Git branches are the report channel; there is no fix here.
