/**
 * Exact normalization of the API_CONTRACT.md read models (`docs/API_CONTRACT.md`, sections 1-2).
 *
 * Rules:
 * - Only contract field names are read. No alternative shapes, no demo defaults, no invented limits.
 * - A value that is absent, or outside its closed set, becomes `null` (or `[]` for a list). The UI shows "Unknown".
 * - Contract state is fail-closed: anything that is not one of the four states is `unverified`.
 */

import type {
  AttemptStatus,
  ContractState,
  DerivedJobState,
  Effort,
  EndState,
  EventPage,
  JobDetailExtras,
  JobRecord,
  JobSummary,
  LoopEvent,
  MergeState,
  ModelCapabilities,
  ModelRecord,
  NormalizedOverview,
  PlanRecord,
  ProfileName,
  ReasoningMode,
  ResultRecord,
  SemanticContract,
  StageKey,
  StageSetting,
  StageStatus,
  StagesResponse,
  SystemHealth,
  SystemLimits,
  Via,
  VerifyResult,
  WaitWhy,
} from './types';

export const CONFIGURABLE_STAGE_KEYS: StageKey[] = [
  'planner',
  'writer_a_write',
  'writer_a_repair',
  'writer_b_write',
  'writer_b_repair',
  'reviewer_b',
  'reviewer_a',
  'reviewer_reask',
  'specialist_repair',
  'review_fix_repair',
];

export const SEMANTIC_CONTRACTS: SemanticContract[] = [
  'planner_json',
  'writer_file_blocks',
  'repair_edit_blocks',
  'repair_whole_file',
  'reviewer_verdict',
  'native_structured_output',
];

const DERIVED_STATES: DerivedJobState[] = [
  'queued',
  'running',
  'waiting',
  'waiting-unknown',
  'stale-warning',
  'orphan',
  'finished',
  'merged',
];
const END_STATES: EndState[] = [
  'passed',
  'review-open',
  'escalate',
  'needs_review',
  'blocked',
  'provider-unavailable',
  'budget-exceeded',
  'quota-exhausted',
  'deadline',
  'integrity-mismatch',
  'acceptance-tampered',
  'exception',
  'invalid-spec',
  'job-too-large',
  'sandbox-unavailable',
  'acceptance-invalid',
  'refused',
  'stopped',
];
const MERGE_STATES: MergeState[] = [
  'merged',
  'merge-conflict',
  'tests-failed',
  'push-failed',
  'main-moved',
  'pending',
  'none',
];
const WAIT_WHYS: WaitWhy[] = ['model-slot', 'account-pace', '429-backoff', 'retry-backoff'];
const STAGE_STATUSES: StageStatus[] = ['ran', 'running', 'not-triggered', 'pending'];
const ATTEMPT_STATUSES: AttemptStatus[] = ['running', 'ok', 'failed', 'rejected'];
const EFFORTS_ALL: Effort[] = ['off', 'low', 'medium', 'high', 'xhigh', 'max'];
const REASONING_MODES: ReasoningMode[] = ['optional', 'mandatory', 'unsupported', 'unknown'];

// ---------------------------------------------------------------------------
// Scalar helpers (absent or wrong type -> null)
// ---------------------------------------------------------------------------

export function isRecord(v: unknown): v is Record<string, any> {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}

export function numOrNull(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null;
}

export function strOrNull(v: unknown): string | null {
  return typeof v === 'string' ? v : null;
}

export function boolOrNull(v: unknown): boolean | null {
  return typeof v === 'boolean' ? v : null;
}

function oneOfOrNull<T extends string>(v: unknown, allowed: readonly T[]): T | null {
  return typeof v === 'string' && (allowed as readonly string[]).includes(v) ? (v as T) : null;
}

function listOf<T>(v: unknown, mapFn: (item: any) => T): T[] {
  return Array.isArray(v) ? v.map(mapFn) : [];
}

// ---------------------------------------------------------------------------
// Contract state (fail-closed)
// ---------------------------------------------------------------------------

export function normalizeContractState(raw: unknown): ContractState {
  if (
    raw === 'verified' ||
    raw === 'failed' ||
    raw === 'unverified' ||
    raw === 'not-applicable'
  ) {
    return raw;
  }
  return 'unverified';
}

/**
 * Interprets the POST /models/verify response.
 * Only `result: "pass"` marks a contract verified. `"fail"` marks it failed.
 * A missing or malformed result is unverified.
 */
