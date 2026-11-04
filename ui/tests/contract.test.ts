/**
 * Unit tests against the canonical contract fixtures in monitor/fixtures/ (loaded directly, never rewritten).
 * Run: npm run test:unit
 */
import { describe, it, expect, vi, afterEach } from 'vitest';

import modelsFx from '../../monitor/fixtures/models.json';
import overviewFx from '../../monitor/fixtures/overview.json';
import stagesFx from '../../monitor/fixtures/stages.json';
import systemHealthFx from '../../monitor/fixtures/system_health.json';
import runningJobFx from '../../monitor/fixtures/running_job.json';
import untriggeredJobFx from '../../monitor/fixtures/untriggered_stage_job.json';
import resultsEscalateFx from '../../monitor/fixtures/results_escalate.json';
import jobsFx from '../../monitor/fixtures/jobs.json';

import {
  normalizeEventPage,
  normalizeJobSummary,
  normalizeJobDetail,
  normalizeLimits,
  normalizeModel,
  normalizeModelList,
  normalizeOverview,
  normalizeResult,
  normalizeStages,
  normalizeVerifyResult,
} from '../src/api/normalizer';
import { SatvikApiClient, buildModelPath, buildStagesPayload, buildStageChanges } from '../src/api/client';
import { UUID_V4_REGEX } from '../src/api/idempotency';
import { fmtInt } from '../src/api/format';
import type { DataMode, SemanticContract } from '../src/api/types';

const KEY = '3f2b8c1e-9a4d-4e6f-8b7a-1c2d3e4f5a6b';
const CONTRACT: SemanticContract = 'writer_file_blocks';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

type Captured = { method: string; url: string; headers: Record<string, string>; body: any };

