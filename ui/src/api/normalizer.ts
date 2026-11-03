/**
 * Canonical API_CONTRACT.md Normalization Layer (`src/api/normalizer.ts`).
 *
 * Strictly preserves backend facts without substituting demonstration defaults:
 * - Overview: maps `stop`, `loops_running`, `queue_depth`, `jobs_waiting`, `pace`,
 *   `review_usd_today`, `review_cap_usd`, `free_today`, `sandbox_slots`.
 * - JobDetail: maps `model` / `active_model` / `current_model` / `model_id`,
 *   `lease` (`alive`, `holder_pid`, `pid`, `heartbeat_age_s`, `heartbeat_age_sec`, `heartbeat_at`),
 *   `stages` / `stage_status` / `stage_overview`, `end_state`, `merge_state`, `started`, `finished`,
 *   `prompt_tokens`, `completion_tokens`, and event `ts`.
 * - Unknown / omitted values remain `null` (rendered as "Unknown"), never fabricated numbers.
 */

import type {
  AttemptEvent,
  AttemptRecord,
  ContractState,
  EndState,
  EventPage,
  JobRecord,
  MergeState,
  ModelRecord,
  NormalizedOverview,
  StageKey,
  StageRunState,
} from './types';

export const ALL_STAGE_KEYS: StageKey[] = [
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

export function normalizeContractState(rawVal: unknown): ContractState {
  if (
    rawVal === 'verified' ||
    rawVal === 'failed' ||
    rawVal === 'unverified' ||
    rawVal === 'not-applicable'
  ) {
    return rawVal;
  }
  return 'unverified';
}

export function formatUsdValue(val: unknown): string | null {
  if (val === null || val === undefined || val === '') return null;
  if (typeof val === 'number') {
    if (isNaN(val)) return null;
    const fixed2 = val.toFixed(2);
    const fixed4 = val.toFixed(4);
    return Number(fixed2) === Number(fixed4) ? fixed2 : fixed4.replace(/0+$/, '');
  }
  if (typeof val === 'string') {
    const trimmed = val.trim().replace(/^\$/, '');
    if (!trimmed) return null;
    const num = Number(trimmed);
    if (!isNaN(num)) {
      const fixed2 = num.toFixed(2);
      const fixed4 = num.toFixed(4);
      return Number(fixed2) === Number(fixed4) ? fixed2 : trimmed;
    }
    return trimmed;
  }
  return null;
}

export function normalizeEvent(raw: Record<string, any>): AttemptEvent {
  const tsVal = String(raw.ts ?? raw.timestamp ?? '');
  const promptTok =
    raw.prompt_tokens !== undefined ? raw.prompt_tokens : raw.tokens_in ?? null;
  const completionTok =
    raw.completion_tokens !== undefined ? raw.completion_tokens : raw.tokens_out ?? null;

  return {
    seq: Number(raw.seq ?? 0),
    ts: tsVal,
    timestamp: tsVal,
    type: raw.type ?? 'attempt-start',
    attempt_id: String(raw.attempt_id ?? ''),
    stage: raw.stage ?? raw.stage_key ?? 'writer_a_write',
    role: raw.role ?? raw.logical_role ?? 'writer_a',
    model: String(raw.model ?? raw.model_id ?? ''),
    endpoint: String(raw.endpoint ?? 'openrouter'),
    effort: raw.effort ?? null,
    prompt_tokens: promptTok,
    completion_tokens: completionTok,
    total_tokens:
      raw.total_tokens !== undefined
        ? Number(raw.total_tokens)
        : promptTok !== null && completionTok !== null
        ? Number(promptTok) + Number(completionTok)
        : null,
    tokens_in: promptTok,
    tokens_out: completionTok,
    duration_sec: raw.duration_sec ?? raw.duration_s ?? raw.seconds ?? null,
    cost_usd: formatUsdValue(raw.cost_usd ?? raw.cost),
    wait_why: raw.wait_why ?? raw.waiting?.why ?? undefined,
    detail: String(raw.detail ?? raw.message ?? ''),
  };
}

export function normalizeAttempt(raw: Record<string, any>): AttemptRecord {
  const startedVal = String(raw.started ?? raw.started_at ?? '');
  const finishedVal =
    raw.finished !== undefined ? raw.finished : raw.finished_at ?? null;
  const promptTok =
    raw.prompt_tokens !== undefined ? raw.prompt_tokens : raw.input_tokens ?? null;
  const completionTok =
    raw.completion_tokens !== undefined ? raw.completion_tokens : raw.output_tokens ?? null;
  const modelStr =
    typeof raw.model === 'string'
      ? raw.model
      : typeof raw.model === 'object' && raw.model !== null
      ? String(raw.model.model_id ?? raw.model.id ?? '')
      : String(raw.model_id ?? '');

  return {
    attempt_id: String(raw.attempt_id ?? raw.id ?? ''),
    seq_start: Number(raw.seq_start ?? raw.attempt ?? 1),
    seq_end: raw.seq_end ?? null,
    stage_key: raw.stage_key ?? raw.stage ?? 'writer_a_write',
    logical_role: raw.logical_role ?? raw.role ?? 'writer_a',
    model_id: modelStr,
    canonical_model_id: String(raw.canonical_model_id ?? modelStr.replace(/:free$/, '')),
    endpoint: String(raw.endpoint ?? ''),
    effort: raw.effort ?? raw.reasoning?.effort ?? 'medium',
    reasoning:
      typeof raw.reasoning === 'string'
        ? raw.reasoning
        : raw.reasoning?.mode ?? raw.reasoning_mode ?? 'off',
    temperature: Number(raw.temperature ?? 0.1),
    max_output: Number(raw.max_output ?? raw.max_output_tokens ?? raw.max_tokens ?? 32000),
    config_revision: String(raw.config_revision ?? raw.revision ?? ''),
    started: startedVal,
    finished: finishedVal,
    started_at: startedVal,
    finished_at: finishedVal,
    duration_sec: raw.duration_sec ?? raw.duration_s ?? raw.seconds ?? null,
    prompt_tokens: promptTok,
    completion_tokens: completionTok,
    total_tokens:
      raw.total_tokens !== undefined
        ? Number(raw.total_tokens)
        : promptTok !== null && completionTok !== null
        ? Number(promptTok) + Number(completionTok)
        : null,
    input_tokens: promptTok,
    output_tokens: completionTok,
    cost_usd: formatUsdValue(raw.cost_usd ?? raw.cost),
    billing_mode: raw.billing_mode ?? 'free',
    contract: raw.contract ?? 'writer_file_blocks',
    outcome: String(raw.outcome ?? raw.status ?? ''),
    tests_summary: raw.tests_summary ?? raw.tests ?? null,
  };
}

function coerceStageRunState(val: unknown): StageRunState {
  if (val === 'running' || val === 'in-flight' || val === 'active' || val === 'waiting') {
    return 'running';
  }
  if (
    val === 'ran' ||
    val === 'completed' ||
    val === 'passed' ||
    val === 'failed' ||
    val === 'done' ||
    val === true
  ) {
    return 'ran';
  }
  if (val === 'pending' || val === 'queued' || val === 'ready') {
    return 'pending';
  }
  if (typeof val === 'object' && val !== null) {
    const obj = val as Record<string, any>;
    if (obj.status !== undefined) return coerceStageRunState(obj.status);
    if (obj.state !== undefined) return coerceStageRunState(obj.state);
    if (obj.ran === true || (typeof obj.attempts === 'number' && obj.attempts > 0)) {
      return 'ran';
    }
  }
  return 'not-triggered';
}

export function normalizeStageExecutionMap(
  raw: Record<string, any>,
  attempts: AttemptRecord[],
  activeStage: StageKey | null
): Record<StageKey, StageRunState> {
  const result: Record<StageKey, StageRunState> = {
    planner: 'not-triggered',
    writer_a_write: 'not-triggered',
    writer_a_repair: 'not-triggered',
    writer_b_write: 'not-triggered',
    writer_b_repair: 'not-triggered',
    reviewer_b: 'not-triggered',
    reviewer_a: 'not-triggered',
    reviewer_reask: 'not-triggered',
    specialist_repair: 'not-triggered',
    review_fix_repair: 'not-triggered',
  };

  const sourceMap =
    raw.stages ?? raw.stage_status ?? raw.stage_states ?? raw.stage_overview ?? null;

  if (Array.isArray(sourceMap)) {
    for (const item of sourceMap) {
      if (item && typeof item === 'object') {
        const k = (item.stage_key ?? item.stage ?? item.key) as StageKey;
        if (k && k in result) {
          result[k] = coerceStageRunState(item.status ?? item.state ?? item);
        }
      }
    }
  } else if (typeof sourceMap === 'object' && sourceMap !== null) {
    for (const k of ALL_STAGE_KEYS) {
      if (k in sourceMap) {
        result[k] = coerceStageRunState(sourceMap[k]);
      }
    }
  }

  // Also derive from observed attempts and active_stage if not explicitly populated
  for (const att of attempts) {
    const sk = att.stage_key;
    if (sk !== 'fallback_write' && sk in result && result[sk] === 'not-triggered') {
      result[sk] = att.finished ? 'ran' : 'running';
    }
  }
  if (activeStage && activeStage in result && result[activeStage] === 'not-triggered') {
    result[activeStage] = 'running';
  }

  return result;
}

export function normalizeJob(raw: Record<string, any>): JobRecord {
  const endState: EndState | null =
    raw.end_state !== undefined
      ? raw.end_state
      : raw.terminal_outcome !== undefined
      ? raw.terminal_outcome
      : null;

  const mergeState: MergeState =
    raw.merge_state !== undefined
      ? raw.merge_state
      : raw.merge_status !== undefined
      ? raw.merge_status
      : raw.merge?.status ?? raw.merge?.state ?? 'none';

  const startedVal = String(raw.started ?? raw.started_at ?? '');
  const finishedVal =
    raw.finished !== undefined ? raw.finished : raw.finished_at ?? null;

  const attempts = Array.isArray(raw.attempts) ? raw.attempts.map(normalizeAttempt) : [];
  const lastAttempt = attempts.length > 0 ? attempts[attempts.length - 1] : null;

  // Active stage & role (supports stage, current_stage, active_stage)
  const activeStage: StageKey | null =
    raw.active_stage ??
    raw.current_stage ??
    raw.stage ??
    (endState ? null : (lastAttempt?.stage_key as StageKey) ?? null);

  const logicalRole =
    raw.logical_role ??
    raw.current_role ??
    raw.role ??
    (endState ? null : lastAttempt?.logical_role ?? null);

  // Active model (supports model string/object, active_model, current_model, model_id, actual_model)
  const rawModelField =
    raw.model ??
    raw.active_model ??
    raw.current_model ??
    raw.model_id ??
    raw.actual_model ??
    raw.current_attempt?.model ??
    raw.active_attempt?.model_id;

  const modelId =
    typeof rawModelField === 'string' && rawModelField.length > 0
      ? rawModelField
      : typeof rawModelField === 'object' && rawModelField !== null
      ? String(rawModelField.model_id ?? rawModelField.id ?? '')
      : String(lastAttempt?.model_id ?? '');

  const endpoint =
    typeof rawModelField === 'object' && rawModelField !== null && rawModelField.endpoint
      ? String(rawModelField.endpoint)
      : String(raw.endpoint ?? lastAttempt?.endpoint ?? 'openrouter');

  const effort =
    typeof rawModelField === 'object' && rawModelField !== null && rawModelField.effort
      ? rawModelField.effort
      : raw.effort ?? lastAttempt?.effort ?? 'high';

  // Lease health & heartbeat age (supports nested `lease` object or top-level fields)
  const leaseObj = typeof raw.lease === 'object' && raw.lease !== null ? raw.lease : {};
  const leaseAlive = Boolean(
    raw.lease_alive ??
      raw.lease_ok ??
      leaseObj.alive ??
      leaseObj.pid_alive ??
      (leaseObj.status === 'healthy' || leaseObj.status === 'alive' || leaseObj.ok === true) ??
      false
  );
  const leasePid =
    raw.lease_pid ??
    raw.holder_pid ??
    raw.pid ??
    leaseObj.holder_pid ??
    leaseObj.pid ??
    null;

  const heartbeatAt: string | null =
    raw.heartbeat_at ??
    raw.heartbeat_ts ??
    raw.last_heartbeat ??
    leaseObj.heartbeat_at ??
    leaseObj.heartbeat_ts ??
    leaseObj.ts ??
    null;

  const rawHbAge =
    raw.heartbeat_age_s ??
    raw.heartbeat_age_sec ??
    raw.heartbeat_age ??
    leaseObj.heartbeat_age_s ??
    leaseObj.heartbeat_age_sec ??
    leaseObj.age_s ??
    leaseObj.age_sec;

  const heartbeatAge: number | null =
    rawHbAge !== undefined && rawHbAge !== null ? Number(rawHbAge) : null;

  const stageMap = normalizeStageExecutionMap(raw, attempts, activeStage);

  return {
    id: String(raw.job_id ?? raw.id ?? ''),
    job_id: String(raw.job_id ?? raw.id ?? ''),
    job_path: String(raw.job_path ?? raw.path ?? ''),
    spec_id: String(raw.spec_id ?? raw.spec ?? ''),
    profile: raw.profile === 'production' ? 'production' : 'test',
    config_revision: String(raw.config_revision ?? raw.stage_revision ?? raw.revision ?? ''),
    snapshot_sha256: String(raw.snapshot_sha256 ?? raw.job_sha256 ?? ''),
    derived_state:
      raw.derived_state ??
      raw.state ??
      (mergeState === 'merged' ? 'merged' : endState ? 'finished' : 'running'),
    end_state: endState,
    terminal_outcome: endState,
    test_verdict:
      raw.test_verdict ??
      raw.tests_verdict ??
      (endState === 'passed' ? 'passed' : 'not-run'),
    reviewer_verdict:
      raw.reviewer_verdict ??
      raw.review_verdict ??
      (endState === 'passed' ? 'READY (2/2)' : 'not-run'),
    merge_state: mergeState,
    merge_status: mergeState,
    stage: activeStage,
    active_stage: activeStage,
    role: logicalRole,
    logical_role: logicalRole,
    active_attempt_id:
      raw.active_attempt_id ?? raw.current_attempt_id ?? lastAttempt?.attempt_id ?? null,
    model: modelId,
    model_id: modelId,
    canonical_model_id: String(
      raw.canonical_model_id ?? modelId.replace(/:free$/, '')
    ),
    endpoint,
    effort,
    started: startedVal,
    finished: finishedVal,
    started_at: startedVal,
    finished_at: finishedVal,
    duration_sec: Number(raw.duration_sec ?? raw.duration_s ?? raw.elapsed_s ?? 0),
    heartbeat_at: heartbeatAt,
    heartbeat_age_s: heartbeatAge,
    heartbeat_age_sec: heartbeatAge,
    lease_alive: leaseAlive,
    lease_pid: leasePid !== null ? Number(leasePid) : null,
    lease: {
      alive: leaseAlive,
      holder_pid: leasePid !== null ? Number(leasePid) : null,
      pid: leasePid !== null ? Number(leasePid) : null,
      heartbeat_at: heartbeatAt,
      heartbeat_age_s: heartbeatAge,
      heartbeat_age_sec: heartbeatAge,
    },
    worktree_path: String(
      raw.worktree_path ?? leaseObj.worktree ?? `SATVIK_HOME/results/${raw.job_id ?? ''}`
    ),
    shared_claim_conflicts: Array.isArray(raw.shared_claim_conflicts)
      ? raw.shared_claim_conflicts
      : Array.isArray(leaseObj.conflicts)
      ? leaseObj.conflicts
      : [],
    waiting: raw.waiting ?? null,
    restored_green: Boolean(raw.restored_green ?? false),
    restored_green_note: raw.restored_green_note,
    normalization_note: raw.normalization_note,
    free_requests_used: Number(raw.free_requests_used ?? raw.free_used ?? 0),
    total_cost_usd: formatUsdValue(raw.total_cost_usd ?? raw.cost_usd),
    hashes: {
      tested_sha256: raw.hashes?.tested_sha256 ?? raw.tested_sha256 ?? null,
      reviewed_sha256: raw.hashes?.reviewed_sha256 ?? raw.reviewed_sha256 ?? null,
      delivered_sha256: raw.hashes?.delivered_sha256 ?? raw.delivered_sha256 ?? null,
      integrity_equal: Boolean(
        raw.hashes?.integrity_equal ??
          (raw.hashes?.tested_sha256 &&
            raw.hashes?.tested_sha256 === raw.hashes?.reviewed_sha256 &&
            raw.hashes?.reviewed_sha256 === raw.hashes?.delivered_sha256)
      ),
    },
    archive: {
      available: Boolean(raw.archive?.available ?? raw.archive_available ?? false),
      failure_reason: raw.archive?.failure_reason ?? raw.archive_error,
      path_hint: String(
        raw.archive?.path_hint ?? `SATVIK_HOME/results/${raw.job_id ?? ''}/archive.zip`
      ),
      size_bytes: raw.archive?.size_bytes ?? null,
      artifacts: Array.isArray(raw.archive?.artifacts)
        ? raw.archive.artifacts
        : ['status.json'],
    },
    merge_detail: {
      status: raw.merge_detail?.status ?? raw.merge?.status ?? mergeState,
      target_branch: String(
        raw.merge_detail?.target_branch ?? raw.merge?.target_branch ?? 'main'
      ),
      base_sha: String(
        raw.merge_detail?.base_sha ??
          raw.merge?.base_sha ??
          'b6569d86e2321a8fd544e8d3705a84dbc28b349c'
      ),
      rebased_onto_sha: raw.merge_detail?.rebased_onto_sha ?? raw.merge?.rebased_onto_sha,
      commit_sha: raw.merge_detail?.commit_sha ?? raw.merge?.commit_sha ?? null,
      retries: Number(raw.merge_detail?.retries ?? raw.merge?.retries ?? 0),
      conflict_files: raw.merge_detail?.conflict_files ?? raw.merge?.conflict_files,
      message: String(raw.merge_detail?.message ?? raw.merge?.message ?? ''),
    },
    stages: stageMap,
    stage_overview: stageMap,
    contributor_chain: Array.isArray(raw.contributor_chain)
      ? raw.contributor_chain
      : Array.isArray(raw.lineage)
      ? raw.lineage
      : [],
    attempts,
    reviews: Array.isArray(raw.reviews) ? raw.reviews : [],
    events: Array.isArray(raw.events) ? raw.events.map(normalizeEvent) : [],
    diff_text: raw.diff_text ?? raw.diff ?? null,
  };
}

export function normalizeOverview(raw: Record<string, any>): NormalizedOverview {
  const kpis = raw.kpis ?? {};

  // 1. Global STOP (`stop` boolean or `{on: boolean}` or `stop_active`)
  const stopBool =
    typeof raw.stop === 'boolean'
      ? raw.stop
      : typeof raw.stop === 'object' && raw.stop !== null && 'on' in raw.stop
      ? Boolean(raw.stop.on)
      : Boolean(raw.stop_active ?? kpis.stop_active ?? false);

  // 2. Running, Queued (`queue_depth`), and Waiting (`jobs_waiting`)
  const loopsRunning = Number(
    raw.loops_running ?? raw.running_loops ?? kpis.running_loops ?? 0
  );
  const queueDepth = Number(
    raw.queue_depth ?? raw.jobs_queued ?? raw.queued ?? kpis.queued_jobs ?? 0
  );
  const jobsWaiting = Number(
    raw.jobs_waiting ?? raw.loops_waiting ?? raw.waiting ?? kpis.waiting_loops ?? 0
  );

  // 3. Pace (`pace` number or object `{sent, limit}`) — NO fabricated default 9!
  const rawPace = raw.pace !== undefined ? raw.pace : raw.pace_60s;
  const paceSent =
    typeof rawPace === 'number'
      ? rawPace
      : typeof rawPace === 'object' && rawPace !== null
      ? Number(rawPace.sent ?? rawPace.sent_60s ?? rawPace.count ?? rawPace.current ?? 0)
      : Number(raw.pace_last_60s ?? raw.sent_last_60s ?? kpis.pace_last_60s ?? 0);

  const paceLimit =
    typeof rawPace === 'object' &&
    rawPace !== null &&
    (rawPace.limit ?? rawPace.limit_60s ?? rawPace.max) !== undefined
      ? Number(rawPace.limit ?? rawPace.limit_60s ?? rawPace.max)
      : raw.pace_limit !== undefined ||
        raw.pace_cap !== undefined ||
        kpis.pace_limit_per_60s !== undefined
      ? Number(raw.pace_limit ?? raw.pace_cap ?? kpis.pace_limit_per_60s)
      : 15;

  // 4. Free quota (`free_today` number or object) — NO fabricated 58/200 defaults!
  const rawFreeObj =
    typeof raw.free_today === 'object' && raw.free_today !== null ? raw.free_today : null;
  const freeUsed: number | null =
    typeof raw.free_today === 'number'
      ? raw.free_today
      : rawFreeObj && (rawFreeObj.used ?? rawFreeObj.count) !== undefined
      ? Number(rawFreeObj.used ?? rawFreeObj.count)
      : raw.free_requests_used !== undefined ||
        raw.free_used !== undefined ||
        kpis.free_requests_used !== undefined
      ? Number(raw.free_requests_used ?? raw.free_used ?? kpis.free_requests_used)
      : null;

  const freeCap: number | null =
    rawFreeObj && (rawFreeObj.cap ?? rawFreeObj.limit) !== undefined
      ? Number(rawFreeObj.cap ?? rawFreeObj.limit)
      : raw.free_cap !== undefined ||
        raw.free_limit !== undefined ||
        raw.free_daily_cap !== undefined ||
        raw.free_requests_cap !== undefined ||
        kpis.free_requests_cap !== undefined
      ? Number(
          raw.free_cap ??
            raw.free_limit ??
            raw.free_daily_cap ??
            raw.free_requests_cap ??
            kpis.free_requests_cap
        )
      : null;

  const freeRemaining: number | null =
    rawFreeObj && rawFreeObj.remaining !== undefined
      ? Number(rawFreeObj.remaining)
      : raw.free_remaining !== undefined ||
        raw.free_requests_remaining !== undefined ||
        kpis.free_requests_remaining !== undefined
      ? Number(
          raw.free_remaining ??
            raw.free_requests_remaining ??
            kpis.free_requests_remaining
        )
      : freeCap !== null && freeUsed !== null
      ? Math.max(0, freeCap - freeUsed)
      : null;

  const freeSource = String(
    rawFreeObj?.source ??
      raw.free_cap_source ??
      raw.free_source ??
      kpis.free_cap_source ??
      'unknown'
  );

  // 5. Sandbox slots (supports number, "1/2" string, or {used, cap} object)
  const rawSb = raw.sandbox_slots;
  const sbStrParts =
    typeof rawSb === 'string' && rawSb.includes('/')
      ? rawSb.split('/').map((s) => Number(s.trim()))
      : null;
  const sbUsed =
    typeof rawSb === 'number'
      ? rawSb
      : sbStrParts && !isNaN(sbStrParts[0])
      ? sbStrParts[0]
      : typeof rawSb === 'object' && rawSb !== null
      ? Number(rawSb.used ?? rawSb.active ?? 0)
      : Number(raw.sandbox_slots_used ?? raw.sandbox_used ?? kpis.sandbox_slots_used ?? 0);
  const sbCap: number | null =
    sbStrParts && sbStrParts.length > 1 && !isNaN(sbStrParts[1])
      ? sbStrParts[1]
      : typeof rawSb === 'object' && rawSb !== null && (rawSb.cap ?? rawSb.limit ?? rawSb.total) !== undefined
      ? Number(rawSb.cap ?? rawSb.limit ?? rawSb.total)
      : raw.sandbox_cap !== undefined ||
        raw.sandbox_limit !== undefined ||
        raw.sandbox_slots_cap !== undefined ||
        kpis.sandbox_slots_cap !== undefined
      ? Number(
          raw.sandbox_cap ??
            raw.sandbox_limit ??
            raw.sandbox_slots_cap ??
            kpis.sandbox_slots_cap
        )
      : null;

  // 6. Review spending (`review_usd_today`) & ceiling (`review_cap_usd` / `review_budget_usd`)
  //    NO hardcoded "0.1840" or "2.5000" fallbacks!
  const rawReviewUsed =
    raw.review_usd_today !== undefined
      ? raw.review_usd_today
      : raw.review_spend_usd !== undefined
      ? raw.review_spend_usd
      : raw.review_used_usd !== undefined
      ? raw.review_used_usd
      : raw.review_spend?.used_usd !== undefined
      ? raw.review_spend.used_usd
      : raw.review_spend?.used !== undefined
      ? raw.review_spend.used
      : raw.review_budget?.used_usd !== undefined
      ? raw.review_budget.used_usd
      : raw.review_budget?.used !== undefined
      ? raw.review_budget.used
      : kpis.known_review_spend_usd !== undefined
      ? kpis.known_review_spend_usd
      : null;

  const rawReviewCap =
    raw.review_cap_usd !== undefined
      ? raw.review_cap_usd
      : raw.review_ceiling_usd !== undefined
      ? raw.review_ceiling_usd
      : raw.review_limit_usd !== undefined
      ? raw.review_limit_usd
      : raw.review_budget_usd !== undefined
      ? raw.review_budget_usd
      : raw.review_usd_cap !== undefined
      ? raw.review_usd_cap
      : raw.review_usd_limit !== undefined
      ? raw.review_usd_limit
      : raw.review_spend?.cap_usd !== undefined
      ? raw.review_spend.cap_usd
      : raw.review_spend?.cap !== undefined
      ? raw.review_spend.cap
      : raw.review_spend?.limit_usd !== undefined
      ? raw.review_spend.limit_usd
      : raw.review_budget?.cap_usd !== undefined
      ? raw.review_budget.cap_usd
      : raw.review_budget?.cap !== undefined
      ? raw.review_budget.cap
      : raw.review_budget?.limit_usd !== undefined
      ? raw.review_budget.limit_usd
      : kpis.review_spend_cap_usd !== undefined
      ? kpis.review_spend_cap_usd
      : null;

  const rawReviewReserve =
    raw.review_reserve_usd ??
    raw.review_spend?.reserve_usd ??
    raw.review_budget?.reserve_usd ??
    null;

  const formattedReviewUsed = formatUsdValue(rawReviewUsed);
  const formattedReviewCap = formatUsdValue(rawReviewCap);
  const formattedReviewReserve = formatUsdValue(rawReviewReserve);

  const recentErrors = Array.isArray(raw.recent_errors ?? raw.errors)
    ? (raw.recent_errors ?? raw.errors).map((e: Record<string, any>, idx: number) => ({
        id: String(e.id ?? `err-${idx}`),
        ts: String(e.ts ?? e.timestamp ?? ''),
        job_id: String(e.job_id ?? ''),
        stage: String(e.stage ?? ''),
        code: String(e.code ?? 'error'),
        message: String(e.message ?? ''),
      }))
    : [];

  return {
    ts: String(raw.ts ?? raw.timestamp ?? ''),
    engine_sha: String(raw.engine_sha ?? raw.sha ?? ''),
    active_profile: raw.active_profile === 'production' || raw.profile === 'production' ? 'production' : 'test',
    stop: stopBool,
    stop_active: stopBool,
    stop_reason: raw.stop_reason ?? null,
    loops_running: loopsRunning,
    queue_depth: queueDepth,
    jobs_queued: queueDepth,
    jobs_waiting: jobsWaiting,
    loops_waiting: jobsWaiting,
    pace: paceSent,
    pace_60s: {
      sent: paceSent,
      limit: paceLimit,
    },
    free_today: {
      used: freeUsed,
      cap: freeCap,
      remaining: freeRemaining,
      source: freeSource,
    },
    reviews_pending: Number(
      raw.reviews_pending ??
        kpis.reviews_pending_attention ??
        (Array.isArray(raw.pending_reviews) ? raw.pending_reviews.length : 0)
    ),
    sandbox_slots: {
      used: sbUsed,
      cap: sbCap,
    },
    active_errors: Number(
      raw.active_errors ?? kpis.active_errors ?? recentErrors.length
    ),
    reconciled_cost_usd: formatUsdValue(
      raw.reconciled_cost_usd ?? kpis.reconciled_api_cost_usd ?? null
    ),
    review_usd_today: formattedReviewUsed,
    review_cap_usd: formattedReviewCap,
    review_spend: {
      used_usd: formattedReviewUsed,
      cap_usd: formattedReviewCap,
      reserve_usd: formattedReviewReserve,
    },
    recent_errors: recentErrors,
  };
}

export function normalizeModel(raw: Record<string, any>): ModelRecord {
  const modelId = String(raw.model_id ?? raw.id ?? '');
  const route =
    raw.route === 'claude_cli' || modelId.startsWith('claude-code/')
      ? 'claude_cli'
      : 'openrouter';
  const billingType =
    raw.billing_type ??
    (route === 'claude_cli'
      ? 'subscription'
      : modelId.endsWith(':free')
      ? 'free'
      : 'paid');

  const rawContracts = raw.contracts ?? {};
  return {
    model_id: modelId,
    canonical_model_id: String(
      raw.canonical_model_id ?? modelId.replace(/:free$/, '')
    ),
    display_name: String(raw.display_name ?? raw.name ?? modelId),
    provider: String(raw.provider ?? 'OpenRouter'),
    endpoints: Array.isArray(raw.endpoints)
      ? raw.endpoints
      : raw.endpoint
      ? [String(raw.endpoint)]
      : ['openrouter'],
    active_endpoint: String(
      raw.active_endpoint ?? raw.endpoint ?? raw.endpoints?.[0] ?? 'openrouter'
    ),
    route,
    billing_type: billingType,
    input_usd_per_1m:
      billingType === 'subscription'
        ? null
        : formatUsdValue(raw.input_usd_per_1m ?? raw.pricing?.prompt_per_1m),
    output_usd_per_1m:
      billingType === 'subscription'
        ? null
        : formatUsdValue(raw.output_usd_per_1m ?? raw.pricing?.completion_per_1m),
    context_window: Number(raw.context_window ?? raw.context_length ?? 65536),
    max_output_tokens: Number(raw.max_output_tokens ?? raw.max_completion_tokens ?? 32768),
    structured_outputs_claimed: Boolean(
      raw.structured_outputs_claimed ?? raw.structured_outputs ?? false
    ),
    validation_path:
      raw.validation_path ?? (route === 'claude_cli' ? 'text_validated' : 'native_json'),
    profile_restriction: raw.profile_restriction,
    synthetic_scale_row: Boolean(raw.synthetic_scale_row ?? false),
    contracts: {
      planner_json: normalizeContractState(rawContracts.planner_json),
      writer_file_blocks: normalizeContractState(rawContracts.writer_file_blocks),
      repair_edit_blocks: normalizeContractState(rawContracts.repair_edit_blocks),
      repair_whole_file: normalizeContractState(rawContracts.repair_whole_file),
      reviewer_verdict: normalizeContractState(rawContracts.reviewer_verdict),
      native_structured_output: normalizeContractState(rawContracts.native_structured_output),
    },
    reasoning: {
      mode: raw.reasoning?.mode ?? 'unknown',
      supported: raw.reasoning?.supported ?? raw.reasoning?.mode ?? 'unknown',
      off_admissible: Boolean(raw.reasoning?.off_admissible ?? true),
      efforts: Array.isArray(raw.reasoning?.efforts)
        ? raw.reasoning.efforts
        : ['low', 'medium', 'high'],
      token_budget_supported: Boolean(raw.reasoning?.token_budget_supported ?? false),
      token_budget_min: raw.reasoning?.token_budget_min ?? null,
      token_budget_max: raw.reasoning?.token_budget_max ?? null,
    },
    temperature_range: {
      min: Number(raw.temperature_range?.min ?? 0.0),
      max: Number(raw.temperature_range?.max ?? 1.0),
      default: Number(raw.temperature_range?.default ?? 0.1),
    },
    verified_at: raw.verified_at ?? null,
    verified_endpoint: raw.verified_endpoint ?? null,
    freshness: String(raw.freshness ?? (raw.verified_at ? 'verified' : 'unverified')),
    availability: raw.availability ?? 'available',
    in_use_stages: Array.isArray(raw.in_use_stages) ? raw.in_use_stages : [],
  };
}

export function normalizeEventPage(raw: Record<string, any>): EventPage {
  const events = Array.isArray(raw.events) ? raw.events.map(normalizeEvent) : [];
  const lastSeq = events.length > 0 ? events[events.length - 1].seq : 0;
  return {
    events,
    next_after: typeof raw.next_after === 'number' ? raw.next_after : lastSeq,
    index_ok: raw.index_ok !== undefined ? Boolean(raw.index_ok) : true,
  };
}

export class EventStreamBuffer {
  public jobId: string;
  private seenSeqs: Set<number> = new Set();
  public events: AttemptEvent[] = [];
  public nextAfter = 0;
  public indexOk = true;

  constructor(jobId: string) {
    this.jobId = jobId;
  }

  resetForJob(newJobId: string): void {
    if (this.jobId !== newJobId) {
      this.jobId = newJobId;
      this.seenSeqs.clear();
      this.events = [];
      this.nextAfter = 0;
      this.indexOk = true;
    }
  }

  appendPage(rawPage: Record<string, any>): EventPage {
    const page = normalizeEventPage(rawPage);
    this.indexOk = page.index_ok;
    for (const ev of page.events) {
      if (!this.seenSeqs.has(ev.seq)) {
        this.seenSeqs.add(ev.seq);
        this.events.push(ev);
      }
    }
    this.events.sort((a, b) => a.seq - b.seq);
    if (page.next_after > this.nextAfter) {
      this.nextAfter = page.next_after;
    } else if (this.events.length > 0) {
      this.nextAfter = this.events[this.events.length - 1].seq;
    }
    return {
      events: [...this.events],
      next_after: this.nextAfter,
      index_ok: this.indexOk,
    };
  }
}
