/**
 * Test double for the FastAPI backend (docs/API_CONTRACT.md) used by the Playwright e2e suite.
 *
 * Every read returns the canonical fixture from monitor/fixtures/ with its content unchanged (parsed JSON, re-serialized).
 * The server never edits fixture content. It only chooses which fixture to send, and it applies the
 * contract's status codes (for example the 409 duplicate-pending on a second queue of the same job).
 *
 * Run: node tests/fake_fastapi_server.mjs   (listens on 127.0.0.1:8765, base /api)
 */

import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const FIXTURES = path.resolve(here, '..', '..', 'monitor', 'fixtures');
const PORT = Number(process.env.FAKE_API_PORT ?? 8765);
const DEV_ORIGIN = process.env.FAKE_API_ORIGIN ?? 'http://127.0.0.1:5173';

function fixture(name) {
  return JSON.parse(fs.readFileSync(path.join(FIXTURES, name), 'utf8'));
}

const overview = fixture('overview.json');
const models = fixture('models.json');
const stages = fixture('stages.json');
const systemHealth = fixture('system_health.json');
const runningJob = fixture('running_job.json');
const completedJob = fixture('completed_job.json');
const failedReviewJob = fixture('failed_review_job.json');
const recoveredMergeJob = fixture('recovered_merge_job.json');
const untriggeredStageJob = fixture('untriggered_stage_job.json');
const resultsEscalate = fixture('results_escalate.json');
const errorExamples = fixture('error_examples.json');

const DETAIL_JOBS = [runningJob, completedJob, failedReviewJob, recoveredMergeJob, untriggeredStageJob];
const LIST_JOBS = [runningJob, failedReviewJob, recoveredMergeJob];

const queuedPaths = new Set();
const requestLog = [];

function errorFromFixture(code) {
  const hit = errorExamples.examples.find((e) => e.error.code === code);
  if (!hit) throw new Error(`fixture error_examples.json has no ${code}`);
  return { status: hit.status, body: { error: hit.error } };
}

function send(res, status, body, origin) {
  const headers = {
    'Access-Control-Allow-Origin': origin,
    'Access-Control-Allow-Methods': 'GET, POST, PUT, PATCH, DELETE, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type, Idempotency-Key, Accept',
    Vary: 'Origin',
  };
  if (status === 204) {
    res.writeHead(204, headers);
    res.end();
    return;
  }
  res.writeHead(status, { ...headers, 'Content-Type': 'application/json; charset=utf-8' });
  res.end(JSON.stringify(body));
}

function notFound(code, message) {
  return { status: 404, body: { error: { code, message, details: {} } } };
}

function jobById(id) {
  return DETAIL_JOBS.find((j) => j.job_id === id);
}

async function readBody(req) {
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  const text = Buffer.concat(chunks).toString('utf8');
  return text.length > 0 ? JSON.parse(text) : undefined;
}

