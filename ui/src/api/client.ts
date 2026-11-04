/**
 * Typed HTTP client for the Satvik Loop control API (docs/API_CONTRACT.md).
 *
 * - Every mutation sends an `Idempotency-Key` header holding an RFC-4122 v4 UUID.
 * - Payloads are built to the contract's request shapes (section 3). Nothing is invented.
 * - Responses are normalized by `normalizer.ts`. Missing values stay null.
 * - Mock mode answers from the canonical fixtures through `mockHandler`; live mode uses fetch.
 */

import { isValidUuidV4 } from './idempotency';
import {
  normalizeEventPage,
  normalizeHealth,
  normalizeJobDetail,
  normalizeJobSummary,
  normalizeLimits,
  normalizeModel,
  normalizeModelList,
  normalizeOverview,
  normalizePlan,
  normalizeResult,
  normalizeStages,
  normalizeVerifyResult,
  isRecord,
} from './normalizer';
import type {
  Effort,
  EventPage,
  JobRecord,
  JobSummary,
  ModelRecord,
  NormalizedOverview,
  PlanRecord,
  ProfileName,
  ResultRecord,
  SemanticContract,
  StageChange,
  StageKey,
  StageSetting,
  StagesResponse,
  SystemHealth,
  SystemLimits,
  VerifyResult,
  DataMode,
  Via,
} from './types';

export const DEFAULT_API_BASE_URL = 'http://127.0.0.1:8765/api';

export class ApiHttpError extends Error {
  public status: number;
  public code: string;
  public details: Record<string, unknown>;
  public endpoint: string;
  public idempotencyKey?: string;

  constructor(params: {
    status: number;
    code: string;
    message: string;
    details?: Record<string, unknown>;
    endpoint: string;
    idempotencyKey?: string;
  }) {
    super(params.message);
    this.name = 'ApiHttpError';
    this.status = params.status;
    this.code = params.code;
    this.details = params.details ?? {};
    this.endpoint = params.endpoint;
    this.idempotencyKey = params.idempotencyKey;
  }
}

/** `DELETE /models/{id}`: the id is percent-encoded so `/` inside an OpenRouter id stays one path segment. */
export function buildModelPath(modelId: string): string {
  if (!modelId || typeof modelId !== 'string') {
    throw new Error('Invalid model_id');
  }
  return `/models/${encodeURIComponent(modelId)}`;
}

/**
 * Builds the keyed `stages` object of POST /jobs and POST /budget/validate:
 * `{"<stage>": {"model", "effort", "settings"}}`.
 */
export function buildStagesPayload(
  rows: StageSetting[]
): Record<string, { model: string | null; via: Via | null; effort: Effort | null; settings: Record<string, unknown> }> {
  const out: Record<string, { model: string | null; via: Via | null; effort: Effort | null; settings: Record<string, unknown> }> = {};
  for (const row of rows) {
    out[row.stage_key] = {
      model: row.configured_model_id,
      via: row.via,
      effort: row.effort,
      settings: row.settings,
    };
  }
  return out;
}

/** Rows of PUT /stages and POST /stages/validate (`changes`). Each row carries its model's `via` with the model (I61). */
export function buildStageChanges(rows: StageSetting[]): StageChange[] {
  return rows.map((row) => ({
    stage: row.stage_key,
    model: row.configured_model_id,
    via: row.via,
    effort: row.effort,
    settings: row.settings,
  }));
}

/**
 * A stage row after its model was picked in the select (I61). The model's transport (`via`) comes with it, so an OpenRouter
 * model is never sent on a Claude Code stage, or the reverse. The effort travels with it too. A model the listing does not know
 * keeps the row's own transport: the API then refuses the change (model-not-selectable), it is not guessed here.
 */
export function stageRowWithModel(
  row: StageSetting,
  modelId: string,
  models: ReadonlyArray<{ model_id: string; via: Via | null }>
): StageSetting {
  const known = models.find((m) => m.model_id === modelId);
  return {
    ...row,
    configured_model_id: modelId,
    via: known ? known.via : row.via,
    effort: row.effort,
  };
}

export interface SatvikApiClientOptions {
  baseUrl?: string;
  getMode: () => DataMode;
  mockHandler?: (req: {
    method: string;
    path: string;
    headers: Record<string, string>;
    body?: any;
  }) => Promise<{ status: number; body: any }>;
}

export class SatvikApiClient {
  private baseUrl: string;
  private getMode: () => DataMode;
  private mockHandler?: SatvikApiClientOptions['mockHandler'];

