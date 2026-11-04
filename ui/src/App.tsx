import React, { useState, useEffect, useMemo, useCallback, useRef } from 'react';
import {
  Activity,
  Archive,
  Cpu,
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

import { ApiHttpError, SatvikApiClient, buildStageChanges, stageRowWithModel } from './api/client';
import { pendingPlanId, pollPlanUntilSettled, rememberPendingPlan } from './api/planPoll';
import { IdempotencyManager } from './api/idempotency';
import { EventStreamBuffer } from './api/normalizer';
import type {
  DataMode,
  Effort,
  EventPage,
  JobRecord,
  LoopEvent,
  ModelRecord,
  NavPage,
  NormalizedOverview,
  PlanRecord,
  ProfileName,
  ResultRecord,
  SemanticContract,
  StageKey,
  StageSetting,
  StagesResponse,
  SystemHealth,
  SystemLimits,
  VerifyResult,
} from './api/types';
import {
  AddModelCatalogModal,
  ConflictModalState,
  ProbeConfirmModal,
  ProbeModalState,
  RevisionConflictModal,
  StopConfirmModal,
} from './components/ShellModals';
import { createFixtureTransport } from './mocks/fixtureTransport';
import { DashboardPage } from './pages/DashboardPage';
import { LiveLoopsPage } from './pages/LiveLoopsPage';
import { ModelsPage } from './pages/ModelsPage';
import { ResultsPage } from './pages/ResultsPage';
import { RunJobsPage } from './pages/RunJobsPage';
import { StageSettingsPage } from './pages/StageSettingsPage';
import { SystemPage } from './pages/SystemPage';

const LIVE_BASE_URL = 'http://127.0.0.1:8765/api';

/**
 * Data mode. `import.meta.env.VITE_DATA_MODE` decides (`npm run dev` = mock, `npm run dev:live` = live).
 * `window.__SATVIK_DATA_MODE__` is an override for debugging only.
 */
function resolveInitialMode(): DataMode {
  if (typeof window !== 'undefined') {
    const override = (window as unknown as { __SATVIK_DATA_MODE__?: unknown }).__SATVIK_DATA_MODE__;
    if (override === 'live' || override === 'mock') return override;
  }
  return import.meta.env.VITE_DATA_MODE === 'mock' ? 'mock' : 'live';
}

type Notice = {
  type: 'info' | 'success' | 'warning' | 'error';
  code: string;
  message: string;
  idempotencyKey?: string;
  endpoint?: string;
};

/** Merges a summary row into the record already held, so detail fields survive a list refresh. */
function mergeJob(prev: JobRecord | undefined, summary: JobRecord): JobRecord {
  return prev ? { ...prev, ...summary } : summary;
}

export default function App() {
  const [activePage, setActivePage] = useState<NavPage>('/dashboard');
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [lightTheme, setLightTheme] = useState(false);
  const [useUtcTime, setUseUtcTime] = useState(false);

  const [dataMode, setDataMode] = useState<DataMode>(resolveInitialMode);
  const dataModeRef = useRef<DataMode>(dataMode);
  dataModeRef.current = dataMode;

  const [liveConnected, setLiveConnected] = useState<boolean>(false);
  const [liveLoading, setLiveLoading] = useState<boolean>(dataMode === 'live');
  const [liveErrorMsg, setLiveErrorMsg] = useState<string | null>(null);
  const [lastRefresh, setLastRefresh] = useState<string | null>(null);
  const [indexOk, setIndexOk] = useState<boolean | null>(null);
  const [pendingAction, setPendingAction] = useState<string | null>(null);
  const [apiNotice, setApiNotice] = useState<Notice | null>(null);
  const [copiedToken, setCopiedToken] = useState<string | null>(null);

  const [stopActive, setStopActive] = useState<boolean | null>(null);
  const [stopModalOpen, setStopModalOpen] = useState<boolean>(false);

  const [overview, setOverview] = useState<NormalizedOverview | null>(null);
  const [jobs, setJobs] = useState<JobRecord[]>([]);
  const [models, setModels] = useState<ModelRecord[]>([]);
  const [health, setHealth] = useState<SystemHealth | null>(null);
  const [limits, setLimits] = useState<SystemLimits | null>(null);

  // Stage settings: the server view per profile, and the working copy the page edits.
  const [stageView, setStageView] = useState<Partial<Record<ProfileName, StagesResponse>>>({});
  const [stageDrafts, setStageDrafts] = useState<Partial<Record<ProfileName, StageSetting[]>>>({});
  const [stageViewProfile, setStageViewProfile] = useState<ProfileName>('test');
  const [selectedStageKey, setSelectedStageKey] = useState<StageKey>('writer_a_write');
  const [stageConflictModal, setStageConflictModal] = useState<ConflictModalState | null>(null);

  const [selectedLoopJobId, setSelectedLoopJobId] = useState<string | null>(null);
  const [loopEvents, setLoopEvents] = useState<LoopEvent[]>([]);
  const [loopEventsIndexOk, setLoopEventsIndexOk] = useState<boolean | null>(null);
  const [selectedResultJobId, setSelectedResultJobId] = useState<string | null>(null);
  const [result, setResult] = useState<ResultRecord | null>(null);
  const [diffFiles, setDiffFiles] = useState<Array<{ path: string; unified_diff: string }>>([]);
  const [selectedModelId, setSelectedModelId] = useState<string | null>(null);

  const idempotencyMgrRef = useRef<IdempotencyManager>(new IdempotencyManager());
  const getOrCreateIdempotencyKey = useCallback((intentSig: string, forceRotate = false) => {
    return idempotencyMgrRef.current.getOrCreateKey(intentSig, forceRotate);
  }, []);

  const apiClient = useMemo(
    () =>
      new SatvikApiClient({
        baseUrl: LIVE_BASE_URL,
        getMode: () => dataModeRef.current,
        mockHandler: createFixtureTransport(),
      }),
    []
  );

  const eventBufferRef = useRef<EventStreamBuffer>(new EventStreamBuffer(''));

  // Run Jobs state
  const [runEntryMode, setRunEntryMode] = useState<'existing_path' | 'planner_request'>('existing_path');
  const [jobPathInput, setJobPathInput] = useState<string>('');
  const [jobValidated, setJobValidated] = useState<boolean | null>(null);
  const [jobProblems, setJobProblems] = useState<string[]>([]);
  const [budgetValidated, setBudgetValidated] = useState<boolean | null>(null);
  const [paidBudgetUsd, setPaidBudgetUsd] = useState<string>('0');
  const [workerCountToStart, setWorkerCountToStart] = useState<number>(1);
  const [planName, setPlanName] = useState<string>('');
  const [planPrompt, setPlanPrompt] = useState<string>('');
  const [planFiles, setPlanFiles] = useState<string>('');
  const [generatedPlan, setGeneratedPlan] = useState<PlanRecord | null>(null);
  const [planApproveAssumed, setPlanApproveAssumed] = useState<boolean>(false);

  // Live Loops state
  const [loopFilter, setLoopFilter] = useState<string>('all');
  const [loopViewMode, setLoopViewMode] = useState<'timeline_list' | 'flow_diagram'>('timeline_list');
  const [loopInspectorTab, setLoopInspectorTab] = useState<
    'tests' | 'reviews' | 'diff' | 'lineage' | 'budget' | 'artifacts'
  >('tests');
  const [futureStageDrawerOpen, setFutureStageDrawerOpen] = useState<boolean>(false);
  const [futureStageTarget, setFutureStageTarget] = useState<StageKey>('writer_b_repair');
  const [futureStageEffort, setFutureStageEffort] = useState<Effort>('high');

  // Models state
  const [modelSearch, setModelSearch] = useState<string>('');
  const [modelBillingFilter, setModelBillingFilter] = useState<string>('all');
  const [modelContractFilter, setModelContractFilter] = useState<string>('all');
  const [addModelModalOpen, setAddModelModalOpen] = useState<boolean>(false);
  const [catalogQuery, setCatalogQuery] = useState<string>('');
  const [catalogResults, setCatalogResults] = useState<ModelRecord[]>([]);
  const [probeConfirmModal, setProbeConfirmModal] = useState<ProbeModalState | null>(null);

  // Results state
  const [resultsSearch, setResultsSearch] = useState<string>('');
  const [resultsOutcomeFilter, setResultsOutcomeFilter] = useState<string>('all');
  const [resultsTab, setResultsTab] = useState<
    'assertions' | 'reviews' | 'diff' | 'lineage' | 'calls' | 'artifacts'
  >('assertions');

  // System state (limits editor). Blank means "not set"; nothing is pre-filled with a default.
  const [sandboxInput, setSandboxInput] = useState<string>('');
  const [reviewInput, setReviewInput] = useState<string>('');

  // ============================================================================
  // Helpers
  // ============================================================================

  const handleCopyText = useCallback((text: string) => {
    try {
      if (navigator.clipboard?.writeText) {
        navigator.clipboard.writeText(text).catch(() => {});
      }
    } catch {
      // clipboard may be blocked; the copied badge below still confirms the action
    }
    setCopiedToken(text);
    setTimeout(() => setCopiedToken(null), 1800);
  }, []);

  const formatTimestamp = useCallback(
    (isoUtc: string | null | undefined): string => {
      if (!isoUtc) return 'Unknown';
      const d = new Date(isoUtc);
      if (isNaN(d.getTime())) return isoUtc;
      if (useUtcTime) return d.toISOString().replace('T', ' ').replace('Z', ' UTC');
      return d.toLocaleString(undefined, {
        month: 'short',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
        hour12: false,
      });
    },
    [useUtcTime]
  );

  const surfaceApiError = useCallback((err: unknown, endpoint?: string) => {
    if (err instanceof ApiHttpError) {
      if (err.status === 503 && dataModeRef.current === 'live') {
        setLiveConnected(false);
        setLiveErrorMsg(err.message);
      }
      const problems = Array.isArray(err.details?.problems) ? (err.details.problems as unknown[]).map(String) : [];
      setApiNotice({
        type: 'error',
        code: `${err.status}-${err.code}`,
        endpoint: err.endpoint,
        idempotencyKey: err.idempotencyKey,
        message: problems.length > 0 ? `${err.message} — ${problems.join('; ')}` : err.message,
      });
      return;
    }
    setApiNotice({
      type: 'error',
      code: 'unexpected-error',
      endpoint,
      message: err instanceof Error ? err.message : String(err),
    });
  }, []);

  // ============================================================================
  // Reads
  // ============================================================================

  const refreshOverviewAndJobs = useCallback(
    async (signal?: AbortSignal, showNotice = false) => {
      try {
        const [ov, list] = await Promise.all([apiClient.getOverview(signal), apiClient.getJobs(signal)]);
        setOverview(ov);
        setStopActive(ov.stop);
        setIndexOk(list.index_ok ?? ov.index_ok);
        setJobs((prev) => {
          const byId = new Map(prev.map((j) => [j.job_id, j]));
          return list.jobs.map((s) => mergeJob(byId.get(s.job_id), s));
        });
        setLastRefresh(new Date().toISOString());
        if (dataModeRef.current === 'live') {
          setLiveConnected(true);
          setLiveLoading(false);
          setLiveErrorMsg(null);
        }
        if (showNotice) {
          setApiNotice({
            type: 'info',
            code: 'refresh-ok',
            endpoint: 'GET /api/overview, GET /api/jobs',
            message: `Refreshed overview and jobs at ${formatTimestamp(new Date().toISOString())}.`,
          });
        }
      } catch (err) {
        if ((err as { name?: string })?.name === 'AbortError') return;
        setLiveLoading(false);
        surfaceApiError(err, 'GET /api/overview');
      }
    },
    [apiClient, formatTimestamp, surfaceApiError]
  );

  const refreshModels = useCallback(
    async (signal?: AbortSignal) => {
      try {
        const res = await apiClient.getModels(signal);
        setModels(res.models);
        setSelectedModelId((prev) => prev ?? res.models[0]?.model_id ?? null);
      } catch (err) {
        if ((err as { name?: string })?.name === 'AbortError') return;
        surfaceApiError(err, 'GET /api/models');
      }
    },
    [apiClient, surfaceApiError]
  );

  const refreshStages = useCallback(
    async (profile: ProfileName, signal?: AbortSignal) => {
      try {
        const res = await apiClient.getStages(profile, signal);
        setStageView((prev) => ({ ...prev, [profile]: res }));
        setStageDrafts((prev) => ({ ...prev, [profile]: res.stages }));
      } catch (err) {
        if ((err as { name?: string })?.name === 'AbortError') return;
        surfaceApiError(err, `GET /api/stages?profile=${profile}`);
      }
    },
    [apiClient, surfaceApiError]
  );

  const refreshHealthAndLimits = useCallback(
    async (signal?: AbortSignal) => {
      try {
        const [h, l] = await Promise.all([apiClient.getSystemHealth(signal), apiClient.getSystemLimits(signal)]);
        setHealth(h);
        setLimits(l);
        if (h.stop !== null) setStopActive(h.stop);
      } catch (err) {
        if ((err as { name?: string })?.name === 'AbortError') return;
        surfaceApiError(err, 'GET /api/system/health');
      }
    },
    [apiClient, surfaceApiError]
  );

  const loadJobDetail = useCallback(
    async (jobId: string, signal?: AbortSignal) => {
      try {
        const detail = await apiClient.getLoop(jobId, signal);
        setJobs((prev) => prev.map((j) => (j.job_id === jobId ? mergeJob(j, detail) : j)));
      } catch (err) {
        if ((err as { name?: string })?.name === 'AbortError') return;
        surfaceApiError(err, `GET /api/loops/${jobId}`);
      }
    },
    [apiClient, surfaceApiError]
  );

  const loadResult = useCallback(
    async (jobId: string, signal?: AbortSignal) => {
      try {
        const r = await apiClient.getResult(jobId, signal);
        // The diff is requested only when the result says one exists (diff_available). Otherwise the API answers 404, and that
        // is not a diff to load: the Results page says so instead.
        const files =
          r.diff_available === true
            ? await apiClient.getResultDiff(jobId, signal).catch(() => [] as Array<{ path: string; unified_diff: string }>)
            : [];
        setResult(r);
        setDiffFiles(files);
      } catch (err) {
        if ((err as { name?: string })?.name === 'AbortError') return;
        setResult(null);
        setDiffFiles([]);
        surfaceApiError(err, `GET /api/results/${jobId}`);
      }
    },
    [apiClient, surfaceApiError]
  );

  // Initial load, then the 5 s overview and jobs poll (contract section 0).
  // Reloads the overview, jobs, models and health whenever the data mode changes, then polls the overview and jobs.
  useEffect(() => {
    const controller = new AbortController();
    void refreshOverviewAndJobs(controller.signal);
    void refreshModels(controller.signal);
    void refreshHealthAndLimits(controller.signal);
    const timer = setInterval(() => void refreshOverviewAndJobs(controller.signal), 5000);
    return () => {
      controller.abort();
      clearInterval(timer);
    };
  }, [dataMode, refreshOverviewAndJobs, refreshModels, refreshHealthAndLimits]);

  // Stage view profile changes.
  useEffect(() => {
    const controller = new AbortController();
    void refreshStages(stageViewProfile, controller.signal);
    return () => controller.abort();
  }, [stageViewProfile, refreshStages, dataMode]);

  // Detail for every listed job that has none yet. The list of ids drives this effect.
  const jobIdKey = jobs.map((j) => j.job_id).join('|');
  useEffect(() => {
    const controller = new AbortController();
    for (const j of jobs) {
      if (j.stages === undefined) void loadJobDetail(j.job_id, controller.signal);
    }
    return () => controller.abort();
    // `jobs` is read through `jobIdKey`; a detail merge must not re-run this effect.
  }, [jobIdKey, loadJobDetail]);

  // Default selections, so a page opens on a real job rather than an empty pane.
  useEffect(() => {
    if (!selectedLoopJobId && jobs.length > 0) setSelectedLoopJobId(jobs[0].job_id);
  }, [jobs, selectedLoopJobId]);
  useEffect(() => {
    if (!selectedResultJobId && jobs.length > 0) setSelectedResultJobId(jobs[0].job_id);
  }, [jobs, selectedResultJobId]);

  // Selected loop: detail every 3 s, new events every 2 s (?after=<seq>).
  useEffect(() => {
    if (!selectedLoopJobId) return;
    eventBufferRef.current.resetForJob(selectedLoopJobId);
    setLoopEvents([]);
    const controller = new AbortController();
    const pollDetail = () => loadJobDetail(selectedLoopJobId, controller.signal);
    const pollEvents = async () => {
      try {
        const page: EventPage = await apiClient.getLoopEvents(
          selectedLoopJobId,
          eventBufferRef.current.nextAfter,
          controller.signal
        );
        const merged = eventBufferRef.current.appendPage(page);
        setLoopEventsIndexOk(page.index_ok);
        setLoopEvents(merged);
      } catch (err) {
        if ((err as { name?: string })?.name === 'AbortError') return;
        surfaceApiError(err, `GET /api/loops/${selectedLoopJobId}/events`);
      }
    };
    void pollDetail();
    void pollEvents();
    const detailTimer = setInterval(pollDetail, 3000);
    const eventsTimer = setInterval(pollEvents, 2000);
    return () => {
      controller.abort();
      clearInterval(detailTimer);
      clearInterval(eventsTimer);
    };
  }, [selectedLoopJobId, apiClient, loadJobDetail, surfaceApiError]);

  // Result detail for the selected job.
  useEffect(() => {
    if (activePage !== '/results' || !selectedResultJobId) return;
    const controller = new AbortController();
    void loadResult(selectedResultJobId, controller.signal);
    return () => controller.abort();
  }, [activePage, selectedResultJobId, loadResult]);

  // Page-specific refresh on navigation.
  useEffect(() => {
    const controller = new AbortController();
    if (activePage === '/models') void refreshModels(controller.signal);
    if (activePage === '/system') void refreshHealthAndLimits(controller.signal);
    if (activePage === '/stages' || activePage === '/jobs/new') void refreshStages(stageViewProfile, controller.signal);
    return () => controller.abort();
  }, [activePage, refreshModels, refreshHealthAndLimits, refreshStages, stageViewProfile]);

  // Keep the limits editor showing the backend's current values (blank until known).
  useEffect(() => {
    setSandboxInput(overview?.sandbox_slots.total != null ? String(overview.sandbox_slots.total) : '');
  }, [overview?.sandbox_slots.total]);
  useEffect(() => {
    setReviewInput(overview?.review_usd_today.cap != null ? String(overview.review_usd_today.cap) : '');
  }, [overview?.review_usd_today.cap]);

  // ============================================================================
  // Mode switch
  // ============================================================================

  const handleSwitchDataMode = useCallback((nextMode: DataMode) => {
    dataModeRef.current = nextMode;
    setDataMode(nextMode);
    setLiveConnected(false);
    setLiveErrorMsg(null);
    setLiveLoading(nextMode === 'live');
    setOverview(null);
    setJobs([]);
    setModels([]);
    setHealth(null);
    setLimits(null);
    setStageView({});
    setStageDrafts({});
    setStopActive(null);
    setGeneratedPlan(null);
    setSelectedLoopJobId(null);
    setLoopEvents([]);
    eventBufferRef.current = new EventStreamBuffer('');
    setSelectedResultJobId(null);
    setResult(null);
    setDiffFiles([]);
    setSelectedModelId(null);
    setApiNotice(
      nextMode === 'mock'
        ? {
            type: 'info',
            code: 'mock-mode',
            message: 'MOCK mode replays the contract fixtures in monitor/fixtures/. Nothing here is live.',
          }
        : null
    );
  }, []);

  // ============================================================================
  // Mutations (each carries an Idempotency-Key; the intent key is reused on double-click)
  // ============================================================================

  const handleConfirmSoftStop = useCallback(
    async (nextStopOn: boolean) => {
      setPendingAction('stop');
      const intent = `system:stop:${nextStopOn}`;
      const idemKey = getOrCreateIdempotencyKey(intent);
      try {
        const res = await apiClient.setGlobalStop(nextStopOn, idemKey);
        idempotencyMgrRef.current.clearIntent(intent);
        setStopActive(res.stop);
        setStopModalOpen(false);
        setApiNotice({
          type: res.stop === false ? 'success' : 'warning',
          code: res.stop ? 'stop-on' : 'stop-off',
          endpoint: 'POST /api/system/stop',
          idempotencyKey: idemKey,
          message: res.stop
            ? 'STOP is on. No new starts; a model call already in flight finishes first.'
            : 'STOP is off. Loop workers may start queued jobs again.',
        });
        await refreshOverviewAndJobs();
      } catch (err) {
        surfaceApiError(err, 'POST /api/system/stop');
      } finally {
        setPendingAction(null);
      }
    },
    [apiClient, getOrCreateIdempotencyKey, refreshOverviewAndJobs, surfaceApiError]
  );

  const handleValidateJobPath = useCallback(async () => {
    setPendingAction('validate-job');
    const idemKey = getOrCreateIdempotencyKey(`validate:${jobPathInput}`);
    try {
      const res = await apiClient.validateJob({ job_path: jobPathInput }, idemKey);
      setJobValidated(res.valid);
      setJobProblems(res.problems);
      setApiNotice({
        type: res.valid ? 'success' : 'warning',
        code: res.valid ? 'job-valid' : 'job-invalid',
        endpoint: 'POST /api/jobs/validate',
        idempotencyKey: idemKey,
        message: res.valid ? `"${jobPathInput}" is valid.` : `"${jobPathInput}" has ${res.problems.length} problem(s).`,
      });
    } catch (err) {
      setJobValidated(false);
      surfaceApiError(err, 'POST /api/jobs/validate');
    } finally {
      setPendingAction(null);
    }
  }, [apiClient, getOrCreateIdempotencyKey, jobPathInput, surfaceApiError]);

  const runProfileRows = stageDrafts[stageViewProfile] ?? [];
  const runProfileRevision = stageView[stageViewProfile]?.revision ?? null;

  const handleValidateBudget = useCallback(async () => {
    setPendingAction('validate-budget');
    const budget = Number(paidBudgetUsd);
    const idemKey = getOrCreateIdempotencyKey(`budget:${paidBudgetUsd}:${stageViewProfile}`);
    try {
      const res = await apiClient.validateBudget({ stages: runProfileRows, paid_budget_usd: budget }, idemKey);
      setBudgetValidated(res.valid);
      setApiNotice({
        type: res.valid ? 'success' : 'warning',
        code: res.valid ? 'budget-valid' : 'budget-invalid',
        endpoint: 'POST /api/budget/validate',
        idempotencyKey: idemKey,
        message: res.valid
          ? `Budget valid. Worst case: ${res.worst_case_usd === null ? 'Unknown' : `$${res.worst_case_usd}`}.`
          : res.problems.join('; ') || 'Budget refused.',
      });
    } catch (err) {
      setBudgetValidated(false);
      surfaceApiError(err, 'POST /api/budget/validate');
    } finally {
      setPendingAction(null);
    }
  }, [apiClient, getOrCreateIdempotencyKey, paidBudgetUsd, runProfileRows, stageViewProfile, surfaceApiError]);

  const handleQueueJob = useCallback(
    async (isRerun: boolean) => {
      if (runProfileRevision === null) {
        setApiNotice({
          type: 'error',
          code: 'stages-not-loaded',
          message: 'Stage settings are not loaded from the backend, so the job cannot be queued.',
        });
        return;
      }
      setPendingAction(isRerun ? 'rerun-job' : 'queue-job');
      const intent = `queue:${jobPathInput}:rerun=${isRerun}`;
      const idemKey = getOrCreateIdempotencyKey(intent, isRerun);
      try {
        const res = await apiClient.queueJob(
          {
            job_path: jobPathInput,
            rerun: isRerun,
            stages: runProfileRows,
            stage_revision: runProfileRevision,
            paid_budget_usd: Number(paidBudgetUsd),
          },
          idemKey
        );
        idempotencyMgrRef.current.clearIntent(intent);
        setApiNotice({
          type: 'success',
          code: isRerun ? 'queued-rerun' : 'queued-job',
          endpoint: `POST /api/jobs (rerun=${isRerun})`,
          idempotencyKey: idemKey,
          message: `Queued${res.queue_file ? ` as ${res.queue_file}` : ''}. Stage snapshot revision: ${
            res.stage_snapshot?.revision ?? 'Unknown'
          }.`,
        });
        await refreshOverviewAndJobs();
      } catch (err) {
        if (err instanceof ApiHttpError && err.code === 'already-completed') {
          setApiNotice({
            type: 'warning',
            code: err.code,
            endpoint: err.endpoint,
            message: `${err.message}. Use "Run again" to send rerun=true.`,
          });
          return;
        }
        surfaceApiError(err, 'POST /api/jobs');
      } finally {
        setPendingAction(null);
      }
    },
    [
      apiClient,
      getOrCreateIdempotencyKey,
      jobPathInput,
      paidBudgetUsd,
      refreshOverviewAndJobs,
      runProfileRevision,
      runProfileRows,
      surfaceApiError,
    ]
  );

  const handleStartWorkers = useCallback(async () => {
    setPendingAction('start-workers');
    const intent = `loops:start:${workerCountToStart}`;
    const idemKey = getOrCreateIdempotencyKey(intent);
    try {
      const res = await apiClient.startLoops(workerCountToStart, idemKey);
      idempotencyMgrRef.current.clearIntent(intent);
      setApiNotice({
        type: 'success',
        code: 'loops-started',
        endpoint: 'POST /api/loops/start',
        idempotencyKey: idemKey,
        message: `Started ${res.started ?? 'Unknown'} loop(s).`,
      });
      await refreshOverviewAndJobs();
    } catch (err) {
      surfaceApiError(err, 'POST /api/loops/start');
    } finally {
      setPendingAction(null);
    }
  }, [apiClient, getOrCreateIdempotencyKey, refreshOverviewAndJobs, surfaceApiError, workerCountToStart]);

  const handleCreatePlan = useCallback(async () => {
    setPendingAction('create-plan');
    const idemKey = getOrCreateIdempotencyKey(`plan:${planName}:${planPrompt}:${planFiles}`);
    const files = planFiles.split('\n').map((s) => s.trim()).filter(Boolean);
    try {
      const created = await apiClient.createPlan({ name: planName, request: planPrompt, files }, idemKey);
      // The plan is drafted asynchronously: read it until a settled state, with backoff and no cutoff (I61). Remembered for this
      // session, so a reload resumes the wait.
      rememberPendingPlan(created.plan_id);
      const plan = await pollPlanUntilSettled({
        initial: created,
        fetchPlan: () => apiClient.getPlan(created.plan_id),
        onUpdate: (p) => setGeneratedPlan(p),
      });
      rememberPendingPlan(null);
      setGeneratedPlan(plan);
      setPlanApproveAssumed(false);
      setApiNotice({
        type: plan.state === 'planned' ? 'success' : 'info',
        code: `plan-${plan.state ?? 'unknown'}`,
        endpoint: 'POST /api/plans, GET /api/plans/{id}',
        idempotencyKey: idemKey,
        message:
          plan.state === 'planned'
            ? `Plan ${plan.plan_id} is planned. Review the assumed items before approving.`
            : `Plan ${plan.plan_id} state: ${plan.state ?? 'Unknown'}.`,
      });
    } catch (err) {
      surfaceApiError(err, 'POST /api/plans');
    } finally {
      setPendingAction(null);
    }
  }, [apiClient, getOrCreateIdempotencyKey, planFiles, planName, planPrompt, surfaceApiError]);

  // A plan this session was still drafting when the page was reloaded: resume its polling until it settles (I61).
  useEffect(() => {
    const planId = pendingPlanId();
    if (!planId) return;
    const controller = new AbortController();
    void (async () => {
      try {
        const initial = await apiClient.getPlan(planId, controller.signal);
        const plan = await pollPlanUntilSettled({
          initial,
          fetchPlan: () => apiClient.getPlan(planId, controller.signal),
          signal: controller.signal,
          onUpdate: (p) => setGeneratedPlan(p),
        });
        rememberPendingPlan(null);
        setGeneratedPlan(plan);
      } catch {
        /* aborted (unmount) or the plan cannot be read: nothing to resume */
      }
    })();
    return () => controller.abort();
  }, [apiClient]);

  const handleApprovePlan = useCallback(async () => {
    if (!generatedPlan || !generatedPlan.job_sha256) return;
    setPendingAction('approve-plan');
    const intent = `approve:${generatedPlan.plan_id}`;
    const idemKey = getOrCreateIdempotencyKey(intent);
    try {
      const res = await apiClient.approvePlan(
        generatedPlan.plan_id,
        { job_sha256: generatedPlan.job_sha256, approve_assumed: planApproveAssumed },
        idemKey
      );
      idempotencyMgrRef.current.clearIntent(intent);
      setApiNotice({
        type: 'success',
        code: 'plan-approved',
        endpoint: `POST /api/plans/${generatedPlan.plan_id}/approve`,
        idempotencyKey: idemKey,
        message: `Plan approved; the job${res.job_id ? ` ${res.job_id}` : ''} is started.`,
      });
      await refreshOverviewAndJobs();
    } catch (err) {
      surfaceApiError(err, `POST /api/plans/${generatedPlan.plan_id}/approve`);
    } finally {
      setPendingAction(null);
    }
  }, [apiClient, generatedPlan, getOrCreateIdempotencyKey, planApproveAssumed, refreshOverviewAndJobs, surfaceApiError]);

  const handlePatchFutureStage = useCallback(async () => {
    const job = jobs.find((j) => j.job_id === selectedLoopJobId);
    const revision = job?.stage_snapshot?.revision;
    if (!job || revision === null || revision === undefined) {
      setApiNotice({
        type: 'error',
        code: 'snapshot-unknown',
        message: 'This job\'s stage snapshot revision is not loaded, so a future stage cannot be edited.',
      });
      return;
    }
    const intent = `patch-loop:${job.job_id}:${futureStageTarget}:${futureStageEffort}`;
    const idemKey = getOrCreateIdempotencyKey(intent);
    try {
      const res = await apiClient.patchLoopStage(
        job.job_id,
        { stage: futureStageTarget, change: { effort: futureStageEffort }, revision },
        idemKey
      );
      idempotencyMgrRef.current.clearIntent(intent);
      setApiNotice({
        type: 'success',
        code: 'future-stage-patched',
        endpoint: `PATCH /api/loops/${job.job_id}/stages`,
        idempotencyKey: idemKey,
        message: `Updated the future stage ${futureStageTarget}. Snapshot revision: ${res.revision ?? 'Unknown'}.`,
      });
      await loadJobDetail(job.job_id);
    } catch (err) {
      if (err instanceof ApiHttpError && (err.code === 'stale-revision' || err.code === 'stage-started')) {
        setStageConflictModal({
          clientRevision: String(revision),
          serverRevision: 'Unknown (reload to see)',
          context: 'future_job_stage',
        });
      }
      surfaceApiError(err, `PATCH /api/loops/${job.job_id}/stages`);
    }
  }, [apiClient, futureStageEffort, futureStageTarget, getOrCreateIdempotencyKey, jobs, loadJobDetail, selectedLoopJobId, surfaceApiError]);

  const handleRefreshModels = useCallback(async () => {
    setPendingAction('refresh-models');
    const idemKey = getOrCreateIdempotencyKey('models:refresh');
    try {
      const res = await apiClient.refreshModels(idemKey);
      idempotencyMgrRef.current.clearIntent('models:refresh');
      await refreshModels();
      setApiNotice({
        type: 'success',
        code: 'prices-refreshed',
        endpoint: 'POST /api/models/refresh',
        idempotencyKey: idemKey,
        message: `Refreshed ${res.updated ?? 'Unknown'} model(s). Metadata at ${formatTimestamp(res.metadata_at)}.`,
      });
    } catch (err) {
      surfaceApiError(err, 'POST /api/models/refresh');
    } finally {
      setPendingAction(null);
    }
  }, [apiClient, formatTimestamp, getOrCreateIdempotencyKey, refreshModels, surfaceApiError]);

  const handleSearchCatalog = useCallback(async () => {
    setPendingAction('search-catalog');
    try {
      setCatalogResults(await apiClient.searchCatalogModels(catalogQuery));
    } catch (err) {
      surfaceApiError(err, 'GET /api/models/search');
    } finally {
      setPendingAction(null);
    }
  }, [apiClient, catalogQuery, surfaceApiError]);

  const handleAddModel = useCallback(
    async (modelId: string) => {
      setPendingAction('add-model');
      const intent = `add-model:${modelId}`;
      const idemKey = getOrCreateIdempotencyKey(intent);
      try {
        const added = await apiClient.addModel(modelId, idemKey);
        idempotencyMgrRef.current.clearIntent(intent);
        setModels((prev) => (prev.some((m) => m.model_id === added.model_id) ? prev : [...prev, added]));
        setSelectedModelId(added.model_id);
        setAddModelModalOpen(false);
        setApiNotice({
          type: 'success',
          code: 'model-added',
          endpoint: 'POST /api/models',
          idempotencyKey: idemKey,
          message: `Added ${added.model_id}. Its contracts are unverified until probed.`,
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
      const intent = `delete-model:${model.model_id}`;
      const idemKey = getOrCreateIdempotencyKey(intent);
      try {
        await apiClient.deleteModel(model.model_id, idemKey);
        idempotencyMgrRef.current.clearIntent(intent);
        setModels((prev) => prev.filter((m) => m.model_id !== model.model_id));
        setApiNotice({
          type: 'success',
          code: 'model-removed',
          endpoint: `DELETE /api/models/${encodeURIComponent(model.model_id)}`,
          idempotencyKey: idemKey,
          message: `Removed ${model.model_id} from the pool.`,
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
    const modal = probeConfirmModal;
    setPendingAction('probe-model');
    try {
      if (modal.probeType === 'verify_contract' && modal.contract) {
        const intent = `probe:${modal.modelId}:${modal.contract}`;
        const idemKey = getOrCreateIdempotencyKey(intent);
        const res: VerifyResult = await apiClient.verifyModelContract(
          { model_id: modal.modelId, contract: modal.contract as SemanticContract },
          idemKey
        );
        idempotencyMgrRef.current.clearIntent(intent);
        setProbeConfirmModal(null);
        // Fail-closed: the UI only says "verified" for a pass. Reload the pool for the stored state.
        await refreshModels();
        if (res.state === 'verified') {
          setApiNotice({
            type: 'success',
            code: 'contract-verified',
            endpoint: 'POST /api/models/verify',
            idempotencyKey: idemKey,
            message: `${modal.contract} verified for ${modal.modelId}.`,
          });
        } else if (res.state === 'failed') {
          setApiNotice({
            type: 'warning',
            code: 'contract-not-verified',
            endpoint: 'POST /api/models/verify',
            idempotencyKey: idemKey,
            message: `${modal.contract} FAILED for ${modal.modelId}. The contract is not verified and the model stays out of that stage.`,
          });
        } else {
          setApiNotice({
            type: 'warning',
            code: 'contract-not-verified',
            endpoint: 'POST /api/models/verify',
            idempotencyKey: idemKey,
            message: `The verify response had no usable result. ${modal.contract} stays unverified.`,
          });
        }
      } else {
        const model = models.find((m) => m.model_id === modal.modelId);
        const efforts = (model?.capabilities.efforts ?? []).filter((e): e is Effort => e !== 'off');
        const intent = `reasoning:${modal.modelId}:${efforts.join(',')}`;
        const idemKey = getOrCreateIdempotencyKey(intent);
        const res = await apiClient.verifyModelReasoning({ model_id: modal.modelId, efforts }, idemKey);
        idempotencyMgrRef.current.clearIntent(intent);
        setProbeConfirmModal(null);
        await refreshModels();
        setApiNotice({
          type: 'success',
          code: 'reasoning-tested',
          endpoint: 'POST /api/models/verify-reasoning',
          idempotencyKey: idemKey,
          message: `Reasoning mode: ${res.reasoning_mode ?? 'Unknown'}. Efforts verified: ${
            res.efforts_verified.join(', ') || 'none'
          }.`,
        });
      }
    } catch (err) {
      surfaceApiError(
        err,
        modal.probeType === 'verify_contract' ? 'POST /api/models/verify' : 'POST /api/models/verify-reasoning'
      );
    } finally {
      setPendingAction(null);
    }
  }, [apiClient, getOrCreateIdempotencyKey, models, probeConfirmModal, refreshModels, surfaceApiError]);

  const handleValidateStages = useCallback(async () => {
    setPendingAction('validate-stages');
    const idemKey = getOrCreateIdempotencyKey(`stages:validate:${stageViewProfile}:${JSON.stringify(runProfileRows)}`);
    try {
      const res = await apiClient.validateStages(
        { profile: stageViewProfile, changes: buildStageChanges(runProfileRows) },
        idemKey
      );
      setApiNotice({
        type: res.valid ? 'success' : 'warning',
        code: res.valid ? 'stages-valid' : 'stages-invalid',
        endpoint: 'POST /api/stages/validate',
        idempotencyKey: idemKey,
        message: res.valid
          ? `The stage settings for "${stageViewProfile}" are valid.`
          : res.problems.map((p) => `${p.stage} ${p.code}: ${p.message}`).join('; '),
      });
    } catch (err) {
      surfaceApiError(err, 'POST /api/stages/validate');
    } finally {
      setPendingAction(null);
    }
  }, [apiClient, getOrCreateIdempotencyKey, runProfileRows, stageViewProfile, surfaceApiError]);

  const handlePutStages = useCallback(async () => {
    if (runProfileRevision === null) return;
    setPendingAction('put-stages');
    const idemKey = getOrCreateIdempotencyKey(`stages:put:${stageViewProfile}:${runProfileRevision}`, true);
    try {
      const res = await apiClient.putStages(
        { profile: stageViewProfile, revision: runProfileRevision, changes: buildStageChanges(runProfileRows) },
        idemKey
      );
      idempotencyMgrRef.current.clearIntent(`stages:put:${stageViewProfile}:${runProfileRevision}`);
      await refreshStages(stageViewProfile);
      setApiNotice({
        type: 'success',
        code: 'stages-saved',
        endpoint: 'PUT /api/stages',
        idempotencyKey: idemKey,
        message: `Stage settings saved as revision ${res.revision ?? 'Unknown'}. They apply to future jobs only.`,
      });
    } catch (err) {
      if (err instanceof ApiHttpError && err.status === 409) {
        setStageConflictModal({
          clientRevision: String(runProfileRevision),
          serverRevision: 'Unknown (reload to see)',
          context: 'global_stages',
        });
      }
      surfaceApiError(err, 'PUT /api/stages');
    } finally {
      setPendingAction(null);
    }
  }, [apiClient, getOrCreateIdempotencyKey, refreshStages, runProfileRevision, runProfileRows, stageViewProfile, surfaceApiError]);

  const handleSaveSystemLimits = useCallback(async () => {
    // PUT /system/limits needs the revision the editor was loaded at (GET /system/limits). Never invented: no revision, no save.
    const revision = limits?.revision ?? null;
    if (revision === null) return;
    setPendingAction('put-limits');
    const sandbox = sandboxInput.trim() === '' ? undefined : Number(sandboxInput);
    const review = reviewInput.trim() === '' ? undefined : Number(reviewInput);
    const idemKey = getOrCreateIdempotencyKey(`limits:put:${sandboxInput}:${reviewInput}`);
    try {
      const res = await apiClient.putSystemLimits(
        { revision, sandbox_slots: sandbox, review_usd_daily_cap: review },
        idemKey
      );
      idempotencyMgrRef.current.clearIntent(`limits:put:${sandboxInput}:${reviewInput}`);
      setApiNotice({
        type: 'success',
        code: 'limits-saved',
        endpoint: 'PUT /api/system/limits',
        idempotencyKey: idemKey,
        message: `Limits saved as revision ${res.revision ?? 'Unknown'}. They apply to new starts only.`,
      });
      await refreshHealthAndLimits();
    } catch (err) {
      // 409 stale-revision: someone saved the limits since this page loaded. The notice shows the backend's message (its current revision);
      // the page reloads the limits so the next save uses that revision. 422 not-applied-by-engine shows the engine's reason in the notice.
      surfaceApiError(err, 'PUT /api/system/limits');
      if (err instanceof ApiHttpError && err.status === 409) await refreshHealthAndLimits();
    } finally {
      setPendingAction(null);
    }
  }, [apiClient, getOrCreateIdempotencyKey, limits, refreshHealthAndLimits, reviewInput, sandboxInput, surfaceApiError]);

  const handleDownloadArchive = useCallback(
    async (jobId: string) => {
      setPendingAction('download-archive');
      try {
        const res = await apiClient.downloadResultArchive(jobId);
        setApiNotice({
          type: 'success',
          code: 'archive-downloaded',
          endpoint: `GET /api/results/${jobId}/download`,
          message: `Downloaded "${res.filename}" (${res.sizeBytes} bytes).`,
        });
      } catch (err) {
        surfaceApiError(err, `GET /api/results/${jobId}/download`);
      } finally {
        setPendingAction(null);
      }
    },
    [apiClient, surfaceApiError]
  );

  // ============================================================================
  // Derived values
  // ============================================================================

  const filteredModels = useMemo(() => {
    return models.filter((m) => {
      if (modelSearch && !m.model_id.toLowerCase().includes(modelSearch.toLowerCase())) return false;
      if (modelBillingFilter === 'subscription' && m.via !== 'claude_code') return false;
      if (modelBillingFilter === 'paid' && m.paid !== true) return false;
      if (modelBillingFilter === 'free' && m.paid !== false) return false;
      if (modelContractFilter !== 'all') {
        const state = m.capabilities.contracts[modelContractFilter as SemanticContract];
        if (state !== 'verified') return false;
      }
      return true;
    });
  }, [models, modelSearch, modelBillingFilter, modelContractFilter]);

  const selectedModel = useMemo(
    () => models.find((m) => m.model_id === selectedModelId) ?? null,
    [models, selectedModelId]
  );

  const selectedLoopJob = useMemo(
    () => jobs.find((j) => j.job_id === selectedLoopJobId) ?? null,
    [jobs, selectedLoopJobId]
  );

  const navItems: Array<{ path: NavPage; label: string; subtitle: string; icon: React.ReactNode }> = [
    { path: '/dashboard', label: 'Dashboard', subtitle: 'Counters, quotas & active loops', icon: <LayoutDashboard className="w-4 h-4" /> },
    { path: '/jobs/new', label: 'Run Jobs', subtitle: 'Job path, planner & queue', icon: <Play className="w-4 h-4" /> },
    { path: '/loops', label: 'Live Loops', subtitle: 'Attempt timeline & inspector', icon: <Activity className="w-4 h-4" /> },
    { path: '/models', label: 'Models', subtitle: 'Model pool & contracts', icon: <Cpu className="w-4 h-4" /> },
    { path: '/stages', label: 'Stage Settings', subtitle: '10 configurable stage types', icon: <Sliders className="w-4 h-4" /> },
    { path: '/results', label: 'Results', subtitle: 'Verdicts, diff & archive', icon: <Archive className="w-4 h-4" /> },
    { path: '/system', label: 'System', subtitle: 'Health, leases, gates & STOP', icon: <Server className="w-4 h-4" /> },
  ];

  const bgCanvas = lightTheme ? 'bg-slate-100 text-slate-900' : 'bg-[#08090c] text-slate-100';
  const bgPanel = lightTheme ? 'bg-white border-slate-300' : 'bg-[#101217] border-slate-800';
  const bgSubtle = lightTheme ? 'bg-slate-50 border-slate-200' : 'bg-[#151821] border-slate-800/80';
  const textMuted = lightTheme ? 'text-slate-600' : 'text-slate-400';

  const modeBanner =
    dataMode === 'mock'
      ? 'bg-sky-950/80 border-sky-700/60 text-sky-100'
      : liveConnected
      ? 'bg-emerald-950/80 border-emerald-700/60 text-emerald-100'
      : 'bg-rose-950/90 border-rose-700 text-rose-100';

  return (
    <div className={`min-h-screen flex flex-col font-sans ${bgCanvas}`}>
      <div className={`border-b px-3 py-2 flex flex-wrap items-center justify-between gap-2 text-xs ${modeBanner}`}>
        <div className="flex flex-wrap items-center gap-2">
          <span className="inline-flex items-center gap-1.5 font-mono font-bold px-2 py-0.5 rounded bg-black/40 border border-white/15">
            {dataMode === 'mock' ? (
              <>MOCK / FIXTURE REPLAY (NOT LIVE)</>
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
                LIVE / DISCONNECTED
              </>
            )}
          </span>
          <span className="hidden md:inline text-slate-300">
            Engine: <code className="font-mono text-sky-300">{health?.engine_version ?? 'Unknown'}</code>
          </span>
        </div>

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
            <span className="font-bold tracking-tight text-sm md:text-base">SATVIK LOOP CONTROL CENTER</span>
            {indexOk === false && (
              <span
                title="index_ok: false. The data came straight from the files (slower, still correct)."
                className="text-[11px] font-mono px-1.5 py-0.5 rounded bg-amber-500/20 text-amber-300 border border-amber-500/40"
              >
                INDEX_OK: FALSE (FILE-BACKED)
              </span>
            )}
          </div>

          <div className="flex flex-wrap items-center gap-2 text-xs font-mono">
            <span className="px-2 py-1 rounded border border-slate-800 bg-slate-900/40">
              RUN: <strong className="text-sky-400">{overview?.loops_running ?? 'Unknown'}</strong>
            </span>
            <span className="px-2 py-1 rounded border border-slate-800 bg-slate-900/40">
              QUEUE: <strong className="text-slate-200">{overview?.queue_depth ?? 'Unknown'}</strong>
            </span>
            <span className="px-2 py-1 rounded border border-slate-800 bg-slate-900/40">
              FREE USED: <strong>{overview?.free_today.used ?? 'Unknown'}</strong>/
              {overview?.free_today.cap ?? 'Unknown'}
            </span>
            <span
              className={`px-2 py-1 rounded border ${
                overview?.pace.limit != null &&
                overview.pace.sent_last_60s != null &&
                overview.pace.sent_last_60s >= overview.pace.limit
                  ? 'border-rose-600 bg-rose-950/60 text-rose-200'
                  : 'border-slate-800 bg-slate-900/40'
              }`}
            >
              PACE 60s: <strong>{overview?.pace.sent_last_60s ?? 'Unknown'}</strong>/{overview?.pace.limit ?? 'Unknown'}
            </span>
            <span className="px-2 py-1 rounded border border-slate-800 bg-slate-900/40">
              REVIEWS: <strong className="text-amber-400">{overview?.pending_reviews ?? 'Unknown'}</strong>
            </span>

            <button
              type="button"
              onClick={() => setUseUtcTime((u) => !u)}
              className="px-2 py-1 rounded border border-slate-700 hover:border-sky-400 text-[11px]"
              title={`Last refresh: ${lastRefresh ?? 'Pending'}`}
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
              onClick={() => refreshOverviewAndJobs(undefined, true)}
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
              {stopActive === null ? 'GLOBAL STOP (UNKNOWN)' : stopActive ? 'STOP ON (SOFT)' : 'GLOBAL STOP'}
            </button>
          </div>
        </div>
      </header>

      <div className="flex-1 flex overflow-hidden">
        <aside
          aria-label="Primary 7-Page Navigation"
          className={`${
            mobileNavOpen ? 'block fixed inset-y-0 left-0 z-40 w-64 shadow-2xl' : 'hidden'
          } lg:block lg:static lg:w-60 shrink-0 border-r ${bgPanel} flex flex-col justify-between`}
        >
          <div className="p-3 space-y-1">
            <div className="flex items-center justify-between lg:hidden pb-2 mb-2 border-b border-slate-800">
              <span className="font-mono text-xs uppercase tracking-wider text-sky-400">7-Page Navigation</span>
              <button type="button" onClick={() => setMobileNavOpen(false)} className="p-1 rounded border border-slate-700">
                <X className="w-4 h-4" />
              </button>
            </div>

            {navItems.map((item) => {
              const isActive = activePage === item.path;
              return (
                <button
                  key={item.path}
                  type="button"
                  data-testid={`nav-${item.path.replace('/', '').replace('/', '-') || 'dashboard'}`}
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
              Soft STOP only. No cancel, resume or manual merge. The merge is automatic after a pass.
            </p>
            <div className="font-mono text-[10px] text-slate-400">Refreshed: {formatTimestamp(lastRefresh)}</div>
          </div>
        </aside>

        <main className="flex-1 min-w-0 overflow-y-auto p-3 md:p-5 space-y-4">
          {dataMode === 'live' && liveLoading && (
            <div role="status" className="rounded-md border border-sky-600 bg-sky-950/60 p-4 text-sky-100 font-mono text-xs flex items-center gap-3">
              <RefreshCw className="w-4 h-4 text-sky-400" />
              Loading state from http://127.0.0.1:8765/api...
            </div>
          )}

          {dataMode === 'live' && !liveLoading && !liveConnected && (
            <div role="alert" className="rounded-md border border-rose-600 bg-rose-950/90 p-3 text-rose-100 flex flex-wrap items-center justify-between gap-3" data-testid="live-disconnected-banner">
              <div className="space-y-1">
                <div className="font-bold text-sm flex items-center gap-2">
                  <WifiOff className="w-4 h-4 text-rose-400" />
                  LIVE BACKEND DISCONNECTED (http://127.0.0.1:8765/api)
                </div>
                <p className="text-xs text-rose-200">
                  {liveErrorMsg || 'Cannot reach the FastAPI backend. Nothing is shown from fixtures.'}
                </p>
              </div>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => refreshOverviewAndJobs(undefined, true)}
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
              data-testid="api-notice"
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
                  <span className="font-bold uppercase px-1.5 py-0.5 rounded bg-black/30">{apiNotice.code}</span>
                  {apiNotice.endpoint && <span className="text-sky-300">{apiNotice.endpoint}</span>}
                  {apiNotice.idempotencyKey && (
                    <span className="text-slate-300">
                      Idempotency-Key: <code>{apiNotice.idempotencyKey}</code>
                    </span>
                  )}
                </div>
                <div>{apiNotice.message}</div>
              </div>
              <button type="button" onClick={() => setApiNotice(null)} className="p-1 rounded hover:bg-black/20" aria-label="Dismiss Notice">
                <X className="w-4 h-4" />
              </button>
            </div>
          )}

          {activePage === '/dashboard' && (
            <DashboardPage
              overview={overview}
              jobs={jobs}
              stopActive={stopActive}
              indexOk={indexOk}
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
              profile={stageViewProfile}
              stageRevision={runProfileRevision}
              stages={runProfileRows}
              runEntryMode={runEntryMode}
              jobPathInput={jobPathInput}
              jobValidated={jobValidated}
              jobProblems={jobProblems}
              budgetValidated={budgetValidated}
              paidBudgetUsd={paidBudgetUsd}
              workerCountToStart={workerCountToStart}
              planName={planName}
              planPrompt={planPrompt}
              planFiles={planFiles}
              generatedPlan={generatedPlan}
              planApproveAssumed={planApproveAssumed}
              pendingAction={pendingAction}
              bgPanel={bgPanel}
              bgSubtle={bgSubtle}
              textMuted={textMuted}
              onChangeProfile={(p) => setStageViewProfile(p)}
              onChangeEntryMode={setRunEntryMode}
              onChangeJobPath={(p) => {
                setJobPathInput(p);
                setJobValidated(null);
                setJobProblems([]);
              }}
              onValidateJobPath={handleValidateJobPath}
              onChangePaidBudget={(val) => {
                setPaidBudgetUsd(val);
                setBudgetValidated(null);
              }}
              onValidateBudget={handleValidateBudget}
              onQueueJob={handleQueueJob}
              onChangeWorkerCount={setWorkerCountToStart}
              onStartWorkers={handleStartWorkers}
              onChangePlanName={setPlanName}
              onChangePlanPrompt={setPlanPrompt}
              onChangePlanFiles={setPlanFiles}
              onCreatePlan={handleCreatePlan}
              onToggleApproveAssumed={setPlanApproveAssumed}
              onApprovePlan={handleApprovePlan}
            />
          )}

          {activePage === '/loops' && (
            <LiveLoopsPage
              dataMode={dataMode}
              jobs={jobs}
              selectedLoopJob={selectedLoopJob}
              events={loopEvents}
              eventsIndexOk={loopEventsIndexOk}
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
              onOpenStopModal={() => setStopModalOpen(true)}
              onChangeLoopFilter={setLoopFilter}
              onSelectLoopJob={setSelectedLoopJobId}
              onToggleFutureStageDrawer={() => setFutureStageDrawerOpen((v) => !v)}
              onChangeFutureStageTarget={setFutureStageTarget}
              onChangeFutureStageEffort={setFutureStageEffort}
              onPatchFutureStage={handlePatchFutureStage}
              onChangeLoopViewMode={setLoopViewMode}
              onChangeInspectorTab={setLoopInspectorTab}
            />
          )}

          {activePage === '/models' && (
            <ModelsPage
              models={models}
              filteredModels={filteredModels}
              selectedModelId={selectedModelId}
              selectedModel={selectedModel}
              modelSearch={modelSearch}
              modelBillingFilter={modelBillingFilter}
              modelContractFilter={modelContractFilter}
              pendingAction={pendingAction}
              bgPanel={bgPanel}
              bgSubtle={bgSubtle}
              textMuted={textMuted}
              onRefreshModels={handleRefreshModels}
              onOpenAddModelModal={() => {
                setAddModelModalOpen(true);
                setCatalogResults([]);
              }}
              onChangeSearch={setModelSearch}
              onChangeBillingFilter={setModelBillingFilter}
              onChangeContractFilter={setModelContractFilter}
              onSelectModel={setSelectedModelId}
              onOpenProbeModal={(params) => setProbeConfirmModal({ ...params, ackChecked: false })}
              onRemoveModel={handleRemoveModel}
            />
          )}

          {activePage === '/stages' && (
            <StageSettingsPage
              profile={stageViewProfile}
              revision={runProfileRevision}
              rows={runProfileRows}
              selectedStageKey={selectedStageKey}
              allModels={models}
              pendingAction={pendingAction}
              bgPanel={bgPanel}
              bgSubtle={bgSubtle}
              textMuted={textMuted}
              onChangeViewProfile={(prof) => setStageViewProfile(prof)}
              onReloadStages={() => refreshStages(stageViewProfile)}
              onSelectStageKey={setSelectedStageKey}
              onChangeStageModel={(stKey, modelId) =>
                setStageDrafts((prev) => ({
                  ...prev,
                  [stageViewProfile]: (prev[stageViewProfile] ?? []).map((r) =>
                    r.stage_key === stKey ? stageRowWithModel(r, modelId, models) : r
                  ),
                }))
              }
              onChangeStageEffort={(stKey, effort) =>
                setStageDrafts((prev) => ({
                  ...prev,
                  [stageViewProfile]: (prev[stageViewProfile] ?? []).map((r) =>
                    r.stage_key === stKey ? { ...r, effort } : r
                  ),
                }))
              }
              onChangeStageMaxTokens={(stKey, maxTokens) =>
                setStageDrafts((prev) => ({
                  ...prev,
                  [stageViewProfile]: (prev[stageViewProfile] ?? []).map((r) =>
                    r.stage_key === stKey
                      ? { ...r, max_output_tokens: maxTokens, settings: { ...r.settings, max_tokens: maxTokens } }
                      : r
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
              selectedResultJobId={selectedResultJobId}
              result={result}
              diffFiles={diffFiles}
              resultsSearch={resultsSearch}
              resultsOutcomeFilter={resultsOutcomeFilter}
              resultsTab={resultsTab}
              pendingAction={pendingAction}
              bgPanel={bgPanel}
              bgSubtle={bgSubtle}
              textMuted={textMuted}
              formatTimestamp={formatTimestamp}
              onChangeSearch={setResultsSearch}
              onChangeOutcomeFilter={setResultsOutcomeFilter}
              onSelectResultJob={(id) => setSelectedResultJobId(id)}
              onChangeTab={setResultsTab}
              onDownloadArchive={handleDownloadArchive}
            />
          )}

          {activePage === '/system' && (
            <SystemPage
              liveConnected={liveConnected}
              stopActive={stopActive}
              health={health}
              limits={limits}
              overview={overview}
              sandboxInput={sandboxInput}
              reviewInput={reviewInput}
              limitsRevision={limits?.revision ?? null}
              pendingAction={pendingAction}
              bgPanel={bgPanel}
              bgSubtle={bgSubtle}
              textMuted={textMuted}
              onRefreshHealth={() => refreshHealthAndLimits()}
              onOpenStopModal={() => setStopModalOpen(true)}
              onChangeSandboxInput={setSandboxInput}
              onChangeReviewInput={setReviewInput}
              onSaveSystemLimits={handleSaveSystemLimits}
            />
          )}
        </main>
      </div>

      <StopConfirmModal
        open={stopModalOpen}
        stopActive={stopActive === true}
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
        onToggleAck={(checked) => setProbeConfirmModal((prev) => (prev ? { ...prev, ackChecked: checked } : null))}
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
        onReloadServerState={async () => {
          setStageConflictModal(null);
          await refreshStages(stageViewProfile);
        }}
      />
    </div>
  );
}
