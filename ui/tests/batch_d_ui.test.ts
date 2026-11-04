/**
 * Batch D, UI (codex audit 2026-11-04, I61): unit tests, no network.
 * - a stage's model change sends its via (and effort) with the model;
 * - plan polling runs to a settled state with backoff and no cutoff, and survives failed reads;
 * - a transport error on a mutation says the outcome is unknown, never "Nothing was changed".
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import stagesFx from '../../monitor/fixtures/stages.json';
import { SatvikApiClient, buildStageChanges, buildStagesPayload, stageRowWithModel } from '../src/api/client';
import type { DataMode } from '../src/api/types';
import { normalizeStages } from '../src/api/normalizer';
import {
  PLAN_POLL_MAX_MS,
  isPlanSettled,
  planPollDelayMs,
  pollPlanUntilSettled,
} from '../src/api/planPoll';

afterEach(() => {
  vi.unstubAllGlobals();
});

const KEY = '11111111-2222-4333-8444-555555555555';

// ---------------------------------------------------------------- I61: via travels with the model

describe('a stage model change carries its via and effort (I61)', () => {
  const rows = normalizeStages(stagesFx).stages;
  const row = rows.find((r) => r.model_selectable === true && r.via === 'openrouter');
  if (!row) throw new Error('the fixture needs one selectable OpenRouter stage');

  it('picking a Claude Code model sets via to claude_code on the draft row, and effort is kept', () => {
    const next = stageRowWithModel(row, 'haiku', [{ model_id: 'haiku', via: 'claude_code' }]);
    expect(next.configured_model_id).toBe('haiku');
    expect(next.via).toBe('claude_code');
    expect(next.effort).toBe(row.effort);
  });

  it('a model the listing does not know keeps the row transport: nothing is guessed', () => {
    const next = stageRowWithModel(row, 'x/unknown:free', []);
    expect(next.configured_model_id).toBe('x/unknown:free');
    expect(next.via).toBe(row.via);
  });

  it('PUT /stages and validate changes name the via with the model', () => {
    const changed = stageRowWithModel(row, 'haiku', [{ model_id: 'haiku', via: 'claude_code' }]);
    const [change] = buildStageChanges([changed]);
    expect(change).toEqual({
      stage: changed.stage_key,
      model: 'haiku',
      via: 'claude_code',
      effort: changed.effort,
      settings: changed.settings,
    });
    expect(buildStagesPayload([changed])[changed.stage_key].via).toBe('claude_code');
  });

  it('the wire body of a save carries via next to model', async () => {
    const captured: Array<{ body: any }> = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (_url: string, init: RequestInit) => {
        captured.push({ body: JSON.parse(String(init.body)) });
        return new Response(JSON.stringify({ revision: 2 }), { status: 200, headers: { 'Content-Type': 'application/json' } });
      })
    );
    const mode: DataMode = 'live';
    const client = new SatvikApiClient({ baseUrl: 'http://127.0.0.1:8765/api', getMode: () => mode });
    const changed = stageRowWithModel(row, 'haiku', [{ model_id: 'haiku', via: 'claude_code' }]);
    await client.putStages({ profile: 'test', revision: 1, changes: buildStageChanges([changed]) }, KEY);
    expect(captured[0].body.changes[0]).toMatchObject({ model: 'haiku', via: 'claude_code' });
  });
});

// ---------------------------------------------------------------- I61: plan polling runs to a settled state

describe('plan polling runs until a settled state, with backoff and no cutoff (I61)', () => {
  it('backs off from 1 s, doubling, and stays at the 15 s cap with no attempt limit', () => {
    expect([0, 1, 2, 3, 4, 5].map(planPollDelayMs)).toEqual([1000, 2000, 4000, 8000, 15000, 15000]);
    expect(planPollDelayMs(10_000)).toBe(PLAN_POLL_MAX_MS);
  });

  it('planning is the only unsettled state', () => {
    expect(isPlanSettled('planning')).toBe(false);
    expect(isPlanSettled('planned')).toBe(true);
    expect(isPlanSettled('refused')).toBe(true);
    expect(isPlanSettled(null)).toBe(true);
  });

  it('keeps reading past the old 30-read, 60 s window until the plan is planned', async () => {
    let reads = 0;
    let waited = 0;
    const sleep = vi.fn(async (ms: number) => {
      waited += ms;
    });
    const plan = await pollPlanUntilSettled({
      initial: { state: 'planning' as string | null },
      fetchPlan: async () => {
        reads += 1;
        return { state: reads < 60 ? 'planning' : 'planned' };
      },
      sleep,
    });
    expect(plan.state).toBe('planned');
    expect(reads).toBe(60);
    expect(waited).toBeGreaterThan(60_000);
  });

  it('a failed read is reported and the wait goes on', async () => {
    const errors: unknown[] = [];
    let reads = 0;
    const plan = await pollPlanUntilSettled({
      initial: { state: 'planning' as string | null },
      fetchPlan: async () => {
        reads += 1;
        if (reads <= 2) throw new TypeError('connection reset');
        return { state: 'planned' };
      },
      onReadError: (err) => errors.push(err),
      sleep: async () => undefined,
    });
    expect(plan.state).toBe('planned');
    expect(errors).toHaveLength(2);
  });

  it('an abort stops the wait with an AbortError', async () => {
    const controller = new AbortController();
    const pending = pollPlanUntilSettled({
      initial: { state: 'planning' as string | null },
      fetchPlan: async () => ({ state: 'planning' }),
      signal: controller.signal,
      sleep: (_ms, signal) =>
        new Promise((_resolve, reject) => {
          signal?.addEventListener('abort', () => {
            const err = new Error('aborted');
            err.name = 'AbortError';
            reject(err);
          });
          controller.abort();
        }),
    });
    await expect(pending).rejects.toMatchObject({ name: 'AbortError' });
  });
});

// ---------------------------------------------------------------- I61: an unknown outcome is not "nothing was changed"

describe('a transport error on a mutation says the outcome is unknown (I61)', () => {
  function unreachable(): SatvikApiClient {
    vi.stubGlobal('fetch', vi.fn(async () => {
      throw new TypeError('fetch failed');
    }));
    const mode: DataMode = 'live';
    return new SatvikApiClient({ baseUrl: 'http://127.0.0.1:8765/api', getMode: () => mode });
  }

  it('a PUT that got no response reports the outcome as unknown', async () => {
    const client = unreachable();
    const err = await client.putStages({ profile: 'test', revision: 1, changes: [] }, KEY).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(Error);
    const message = (err as Error).message;
    expect(message).toContain('outcome is UNKNOWN');
    expect(message).not.toContain('Nothing was changed');
  });

  it('a read that got no response may still say nothing was changed', async () => {
    const client = unreachable();
    const err = await client.getOverview().catch((e: unknown) => e);
    expect((err as Error).message).toContain('Nothing was changed');
  });
});