  constructor(options: SatvikApiClientOptions) {
    this.baseUrl = (options.baseUrl ?? DEFAULT_API_BASE_URL).replace(/\/+$/, '');
    this.getMode = options.getMode;
    this.mockHandler = options.mockHandler;
  }

  public async requestJson<T = any>(params: {
    method: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
    path: string;
    body?: unknown;
    idempotencyKey?: string;
    signal?: AbortSignal;
  }): Promise<T> {
    const { method, path, body, idempotencyKey, signal } = params;
    const endpointLabel = `${method} /api${path}`;
    const headers: Record<string, string> = { Accept: 'application/json' };

    if (method !== 'GET') {
      if (!idempotencyKey || !isValidUuidV4(idempotencyKey)) {
        throw new ApiHttpError({
          status: 400,
          code: 'invalid-idempotency-key',
          message: `Mutation ${endpointLabel} requires a valid RFC-4122 v4 UUID Idempotency-Key header.`,
          endpoint: endpointLabel,
          idempotencyKey,
        });
      }
      headers['Content-Type'] = 'application/json';
      headers['Idempotency-Key'] = idempotencyKey;
    }

    if (this.getMode() === 'mock') {
      if (!this.mockHandler) {
        throw new ApiHttpError({
          status: 503,
          code: 'mock-transport-unconfigured',
          message: 'Mock transport handler is not configured.',
          endpoint: endpointLabel,
          idempotencyKey,
        });
      }
      const mockRes = await this.mockHandler({ method, path, headers, body });
      if (mockRes.status >= 400) {
        throw errorFromEnvelope(mockRes.status, mockRes.body, endpointLabel, idempotencyKey);
      }
      return mockRes.body as T;
    }

    let response: Response;
    try {
      response = await fetch(`${this.baseUrl}${path}`, {
        method,
        headers,
        body: body !== undefined ? JSON.stringify(body) : undefined,
        signal,
      });
    } catch (networkErr: any) {
      if (networkErr?.name === 'AbortError') throw networkErr;
      throw new ApiHttpError({
        status: 503,
        code: 'backend-unreachable',
        // A mutation may have reached the backend before the connection failed: its outcome is unknown, not "nothing changed" (I61).
        message:
          method === 'GET'
            ? `Cannot reach the live backend at ${this.baseUrl}${path} (${networkErr?.message || 'connection refused'}). Nothing was changed.`
            : `Cannot reach the live backend at ${this.baseUrl}${path} (${
                networkErr?.message || 'connection refused'
              }). The outcome is UNKNOWN: the request may have reached the backend. Reload and check the current state before you retry.`,
        endpoint: endpointLabel,
        idempotencyKey,
      });
    }

    if (response.status === 204) {
      return undefined as unknown as T;
    }

    const rawText = await response.text();
    let parsedBody: any = null;
    if (rawText.trim().length > 0) {
      try {
        parsedBody = JSON.parse(rawText);
      } catch {
        throw new ApiHttpError({
          status: response.status,
          code: 'malformed-json-response',
          message: `Backend returned a non-JSON body from ${endpointLabel} (HTTP ${response.status}).`,
          endpoint: endpointLabel,
          idempotencyKey,
        });
      }
    }

    if (!response.ok) {
      throw errorFromEnvelope(response.status, parsedBody, endpointLabel, idempotencyKey);
    }
    return parsedBody as T;
  }

  // ---- Reads -------------------------------------------------------------

  public async getOverview(signal?: AbortSignal): Promise<NormalizedOverview> {
    const raw = await this.requestJson<unknown>({ method: 'GET', path: '/overview', signal });
    return normalizeOverview(isRecord(raw) ? raw : {});
  }

  /** `GET /jobs`. The list envelope carries `index_ok`. */
  public async getJobs(signal?: AbortSignal): Promise<{ jobs: JobSummary[]; index_ok: boolean | null }> {
    const raw = await this.requestJson<unknown>({ method: 'GET', path: '/jobs', signal });
    const body = isRecord(raw) ? raw : {};
    const items: unknown[] = Array.isArray(raw) ? raw : Array.isArray(body.jobs) ? body.jobs : [];
    return {
      jobs: items.filter(isRecord).map((j) => normalizeJobSummary(j)),
      index_ok: typeof body.index_ok === 'boolean' ? body.index_ok : null,
    };
  }