export function normalizeVerifyResult(
  raw: unknown,
  requested: { model_id: string; contract: SemanticContract }
): VerifyResult {
  const body = isRecord(raw) ? raw : {};
  let state: VerifyResult['state'];
  if (body.result === 'pass') {
    state = 'verified';
  } else if (body.result === 'fail') {
    state = 'failed';
  } else {
    state = 'unverified';
  }
  return {
    model_id: requested.model_id,
    contract: requested.contract,
    state,
    verified_at: state === 'unverified' ? null : strOrNull(body.verified_at),
  };
}

// ---------------------------------------------------------------------------
// Events
// ---------------------------------------------------------------------------

export function normalizeEvent(raw: Record<string, any>): LoopEvent {
  return {
    seq: numOrNull(raw.seq) ?? 0,
    ts: strOrNull(raw.ts),
    type: strOrNull(raw.type) ?? 'unknown',
    attempt_id: strOrNull(raw.attempt_id),
    stage: strOrNull(raw.stage),
    role: strOrNull(raw.role),
    mode: strOrNull(raw.mode),
    model: strOrNull(raw.model),
    via: oneOfOrNull<Via>(raw.via, ['openrouter', 'claude_code']),
    effort: oneOfOrNull<Effort>(raw.effort, EFFORTS_ALL),
    status: oneOfOrNull<AttemptStatus>(raw.status, ATTEMPT_STATUSES),
    prompt_tokens: numOrNull(raw.prompt_tokens),
    completion_tokens: numOrNull(raw.completion_tokens),
    reasoning_tokens: numOrNull(raw.reasoning_tokens),
    finish_reason: strOrNull(raw.finish_reason),
    seconds: numOrNull(raw.seconds),
    error: strOrNull(raw.error),
    why: oneOfOrNull<WaitWhy>(raw.why, WAIT_WHYS),
    green: boolOrNull(raw.green),
    failing: Array.isArray(raw.failing) ? raw.failing : null,
    reviewer: strOrNull(raw.reviewer),
    verdict: strOrNull(raw.verdict),
    evidence_ok: boolOrNull(raw.evidence_ok),
    end_state: oneOfOrNull<EndState>(raw.end_state, END_STATES),
    reason: strOrNull(raw.reason),
    merge_state: oneOfOrNull<MergeState>(raw.merge_state, MERGE_STATES),
    head: strOrNull(raw.head),
    attempts: numOrNull(raw.attempts),
  };
}

export function normalizeEventPage(raw: unknown): EventPage {
  const body = isRecord(raw) ? raw : {};
  return {
    events: listOf(body.events, (e) => normalizeEvent(isRecord(e) ? e : {})),
    next_after: numOrNull(body.next_after),
    index_ok: boolOrNull(body.index_ok),
  };
}

/** Merges event pages by `seq`, keeping every recorded event once and in order. */
export class EventStreamBuffer {
  public jobId: string;
  private seen: Set<number> = new Set();
  public events: LoopEvent[] = [];
  public nextAfter = 0;
  public indexOk: boolean | null = null;

  constructor(jobId: string) {
    this.jobId = jobId;
  }

  resetForJob(jobId: string): void {
    if (this.jobId === jobId) return;
    this.jobId = jobId;
    this.seen.clear();
    this.events = [];
    this.nextAfter = 0;
    this.indexOk = null;
  }

  appendPage(page: EventPage): LoopEvent[] {
    this.indexOk = page.index_ok;
    for (const ev of page.events) {
      if (!this.seen.has(ev.seq)) {
        this.seen.add(ev.seq);
        this.events.push(ev);
      }
    }
    this.events.sort((a, b) => a.seq - b.seq);
    if (page.next_after !== null && page.next_after > this.nextAfter) {
      this.nextAfter = page.next_after;
    }
    return [...this.events];
  }
}

// ---------------------------------------------------------------------------
// Jobs
// ---------------------------------------------------------------------------

function normalizeWaiting(raw: unknown): JobSummary['waiting'] {
  if (!isRecord(raw)) return null;
  return {
    why: oneOfOrNull<WaitWhy>(raw.why, WAIT_WHYS),
    since: strOrNull(raw.since),
    model: strOrNull(raw.model),
  };
}

