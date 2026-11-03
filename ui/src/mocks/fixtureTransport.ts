/**
 * MOCK mode transport. Answers every read from the canonical contract fixtures in `monitor/fixtures/`,
 * unmodified. Anything that would need a live engine (a provider probe, a budget check, a rebuilt
 * archive) returns 503 instead of an invented result.
 */

import overviewFx from '../../../monitor/fixtures/overview.json';
import modelsFx from '../../../monitor/fixtures/models.json';
import stagesFx from '../../../monitor/fixtures/stages.json';
import systemHealthFx from '../../../monitor/fixtures/system_health.json';
import runningJobFx from '../../../monitor/fixtures/running_job.json';
import completedJobFx from '../../../monitor/fixtures/completed_job.json';
import failedReviewJobFx from '../../../monitor/fixtures/failed_review_job.json';
import recoveredMergeJobFx from '../../../monitor/fixtures/recovered_merge_job.json';
import untriggeredStageJobFx from '../../../monitor/fixtures/untriggered_stage_job.json';
import resultsEscalateFx from '../../../monitor/fixtures/results_escalate.json';
import errorExamplesFx from '../../../monitor/fixtures/error_examples.json';

type MockRequest = {
  method: string;
  path: string;
  headers: Record<string, string>;
  body?: any;
};
type MockResponse = { status: number; body: any };

const ALL_JOBS: Array<Record<string, any>> = [
  runningJobFx,
  failedReviewJobFx,
  recoveredMergeJobFx,
  completedJobFx,
  untriggeredStageJobFx,
];

const LIST_JOBS: Array<Record<string, any>> = [runningJobFx, failedReviewJobFx, recoveredMergeJobFx];

function errorBody(code: string, message: string, details: Record<string, unknown> = {}) {
  return { error: { code, message, details } };
}

function findJob(jobId: string): Record<string, any> | undefined {
  return ALL_JOBS.find((j) => j.job_id === jobId);
}

function notImplementedInMock(what: string): MockResponse {
  return {
    status: 503,
    body: errorBody(
      'mock-no-engine',
      `${what} needs the live engine. MOCK mode only replays the contract fixtures.`
    ),
  };
}

