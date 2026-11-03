import http from 'node:http';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { loadTsModule } from './ts_loader.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

export function loadClientModules(customFetch) {
  const idempotencyPath = path.resolve(__dirname, '../src/api/idempotency.ts');
  const normalizerPath = path.resolve(__dirname, '../src/api/normalizer.ts');
  const clientPath = path.resolve(__dirname, '../src/api/client.ts');
  const mockTransportPath = path.resolve(__dirname, '../src/mocks/mockTransport.ts');

  const idempotencyMod = loadTsModule(idempotencyPath, { fetch: customFetch });
  const normalizerMod = loadTsModule(normalizerPath, { fetch: customFetch });
  const clientMod = loadTsModule(clientPath, { fetch: customFetch });
  const mockTransportMod = loadTsModule(mockTransportPath, { fetch: customFetch });
  return { ...idempotencyMod, ...normalizerMod, ...clientMod, ...mockTransportMod };
}

export async function startFakeFastApiServer() {
  const state = {
    requests: [],
    stagesRev: 'rev_a1b2c3d4',
    stop: false,
    models: [
      {
        model_id: 'openrouter/qwen/qwen3-coder:free',
        display_name: 'Qwen3 Coder 480B (Free)',
        provider: 'OpenRouter',
        billing_type: 'free',
        context_window: 131072,
        max_output_tokens: 32768,
        contracts: {
          planner_json: 'verified',
          writer_file_blocks: 'verified',
          repair_edit_blocks: 'verified',
          repair_whole_file: 'verified',
          reviewer_verdict: 'verified',
          native_structured_output: 'verified',
        },
      },
    ],
  };

  const server = http.createServer(async (req, res) => {
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    const rawBody = Buffer.concat(chunks).toString('utf8');
    let parsedBody = null;
    try {
      parsedBody = rawBody ? JSON.parse(rawBody) : null;
    } catch {
      parsedBody = rawBody;
    }

    state.requests.push({
      method: req.method,
      url: req.url,
      headers: req.headers,
      body: parsedBody,
    });

    const sendJson = (status, data) => {
      res.writeHead(status, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify(data));
    };

    // GET /api/system/health
    if (req.method === 'GET' && req.url === '/api/system/health') {
      return sendJson(200, {
        status: 'ok',
        docker_available: true,
        repo_clean: true,
        active_leases: 2,
        stop_file_present: state.stop,
        sqlite_ok: true,
        checked_at: '2026-11-03T10:15:42Z',
      });
    }

    // GET /api/system/limits
    if (req.method === 'GET' && req.url === '/api/system/limits') {
      return sendJson(200, {
        sandbox_slots: 2,
        review_spend_cap_usd: '0.5000',
        revision: 'rev-limits-01',
      });
    }

    // GET /api/overview (Exact API_CONTRACT.md shape from Revision 3 Audit)
    if (req.method === 'GET' && req.url === '/api/overview') {
      return sendJson(200, {
        stop: state.stop,
        loops_running: 1,
        loops_max: 2,
        queue_depth: 3,
        jobs_waiting: 1,
        free_today: 58,
        free_cap: 200,
        pace: 4,
        pace_limit: 15,
        review_usd_today: 0.0,
        review_cap_usd: 0.5,
        sandbox_slots: '1/2',
        docker_healthy: true,
        ts: '2026-11-03T10:15:42Z',
      });
    }

    // POST /api/system/stop (Exact API_CONTRACT.md shape: {"stop": bool})
    if (req.method === 'POST' && req.url === '/api/system/stop') {
      state.stop = Boolean(parsedBody?.on ?? parsedBody?.stop);
      return sendJson(200, {
        stop: state.stop,
      });
    }

    // POST /api/jobs/validate (Exact API_CONTRACT.md shape: {"valid": bool, "problems": [...]})
    if (req.method === 'POST' && req.url === '/api/jobs/validate') {
      const pathStr = parsedBody?.job_path || parsedBody?.job_file || '';
      if (!pathStr.startsWith('jobs/') || pathStr.includes('..')) {
        return sendJson(200, {
          valid: false,
          problems: ['Job path must stay inside jobs/<spec>.json with no traversal.'],
        });
      }
      return sendJson(200, {
        valid: true,
        problems: [],
      });
    }

    // POST /api/jobs (Exact API_CONTRACT.md shape: {"queued": true, "queue_file": "...", "stage_snapshot": {...}})
    if (req.method === 'POST' && req.url === '/api/jobs') {
      return sendJson(201, {
        queued: true,
        queue_file: parsedBody?.job_path || 'jobs/auth_token_refresh.json',
        stage_snapshot: {
          rev: state.stagesRev,
          stages: {
            planner: { primary: 'openrouter/qwen/qwen3-coder:free' },
          },
        },
      });
    }

    // POST /api/loops/start (Exact API_CONTRACT.md shape: {"started": 2})
    if (req.method === 'POST' && req.url === '/api/loops/start') {
      const count = Number(parsedBody?.loops ?? parsedBody?.count ?? 1);
      if (count < 1 || count > 2) {
        return sendJson(422, { error: { code: 'invalid-loops', message: 'Worker count must be 1 or 2.' } });
      }
      return sendJson(200, {
        started: count,
      });
    }

    // POST /api/plans (HTTP 202 Accepted)
    if (req.method === 'POST' && req.url === '/api/plans') {
      return sendJson(202, {
        plan_id: 'plan_test_99',
        status: 'planning',
      });
    }

    // GET /api/plans/plan_test_99
    if (req.method === 'GET' && req.url === '/api/plans/plan_test_99') {
      return sendJson(200, {
        plan_id: 'plan_test_99',
        status: 'ready',
        job_sha256: 'sha256_plan_99_verified',
        known: ['Target file loop/retry.py exists'],
        assumed: [
          { id: 'as-1', text: 'Preserve exponential backoff jitter', checked: true },
        ],
      });
    }

    // GET /api/models
    if (req.method === 'GET' && req.url === '/api/models') {
      return sendJson(200, { models: state.models });
    }

    // POST /api/models (Exact API_CONTRACT.md shape: returns direct Model object)
    if (req.method === 'POST' && req.url === '/api/models') {
      const createdModel = {
        model_id: parsedBody?.model_id || 'openrouter/deepseek/deepseek-chat-v3:free',
        display_name: 'DeepSeek V3 Free',
        provider: 'OpenRouter',
        billing_type: 'free',
        context_window: 65536,
        max_output_tokens: 32768,
        contracts: {
          planner_json: 'unverified',
          writer_file_blocks: 'unverified',
          repair_edit_blocks: 'unverified',
          repair_whole_file: 'unverified',
          reviewer_verdict: 'unverified',
          native_structured_output: 'unverified',
        },
      };
      state.models.push(createdModel);
      return sendJson(201, createdModel);
    }

    // DELETE /api/models/:id (Exact API_CONTRACT.md shape: HTTP 204 No Content with empty body)
    if (req.method === 'DELETE' && req.url.startsWith('/api/models/')) {
      const modelId = decodeURIComponent(req.url.slice('/api/models/'.length));
      if (modelId === 'openrouter/qwen/qwen3-coder:free') {
        return sendJson(409, {
          error: {
            code: 'model-in-use',
            message: 'Cannot delete model openrouter/qwen/qwen3-coder:free because it is assigned to active stage planner.',
          },
        });
      }
      res.writeHead(204);
      return res.end();
    }

    // PUT /api/stages (Optimistic concurrency revision check)
    if (req.method === 'PUT' && req.url === '/api/stages') {
      const sentRev = parsedBody?.revision ?? parsedBody?.rev;
      if (sentRev !== state.stagesRev) {
        return sendJson(409, {
          error: {
            code: 'revision-conflict',
            message: `Revision conflict: expected ${state.stagesRev}, got ${sentRev}`,
          },
        });
      }
      state.stagesRev = 'rev_e5f6g7h8';
      return sendJson(200, {
        revision: state.stagesRev,
        profile: parsedBody?.profile ?? 'test',
        stages: parsedBody?.changes ?? [],
      });
    }

    // GET /api/results/:id/download (Binary ZIP download)
    if (req.method === 'GET' && req.url === '/api/results/job_rate_limiter_refactor/download') {
      const zipMagic = Buffer.from([0x50, 0x4b, 0x03, 0x04, 0x14, 0x00, 0x00, 0x00]);
      res.writeHead(200, { 'Content-Type': 'application/zip' });
      return res.end(zipMagic);
    }

    return sendJson(404, { error: { code: 'not-found', message: `Unhandled route ${req.method} ${req.url}` } });
  });

  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const address = server.address();
  const baseUrl = `http://127.0.0.1:${address.port}/api`;

  return {
    baseUrl,
    state,
    close: () => new Promise((resolve) => server.close(resolve)),
  };
}
