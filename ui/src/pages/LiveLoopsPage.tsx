import React from 'react';
import { X } from 'lucide-react';
import type { DataMode, JobRecord, StageKey } from '../api/types';

export interface LiveLoopsPageProps {
  dataMode: DataMode;
  jobs: JobRecord[];
  selectedLoopJob: JobRecord | null;
  loopFilter: string;
  loopViewMode: 'timeline_list' | 'flow_diagram';
  loopInspectorTab: 'tests' | 'reviews' | 'diff' | 'lineage' | 'budget' | 'artifacts';
  futureStageDrawerOpen: boolean;
  futureStageTarget: StageKey;
  futureStageEffort: 'low' | 'medium' | 'high';
  indexOk: boolean;
  bgPanel: string;
  bgSubtle: string;
  textMuted: string;
  formatTimestamp: (iso: string | null | undefined) => string;
  onToggleIndexOk: () => void;
  onOpenStopModal: () => void;
  onChangeLoopFilter: (f: string) => void;
  onSelectLoopJob: (jobId: string) => void;
  onToggleFutureStageDrawer: () => void;
  onChangeFutureStageTarget: (st: StageKey) => void;
  onChangeFutureStageEffort: (eff: 'low' | 'medium' | 'high') => void;
  onPatchFutureStage: () => void;
  onSimulate409FutureStage: () => void;
  onSimulateSlotRelease: () => void;
  onChangeLoopViewMode: (m: 'timeline_list' | 'flow_diagram') => void;
  onChangeInspectorTab: (
    t: 'tests' | 'reviews' | 'diff' | 'lineage' | 'budget' | 'artifacts'
  ) => void;
}

