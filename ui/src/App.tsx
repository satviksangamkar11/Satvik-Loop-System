import React, { useState, useEffect, useMemo, useCallback, useRef } from 'react';
import {
  Activity,
  Archive,
  Cpu,
  Database,
  LayoutDashboard,
  Menu,
  Octagon,
  Play,
  RefreshCw,
  Server,
  ShieldCheck,
  Sliders,
  Wifi,
  WifiOff,
  X,
} from 'lucide-react';

import { ApiHttpError, SatvikApiClient } from './api/client';
import { IdempotencyManager } from './api/idempotency';
import { EventStreamBuffer, normalizeJob, normalizeOverview } from './api/normalizer';
import type {
  AttemptEvent,
  DataMode,
  JobRecord,
  ModelRecord,
  NavPage,
  NormalizedOverview,
  ProfileName,
  ScenarioId,
  SemanticContract,
  StageConfigRow,
  StageKey,
} from './api/types';
import {
  AddModelCatalogModal,
  ConflictModalState,
  ProbeConfirmModal,
  ProbeModalState,
  RevisionConflictModal,
  StopConfirmModal,
} from './components/ShellModals';
import {
  INITIAL_CLAUDE_CLI_MODELS,
  INITIAL_OPENROUTER_MODELS,
  INITIAL_STAGES_BY_PROFILE,
  INITIAL_WIRE_JOBS,
  SCENARIO_PRESETS,
} from './mocks/mockData';
import { DeterministicMockTransport } from './mocks/mockTransport';
import { DashboardPage } from './pages/DashboardPage';
import { LiveLoopsPage } from './pages/LiveLoopsPage';
import { ModelsPage } from './pages/ModelsPage';
import { ResultsPage } from './pages/ResultsPage';
import { RunJobsPage } from './pages/RunJobsPage';
import { StageSettingsPage } from './pages/StageSettingsPage';
import { SystemPage } from './pages/SystemPage';

const INITIAL_JOBS_NORMALIZED: JobRecord[] = INITIAL_WIRE_JOBS.map(normalizeJob);

const INITIAL_OVERVIEW_NORMALIZED: NormalizedOverview = normalizeOverview({
  ts: '2026-11-03T18:42:10Z',
  engine_sha: 'b6569d86e2321a8fd544e8d3705a84dbc28b349c',
  active_profile: 'test',
  stop: false,
  stop_reason: null,
  loops_running: 2,
  queue_depth: 0,
  jobs_waiting: 1,
  pace: 9,
  pace_limit: 15,
  review_usd_today: 0.184,
  review_cap_usd: 2.5,
  free_today: {
    used: 58,
    cap: 200,
    remaining: 142,
    source: 'live_openrouter_key_limit',
  },
  reviews_pending: 1,
  sandbox_slots: { used: 2, cap: 4 },
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
});

// Clean neutral overview when entering LIVE mode before backend responds (zero demo data leakage!)
const EMPTY_LIVE_OVERVIEW: NormalizedOverview = normalizeOverview({
  ts: '',
  engine_sha: '',
  active_profile: 'test',
  stop: false,
  loops_running: 0,
  queue_depth: 0,
  jobs_waiting: 0,
  pace: 0,
  reviews_pending: 0,
  active_errors: 0,
  recent_errors: [],
});