/** Builds the handler for `SatvikApiClient`'s `mockHandler` option. */
export function createFixtureTransport(): (req: MockRequest) => Promise<MockResponse> {
  const queuedPaths = new Set<string>();
  return async ({ method, path, body }: MockRequest): Promise<MockResponse> => {
    const [route, query = ''] = path.split('?');
    const params = new URLSearchParams(query);

    if (method === 'GET') {
      if (route === '/overview') return { status: 200, body: overviewFx };
      if (route === '/jobs') {
        return { status: 200, body: { jobs: LIST_JOBS, index_ok: true } };
      }
      const loopEvents = route.match(/^\/loops\/([^/]+)\/events$/);
      if (loopEvents) {
        const job = findJob(decodeURIComponent(loopEvents[1]));
        if (!job) return { status: 404, body: errorBody('not-found', 'unknown job id') };
        const after = Number(params.get('after') ?? '0');
        const page = (job.events as { events?: Array<{ seq: number }>; next_after?: number; index_ok?: boolean } | undefined);
        const events = (page?.events ?? []).filter((e) => e.seq > after);
        const nextAfter = events.length > 0 ? events[events.length - 1].seq : after;
        return { status: 200, body: { events, next_after: nextAfter, index_ok: page?.index_ok ?? true } };
      }
      const loop = route.match(/^\/loops\/([^/]+)$/);
      if (loop) {
        const job = findJob(decodeURIComponent(loop[1]));
        if (!job) return { status: 404, body: errorBody('not-found', 'unknown job id') };
        return { status: 200, body: job };
      }
      if (route === '/models') return { status: 200, body: modelsFx };
      if (route === '/models/search') return { status: 200, body: { results: [] } };
      if (route === '/stages') {
        const profile = params.get('profile');
        if (profile !== stagesFx.profile) {
          return { status: 404, body: errorBody('not-found', `no fixture for profile ${profile}`) };
        }
        return { status: 200, body: stagesFx };
      }
      const resultDiff = route.match(/^\/results\/([^/]+)\/diff$/);
      if (resultDiff) {
        return { status: 404, body: errorBody('not-archived', 'no diff in MOCK mode') };
      }
      const result = route.match(/^\/results\/([^/]+)$/);
      if (result) {
        if (decodeURIComponent(result[1]) !== resultsEscalateFx.job_id) {
          return { status: 404, body: errorBody('not-found', 'no result fixture for that job') };
        }
        return { status: 200, body: resultsEscalateFx };
      }
      if (route === '/system/health') return { status: 200, body: systemHealthFx.health };
      if (route === '/system/limits') return { status: 200, body: systemHealthFx.limits };
      if (route.startsWith('/plans/')) {
        return notImplementedInMock('Reading a plan');
      }
      return { status: 404, body: errorBody('not-found', `no MOCK route for GET ${route}`) };
    }

    // Mutations
    if (method === 'POST' && route === '/system/stop') {
      return { status: 200, body: { stop: Boolean(body?.on) } };
    }
    if (method === 'POST' && route === '/jobs/validate') {
      return { status: 200, body: { valid: true, problems: [] } };
    }
    if (method === 'POST' && route === '/jobs') {
      const jobPath = String(body?.job_path ?? '');
      if (body?.rerun !== true && queuedPaths.has(jobPath)) {
        // error_examples.json: the duplicate-pending refusal, returned unmodified.
        const dup = errorExamplesFx.examples.find((e) => e.error.code === 'duplicate-pending');
        return { status: 409, body: dup?.error ? { error: dup.error } : errorBody('duplicate-pending', 'duplicate') };
      }
      queuedPaths.add(jobPath);
      return {
        status: 201,
        body: {
          queued: true,
          queue_file: `_loop/queue/${String(body?.job_path ?? '').replace(/^jobs\//, '')}`,
          stage_snapshot: { sha256: null, revision: body?.stage_revision ?? null },
        },
      };
    }
    if (method === 'POST' && route === '/loops/start') {
      return { status: 202, body: { started: body?.loops ?? 0 } };
    }
    if (method === 'POST' && route === '/plans') {
      return { status: 202, body: { plan_id: 'plan-mock', state: 'planning' } };
    }
    if (method === 'POST' && /^\/plans\/[^/]+\/approve$/.test(route)) {
      return notImplementedInMock('Approving a plan');
    }
    if (method === 'PATCH' && /^\/loops\/[^/]+\/stages$/.test(route)) {
      return { status: 200, body: { ok: true, revision: body?.revision ?? null } };
    }
    if (method === 'POST' && route === '/stages/validate') {
      return { status: 200, body: { valid: true, problems: [] } };
    }
    if (method === 'PUT' && route === '/stages') {
      return { status: 200, body: { revision: (stagesFx.revision ?? 0) + 1 } };
    }
    if (method === 'POST' && route === '/models') {
      return {
        status: 201,
        body: {
          model_id: String(body?.model_id ?? ''),
          via: 'openrouter',
          hosting_provider: null,
          paid: null,
          price_in_usd_per_mtok: null,
          price_out_usd_per_mtok: null,
          context_tokens: null,
          max_output_tokens: null,
          capabilities: {
            reasoning_on_off: null,
            efforts: [],
            token_budget: null,
            temperature: null,
            structured_outputs: null,
            contracts: {
              planner_json: 'unverified',
              writer_file_blocks: 'unverified',
              repair_edit_blocks: 'unverified',
              repair_whole_file: 'unverified',
              reviewer_verdict: 'unverified',
              native_structured_output: 'unverified',
            },
            reasoning_mode: 'unknown',
            text_validated: null,
          },
          verified_at: null,
          metadata_at: null,
          note: null,
          endpoint: null,
        },
      };
    }
    if (method === 'DELETE' && route.startsWith('/models/')) {
      return { status: 204, body: undefined };
    }
    if (method === 'POST' && route === '/models/verify') {
      return notImplementedInMock('Verifying a contract');
    }
    if (method === 'POST' && route === '/models/verify-reasoning') {
      return notImplementedInMock('Verifying reasoning');
    }
    if (method === 'POST' && route === '/models/refresh') {
      return notImplementedInMock('Refreshing prices');
    }
    if (method === 'POST' && route === '/budget/validate') {
      return notImplementedInMock('Validating the paid budget');
    }
    if (method === 'PUT' && route === '/system/limits') {
      return notImplementedInMock('Saving limits');
    }
    return { status: 404, body: errorBody('not-found', `no MOCK route for ${method} ${route}`) };
  };
}
