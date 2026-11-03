/**
 * Deterministic Contract-Faithful Mock Transport (`mockTransport.ts`).
 * Returns the exact FastAPI wire contract shapes defined in `docs/API_CONTRACT.md`:
 * - `GET /overview`: `{ stop, loops_running, queue_depth, jobs_waiting, pace, review_usd_today, review_cap_usd, free_today, sandbox_slots }`
 * - `POST /system/stop`: `{ "stop": boolean }`
 * - `POST /jobs/validate`: `{ "valid": boolean, "problems": string[] }`
 * - `POST /jobs`: `{ "queued": true, "queue_file": string, "stage_snapshot": ... }`
 * - `POST /loops/start`: `{ "started": number }`
 * - `POST /models`: Direct `Model` object
 * - `DELETE /models/{id}`: HTTP 204 No Content with empty body
 * - `POST /plans`: HTTP 202 `{ "plan_id": string, "status": "planning" }`, resolved by `GET /plans/{id}`
 */

import { buildValidZipArchiveBytes } from '../api/client';
import { isValidUuidV4 } from '../api/idempotency';
import type {
  ModelRecord,
  ProfileName,
  SemanticContract,
  StageConfigRow,
  StageKey,
} from '../api/types';
import {
  BROADER_CATALOG_CANDIDATES,
  INITIAL_CLAUDE_CLI_MODELS,
  INITIAL_OPENROUTER_MODELS,
  INITIAL_STAGES_BY_PROFILE,
  INITIAL_WIRE_JOBS,
} from './mockData';

export interface MockServerState {
  authoritativeProfile: ProfileName;
  stopActive: boolean;
  indexOk: boolean;
  simulate409Conflict: boolean;
  freeUsed: number;
  freeCap: number;
  paceLast60s: number;
  paceLimit: number;
  sandboxUsed: number;
  sandboxCap: number;
  reviewSpendUsd: string;
  reviewCeilingUsd: string;
  limitsRevision: string | null;
  stageRevision: string;
  jobs: Array<Record<string, any>>;
  openRouterModels: ModelRecord[];
  claudeCliModels: ModelRecord[];
  stagesByProfile: Record<ProfileName, StageConfigRow[]>;
  plans: Map<string, Record<string, any>>;
}

export class DeterministicMockTransport {
  public state: MockServerState;
  private idempotencyResponseCache: Map<
    string,
    { status: number; body: any; binaryBytes?: Uint8Array; filename?: string }
  > = new Map();

  constructor(initialState?: Partial<MockServerState>) {
    this.state = {
      authoritativeProfile: 'test',
      stopActive: false,
      indexOk: true,
      simulate409Conflict: false,
      freeUsed: 58,
      freeCap: 200,
      paceLast60s: 9,
      paceLimit: 15,
      sandboxUsed: 2,
      sandboxCap: 4,
      reviewSpendUsd: '0.1840',
      reviewCeilingUsd: '2.5000',
      limitsRevision: null,
      stageRevision: 'rev-stages-20261103-07',
      jobs: JSON.parse(JSON.stringify(INITIAL_WIRE_JOBS)),
      openRouterModels: JSON.parse(JSON.stringify(INITIAL_OPENROUTER_MODELS)),
      claudeCliModels: JSON.parse(JSON.stringify(INITIAL_CLAUDE_CLI_MODELS)),
      stagesByProfile: JSON.parse(JSON.stringify(INITIAL_STAGES_BY_PROFILE)),
      plans: new Map(),
      ...initialState,
    };
  }

  public syncExternalState(patch: Partial<MockServerState>): void {
    this.state = { ...this.state, ...patch };
  }

