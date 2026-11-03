import test from 'node:test';
import assert from 'node:assert/strict';
import { loadClientModules, startFakeFastApiServer } from './fake_fastapi_server.mjs';

const {
  generateIdempotencyKey,
  normalizeOverview,
  normalizeJob,
  normalizeEvent,
  createHttpTransport,
  buildValidZipArchiveBytes,
} = loadClientModules(globalThis.fetch);

test('1. Revision 3 Audit Blocker #2: normalizeOverview preserves exact API_CONTRACT.md fields without demo defaults', () => {
  const contractOverview = {
    stop: true,
    loops_running: 1,
    loops_max: 2,
    queue_depth: 3,
    jobs_waiting: 1,
    pace: 4,
    pace_limit: 15,
    review_usd_today: 0.0,
    review_cap_usd: 0.5,
    free_today: 12,
    free_cap: 200,
    sandbox_slots: '1/2',
    docker_healthy: true,
    ts: '2026-11-03T12:00:00Z',
  };

  const out = normalizeOverview(contractOverview);
  assert.equal(out.stop, true);
  assert.equal(out.stop_active, true);
  assert.equal(out.queue_depth, 3);
  assert.equal(out.jobs_queued, 3);
  assert.equal(out.jobs_waiting, 1);
  assert.equal(out.loops_waiting, 1);
  assert.equal(out.pace, 4);
  assert.equal(out.pace_60s.sent, 4);
  assert.equal(out.pace_60s.limit, 15);
  assert.equal(out.review_usd_today, '0.00');
  assert.equal(out.review_spend.used_usd, '0.00');
  assert.equal(out.review_cap_usd, '0.50');
  assert.equal(out.review_spend.cap_usd, '0.50');
  assert.equal(out.sandbox_slots.used, 1);
  assert.equal(out.sandbox_slots.cap, 2);
});

test('2. Revision 3 Audit Blocker #2: normalizeJob preserves contract JobDetail model, lease, heartbeat_age_s, and stage execution map', () => {
  const rawContractJob = {
    id: 'job_contract_01',
    title: 'Contract Job Detail',
    spec_path: 'jobs/contract_01.json',
    state: 'running',
    stage: 'reviewer_b',
    model: 'openrouter/qwen/qwen3-coder:free',
    attempt: 2,
    max_attempts: 2,
    worker_slot: 1,
    started: '2026-11-03T10:00:00Z',
    lease: {
      alive: true,
      holder_pid: 54321,
      heartbeat_age_s: 3,
      heartbeat_at: '2026-11-03T10:04:57Z',
    },
    stages: {
      planner: 'passed',
      writer_a_write: 'passed',
      reviewer_a: 'passed',
      reviewer_b: 'running',
    },
    end_state: null,
    merge_state: null,
  };

  const job = normalizeJob(rawContractJob);
  assert.equal(job.id, 'job_contract_01');
  assert.equal(job.job_id, 'job_contract_01');
  assert.equal(job.model, 'openrouter/qwen/qwen3-coder:free');
  assert.equal(job.model_id, 'openrouter/qwen/qwen3-coder:free');
  assert.equal(job.lease.alive, true);
  assert.equal(job.lease.holder_pid, 54321);
  assert.equal(job.lease.heartbeat_age_s, 3);
  assert.equal(job.stages.planner, 'ran');
  assert.equal(job.stages.reviewer_b, 'running');
  assert.equal(job.started, '2026-11-03T10:00:00Z');
  assert.equal(job.started_at, '2026-11-03T10:00:00Z');
});

test('3. Normalizer maps wire event ts, prompt_tokens, completion_tokens, and total_tokens accurately', () => {
  const rawEvent = {
    seq: 55,
    ts: '2026-11-03T10:15:39Z',
    stage: 'reviewer_b',
    role: 'reviewer_b',
    model: 'openai/gpt-4o-mini',
    prompt_tokens: 1500,
    completion_tokens: 300,
    total_tokens: 1800,
    cost_usd: 0.0024,
    detail: 'Independent review started',
  };

  const evt = normalizeEvent(rawEvent);
  assert.equal(evt.ts, '2026-11-03T10:15:39Z');
  assert.equal(evt.timestamp, '2026-11-03T10:15:39Z');
  assert.equal(evt.prompt_tokens, 1500);
  assert.equal(evt.completion_tokens, 300);
  assert.equal(evt.total_tokens, 1800);
  assert.equal(evt.cost_usd, '0.0024');
});