export default function App() {
  const [activePage, setActivePage] = useState<NavPage>('/dashboard');
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [lightTheme, setLightTheme] = useState(false);
  const [useUtcTime, setUseUtcTime] = useState(false);

  const initialMode: DataMode =
    typeof window !== 'undefined' && (window as any).__SATVIK_DATA_MODE__ === 'live'
      ? 'live'
      : 'mock';
  const [dataMode, setDataMode] = useState<DataMode>(initialMode);
  const dataModeRef = useRef<DataMode>(initialMode);
  dataModeRef.current = dataMode;

  const [liveConnected, setLiveConnected] = useState<boolean>(false);
  const [liveLoading, setLiveLoading] = useState<boolean>(initialMode === 'live');
  const [liveErrorMsg, setLiveErrorMsg] = useState<string | null>(null);
  const [lastVerifiedRefresh, setLastVerifiedRefresh] = useState<string>(
    initialMode === 'live' ? '' : '2026-11-03T18:42:10Z'
  );
  const [activeScenario, setActiveScenario] = useState<ScenarioId>('default_all_fixtures');
  const [indexOk, setIndexOk] = useState<boolean>(true);
  const [pendingAction, setPendingAction] = useState<string | null>(null);

  const [authoritativeProfile, setAuthoritativeProfile] = useState<ProfileName>('test');
  const [stageViewProfile, setStageViewProfile] = useState<ProfileName>('test');

  const [stopActive, setStopActive] = useState<boolean>(false);
  const [stopModalOpen, setStopModalOpen] = useState<boolean>(false);

  // Core Data Stores: If starting in LIVE mode, start empty (NEVER pre-populate with mock demo data!)
  const [overview, setOverview] = useState<NormalizedOverview>(
    initialMode === 'live' ? EMPTY_LIVE_OVERVIEW : INITIAL_OVERVIEW_NORMALIZED
  );
  const [jobs, setJobs] = useState<JobRecord[]>(
    initialMode === 'live' ? [] : INITIAL_JOBS_NORMALIZED
  );
  const [openRouterModels, setOpenRouterModels] = useState<ModelRecord[]>(
    initialMode === 'live' ? [] : INITIAL_OPENROUTER_MODELS
  );
  const [claudeCliModels, setClaudeCliModels] = useState<ModelRecord[]>(
    initialMode === 'live' ? [] : INITIAL_CLAUDE_CLI_MODELS
  );
  const [stagesByProfile, setStagesByProfile] = useState<Record<ProfileName, StageConfigRow[]>>(
    initialMode === 'live'
      ? { test: [], production: [] }
      : INITIAL_STAGES_BY_PROFILE
  );
  const [stageRevision, setStageRevision] = useState<string>(
    initialMode === 'live' ? '' : 'rev-stages-20261103-07'
  );
  const [systemHealth, setSystemHealth] = useState<Record<string, any> | null>(null);

  // System Limits State
  const [sandboxCap, setSandboxCap] = useState<number>(4);
  const [reviewCeilingUsd, setReviewCeilingUsd] = useState<string>('2.5000');
  const [limitsRevision, setLimitsRevision] = useState<string | null>(null);

  const mockTransportRef = useRef<DeterministicMockTransport>(new DeterministicMockTransport());
  const idempotencyMgrRef = useRef<IdempotencyManager>(
    new IdempotencyManager({
      'queue:jobs/tl_parser_strict.json:rerun=false': '7f8a92c1-4b1e-4c9a-9d2e-112233445566',
    })
  );
  const [idempotencySnapshot, setIdempotencySnapshot] = useState<Record<string, string>>(
    idempotencyMgrRef.current.snapshot()
  );

  const getOrCreateIdempotencyKey = useCallback((intentSig: string, forceRotate = false) => {
    const key = idempotencyMgrRef.current.getOrCreateKey(intentSig, forceRotate);
    setIdempotencySnapshot(idempotencyMgrRef.current.snapshot());
    return key;
  }, []);

  const apiClient = useMemo(
    () =>
      new SatvikApiClient({
        getMode: () => dataModeRef.current,
        mockHandler: mockTransportRef.current.handleRequest,
      }),
    []
  );

  const eventBufferRef = useRef<EventStreamBuffer>(new EventStreamBuffer('job-tl-parser-042'));

  const [apiNotice, setApiNotice] = useState<{
    type: 'info' | 'success' | 'warning' | 'error';
    code: string;
    message: string;
    idempotencyKey?: string;
    endpoint?: string;
  } | null>(null);

  const [copiedToken, setCopiedToken] = useState<string | null>(null);

  const [selectedLoopJobId, setSelectedLoopJobId] = useState<string>('job-tl-parser-042');
  const [selectedResultJobId, setSelectedResultJobId] = useState<string>('job-schema-guard-039');
  const [selectedModelId, setSelectedModelId] = useState<string | null>('deepseek/deepseek-r1:free');
  const [selectedStageKey, setSelectedStageKey] = useState<StageKey>('writer_a_write');

  // Page 2 (Run Jobs) State
  const [runEntryMode, setRunEntryMode] = useState<'existing_path' | 'planner_request'>(
    'existing_path'
  );
  const [jobPathInput, setJobPathInput] = useState<string>('jobs/tl_parser_strict.json');
  const [jobValidated, setJobValidated] = useState<boolean>(true);
  const [budgetValidated, setBudgetValidated] = useState<boolean>(true);
  const [authorizedPaidBudget, setAuthorizedPaidBudget] = useState<string>('0.00');
  const [workerCountToStart, setWorkerCountToStart] = useState<number>(2);
  const [planJobName, setPlanJobName] = useState<string>('strict_frame_decoder');
  const [planPrompt, setPlanPrompt] = useState<string>(
    'Synthesize deterministic parser & acceptance tests for length-prefixed UTF-8 frame decoding with zero buffer mutation.'
  );
  const [planAllowedFiles, setPlanAllowedFiles] = useState<string>(
    'src/tl_parser/frame.py\nsrc/tl_parser/header.py\ntests/test_tl_parser.py'
  );
  const [planCostAuthorized, setPlanCostAuthorized] = useState<boolean>(false);
  const [generatedPlan, setGeneratedPlan] = useState<{
    plan_id: string;
    job_sha256: string;
    known: string[];
    assumed: Array<{ id: string; text: string; checked: boolean }>;
  } | null>(null);

  // Page 3 (Live Loops) State
  const [loopFilter, setLoopFilter] = useState<string>('all');
  const [loopViewMode, setLoopViewMode] = useState<'timeline_list' | 'flow_diagram'>(
    'timeline_list'
  );
  const [loopInspectorTab, setLoopInspectorTab] = useState<
    'tests' | 'reviews' | 'diff' | 'lineage' | 'budget' | 'artifacts'
  >('tests');
  const [futureStageDrawerOpen, setFutureStageDrawerOpen] = useState<boolean>(false);
  const [futureStageTarget, setFutureStageTarget] = useState<StageKey>('writer_b_repair');
  const [futureStageEffort, setFutureStageEffort] = useState<'low' | 'medium' | 'high'>('high');

  // Page 4 (Models) State
  const [modelSearch, setModelSearch] = useState<string>('');
  const [modelBillingFilter, setModelBillingFilter] = useState<string>('all');
  const [modelContractFilter, setModelContractFilter] = useState<string>('all');
  const [synthetic20ScaleMode, setSynthetic20ScaleMode] = useState<boolean>(false);
  const [addModelModalOpen, setAddModelModalOpen] = useState<boolean>(false);
  const [catalogQuery, setCatalogQuery] = useState<string>('llama');
  const [catalogResults, setCatalogResults] = useState<ModelRecord[]>([]);
  const [probeConfirmModal, setProbeConfirmModal] = useState<ProbeModalState | null>(null);

  // Page 5 (Stage Settings) State
  const [stageConflictModal, setStageConflictModal] = useState<ConflictModalState | null>(null);

  // Page 6 (Results) State
  const [resultsSearch, setResultsSearch] = useState<string>('');
  const [resultsOutcomeFilter, setResultsOutcomeFilter] = useState<string>('all');
  const [resultsTab, setResultsTab] = useState<
    'assertions' | 'reviews' | 'diff' | 'lineage' | 'calls' | 'artifacts'
  >('reviews');

  // Page 7 (System) State
  const [showRev4Reference, setShowRev4Reference] = useState<boolean>(false);

  // ============================================================================
  // HELPERS & ERROR SURFACING
  // ============================================================================

  const handleCopyText = useCallback((text: string) => {
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).catch(() => {});
      }
      const el = document.createElement('textarea');
      el.value = text;
      document.body.appendChild(el);
      el.select();
      document.execCommand('copy');
      document.body.removeChild(el);
      setCopiedToken(text);
      setTimeout(() => setCopiedToken(null), 1800);
    } catch {
      setCopiedToken(text);
      setTimeout(() => setCopiedToken(null), 1800);
    }
  }, []);

  const formatTimestamp = useCallback(
    (isoUtc: string | null | undefined): string => {
      if (!isoUtc) return 'Unknown';
      try {
        const d = new Date(isoUtc);
        if (isNaN(d.getTime())) return isoUtc;
        if (useUtcTime) {
          return d.toISOString().replace('T', ' ').replace('Z', ' UTC');
        }
        return d.toLocaleString(undefined, {
          month: 'short',
          day: '2-digit',
          hour: '2-digit',
          minute: '2-digit',
          second: '2-digit',
          hour12: false,
        });
      } catch {
        return isoUtc;
      }
    },
    [useUtcTime]
  );

  const surfaceApiError = useCallback((err: unknown, fallbackEndpoint?: string) => {
    if (err instanceof ApiHttpError) {
      if (err.status === 503 && dataModeRef.current === 'live') {
        setLiveConnected(false);
        setLiveErrorMsg(err.message);
      }
      setApiNotice({
        type: 'error',
        code: `${err.status}-${err.code}`,
        endpoint: err.endpoint,
        idempotencyKey: err.idempotencyKey,
        message: err.message,
      });
      return;
    }
    setApiNotice({
      type: 'error',
      code: 'unexpected-error',
      endpoint: fallbackEndpoint,
      message: err instanceof Error ? err.message : String(err),
    });
  }, []);

  // ============================================================================
  // AUTHORITATIVE DATA FETCHING FOR ALL 7 PAGES (Blocker #1 Repair)
  // ============================================================================

  const fetchOverviewAndJobs = useCallback(
    async (signal?: AbortSignal, showToast = false) => {
      try {
        const [{ overview: normOv }, jobsList] = await Promise.all([
          apiClient.getOverview(signal),
          apiClient.getJobs(signal),
        ]);
        setOverview(normOv);
        setJobs(jobsList);
        setStopActive(normOv.stop_active);
        setAuthoritativeProfile(normOv.active_profile);
        setLastVerifiedRefresh(normOv.ts || new Date().toISOString());
        if (dataModeRef.current === 'live') {
          setLiveConnected(true);
          setLiveLoading(false);
          setLiveErrorMsg(null);
        }
        if (showToast) {
          setApiNotice({
            type: 'info',
            code: 'refresh-ok',
            endpoint: 'GET /api/overview, GET /api/jobs',
            message: `Refreshed overview & jobs at ${formatTimestamp(
              normOv.ts || new Date().toISOString()
            )} (zero billable probes).`,
          });
        }
      } catch (err) {
        if ((err as any)?.name === 'AbortError') return;
        setLiveLoading(false);
        surfaceApiError(err, 'GET /api/overview');
      }
    },
    [apiClient, formatTimestamp, surfaceApiError]
  );

  const fetchModelsCatalog = useCallback(
    async (signal?: AbortSignal) => {
      try {
        const res = await apiClient.getModels(signal);
        setOpenRouterModels(res.openrouter_models);
        setClaudeCliModels(res.claude_cli_models);
      } catch (err) {
        if ((err as any)?.name === 'AbortError') return;
        surfaceApiError(err, 'GET /api/models');
      }
    },
    [apiClient, surfaceApiError]
  );

  const fetchStagesForProfile = useCallback(
    async (prof: ProfileName, signal?: AbortSignal) => {
      try {
        const res = await apiClient.getStages(prof, signal);
        setStageRevision(res.revision);
        setAuthoritativeProfile(res.authoritative_runtime_profile);
        setStagesByProfile((prev) => ({ ...prev, [prof]: res.stages }));
      } catch (err) {
        if ((err as any)?.name === 'AbortError') return;
        surfaceApiError(err, `GET /api/stages?profile=${prof}`);
      }
    },
    [apiClient, surfaceApiError]
  );

  const fetchResultDetailAndDiff = useCallback(
    async (jobId: string, signal?: AbortSignal) => {
      if (!jobId) return;
      try {
        const [detail, diffRes] = await Promise.all([
          apiClient.getResult(jobId, signal),
          apiClient.getResultDiff(jobId, signal).catch(() => ({ job_id: jobId, diff_text: null })),
        ]);
        const mergedDetail: JobRecord = {
          ...detail,
          diff_text: diffRes.diff_text ?? detail.diff_text,
        };
        setJobs((prev) => {
          const exists = prev.some((j) => j.job_id === jobId);
          return exists
            ? prev.map((j) => (j.job_id === jobId ? mergedDetail : j))
            : [mergedDetail, ...prev];
        });
      } catch (err) {
        if ((err as any)?.name === 'AbortError') return;
        surfaceApiError(err, `GET /api/results/${jobId}`);
      }
    },
    [apiClient, surfaceApiError]
  );

  const fetchSystemHealthAndLimits = useCallback(
    async (signal?: AbortSignal, showToast = false) => {
      try {
        const [healthRes, limitsRes] = await Promise.all([
          apiClient.getSystemHealth(signal),
          apiClient.getSystemLimits(signal),
        ]);
        setSystemHealth(healthRes);
        if (typeof healthRes?.stop?.on === 'boolean') {
          setStopActive(healthRes.stop.on);
        } else if (typeof healthRes?.stop === 'boolean') {
          setStopActive(healthRes.stop);
        }
        setSandboxCap(limitsRes.sandbox_slots);
        setReviewCeilingUsd(limitsRes.review_spend_cap_usd);
        setLimitsRevision(limitsRes.revision);
        if (dataModeRef.current === 'live') {
          setLiveConnected(true);
          setLiveLoading(false);
          setLiveErrorMsg(null);
        }
        if (showToast) {
          setApiNotice({
            type: 'info',
            code: 'system-health-refreshed',
            endpoint: 'GET /api/system/health, GET /api/system/limits',
            message: 'Refreshed system health and limits.',
          });
        }
      } catch (err) {
        if ((err as any)?.name === 'AbortError') return;
        setLiveLoading(false);
        surfaceApiError(err, 'GET /api/system/health');
      }
    },
    [apiClient, surfaceApiError]
  );

  // Immediate fetch (0ms) + 5s Overview & Jobs polling
  useEffect(() => {
    const controller = new AbortController();
    fetchOverviewAndJobs(controller.signal, false);
    fetchModelsCatalog(controller.signal);
    fetchStagesForProfile(stageViewProfile, controller.signal);
    fetchSystemHealthAndLimits(controller.signal, false);

    const timer = setInterval(() => {
      fetchOverviewAndJobs(controller.signal, false);
    }, 5000);
    return () => {
      controller.abort();
      clearInterval(timer);
    };
  }, [
    fetchOverviewAndJobs,
    fetchModelsCatalog,
    fetchStagesForProfile,
    fetchSystemHealthAndLimits,
    stageViewProfile,
    dataMode,
  ]);

  // Page-specific authoritative fetch when navigating between pages or changing selected result job
  useEffect(() => {
    const controller = new AbortController();
    if (activePage === '/models') {
      fetchModelsCatalog(controller.signal);
    } else if (activePage === '/stages' || activePage === '/jobs/new') {
      fetchStagesForProfile(stageViewProfile, controller.signal);
    } else if (activePage === '/results' && selectedResultJobId) {
      fetchResultDetailAndDiff(selectedResultJobId, controller.signal);
    } else if (activePage === '/system') {
      fetchSystemHealthAndLimits(controller.signal, false);
    }
    return () => controller.abort();
  }, [
    activePage,
    selectedResultJobId,
    stageViewProfile,
    fetchModelsCatalog,
    fetchStagesForProfile,
    fetchResultDetailAndDiff,
    fetchSystemHealthAndLimits,
  ]);

  // Immediate fetch (0ms) + 3s Selected Loop & 2s Incremental Events (?after=<seq>) polling on /loops
  useEffect(() => {
    if (activePage !== '/loops' || !selectedLoopJobId) return;
    eventBufferRef.current.resetForJob(selectedLoopJobId);
    const controller = new AbortController();

    const pollLoop = async () => {
      try {
        const updatedLoop = await apiClient.getLoop(selectedLoopJobId, controller.signal);
        setJobs((prev) =>
          prev.map((j) => (j.job_id === updatedLoop.job_id ? updatedLoop : j))
        );
      } catch {
        // Handled by main poll
      }
    };

    const pollEvents = async () => {
      try {
        const page = await apiClient.getLoopEvents(
          selectedLoopJobId,
          eventBufferRef.current.nextAfter,
          controller.signal
        );
        const merged = eventBufferRef.current.appendPage(page);
        setIndexOk(merged.index_ok);
        if (merged.events.length > 0) {
          setJobs((prev) =>
            prev.map((j) =>
              j.job_id === selectedLoopJobId ? { ...j, events: merged.events } : j
            )
          );
        }
      } catch {
        // Ignore background poll abort
      }
    };

    pollLoop();
    pollEvents();
    const loopTimer = setInterval(pollLoop, 3000);
    const eventsTimer = setInterval(pollEvents, 2000);

    return () => {
      controller.abort();
      clearInterval(loopTimer);
      clearInterval(eventsTimer);
    };
  }, [activePage, selectedLoopJobId, apiClient, dataMode]);

  // Mode Switcher: Clears fixture state immediately when entering LIVE mode so demo data never leaks!
  const handleSwitchDataMode = useCallback(
    async (nextMode: DataMode) => {
      setDataMode(nextMode);
      dataModeRef.current = nextMode;
      if (nextMode === 'live') {
        setLiveLoading(true);
        setLiveConnected(false);
        setOverview(EMPTY_LIVE_OVERVIEW);
        setJobs([]);
        setOpenRouterModels([]);
        setClaudeCliModels([]);
        setStagesByProfile({ test: [], production: [] });
        setSystemHealth(null);
        await Promise.all([
          fetchOverviewAndJobs(undefined, true),
          fetchModelsCatalog(undefined),
          fetchStagesForProfile(stageViewProfile, undefined),
          fetchSystemHealthAndLimits(undefined, false),
        ]);
      } else {
        setLiveLoading(false);
        setLiveErrorMsg(null);
        await Promise.all([
          fetchOverviewAndJobs(undefined, false),
          fetchModelsCatalog(undefined),
          fetchStagesForProfile(stageViewProfile, undefined),
          fetchSystemHealthAndLimits(undefined, false),
        ]);
        setApiNotice({
          type: 'info',
          code: 'mock-mode-active',
          message:
            'Switched to deterministic MOCK transport backed by the 12 contract-shaped fixtures.',
        });
      }
    },
    [
      fetchModelsCatalog,
      fetchOverviewAndJobs,
      fetchStagesForProfile,
      fetchSystemHealthAndLimits,
      stageViewProfile,
    ]
  );

  // ============================================================================
  // 16 MANDATORY SCENARIO PRESETS
  // ============================================================================

  const applyScenarioPreset = useCallback(
    async (scenario: ScenarioId) => {
      setActiveScenario(scenario);
      setDataMode('mock');
      dataModeRef.current = 'mock';
      setLiveLoading(false);
      setLiveErrorMsg(null);

      mockTransportRef.current = new DeterministicMockTransport();
      const meta = SCENARIO_PRESETS.find((s) => s.id === scenario);
      if (meta?.targetPage) {
        setActivePage(meta.targetPage);
      }

      switch (scenario) {
        case 's1_empty_startup':
          mockTransportRef.current.syncExternalState({
            jobs: [],
            openRouterModels: [],
            freeUsed: 0,
            paceLast60s: 0,
            sandboxUsed: 0,
          });
          setOpenRouterModels([]);
          break;
        case 's2_waiting_slot_to_start':
          setSelectedLoopJobId('job-tl-parser-042');
          break;
        case 's3_passed_merge_pending_vs_merged':
          setSelectedResultJobId('job-auth-token-019');
          setResultsTab('artifacts');
          break;
        case 's4_review_open_restored_green':
          setSelectedResultJobId('job-schema-guard-039');
          setResultsTab('reviews');
          break;
        case 's5_quota_refusal_pace_exhausted':
          mockTransportRef.current.syncExternalState({
            freeUsed: 200,
            paceLast60s: 15,
          });
          break;
        case 's6_soft_stop_active':
          mockTransportRef.current.syncExternalState({ stopActive: true });
          break;
        case 's7_untriggered_vs_fallback_write':
          setSelectedLoopJobId('job-fallback-write-demo-031');
          break;
        case 's8_disconnected_or_stale_index':
          mockTransportRef.current.syncExternalState({ indexOk: false });
          setIndexOk(false);
          break;
        case 's9_concurrent_same_model_diff_effort':
          setSelectedLoopJobId('job-ast-rewriter-051');
          break;
        case 's10_409_revision_conflict':
          mockTransportRef.current.syncExternalState({ simulate409Conflict: true });
          setStageConflictModal({
            clientRevision: 'rev-stages-20261103-06',
            serverRevision: 'rev-stages-20261103-09',
            context: 'global_stages',
          });
          break;
        case 's11_model_capability_restrictions':
          setSelectedModelId('deepseek/deepseek-r1:free');
          break;
        case 's12_archive_fail_and_merge_conflict':
          setSelectedResultJobId('job-ledger-sync-044');
          setResultsTab('artifacts');
          break;
        case 's13_idempotency_and_rerun':
          setJobPathInput('jobs/auth_token_validator.json');
          break;
        case 's14_paid_planner_probe_gate':
          setRunEntryMode('planner_request');
          break;
        case 's15_model_pool_removal_refusal':
          setSelectedModelId('deepseek/deepseek-chat-v3-0324:free');
          break;
        case 's16_system_limits_c08_gated':
          mockTransportRef.current.syncExternalState({ limitsRevision: null });
          setLimitsRevision(null);
          break;
        default:
          setOpenRouterModels(INITIAL_OPENROUTER_MODELS);
          break;
      }

      if (scenario !== 's1_empty_startup') {
        setOpenRouterModels(mockTransportRef.current.state.openRouterModels);
      }
      await fetchOverviewAndJobs(undefined, false);
      setApiNotice({
        type: 'info',
        code: scenario,
        message: `${meta?.shortLabel}: ${meta?.description}`,
      });
    },
    [fetchOverviewAndJobs]
  );

  // ============================================================================
  // REAL API MUTATION HANDLERS
  // ============================================================================

  const handleConfirmSoftStop = useCallback(
    async (nextStopOn: boolean) => {
      setPendingAction('stop');
      const idemKey = getOrCreateIdempotencyKey(`system:stop:${nextStopOn}`, false);
      try {
        const res = await apiClient.setGlobalStop(nextStopOn, idemKey);
        setStopActive(res.stop_active);
        setStopModalOpen(false);
        idempotencyMgrRef.current.clearIntent(`system:stop:${nextStopOn}`);
        setApiNotice({
          type: res.stop_active ? 'warning' : 'success',
          code: res.stop_active ? 'soft-stop-enabled' : 'soft-stop-cleared',
          endpoint: 'POST /api/system/stop',
          idempotencyKey: idemKey,
          message: res.message,
        });
        await fetchOverviewAndJobs(undefined, false);
      } catch (err) {
        surfaceApiError(err, 'POST /api/system/stop');
      } finally {
        setPendingAction(null);
      }
    },
    [apiClient, fetchOverviewAndJobs, getOrCreateIdempotencyKey, surfaceApiError]
  );

  const handleValidateJobPath = useCallback(async () => {
    setPendingAction('validate-job');
    const idemKey = getOrCreateIdempotencyKey(`validate:${jobPathInput}`, false);
    try {
      const res = await apiClient.validateJob({ job_path: jobPathInput }, idemKey);
      setJobValidated(res.valid);
      setApiNotice({
        type: 'success',
        code: 'job-schema-valid',
        endpoint: 'POST /api/jobs/validate',
        idempotencyKey: idemKey,
        message: `Validated "${res.job_path}" (problems: ${res.problems.length}).${
          res.snapshot_sha256 ? ` Snapshot SHA-256: ${res.snapshot_sha256.slice(0, 16)}…` : ''
        }`,
      });
    } catch (err) {
      setJobValidated(false);
      surfaceApiError(err, 'POST /api/jobs/validate');
    } finally {
      setPendingAction(null);
    }
  }, [apiClient, getOrCreateIdempotencyKey, jobPathInput, surfaceApiError]);

  const handleValidateBudget = useCallback(async () => {
    setPendingAction('validate-budget');
    const idemKey = getOrCreateIdempotencyKey(
      `budget:${authorizedPaidBudget}:${authoritativeProfile}`,
      false
    );
    try {
      const res = await apiClient.validateBudget(
        { paid_budget_usd: authorizedPaidBudget, profile: authoritativeProfile },
        idemKey
      );
      setBudgetValidated(res.valid);
      setApiNotice({
        type: 'success',
        code: 'budget-valid',
        endpoint: 'POST /api/budget/validate',
        idempotencyKey: idemKey,
        message: res.message,
      });
    } catch (err) {
      setBudgetValidated(false);
      surfaceApiError(err, 'POST /api/budget/validate');
    } finally {
      setPendingAction(null);
    }
  }, [
    apiClient,
    authoritativeProfile,
    authorizedPaidBudget,
    getOrCreateIdempotencyKey,
    surfaceApiError,
  ]);

  const handleQueueJob = useCallback(
    async (isRerun: boolean) => {
      setPendingAction(isRerun ? 'rerun-job' : 'queue-job');
      const intentSig = `queue:${jobPathInput}:rerun=${isRerun}`;
      const idemKey = getOrCreateIdempotencyKey(intentSig, isRerun);
      try {
        const res = await apiClient.queueJob(
          {
            job_path: jobPathInput,
            rerun: isRerun,
            stage_revision: stageRevision,
            paid_budget_usd: authorizedPaidBudget,
            stages: stagesByProfile[authoritativeProfile],
          },
          idemKey
        );
        setJobs((prev) => [
          res.queued_job,
          ...prev.filter((j) => j.job_id !== res.queued_job.job_id),
        ]);
        setApiNotice({
          type: 'success',
          code: isRerun ? 'queued-rerun' : 'queued-job',
          endpoint: `POST /api/jobs (rerun=${isRerun})`,
          idempotencyKey: idemKey,
          message: res.message,
        });
        await fetchOverviewAndJobs(undefined, false);
      } catch (err) {
        surfaceApiError(err, 'POST /api/jobs');
      } finally {
        setPendingAction(null);
      }
    },
    [
      apiClient,
      authoritativeProfile,
      authorizedPaidBudget,
      fetchOverviewAndJobs,
      getOrCreateIdempotencyKey,
      jobPathInput,
      stageRevision,
      stagesByProfile,
      surfaceApiError,
    ]
  );

  const handleStartWorkers = useCallback(async () => {
    setPendingAction('start-workers');
    const idemKey = getOrCreateIdempotencyKey(`loops:start:${workerCountToStart}`, false);
    try {
      const res = await apiClient.startWorkers(workerCountToStart, idemKey);
      idempotencyMgrRef.current.clearIntent(`loops:start:${workerCountToStart}`);
      setApiNotice({
        type: 'success',
        code: 'workers-started',
        endpoint: `POST /api/loops/start { started: ${res.started} }`,
        idempotencyKey: idemKey,
        message: res.message,
      });
    } catch (err) {
      surfaceApiError(err, 'POST /api/loops/start');
    } finally {
      setPendingAction(null);
    }
  }, [apiClient, getOrCreateIdempotencyKey, surfaceApiError, workerCountToStart]);

  const handleCreatePlan = useCallback(async () => {
    setPendingAction('create-plan');
    const idemKey = getOrCreateIdempotencyKey(`plan:${planJobName}:${authoritativeProfile}`, false);
    const filesList = planAllowedFiles
      .split('\n')
      .map((s) => s.trim())
      .filter(Boolean);
    try {
      const res = await apiClient.createPlan(
        {
          job_name: planJobName,
          prompt: planPrompt,
          allowed_files: filesList,
          cost_authorized: planCostAuthorized,
          profile: authoritativeProfile,
        },
        idemKey
      );
      setGeneratedPlan(res);
      setApiNotice({
        type: 'success',
        code: 'plan-drafted',
        endpoint: 'POST /api/plans -> GET /api/plans/{id}',
        idempotencyKey: idemKey,
        message: `Synthesized PLAN/SPEC (${res.plan_id}). Tick every ASSUMED item before Approve & Start.`,
      });
    } catch (err) {
      surfaceApiError(err, 'POST /api/plans');
    } finally {
      setPendingAction(null);
    }
  }, [
    apiClient,
    authoritativeProfile,
    getOrCreateIdempotencyKey,
    planAllowedFiles,
    planCostAuthorized,
    planJobName,
    planPrompt,
    surfaceApiError,
  ]);

  const handleApprovePlan = useCallback(async () => {
    if (!generatedPlan) return;
    setPendingAction('approve-plan');
    const idemKey = getOrCreateIdempotencyKey(`approve:${generatedPlan.plan_id}`, false);
    try {
      const res = await apiClient.approvePlan(
        generatedPlan.plan_id,
        {
          job_sha256: generatedPlan.job_sha256,
          assumed_approved_ids: generatedPlan.assumed.filter((a) => a.checked).map((a) => a.id),
        },
        idemKey
      );
      setApiNotice({
        type: 'success',
        code: 'plan-approved',
        endpoint: `POST /api/plans/${generatedPlan.plan_id}/approve`,
        idempotencyKey: idemKey,
        message: res.message,
      });
    } catch (err) {
      surfaceApiError(err, `POST /api/plans/${generatedPlan.plan_id}/approve`);
    } finally {
      setPendingAction(null);
    }
  }, [apiClient, generatedPlan, getOrCreateIdempotencyKey, surfaceApiError]);

  const handlePatchFutureStage = useCallback(async () => {
    if (!selectedLoopJobId) return;
    const job = jobs.find((j) => j.job_id === selectedLoopJobId);
    if (!job) return;
    const idemKey = getOrCreateIdempotencyKey(
      `patch-loop:${job.job_id}:${futureStageTarget}:${futureStageEffort}`,
      false
    );
    try {
      const res = await apiClient.patchLoopStages(
        job.job_id,
        {
          stage: futureStageTarget,
          change: { effort: futureStageEffort },
          revision: job.config_revision,
        },
        idemKey
      );
      setApiNotice({
        type: 'success',
        code: 'future-stage-patched',
        endpoint: `PATCH /api/loops/${job.job_id}/stages`,
        idempotencyKey: idemKey,
        message: res.message,
      });
    } catch (err) {
      if (err instanceof ApiHttpError && err.status === 409) {
        setStageConflictModal({
          clientRevision: job.config_revision,
          serverRevision: String(err.details?.server_revision ?? 'rev-stages-20261103-09'),
          context: 'future_job_stage',
        });
      }
      surfaceApiError(err, `PATCH /api/loops/${job.job_id}/stages`);
    }
  }, [
    apiClient,
    futureStageEffort,
    futureStageTarget,
    getOrCreateIdempotencyKey,
    jobs,
    selectedLoopJobId,
    surfaceApiError,
  ]);

  const handleRefreshModels = useCallback(async () => {
    setPendingAction('refresh-models');
    const idemKey = getOrCreateIdempotencyKey('models:refresh', false);
    try {
      const res = await apiClient.refreshModels(idemKey);
      idempotencyMgrRef.current.clearIntent('models:refresh');
      await fetchModelsCatalog();
      setApiNotice({
        type: 'success',
        code: 'prices-refreshed',
        endpoint: 'POST /api/models/refresh',
        idempotencyKey: idemKey,
        message: res.message,
      });
    } catch (err) {
      surfaceApiError(err, 'POST /api/models/refresh');
    } finally {
      setPendingAction(null);
    }
  }, [apiClient, fetchModelsCatalog, getOrCreateIdempotencyKey, surfaceApiError]);

  const handleSearchCatalog = useCallback(async () => {
    setPendingAction('search-catalog');
    try {
      const res = await apiClient.searchCatalogModels(catalogQuery);
      setCatalogResults(res.results);
    } catch (err) {
      surfaceApiError(err, 'GET /api/models/search');
    } finally {
      setPendingAction(null);
    }
  }, [apiClient, catalogQuery, surfaceApiError]);

  const handleAddModel = useCallback(
    async (modelId: string) => {
      setPendingAction('add-model');
      const idemKey = getOrCreateIdempotencyKey(`add-model:${modelId}`, false);
      try {
        const res = await apiClient.addModel(modelId, idemKey);
        setOpenRouterModels((prev) =>
          prev.some((m) => m.model_id === res.model.model_id)
            ? prev
            : [...prev, res.model]
        );
        setSelectedModelId(res.model.model_id);
        setAddModelModalOpen(false);
        setApiNotice({
          type: 'success',
          code: 'model-added-unverified',
          endpoint: 'POST /api/models',
          idempotencyKey: idemKey,
          message: res.message,
        });
      } catch (err) {
        surfaceApiError(err, 'POST /api/models');
      } finally {
        setPendingAction(null);
      }
    },
    [apiClient, getOrCreateIdempotencyKey, surfaceApiError]
  );

  const handleRemoveModel = useCallback(
    async (model: ModelRecord) => {
      setPendingAction('delete-model');
      const idemKey = getOrCreateIdempotencyKey(`delete-model:${model.model_id}`, false);
      try {
        const res = await apiClient.deleteModel(model.model_id, idemKey);
        setOpenRouterModels((prev) => prev.filter((m) => m.model_id !== res.deleted_model_id));
        setApiNotice({
          type: 'success',
          code: 'model-removed',
          endpoint: `DELETE ${res.encoded_path}`,
          idempotencyKey: idemKey,
          message: res.message,
        });
      } catch (err) {
        surfaceApiError(err, `DELETE /api/models/${encodeURIComponent(model.model_id)}`);
      } finally {
        setPendingAction(null);
      }
    },
    [apiClient, getOrCreateIdempotencyKey, surfaceApiError]
  );

  const handleConfirmProbe = useCallback(async () => {
    if (!probeConfirmModal) return;
    setPendingAction('probe-model');
    const idemKey = getOrCreateIdempotencyKey(
      `probe:${probeConfirmModal.modelId}:${probeConfirmModal.probeType}:${
        probeConfirmModal.contract || 'reasoning'
      }`,
      false
    );
    try {
      if (probeConfirmModal.probeType === 'verify_contract' && probeConfirmModal.contract) {
        const res = await apiClient.verifyModelContract(
          { model_id: probeConfirmModal.modelId, contract: probeConfirmModal.contract },
          idemKey
        );
        setOpenRouterModels((prev) =>
          prev.map((m) =>
            m.model_id === res.model_id
              ? {
                  ...m,
                  contracts: { ...m.contracts, [res.contract]: res.contract_state },
                  verified_at: res.verified_at,
                  freshness: 'just verified',
                }
              : m
          )
        );
        setProbeConfirmModal(null);
        setApiNotice({
          type: 'success',
          code: 'contract-verified',
          endpoint: 'POST /api/models/verify',
          idempotencyKey: idemKey,
          message: res.message,
        });
      } else {
        const res = await apiClient.verifyModelReasoning(
          { model_id: probeConfirmModal.modelId, efforts: ['low', 'medium', 'high'] },
          idemKey
        );
        setProbeConfirmModal(null);
        setApiNotice({
          type: 'success',
          code: 'reasoning-verified',
          endpoint: 'POST /api/models/verify-reasoning',
          idempotencyKey: idemKey,
          message: res.message,
        });
      }
    } catch (err) {
      surfaceApiError(
        err,
        probeConfirmModal.probeType === 'verify_contract'
          ? 'POST /api/models/verify'
          : 'POST /api/models/verify-reasoning'
      );
    } finally {
      setPendingAction(null);
    }
  }, [apiClient, getOrCreateIdempotencyKey, probeConfirmModal, surfaceApiError]);

  const handleValidateStages = useCallback(async () => {
    setPendingAction('validate-stages');
    const idemKey = getOrCreateIdempotencyKey(`stages:validate:${stageViewProfile}`, false);
    try {
      const res = await apiClient.validateStages(
        { profile: stageViewProfile, changes: stagesByProfile[stageViewProfile] },
        idemKey
      );
      setApiNotice({
        type: 'success',
        code: 'stages-validated',
        endpoint: 'POST /api/stages/validate',
        idempotencyKey: idemKey,
        message: res.message,
      });
    } catch (err) {
      surfaceApiError(err, 'POST /api/stages/validate');
    } finally {
      setPendingAction(null);
    }
  }, [apiClient, getOrCreateIdempotencyKey, stageViewProfile, stagesByProfile, surfaceApiError]);

  const handlePutStages = useCallback(async () => {
    setPendingAction('put-stages');
    const idemKey = getOrCreateIdempotencyKey(
      `stages:put:${stageViewProfile}:${stageRevision}`,
      false
    );
    try {
      const res = await apiClient.putStages(
        {
          profile: stageViewProfile,
          revision: stageRevision,
          changes: stagesByProfile[stageViewProfile],
        },
        idemKey
      );
      setStageRevision(res.revision);
      setStagesByProfile((prev) => ({ ...prev, [res.profile]: res.stages }));
      setApiNotice({
        type: 'success',
        code: 'stages-applied',
        endpoint: 'PUT /api/stages',
        idempotencyKey: idemKey,
        message: res.message,
      });
    } catch (err) {
      if (err instanceof ApiHttpError && err.status === 409) {
        setStageConflictModal({
          clientRevision: stageRevision,
          serverRevision: String(err.details?.server_revision ?? 'rev-stages-20261103-09'),
          context: 'global_stages',
        });
      }
      surfaceApiError(err, 'PUT /api/stages');
    } finally {
      setPendingAction(null);
    }
  }, [
    apiClient,
    getOrCreateIdempotencyKey,
    stageRevision,
    stageViewProfile,
    stagesByProfile,
    surfaceApiError,
  ]);

  const handleReloadServerRevision = useCallback(async () => {
    if (!stageConflictModal) return;
    mockTransportRef.current.syncExternalState({
      simulate409Conflict: false,
      stageRevision: stageConflictModal.serverRevision,
    });
    try {
      const res = await apiClient.getStages(stageViewProfile);
      setStageRevision(res.revision);
      setStagesByProfile((prev) => ({ ...prev, [stageViewProfile]: res.stages }));
      setStageConflictModal(null);
      setApiNotice({
        type: 'info',
        code: 'reloaded-server-revision',
        endpoint: `GET /api/stages?profile=${stageViewProfile}`,
        message: `Fetched authoritative server state and updated local revision to ${res.revision}.`,
      });
    } catch (err) {
      surfaceApiError(err, `GET /api/stages?profile=${stageViewProfile}`);
    }
  }, [apiClient, stageConflictModal, stageViewProfile, surfaceApiError]);

  const handleDownloadArchive = useCallback(
    async (jobId: string) => {
      setPendingAction('download-archive');
      try {
        const res = await apiClient.downloadResultArchive(jobId);
        setApiNotice({
          type: 'success',
          code: 'archive-zip-downloaded',
          endpoint: `GET /api/results/${jobId}/download`,
          message: `Downloaded valid CRC-32 PKZIP archive "${res.filename}" (${res.sizeBytes} bytes, application/zip).`,
        });
      } catch (err) {
        surfaceApiError(err, `GET /api/results/${jobId}/download`);
      } finally {
        setPendingAction(null);
      }
    },
    [apiClient, surfaceApiError]
  );

  const handleSaveSystemLimits = useCallback(async () => {
    setPendingAction('put-limits');
    const idemKey = getOrCreateIdempotencyKey(
      `limits:put:${limitsRevision}:${sandboxCap}:${reviewCeilingUsd}`,
      false
    );
    try {
      const res = await apiClient.putSystemLimits(
        {
          sandbox_slots: sandboxCap,
          review_spend_cap_usd: reviewCeilingUsd,
          revision: limitsRevision,
        },
        idemKey
      );
      setSandboxCap(res.sandbox_slots);
      setReviewCeilingUsd(res.review_spend_cap_usd);
      setLimitsRevision(res.revision);
      setApiNotice({
        type: 'success',
        code: 'system-limits-updated',
        endpoint: 'PUT /api/system/limits',
        idempotencyKey: idemKey,
        message: res.message,
      });
    } catch (err) {
      surfaceApiError(err, 'PUT /api/system/limits');
    } finally {
      setPendingAction(null);
    }
  }, [
    apiClient,
    getOrCreateIdempotencyKey,
    limitsRevision,
    reviewCeilingUsd,
    sandboxCap,
    surfaceApiError,
  ]);

  const handleSimulateSlotRelease = useCallback(async () => {
    const nowIso = new Date().toISOString();
    mockTransportRef.current.state.jobs = mockTransportRef.current.state.jobs.map((j) => {
      if (j.job_id !== 'job-tl-parser-042') return j;
      const nextEvents: AttemptEvent[] = [
        ...(j.events ?? []),
        {
          seq: 11,
          ts: nowIso,
          timestamp: nowIso,
          type: 'wait-end',
          attempt_id: 'att-042-03',
          stage: 'writer_b_write',
          role: 'writer_b',
          model: 'qwen/qwen-2.5-coder-32b-instruct:free',
          endpoint: 'openrouter/chutes-fp8',
          effort: 'high',
          detail: 'OWN_SLOT=1 released by job-other-worker-040.',
        },
        {
          seq: 12,
          ts: nowIso,
          timestamp: nowIso,
          type: 'attempt-start',
          attempt_id: 'att-042-03',
          stage: 'writer_b_write',
          role: 'writer_b',
          model: 'qwen/qwen-2.5-coder-32b-instruct:free',
          endpoint: 'openrouter/chutes-fp8',
          effort: 'high',
          detail: 'Genuine attempt-start received for writer_b_write (att-042-03).',
        },
      ];
      return {
        ...j,
        derived_state: 'running',
        waiting: null,
        attempts: (j.attempts ?? []).map((a: any) =>
          a.attempt_id === 'att-042-03' ? { ...a, outcome: 'in-flight (running)' } : a
        ),
        events: nextEvents,
      };
    });
    await fetchOverviewAndJobs(undefined, false);
    setApiNotice({
      type: 'success',
      code: 'event-seq-12-attempt-start',
      endpoint: 'GET /api/loops/job-tl-parser-042/events?after=10',
      message:
        'Received seq=11 (wait-end) and seq=12 (attempt-start): job-tl-parser-042 transitioned from waiting (model-slot) to running.',
    });
  }, [fetchOverviewAndJobs]);

  const handleSimulateMergeComplete = useCallback(
    async (jobId: string) => {
      mockTransportRef.current.state.jobs = mockTransportRef.current.state.jobs.map((j) =>
        j.job_id === jobId
          ? {
              ...j,
              derived_state: 'merged',
              merge_state: 'merged',
              merge_detail: {
                ...j.merge_detail,
                status: 'merged',
                commit_sha: 'd8841209aabbccddeeff00112233445566778899',
                message:
                  'Automatic merge worker rebased onto main, verified full sandbox suite (19/19 green), and pushed commit d8841209.',
              },
            }
          : j
      );
      await fetchOverviewAndJobs(undefined, false);
      setApiNotice({
        type: 'success',
        code: 'merge-completed',
        endpoint: `GET /api/results/${jobId}`,
        message: `Job ${jobId} merge_state transitioned from "pending" to "merged" (commit d8841209).`,
      });
    },
    [fetchOverviewAndJobs]
  );

  // Synthetic 20-Model Scale Generator (C-14, Mock Mode Only)
  const displayedOpenRouterModels = useMemo(() => {
    if (!synthetic20ScaleMode || dataMode === 'live') return openRouterModels;
    const extraRows: ModelRecord[] = Array.from({ length: 17 }).map((_, idx) => {
      void _;
      const num = idx + 4;
      const isFree = idx % 2 === 0;
      return {
        model_id: `synthetic-pool/model-scale-row-${num.toString().padStart(2, '0')}${
          isFree ? ':free' : ''
        }`,
        canonical_model_id: `synthetic-pool/model-scale-row-${num.toString().padStart(2, '0')}`,
        display_name: `[SYNTHETIC SCALE #${num}] Pool Capacity Test Model ${num}`,
        provider: idx % 3 === 0 ? 'Chutes FP8' : idx % 3 === 1 ? 'Targon' : 'Featherless',
        endpoints: [`openrouter/synthetic-ep-${num}`],
        active_endpoint: `openrouter/synthetic-ep-${num}`,
        route: 'openrouter',
        billing_type: isFree ? 'free' : 'paid',
        input_usd_per_1m: isFree ? '0.00' : null,
        output_usd_per_1m: isFree ? '0.00' : null,
        context_window: 65536,
        max_output_tokens: 32768,
        structured_outputs_claimed: true,
        validation_path: 'native_json',
        synthetic_scale_row: true,
        contracts: {
          planner_json: idx % 4 === 0 ? 'verified' : 'unverified',
          writer_file_blocks: 'verified',
          repair_edit_blocks: idx % 3 === 0 ? 'verified' : 'unverified',
          repair_whole_file: 'verified',
          reviewer_verdict: idx % 2 === 0 ? 'verified' : 'unverified',
          native_structured_output: 'verified',
        },
        reasoning: {
          mode: idx % 3 === 0 ? 'mandatory' : 'optional',
          supported: idx % 3 === 0 ? 'mandatory' : 'optional',
          off_admissible: idx % 3 !== 0,
          efforts: ['low', 'medium', 'high'],
          token_budget_supported: true,
          token_budget_min: 1024,
          token_budget_max: 16384,
        },
        temperature_range: { min: 0.0, max: 1.0, default: 0.1 },
        verified_at: '2026-11-03T12:00:00Z',
        verified_endpoint: `openrouter/synthetic-ep-${num}`,
        freshness: 'synthetic UI scale row (C-14)',
        availability: 'available',
        in_use_stages: [],
      };
    });
    return [...openRouterModels, ...extraRows];
  }, [openRouterModels, synthetic20ScaleMode, dataMode]);

  const filteredOpenRouterModels = useMemo(() => {
    return displayedOpenRouterModels.filter((m) => {
      if (
        modelSearch &&
        !m.model_id.toLowerCase().includes(modelSearch.toLowerCase()) &&
        !m.display_name.toLowerCase().includes(modelSearch.toLowerCase())
      ) {
        return false;
      }
      if (modelBillingFilter !== 'all' && m.billing_type !== modelBillingFilter) {
        return false;
      }
      if (modelContractFilter !== 'all') {
        const state = m.contracts[modelContractFilter as SemanticContract];
        if (state !== 'verified') return false;
      }
      return true;
    });
  }, [displayedOpenRouterModels, modelSearch, modelBillingFilter, modelContractFilter]);

  const selectedModelObj = useMemo(() => {
    return (
      [...displayedOpenRouterModels, ...claudeCliModels].find(
        (m) => m.model_id === selectedModelId
      ) || null
    );
  }, [displayedOpenRouterModels, claudeCliModels, selectedModelId]);

  const selectedLoopJob = useMemo(() => {
    return jobs.find((j) => j.job_id === selectedLoopJobId) || jobs[0] || null;
  }, [jobs, selectedLoopJobId]);

  const selectedResultJob = useMemo(() => {
    return jobs.find((j) => j.job_id === selectedResultJobId) || jobs[0] || null;
  }, [jobs, selectedResultJobId]);

  const navItems: Array<{ path: NavPage; label: string; subtitle: string; icon: React.ReactNode }> =
    [
      {
        path: '/dashboard',
        label: 'Dashboard',
        subtitle: 'KPIs, Quotas & Active Loops',
        icon: <LayoutDashboard className="w-4 h-4" />,
      },
      {
        path: '/jobs/new',
        label: 'Run Jobs',
        subtitle: 'Spec Path, Planner & Queue',
        icon: <Play className="w-4 h-4" />,
      },
      {
        path: '/loops',
        label: 'Live Loops',
        subtitle: 'Attempt Timeline & Inspector',
        icon: <Activity className="w-4 h-4" />,
      },
      {
        path: '/models',
        label: 'Models',
        subtitle: 'OpenRouter Pool & Claude CLI',
        icon: <Cpu className="w-4 h-4" />,
      },
      {
        path: '/stages',
        label: 'Stage Settings',
        subtitle: '10 Configurable Stage Types',
        icon: <Sliders className="w-4 h-4" />,
      },
      {
        path: '/results',
        label: 'Results',
        subtitle: '4 Verdicts, Diff & Archive',
        icon: <Archive className="w-4 h-4" />,
      },
      {
        path: '/system',
        label: 'System',
        subtitle: 'Health, Leases, Gates & STOP',
        icon: <Server className="w-4 h-4" />,
      },
    ];

  const bgCanvas = lightTheme ? 'bg-slate-100 text-slate-900' : 'bg-[#08090c] text-slate-100';
  const bgPanel = lightTheme ? 'bg-white border-slate-300' : 'bg-[#101217] border-slate-800';
  const bgSubtle = lightTheme
    ? 'bg-slate-50 border-slate-200'
    : 'bg-[#151821] border-slate-800/80';
  const textMuted = lightTheme ? 'text-slate-600' : 'text-slate-400';

  const activeQueueKey =
    idempotencySnapshot[`queue:${jobPathInput}:rerun=false`] ||
    idempotencyMgrRef.current.getOrCreateKey(`queue:${jobPathInput}:rerun=false`, false);

  return (
    <div className={`min-h-screen flex flex-col font-sans ${bgCanvas}`}>
      {/* TOP UNMISTAKABLE MOCK / LIVE CONNECTION WATERMARK & SCENARIO BAR */}
      <div
        className={`border-b px-3 py-2 flex flex-wrap items-center justify-between gap-2 text-xs ${
          dataMode === 'mock'
            ? 'bg-sky-950/80 border-sky-700/60 text-sky-100'
            : liveConnected
            ? 'bg-emerald-950/80 border-emerald-700/60 text-emerald-100'
            : 'bg-rose-950/90 border-rose-700 text-rose-100'
        }`}
      >
        <div className="flex flex-wrap items-center gap-2">
          <span className="inline-flex items-center gap-1.5 font-mono font-bold px-2 py-0.5 rounded bg-black/40 border border-white/15">
            {dataMode === 'mock' ? (
              <>
                <Database className="w-3.5 h-3.5 text-sky-400" />
                MOCK DATA / NOT CONNECTED TO LIVE ENGINE
              </>
            ) : liveLoading ? (
              <>
                <RefreshCw className="w-3.5 h-3.5 text-amber-400" />
                LIVE / CONNECTING TO 127.0.0.1:8765...
              </>
            ) : liveConnected ? (
              <>
                <Wifi className="w-3.5 h-3.5 text-emerald-400" />
                LIVE / CONNECTED (127.0.0.1:8765)
              </>
            ) : (
              <>
                <WifiOff className="w-3.5 h-3.5 text-rose-400" />
                LIVE / DISCONNECTED (NO SILENT FIXTURE FALLBACK)
              </>
            )}
          </span>
          <span className="hidden md:inline text-slate-300">
            Engine SHA:{' '}
            <code className="font-mono text-sky-300">
              {overview.engine_sha ? overview.engine_sha.slice(0, 12) : 'Unknown'}
            </code>{' '}
            | Branch: <code className="font-mono text-sky-300">feat/satvik-loop-control-center-ui</code>
          </span>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <label className="flex items-center gap-1.5 text-xs">
            <span className="font-semibold text-sky-200">16-Scenario Verification Preset:</span>
            <select
              aria-label="16-Scenario Verification Preset"
              value={activeScenario}
              onChange={(e) => applyScenarioPreset(e.target.value as ScenarioId)}
              className="bg-slate-900 text-slate-100 border border-slate-700 rounded px-2 py-1 text-xs font-mono focus:outline-none focus:ring-1 focus:ring-sky-400"
            >
              {SCENARIO_PRESETS.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.shortLabel}
                </option>
              ))}
            </select>
          </label>

          <div className="inline-flex rounded border border-slate-700 overflow-hidden">
            <button
              type="button"
              onClick={() => handleSwitchDataMode('mock')}
              className={`px-2 py-1 text-xs font-mono font-semibold ${
                dataMode === 'mock' ? 'bg-sky-500 text-slate-950' : 'bg-slate-900 text-slate-300'
              }`}
            >
              MOCK
            </button>
            <button
              type="button"
              onClick={() => handleSwitchDataMode('live')}
              className={`px-2 py-1 text-xs font-mono font-semibold ${
                dataMode === 'live' ? 'bg-rose-600 text-white' : 'bg-slate-900 text-slate-300'
              }`}
            >
              LIVE API
            </button>
          </div>
        </div>
      </div>

      {/* COMPACT STICKY TELEMETRY & HEALTH STRIP */}
      <header className={`sticky top-0 z-30 border-b px-3 py-2 ${bgPanel}`}>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <button
              type="button"
              aria-label="Toggle Navigation Menu"
              onClick={() => setMobileNavOpen((v) => !v)}
              className="lg:hidden p-1.5 rounded border border-slate-700 hover:border-sky-400"
            >
              <Menu className="w-4 h-4" />
            </button>
            <div className="flex items-center gap-2">
              <span className="font-bold tracking-tight text-sm md:text-base">
                SATVIK LOOP CONTROL CENTER
              </span>
              <span className="text-[11px] font-mono px-1.5 py-0.5 rounded bg-sky-500/15 text-sky-400 border border-sky-500/30">
                v2.2 API-Contract Verified
              </span>
              {!indexOk && (
                <span
                  title="index_ok: false — Slower file-backed response (not fatal)"
                  className="text-[11px] font-mono px-1.5 py-0.5 rounded bg-amber-500/20 text-amber-300 border border-amber-500/40"
                >
                  INDEX_OK: FALSE (FILE-BACKED)
                </span>
              )}
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2 text-xs font-mono">
            <span
              title="Authoritative backend SATVIK_PROFILE (C-05: read-only in live runtime)"
              className="px-2 py-1 rounded border border-slate-700 bg-slate-900/60 text-slate-200 flex items-center gap-1.5"
            >
              <span>PROFILE:</span>
              <strong
                className={
                  authoritativeProfile === 'test' ? 'text-amber-400' : 'text-emerald-400'
                }
              >
                {authoritativeProfile.toUpperCase()}
              </strong>
              {dataMode === 'mock' && (
                <button
                  type="button"
                  onClick={() => {
                    const nextProf: ProfileName =
                      authoritativeProfile === 'test' ? 'production' : 'test';
                    setAuthoritativeProfile(nextProf);
                    mockTransportRef.current.syncExternalState({
                      authoritativeProfile: nextProf,
                    });
                  }}
                  className="ml-1 text-[10px] underline text-sky-400 hover:text-sky-300"
                >
                  (Sim Toggle)
                </button>
              )}
            </span>

            <span className="px-2 py-1 rounded border border-slate-800 bg-slate-900/40">
              RUN: <strong className="text-sky-400">{overview.loops_running}</strong>
            </span>
            <span className="px-2 py-1 rounded border border-slate-800 bg-slate-900/40">
              QUEUE: <strong className="text-slate-200">{overview.queue_depth}</strong>
            </span>
            <span
              className={`px-2 py-1 rounded border ${
                overview.free_today.remaining === 0
                  ? 'border-rose-600 bg-rose-950/60 text-rose-200'
                  : 'border-slate-800 bg-slate-900/40'
              }`}
            >
              FREE REM:{' '}
              <strong>
                {overview.free_today.remaining !== null
                  ? overview.free_today.remaining
                  : 'Unknown'}
              </strong>
              /{overview.free_today.cap !== null ? overview.free_today.cap : 'Unknown'}
            </span>
            <span className="px-2 py-1 rounded border border-slate-800 bg-slate-900/40">
              REVIEWS: <strong className="text-amber-400">{overview.reviews_pending}</strong>
            </span>
            <span
              className={`px-2 py-1 rounded border ${
                overview.pace_60s.limit !== null &&
                overview.pace_60s.sent >= overview.pace_60s.limit
                  ? 'border-rose-600 bg-rose-950/60 text-rose-200'
                  : 'border-slate-800 bg-slate-900/40'
              }`}
            >
              PACE 60s: <strong>{overview.pace_60s.sent}</strong>/
              {overview.pace_60s.limit ?? 'Unknown'}
            </span>
            <span
              className="px-2 py-1 rounded border border-slate-800 bg-slate-900/40"
              title="Reconciled total API cost is null/not provided by backend yet (C-15); never fabricated as $0.00"
            >
              API COST:{' '}
              <strong className="text-slate-400">
                {overview.reconciled_cost_usd !== null
                  ? `$${overview.reconciled_cost_usd}`
                  : 'Unknown'}
              </strong>
            </span>

            <button
              type="button"
              onClick={() => setUseUtcTime((u) => !u)}
              className="px-2 py-1 rounded border border-slate-700 hover:border-sky-400 text-[11px]"
              title={`Last refresh: ${lastVerifiedRefresh || 'Pending'}`}
            >
              TZ: {useUtcTime ? 'UTC' : 'LOCAL'}
            </button>

            <button
              type="button"
              onClick={() => setLightTheme((t) => !t)}
              className="px-2 py-1 rounded border border-slate-700 hover:border-sky-400 text-[11px]"
            >
              {lightTheme ? 'DARK' : 'LIGHT'}
            </button>

            <button
              type="button"
              onClick={() => fetchOverviewAndJobs(undefined, true)}
              className="inline-flex items-center gap-1 px-2.5 py-1 rounded border border-slate-700 hover:border-sky-400 text-xs"
            >
              <RefreshCw className="w-3.5 h-3.5" />
              Refresh
            </button>

            <button
              type="button"
              onClick={() => setStopModalOpen(true)}
              className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded font-semibold text-xs border ${
                stopActive
                  ? 'bg-rose-600 text-white border-rose-400'
                  : 'bg-rose-950/70 text-rose-200 border-rose-700 hover:bg-rose-900'
              }`}
            >
              <Octagon className="w-3.5 h-3.5" />
              {stopActive ? 'STOP ACTIVE (SOFT)' : 'GLOBAL STOP'}
            </button>
          </div>
        </div>
      </header>

      {/* BODY: LEFT SIDEBAR (7 PAGES) + SCROLLABLE MAIN WORKSPACE */}
      <div className="flex-1 flex overflow-hidden">
        <aside
          aria-label="Primary 7-Page Navigation"
          className={`${
            mobileNavOpen ? 'block fixed inset-y-0 left-0 z-40 w-64 shadow-2xl' : 'hidden'
          } lg:block lg:static lg:w-60 shrink-0 border-r ${bgPanel} flex flex-col justify-between`}
        >
          <div className="p-3 space-y-1">
            <div className="flex items-center justify-between lg:hidden pb-2 mb-2 border-b border-slate-800">
              <span className="font-mono text-xs uppercase tracking-wider text-sky-400">
                7-Page Navigation
              </span>
              <button
                type="button"
                onClick={() => setMobileNavOpen(false)}
                className="p-1 rounded border border-slate-700"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <div className={`text-[11px] font-mono uppercase px-2 py-1 ${textMuted}`}>
              Control Destinations (7)
            </div>

            {navItems.map((item) => {
              const isActive = activePage === item.path;
              return (
                <button
                  key={item.path}
                  type="button"
                  onClick={() => {
                    setActivePage(item.path);
                    setMobileNavOpen(false);
                  }}
                  className={`w-full text-left px-3 py-2.5 rounded-md border transition flex items-start gap-2.5 ${
                    isActive
                      ? 'bg-sky-500/15 border-sky-400 text-sky-300 font-semibold'
                      : 'border-transparent hover:bg-slate-800/50 text-slate-300'
                  }`}
                >
                  <span className="mt-0.5 text-sky-400">{item.icon}</span>
                  <div className="min-w-0">
                    <div className="text-sm leading-tight">{item.label}</div>
                    <div className={`text-[11px] truncate ${textMuted}`}>{item.subtitle}</div>
                  </div>
                </button>
              );
            })}
          </div>

          <div className="p-3 border-t border-slate-800/80 text-[11px] space-y-1.5">
            <div className="font-mono font-semibold text-slate-300 flex items-center gap-1.5">
              <ShieldCheck className="w-3.5 h-3.5 text-emerald-400" />
              Fixed Engine Boundaries
            </div>
            <p className={textMuted}>
              6 Roles · 10 Stage Keys · Contributor-Disjoint Reviews · Soft STOP Only · No Manual
              Merge/Cancel/Resume.
            </p>
            <div className="font-mono text-[10px] text-slate-400">
              Refreshed: {formatTimestamp(lastVerifiedRefresh)}
            </div>
          </div>
        </aside>

        {/* Main Content Area */}
        <main className="flex-1 min-w-0 overflow-y-auto p-3 md:p-5 space-y-4">
          {dataMode === 'live' && liveLoading && (
            <div
              role="status"
              className="rounded-md border border-sky-600 bg-sky-950/60 p-4 text-sky-100 font-mono text-xs flex items-center gap-3"
            >
              <RefreshCw className="w-4 h-4 text-sky-400" />
              Loading authoritative LIVE state from http://127.0.0.1:8765/api (zero demonstration
              data carried into LIVE mode)...
            </div>
          )}

          {dataMode === 'live' && !liveLoading && !liveConnected && (
            <div
              role="alert"
              className="rounded-md border border-rose-600 bg-rose-950/90 p-3 text-rose-100 flex flex-wrap items-center justify-between gap-3"
            >
              <div className="space-y-1">
                <div className="font-bold text-sm flex items-center gap-2">
                  <WifiOff className="w-4 h-4 text-rose-400" />
                  LIVE BACKEND DISCONNECTED — HTTP 503 / CONNECTION REFUSED
                  (http://127.0.0.1:8765/api) [STALE TELEMETRY]
                </div>
                <p className="text-xs text-rose-200">
                  {liveErrorMsg ||
                    'Cannot connect to FastAPI server. Mutations are never fabricated locally.'}
                </p>
              </div>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => fetchOverviewAndJobs(undefined, true)}
                  className="px-3 py-1.5 rounded bg-rose-700 hover:bg-rose-600 text-xs font-mono font-semibold"
                >
                  Retry Live Connection
                </button>
                <button
                  type="button"
                  onClick={() => handleSwitchDataMode('mock')}
                  className="px-3 py-1.5 rounded bg-sky-500 text-slate-950 text-xs font-mono font-semibold"
                >
                  Return to MOCK Mode
                </button>
              </div>
            </div>
          )}

          {apiNotice && (
            <div
              role="status"
              className={`rounded-md border p-3 text-xs flex flex-wrap items-start justify-between gap-2 ${
                apiNotice.type === 'error'
                  ? 'bg-rose-950/70 border-rose-700 text-rose-100'
                  : apiNotice.type === 'warning'
                  ? 'bg-amber-950/70 border-amber-700 text-amber-100'
                  : apiNotice.type === 'success'
                  ? 'bg-emerald-950/70 border-emerald-700 text-emerald-100'
                  : 'bg-sky-950/60 border-sky-800 text-sky-100'
              }`}
            >
              <div className="space-y-1">
                <div className="flex flex-wrap items-center gap-2 font-mono">
                  <span className="font-bold uppercase px-1.5 py-0.5 rounded bg-black/30">
                    {apiNotice.code}
                  </span>
                  {apiNotice.endpoint && (
                    <span className="text-sky-300">{apiNotice.endpoint}</span>
                  )}
                  {apiNotice.idempotencyKey && (
                    <span className="text-slate-300">
                      Idempotency-Key: <code>{apiNotice.idempotencyKey}</code>
                    </span>
                  )}
                </div>
                <div>{apiNotice.message}</div>
              </div>
              <button
                type="button"
                onClick={() => setApiNotice(null)}
                className="p-1 rounded hover:bg-black/20"
                aria-label="Dismiss Notice"
              >
                <X className="w-4 h-4" />
              </button>
            </div>
          )}

          {activePage === '/dashboard' && (
            <DashboardPage
              overview={overview}
              jobs={jobs}
              stopActive={stopActive}
              copiedToken={copiedToken}
              bgPanel={bgPanel}
              bgSubtle={bgSubtle}
              textMuted={textMuted}
              formatTimestamp={formatTimestamp}
              onCopyText={handleCopyText}
              onNavigate={setActivePage}
              onOpenLoopJob={(id) => {
                setSelectedLoopJobId(id);
                setActivePage('/loops');
              }}
              onOpenResultJob={(id) => {
                setSelectedResultJobId(id);
                setActivePage('/results');
              }}
            />
          )}

          {activePage === '/jobs/new' && (
            <RunJobsPage
              authoritativeProfile={authoritativeProfile}
              stageRevision={stageRevision}
              stages={stagesByProfile[authoritativeProfile]}
              productionPlannerStage={
                stagesByProfile.production[0] ?? INITIAL_STAGES_BY_PROFILE.production[0]
              }
              runEntryMode={runEntryMode}
              jobPathInput={jobPathInput}
              jobValidated={jobValidated}
              budgetValidated={budgetValidated}
              authorizedPaidBudget={authorizedPaidBudget}
              workerCountToStart={workerCountToStart}
              planJobName={planJobName}
              planPrompt={planPrompt}
              planAllowedFiles={planAllowedFiles}
              planCostAuthorized={planCostAuthorized}
              generatedPlan={generatedPlan}
              activeIdempotencyKey={activeQueueKey}
              pendingAction={pendingAction}
              bgPanel={bgPanel}
              bgSubtle={bgSubtle}
              textMuted={textMuted}
              onChangeEntryMode={setRunEntryMode}
              onChangeJobPath={(p) => {
                setJobPathInput(p);
                setJobValidated(false);
              }}
              onResetForm={() => {
                setJobPathInput('jobs/new_candidate_spec.json');
                setJobValidated(false);
                setGeneratedPlan(null);
                setApiNotice({
                  type: 'info',
                  code: 'form-reset',
                  message:
                    'Reset Run Jobs form (New Job resets client state; does not create a backend file).',
                });
              }}
              onValidateJobPath={handleValidateJobPath}
              onChangePaidBudget={(val) => {
                setAuthorizedPaidBudget(val);
                setBudgetValidated(false);
              }}
              onValidateBudget={handleValidateBudget}
              onQueueJob={handleQueueJob}
              onChangeWorkerCount={setWorkerCountToStart}
              onStartWorkers={handleStartWorkers}
              onChangePlanJobName={setPlanJobName}
              onChangePlanPrompt={setPlanPrompt}
              onChangePlanAllowedFiles={setPlanAllowedFiles}
              onChangePlanCostAuthorized={setPlanCostAuthorized}
              onCreatePlan={handleCreatePlan}
              onToggleAssumedItem={(id, checked) =>
                setGeneratedPlan((prev) =>
                  prev
                    ? {
                        ...prev,
                        assumed: prev.assumed.map((a) => (a.id === id ? { ...a, checked } : a)),
                      }
                    : null
                )
              }
              onApprovePlan={handleApprovePlan}
            />
          )}

          {activePage === '/loops' && (
            <LiveLoopsPage
              dataMode={dataMode}
              jobs={jobs}
              selectedLoopJob={selectedLoopJob}
              loopFilter={loopFilter}
              loopViewMode={loopViewMode}
              loopInspectorTab={loopInspectorTab}
              futureStageDrawerOpen={futureStageDrawerOpen}
              futureStageTarget={futureStageTarget}
              futureStageEffort={futureStageEffort}
              indexOk={indexOk}
              bgPanel={bgPanel}
              bgSubtle={bgSubtle}
              textMuted={textMuted}
              formatTimestamp={formatTimestamp}
              onToggleIndexOk={() => {
                const next = !indexOk;
                setIndexOk(next);
                mockTransportRef.current.syncExternalState({ indexOk: next });
              }}
              onOpenStopModal={() => setStopModalOpen(true)}
              onChangeLoopFilter={setLoopFilter}
              onSelectLoopJob={setSelectedLoopJobId}
              onToggleFutureStageDrawer={() => setFutureStageDrawerOpen((v) => !v)}
              onChangeFutureStageTarget={setFutureStageTarget}
              onChangeFutureStageEffort={setFutureStageEffort}
              onPatchFutureStage={handlePatchFutureStage}
              onSimulate409FutureStage={() =>
                setStageConflictModal({
                  clientRevision: selectedLoopJob?.config_revision || stageRevision,
                  serverRevision: 'rev-stages-20261103-09',
                  context: 'future_job_stage',
                })
              }
              onSimulateSlotRelease={handleSimulateSlotRelease}
              onChangeLoopViewMode={setLoopViewMode}
              onChangeInspectorTab={setLoopInspectorTab}
            />
          )}

          {activePage === '/models' && (
            <ModelsPage
              displayedOpenRouterModels={displayedOpenRouterModels}
              filteredOpenRouterModels={filteredOpenRouterModels}
              claudeCliModels={claudeCliModels}
              selectedModelId={selectedModelId}
              selectedModelObj={selectedModelObj}
              modelSearch={modelSearch}
              modelBillingFilter={modelBillingFilter}
              modelContractFilter={modelContractFilter}
              synthetic20ScaleMode={synthetic20ScaleMode}
              pendingAction={pendingAction}
              bgPanel={bgPanel}
              bgSubtle={bgSubtle}
              textMuted={textMuted}
              onToggleSynthetic20Scale={() => setSynthetic20ScaleMode((v) => !v)}
              onRefreshModels={handleRefreshModels}
              onOpenAddModelModal={() => {
                setAddModelModalOpen(true);
                setCatalogResults([]);
              }}
              onChangeSearch={setModelSearch}
              onChangeBillingFilter={setModelBillingFilter}
              onChangeContractFilter={setModelContractFilter}
              onSelectModel={setSelectedModelId}
              onOpenProbeModal={(params) =>
                setProbeConfirmModal({
                  ...params,
                  ackChecked: false,
                })
              }
              onRemoveModel={handleRemoveModel}
            />
          )}

          {activePage === '/stages' && (
            <StageSettingsPage
              authoritativeProfile={authoritativeProfile}
              stageViewProfile={stageViewProfile}
              stageRevision={stageRevision}
              stages={stagesByProfile[stageViewProfile]}
              selectedStageKey={selectedStageKey}
              allModels={[...openRouterModels, ...claudeCliModels]}
              pendingAction={pendingAction}
              bgPanel={bgPanel}
              bgSubtle={bgSubtle}
              textMuted={textMuted}
              onChangeViewProfile={(prof) => {
                setStageViewProfile(prof);
                fetchStagesForProfile(prof);
              }}
              onSimulate409Conflict={() =>
                setStageConflictModal({
                  clientRevision: stageRevision,
                  serverRevision: 'rev-stages-20261103-09',
                  context: 'global_stages',
                })
              }
              onSelectStageKey={setSelectedStageKey}
              onChangeStageModel={(stKey, modelId) =>
                setStagesByProfile((prev) => ({
                  ...prev,
                  [stageViewProfile]: prev[stageViewProfile].map((r) =>
                    r.stage_key === stKey ? { ...r, configured_model_id: modelId } : r
                  ),
                }))
              }
              onChangeStageEffort={(stKey, effort) =>
                setStagesByProfile((prev) => ({
                  ...prev,
                  [stageViewProfile]: prev[stageViewProfile].map((r) =>
                    r.stage_key === stKey ? { ...r, effort } : r
                  ),
                }))
              }
              onChangeStageMaxTokens={(stKey, maxTokens) =>
                setStagesByProfile((prev) => ({
                  ...prev,
                  [stageViewProfile]: prev[stageViewProfile].map((r) =>
                    r.stage_key === stKey ? { ...r, max_output_tokens: maxTokens } : r
                  ),
                }))
              }
              onValidateStages={handleValidateStages}
              onPutStages={handlePutStages}
            />
          )}

          {activePage === '/results' && (
            <ResultsPage
              jobs={jobs}
              selectedResultJob={selectedResultJob}
              resultsSearch={resultsSearch}
              resultsOutcomeFilter={resultsOutcomeFilter}
              resultsTab={resultsTab}
              pendingAction={pendingAction}
              bgPanel={bgPanel}
              bgSubtle={bgSubtle}
              textMuted={textMuted}
              onChangeSearch={setResultsSearch}
              onChangeOutcomeFilter={setResultsOutcomeFilter}
              onSelectResultJob={(id) => {
                setSelectedResultJobId(id);
                fetchResultDetailAndDiff(id);
              }}
              onChangeTab={setResultsTab}
              onDownloadArchive={handleDownloadArchive}
              onSimulateMergeComplete={handleSimulateMergeComplete}
            />
          )}

          {activePage === '/system' && (
            <SystemPage
              dataMode={dataMode}
              liveConnected={liveConnected}
              stopActive={stopActive}
              systemHealth={systemHealth}
              jobs={jobs}
              sandboxCap={sandboxCap}
              reviewCeilingUsd={reviewCeilingUsd}
              limitsRevision={limitsRevision}
              showRev4Reference={showRev4Reference}
              pendingAction={pendingAction}
              bgPanel={bgPanel}
              bgSubtle={bgSubtle}
              textMuted={textMuted}
              onRefreshHealth={() => fetchSystemHealthAndLimits(undefined, true)}
              onOpenStopModal={() => setStopModalOpen(true)}
              onToggleLimitsRevisionSim={() => {
                const nextRev = limitsRevision ? null : 'rev-limits-mock-01';
                setLimitsRevision(nextRev);
                mockTransportRef.current.syncExternalState({ limitsRevision: nextRev });
              }}
              onChangeSandboxCap={setSandboxCap}
              onChangeReviewCeiling={setReviewCeilingUsd}
              onSaveSystemLimits={handleSaveSystemLimits}
              onToggleRev4Reference={() => setShowRev4Reference((v) => !v)}
            />
          )}
        </main>
      </div>

      {/* MODALS */}
      <StopConfirmModal
        open={stopModalOpen}
        stopActive={stopActive}
        pending={pendingAction === 'stop'}
        bgPanel={bgPanel}
        onClose={() => setStopModalOpen(false)}
        onConfirm={handleConfirmSoftStop}
      />

      <ProbeConfirmModal
        modal={probeConfirmModal}
        pending={pendingAction === 'probe-model'}
        bgPanel={bgPanel}
        onClose={() => setProbeConfirmModal(null)}
        onToggleAck={(checked) =>
          setProbeConfirmModal((prev) => (prev ? { ...prev, ackChecked: checked } : null))
        }
        onConfirm={handleConfirmProbe}
      />

      <AddModelCatalogModal
        open={addModelModalOpen}
        query={catalogQuery}
        results={catalogResults}
        pending={pendingAction === 'search-catalog' || pendingAction === 'add-model'}
        bgPanel={bgPanel}
        bgSubtle={bgSubtle}
        onClose={() => setAddModelModalOpen(false)}
        onChangeQuery={setCatalogQuery}
        onSearch={handleSearchCatalog}
        onAddModel={handleAddModel}
      />

      <RevisionConflictModal
        modal={stageConflictModal}
        bgPanel={bgPanel}
        onClose={() => setStageConflictModal(null)}
        onReloadServerState={handleReloadServerRevision}
      />
    </div>
  );
}