  public handleRequest = async (req: {
    method: string;
    path: string;
    headers: Record<string, string>;
    body?: any;
  }): Promise<{ status: number; body: any; binaryBytes?: Uint8Array; filename?: string }> => {
    const { method, path, headers, body } = req;

    if (method !== 'GET') {
      const idemKey = headers['Idempotency-Key'] || headers['idempotency-key'];
      if (!idemKey || !isValidUuidV4(idemKey)) {
        return {
          status: 400,
          body: {
            error: {
              code: 'invalid-idempotency-key',
              message: 'Idempotency-Key header must be a valid RFC-4122 v4 UUID.',
              details: { received: idemKey ?? null },
            },
          },
        };
      }
      const cacheKey = `${method}:${path}:${idemKey}`;
      if (this.idempotencyResponseCache.has(cacheKey)) {
        return this.idempotencyResponseCache.get(cacheKey)!;
      }
      const response = this.executeRoute(method, path, body);
      this.idempotencyResponseCache.set(cacheKey, response);
      return response;
    }

    return this.executeRoute(method, path, body);
  };

  private executeRoute(
    method: string,
    path: string,
    body?: any
  ): { status: number; body: any; binaryBytes?: Uint8Array; filename?: string } {
    // 1. GET /overview (Exact API_CONTRACT.md wire keys: stop, loops_running, queue_depth, jobs_waiting, pace, review_usd_today, review_cap_usd)
    if (method === 'GET' && path === '/overview') {
      const runningCount = this.state.jobs.filter(
        (j) => j.derived_state === 'running' || j.derived_state === 'waiting'
      ).length;
      const queuedCount = this.state.jobs.filter((j) => j.derived_state === 'queued').length;
      const waitingCount = this.state.jobs.filter((j) => j.derived_state === 'waiting').length;
      const reviewOpenCount = this.state.jobs.filter(
        (j) => (j.end_state ?? j.terminal_outcome) === 'review-open'
      ).length;

      return {
        status: 200,
        body: {
          ts: new Date().toISOString(),
          engine_sha: 'b6569d86e2321a8fd544e8d3705a84dbc28b349c',
          active_profile: this.state.authoritativeProfile,
          stop: this.state.stopActive,
          stop_reason: this.state.stopActive ? 'Operator soft STOP' : null,
          loops_running: runningCount,
          queue_depth: queuedCount,
          jobs_waiting: waitingCount,
          pace: this.state.paceLast60s,
          pace_limit: this.state.paceLimit,
          review_usd_today: Number(this.state.reviewSpendUsd),
          review_cap_usd: Number(this.state.reviewCeilingUsd),
          free_today: {
            used: this.state.freeUsed,
            cap: this.state.freeCap,
            remaining: Math.max(0, this.state.freeCap - this.state.freeUsed),
            source: 'live_openrouter_key_limit',
          },
          reviews_pending: reviewOpenCount,
          sandbox_slots: {
            used: this.state.sandboxUsed,
            cap: this.state.sandboxCap,
          },
          active_errors: 2,
          reconciled_cost_usd: null,
          recent_errors: [
            {
              id: 'err-901',
              ts: '2026-11-03T18:39:12Z',
              job_id: 'job-tl-parser-042',
              stage: 'writer_a_repair',
              code: '429-backoff',
              message:
                'Provider rate limit on openrouter/featherless; backed off 18s before transitioning to writer_b_write.',
            },
            {
              id: 'err-900',
              ts: '2026-11-03T18:22:38Z',
              job_id: 'job-schema-guard-039',
              stage: 'review_fix_repair',
              code: 'sandbox-test-red-revert',
              message:
                'Review-fix candidate broke test_null_schema_rejection; restored last test-passing green candidate (sha256:9f84a1c).',
            },
          ],
        },
      };
    }

    // 2. GET /jobs
    if (method === 'GET' && path === '/jobs') {
      return {
        status: 200,
        body: {
          jobs: this.state.jobs,
        },
      };
    }

    // 3. GET /loops/{id}/events?after=<seq>
    const loopEventsMatch = path.match(/^\/loops\/([^/?]+)\/events(?:\?after=(\d+))?/);
    if (method === 'GET' && loopEventsMatch) {
      const jobId = decodeURIComponent(loopEventsMatch[1]);
      const afterSeq = Number(loopEventsMatch[2] ?? 0);
      const job = this.state.jobs.find((j) => j.job_id === jobId);
      if (!job) {
        return {
          status: 404,
          body: {
            error: {
              code: 'job-not-found',
              message: `No loop matches job_id "${jobId}".`,
              details: { job_id: jobId },
            },
          },
        };
      }
      const events = (job.events ?? []).filter((e: any) => Number(e.seq) > afterSeq);
      const nextAfter =
        events.length > 0
          ? events[events.length - 1].seq
          : (job.events ?? []).length > 0
          ? job.events[job.events.length - 1].seq
          : afterSeq;
      return {
        status: 200,
        body: {
          events,
          next_after: nextAfter,
          index_ok: this.state.indexOk,
        },
      };
    }

    // 4. GET /loops/{id}
    const loopMatch = path.match(/^\/loops\/([^/?]+)$/);
    if (method === 'GET' && loopMatch) {
      const jobId = decodeURIComponent(loopMatch[1]);
      const job = this.state.jobs.find((j) => j.job_id === jobId);
      if (!job) {
        return {
          status: 404,
          body: {
            error: {
              code: 'job-not-found',
              message: `No loop matches job_id "${jobId}".`,
              details: { job_id: jobId },
            },
          },
        };
      }
      return { status: 200, body: job };
    }

    // 5. PATCH /loops/{id}/stages
    const patchStageMatch = path.match(/^\/loops\/([^/?]+)\/stages$/);
    if (method === 'PATCH' && patchStageMatch) {
      const jobId = decodeURIComponent(patchStageMatch[1]);
      const job = this.state.jobs.find((j) => j.job_id === jobId);
      if (!job) {
        return {
          status: 404,
          body: {
            error: {
              code: 'job-not-found',
              message: `No loop matches job_id "${jobId}".`,
              details: { job_id: jobId },
            },
          },
        };
      }
      if (this.state.simulate409Conflict || body?.revision !== job.config_revision) {
        return {
          status: 409,
          body: {
            error: {
              code: 'stale-revision',
              message: `Job stage revision conflict: client sent ${body?.revision}, server is at rev-stages-20261103-09.`,
              details: {
                client_revision: body?.revision,
                server_revision: 'rev-stages-20261103-09',
              },
            },
          },
        };
      }
      const stKey = body?.stage as StageKey;
      const stState = (job.stages ?? job.stage_overview)?.[stKey];
      if (stState === 'ran' || stState === 'running') {
        return {
          status: 422,
          body: {
            error: {
              code: 'stage-already-started',
              message: `Stage "${stKey}" has already ${stState} and cannot be modified in flight.`,
              details: { stage: stKey, state: stState },
            },
          },
        };
      }
      return {
        status: 200,
        body: {
          updated: true,
          stage: stKey,
          revision: job.config_revision,
          message: `Updated future unstarted stage "${stKey}" effort=${body?.change?.effort} on ${jobId}.`,
        },
      };
    }

    // 6. GET /models
    if (method === 'GET' && path === '/models') {
      return {
        status: 200,
        body: {
          openrouter_models: this.state.openRouterModels,
          claude_cli_models: this.state.claudeCliModels,
        },
      };
    }

    // 7. GET /models/search?q=
    const searchMatch = path.match(/^\/models\/search\?q=(.*)$/);
    if (method === 'GET' && searchMatch) {
      const q = decodeURIComponent(searchMatch[1] || '').toLowerCase();
      const results = BROADER_CATALOG_CANDIDATES.filter(
        (c) =>
          !q ||
          c.model_id.toLowerCase().includes(q) ||
          c.display_name.toLowerCase().includes(q)
      );
      return {
        status: 200,
        body: { results },
      };
    }

    // 8. POST /models (API_CONTRACT.md: returns the Model object directly)
    if (method === 'POST' && path === '/models') {
      const modelId = String(body?.model_id ?? '');
      if (this.state.openRouterModels.length >= 20) {
        return {
          status: 422,
          body: {
            error: {
              code: 'pool-max-exceeded',
              message: 'Curated OpenRouter model pool is at maximum capacity (20 models).',
              details: { max_pool: 20 },
            },
          },
        };
      }
      const cand =
        BROADER_CATALOG_CANDIDATES.find((c) => c.model_id === modelId) ?? {
          ...BROADER_CATALOG_CANDIDATES[0],
          model_id: modelId || BROADER_CATALOG_CANDIDATES[0].model_id,
        };
      if (!this.state.openRouterModels.some((m) => m.model_id === cand.model_id)) {
        this.state.openRouterModels = [...this.state.openRouterModels, cand];
      }
      return {
        status: 201,
        body: cand,
      };
    }

    // 9. DELETE /models/{id} (API_CONTRACT.md: returns HTTP 204 No Content with empty body)
    const delModelMatch = path.match(/^\/models\/([^/?]+)$/);
    if (method === 'DELETE' && delModelMatch) {
      const rawEncoded = delModelMatch[1];
      const modelId = decodeURIComponent(rawEncoded);
      const target = this.state.openRouterModels.find((m) => m.model_id === modelId);
      if (!target) {
        return {
          status: 404,
          body: {
            error: {
              code: 'model-not-found',
              message: `Model "${modelId}" not found in OpenRouter pool.`,
              details: { model_id: modelId },
            },
          },
        };
      }
      if (target.in_use_stages.length > 0) {
        return {
          status: 422,
          body: {
            error: {
              code: 'model-in-use',
              message: `Cannot remove "${modelId}" while assigned to stage(s): ${target.in_use_stages.join(', ')}.`,
              details: { in_use_stages: target.in_use_stages },
            },
          },
        };
      }
      if (this.state.openRouterModels.length <= 3) {
        return {
          status: 422,
          body: {
            error: {
              code: 'pool-min-policy',
              message: `Server policy enforces minimum pool size (sample fixture has ${this.state.openRouterModels.length} records; C-10 encoded path /api/models/${rawEncoded}).`,
              details: { current_count: this.state.openRouterModels.length, target_min: 10 },
            },
          },
        };
      }
      this.state.openRouterModels = this.state.openRouterModels.filter(
        (m) => m.model_id !== modelId
      );
      return {
        status: 204,
        body: null,
      };
    }

    // 10. POST /models/verify
    if (method === 'POST' && path === '/models/verify') {
      const modelId = String(body?.model_id ?? '');
      const contract = body?.contract as SemanticContract;
      const nowIso = new Date().toISOString();
      this.state.openRouterModels = this.state.openRouterModels.map((m) =>
        m.model_id === modelId
          ? {
              ...m,
              contracts: { ...m.contracts, [contract]: 'verified' },
              verified_at: nowIso,
              freshness: 'just verified',
            }
          : m
      );
      return {
        status: 200,
        body: {
          model_id: modelId,
          contract,
          contract_state: 'verified',
          verified_at: nowIso,
        },
      };
    }

    // 11. POST /models/verify-reasoning
    if (method === 'POST' && path === '/models/verify-reasoning') {
      const modelId = String(body?.model_id ?? '');
      const efforts = body?.efforts ?? ['low', 'medium', 'high'];
      const nowIso = new Date().toISOString();
      return {
        status: 200,
        body: {
          model_id: modelId,
          efforts,
          verified_at: nowIso,
        },
      };
    }

    // 12. POST /models/refresh
    if (method === 'POST' && path === '/models/refresh') {
      return {
        status: 200,
        body: {
          refreshed_at: new Date().toISOString(),
        },
      };
    }

    // 13. GET /stages?profile=
    const getStagesMatch = path.match(/^\/stages(?:\?profile=([^&]+))?$/);
    if (method === 'GET' && getStagesMatch) {
      const prof = (getStagesMatch[1] === 'production' ? 'production' : 'test') as ProfileName;
      return {
        status: 200,
        body: {
          revision: this.state.stageRevision,
          authoritative_runtime_profile: this.state.authoritativeProfile,
          stages: this.state.stagesByProfile[prof],
        },
      };
    }

    // 14. POST /stages/validate
    if (method === 'POST' && path === '/stages/validate') {
      const changes = (body?.changes ?? []) as StageConfigRow[];
      const overMax = changes.find((s) => s.max_output_tokens > 32768);
      if (overMax) {
        return {
          status: 422,
          body: {
            error: {
              code: 'max-output-exceeded',
              message: `Stage "${overMax.stage_key}" max_output_tokens (${overMax.max_output_tokens}) exceeds verified model capability (32,768).`,
              details: { stage_key: overMax.stage_key, max_output_tokens: overMax.max_output_tokens },
            },
          },
        };
      }
      return {
        status: 200,
        body: {
          valid: true,
          problems: [],
        },
      };
    }

    // 15. PUT /stages
    if (method === 'PUT' && path === '/stages') {
      if (this.state.simulate409Conflict || body?.revision !== this.state.stageRevision) {
        return {
          status: 409,
          body: {
            error: {
              code: 'stale-revision',
              message: `Stage revision mismatch: client sent ${body?.revision}, server is at rev-stages-20261103-09.`,
              details: {
                client_revision: body?.revision,
                server_revision: 'rev-stages-20261103-09',
              },
            },
          },
        };
      }
      const prof = (body?.profile === 'production' ? 'production' : 'test') as ProfileName;
      const changes = (body?.changes ?? this.state.stagesByProfile[prof]) as StageConfigRow[];
      const overMax = changes.find((s) => s.max_output_tokens > 32768);
      if (overMax) {
        return {
          status: 422,
          body: {
            error: {
              code: 'max-output-exceeded',
              message: `Stage "${overMax.stage_key}" max_output_tokens (${overMax.max_output_tokens}) exceeds verified model capability (32,768).`,
              details: { stage_key: overMax.stage_key },
            },
          },
        };
      }
      const nextRevNum = Number(this.state.stageRevision.split('-').pop() || '7') + 1;
      const nextRev = `rev-stages-20261103-${String(nextRevNum).padStart(2, '0')}`;
      this.state.stageRevision = nextRev;
      this.state.stagesByProfile[prof] = changes;
      return {
        status: 200,
        body: {
          revision: nextRev,
          profile: prof,
          stages: changes,
        },
      };
    }

    // 16. GET /results/{id}/diff
    const diffMatch = path.match(/^\/results\/([^/?]+)\/diff$/);
    if (method === 'GET' && diffMatch) {
      const jobId = decodeURIComponent(diffMatch[1]);
      const job = this.state.jobs.find((j) => j.job_id === jobId);
      if (!job) {
        return {
          status: 404,
          body: {
            error: {
              code: 'result-not-found',
              message: `No result matches job_id "${jobId}".`,
              details: { job_id: jobId },
            },
          },
        };
      }
      return {
        status: 200,
        body: {
          job_id: jobId,
          diff_text: job.diff_text ?? null,
        },
      };
    }

    // 17. GET /results/{id}/download (Valid IEEE 802.3 CRC-32 PKZIP binary)
    const downloadMatch = path.match(/^\/results\/([^/?]+)\/download$/);
    if (method === 'GET' && downloadMatch) {
      const jobId = decodeURIComponent(downloadMatch[1]);
      const job = this.state.jobs.find((j) => j.job_id === jobId);
      if (!job || !job.archive?.available) {
        return {
          status: 404,
          body: {
            error: {
              code: 'archive-unavailable',
              message:
                job?.archive?.failure_reason ??
                `Archive ZIP is unavailable for job "${jobId}".`,
              details: { job_id: jobId },
            },
          },
        };
      }
      const binaryBytes = buildValidZipArchiveBytes(jobId, JSON.stringify(job, null, 2));
      return {
        status: 200,
        body: { ok: true },
        binaryBytes,
        filename: `${jobId}-archive.zip`,
      };
    }

    // 18. GET /results/{id}
    const resultMatch = path.match(/^\/results\/([^/?]+)$/);
    if (method === 'GET' && resultMatch) {
      const jobId = decodeURIComponent(resultMatch[1]);
      const job = this.state.jobs.find((j) => j.job_id === jobId);
      if (!job) {
        return {
          status: 404,
          body: {
            error: {
              code: 'result-not-found',
              message: `No result matches job_id "${jobId}".`,
              details: { job_id: jobId },
            },
          },
        };
      }
      return { status: 200, body: job };
    }

    // 19. POST /jobs/validate (API_CONTRACT.md: returns { valid, problems })
    if (method === 'POST' && path === '/jobs/validate') {
      const jobPath = String(body?.job_path ?? '');
      if (jobPath.includes('..') || !jobPath.startsWith('jobs/') || !jobPath.endsWith('.json')) {
        return {
          status: 400,
          body: {
            error: {
              code: 'invalid-job-path',
              message: `job_path "${jobPath}" must be a repo-relative path under jobs/ ending with .json without traversal.`,
              details: { job_path: jobPath },
            },
          },
        };
      }
      return {
        status: 200,
        body: {
          valid: true,
          problems: [],
          snapshot_sha256: 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
        },
      };
    }

    // 20. POST /budget/validate
    if (method === 'POST' && path === '/budget/validate') {
      const paidStr = String(body?.paid_budget_usd ?? '0.00');
      const num = parseFloat(paidStr);
      if (isNaN(num) || num < 0) {
        return {
          status: 400,
          body: {
            error: {
              code: 'invalid-budget',
              message: 'Authorized paid budget must be a non-negative decimal USD amount.',
              details: { paid_budget_usd: paidStr },
            },
          },
        };
      }
      if (this.state.authoritativeProfile === 'test' && num > 0) {
        return {
          status: 422,
          body: {
            error: {
              code: 'profile-refusal',
              message: 'Active profile "test" forbids paid OpenRouter spend (> $0.00).',
              details: { profile: 'test', paid_budget_usd: paidStr },
            },
          },
        };
      }
      return {
        status: 200,
        body: {
          valid: true,
          paid_budget_usd: num.toFixed(2),
        },
      };
    }

    // 21. POST /jobs (API_CONTRACT.md: returns { queued, queue_file, stage_snapshot })
    if (method === 'POST' && path === '/jobs') {
      const jobPath = String(body?.job_path ?? '');
      const isRerun = Boolean(body?.rerun);

      if (
        this.state.freeUsed >= this.state.freeCap ||
        this.state.paceLast60s >= this.state.paceLimit
      ) {
        return {
          status: 429,
          body: {
            error: {
              code: 'quota-exhausted',
              message: `Cannot queue job while pace (${this.state.paceLast60s}/${this.state.paceLimit}) or daily free cap (${this.state.freeUsed}/${this.state.freeCap}) is exhausted.`,
              details: {
                pace_60s: this.state.paceLast60s,
                free_used: this.state.freeUsed,
              },
            },
          },
        };
      }

      const existing = this.state.jobs.find((j) => j.job_path === jobPath);
      if (
        existing &&
        (existing.derived_state === 'running' ||
          existing.derived_state === 'waiting' ||
          existing.derived_state === 'queued')
      ) {
        return {
          status: 409,
          body: {
            error: {
              code: 'duplicate-active-job',
              message: `Job "${existing.job_id}" (${jobPath}) is already ${existing.derived_state}.`,
              details: { job_id: existing.job_id, derived_state: existing.derived_state },
            },
          },
        };
      }

      if (existing && !isRerun) {
        return {
          status: 409,
          body: {
            error: {
              code: 'completed-requires-rerun',
              message: `"${jobPath}" already completed (${
                existing.end_state ?? existing.terminal_outcome
              }). Set rerun=true to queue an intentional repeat run.`,
              details: { job_id: existing.job_id, end_state: existing.end_state },
            },
          },
        };
      }

      const newId = `job-queued-${100 + this.state.jobs.length}`;
      const nowIso = new Date().toISOString();
      const queueFile = `_loop/queue/${newId}.json`;
      const stageSnapshot = {
        revision: body?.stage_revision ?? this.state.stageRevision,
        stages: body?.stages ?? this.state.stagesByProfile[this.state.authoritativeProfile],
      };

      const newWireJob: Record<string, any> = {
        ...INITIAL_WIRE_JOBS[0],
        job_id: newId,
        job_path: jobPath,
        spec_id: jobPath.replace('jobs/', '').replace('.json', '').toUpperCase(),
        derived_state: 'queued',
        end_state: null,
        test_verdict: 'not-run',
        reviewer_verdict: 'not-run',
        merge_state: 'none',
        active_stage: null,
        logical_role: null,
        active_attempt_id: null,
        waiting: null,
        started: nowIso,
        finished: null,
        duration_sec: 0,
        free_requests_used: 0,
        attempts: [],
        reviews: [],
        events: [
          {
            seq: 1,
            ts: nowIso,
            type: 'wait-start',
            attempt_id: `${newId}-init`,
            stage: 'writer_a_write',
            role: 'writer_a',
            model: 'deepseek/deepseek-chat-v3-0324:free',
            endpoint: 'openrouter/chutes-fp8',
            effort: 'medium',
            detail: `Queued job snapshot (${this.state.stageRevision}) -> ${queueFile}.`,
          },
        ],
      };

      this.state.jobs = [newWireJob, ...this.state.jobs];
      return {
        status: 201,
        body: {
          queued: true,
          job_id: newId,
          queue_file: queueFile,
          stage_snapshot: stageSnapshot,
        },
      };
    }

    // 22. POST /loops/start (API_CONTRACT.md: returns { started: number })
    if (method === 'POST' && path === '/loops/start') {
      const loopsCount = Number(body?.loops ?? 1);
      if (this.state.stopActive) {
        return {
          status: 422,
          body: {
            error: {
              code: 'stop-active',
              message: 'Cannot start loop workers while Global Soft STOP is active.',
              details: { stop_active: true },
            },
          },
        };
      }
      return {
        status: 200,
        body: {
          started: loopsCount,
        },
      };
    }

    // 23. POST /plans (API_CONTRACT.md: returns HTTP 202 { plan_id, status: "planning" })
    if (method === 'POST' && path === '/plans') {
      if (this.state.authoritativeProfile === 'test') {
        return {
          status: 422,
          body: {
            error: {
              code: 'profile-refusal',
              message:
                'Active runtime profile "test" forbids natural-language planning and paid/Opus calls.',
              details: { profile: 'test', rule: 'no_paid_or_planner_in_test' },
            },
          },
        };
      }
      if (!body?.cost_authorized) {
        return {
          status: 400,
          body: {
            error: {
              code: 'authorization-required',
              message:
                'Deliberate cost/CLI authorization must be confirmed before invoking the planner.',
              details: {},
            },
          },
        };
      }
      const filesList = Array.isArray(body?.allowed_files) ? body.allowed_files : [];
      if (filesList.length === 0 || filesList.length > 30) {
        return {
          status: 400,
          body: {
            error: {
              code: 'allowed-files-bounds',
              message: `Planner requires between 1 and 30 allowed repo-relative files (received ${filesList.length}).`,
              details: { count: filesList.length },
            },
          },
        };
      }
      const planId = 'plan-prod-902';
      this.state.plans.set(planId, {
        plan_id: planId,
        status: 'ready',
        job_sha256: 'a948904f2f0f479b8f8197694b30184b0d2ed1c1cd2a1ec0fb85d299a192a447',
        known: [
          `Target files restricted to ${filesList.length} path(s): ${filesList.join(', ')}`,
          'Acceptance tests are immutable once INTENT.json is locked',
        ],
        assumed: [
          {
            id: 'as-1',
            text: 'Malformed UTF-8 continuation bytes raise FrameDecodeError rather than returning replacement char U+FFFD',
            checked: false,
          },
          {
            id: 'as-2',
            text: 'Maximum nested frame depth is bounded to 32 levels in production spec',
            checked: false,
          },
        ],
      });
      return {
        status: 202,
        body: {
          plan_id: planId,
          status: 'planning',
        },
      };
    }

    // 23b. GET /plans/{id}
    const getPlanMatch = path.match(/^\/plans\/([^/?]+)$/);
    if (method === 'GET' && getPlanMatch) {
      const planId = decodeURIComponent(getPlanMatch[1]);
      const plan = this.state.plans.get(planId) ?? {
        plan_id: planId,
        status: 'ready',
        job_sha256: 'a948904f2f0f479b8f8197694b30184b0d2ed1c1cd2a1ec0fb85d299a192a447',
        known: ['Acceptance tests are immutable once INTENT.json is locked'],
        assumed: [
          {
            id: 'as-1',
            text: 'Malformed UTF-8 continuation bytes raise FrameDecodeError',
            checked: false,
          },
        ],
      };
      return {
        status: 200,
        body: plan,
      };
    }

    // 24. POST /plans/{id}/approve
    const approveMatch = path.match(/^\/plans\/([^/?]+)\/approve$/);
    if (method === 'POST' && approveMatch) {
      const planId = decodeURIComponent(approveMatch[1]);
      return {
        status: 200,
        body: {
          approved: true,
          started_job_id: 'job-planned-902',
          message: `Approved ${planId} with verified job_sha256 and started execution.`,
        },
      };
    }

    // 25. GET /system/health
    if (method === 'GET' && path === '/system/health') {
      return {
        status: 200,
        body: {
          status: 'ok',
          ts: new Date().toISOString(),
          docker_status: 'healthy (daemon 27.3.1, rootless isolation active)',
          sandbox_image: 'satvik-sandbox:rev4-py312',
          engine_main_sha: 'b6569d86e2321a8fd544e8d3705a84dbc28b349c',
          active_profile: this.state.authoritativeProfile,
          stop: {
            on: this.state.stopActive,
            mode: 'soft',
          },
        },
      };
    }

    // 26. GET /system/limits
    if (method === 'GET' && path === '/system/limits') {
      return {
        status: 200,
        body: {
          sandbox_slots: this.state.sandboxCap,
          review_spend_cap_usd: this.state.reviewCeilingUsd,
          revision: this.state.limitsRevision,
        },
      };
    }

    // 27. PUT /system/limits
    if (method === 'PUT' && path === '/system/limits') {
      if (!body?.revision) {
        return {
          status: 422,
          body: {
            error: {
              code: 'missing-revision-c08',
              message:
                'PUT /api/system/limits requires a non-null revision token (Gap C-08).',
              details: {},
            },
          },
        };
      }
      this.state.sandboxCap = Number(body.sandbox_slots ?? this.state.sandboxCap);
      this.state.reviewCeilingUsd = String(
        body.review_spend_cap_usd ?? this.state.reviewCeilingUsd
      );
      const nextRev = 'rev-limits-mock-02';
      this.state.limitsRevision = nextRev;
      return {
        status: 200,
        body: {
          sandbox_slots: this.state.sandboxCap,
          review_spend_cap_usd: this.state.reviewCeilingUsd,
          revision: nextRev,
        },
      };
    }

    // 28. POST /system/stop (API_CONTRACT.md: returns { "stop": boolean })
    if (method === 'POST' && path === '/system/stop') {
      const nextOn = Boolean(body?.on);
      this.state.stopActive = nextOn;
      return {
        status: 200,
        body: {
          stop: nextOn,
        },
      };
    }

    return {
      status: 404,
      body: {
        error: {
          code: 'route-not-found',
          message: `Unrecognized mock endpoint ${method} /api${path}`,
          details: {},
        },
      },
    };
  }
}
