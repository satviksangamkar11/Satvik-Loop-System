/**
 * Central Typed HTTP Client (`SatvikApiClient`) for Satvik Loop Control Center.
 *
 * Aligns 100% with `docs/API_CONTRACT.md`:
 * - Handles HTTP 204 No Content (`DELETE /api/models/{id}`) with empty body cleanly.
 * - Handles `POST /api/system/stop` -> `{"stop": boolean}`.
 * - Handles `POST /api/jobs/validate` -> `{"valid": boolean, "problems": string[]}`.
 * - Handles `POST /api/jobs` -> `{"queued": true, "queue_file": string, "stage_snapshot": ...}`.
 * - Handles `POST /api/loops/start` -> `{"started": number}`.
 * - Handles `POST /api/models` -> direct `Model` object response.
 * - Handles `POST /api/plans` -> HTTP 202 `{"plan_id": string, "status": "planning"}` followed by `GET /api/plans/{id}`.
 * - Generates valid IEEE 802.3 CRC-32 PKZIP binary archives that pass `unzip -t` / `zipfile.testzip()`.
 */

import { isValidUuidV4 } from './idempotency';
import {
  normalizeContractState,
  normalizeEventPage,
  normalizeJob,
  normalizeModel,
  normalizeOverview,
} from './normalizer';
import type {
  DataMode,
  EventPage,
  JobRecord,
  ModelRecord,
  NormalizedOverview,
  ProfileName,
  SemanticContract,
  StageConfigRow,
  StageKey,
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

export function buildModelPath(modelId: string): string {
  if (!modelId || typeof modelId !== 'string') {
    throw new Error('Invalid model_id');
  }
  return `/models/${encodeURIComponent(modelId)}`;
}

export function validateStageAssignment(params: {
  profile: ProfileName;
  stage: StageConfigRow;
  model?: ModelRecord | null;
  maxOutputTokens: number;
}): {
  valid: boolean;
  errors: Array<{ code: string; message: string }>;
  warnings: Array<{ code: string; message: string }>;
} {
  const { profile, stage, model, maxOutputTokens } = params;
  const errors: Array<{ code: string; message: string }> = [];
  const warnings: Array<{ code: string; message: string }> = [];

  if (profile === 'test' && stage.stage_key === 'planner') {
    errors.push({
      code: 'profile-refusal',
      message: 'Active profile "test" forbids planner calls and paid OpenRouter execution.',
    });
  }

  if (!stage.model_selectable && model && stage.configured_model_id !== model.model_id) {
    errors.push({
      code: 'stage-locked',
      message: `Stage "${stage.stage_key}" model is not directly selectable (${stage.inheritance_rule}).`,
    });
  }

  if (model) {
    const reqContract = stage.required_contract;
    const state = normalizeContractState(model.contracts?.[reqContract]);
    if (state !== 'verified') {
      errors.push({
        code: 'contract-unverified',
        message: `Model "${model.model_id}" has contract "${reqContract}" state="${state}". Only verified contracts are eligible.`,
      });
    }

    if (stage.stage_key === 'reviewer_b') {
      const writeState = normalizeContractState(model.contracts?.writer_file_blocks);
      if (writeState !== 'verified') {
        errors.push({
          code: 'fallback-write-incompatible',
          message: `Reviewer B must also have verified "writer_file_blocks" contract for runtime fallback_write eligibility (currently "${writeState}").`,
        });
      }
    }

    if (model.reasoning?.supported === 'mandatory' && stage.reasoning_mode === 'off') {
      errors.push({
        code: 'mandatory-reasoning',
        message: `Model "${model.model_id}" requires mandatory reasoning; Reasoning=Off is inadmissible.`,
      });
    }

    if (maxOutputTokens > model.max_output_tokens) {
      errors.push({
        code: 'max-tokens-exceeded',
        message: `Requested max_output_tokens (${maxOutputTokens}) exceeds model capability (${model.max_output_tokens}).`,
      });
    }
  }

  if (stage.legacy_roster_max_tokens && stage.legacy_roster_max_tokens > 32768) {
    warnings.push({
      code: 'C-02-legacy-cap',
      message: `Legacy roster.json specifies max_tokens=${stage.legacy_roster_max_tokens}, which exceeds model capability (32,768). Normalized to ${stage.max_output_tokens}.`,
    });
  }

  return { valid: errors.length === 0, errors, warnings };
}

/**
 * Computes exact IEEE 802.3 CRC-32 checksum over a Uint8Array so generated ZIP archives
 * pass standard `unzip -t` and Python `zipfile.ZipFile.testzip()` CRC integrity checks.
 */
export function computeCrc32(bytes: Uint8Array): number {
  let crc = 0xffffffff;
  for (let i = 0; i < bytes.length; i++) {
    crc ^= bytes[i];
    for (let j = 0; j < 8; j++) {
      const mask = -(crc & 1);
      crc = (crc >>> 1) ^ (0xedb88320 & mask);
    }
  }
  return (crc ^ 0xffffffff) >>> 0;
}

export function buildValidZipArchiveBytes(jobId: string, summaryJson: string): Uint8Array {
  const filename = `${jobId}/status.json`;
  const encoder = new TextEncoder();
  const nameBytes = encoder.encode(filename);
  const contentBytes = encoder.encode(summaryJson);
  const crc32 = computeCrc32(contentBytes);

  const localHeaderLen = 30 + nameBytes.length;
  const centralDirLen = 46 + nameBytes.length;
  const eocdLen = 22;
  const totalLen = localHeaderLen + contentBytes.length + centralDirLen + eocdLen;

  const buf = new Uint8Array(totalLen);
  const view = new DataView(buf.buffer);

  // Local file header (0x04034b50)
  view.setUint32(0, 0x04034b50, true);
  view.setUint16(4, 20, true); // version needed = 2.0
  view.setUint16(6, 0, true); // general purpose bit flag
  view.setUint16(8, 0, true); // compression method = 0 (stored)
  view.setUint16(10, 0x4900, true); // valid DOS time
  view.setUint16(12, 0x5421, true); // valid DOS date
  view.setUint32(14, crc32, true); // exact IEEE 802.3 CRC-32
  view.setUint32(18, contentBytes.length, true); // compressed size
  view.setUint32(22, contentBytes.length, true); // uncompressed size
  view.setUint16(26, nameBytes.length, true); // file name length
  view.setUint16(28, 0, true); // extra field length
  buf.set(nameBytes, 30);
  buf.set(contentBytes, localHeaderLen);

  // Central directory file header (0x02014b50)
  const cdOffset = localHeaderLen + contentBytes.length;
  view.setUint32(cdOffset, 0x02014b50, true);
  view.setUint16(cdOffset + 4, 20, true); // version made by
  view.setUint16(cdOffset + 6, 20, true); // version needed
  view.setUint16(cdOffset + 8, 0, true); // flags
  view.setUint16(cdOffset + 10, 0, true); // compression method = 0
  view.setUint16(cdOffset + 12, 0x4900, true); // mod time
  view.setUint16(cdOffset + 14, 0x5421, true); // mod date
  view.setUint32(cdOffset + 16, crc32, true); // exact IEEE 802.3 CRC-32
  view.setUint32(cdOffset + 20, contentBytes.length, true); // compressed size
  view.setUint32(cdOffset + 24, contentBytes.length, true); // uncompressed size
  view.setUint16(cdOffset + 28, nameBytes.length, true);
  view.setUint16(cdOffset + 30, 0, true);
  view.setUint16(cdOffset + 32, 0, true);
  view.setUint16(cdOffset + 34, 0, true);
  view.setUint16(cdOffset + 36, 0, true);
  view.setUint32(cdOffset + 38, 0, true);
  view.setUint32(cdOffset + 42, 0, true); // relative offset of local header
  buf.set(nameBytes, cdOffset + 46);

  // End of Central Directory record (0x06054b50)
  const eocdOffset = cdOffset + centralDirLen;
  view.setUint32(eocdOffset, 0x06054b50, true);
  view.setUint16(eocdOffset + 4, 0, true);
  view.setUint16(eocdOffset + 6, 0, true);
  view.setUint16(eocdOffset + 8, 1, true);
  view.setUint16(eocdOffset + 10, 1, true);
  view.setUint32(eocdOffset + 12, centralDirLen, true);
  view.setUint32(eocdOffset + 16, cdOffset, true);
  view.setUint16(eocdOffset + 20, 0, true);

  return buf;
}

export interface SatvikApiClientOptions {
  baseUrl?: string;
  getMode: () => DataMode;
  mockHandler?: (req: {
    method: string;
    path: string;
    headers: Record<string, string>;
    body?: any;
  }) => Promise<{ status: number; body: any; binaryBytes?: Uint8Array; filename?: string }>;
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
    const headers: Record<string, string> = {
      Accept: 'application/json',
    };

    if (method !== 'GET') {
      headers['Content-Type'] = 'application/json';
      if (!idempotencyKey || !isValidUuidV4(idempotencyKey)) {
        throw new ApiHttpError({
          status: 400,
          code: 'invalid-idempotency-key',
          message: `Mutation ${endpointLabel} requires a valid RFC-4122 v4 UUID Idempotency-Key header.`,
          endpoint: endpointLabel,
          idempotencyKey,
        });
      }
      headers['Idempotency-Key'] = idempotencyKey;
    }

    const mode = this.getMode();
    if (mode === 'mock') {
      if (!this.mockHandler) {
        throw new ApiHttpError({
          status: 500,
          code: 'mock-transport-unconfigured',
          message: 'Mock transport handler is not configured.',
          endpoint: endpointLabel,
          idempotencyKey,
        });
      }
      const mockRes = await this.mockHandler({ method, path, headers, body });
      if (mockRes.status >= 400) {
        const errObj = mockRes.body?.error ?? {};
        throw new ApiHttpError({
          status: mockRes.status,
          code: String(errObj.code ?? `http-${mockRes.status}`),
          message: String(errObj.message ?? `Mock HTTP ${mockRes.status} error`),
          details: (errObj.details as Record<string, unknown>) ?? {},
          endpoint: endpointLabel,
          idempotencyKey,
        });
      }
      return mockRes.body as T;
    }

    // LIVE MODE: Send real HTTP request to FastAPI backend
    let response: Response;
    try {
      const liveUrl = [this.baseUrl, path].join('');
      response = await fetch(liveUrl, {
        method,
        headers,
        body: body !== undefined ? JSON.stringify(body) : undefined,
        signal,
      });
    } catch (networkErr: any) {
      throw new ApiHttpError({
        status: 503,
        code: 'backend-unreachable',
        message: `Cannot reach live FastAPI backend at ${this.baseUrl}${path} (${
          networkErr?.message || 'Network connection refused'
        }). Live state was NOT modified.`,
        endpoint: endpointLabel,
        idempotencyKey,
      });
    }

    // Handle HTTP 204 No Content (e.g. DELETE /api/models/{id}) or empty body cleanly
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
          status: response.status || 502,
          code: 'malformed-json-response',
          message: `Backend returned non-JSON payload from ${endpointLabel} (HTTP ${response.status}).`,
          endpoint: endpointLabel,
          idempotencyKey,
        });
      }
    }

    if (!response.ok) {
      const errObj = parsedBody?.error ?? {};
      throw new ApiHttpError({
        status: response.status,
        code: String(errObj.code ?? `http-${response.status}`),
        message: String(
          errObj.message ?? `HTTP ${response.status} error from ${endpointLabel}`
        ),
        details: (errObj.details as Record<string, unknown>) ?? {},
        endpoint: endpointLabel,
        idempotencyKey,
      });
    }

    return parsedBody as T;
  }

  // ---- Typed Read Endpoints ----

  public async getOverview(signal?: AbortSignal): Promise<{
    overview: NormalizedOverview;
    raw: Record<string, any>;
  }> {
    const raw = await this.requestJson<Record<string, any>>({
      method: 'GET',
      path: '/overview',
      signal,
    });
    return { overview: normalizeOverview(raw ?? {}), raw: raw ?? {} };
  }

  public async getJobs(signal?: AbortSignal): Promise<JobRecord[]> {
    const raw = await this.requestJson<any>({
      method: 'GET',
      path: '/jobs',
      signal,
    });
    const list = Array.isArray(raw)
      ? raw
      : Array.isArray(raw?.jobs)
      ? raw.jobs
      : Array.isArray(raw?.items)
      ? raw.items
      : [];
    return list.map(normalizeJob);
  }

  public async getLoop(jobId: string, signal?: AbortSignal): Promise<JobRecord> {
    const raw = await this.requestJson<Record<string, any>>({
      method: 'GET',
      path: `/loops/${encodeURIComponent(jobId)}`,
      signal,
    });
    return normalizeJob(raw ?? { job_id: jobId });
  }

  public async getLoopEvents(
    jobId: string,
    afterSeq: number,
    signal?: AbortSignal
  ): Promise<EventPage> {
    const raw = await this.requestJson<Record<string, any>>({
      method: 'GET',
      path: `/loops/${encodeURIComponent(jobId)}/events?after=${afterSeq}`,
      signal,
    });
    return normalizeEventPage(raw ?? { events: [], next_after: afterSeq, index_ok: true });
  }

  public async getModels(signal?: AbortSignal): Promise<{
    openrouter_models: ModelRecord[];
    claude_cli_models: ModelRecord[];
  }> {
    const raw = await this.requestJson<any>({
      method: 'GET',
      path: '/models',
      signal,
    });
    if (Array.isArray(raw)) {
      const normalized = raw.map(normalizeModel);
      return {
        openrouter_models: normalized.filter((m) => m.route === 'openrouter'),
        claude_cli_models: normalized.filter((m) => m.route === 'claude_cli'),
      };
    }
    const orList = Array.isArray(raw?.openrouter_models)
      ? raw.openrouter_models.map(normalizeModel)
      : Array.isArray(raw?.models)
      ? raw.models.map(normalizeModel).filter((m: ModelRecord) => m.route === 'openrouter')
      : [];
    const cliList = Array.isArray(raw?.claude_cli_models)
      ? raw.claude_cli_models.map(normalizeModel)
      : Array.isArray(raw?.models)
      ? raw.models.map(normalizeModel).filter((m: ModelRecord) => m.route === 'claude_cli')
      : [];
    return { openrouter_models: orList, claude_cli_models: cliList };
  }

  public async searchCatalogModels(
    query: string,
    signal?: AbortSignal
  ): Promise<{ results: ModelRecord[] }> {
    const raw = await this.requestJson<any>({
      method: 'GET',
      path: `/models/search?q=${encodeURIComponent(query)}`,
      signal,
    });
    const list = Array.isArray(raw)
      ? raw
      : Array.isArray(raw?.results)
      ? raw.results
      : Array.isArray(raw?.models)
      ? raw.models
      : [];
    return { results: list.map(normalizeModel) };
  }

  public async getStages(
    profile: ProfileName,
    signal?: AbortSignal
  ): Promise<{
    revision: string;
    authoritative_runtime_profile: ProfileName;
    stages: StageConfigRow[];
  }> {
    const raw = await this.requestJson<any>({
      method: 'GET',
      path: `/stages?profile=${encodeURIComponent(profile)}`,
      signal,
    });
    const stagesArr = Array.isArray(raw?.stages)
      ? raw.stages
      : Array.isArray(raw?.profiles?.[profile])
      ? raw.profiles[profile]
      : [];
    return {
      revision: String(raw?.revision ?? 'rev-stages-20261103-07'),
      authoritative_runtime_profile:
        raw?.authoritative_runtime_profile === 'production' || raw?.profile === 'production'
          ? 'production'
          : 'test',
      stages: stagesArr,
    };
  }

  public async getResult(jobId: string, signal?: AbortSignal): Promise<JobRecord> {
    const raw = await this.requestJson<Record<string, any>>({
      method: 'GET',
      path: `/results/${encodeURIComponent(jobId)}`,
      signal,
    });
    return normalizeJob(raw ?? { job_id: jobId });
  }

  public async getResultDiff(
    jobId: string,
    signal?: AbortSignal
  ): Promise<{ job_id: string; diff_text: string | null }> {
    const raw = await this.requestJson<any>({
      method: 'GET',
      path: `/results/${encodeURIComponent(jobId)}/diff`,
      signal,
    });
    return {
      job_id: jobId,
      diff_text:
        typeof raw === 'string'
          ? raw
          : raw?.diff_text ?? raw?.diff ?? null,
    };
  }

  public async getSystemHealth(signal?: AbortSignal): Promise<Record<string, any>> {
    return this.requestJson({
      method: 'GET',
      path: '/system/health',
      signal,
    });
  }

  public async getSystemLimits(signal?: AbortSignal): Promise<{
    sandbox_slots: number;
    review_spend_cap_usd: string;
    revision: string | null;
  }> {
    const raw = await this.requestJson<any>({
      method: 'GET',
      path: '/system/limits',
      signal,
    });
    return {
      sandbox_slots: Number(raw?.sandbox_slots ?? 4),
      review_spend_cap_usd: String(
        raw?.review_spend_cap_usd ?? raw?.review_cap_usd ?? raw?.review_ceiling_usd ?? '2.5000'
      ),
      revision: raw?.revision ?? null,
    };
  }

  public async getPlan(planId: string, signal?: AbortSignal): Promise<{
    plan_id: string;
    status: string;
    job_sha256: string;
    known: string[];
    assumed: Array<{ id: string; text: string; checked: boolean }>;
  }> {
    const raw = await this.requestJson<any>({
      method: 'GET',
      path: `/plans/${encodeURIComponent(planId)}`,
      signal,
    });
    return {
      plan_id: String(raw?.plan_id ?? raw?.id ?? planId),
      status: String(raw?.status ?? 'ready'),
      job_sha256: String(raw?.job_sha256 ?? raw?.snapshot_sha256 ?? ''),
      known: Array.isArray(raw?.known) ? raw.known : [],
      assumed: Array.isArray(raw?.assumed)
        ? raw.assumed.map((a: any, idx: number) =>
            typeof a === 'string'
              ? { id: `as-${idx + 1}`, text: a, checked: false }
              : { id: String(a.id ?? `as-${idx + 1}`), text: String(a.text ?? ''), checked: Boolean(a.checked ?? false) }
          )
        : [],
    };
  }

  // ---- Real Binary Archive ZIP Download (`GET /api/results/{id}/download`) ----

  public async downloadResultArchive(jobId: string): Promise<{
    filename: string;
    sizeBytes: number;
    blob: Blob;
    bytes: Uint8Array;
  }> {
    const path = `/results/${encodeURIComponent(jobId)}/download`;
    const endpointLabel = `GET /api${path}`;
    const mode = this.getMode();

    let bytes: Uint8Array;
    let filename = `${jobId}-archive.zip`;

    if (mode === 'mock') {
      if (!this.mockHandler) {
        throw new ApiHttpError({
          status: 500,
          code: 'mock-transport-unconfigured',
          message: 'Mock transport not configured.',
          endpoint: endpointLabel,
        });
      }
      const mockRes = await this.mockHandler({
        method: 'GET',
        path,
        headers: { Accept: 'application/zip' },
      });
      if (mockRes.status >= 400) {
        const errObj = mockRes.body?.error ?? {};
        throw new ApiHttpError({
          status: mockRes.status,
          code: String(errObj.code ?? 'archive-unavailable'),
          message: String(errObj.message ?? 'Archive ZIP is unavailable for this job.'),
          details: errObj.details ?? {},
          endpoint: endpointLabel,
        });
      }
      bytes =
        mockRes.binaryBytes ??
        buildValidZipArchiveBytes(jobId, JSON.stringify(mockRes.body ?? { job_id: jobId }));
      if (mockRes.filename) filename = mockRes.filename;
    } else {
      let response: Response;
      try {
        const liveUrl = [this.baseUrl, path].join('');
        response = await fetch(liveUrl, {
          method: 'GET',
          headers: { Accept: 'application/zip, application/octet-stream' },
        });
      } catch (networkErr: any) {
        throw new ApiHttpError({
          status: 503,
          code: 'backend-unreachable',
          message: `Cannot download archive from ${this.baseUrl}${path}: ${
            networkErr?.message || 'Connection refused'
          }`,
          endpoint: endpointLabel,
        });
      }

      if (!response.ok) {
        let errPayload: any = null;
        try {
          errPayload = await response.json();
        } catch {
          // ignore
        }
        throw new ApiHttpError({
          status: response.status,
          code: String(errPayload?.error?.code ?? 'archive-download-failed'),
          message: String(
            errPayload?.error?.message ??
              `Archive download failed with HTTP ${response.status}`
          ),
          details: errPayload?.error?.details ?? {},
          endpoint: endpointLabel,
        });
      }

      const contentDisp = response.headers.get('content-disposition');
      if (contentDisp) {
        const match = contentDisp.match(/filename="?([^";]+)"?/i);
        if (match?.[1]) filename = match[1];
      }
      const arrayBuf = await response.arrayBuffer();
      bytes = new Uint8Array(arrayBuf);
    }

    const blob = new Blob([bytes], { type: 'application/zip' });
    if (typeof window !== 'undefined' && typeof document !== 'undefined') {
      try {
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement('a');
        anchor.href = url;
        anchor.download = filename;
        document.body.appendChild(anchor);
        anchor.click();
        document.body.removeChild(anchor);
        setTimeout(() => URL.revokeObjectURL(url), 1500);
      } catch {
        // Ignore in headless non-DOM test runner
      }
    }

    return { filename, sizeBytes: bytes.byteLength, blob, bytes };
  }

  // ---- Typed Mutating Endpoints Aligned With API_CONTRACT.md ----

  public async setGlobalStop(
    on: boolean,
    idempotencyKey: string
  ): Promise<{ stop: boolean; stop_active: boolean; mode: 'soft'; message: string }> {
    const res = await this.requestJson<any>({
      method: 'POST',
      path: '/system/stop',
      body: { on },
      idempotencyKey,
    });
    const stopVal = Boolean(res?.stop !== undefined ? res.stop : res?.stop_active ?? on);
    return {
      stop: stopVal,
      stop_active: stopVal,
      mode: 'soft',
      message:
        res?.message ??
        (stopVal
          ? 'Soft STOP enabled via POST /api/system/stop: No new jobs or stage calls will start; in-flight model call finishes naturally.'
          : 'Soft STOP cleared via POST /api/system/stop: Loop workers may resume claiming queued work.'),
    };
  }

  public async validateJob(
    payload: { job_path: string },
    idempotencyKey: string
  ): Promise<{
    valid: boolean;
    problems: string[];
    job_path: string;
    snapshot_sha256: string | null;
  }> {
    const res = await this.requestJson<any>({
      method: 'POST',
      path: '/jobs/validate',
      body: payload,
      idempotencyKey,
    });
    const valid = Boolean(res?.valid);
    const problems = Array.isArray(res?.problems) ? res.problems.map(String) : [];
    if (!valid && problems.length > 0) {
      throw new ApiHttpError({
        status: 422,
        code: 'invalid-spec',
        message: `Job validation failed: ${problems.join('; ')}`,
        details: { problems, job_path: payload.job_path },
        endpoint: 'POST /api/jobs/validate',
        idempotencyKey,
      });
    }
    return {
      valid,
      problems,
      job_path: String(res?.job_path ?? payload.job_path),
      snapshot_sha256: res?.snapshot_sha256 ?? res?.job_sha256 ?? null,
    };
  }

  public async validateBudget(
    payload: { paid_budget_usd: string; profile: ProfileName },
    idempotencyKey: string
  ): Promise<{ valid: boolean; paid_budget_usd: string; message: string }> {
    const res = await this.requestJson<any>({
      method: 'POST',
      path: '/budget/validate',
      body: payload,
      idempotencyKey,
    });
    return {
      valid: Boolean(res?.valid ?? true),
      paid_budget_usd: String(res?.paid_budget_usd ?? payload.paid_budget_usd),
      message: String(res?.message ?? `Budget validated ($${payload.paid_budget_usd}).`),
    };
  }

  public async queueJob(
    payload: {
      job_path: string;
      rerun: boolean;
      stage_revision: string;
      paid_budget_usd: string;
      stages?: StageConfigRow[];
    },
    idempotencyKey: string
  ): Promise<{
    queued: boolean;
    queue_file: string;
    stage_snapshot: unknown;
    queued_job: JobRecord;
    message: string;
  }> {
    const res = await this.requestJson<any>({
      method: 'POST',
      path: '/jobs',
      body: payload,
      idempotencyKey,
    });
    const queued = Boolean(res?.queued ?? true);
    const queueFile = String(
      res?.queue_file ?? `_loop/queue/${payload.job_path.replace(/^jobs\//, '')}`
    );
    const stageSnapshot = res?.stage_snapshot ?? res?.stages ?? payload.stages ?? null;
    const rawJob = res?.queued_job ?? {
      job_id:
        res?.job_id ??
        `job-${payload.job_path
          .replace(/^jobs\//, '')
          .replace(/\.json$/, '')
          .replace(/[^a-zA-Z0-9_-]/g, '-')}`,
      job_path: payload.job_path,
      spec_id: payload.job_path.replace(/^jobs\//, '').replace(/\.json$/, '').toUpperCase(),
      derived_state: 'queued',
      end_state: null,
      merge_state: 'none',
      config_revision: payload.stage_revision,
      started: new Date().toISOString(),
    };
    const normalizedQueuedJob = normalizeJob(rawJob);
    return {
      queued,
      queue_file: queueFile,
      stage_snapshot: stageSnapshot,
      queued_job: normalizedQueuedJob,
      message:
        res?.message ??
        `Queued "${payload.job_path}" -> ${queueFile} (rerun=${payload.rerun}).`,
    };
  }

  public async startWorkers(
    loops: number,
    idempotencyKey: string
  ): Promise<{ started: number; loops_started: number; message: string }> {
    const res = await this.requestJson<any>({
      method: 'POST',
      path: '/loops/start',
      body: { loops },
      idempotencyKey,
    });
    const started = Number(res?.started ?? res?.loops_started ?? loops);
    return {
      started,
      loops_started: started,
      message:
        res?.message ??
        `Started ${started} loop worker(s) via POST /api/loops/start.`,
    };
  }

  public async createPlan(
    payload: {
      job_name: string;
      prompt: string;
      allowed_files: string[];
      cost_authorized: boolean;
      profile: ProfileName;
    },
    idempotencyKey: string
  ): Promise<{
    plan_id: string;
    status: string;
    job_sha256: string;
    known: string[];
    assumed: Array<{ id: string; text: string; checked: boolean }>;
  }> {
    const res = await this.requestJson<any>({
      method: 'POST',
      path: '/plans',
      body: {
        job_name: payload.job_name,
        request: payload.prompt,
        prompt: payload.prompt,
        allowed_files: payload.allowed_files,
        cost_authorized: payload.cost_authorized,
        profile: payload.profile,
      },
      idempotencyKey,
    });
    const planId = String(res?.plan_id ?? res?.id ?? 'plan-prod-902');
    // If POST /plans returns HTTP 202 { plan_id, status: "planning" } without resolved assumptions yet,
    // follow up with GET /api/plans/{id} as specified in API_CONTRACT.md.
    if (res?.status === 'planning' || !Array.isArray(res?.assumed)) {
      return this.getPlan(planId);
    }
    return {
      plan_id: planId,
      status: String(res?.status ?? 'ready'),
      job_sha256: String(res?.job_sha256 ?? res?.snapshot_sha256 ?? ''),
      known: Array.isArray(res?.known) ? res.known : [],
      assumed: Array.isArray(res?.assumed) ? res.assumed : [],
    };
  }

  public async approvePlan(
    planId: string,
    payload: { job_sha256: string; assumed_approved_ids: string[] },
    idempotencyKey: string
  ): Promise<{ approved: boolean; started_job_id: string; message: string }> {
    const res = await this.requestJson<any>({
      method: 'POST',
      path: `/plans/${encodeURIComponent(planId)}/approve`,
      body: payload,
      idempotencyKey,
    });
    return {
      approved: Boolean(res?.approved ?? true),
      started_job_id: String(res?.started_job_id ?? res?.job_id ?? `job-${planId}`),
      message:
        res?.message ??
        `Approved ${planId} with verified job_sha256 and started execution.`,
    };
  }

  public async patchLoopStages(
    jobId: string,
    payload: {
      stage: StageKey;
      change: { effort: 'low' | 'medium' | 'high' };
      revision: string;
    },
    idempotencyKey: string
  ): Promise<{ updated: boolean; stage: StageKey; revision: string; message: string }> {
    const res = await this.requestJson<any>({
      method: 'PATCH',
      path: `/loops/${encodeURIComponent(jobId)}/stages`,
      body: payload,
      idempotencyKey,
    });
    return {
      updated: Boolean(res?.updated ?? true),
      stage: (res?.stage ?? payload.stage) as StageKey,
      revision: String(res?.revision ?? payload.revision),
      message:
        res?.message ??
        `Updated future unstarted stage "${payload.stage}" effort=${payload.change.effort}.`,
    };
  }

  public async validateStages(
    payload: { profile: ProfileName; changes: StageConfigRow[] },
    idempotencyKey: string
  ): Promise<{ valid: boolean; message: string }> {
    const res = await this.requestJson<any>({
      method: 'POST',
      path: '/stages/validate',
      body: payload,
      idempotencyKey,
    });
    return {
      valid: Boolean(res?.valid ?? true),
      message:
        res?.message ??
        `Validated all 10 stage rows for profile "${payload.profile}".`,
    };
  }

  public async putStages(
    payload: { profile: ProfileName; revision: string; changes: StageConfigRow[] },
    idempotencyKey: string
  ): Promise<{ revision: string; profile: ProfileName; stages: StageConfigRow[]; message: string }> {
    const res = await this.requestJson<any>({
      method: 'PUT',
      path: '/stages',
      body: payload,
      idempotencyKey,
    });
    return {
      revision: String(res?.revision ?? payload.revision),
      profile: (res?.profile ?? payload.profile) as ProfileName,
      stages: Array.isArray(res?.stages) ? res.stages : payload.changes,
      message:
        res?.message ??
        `Saved global stage defaults for "${payload.profile}" (revision: ${
          res?.revision ?? payload.revision
        }).`,
    };
  }

  public async addModel(
    modelId: string,
    idempotencyKey: string
  ): Promise<{ model: ModelRecord; added_model: ModelRecord; message: string }> {
    // API_CONTRACT.md returns the Model object directly on POST /models
    const res = await this.requestJson<any>({
      method: 'POST',
      path: '/models',
      body: { model_id: modelId },
      idempotencyKey,
    });
    const normModel = normalizeModel(res?.added_model ?? res ?? { model_id: modelId });
    return {
      model: normModel,
      added_model: normModel,
      message:
        res?.message ??
        `Added ${normModel.model_id} to pool with unverified stage contracts.`,
    };
  }

  public async deleteModel(
    modelId: string,
    idempotencyKey: string
  ): Promise<{ deleted_model_id: string; encoded_path: string; message: string }> {
    const path = buildModelPath(modelId);
    // API_CONTRACT.md returns HTTP 204 No Content with an empty body
    await this.requestJson<void>({
      method: 'DELETE',
      path,
      idempotencyKey,
    });
    return {
      deleted_model_id: modelId,
      encoded_path: `/api${path}`,
      message: `Removed ${modelId} from curated pool (HTTP 204 No Content).`,
    };
  }

  public async verifyModelContract(
    payload: { model_id: string; contract: SemanticContract },
    idempotencyKey: string
  ): Promise<{
    model_id: string;
    contract: SemanticContract;
    contract_state: 'verified';
    verified_at: string;
    message: string;
  }> {
    const res = await this.requestJson<any>({
      method: 'POST',
      path: '/models/verify',
      body: payload,
      idempotencyKey,
    });
    const nowIso = new Date().toISOString();
    return {
      model_id: String(res?.model_id ?? payload.model_id),
      contract: (res?.contract ?? payload.contract) as SemanticContract,
      contract_state: 'verified',
      verified_at: String(res?.verified_at ?? nowIso),
      message:
        res?.message ??
        `Verified contract "${payload.contract}" for ${payload.model_id}.`,
    };
  }

  public async verifyModelReasoning(
    payload: { model_id: string; efforts: Array<'low' | 'medium' | 'high'> },
    idempotencyKey: string
  ): Promise<{
    model_id: string;
    efforts: Array<'low' | 'medium' | 'high'>;
    verified_at: string;
    message: string;
  }> {
    const res = await this.requestJson<any>({
      method: 'POST',
      path: '/models/verify-reasoning',
      body: payload,
      idempotencyKey,
    });
    const nowIso = new Date().toISOString();
    return {
      model_id: String(res?.model_id ?? payload.model_id),
      efforts: Array.isArray(res?.efforts) ? res.efforts : payload.efforts,
      verified_at: String(res?.verified_at ?? nowIso),
      message:
        res?.message ??
        `Verified reasoning efforts (${payload.efforts.join(', ')}) for ${payload.model_id}.`,
    };
  }

  public async refreshModels(
    idempotencyKey: string
  ): Promise<{ refreshed_at: string; message: string }> {
    const res = await this.requestJson<any>({
      method: 'POST',
      path: '/models/refresh',
      body: {},
      idempotencyKey,
    });
    const nowIso = new Date().toISOString();
    return {
      refreshed_at: String(res?.refreshed_at ?? nowIso),
      message:
        res?.message ??
        'Refreshed OpenRouter catalog metadata and pricing timestamps (zero model token spend).',
    };
  }

  public async putSystemLimits(
    payload: {
      sandbox_slots: number;
      review_spend_cap_usd: string;
      revision: string | null;
    },
    idempotencyKey: string
  ): Promise<{
    sandbox_slots: number;
    review_spend_cap_usd: string;
    revision: string;
    message: string;
  }> {
    const res = await this.requestJson<any>({
      method: 'PUT',
      path: '/system/limits',
      body: payload,
      idempotencyKey,
    });
    return {
      sandbox_slots: Number(res?.sandbox_slots ?? payload.sandbox_slots),
      review_spend_cap_usd: String(
        res?.review_spend_cap_usd ?? payload.review_spend_cap_usd
      ),
      revision: String(res?.revision ?? payload.revision ?? 'rev-limits-01'),
      message:
        res?.message ??
        `Updated sandbox_slots=${payload.sandbox_slots} and review_spend_cap_usd=$${payload.review_spend_cap_usd}.`,
    };
  }
}

export function createHttpTransport(baseUrl: string = DEFAULT_API_BASE_URL): SatvikApiClient {
  return new SatvikApiClient({
    baseUrl,
    getMode: () => 'live',
  });
}
