/**
 * UI gates added for the LIVE integration (B5): which models a stage may offer, how an API problem is shown, and what a queue request
 * sends. Fixtures are the canonical monitor/fixtures files, read unchanged.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';

import modelsFx from '../../monitor/fixtures/models.json';
import stagesFx from '../../monitor/fixtures/stages.json';
import { isEligibleForStage } from '../src/api/eligibility';
import { normalizeModel, normalizeStages } from '../src/api/normalizer';
import { ApiHttpError, SatvikApiClient, problemText } from '../src/api/client';
import type { DataMode } from '../src/api/types';

const KEY = '3f2b8c1e-9a4d-4e6f-8b7a-1c2d3e4f5a6b';

afterEach(() => {
  vi.unstubAllGlobals();
});

function liveClient(status: number, body: unknown) {
  const captured: Array<{ body: any }> = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (_url: string, init: RequestInit) => {
      captured.push({ body: init.body ? JSON.parse(String(init.body)) : undefined });
      return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
    })
  );
  const mode: DataMode = 'live';
  const client = new SatvikApiClient({ baseUrl: 'http://127.0.0.1:8765/api', getMode: () => mode });
  return { client, captured };
}

describe('isEligibleForStage: a model is offered only when its required contract is verified and dated', () => {
  const verifiedRaw = modelsFx.models.find((m) => m.model_id === 'nvidia/nemotron-3-ultra-550b-a55b:free');
  if (!verifiedRaw) throw new Error('fixture lost nvidia/nemotron-3-ultra-550b-a55b:free');
  const model = normalizeModel(verifiedRaw as Record<string, any>);

  it('a verified contract with a recorded verification time is eligible', () => {
    expect(model.capabilities.contracts.writer_file_blocks).toBe('verified');
    expect(model.verified_at).not.toBeNull();
    expect(isEligibleForStage(model, 'writer_file_blocks')).toBe(true);
  });

  it('an unverified contract is not eligible (repair_edit_blocks is unverified in the fixture)', () => {
    expect(model.capabilities.contracts.repair_edit_blocks).toBe('unverified');
    expect(isEligibleForStage(model, 'repair_edit_blocks')).toBe(false);
  });

  it('a verified contract without a verification time is not eligible', () => {
    expect(isEligibleForStage({ ...model, verified_at: null }, 'writer_file_blocks')).toBe(false);
  });

  it('a stage with no required contract offers no model (fail closed)', () => {
    expect(isEligibleForStage(model, null)).toBe(false);
  });
});

describe('problemText: API problems are shown as text, never as [object Object]', () => {
  it('a string problem is itself', () => {
    expect(problemText('model is not in the pool')).toBe('model is not in the pool');
  });

  it('an object problem shows its message', () => {
    expect(problemText({ code: 'bad-model', stage: 'reviewer_a', message: 'reviewer_a: model must be a non-empty string' })).toBe(
      'reviewer_a: model must be a non-empty string'
    );
  });

  it('an object with only a code shows the code', () => {
    expect(problemText({ code: 'writers-share-model' })).toBe('writers-share-model');
  });
});

describe('error envelopes: details.problems are readable strings', () => {
  it('a 400 with object problems is normalized to message strings', async () => {
    const { client } = liveClient(400, {
      error: {
        code: 'bad-model',
        message: 'reviewer_a: model must be a non-empty string',
        details: { problems: [{ code: 'bad-model', stage: 'reviewer_a', message: 'reviewer_a: model must be a non-empty string' }] },
      },
    });
    let caught: unknown = null;
    try {
      await client.queueJob({ job_path: 'jobs/x.json', rerun: false, stages: [], stage_revision: 1, paid_budget_usd: 0 }, KEY);
    } catch (e) {
      caught = e;
    }
    expect(caught).toBeInstanceOf(ApiHttpError);
    const err = caught as ApiHttpError;
    expect(err.status).toBe(400);
    expect(err.details.problems).toEqual(['reviewer_a: model must be a non-empty string']);
  });
});

describe('POST /jobs sends only the overrides the API accepts', () => {
  it('a locked or inherited row (model_selectable false) is not sent; selectable rows are', async () => {
    const rows = normalizeStages(stagesFx).stages;
    const locked = rows.filter((r) => r.model_selectable !== true).map((r) => r.stage_key);
    expect(locked.length).toBeGreaterThan(0);

    const { client, captured } = liveClient(201, { queued: true, queue_file: 'q.json', stage_snapshot: null });
    await client.queueJob({ job_path: 'jobs/x.json', rerun: false, stages: rows, stage_revision: stagesFx.revision, paid_budget_usd: 0 }, KEY);

    const sent = Object.keys(captured[0].body.stages);
    for (const stage of locked) expect(sent).not.toContain(stage);
    expect(sent).toContain('writer_a_write');
  });
});
