/**
 * Front-end types for docs/API_CONTRACT.md. Every field name is the contract's own name.
 * Absent or unknown backend values are `null` (rendered "Unknown"), never a guessed value.
 * Optional (`?`) fields exist only on detail responses; they are undefined on list items.
 */

export type DataMode = 'mock' | 'live';
export type ProfileName = 'test' | 'production';

export type NavPage =
  | '/dashboard'
  | '/jobs/new'
  | '/loops'
  | '/models'
  | '/stages'
  | '/results'
  | '/system';

/** The ten configurable stage types (contract section 1). */
export type StageKey =
  | 'planner'
  | 'writer_a_write'
  | 'writer_a_repair'
  | 'writer_b_write'
  | 'writer_b_repair'
  | 'reviewer_b'
  | 'reviewer_a'
  | 'reviewer_reask'
  | 'specialist_repair'
  | 'review_fix_repair';

export type DerivedJobState =
  | 'queued'
  | 'running'
  | 'waiting'
  | 'waiting-unknown'
  | 'stale-warning'
  | 'orphan'
  | 'finished'
  | 'merged';

export type EndState =
  | 'passed'
  | 'review-open'
  | 'escalate'
  | 'needs_review'
  | 'blocked'
  | 'provider-unavailable'
  | 'budget-exceeded'
  | 'quota-exhausted'
  | 'deadline'
  | 'integrity-mismatch'
  | 'acceptance-tampered'
  | 'exception'
  | 'invalid-spec'
  | 'job-too-large'
  | 'sandbox-unavailable'
  | 'acceptance-invalid'
  | 'refused'
  | 'stopped';

export type MergeState =
  | 'merged'
  | 'merge-conflict'
  | 'tests-failed'
  | 'push-failed'
  | 'main-moved'
  | 'pending'
  | 'none';

export type WaitWhy = 'model-slot' | 'account-pace' | '429-backoff' | 'retry-backoff';

export type StageStatus = 'ran' | 'running' | 'not-triggered' | 'pending';

export type AttemptStatus = 'running' | 'ok' | 'failed' | 'rejected';

export type Via = 'openrouter' | 'claude_code';

export type Effort = 'off' | 'low' | 'medium' | 'high' | 'xhigh' | 'max';

export type SemanticContract =
  | 'planner_json'
  | 'writer_file_blocks'
  | 'repair_edit_blocks'
  | 'repair_whole_file'
  | 'reviewer_verdict'
  | 'native_structured_output';

export type ContractState = 'verified' | 'failed' | 'unverified' | 'not-applicable';

export type ReasoningMode = 'optional' | 'mandatory' | 'unsupported' | 'unknown';

export type StartRefusal =
  | 'stop-file'
  | 'provider-unavailable'
  | 'free-requests'
  | 'review-spend'
  | 'request-running'
  | 'no-sandbox-slot'
  | 'claim-conflict'
  | 'no-budget-snapshot';

/** Error envelope: {"error": {"code", "message", "details"}} with an HTTP status. */
export interface ApiErrorEnvelope {
  status: number;
  error: {
    code: string;
    message: string;
    details: Record<string, unknown>;
  };
}

// ---------------------------------------------------------------------------
// Events (GET /loops/{id}/events)
// ---------------------------------------------------------------------------

export type LoopEventType =
  | 'job-start'
  | 'attempt-start'
  | 'attempt-end'
  | 'wait-start'
  | 'wait-end'
  | 'tests'
  | 'review'
  | 'restore-green'
  | 'finish'
  | 'merge';

/** One recorded event. Type-specific fields are null when the event type does not carry them. */
export interface LoopEvent {
  seq: number;
  ts: string | null;
  type: LoopEventType | string;
  attempt_id: string | null;
  stage: string | null;
  role: string | null;
  mode: string | null;
  model: string | null;
  via: Via | null;
  effort: Effort | null;
  status: AttemptStatus | null;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  reasoning_tokens: number | null;
  finish_reason: string | null;
  seconds: number | null;
  error: string | null;
  why: WaitWhy | null;
  green: boolean | null;
  failing: unknown[] | null;
  reviewer: string | null;
  verdict: string | null;
  evidence_ok: boolean | null;
  end_state: EndState | null;
  reason: string | null;
  merge_state: MergeState | null;
  head: string | null;
  attempts: number | null;
}

export interface EventPage {
  events: LoopEvent[];
  next_after: number | null;
  index_ok: boolean | null;
}

// ---------------------------------------------------------------------------
// Jobs (GET /jobs, GET /loops/{id})
// ---------------------------------------------------------------------------

export interface WaitingInfo {
  why: WaitWhy | null;
  since: string | null;
  model: string | null;
}

/** A row of `GET /jobs` (JobSummary). */
export interface JobSummary {
  job_id: string;
  name: string | null;
  derived_state: DerivedJobState | null;
  end_state: EndState | null;
  merge_state: MergeState | null;
  active_stage: StageKey | null;
  active_model: string | null;
  waiting: WaitingInfo | null;
  heartbeat_age_s: number | null;
  started: string | null;
  finished: string | null;
  free_requests_used: number | null;
  review_usd_spent: number | null;
}

export interface StageRunRow {
  stage: StageKey;
  status: StageStatus | null;
  attempts: number | null;
}