/** A live-mode client whose fetch is replaced by `respond`. Every request is captured. */
function liveClient(respond: (c: Captured) => { status: number; body?: unknown }) {
  const captured: Captured[] = [];
  const fetchStub = vi.fn(async (url: string, init: RequestInit) => {
    const c: Captured = {
      method: init.method ?? 'GET',
      url,
      headers: (init.headers ?? {}) as Record<string, string>,
      body: init.body ? JSON.parse(String(init.body)) : undefined,
    };
    captured.push(c);
    const r = respond(c);
    if (r.status === 204) return new Response(null, { status: 204 });
    return new Response(JSON.stringify(r.body ?? {}), {
      status: r.status,
      headers: { 'Content-Type': 'application/json' },
    });
  });
  vi.stubGlobal('fetch', fetchStub);
  const mode: DataMode = 'live';
  const client = new SatvikApiClient({ baseUrl: 'http://127.0.0.1:8765/api', getMode: () => mode });
  return { client, captured };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

// ---------------------------------------------------------------------------
// 1. Fail-closed verification
// ---------------------------------------------------------------------------

describe('verify is fail-closed (POST /models/verify)', () => {
  const requested = { model_id: 'z-ai/glm-5.3-flash', contract: CONTRACT };

  it('marks a contract verified ONLY on result "pass"', () => {
    const out = normalizeVerifyResult({ result: 'pass', verified_at: '2026-11-03T18:00:00Z' }, requested);
    expect(out.state).toBe('verified');
    expect(out.verified_at).toBe('2026-11-03T18:00:00Z');
  });

  it('marks it failed on result "fail"', () => {
    const out = normalizeVerifyResult({ result: 'fail', verified_at: '2026-11-03T18:00:00Z' }, requested);
    expect(out.state).toBe('failed');
  });

  it('marks it unverified when the result is missing', () => {
    const out = normalizeVerifyResult({ verified_at: '2026-11-03T18:00:00Z' }, requested);
    expect(out.state).toBe('unverified');
    expect(out.verified_at).toBeNull();
  });

  it('marks it unverified when the body is empty or malformed', () => {
    expect(normalizeVerifyResult(undefined, requested).state).toBe('unverified');
    expect(normalizeVerifyResult(null, requested).state).toBe('unverified');
    expect(normalizeVerifyResult({ result: 'PASS' }, requested).state).toBe('unverified');
    expect(normalizeVerifyResult({ result: true }, requested).state).toBe('unverified');
  });

  it('the client reports verified only for a pass response from the wire', async () => {
    const pass = liveClient(() => ({ status: 200, body: { result: 'pass', verified_at: 't' } }));
    expect((await pass.client.verifyModelContract(requested, KEY)).state).toBe('verified');

    const fail = liveClient(() => ({ status: 200, body: { result: 'fail', verified_at: 't' } }));
    expect((await fail.client.verifyModelContract(requested, KEY)).state).toBe('failed');

    const missing = liveClient(() => ({ status: 200, body: {} }));
    expect((await missing.client.verifyModelContract(requested, KEY)).state).toBe('unverified');
  });
});

// ---------------------------------------------------------------------------
// 2. Models: Claude Code classification, efforts, contracts
// ---------------------------------------------------------------------------

describe('normalizeModel against models.json', () => {
  const [openrouterFree, , claudeCode] = modelsFx.models;

  it('keeps a Claude Code model on claude_code, with subscription pricing (no paid API price)', () => {
    const m = normalizeModel(claudeCode);
    expect(m.via).toBe('claude_code');
    expect(m.paid).toBeNull();
    expect(m.price_in_usd_per_mtok).toBeNull();
    expect(m.price_out_usd_per_mtok).toBeNull();
    expect(m.capabilities.text_validated).toBe(true);
  });

  it('keeps every effort the model lists, including xhigh and max', () => {
    expect(normalizeModel(claudeCode).capabilities.efforts).toEqual(['low', 'medium', 'high', 'xhigh', 'max']);
    expect(normalizeModel(openrouterFree).capabilities.efforts).toEqual(['off', 'low', 'medium', 'high']);
  });

  it('reads the six nested contract states and keeps not-applicable', () => {
    const m = normalizeModel(openrouterFree);
    expect(m.capabilities.contracts).toEqual({
      planner_json: 'not-applicable',
      writer_file_blocks: 'verified',
      repair_edit_blocks: 'unverified',
      repair_whole_file: 'verified',
      reviewer_verdict: 'verified',
      native_structured_output: 'unverified',
    });
    expect(normalizeModel(claudeCode).capabilities.contracts.native_structured_output).toBe('not-applicable');
  });

  it('keeps reasoning_mode, endpoint, and the paid OpenRouter price', () => {
    const [, paid] = modelsFx.models;
    const m = normalizeModel(paid);
    expect(m.capabilities.reasoning_mode).toBe('optional');
    expect(m.endpoint).toBe('z-ai');
    expect(m.paid).toBe(true);
    expect(m.price_in_usd_per_mtok).toBe(0.15);
    expect(normalizeModel(claudeCode).capabilities.reasoning_mode).toBe('unknown');
  });

  it('keeps context_tokens null when null (no invented 65536)', () => {
    const m = normalizeModel(claudeCode);
    expect(m.context_tokens).toBeNull();
    expect(m.max_output_tokens).toBe(32000);
  });

  it('normalizes the whole pool from the fixture envelope', () => {
    const pool = normalizeModelList(modelsFx);
    expect(pool.map((m) => m.model_id)).toEqual(modelsFx.models.map((m) => m.model_id));
  });

  it('marks a contract state outside the four states as unverified (fail-closed)', () => {
    const m = normalizeModel({
      ...openrouterFree,
      capabilities: { ...openrouterFree.capabilities, contracts: { writer_file_blocks: true } },
    });
    expect(m.capabilities.contracts.writer_file_blocks).toBe('unverified');
    expect(m.capabilities.contracts.planner_json).toBe('unverified');
  });
});

// ---------------------------------------------------------------------------
// 3. Stage rendering, including a null max_tokens
// ---------------------------------------------------------------------------

describe('normalizeStages against stages.json', () => {
  const out = normalizeStages(stagesFx);

  it('maps stage -> stage_key, model -> configured_model_id, settings.max_tokens -> max_output_tokens', () => {
    const writerA = out.stages.find((s) => s.stage_key === 'writer_a_write');
    expect(writerA?.configured_model_id).toBe('dots-studio/dots-3-note-preview:free');
    expect(writerA?.max_output_tokens).toBe(32000);
  });

  it('gives null max_output_tokens when settings has no max_tokens', () => {
    const planner = out.stages.find((s) => s.stage_key === 'planner');
    const reviewerA = out.stages.find((s) => s.stage_key === 'reviewer_a');
    expect(planner?.max_output_tokens).toBeNull();
    expect(reviewerA?.max_output_tokens).toBeNull();
  });

  it('keeps model_selectable, inherits, effort, revision and source', () => {
    const reviewerA = out.stages.find((s) => s.stage_key === 'reviewer_a');
    expect(reviewerA?.model_selectable).toBe(false);
    expect(reviewerA?.inherits).toBe('non-author writer');
    expect(reviewerA?.effort).toBe('low');
    expect(reviewerA?.source).toBe('role:reviewer_a');
    const reask = out.stages.find((s) => s.stage_key === 'reviewer_reask');
    expect(reask?.revision).toBeNull();
    expect(out.revision).toBe(stagesFx.revision);
    expect(out.profile).toBe('test');
  });

  it('a null max_tokens renders as Unknown and never throws', () => {
    // The pages render max output with fmtInt, which must accept null (no .toLocaleString() on null).
    const planner = out.stages.find((s) => s.stage_key === 'planner');
    expect(planner?.max_output_tokens).toBeNull();
    expect(fmtInt(planner?.max_output_tokens)).toBe('Unknown');
    expect(fmtInt(32000)).toBe('32,000');
    expect(() => fmtInt(null)).not.toThrow();
  });
});

// ---------------------------------------------------------------------------
// 4. Overview values
// ---------------------------------------------------------------------------

describe('normalizeOverview against overview.json', () => {
  const ov = normalizeOverview(overviewFx);

  it('reads the backend values exactly', () => {
    expect(ov.loops_running).toBe(2);
    expect(ov.queue_depth).toBe(1);
    expect(ov.jobs_waiting).toBe(1);
    expect(ov.active_errors).toBe(0);
    expect(ov.stop).toBe(false);
    expect(ov.pending_reviews).toBe(1);
    expect(ov.pace).toEqual({ sent_last_60s: 4, limit: 15 });
    expect(ov.review_usd_today).toEqual({ spent: 0, cap: 0.5 });
    expect(ov.sandbox_slots).toEqual({ used: 2, total: 4 });
    expect(ov.free_today).toEqual({ used: 87, cap: 1000, cap_source: 'live' });
    expect(ov.index_ok).toBe(true);
    expect(ov.updated).toBe('2026-11-03T17:45:00Z');
  });

  it('gives null, never a fallback, for absent values', () => {
    const empty = normalizeOverview({});
    expect(empty.pace).toEqual({ sent_last_60s: null, limit: null });
    expect(empty.review_usd_today).toEqual({ spent: null, cap: null });
    expect(empty.sandbox_slots).toEqual({ used: null, total: null });
    expect(empty.free_today).toEqual({ used: null, cap: null, cap_source: null });
    expect(empty.stop).toBeNull();
    expect(empty.index_ok).toBeNull();
    expect(empty.pending_reviews).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// 5. System limits kept as given
// ---------------------------------------------------------------------------

describe('normalizeLimits against system_health.json', () => {
  const limits = normalizeLimits(systemHealthFx.limits);

  it('keeps leases, claims, gates and ledger exactly as given', () => {
    expect(limits.leases).toEqual([
      { job_id: 'fl-top-20261103-2309-7d15', held: true, heartbeat_age_s: 4, pid_alive: true },
    ]);
    expect(limits.claims).toEqual([{ path: 'scripts/source/fl_top.py', job_id: 'fl-top-20261103-2309-7d15' }]);
    expect(limits.gates).toEqual([{ gate: 'nemotron', holders: 1, capacity: 1 }]);
    expect(limits.ledger).toEqual({ free_used: 87, free_cap: 1000, review_spent_usd: 0 });
  });

  it('invents no sandbox slot count and no review ceiling', () => {
    expect(Object.keys(limits)).toEqual(['revision', 'leases', 'claims', 'gates', 'ledger']);
    expect(limits.revision).toBe(1);
    expect(JSON.stringify(limits)).not.toMatch(/2\.5000|65536/);
  });

  it('keeps a missing limit as null', () => {
    const empty = normalizeLimits({});
    expect(empty).toEqual({
      revision: null,
      leases: [],
      claims: [],
      gates: [],
      ledger: { free_used: null, free_cap: null, review_spent_usd: null },
    });
  });
});

// ---------------------------------------------------------------------------
// Jobs and events from the fixtures
// ---------------------------------------------------------------------------

describe('job detail and events from the fixtures', () => {
  it('lists all ten stages, untriggered ones included, and keeps not-triggered status', () => {
    const job = normalizeJobDetail(untriggeredJobFx);
    expect(job.stages).toHaveLength(10);
    expect(job.stages?.filter((s) => s.status === 'not-triggered').length).toBeGreaterThan(0);
  });

  it('keeps the waiting reason and the attempt events of a running job', () => {
    const job = normalizeJobDetail(runningJobFx);
    expect(job.waiting?.why).toBe('model-slot');
    expect(job.derived_state).toBe('waiting');
    expect(job.lease?.pid).toBe(4120);
    const page = normalizeEventPage(runningJobFx.events);
    expect(page.events.map((e) => e.type)).toEqual(['job-start', 'attempt-start', 'attempt-end', 'tests', 'wait-start']);
    expect(page.events[1].prompt_tokens).toBeNull();
    expect(page.next_after).toBe(5);
  });

  it('results keep the failing test and the verdicts as given', () => {
    const r = normalizeResult(resultsEscalateFx);
    expect(r.end_state).toBe('escalate');
    expect(r.merge_state).toBe('none');
    expect(r.tests?.passed).toBe(false);
    expect(r.tests?.failing).toEqual([
      {
        id: 'tests/acceptance/fl_top_acceptance.py::test_counts_come_from_fl_wordcount',
        message: "assert [('anything', 1)] == [('q', 9)]",
        file: 'tests/acceptance/fl_top_acceptance.py',
        line: 41,
      },
    ]);
    expect(r.merge).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// 6. Mutation payload shapes (contract section 3)
// ---------------------------------------------------------------------------

describe('mutation payloads match the contract', () => {
  const stageRows = normalizeStages(stagesFx).stages;

  it('every mutation sends a valid Idempotency-Key header', async () => {
    const { client, captured } = liveClient(() => ({ status: 200, body: { stop: true } }));
    await client.setGlobalStop(true, KEY);
    expect(captured[0].headers['Idempotency-Key']).toBe(KEY);
    expect(UUID_V4_REGEX.test(captured[0].headers['Idempotency-Key'])).toBe(true);
  });

  it('refuses a mutation with no valid idempotency key, without a request', async () => {
    const { client, captured } = liveClient(() => ({ status: 200, body: {} }));
    await expect(client.setGlobalStop(true, 'not-a-uuid')).rejects.toMatchObject({ code: 'invalid-idempotency-key' });
    expect(captured).toHaveLength(0);
  });

  it('POST /plans sends {name, request, files}', async () => {
    const { client, captured } = liveClient(() => ({ status: 202, body: { plan_id: 'p1', state: 'planning' } }));
    const plan = await client.createPlan({ name: 'n', request: 'r', files: ['a.py'] }, KEY);
    expect(captured[0].method).toBe('POST');
    expect(captured[0].url).toBe('http://127.0.0.1:8765/api/plans');
    expect(captured[0].body).toEqual({ name: 'n', request: 'r', files: ['a.py'] });
    expect(plan.plan_id).toBe('p1');
    expect(plan.state).toBe('planning');
  });

  it('POST /plans/{id}/approve sends {job_sha256, approve_assumed}', async () => {
    const { client, captured } = liveClient(() => ({ status: 202, body: { job_id: 'job-1' } }));
    await client.approvePlan('p1', { job_sha256: 'abc', approve_assumed: true }, KEY);
    expect(captured[0].url).toBe('http://127.0.0.1:8765/api/plans/p1/approve');
    expect(captured[0].body).toEqual({ job_sha256: 'abc', approve_assumed: true });
  });

  it('POST /jobs sends {job_path, rerun, stages (keyed object), stage_revision, paid_budget_usd}', async () => {
    const { client, captured } = liveClient(() => ({
      status: 201,
      body: { queued: true, queue_file: 'q.json', stage_snapshot: { sha256: 'x', revision: 4 } },
    }));
    const res = await client.queueJob(
      {
        job_path: 'jobs/fl_top.json',
        rerun: false,
        stages: stageRows,
        stage_revision: stagesFx.revision,
        paid_budget_usd: 0,
      },
      KEY
    );
    const body = captured[0].body;
    expect(Object.keys(body).sort()).toEqual(['job_path', 'paid_budget_usd', 'rerun', 'stage_revision', 'stages']);
    expect(Array.isArray(body.stages)).toBe(false);
    expect(body.stages.writer_a_write).toEqual({
      model: 'dots-studio/dots-3-note-preview:free',
      via: 'openrouter',                 // I61: the model's transport travels with the model
      effort: 'high',
      settings: { temperature: 0.1, max_tokens: 32000, reasoning: { max_tokens: 8000 } },
    });
    expect(body.stage_revision).toBe(4);
    expect(res.queued).toBe(true);
    expect(res.stage_snapshot).toEqual({ sha256: 'x', revision: 4 });
  });

  it('PATCH /loops/{id}/stages sends {stage, change, revision}', async () => {
    const { client, captured } = liveClient(() => ({ status: 200, body: { ok: true, revision: 1 } }));
    await client.patchLoopStage(
      'fl-top-20261103-2309-7d15',
      { stage: 'writer_b_repair', change: { effort: 'low' }, revision: 1 },
      KEY
    );
    expect(captured[0].method).toBe('PATCH');
    expect(captured[0].body).toEqual({ stage: 'writer_b_repair', change: { effort: 'low' }, revision: 1 });
  });

  it('POST /system/stop sends {on}', async () => {
    const { client, captured } = liveClient(() => ({ status: 200, body: { stop: true } }));
    const res = await client.setGlobalStop(true, KEY);
    expect(captured[0].body).toEqual({ on: true });
    expect(res.stop).toBe(true);
  });

  it('POST /jobs/validate sends {job_path}', async () => {
    const { client, captured } = liveClient(() => ({ status: 200, body: { valid: true, problems: [] } }));
    await client.validateJob({ job_path: 'jobs/x.json' }, KEY);
    expect(captured[0].body).toEqual({ job_path: 'jobs/x.json' });
  });

  it('POST /budget/validate sends {stages (keyed), paid_budget_usd}', async () => {
    const { client, captured } = liveClient(() => ({ status: 200, body: { valid: true, worst_case_usd: 0, problems: [] } }));
    await client.validateBudget({ stages: stageRows, paid_budget_usd: 0 }, KEY);
    expect(captured[0].url).toBe('http://127.0.0.1:8765/api/budget/validate');
    expect(Object.keys(captured[0].body)).toEqual(['stages', 'paid_budget_usd']);
    expect(captured[0].body.stages.planner).toBeDefined();
  });

  it('POST /loops/start sends {loops}', async () => {
    const { client, captured } = liveClient(() => ({ status: 202, body: { started: 2 } }));
    const res = await client.startLoops(2, KEY);
    expect(captured[0].body).toEqual({ loops: 2 });
    expect(res.started).toBe(2);
  });

  it('POST /models sends {model_id}', async () => {
    const { client, captured } = liveClient(() => ({ status: 201, body: modelsFx.models[0] }));
    await client.addModel('nvidia/nemotron-3-ultra-550b-a55b:free', KEY);
    expect(captured[0].body).toEqual({ model_id: 'nvidia/nemotron-3-ultra-550b-a55b:free' });
  });

  it('DELETE /models/{id} encodes the slash in an OpenRouter id and sends no body', async () => {
    expect(buildModelPath('deepseek/deepseek-chat-v3-0324:free')).toBe(
      '/models/deepseek%2Fdeepseek-chat-v3-0324%3Afree'
    );
    const { client, captured } = liveClient(() => ({ status: 204 }));
    await client.deleteModel('deepseek/deepseek-chat-v3-0324:free', KEY);
    expect(captured[0].method).toBe('DELETE');
    expect(captured[0].url).toBe('http://127.0.0.1:8765/api/models/deepseek%2Fdeepseek-chat-v3-0324%3Afree');
    expect(captured[0].body).toBeUndefined();
  });

  it('POST /models/verify sends {model_id, contract}', async () => {
    const { client, captured } = liveClient(() => ({ status: 200, body: { result: 'pass', verified_at: 't' } }));
    await client.verifyModelContract({ model_id: 'm', contract: 'reviewer_verdict' }, KEY);
    expect(captured[0].body).toEqual({ model_id: 'm', contract: 'reviewer_verdict' });
  });

  it('POST /models/verify-reasoning sends {model_id, efforts}', async () => {
    const { client, captured } = liveClient(() => ({
      status: 200,
      body: { reasoning_mode: 'optional', efforts_verified: ['low'] },
    }));
    await client.verifyModelReasoning({ model_id: 'm', efforts: ['low', 'high'] }, KEY);
    expect(captured[0].body).toEqual({ model_id: 'm', efforts: ['low', 'high'] });
  });

  it('POST /models/refresh sends {}', async () => {
    const { client, captured } = liveClient(() => ({ status: 200, body: { updated: 0, metadata_at: 't' } }));
    await client.refreshModels(KEY);
    expect(captured[0].body).toEqual({});
  });

  it('POST /stages/validate and PUT /stages send {profile, changes} and {profile, revision, changes}', async () => {
    const { client, captured } = liveClient(() => ({ status: 200, body: { valid: true, revision: 5 } }));
    const changes = buildStageChanges(stageRows);
    await client.validateStages({ profile: 'test', changes }, KEY);
    await client.putStages({ profile: 'test', revision: 4, changes }, KEY);
    expect(Object.keys(captured[0].body)).toEqual(['profile', 'changes']);
    expect(Object.keys(captured[1].body)).toEqual(['profile', 'revision', 'changes']);
    expect(captured[1].body.revision).toBe(4);
    expect(captured[1].body.changes[0].stage).toBe('planner');
  });

  it('PUT /system/limits sends {revision, sandbox_slots?, review_usd_daily_cap?}', async () => {
    const { client, captured } = liveClient(() => ({ status: 200, body: { revision: 2 } }));
    await client.putSystemLimits({ revision: 1, sandbox_slots: 2 }, KEY);
    expect(captured[0].body).toEqual({ revision: 1, sandbox_slots: 2 });
  });

  it('builds the keyed stages object from rows', () => {
    const payload = buildStagesPayload(stageRows);
    expect(Object.keys(payload)).toHaveLength(10);
    expect(payload.reviewer_a.model).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// 9. GET /jobs: {jobs, index_ok} (monitor/fixtures/jobs.json, read unchanged)
// ---------------------------------------------------------------------------

describe('GET /jobs against jobs.json', () => {
  it('normalizes every fixture job to the same id, state and end state', () => {
    expect(Object.keys(jobsFx).sort()).toEqual(['index_ok', 'jobs']);
    const jobs = jobsFx.jobs.map((j) => normalizeJobSummary(j));
    expect(jobs.map((j) => j.job_id)).toEqual(jobsFx.jobs.map((j) => j.job_id));
    expect(jobs.map((j) => j.derived_state)).toEqual(jobsFx.jobs.map((j) => j.derived_state));
    expect(jobs.map((j) => j.end_state)).toEqual(jobsFx.jobs.map((j) => j.end_state));
  });

  it('the client returns the jobs and index_ok from the wire', async () => {
    const { client } = liveClient(() => ({ status: 200, body: jobsFx }));
    const list = await client.getJobs();
    expect(list.index_ok).toBe(true);
    expect(list.jobs.map((j) => j.job_id)).toEqual(jobsFx.jobs.map((j) => j.job_id));
  });
});

// ---------------------------------------------------------------------------
// 10. System limits: the revision is kept, and a PUT shows the backend's refusal (I8)
// ---------------------------------------------------------------------------

describe('System limits save (PUT /system/limits)', () => {
  it('keeps the revision from GET /system/limits, and null when the backend sent none', () => {
    expect(normalizeLimits(systemHealthFx.limits).revision).toBe(1);
    expect(normalizeLimits({ leases: [] }).revision).toBeNull();
  });

  it('a stale revision (409) is refused with the backend message and its current revision', async () => {
    const { client, captured } = liveClient(() => ({
      status: 409,
      body: { error: { code: 'stale-revision', message: 'the limits are at revision 2, not 1', details: { revision: 2 } } },
    }));
    await expect(client.putSystemLimits({ revision: 1, sandbox_slots: 2 }, KEY)).rejects.toMatchObject({
      status: 409,
      code: 'stale-revision',
      details: { revision: 2 },
    });
    expect(captured[0].body).toEqual({ revision: 1, sandbox_slots: 2 });
  });

  it('a value the engine does not apply (422 not-applied-by-engine) is refused with its reason', async () => {
    const { client } = liveClient(() => ({
      status: 422,
      body: {
        error: {
          code: 'not-applied-by-engine',
          message: 'job_review_usd 0.01 would not apply: the engine requires exactly 0.05',
          details: { required: 0.05, given: 0.01 },
        },
      },
    }));
    await expect(client.putSystemLimits({ revision: 1, job_review_usd: 0.01 }, KEY)).rejects.toMatchObject({
      status: 422,
      code: 'not-applied-by-engine',
      message: expect.stringContaining('would not apply'),
    });
  });
});