  public async getLoop(jobId: string, signal?: AbortSignal): Promise<JobRecord> {
    const raw = await this.requestJson<unknown>({
      method: 'GET',
      path: `/loops/${encodeURIComponent(jobId)}`,
      signal,
    });
    return normalizeJobDetail(isRecord(raw) ? raw : { job_id: jobId });
  }

  public async getLoopEvents(jobId: string, afterSeq: number, signal?: AbortSignal): Promise<EventPage> {
    const raw = await this.requestJson<unknown>({
      method: 'GET',
      path: `/loops/${encodeURIComponent(jobId)}/events?after=${afterSeq}`,
      signal,
    });
    return normalizeEventPage(raw);
  }

  /** `GET /models`: the whole pool, each row with its own `via`. */
  public async getModels(signal?: AbortSignal): Promise<{ models: ModelRecord[]; index_ok: boolean | null }> {
    const raw = await this.requestJson<unknown>({ method: 'GET', path: '/models', signal });
    return {
      models: normalizeModelList(raw),
      index_ok: isRecord(raw) && typeof raw.index_ok === 'boolean' ? raw.index_ok : null,
    };
  }

  public async searchCatalogModels(query: string, signal?: AbortSignal): Promise<ModelRecord[]> {
    const raw = await this.requestJson<unknown>({
      method: 'GET',
      path: `/models/search?q=${encodeURIComponent(query)}`,
      signal,
    });
    return isRecord(raw) && Array.isArray(raw.results)
      ? raw.results.map((m: unknown) => normalizeModel(isRecord(m) ? m : {}))
      : [];
  }

  public async getStages(profile: ProfileName, signal?: AbortSignal): Promise<StagesResponse> {
    const raw = await this.requestJson<unknown>({
      method: 'GET',
      path: `/stages?profile=${encodeURIComponent(profile)}`,
      signal,
    });
    return normalizeStages(raw);
  }

  public async getResult(jobId: string, signal?: AbortSignal): Promise<ResultRecord> {
    const raw = await this.requestJson<unknown>({
      method: 'GET',
      path: `/results/${encodeURIComponent(jobId)}`,
      signal,
    });
    return normalizeResult(isRecord(raw) ? raw : { job_id: jobId });
  }

  /** `GET /results/{id}/diff` -> `{"files": [{"path", "unified_diff"}]}`. */
  public async getResultDiff(
    jobId: string,
    signal?: AbortSignal
  ): Promise<Array<{ path: string; unified_diff: string }>> {
    const raw = await this.requestJson<unknown>({
      method: 'GET',
      path: `/results/${encodeURIComponent(jobId)}/diff`,
      signal,
    });
    const files = isRecord(raw) && Array.isArray(raw.files) ? raw.files : [];
    return files.filter(isRecord).map((f) => ({
      path: typeof f.path === 'string' ? f.path : '',
      unified_diff: typeof f.unified_diff === 'string' ? f.unified_diff : '',
    }));
  }

  public async getSystemHealth(signal?: AbortSignal): Promise<SystemHealth> {
    const raw = await this.requestJson<unknown>({ method: 'GET', path: '/system/health', signal });
    return normalizeHealth(raw);
  }

  public async getSystemLimits(signal?: AbortSignal): Promise<SystemLimits> {
    const raw = await this.requestJson<unknown>({ method: 'GET', path: '/system/limits', signal });
    return normalizeLimits(raw);
  }

  public async getPlan(planId: string, signal?: AbortSignal): Promise<PlanRecord> {
    const raw = await this.requestJson<unknown>({
      method: 'GET',
      path: `/plans/${encodeURIComponent(planId)}`,
      signal,
    });
    return normalizePlan(raw, planId);
  }