/** The JobDetail-only fields (GET /loops/{id}). Absent on list items. */
export interface JobDetailExtras {
  profile: ProfileName | null;
  spec: string | null;
  files: string[];
  reason: string | null;
  tests_passed: boolean | null;
  failing_tests: unknown[];
  review_verdict: string | null;
  findings: string[];
  stage_snapshot: { sha256: string | null; revision: number | null } | null;
  stages: StageRunRow[];
  lease: {
    held: boolean | null;
    pid: number | null;
    pid_alive: boolean | null;
    heartbeat: string | null;
    generation: number | null;
  } | null;
  budget: {
    free_requests_used: number | null;
    free_requests: number | null;
    review_usd_spent: number | null;
    review_usd: number | null;
    repair_calls: number | null;
  } | null;
  hashes: { tested: string | null; reviewed: string | null; delivered: string | null } | null;
  archive: { available: boolean | null; archive_failed: boolean | null } | null;
  merge: {
    merge_state: MergeState | null;
    head: string | null;
    attempts: number | null;
    note: string | null;
  } | null;
  note: string | null;
}

/** A job as the UI holds it: a summary, plus detail fields once a detail response was loaded. */
export type JobRecord = JobSummary & Partial<JobDetailExtras>;

// ---------------------------------------------------------------------------
// Results (GET /results/{id})
// ---------------------------------------------------------------------------

export interface ResultTestFailure {
  id: string | null;
  message: string | null;
  file: string | null;
  line: number | null;
}

export interface ResultReview {
  reviewer: string | null;
  verdict: string | null;
  findings: string[];
  file: string | null;
}

export interface ResultRecord {
  job_id: string;
  end_state: EndState | null;
  merge_state: MergeState | null;
  tests: { passed: boolean | null; failing: ResultTestFailure[] } | null;
  reviews: ResultReview[];
  diff_available: boolean | null;
  artifacts: string[];
  branch: string | null;
  merge: unknown | null;
}

// ---------------------------------------------------------------------------
// Overview (GET /overview)
// ---------------------------------------------------------------------------

export interface NormalizedOverview {
  loops_running: number | null;
  queue_depth: number | null;
  jobs_waiting: number | null;
  active_errors: number | null;
  stop: boolean | null;
  free_today: { used: number | null; cap: number | null; cap_source: string | null };
  pace: { sent_last_60s: number | null; limit: number | null };
  review_usd_today: { spent: number | null; cap: number | null };
  pending_reviews: number | null;
  sandbox_slots: { used: number | null; total: number | null };
  updated: string | null;
  index_ok: boolean | null;
}

// ---------------------------------------------------------------------------
// Models (GET /models, POST /models, GET /models/search)
// ---------------------------------------------------------------------------

export interface ModelCapabilities {
  reasoning_on_off: boolean | null;
  efforts: Effort[];
  token_budget: boolean | null;
  temperature: { min: number; max: number } | null;
  structured_outputs: boolean | null;
  contracts: Record<SemanticContract, ContractState>;
  reasoning_mode: ReasoningMode;
  text_validated: boolean | null;
}

export interface ModelRecord {
  model_id: string;
  via: Via | null;
  hosting_provider: string | null;
  paid: boolean | null;
  price_in_usd_per_mtok: number | null;
  price_out_usd_per_mtok: number | null;
  context_tokens: number | null;
  max_output_tokens: number | null;
  capabilities: ModelCapabilities;
  verified_at: string | null;
  metadata_at: string | null;
  note: string | null;
  endpoint: string | null;
}

/** Result of POST /models/verify. Only `pass` yields "verified"; everything else is not verified. */
export interface VerifyResult {
  model_id: string;
  contract: SemanticContract;
  state: 'verified' | 'failed' | 'unverified';
  verified_at: string | null;
}

// ---------------------------------------------------------------------------
// Stage settings (GET /stages, PUT /stages)
// ---------------------------------------------------------------------------

export interface StageSetting {
  stage_key: StageKey;
  configured_model_id: string | null;
  via: Via | null;
  model_selectable: boolean | null;
  inherits: string | null;
  /** The contract a model must have `verified` to be used for this stage (GET /stages `required_contract`). */
  required_contract: SemanticContract | null;
  effort: Effort | null;
  revision: number | null;
  source: string | null;
  /** settings.max_tokens, or null when the backend sends none. */
  max_output_tokens: number | null;
  settings: Record<string, unknown>;
  note: string | null;
}

export interface StagesResponse {
  profile: ProfileName | null;
  revision: number | null;
  stages: StageSetting[];
}

// ---------------------------------------------------------------------------
// Plans (POST /plans, GET /plans/{id})
// ---------------------------------------------------------------------------

export interface PlanRecord {
  plan_id: string;
  state: 'planning' | 'planned' | string | null;
  job_sha256: string | null;
  assumed: unknown[];
}

// ---------------------------------------------------------------------------
// System (GET /system/health, GET /system/limits)
// ---------------------------------------------------------------------------

export interface SystemHealth {
  backend: string | null;
  engine_version: string | null;
  docker: string | null;
  sandbox_image: string | null;
  satvik_home: string | null;
  index_ok: boolean | null;
  keys: Array<{ alias: string; present: boolean | null }>;
  stop: boolean | null;
}

export interface SystemLimits {
  /** The limits' revision (GET /system/limits); PUT /system/limits must send it. null when the backend did not report one. */
  revision: number | null;
  leases: Array<{ job_id: string; held: boolean | null; heartbeat_age_s: number | null; pid_alive: boolean | null }>;
  claims: Array<{ path: string; job_id: string }>;
  gates: Array<{ gate: string; holders: number | null; capacity: number | null }>;
  ledger: { free_used: number | null; free_cap: number | null; review_spent_usd: number | null };
}

// ---------------------------------------------------------------------------
// Stage-settings and STOP helpers
// ---------------------------------------------------------------------------

export interface StageChange {
  stage: StageKey;
  model: string | null;
  /** The transport of `model` (I61): sent with the model when the row has one; absent otherwise (never null). */
  via?: Via;
  effort: Effort | null;
  settings: Record<string, unknown>;
}