export const LiveLoopsPage: React.FC<LiveLoopsPageProps> = ({
  dataMode,
  jobs,
  selectedLoopJob,
  loopFilter,
  loopViewMode,
  loopInspectorTab,
  futureStageDrawerOpen,
  futureStageTarget,
  futureStageEffort,
  indexOk,
  bgPanel,
  bgSubtle,
  textMuted,
  formatTimestamp,
  onToggleIndexOk,
  onOpenStopModal,
  onChangeLoopFilter,
  onSelectLoopJob,
  onToggleFutureStageDrawer,
  onChangeFutureStageTarget,
  onChangeFutureStageEffort,
  onPatchFutureStage,
  onSimulate409FutureStage,
  onSimulateSlotRelease,
  onChangeLoopViewMode,
  onChangeInspectorTab,
}) => {
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="text-lg md:text-xl font-bold tracking-tight">3. Live Loops (/loops)</h1>
          <p className={`text-xs ${textMuted}`}>
            {'Event-ordered attempt timeline (?after=<seq>), wait spans (model-slot, 429-backoff), runtime-only fallback_write, 6 inspector panels, and P1-gated future stage editor.'}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-xs font-mono">
          <button
            type="button"
            onClick={onToggleIndexOk}
            className="px-2.5 py-1 rounded border border-slate-700 hover:border-amber-400"
          >
            Toggle index_ok (Currently: {indexOk ? 'true' : 'false'})
          </button>
          <button
            type="button"
            onClick={onOpenStopModal}
            className="px-2.5 py-1 rounded bg-rose-950/80 text-rose-200 border border-rose-700 hover:bg-rose-900"
          >
            Request Global Soft STOP
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-4 items-start">
        {/* Left Master List (3 cols) */}
        <div className={`lg:col-span-3 rounded-md border p-3 space-y-3 ${bgPanel}`}>
          <div className="flex items-center justify-between">
            <span className="text-xs font-mono font-bold uppercase text-sky-400">
              Loops &amp; History ({jobs.length})
            </span>
            <select
              aria-label="Filter Loops by State"
              value={loopFilter}
              onChange={(e) => onChangeLoopFilter(e.target.value)}
              className="bg-slate-900 border border-slate-700 rounded px-2 py-0.5 text-xs font-mono"
            >
              <option value="all">All States</option>
              <option value="running">running</option>
              <option value="waiting">waiting</option>
              <option value="queued">queued</option>
              <option value="finished">finished</option>
              <option value="merged">merged</option>
            </select>
          </div>

          <div className="space-y-2 max-h-[600px] overflow-y-auto pr-1">
            {jobs
              .filter((j) => loopFilter === 'all' || j.derived_state === loopFilter)
              .map((j) => {
                const isSel = selectedLoopJob?.job_id === j.job_id;
                return (
                  <button
                    key={j.job_id}
                    type="button"
                    onClick={() => onSelectLoopJob(j.job_id)}
                    className={`w-full text-left p-2.5 rounded border transition text-xs font-mono space-y-1 ${
                      isSel
                        ? 'bg-sky-500/15 border-sky-400'
                        : 'bg-slate-900/50 border-slate-800 hover:border-slate-700'
                    }`}
                  >
                    <div className="flex items-center justify-between gap-1">
                      <span className="font-bold text-sky-300 truncate">{j.job_id}</span>
                      <span
                        className={`px-1.5 py-0.5 rounded text-[10px] ${
                          j.derived_state === 'running'
                            ? 'bg-emerald-500/20 text-emerald-300'
                            : j.derived_state === 'waiting'
                            ? 'bg-amber-500/20 text-amber-300'
                            : 'bg-slate-800 text-slate-300'
                        }`}
                      >
                        {j.derived_state}
                      </span>
                    </div>
                    <div className="text-[11px] text-slate-400 truncate">{j.job_path}</div>
                    <div className="text-[10px] text-slate-300 flex justify-between">
                      <span>stage: {j.active_stage || j.end_state || 'queued'}</span>
                      <span>effort: {j.effort}</span>
                    </div>
                  </button>
                );
              })}
          </div>
        </div>

        {/* Center Timeline & Stage Overview (5 cols) */}
        {selectedLoopJob ? (
          <div className="lg:col-span-5 space-y-3">
            <div className={`rounded-md border p-3.5 space-y-2.5 ${bgPanel}`}>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="font-mono">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-bold text-sky-300">
                      {selectedLoopJob.job_id}
                    </span>
                    <span className="px-2 py-0.5 rounded text-[11px] bg-sky-500/15 text-sky-300 border border-sky-500/30">
                      {selectedLoopJob.derived_state}
                    </span>
                    {selectedLoopJob.restored_green && (
                      <span className="px-2 py-0.5 rounded text-[11px] bg-emerald-500/20 text-emerald-300 border border-emerald-500/40">
                        restored-green
                      </span>
                    )}
                  </div>
                  <div className="text-[11px] text-slate-400 mt-0.5">
                    {selectedLoopJob.job_path} · rev: <code>{selectedLoopJob.config_revision}</code>
                  </div>
                </div>

                <div className="flex items-center gap-1.5">
                  <button
                    type="button"
                    onClick={onToggleFutureStageDrawer}
                    className="px-2.5 py-1 rounded border border-sky-500/40 bg-sky-500/15 text-sky-300 text-xs font-mono"
                  >
                    Edit Future Stage
                  </button>
                </div>
              </div>

              <div
                className={`p-2.5 rounded border text-[11px] font-mono grid grid-cols-2 sm:grid-cols-3 gap-2 ${bgSubtle}`}
              >
                <div>
                  <span className="text-slate-400">Active Stage:</span>{' '}
                  <strong>{selectedLoopJob.active_stage || 'none (terminal)'}</strong>
                </div>
                <div>
                  <span className="text-slate-400">Attempt ID:</span>{' '}
                  <strong>{selectedLoopJob.active_attempt_id || 'n/a'}</strong>
                </div>
                <div>
                  <span className="text-slate-400">Heartbeat / PID:</span>{' '}
                  <strong>
                    {selectedLoopJob.heartbeat_age_sec}s / {selectedLoopJob.lease_pid ?? 'none'}
                  </strong>
                </div>
                <div className="col-span-2 sm:col-span-3 text-slate-400 border-t border-slate-800 pt-1.5">
                  Safety Invariant: No per-job Cancel, no Resume Failed, no Manual Merge. In-flight
                  stages cannot change model/effort.
                </div>
              </div>

              {selectedLoopJob.waiting && (
                <div className="p-2.5 rounded border border-amber-600/60 bg-amber-950/40 text-amber-100 text-xs flex flex-wrap items-center justify-between gap-2 font-mono">
                  <div>
                    <strong>WAITING ({selectedLoopJob.waiting.why}):</strong> Gate on{' '}
                    <code>{selectedLoopJob.waiting.model}</code> since{' '}
                    {formatTimestamp(selectedLoopJob.waiting.since)}
                  </div>
                  {selectedLoopJob.job_id === 'job-tl-parser-042' && (
                    <button
                      type="button"
                      onClick={onSimulateSlotRelease}
                      className="px-2.5 py-1 rounded bg-amber-400 text-slate-950 font-bold text-xs hover:bg-amber-300"
                    >
                      Simulate Slot Release → Attempt Start
                    </button>
                  )}
                </div>
              )}

              {futureStageDrawerOpen && (
                <div className="p-3 rounded border border-sky-500/50 bg-slate-900/90 space-y-2.5 text-xs">
                  <div className="flex items-center justify-between font-mono">
                    <span className="font-bold text-sky-300">
                      {`Edit Future Unstarted Stage (PATCH /api/loops/${selectedLoopJob.job_id}/stages)`}
                    </span>
                    <button
                      type="button"
                      onClick={onToggleFutureStageDrawer}
                      className="text-slate-400 hover:text-white"
                    >
                      <X className="w-4 h-4" />
                    </button>
                  </div>
                  <p className="text-[11px] text-slate-300">
                    Frozen Revision: <code>{selectedLoopJob.config_revision}</code>. Stages already
                    ran or in-flight (`writer_a_write`, `writer_a_repair`, `writer_b_write`) are
                    locked.
                  </p>
                  <div className="grid grid-cols-2 gap-2 font-mono">
                    <div>
                      <label className="block text-[11px] text-slate-400 mb-1">
                        Unstarted Stage:
                      </label>
                      <select
                        value={futureStageTarget}
                        onChange={(e) => onChangeFutureStageTarget(e.target.value as StageKey)}
                        className="w-full bg-slate-950 border border-slate-700 rounded px-2 py-1"
                      >
                        <option value="writer_b_repair">writer_b_repair (pending)</option>
                        <option value="reviewer_b">reviewer_b (pending)</option>
                      </select>
                    </div>
                    <div>
                      <label className="block text-[11px] text-slate-400 mb-1">
                        Target Effort:
                      </label>
                      <select
                        value={futureStageEffort}
                        onChange={(e) =>
                          onChangeFutureStageEffort(e.target.value as 'low' | 'medium' | 'high')
                        }
                        className="w-full bg-slate-950 border border-slate-700 rounded px-2 py-1"
                      >
                        <option value="low">low</option>
                        <option value="medium">medium</option>
                        <option value="high">high</option>
                      </select>
                    </div>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <button
                      type="button"
                      disabled={dataMode === 'live'}
                      onClick={onPatchFutureStage}
                      className="px-2.5 py-1 rounded bg-sky-500 text-slate-950 font-mono font-semibold disabled:opacity-50"
                    >
                      {'Apply Stage Patch (PATCH /loops/{id}/stages)'}
                    </button>
                    <button
                      type="button"
                      onClick={onSimulate409FutureStage}
                      className="px-2.5 py-1 rounded border border-rose-600/60 bg-rose-950/50 text-rose-200 font-mono"
                    >
                      Simulate 409 Stale Revision
                    </button>
                  </div>
                </div>
              )}

              <div className="space-y-1">
                <div className="text-[11px] font-mono text-slate-400">
                  10 Configurable Stage Overview (`not-triggered` != `completed`; `fallback_write`
                  in runtime history only):
                </div>
                <div className="grid grid-cols-2 sm:grid-cols-5 gap-1.5 text-[10px] font-mono">
                  {(Object.keys(selectedLoopJob.stage_overview) as StageKey[]).map((sk) => {
                    const stState = selectedLoopJob.stage_overview[sk];
                    return (
                      <div
                        key={sk}
                        className={`px-2 py-1 rounded border truncate ${
                          stState === 'ran'
                            ? 'bg-emerald-950/40 border-emerald-700/50 text-emerald-300'
                            : stState === 'running'
                            ? 'bg-sky-950/60 border-sky-500 text-sky-200 font-bold'
                            : stState === 'pending'
                            ? 'bg-slate-900 border-slate-700 text-slate-300'
                            : 'bg-slate-950/50 border-slate-800/60 text-slate-500'
                        }`}
                        title={`${sk}: ${stState}`}
                      >
                        <div>{sk}</div>
                        <div className="uppercase text-[9px]">{stState}</div>
                      </div>
                    );
                  })}
                </div>
              </div>
            </div>

            {/* Center Attempt Timeline & View Switcher */}
            <div className={`rounded-md border p-3.5 space-y-3 ${bgPanel}`}>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="text-xs font-mono font-bold uppercase text-sky-400">
                  Observed Attempt &amp; Event Timeline (seq cursor: after=
                  {selectedLoopJob.events.length > 0
                    ? selectedLoopJob.events[selectedLoopJob.events.length - 1].seq
                    : 0}
                  )
                </span>
                <div className="inline-flex rounded border border-slate-700 overflow-hidden text-xs font-mono">
                  <button
                    type="button"
                    onClick={() => onChangeLoopViewMode('timeline_list')}
                    className={`px-2.5 py-1 ${
                      loopViewMode === 'timeline_list'
                        ? 'bg-sky-500 text-slate-950 font-bold'
                        : 'bg-slate-900 text-slate-300'
                    }`}
                  >
                    Canonical List
                  </button>
                  <button
                    type="button"
                    onClick={() => onChangeLoopViewMode('flow_diagram')}
                    className={`px-2.5 py-1 ${
                      loopViewMode === 'flow_diagram'
                        ? 'bg-sky-500 text-slate-950 font-bold'
                        : 'bg-slate-900 text-slate-300'
                    }`}
                  >
                    Conditional Ladder Diagram
                  </button>
                </div>
              </div>

              {loopViewMode === 'timeline_list' ? (
                <div className="space-y-2.5 max-h-[460px] overflow-y-auto pr-1">
                  {selectedLoopJob.attempts.map((att) => (
                    <div
                      key={att.attempt_id}
                      className={`p-3 rounded border text-xs font-mono space-y-1.5 ${
                        att.stage_key === 'fallback_write'
                          ? 'border-purple-500/60 bg-purple-950/25'
                          : bgSubtle
                      }`}
                    >
                      <div className="flex flex-wrap items-center justify-between gap-1">
                        <span className="font-bold text-sky-300">
                          {att.attempt_id} · {att.stage_key}{' '}
                          {att.stage_key === 'fallback_write' && (
                            <span className="px-1.5 py-0.5 rounded bg-purple-500/20 text-purple-300 text-[10px]">
                              RUNTIME FALLBACK EVENT ONLY
                            </span>
                          )}
                        </span>
                        <span className="text-[11px] px-1.5 py-0.5 rounded bg-black/40 border border-white/10">
                          {att.outcome}
                        </span>
                      </div>
                      <div className="text-[11px] text-slate-300">
                        Model: <code>{att.model_id}</code> ({att.endpoint})
                      </div>
                      <div className="flex flex-wrap gap-3 text-[11px] text-slate-400">
                        <span>role: {att.logical_role}</span>
                        <span>
                          effort: <strong className="text-slate-200">{att.effort}</strong>
                        </span>
                        <span>contract: {att.contract}</span>
                        <span>
                          prompt_tokens: {att.prompt_tokens ?? 'Unknown'} / completion_tokens:{' '}
                          {att.completion_tokens ?? 'Unknown'}
                        </span>
                        <span>
                          cost:{' '}
                          {att.billing_mode === 'subscription'
                            ? 'Subscription'
                            : att.cost_usd !== null
                            ? `$${att.cost_usd}`
                            : 'Unknown'}
                        </span>
                      </div>
                    </div>
                  ))}

                  <div className="pt-2 border-t border-slate-800 space-y-1.5">
                    <div className="text-[11px] font-mono text-slate-400">
                      {`Ordered Event Stream (GET /api/loops/${selectedLoopJob.job_id}/events?after=0):`}
                    </div>
                    {selectedLoopJob.events.map((ev) => (
                      <div
                        key={ev.seq}
                        className="p-2 rounded bg-slate-950/80 border border-slate-800/80 text-[11px] font-mono flex items-start gap-2"
                      >
                        <span className="px-1.5 py-0.5 rounded bg-slate-800 text-sky-300 shrink-0">
                          #{ev.seq}
                        </span>
                        <div className="min-w-0 flex-1">
                          <div className="flex flex-wrap items-center gap-2 text-slate-300">
                            <span className="font-bold text-amber-300">{ev.type}</span>
                            <span>{ev.stage}</span>
                            <span className="text-slate-500">{formatTimestamp(ev.ts)}</span>
                          </div>
                          <div className="text-slate-300 mt-0.5 break-words">{ev.detail}</div>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              ) : (
                <div className={`p-3 rounded border space-y-2 text-xs font-mono ${bgSubtle}`}>
                  <div className="text-[11px] text-slate-400">
                    Satvik Loop Conditional Ladder Visualization (Observed path for{' '}
                    {selectedLoopJob.job_id}):
                  </div>
                  {[
                    {
                      step: '1. writer_a_write (Fresh Write)',
                      status: selectedLoopJob.stage_overview.writer_a_write,
                    },
                    {
                      step: '2. writer_a_repair (Conditional Localized Repair)',
                      status: selectedLoopJob.stage_overview.writer_a_repair,
                    },
                    {
                      step: '3. writer_b_write (Conditional Fresh Write Fallback / Reviewer B fallback_write)',
                      status: selectedLoopJob.stage_overview.writer_b_write,
                    },
                    {
                      step: '4. writer_b_repair (Conditional Localized Repair)',
                      status: selectedLoopJob.stage_overview.writer_b_repair,
                    },
                    {
                      step: '5. specialist_repair (Owner-Gated Max 2 Calls)',
                      status: selectedLoopJob.stage_overview.specialist_repair,
                    },
                    {
                      step: '6. Dual Independent Reviews: reviewer_b + reviewer_a',
                      status: selectedLoopJob.stage_overview.reviewer_b,
                    },
                    {
                      step: '7. review_fix_repair (Evidence-Backed; Restores Green on Red Test)',
                      status: selectedLoopJob.stage_overview.review_fix_repair,
                    },
                    {
                      step: '8. Hash Equality Check & Automatic Merge Rebase',
                      status: selectedLoopJob.merge_state === 'merged' ? 'ran' : 'pending',
                    },
                  ].map((node, idx) => (
                    <div
                      key={idx}
                      className="flex items-center justify-between p-2 rounded bg-slate-900 border border-slate-800"
                    >
                      <span>{node.step}</span>
                      <span
                        className={`px-2 py-0.5 rounded text-[10px] uppercase ${
                          node.status === 'ran'
                            ? 'bg-emerald-500/20 text-emerald-300'
                            : node.status === 'running'
                            ? 'bg-sky-500/20 text-sky-300'
                            : 'bg-slate-800 text-slate-400'
                        }`}
                      >
                        {node.status}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        ) : null}

        {/* Right 6-Tab Inspector Panel (4 cols) */}
        {selectedLoopJob ? (
          <div className={`lg:col-span-4 rounded-md border p-3.5 space-y-3 ${bgPanel}`}>
            <div className="flex flex-wrap gap-1 border-b border-slate-800 pb-2 text-xs font-mono">
              {(
                [
                  { id: 'tests', label: 'Tests' },
                  { id: 'reviews', label: 'Reviews' },
                  { id: 'diff', label: 'Diff' },
                  { id: 'lineage', label: 'Lineage' },
                  { id: 'budget', label: 'Budget' },
                  { id: 'artifacts', label: 'Artifacts' },
                ] as const
              ).map((tab) => (
                <button
                  key={tab.id}
                  type="button"
                  onClick={() => onChangeInspectorTab(tab.id)}
                  className={`px-2 py-1 rounded ${
                    loopInspectorTab === tab.id
                      ? 'bg-sky-500 text-slate-950 font-bold'
                      : 'bg-slate-900 text-slate-300 hover:bg-slate-800'
                  }`}
                >
                  {tab.label}
                </button>
              ))}
            </div>

            {loopInspectorTab === 'tests' && (
              <div className="space-y-2 text-xs font-mono">
                <div className="font-bold text-sky-300">
                  Sandbox Pytest &amp; JUnit Locality Summary
                </div>
                {selectedLoopJob.attempts
                  .filter((a) => a.tests_summary)
                  .map((a) => (
                    <div key={a.attempt_id} className={`p-2.5 rounded border space-y-1.5 ${bgSubtle}`}>
                      <div className="flex justify-between">
                        <span>
                          {a.attempt_id} ({a.stage_key})
                        </span>
                        <span>
                          <strong className="text-emerald-400">
                            {a.tests_summary?.passed} pass
                          </strong>{' '}
                          /{' '}
                          <strong className="text-rose-400">
                            {a.tests_summary?.failed} fail
                          </strong>
                        </span>
                      </div>
                      {a.tests_summary?.failures.map((f, i) => (
                        <div
                          key={i}
                          className="p-2 rounded bg-rose-950/40 border border-rose-800/60 text-[11px]"
                        >
                          <div className="text-rose-300 font-semibold">{f.test_name}</div>
                          <div className="text-sky-300">
                            {f.file}:{f.line}
                          </div>
                          <div className="text-slate-300 mt-0.5">{f.assertion}</div>
                        </div>
                      ))}
                    </div>
                  ))}
              </div>
            )}

            {loopInspectorTab === 'reviews' && (
              <div className="space-y-2 text-xs font-mono">
                <div className="font-bold text-sky-300">
                  Independent Reviewer Reports (Contributor-Disjoint Gate)
                </div>
                {selectedLoopJob.reviews.length === 0 ? (
                  <div className={`p-3 rounded border ${bgSubtle} text-slate-400`}>
                    No completed reviewer reports yet for {selectedLoopJob.job_id}.
                  </div>
                ) : (
                  selectedLoopJob.reviews.map((r, idx) => (
                    <div key={idx} className={`p-2.5 rounded border space-y-1.5 ${bgSubtle}`}>
                      <div className="flex items-center justify-between">
                        <span className="font-bold text-slate-200">{r.reviewer_slot}</span>
                        <span
                          className={`px-1.5 py-0.5 rounded text-[10px] ${
                            r.verdict === 'READY'
                              ? 'bg-emerald-500/20 text-emerald-300'
                              : 'bg-amber-500/20 text-amber-300'
                          }`}
                        >
                          {r.verdict}
                        </span>
                      </div>
                      <div className="text-[11px] text-slate-400">
                        Model: <code>{r.model_id}</code> · Author-Disjoint:{' '}
                        <strong className="text-emerald-400">
                          {r.independent_of_authors ? 'VERIFIED' : 'VIOLATION'}
                        </strong>
                      </div>
                      {r.findings.map((fd, fi) => (
                        <div
                          key={fi}
                          className="p-2 rounded bg-amber-950/40 border border-amber-700/50 text-[11px]"
                        >
                          <div className="text-amber-300 font-semibold">
                            [{fd.ref}] {fd.file}:{fd.line} (evidence_ok: {String(fd.evidence_ok)})
                          </div>
                          <div className="text-slate-200 mt-0.5">{fd.message}</div>
                        </div>
                      ))}
                    </div>
                  ))
                )}
              </div>
            )}

            {loopInspectorTab === 'diff' && (
              <div className="space-y-2 text-xs font-mono">
                <div className="font-bold text-sky-300">
                  {`Candidate Unified Diff (GET /api/results/${selectedLoopJob.job_id}/diff)`}
                </div>
                {selectedLoopJob.diff_text ? (
                  <pre className="p-2.5 rounded bg-slate-950 border border-slate-800 text-[11px] overflow-x-auto text-emerald-300 whitespace-pre-wrap">
                    {selectedLoopJob.diff_text}
                  </pre>
                ) : (
                  <div className={`p-3 rounded border ${bgSubtle} text-slate-400`}>
                    Unavailable: No candidate diff recorded for {selectedLoopJob.job_id}.
                  </div>
                )}
              </div>
            )}

            {loopInspectorTab === 'lineage' && (
              <div className="space-y-2 text-xs font-mono">
                <div className="font-bold text-sky-300">
                  Contributor Lineage &amp; Canonical Identity Tracking
                </div>
                <p className="text-[11px] text-slate-400 font-sans">
                  Tracks actual contributor `canonical_model_id` across every
                  write/repair/fallback_write attempt so no model ever reviews code it contributed.
                </p>
                {selectedLoopJob.contributor_chain.map((c, idx) => (
                  <div key={idx} className={`p-2.5 rounded border ${bgSubtle}`}>
                    <div className="text-sky-300 font-semibold">
                      {c.attempt_id} · {c.stage} ({c.logical_role})
                    </div>
                    <div className="text-[11px] text-slate-300">
                      canonical_model_id: <code>{c.canonical_model_id}</code>
                    </div>
                    <div className="text-[11px] text-slate-400">status: {c.status}</div>
                  </div>
                ))}
              </div>
            )}

            {loopInspectorTab === 'budget' && (
              <div className="space-y-2 text-xs font-mono">
                <div className="font-bold text-sky-300">
                  Per-Attempt Calls, Tokens &amp; Cost Ledger
                </div>
                <div className={`p-2.5 rounded border ${bgSubtle} space-y-1`}>
                  <div>Free Requests Used: {selectedLoopJob.free_requests_used}</div>
                  <div>
                    Reconciled Job Cost:{' '}
                    {selectedLoopJob.total_cost_usd !== null
                      ? `$${selectedLoopJob.total_cost_usd}`
                      : 'Unknown (null)'}
                  </div>
                </div>
              </div>
            )}

            {loopInspectorTab === 'artifacts' && (
              <div className="space-y-2 text-xs font-mono">
                <div className="font-bold text-sky-300">Archived Artifacts &amp; Raw Log Policy</div>
                <div className={`p-2.5 rounded border ${bgSubtle} space-y-1.5`}>
                  <div className="text-[11px] text-amber-300">
                    Contract Gap C-11: Direct raw text-file preview endpoint is not defined in
                    docs/API_CONTRACT.md. Displaying structured artifact inventory only:
                  </div>
                  <ul className="list-disc list-inside text-slate-300">
                    {selectedLoopJob.archive.artifacts.map((art) => (
                      <li key={art}>{art}</li>
                    ))}
                  </ul>
                </div>
              </div>
            )}
          </div>
        ) : null}
      </div>
    </div>
  );
};