/** A `GET /jobs` row (JobSummary). */
export function normalizeJobSummary(raw: Record<string, any>): JobSummary {
  return {
    job_id: strOrNull(raw.job_id) ?? '',
    name: strOrNull(raw.name),
    derived_state: oneOfOrNull<DerivedJobState>(raw.derived_state, DERIVED_STATES),
    end_state: oneOfOrNull<EndState>(raw.end_state, END_STATES),
    merge_state: oneOfOrNull<MergeState>(raw.merge_state, MERGE_STATES),
    active_stage: oneOfOrNull<StageKey>(raw.active_stage, CONFIGURABLE_STAGE_KEYS),
    active_model: strOrNull(raw.active_model),
    waiting: normalizeWaiting(raw.waiting),
    heartbeat_age_s: numOrNull(raw.heartbeat_age_s),
    started: strOrNull(raw.started),
    finished: strOrNull(raw.finished),
    free_requests_used: numOrNull(raw.free_requests_used),
    review_usd_spent: numOrNull(raw.review_usd_spent),
  };
}

/** A `GET /loops/{id}` response (JobDetail): the summary plus the detail fields. */
export function normalizeJobDetail(raw: Record<string, any>): JobRecord {
  const extras: JobDetailExtras = {
    profile: oneOfOrNull<ProfileName>(raw.profile, ['test', 'production']),
    spec: strOrNull(raw.spec),
    files: listOf(raw.files, (f) => String(f)),
    reason: strOrNull(raw.reason),
    tests_passed: boolOrNull(raw.tests_passed),
    failing_tests: Array.isArray(raw.failing_tests) ? raw.failing_tests : [],
    review_verdict: strOrNull(raw.review_verdict),
    findings: listOf(raw.findings, (f) => String(f)),
    stage_snapshot: isRecord(raw.stage_snapshot)
      ? {
          sha256: strOrNull(raw.stage_snapshot.sha256),
          revision: numOrNull(raw.stage_snapshot.revision),
        }
      : null,
    stages: listOf(raw.stages, (row) => {
      const r = isRecord(row) ? row : {};
      return {
        stage: oneOfOrNull<StageKey>(r.stage, CONFIGURABLE_STAGE_KEYS),
        status: oneOfOrNull<StageStatus>(r.status, STAGE_STATUSES),
        attempts: numOrNull(r.attempts),
      };
    }).filter((row): row is { stage: StageKey; status: StageStatus | null; attempts: number | null } => row.stage !== null),
    lease: isRecord(raw.lease)
      ? {
          held: boolOrNull(raw.lease.held),
          pid: numOrNull(raw.lease.pid),
          pid_alive: boolOrNull(raw.lease.pid_alive),
          heartbeat: strOrNull(raw.lease.heartbeat),
          generation: numOrNull(raw.lease.generation),
        }
      : null,
    budget: isRecord(raw.budget)
      ? {
          free_requests_used: numOrNull(raw.budget.free_requests_used),
          free_requests: numOrNull(raw.budget.free_requests),
          review_usd_spent: numOrNull(raw.budget.review_usd_spent),
          review_usd: numOrNull(raw.budget.review_usd),
          repair_calls: numOrNull(raw.budget.repair_calls),
        }
      : null,
    hashes: isRecord(raw.hashes)
      ? {
          tested: strOrNull(raw.hashes.tested),
          reviewed: strOrNull(raw.hashes.reviewed),
          delivered: strOrNull(raw.hashes.delivered),
        }
      : null,
    archive: isRecord(raw.archive)
      ? {
          available: boolOrNull(raw.archive.available),
          archive_failed: boolOrNull(raw.archive.archive_failed),
        }
      : null,
    merge: isRecord(raw.merge)
      ? {
          merge_state: oneOfOrNull<MergeState>(raw.merge.merge_state, MERGE_STATES),
          head: strOrNull(raw.merge.head),
          attempts: numOrNull(raw.merge.attempts),
          note: strOrNull(raw.merge.note),
        }
      : null,
    note: strOrNull(raw.note),
  };
  return { ...normalizeJobSummary(raw), ...extras };
}

// ---------------------------------------------------------------------------
// Overview
// ---------------------------------------------------------------------------

export function normalizeOverview(raw: Record<string, any>): NormalizedOverview {
  const free = isRecord(raw.free_today) ? raw.free_today : {};
  const pace = isRecord(raw.pace) ? raw.pace : {};
  const review = isRecord(raw.review_usd_today) ? raw.review_usd_today : {};
  const sandbox = isRecord(raw.sandbox_slots) ? raw.sandbox_slots : {};
  return {
    loops_running: numOrNull(raw.loops_running),
    queue_depth: numOrNull(raw.queue_depth),
    jobs_waiting: numOrNull(raw.jobs_waiting),
    active_errors: numOrNull(raw.active_errors),
    stop: boolOrNull(raw.stop),
    free_today: {
      used: numOrNull(free.used),
      cap: numOrNull(free.cap),
      cap_source: strOrNull(free.cap_source),
    },
    pace: {
      sent_last_60s: numOrNull(pace.sent_last_60s),
      limit: numOrNull(pace.limit),
    },
    review_usd_today: {
      spent: numOrNull(review.spent),
      cap: numOrNull(review.cap),
    },
    pending_reviews: numOrNull(raw.pending_reviews),
    sandbox_slots: {
      used: numOrNull(sandbox.used),
      total: numOrNull(sandbox.total),
    },
    updated: strOrNull(raw.updated),
    index_ok: boolOrNull(raw.index_ok),
  };
}