  /** `GET /results/{id}/download`: a zip of `results/<job_id>/`, saved to the viewer's disk. */
  public async downloadResultArchive(jobId: string): Promise<{ filename: string; sizeBytes: number }> {
    const path = `/results/${encodeURIComponent(jobId)}/download`;
    const endpointLabel = `GET /api${path}`;
    if (this.getMode() === 'mock') {
      throw new ApiHttpError({
        status: 503,
        code: 'mock-no-archive',
        message: 'Mock mode has no archive. Switch to LIVE API to download the engine archive.',
        endpoint: endpointLabel,
      });
    }
    let response: Response;
    try {
      response = await fetch(`${this.baseUrl}${path}`, {
        method: 'GET',
        headers: { Accept: 'application/zip, application/octet-stream' },
      });
    } catch (networkErr: any) {
      throw new ApiHttpError({
        status: 503,
        code: 'backend-unreachable',
        message: `Cannot download the archive from ${this.baseUrl}${path}: ${networkErr?.message || 'connection refused'}`,
        endpoint: endpointLabel,
      });
    }
    if (!response.ok) {
      let errPayload: unknown = null;
      try {
        errPayload = await response.json();
      } catch {
        errPayload = null;
      }
      throw errorFromEnvelope(response.status, errPayload, endpointLabel);
    }
    const blob = await response.blob();
    let filename = `${jobId}-archive.zip`;
    const contentDisp = response.headers.get('content-disposition');
    const match = contentDisp?.match(/filename="?([^";]+)"?/i);
    if (match?.[1]) filename = match[1];
    if (typeof window !== 'undefined' && typeof document !== 'undefined') {
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = filename;
      document.body.appendChild(anchor);
      anchor.click();
      document.body.removeChild(anchor);
      setTimeout(() => URL.revokeObjectURL(url), 1500);
    }
    return { filename, sizeBytes: blob.size };
  }

  // ---- Mutations (every call carries an Idempotency-Key) ---------------

  /** `POST /system/stop` `{on}` -> `{stop}`. A model call already in flight still finishes. */
  public async setGlobalStop(on: boolean, idempotencyKey: string): Promise<{ stop: boolean | null }> {
    const res = await this.requestJson<unknown>({
      method: 'POST',
      path: '/system/stop',
      body: { on },
      idempotencyKey,
    });
    return { stop: isRecord(res) && typeof res.stop === 'boolean' ? res.stop : null };
  }

  /** `POST /jobs/validate` `{job_path}` -> `{valid, problems}`. A 400 `invalid-job` is thrown with `details.problems`. */
  public async validateJob(
    payload: { job_path: string },
    idempotencyKey: string
  ): Promise<{ valid: boolean | null; problems: string[] }> {
    const res = await this.requestJson<unknown>({
      method: 'POST',
      path: '/jobs/validate',
      body: { job_path: payload.job_path },
      idempotencyKey,
    });
    const body = isRecord(res) ? res : {};
    return {
      valid: typeof body.valid === 'boolean' ? body.valid : null,
      problems: Array.isArray(body.problems) ? body.problems.map(String) : [],
    };
  }

  /** `POST /budget/validate` `{stages, paid_budget_usd}` -> `{valid, worst_case_usd, problems}`. */
  public async validateBudget(
    payload: { stages: StageSetting[]; paid_budget_usd: number },
    idempotencyKey: string
  ): Promise<{ valid: boolean | null; worst_case_usd: number | null; problems: string[] }> {
    const res = await this.requestJson<unknown>({
      method: 'POST',
      path: '/budget/validate',
      body: { stages: buildStagesPayload(payload.stages), paid_budget_usd: payload.paid_budget_usd },
      idempotencyKey,
    });
    const body = isRecord(res) ? res : {};
    return {
      valid: typeof body.valid === 'boolean' ? body.valid : null,
      worst_case_usd: typeof body.worst_case_usd === 'number' ? body.worst_case_usd : null,
      problems: Array.isArray(body.problems) ? body.problems.map(String) : [],
    };
  }

  /** `POST /jobs` -> 201 `{queued, queue_file}` plus the frozen `stage_snapshot`. */
  public async queueJob(
    payload: {
      job_path: string;
      rerun: boolean;
      stages: StageSetting[];
      stage_revision: number;
      paid_budget_usd: number;
    },
    idempotencyKey: string
  ): Promise<{
    queued: boolean | null;
    queue_file: string | null;
    stage_snapshot: { sha256: string | null; revision: number | null } | null;
  }> {
    const res = await this.requestJson<unknown>({
      method: 'POST',
      path: '/jobs',
      body: {
        job_path: payload.job_path,
        rerun: payload.rerun,
        // Only the selectable rows are overrides. A locked or inherited row (model_selectable false, model null, e.g. reviewer_a in
        // the test profile) cannot be set, and the API refuses a null model for it (bad-model), so it is left out.
        stages: buildStagesPayload(payload.stages.filter((row) => row.model_selectable === true)),
        stage_revision: payload.stage_revision,
        paid_budget_usd: payload.paid_budget_usd,
      },
      idempotencyKey,
    });
    const body = isRecord(res) ? res : {};
    return {
      queued: typeof body.queued === 'boolean' ? body.queued : null,
      queue_file: typeof body.queue_file === 'string' ? body.queue_file : null,
      stage_snapshot: isRecord(body.stage_snapshot)
        ? {
            sha256: typeof body.stage_snapshot.sha256 === 'string' ? body.stage_snapshot.sha256 : null,
            revision: typeof body.stage_snapshot.revision === 'number' ? body.stage_snapshot.revision : null,
          }
        : null,
    };
  }

  /** `POST /loops/start` `{loops}` -> 202 `{started}`. */
  public async startLoops(loops: number, idempotencyKey: string): Promise<{ started: number | null }> {
    const res = await this.requestJson<unknown>({
      method: 'POST',
      path: '/loops/start',
      body: { loops },
      idempotencyKey,
    });
    return { started: isRecord(res) && typeof res.started === 'number' ? res.started : null };
  }

  /** `POST /plans` `{name, request, files}` -> 202 `{plan_id, state: "planning"}`. Read the plan with `getPlan`. */
  public async createPlan(
    payload: { name: string; request: string; files: string[] },
    idempotencyKey: string
  ): Promise<PlanRecord> {
    const res = await this.requestJson<unknown>({
      method: 'POST',
      path: '/plans',
      body: { name: payload.name, request: payload.request, files: payload.files },
      idempotencyKey,
    });
    return normalizePlan(res, '');
  }

  /** `POST /plans/{id}/approve` `{job_sha256, approve_assumed}` -> 202 `{job_id}`. Approving also starts the job. */
  public async approvePlan(
    planId: string,
    payload: { job_sha256: string; approve_assumed: boolean },
    idempotencyKey: string
  ): Promise<{ job_id: string | null }> {
    const res = await this.requestJson<unknown>({
      method: 'POST',
      path: `/plans/${encodeURIComponent(planId)}/approve`,
      body: { job_sha256: payload.job_sha256, approve_assumed: payload.approve_assumed },
      idempotencyKey,
    });
    return { job_id: isRecord(res) && typeof res.job_id === 'string' ? res.job_id : null };
  }

  /** `PATCH /loops/{id}/stages` `{stage, change, revision}` -> `{ok, revision}`. */
  public async patchLoopStage(
    jobId: string,
    payload: { stage: StageKey; change: { effort: Effort }; revision: number },
    idempotencyKey: string
  ): Promise<{ ok: boolean | null; revision: number | null }> {
    const res = await this.requestJson<unknown>({
      method: 'PATCH',
      path: `/loops/${encodeURIComponent(jobId)}/stages`,
      body: payload,
      idempotencyKey,
    });
    const body = isRecord(res) ? res : {};
    return {
      ok: typeof body.ok === 'boolean' ? body.ok : null,
      revision: typeof body.revision === 'number' ? body.revision : null,
    };
  }

  /** `POST /stages/validate` `{profile, changes}` -> `{valid, problems}`. */
  public async validateStages(
    payload: { profile: ProfileName; changes: StageChange[] },
    idempotencyKey: string
  ): Promise<{ valid: boolean | null; problems: Array<{ code: string; stage: string; message: string }> }> {
    const res = await this.requestJson<unknown>({
      method: 'POST',
      path: '/stages/validate',
      body: payload,
      idempotencyKey,
    });
    const body = isRecord(res) ? res : {};
    return {
      valid: typeof body.valid === 'boolean' ? body.valid : null,
      problems: Array.isArray(body.problems)
        ? body.problems.filter(isRecord).map((p) => ({
            code: String(p.code ?? ''),
            stage: String(p.stage ?? ''),
            message: String(p.message ?? ''),
          }))
        : [],
    };
  }

  /** `PUT /stages` `{profile, revision, changes}` -> `{revision}`. Affects future jobs only. */
  public async putStages(
    payload: { profile: ProfileName; revision: number; changes: StageChange[] },
    idempotencyKey: string
  ): Promise<{ revision: number | null }> {
    const res = await this.requestJson<unknown>({
      method: 'PUT',
      path: '/stages',
      body: payload,
      idempotencyKey,
    });
    return { revision: isRecord(res) && typeof res.revision === 'number' ? res.revision : null };
  }

  /** `POST /models` `{model_id}` -> 201 Model (contracts unverified until probed). */
  public async addModel(modelId: string, idempotencyKey: string): Promise<ModelRecord> {
    const res = await this.requestJson<unknown>({
      method: 'POST',
      path: '/models',
      body: { model_id: modelId },
      idempotencyKey,
    });
    return normalizeModel(isRecord(res) ? res : {});
  }

  /** `DELETE /models/{id}` -> 204. */
  public async deleteModel(modelId: string, idempotencyKey: string): Promise<void> {
    await this.requestJson<void>({
      method: 'DELETE',
      path: buildModelPath(modelId),
      idempotencyKey,
    });
  }

  /**
   * `POST /models/verify` `{model_id, contract}` -> `{result, verified_at}`.
   * Fail-closed: only `pass` yields verified; `fail` yields failed; anything else is unverified.
   */
  public async verifyModelContract(
    payload: { model_id: string; contract: SemanticContract },
    idempotencyKey: string
  ): Promise<VerifyResult> {
    const res = await this.requestJson<unknown>({
      method: 'POST',
      path: '/models/verify',
      body: payload,
      idempotencyKey,
    });
    return normalizeVerifyResult(res, payload);
  }

  /** `POST /models/verify-reasoning` `{model_id, efforts}` -> `{reasoning_mode, efforts_verified}`. */
  public async verifyModelReasoning(
    payload: { model_id: string; efforts: Effort[] },
    idempotencyKey: string
  ): Promise<{ reasoning_mode: string | null; efforts_verified: string[] }> {
    const res = await this.requestJson<unknown>({
      method: 'POST',
      path: '/models/verify-reasoning',
      body: payload,
      idempotencyKey,
    });
    const body = isRecord(res) ? res : {};
    return {
      reasoning_mode: typeof body.reasoning_mode === 'string' ? body.reasoning_mode : null,
      efforts_verified: Array.isArray(body.efforts_verified) ? body.efforts_verified.map(String) : [],
    };
  }

  /** `POST /models/refresh` `{}` -> `{updated, metadata_at}`. */
  public async refreshModels(idempotencyKey: string): Promise<{ updated: number | null; metadata_at: string | null }> {
    const res = await this.requestJson<unknown>({
      method: 'POST',
      path: '/models/refresh',
      body: {},
      idempotencyKey,
    });
    const body = isRecord(res) ? res : {};
    return {
      updated: typeof body.updated === 'number' ? body.updated : null,
      metadata_at: typeof body.metadata_at === 'string' ? body.metadata_at : null,
    };
  }

  /**
   * `PUT /system/limits` `{revision, sandbox_slots?, review_usd_daily_cap?, job_review_usd?}` -> `{revision}`.
   * `revision` is required by the contract and the GET response does not supply it (C-08), so the caller must
   * not send this call without a real revision.
   */
  public async putSystemLimits(
    payload: {
      revision: number;
      sandbox_slots?: number;
      review_usd_daily_cap?: number;
      job_review_usd?: number;
    },
    idempotencyKey: string
  ): Promise<{ revision: number | null }> {
    const res = await this.requestJson<unknown>({
      method: 'PUT',
      path: '/system/limits',
      body: payload,
      idempotencyKey,
    });
    return { revision: isRecord(res) && typeof res.revision === 'number' ? res.revision : null };
  }
}