export function routeRequest(method, urlPath, body) {
  const url = new URL(urlPath, 'http://127.0.0.1');
  const route = url.pathname.replace(/^\/api/, '');
  const params = url.searchParams;

  if (method === 'GET') {
    if (route === '/overview') return { status: 200, body: overview };
    if (route === '/jobs') return { status: 200, body: { jobs: LIST_JOBS, index_ok: true } };
    const ev = route.match(/^\/loops\/([^/]+)\/events$/);
    if (ev) {
      const job = jobById(decodeURIComponent(ev[1]));
      if (!job) return notFound('not-found', 'unknown job id');
      const after = Number(params.get('after') ?? 0);
      const page = job.events ?? { events: [], next_after: after, index_ok: true };
      return {
        status: 200,
        body: {
          events: page.events.filter((e) => e.seq > after),
          next_after: page.next_after,
          index_ok: page.index_ok,
        },
      };
    }
    const loop = route.match(/^\/loops\/([^/]+)$/);
    if (loop) {
      const job = jobById(decodeURIComponent(loop[1]));
      return job ? { status: 200, body: job } : notFound('not-found', 'unknown job id');
    }
    if (route === '/models') return { status: 200, body: models };
    if (route === '/models/search') return { status: 200, body: { results: [] } };
    if (route === '/stages') {
      return params.get('profile') === stages.profile
        ? { status: 200, body: stages }
        : notFound('not-found', 'no stage fixture for that profile');
    }
    const diff = route.match(/^\/results\/([^/]+)\/diff$/);
    if (diff) return notFound('not-archived', 'no diff for this job');
    const result = route.match(/^\/results\/([^/]+)$/);
    if (result) {
      return decodeURIComponent(result[1]) === resultsEscalate.job_id
        ? { status: 200, body: resultsEscalate }
        : notFound('not-found', 'unknown job id');
    }
    if (route === '/system/health') return { status: 200, body: systemHealth.health };
    if (route === '/system/limits') return { status: 200, body: systemHealth.limits };
    if (route.startsWith('/plans/')) {
      return { status: 200, body: { plan_id: route.split('/')[2], state: 'planned', job_sha256: null, assumed: [] } };
    }
    return notFound('not-found', `no route for GET ${route}`);
  }

  // Mutations
  if (method === 'POST' && route === '/system/stop') return { status: 200, body: { stop: Boolean(body?.on) } };
  if (method === 'POST' && route === '/jobs/validate') return { status: 200, body: { valid: true, problems: [] } };
  if (method === 'POST' && route === '/jobs') {
    const jobPath = String(body?.job_path ?? '');
    if (body?.rerun !== true && queuedPaths.has(jobPath)) return errorFromFixture('duplicate-pending');
    queuedPaths.add(jobPath);
    return {
      status: 201,
      body: {
        queued: true,
        queue_file: `_loop/queue/${jobPath.replace(/^jobs\//, '')}`,
        stage_snapshot: { sha256: '9b1c', revision: body?.stage_revision ?? null },
      },
    };
  }
  if (method === 'POST' && route === '/loops/start') return { status: 202, body: { started: body?.loops ?? 0 } };
  if (method === 'POST' && route === '/models/verify') {
    // Test double rule: the `repair_edit_blocks` probe fails; every other probe passes.
    const result = body?.contract === 'repair_edit_blocks' ? 'fail' : 'pass';
    return { status: 200, body: { result, verified_at: '2026-11-03T18:00:00Z' } };
  }
  if (method === 'POST' && route === '/models/verify-reasoning') {
    return { status: 200, body: { reasoning_mode: 'optional', efforts_verified: body?.efforts ?? [] } };
  }
  if (method === 'POST' && route === '/models/refresh') {
    return { status: 200, body: { updated: 0, metadata_at: '2026-11-03T15:00:00Z' } };
  }
  if (method === 'POST' && route === '/plans') return { status: 202, body: { plan_id: 'plan-test-1', state: 'planning' } };
  if (method === 'POST' && /^\/plans\/[^/]+\/approve$/.test(route)) return { status: 202, body: { job_id: 'job-plan-test-1' } };
  if (method === 'PATCH' && /^\/loops\/[^/]+\/stages$/.test(route)) return { status: 200, body: { ok: true, revision: body?.revision } };
  if (method === 'POST' && route === '/stages/validate') return { status: 200, body: { valid: true, problems: [] } };
  if (method === 'PUT' && route === '/stages') return { status: 200, body: { revision: stages.revision + 1 } };
  if (method === 'POST' && route === '/budget/validate') {
    return { status: 200, body: { valid: true, worst_case_usd: 0, problems: [] } };
  }
  if (method === 'POST' && route === '/models') {
    return {
      status: 201,
      body: {
        model_id: body?.model_id,
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
  if (method === 'DELETE' && route.startsWith('/models/')) return { status: 204, body: undefined };
  if (method === 'PUT' && route === '/system/limits') return { status: 200, body: { revision: (body?.revision ?? 0) + 1 } };
  return notFound('not-found', `no route for ${method} ${route}`);
}

export function startFakeFastApiServer(port = PORT) {
  const server = http.createServer(async (req, res) => {
    const origin = DEV_ORIGIN;
    if (req.method === 'OPTIONS') {
      send(res, 204, undefined, origin);
      return;
    }
    let body;
    try {
      body = await readBody(req);
    } catch {
      send(res, 400, { error: { code: 'invalid-json', message: 'body is not JSON', details: {} } }, origin);
      return;
    }
    requestLog.push({ method: req.method, url: req.url, body, idempotencyKey: req.headers['idempotency-key'] ?? null });
    const out = routeRequest(req.method, req.url, body);
    send(res, out.status, out.body, origin);
  });
  server.requestLog = requestLog;
  return new Promise((resolve) => {
    server.listen(port, '127.0.0.1', () => resolve(server));
  });
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  startFakeFastApiServer().then(() => {
    console.log(`fake FastAPI on http://127.0.0.1:${PORT}/api`);
  });
}