// ---------------------------------------------------------------------------
// Results
// ---------------------------------------------------------------------------

export function normalizeResult(raw: Record<string, any>): ResultRecord {
  const tests = isRecord(raw.tests) ? raw.tests : null;
  return {
    job_id: strOrNull(raw.job_id) ?? '',
    end_state: oneOfOrNull<EndState>(raw.end_state, END_STATES),
    merge_state: oneOfOrNull<MergeState>(raw.merge_state, MERGE_STATES),
    tests: tests
      ? {
          passed: boolOrNull(tests.passed),
          failing: listOf(tests.failing, (f) => {
            const r = isRecord(f) ? f : {};
            return {
              id: strOrNull(r.id),
              message: strOrNull(r.message),
              file: strOrNull(r.file),
              line: numOrNull(r.line),
            };
          }),
        }
      : null,
    reviews: listOf(raw.reviews, (r) => {
      const rv = isRecord(r) ? r : {};
      return {
        reviewer: strOrNull(rv.reviewer),
        verdict: strOrNull(rv.verdict),
        findings: listOf(rv.findings, (f) => String(f)),
        file: strOrNull(rv.file),
      };
    }),
    diff_available: boolOrNull(raw.diff_available),
    artifacts: listOf(raw.artifacts, (a) => String(a)),
    branch: strOrNull(raw.branch),
    merge: raw.merge ?? null,
  };
}

// ---------------------------------------------------------------------------
// Models
// ---------------------------------------------------------------------------

export function normalizeModel(raw: Record<string, any>): ModelRecord {
  const cap = isRecord(raw.capabilities) ? raw.capabilities : {};
  const contracts = isRecord(cap.contracts) ? cap.contracts : {};
  const temp = isRecord(cap.temperature) ? cap.temperature : null;
  const capabilities: ModelCapabilities = {
    reasoning_on_off: boolOrNull(cap.reasoning_on_off),
    // Kept as the backend lists them, xhigh and max included. Only values in the contract's effort enum are kept.
    efforts: Array.isArray(cap.efforts)
      ? cap.efforts.filter((e: unknown): e is Effort => typeof e === 'string' && (EFFORTS_ALL as string[]).includes(e))
      : [],
    token_budget: boolOrNull(cap.token_budget),
    temperature: temp && numOrNull(temp.min) !== null && numOrNull(temp.max) !== null
      ? { min: temp.min as number, max: temp.max as number }
      : null,
    structured_outputs: boolOrNull(cap.structured_outputs),
    contracts: {
      planner_json: normalizeContractState(contracts.planner_json),
      writer_file_blocks: normalizeContractState(contracts.writer_file_blocks),
      repair_edit_blocks: normalizeContractState(contracts.repair_edit_blocks),
      repair_whole_file: normalizeContractState(contracts.repair_whole_file),
      reviewer_verdict: normalizeContractState(contracts.reviewer_verdict),
      native_structured_output: normalizeContractState(contracts.native_structured_output),
    },
    reasoning_mode: oneOfOrNull<ReasoningMode>(cap.reasoning_mode, REASONING_MODES) ?? 'unknown',
    text_validated: boolOrNull(cap.text_validated),
  };

  const via = oneOfOrNull<Via>(raw.via, ['openrouter', 'claude_code']);
  const isClaudeCode = via === 'claude_code';
  return {
    model_id: strOrNull(raw.model_id) ?? '',
    via,
    hosting_provider: strOrNull(raw.hosting_provider),
    // Claude Code is a subscription: never a paid API price, never a dollar figure.
    paid: isClaudeCode ? null : boolOrNull(raw.paid),
    price_in_usd_per_mtok: isClaudeCode ? null : numOrNull(raw.price_in_usd_per_mtok),
    price_out_usd_per_mtok: isClaudeCode ? null : numOrNull(raw.price_out_usd_per_mtok),
    context_tokens: numOrNull(raw.context_tokens),
    max_output_tokens: numOrNull(raw.max_output_tokens),
    capabilities,
    verified_at: strOrNull(raw.verified_at),
    metadata_at: strOrNull(raw.metadata_at),
    note: strOrNull(raw.note),
    endpoint: strOrNull(raw.endpoint),
  };
}