/**
 * One problem as text. The API sends `details.problems` as plain strings (POST /jobs/validate) or as objects {code, stage, message}
 * (POST /stages/validate, POST /jobs); an object is shown by its message, never as "[object Object]".
 */
export function problemText(problem: unknown): string {
  if (typeof problem === 'string') return problem;
  if (isRecord(problem)) {
    if (typeof problem.message === 'string') return problem.message;
    if (typeof problem.code === 'string') return problem.code;
  }
  return 'unreadable problem';
}

function errorFromEnvelope(
  status: number,
  body: unknown,
  endpoint: string,
  idempotencyKey?: string
): ApiHttpError {
  const errObj = isRecord(body) && isRecord(body.error) ? body.error : {};
  const details = isRecord(errObj.details) ? { ...errObj.details } : {};
  if (Array.isArray(details.problems)) {
    details.problems = details.problems.map(problemText);
  }
  return new ApiHttpError({
    status,
    code: typeof errObj.code === 'string' ? errObj.code : `http-${status}`,
    message: typeof errObj.message === 'string' ? errObj.message : `HTTP ${status} from ${endpoint}`,
    details,
    endpoint,
    idempotencyKey,
  });
}

export function createHttpTransport(baseUrl: string = DEFAULT_API_BASE_URL): SatvikApiClient {
  return new SatvikApiClient({ baseUrl, getMode: () => 'live' });
}

export type { JobRecord, JobSummary, NormalizedOverview, PlanRecord, ProfileName, StagesResponse };
