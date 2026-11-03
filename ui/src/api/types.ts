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

export type ExecutionRole =
  | 'planner'
  | 'writer_a'
  | 'writer_b'
  | 'reviewer_a'
  | 'reviewer_b'
  | 'repair';

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

export type TerminalOutcome = EndState;

export type MergeState =
  | 'pending'
  | 'merged'
  | 'merge-conflict'
  | 'tests-failed'
  | 'push-failed'
  | 'main-moved'
  | 'none';

export type MergeStatus = MergeState;

export type WaitReason =
  | 'model-slot'
  | 'account-pace'
  | '429-backoff'
  | 'retry-backoff';

export type ContractState =
  | 'verified'
  | 'failed'
  | 'unverified'
  | 'not-applicable';

export type SemanticContract =
  | 'planner_json'
  | 'writer_file_blocks'
  | 'repair_edit_blocks'
  | 'repair_whole_file'
  | 'reviewer_verdict'
  | 'native_structured_output';

export interface ApiErrorEnvelope {
  status: number;
  error: {
    code: string;
    message: string;
    details: Record<string, unknown>;
  };
}

export interface AttemptEvent {
  seq: number;
  ts: string;
  timestamp: string;
  type:
    | 'attempt-start'
    | 'attempt-end'
    | 'wait-start'
    | 'wait-end'
    | 'fallback_write'
    | 'restored-green'
    | 'merge-status';
  attempt_id: string;
  stage: StageKey | 'fallback_write';
  role: ExecutionRole;
  model: string;
  endpoint: string;
  effort: 'low' | 'medium' | 'high' | null;
  prompt_tokens?: number | null;
  completion_tokens?: number | null;
  tokens_in?: number | null;
  tokens_out?: number | null;
  duration_sec?: number | null;
  cost_usd?: string | null;
  wait_why?: WaitReason;
  detail: string;
}

export interface EventPage {
  events: AttemptEvent[];
  next_after: number;
  index_ok: boolean;
}

export interface TestFailureItem {
  test_name: string;
  file: string;
  line: number;
  assertion: string;
}

export interface ReviewFinding {
  file: string;
  line: number;
  ref: string;
  message: string;
  evidence_ok: boolean;
}

export interface ReviewerReport {
  reviewer_slot: string;
  stage_key: 'reviewer_b' | 'reviewer_a';
  model_id: string;
  canonical_model_id: string;
  verdict: 'READY' | 'FIX FIRST';
  independent_of_authors: boolean;
  evidence_ok: boolean;
  findings: ReviewFinding[];
}

export interface AttemptRecord {
  attempt_id: string;
  seq_start: number;
  seq_end: number | null;
  stage_key: StageKey | 'fallback_write';
  logical_role: ExecutionRole;
  model_id: string;
  canonical_model_id: string;
  endpoint: string;
  effort: 'low' | 'medium' | 'high';
  reasoning: 'off' | 'optional' | 'mandatory';
  temperature: number;
  max_output: number;
  config_revision: string;
  started: string;
  finished: string | null;
  started_at: string;
  finished_at: string | null;
  duration_sec: number | null;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  total_tokens?: number | null;
  input_tokens: number | null;
  output_tokens: number | null;
  cost_usd: string | null;
  billing_mode: 'free' | 'paid' | 'subscription';
  contract: SemanticContract;
  outcome: string;
  tests_summary: {
    passed: number;
    failed: number;
    failures: TestFailureItem[];
  } | null;
}

export type StageRunState = 'ran' | 'running' | 'pending' | 'not-triggered';

export interface JobRecord {
  id?: string;
  job_id: string;
  job_path: string;
  spec_id: string;
  profile: ProfileName;
  config_revision: string;
  snapshot_sha256: string;
  derived_state: DerivedJobState;
  end_state: EndState | null;
  terminal_outcome: EndState | null;
  test_verdict: 'passed' | 'passed (restored-green)' | 'failed' | 'running' | 'not-run';
  reviewer_verdict: 'READY (2/2)' | 'FIX FIRST' | 'pending' | 'not-run';
  merge_state: MergeState;
  merge_status: MergeState;
  stage: StageKey | null;
  active_stage: StageKey | null;
  role: ExecutionRole | null;
  logical_role: ExecutionRole | null;
  active_attempt_id: string | null;
  model: string;
  model_id: string;
  canonical_model_id: string;
  endpoint: string;
  effort: 'low' | 'medium' | 'high';
  started: string;
  finished: string | null;
  started_at: string;
  finished_at: string | null;
  duration_sec: number;
  heartbeat_at: string | null;
  heartbeat_age_s: number | null;
  heartbeat_age_sec: number | null;
  lease_alive: boolean;
  lease_pid: number | null;
  lease: {
    alive: boolean;
    holder_pid: number | null;
    pid: number | null;
    heartbeat_at: string | null;
    heartbeat_age_s: number | null;
    heartbeat_age_sec: number | null;
  };
  worktree_path: string;
  shared_claim_conflicts: string[];
  waiting: {
    why: WaitReason;
    since: string;
    model: string;
    slot_holder_job_id?: string;
  } | null;
  restored_green: boolean;
  restored_green_note?: string;
  normalization_note?: string;
  free_requests_used: number;
  total_cost_usd: string | null;
  hashes: {
    tested_sha256: string | null;
    reviewed_sha256: string | null;
    delivered_sha256: string | null;
    integrity_equal: boolean;
  };
  archive: {
    available: boolean;
    failure_reason?: string;
    path_hint: string;
    size_bytes: number | null;
    artifacts: string[];
  };
  merge_detail: {
    status: MergeState;
    target_branch: string;
    base_sha: string;
    rebased_onto_sha?: string;
    commit_sha: string | null;
    retries: number;
    conflict_files?: string[];
    message: string;
  };
  stages: Record<StageKey, StageRunState>;
  stage_overview: Record<StageKey, StageRunState>;
  contributor_chain: Array<{
    attempt_id: string;
    stage: StageKey | 'fallback_write';
    logical_role: ExecutionRole;
    canonical_model_id: string;
    status: string;
  }>;
  attempts: AttemptRecord[];
  reviews: ReviewerReport[];
  events: AttemptEvent[];
  diff_text: string | null;
}