export function normalizeModelList(raw: unknown): ModelRecord[] {
  if (Array.isArray(raw)) return raw.map((m) => normalizeModel(isRecord(m) ? m : {}));
  if (isRecord(raw) && Array.isArray(raw.models)) {
    return raw.models.map((m: unknown) => normalizeModel(isRecord(m) ? m : {}));
  }
  return [];
}

// ---------------------------------------------------------------------------
// Stage settings
// ---------------------------------------------------------------------------

export function normalizeStageSetting(raw: Record<string, any>): StageSetting | null {
  const stageKey = oneOfOrNull<StageKey>(raw.stage, CONFIGURABLE_STAGE_KEYS);
  if (stageKey === null) return null;
  const settings = isRecord(raw.settings) ? raw.settings : {};
  return {
    stage_key: stageKey,
    configured_model_id: strOrNull(raw.model),
    via: oneOfOrNull<Via>(raw.via, ['openrouter', 'claude_code']),
    model_selectable: boolOrNull(raw.model_selectable),
    inherits: strOrNull(raw.inherits),
    required_contract: oneOfOrNull<SemanticContract>(raw.required_contract, SEMANTIC_CONTRACTS),
    effort: oneOfOrNull<Effort>(raw.effort, EFFORTS_ALL),
    revision: numOrNull(raw.revision),
    source: strOrNull(raw.source),
    max_output_tokens: numOrNull(settings.max_tokens),
    settings,
    note: strOrNull(raw.note),
  };
}

export function normalizeStages(raw: unknown): StagesResponse {
  const body = isRecord(raw) ? raw : {};
  return {
    profile: oneOfOrNull<ProfileName>(body.profile, ['test', 'production']),
    revision: numOrNull(body.revision),
    stages: listOf(body.stages, (s) => normalizeStageSetting(isRecord(s) ? s : {})).filter(
      (s): s is StageSetting => s !== null
    ),
  };
}

// ---------------------------------------------------------------------------
// System
// ---------------------------------------------------------------------------

export function normalizeHealth(raw: unknown): SystemHealth {
  const h = isRecord(raw) ? raw : {};
  return {
    backend: strOrNull(h.backend),
    engine_version: strOrNull(h.engine_version),
    docker: strOrNull(h.docker),
    sandbox_image: strOrNull(h.sandbox_image),
    satvik_home: strOrNull(h.satvik_home),
    index_ok: boolOrNull(h.index_ok),
    keys: listOf(h.keys, (k) => {
      const r = isRecord(k) ? k : {};
      return { alias: String(r.alias ?? ''), present: boolOrNull(r.present) };
    }),
    stop: boolOrNull(h.stop),
  };
}

/** Kept as given by GET /system/limits. Nothing is added, nothing defaulted. */
export function normalizeLimits(raw: unknown): SystemLimits {
  const l = isRecord(raw) ? raw : {};
  const ledger = isRecord(l.ledger) ? l.ledger : {};
  return {
    leases: listOf(l.leases, (x) => {
      const r = isRecord(x) ? x : {};
      return {
        job_id: String(r.job_id ?? ''),
        held: boolOrNull(r.held),
        heartbeat_age_s: numOrNull(r.heartbeat_age_s),
        pid_alive: boolOrNull(r.pid_alive),
      };
    }),
    claims: listOf(l.claims, (x) => {
      const r = isRecord(x) ? x : {};
      return { path: String(r.path ?? ''), job_id: String(r.job_id ?? '') };
    }),
    gates: listOf(l.gates, (x) => {
      const r = isRecord(x) ? x : {};
      return {
        gate: String(r.gate ?? ''),
        holders: numOrNull(r.holders),
        capacity: numOrNull(r.capacity),
      };
    }),
    ledger: {
      free_used: numOrNull(ledger.free_used),
      free_cap: numOrNull(ledger.free_cap),
      review_spent_usd: numOrNull(ledger.review_spent_usd),
    },
  };
}

// ---------------------------------------------------------------------------
// Plans
// ---------------------------------------------------------------------------

export function normalizePlan(raw: unknown, fallbackPlanId: string): PlanRecord {
  const p = isRecord(raw) ? raw : {};
  return {
    plan_id: strOrNull(p.plan_id) ?? fallbackPlanId,
    state: strOrNull(p.state),
    job_sha256: strOrNull(p.job_sha256),
    assumed: Array.isArray(p.assumed) ? p.assumed : [],
  };
}