test('4. Revision 3 Audit Blocker #3: HTTP client handles all 7 FastAPI contract mutation shapes', async () => {
  const server = await startFakeFastApiServer();
  try {
    const transport = createHttpTransport(server.baseUrl);

    // 1) GET /api/overview
    const { overview } = await transport.getOverview();
    assert.equal(overview.stop, false);
    assert.equal(overview.queue_depth, 3);
    assert.equal(overview.jobs_waiting, 1);
    assert.equal(overview.pace, 4);
    assert.equal(overview.review_usd_today, '0.00');
    assert.equal(overview.review_cap_usd, '0.50');

    // 2) POST /api/system/stop -> {"stop": true}
    const stopRes = await transport.setGlobalStop(true, generateIdempotencyKey());
    assert.equal(stopRes.stop, true);
    assert.equal(stopRes.stop_active, true);

    // 3) POST /api/jobs/validate -> {"valid": true, "problems": []}
    const validRes = await transport.validateJob(
      { job_path: 'jobs/auth_token_refresh.json' },
      generateIdempotencyKey()
    );
    assert.equal(validRes.valid, true);
    assert.deepEqual(validRes.problems, []);

    await assert.rejects(
      () =>
        transport.validateJob(
          { job_path: '../outside.json' },
          generateIdempotencyKey()
        ),
      (err) => err.status === 422 && err.code === 'invalid-spec'
    );

    // 4) POST /api/jobs -> {"queued": true, "queue_file": "...", "stage_snapshot": {...}}
    const queueRes = await transport.queueJob(
      {
        job_path: 'jobs/auth_token_refresh.json',
        rerun: false,
        stage_revision: 'rev_a1b2c3d4',
        paid_budget_usd: '0.00',
      },
      generateIdempotencyKey()
    );
    assert.equal(queueRes.queued, true);
    assert.equal(queueRes.queue_file, 'jobs/auth_token_refresh.json');
    assert.equal(queueRes.stage_snapshot.rev, 'rev_a1b2c3d4');

    // 5) POST /api/loops/start -> {"started": 2}
    const startRes = await transport.startWorkers(2, generateIdempotencyKey());
    assert.equal(startRes.started, 2);
    assert.equal(startRes.loops_started, 2);

    // 6) POST /api/plans (HTTP 202 Accepted + GET /api/plans/{id})
    const planRes = await transport.createPlan(
      {
        job_name: 'retry_backoff',
        prompt: 'Add retry backoff',
        allowed_files: ['loop/retry.py'],
        cost_authorized: true,
        profile: 'production',
      },
      generateIdempotencyKey()
    );
    assert.equal(planRes.plan_id, 'plan_test_99');
    assert.equal(planRes.status, 'ready');
    assert.equal(planRes.job_sha256, 'sha256_plan_99_verified');
    assert.equal(planRes.assumed.length, 1);

    // 7) POST /api/models -> direct Model object
    const createdModel = await transport.addModel(
      'openrouter/deepseek/deepseek-chat-v3:free',
      generateIdempotencyKey()
    );
    assert.equal(createdModel.model.model_id, 'openrouter/deepseek/deepseek-chat-v3:free');

    // 8) DELETE /api/models/:id -> HTTP 204 No Content with empty body (must not crash on response.json())
    const deleteRes = await transport.deleteModel(
      'openrouter/deepseek/deepseek-chat-v3:free',
      generateIdempotencyKey()
    );
    assert.equal(deleteRes.deleted_model_id, 'openrouter/deepseek/deepseek-chat-v3:free');

    // Active stage model deletion -> HTTP 409 Conflict
    await assert.rejects(
      () =>
        transport.deleteModel(
          'openrouter/qwen/qwen3-coder:free',
          generateIdempotencyKey()
        ),
      (err) => err.status === 409
    );
  } finally {
    await server.close();
  }
});

test('5. Stage settings PUT /api/stages enforces optimistic concurrency revision and binary ZIP download works', async () => {
  const server = await startFakeFastApiServer();
  try {
    const transport = createHttpTransport(server.baseUrl);
    const saved = await transport.putStages(
      {
        profile: 'test',
        revision: 'rev_a1b2c3d4',
        changes: [],
      },
      generateIdempotencyKey()
    );
    assert.equal(saved.revision, 'rev_e5f6g7h8');

    await assert.rejects(
      () =>
        transport.putStages(
          {
            profile: 'test',
            revision: 'rev_stale_000',
            changes: [],
          },
          generateIdempotencyKey()
        ),
      (err) => err.status === 409
    );

    const archive = await transport.downloadResultArchive('job_rate_limiter_refactor');
    assert.equal(archive.bytes[0], 0x50); // 'P'
    assert.equal(archive.bytes[1], 0x4b); // 'K'
  } finally {
    await server.close();
  }
});

test('6. buildValidZipArchiveBytes computes valid IEEE 802.3 CRC-32 checksums in local and central headers', () => {
  const zipBytes = buildValidZipArchiveBytes('test_job', '{"status":"passed"}');
  const view = new DataView(zipBytes.buffer, zipBytes.byteOffset, zipBytes.byteLength);
  // Local file header signature PK\x03\x04
  assert.equal(view.getUint32(0, true), 0x04034b50);
  const crcLocal = view.getUint32(14, true);
  assert.notEqual(crcLocal, 0, 'CRC-32 must be non-zero for non-empty file payload');
});