export interface NormalizedOverview {
  ts: string;
  engine_sha: string;
  active_profile: ProfileName;
  stop: boolean;
  stop_active: boolean;
  stop_reason: string | null;
  loops_running: number;
  queue_depth: number;
  jobs_queued: number;
  jobs_waiting: number;
  loops_waiting: number;
  pace: number;
  pace_60s: {
    sent: number;
    limit: number | null;
  };
  free_today: {
    used: number | null;
    cap: number | null;
    remaining: number | null;
    source: string;
  };
  reviews_pending: number;
  sandbox_slots: {
    used: number;
    cap: number | null;
  };
  active_errors: number;
  reconciled_cost_usd: string | null;
  review_usd_today: string | null;
  review_cap_usd: string | null;
  review_spend: {
    used_usd: string | null;
    cap_usd: string | null;
    reserve_usd: string | null;
  };
  recent_errors: Array<{
    id: string;
    ts: string;
    job_id: string;
    stage: string;
    code: string;
    message: string;
  }>;
}

export interface ModelRecord {
  model_id: string;
  canonical_model_id: string;
  display_name: string;
  provider: string;
  endpoints: string[];
  active_endpoint: string;
  route: 'openrouter' | 'claude_cli';
  billing_type: 'free' | 'paid' | 'subscription';
  input_usd_per_1m: string | null;
  output_usd_per_1m: string | null;
  context_window: number;
  max_output_tokens: number;
  structured_outputs_claimed: boolean;
  validation_path?: 'native_json' | 'text_validated';
  profile_restriction?: string;
  synthetic_scale_row?: boolean;
  contracts: Record<SemanticContract, ContractState>;
  reasoning: {
    mode: 'off' | 'optional' | 'mandatory' | 'unknown';
    supported: 'optional' | 'mandatory' | 'unsupported' | 'unknown';
    off_admissible: boolean;
    efforts: Array<'low' | 'medium' | 'high'>;
    token_budget_supported: boolean;
    token_budget_min: number | null;
    token_budget_max: number | null;
  };
  temperature_range: { min: number; max: number; default: number };
  verified_at: string | null;
  verified_endpoint: string | null;
  freshness: string;
  availability: 'available' | 'owner-gated' | 'unavailable';
  in_use_stages: StageKey[];
}

export interface StageConfigRow {
  stage_key: StageKey;
  display_name: string;
  logical_role: ExecutionRole;
  routing_trigger: string;
  model_selectable: boolean;
  inheritance_rule: string;
  configured_model_id: string | null;
  endpoint: string | null;
  route: 'openrouter' | 'claude_cli' | 'inherited' | 'disabled';
  required_contract: SemanticContract;
  contract_verified: boolean;
  fallback_writer_contract_verified?: boolean;
  effort: 'low' | 'medium' | 'high' | null;
  reasoning_mode: 'off' | 'optional' | 'mandatory' | 'inherited';
  token_budget: number | null;
  temperature: number;
  max_output_tokens: number;
  legacy_roster_max_tokens: number;
  profile_source: string;
  budget_estimate_usd: string | null;
  status_note: string;
}

export type ScenarioId =
  | 'default_all_fixtures'
  | 's1_empty_startup'
  | 's2_waiting_slot_to_start'
  | 's3_passed_merge_pending_vs_merged'
  | 's4_review_open_restored_green'
  | 's5_quota_refusal_pace_exhausted'
  | 's6_soft_stop_active'
  | 's7_untriggered_vs_fallback_write'
  | 's8_disconnected_or_stale_index'
  | 's9_concurrent_same_model_diff_effort'
  | 's10_409_revision_conflict'
  | 's11_model_capability_restrictions'
  | 's12_archive_fail_and_merge_conflict'
  | 's13_idempotency_and_rerun'
  | 's14_paid_planner_probe_gate'
  | 's15_model_pool_removal_refusal'
  | 's16_system_limits_c08_gated';
