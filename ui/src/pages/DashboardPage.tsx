import React from 'react';
import { Copy, Play } from 'lucide-react';
import type { JobRecord, NavPage, NormalizedOverview } from '../api/types';

export interface DashboardPageProps {
  overview: NormalizedOverview;
  jobs: JobRecord[];
  stopActive: boolean;
  copiedToken: string | null;
  bgPanel: string;
  bgSubtle: string;
  textMuted: string;
  formatTimestamp: (iso: string | null | undefined) => string;
  onCopyText: (text: string) => void;
  onNavigate: (page: NavPage) => void;
  onOpenLoopJob: (jobId: string) => void;
  onOpenResultJob: (jobId: string) => void;
}

export const DashboardPage: React.FC<DashboardPageProps> = ({
  overview,
  jobs,
  stopActive,
  copiedToken,
  bgPanel,
  bgSubtle,
  textMuted,
  formatTimestamp,
  onCopyText,
  onNavigate,
  onOpenLoopJob,
  onOpenResultJob,
}) => {
  const activeJobs = jobs.filter(
    (j) =>
      j.derived_state === 'running' ||
      j.derived_state === 'waiting' ||
      j.derived_state === 'queued'
  );

  const reviewAttentionJobs = jobs.filter(
    (j) => j.end_state === 'review-open' || j.end_state === 'escalate'
  );

  const terminalJobs = jobs.filter(
    (j) => j.derived_state === 'finished' || j.derived_state === 'merged'
  );

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="text-lg md:text-xl font-bold tracking-tight">
            1. Operational Dashboard (/dashboard)
          </h1>
          <p className={`text-xs ${textMuted}`}>
            Real-time loop counters (`loops_running`, `free_today`, `pace_60s`, `sandbox_slots`),
            soft STOP boundary, and split `end_state` vs `test_verdict` vs `reviewer_verdict` vs
            `merge_state`.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => onNavigate('/jobs/new')}
            className="px-3 py-1.5 rounded bg-sky-500 hover:bg-sky-400 text-slate-950 font-semibold text-xs inline-flex items-center gap-1.5"
          >
            <Play className="w-3.5 h-3.5" />
            Queue / Launch Job
          </button>
        </div>
      </div>

      {/* Top KPI Grid (8 Cards mapped directly from NormalizedOverview) */}
      <div className="grid grid-cols-2 sm:grid-cols-4 xl:grid-cols-8 gap-2.5">
        <div className={`p-3 rounded-md border ${bgPanel}`}>
          <div className={`text-[11px] font-mono ${textMuted}`}>RUNNING LOOPS</div>
          <div className="text-xl font-bold font-mono text-sky-400 mt-1">
            {overview.loops_running}
          </div>
          <div className="text-[11px] text-slate-400">
            {overview.loops_waiting} waiting on gate
          </div>
        </div>

        <div className={`p-3 rounded-md border ${bgPanel}`}>
          <div className={`text-[11px] font-mono ${textMuted}`}>QUEUED JOBS</div>
          <div className="text-xl font-bold font-mono mt-1">{overview.jobs_queued}</div>
          <div className="text-[11px] text-slate-400">Frozen snapshots</div>
        </div>

        <div
          className={`p-3 rounded-md border ${
            overview.free_today.remaining === 0
              ? 'border-rose-600 bg-rose-950/30'
              : bgPanel
          }`}
        >
          <div className={`text-[11px] font-mono ${textMuted}`}>FREE QUOTA REM</div>
          <div className="text-xl font-bold font-mono mt-1">
            {overview.free_today.remaining !== null ? overview.free_today.remaining : 'Unknown'}{' '}
            <span className="text-xs font-normal">/ {overview.free_today.cap !== null ? overview.free_today.cap : 'Unknown'}</span>
          </div>
          <div
            className="text-[10px] font-mono text-slate-400 truncate"
            title={`Source: ${overview.free_today.source}`}
          >
            src: {overview.free_today.source}
          </div>
        </div>

        <div
          className={`p-3 rounded-md border ${
            overview.pace_60s.sent >= overview.pace_60s.limit
              ? 'border-rose-600 bg-rose-950/30'
              : bgPanel
          }`}
        >
          <div className={`text-[11px] font-mono ${textMuted}`}>PACE (LAST 60s)</div>
          <div className="text-xl font-bold font-mono mt-1">
            {overview.pace_60s.sent}{' '}
            <span className="text-xs font-normal">/ {overview.pace_60s.limit} rpm</span>
          </div>
          <div className="text-[11px] text-slate-400">Account shared ledger</div>
        </div>

        <div className={`p-3 rounded-md border ${bgPanel}`}>
          <div className={`text-[11px] font-mono ${textMuted}`}>SANDBOX SLOTS</div>
          <div className="text-xl font-bold font-mono mt-1">
            {overview.sandbox_slots.used}{' '}
            <span className="text-xs font-normal">/ {overview.sandbox_slots.cap !== null ? overview.sandbox_slots.cap : 'Unknown'}</span>
          </div>
          <div className="text-[11px] text-slate-400">Docker rootless</div>
        </div>

        <div className={`p-3 rounded-md border ${bgPanel}`}>
          <div className={`text-[11px] font-mono ${textMuted}`}>REVIEW ATTENTION</div>
          <div className="text-xl font-bold font-mono text-amber-400 mt-1">
            {overview.reviews_pending}
          </div>
          <div className="text-[11px] text-slate-400">FIX FIRST / restored-green</div>
        </div>

        <div className={`p-3 rounded-md border ${bgPanel}`}>
          <div className={`text-[11px] font-mono ${textMuted}`}>REVIEW SPEND</div>
          <div className="text-lg font-bold font-mono mt-1">
            {overview.review_spend.used_usd !== null ? `$${overview.review_spend.used_usd}` : 'Unknown'}
          </div>
          <div className="text-[11px] font-mono text-slate-400">
            Cap: {overview.review_spend.cap_usd !== null ? `$${overview.review_spend.cap_usd}` : 'Unknown'}
          </div>
        </div>

        <div
          className={`p-3 rounded-md border ${
            stopActive ? 'border-rose-500 bg-rose-950/50' : bgPanel
          }`}
        >
          <div className={`text-[11px] font-mono ${textMuted}`}>GLOBAL STOP</div>
          <div
            className={`text-lg font-bold font-mono mt-1 ${
              stopActive ? 'text-rose-400' : 'text-emerald-400'
            }`}
          >
            {stopActive ? 'SOFT STOP ON' : 'NORMAL (OFF)'}
          </div>
          <div className="text-[11px] text-slate-400">In-flight calls finish</div>
        </div>
      </div>

      {/* Primary Region: Active Loops Table (2/3) + Attention & Quota Panel (1/3) */}
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
        <div className={`xl:col-span-2 rounded-md border p-4 space-y-3 ${bgPanel}`}>
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-bold uppercase tracking-wider font-mono text-sky-400">
              Active &amp; Queued Loops (No Fabricated Progress %)
            </h2>
            <span className="text-xs font-mono text-slate-400">Poll interval: 5s</span>
          </div>

          {activeJobs.length === 0 ? (
            <div className={`p-6 rounded border text-center space-y-2 ${bgSubtle}`}>
              <div className="text-sm font-semibold">No Active or Queued Loops</div>
              <p className={`text-xs ${textMuted}`}>
                Queue a job from Run Jobs (/jobs/new) and start loop workers to observe live
                execution.
              </p>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left border-collapse text-xs">
                <thead>
                  <tr className="border-b border-slate-800 text-[11px] font-mono text-slate-400">
                    <th className="py-2 pr-3">Job ID / Spec</th>
                    <th className="py-2 px-2">State</th>
                    <th className="py-2 px-2">Stage &amp; Role</th>
                    <th className="py-2 px-2">Actual Model &amp; Effort</th>
                    <th className="py-2 px-2">Wait Reason</th>
                    <th className="py-2 px-2">Heartbeat / PID</th>
                    <th className="py-2 pl-2 text-right">Action</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800/70">
                  {activeJobs.map((j) => (
                    <tr key={j.job_id} className="hover:bg-slate-800/30">
                      <td className="py-2.5 pr-3 font-mono">
                        <div className="flex items-center gap-1.5">
                          <span className="font-semibold text-sky-300">{j.job_id}</span>
                          <button
                            type="button"
                            onClick={() => onCopyText(j.job_id)}
                            className="p-0.5 rounded hover:bg-slate-700"
                            title="Copy Job ID"
                          >
                            <Copy className="w-3 h-3" />
                          </button>
                          {copiedToken === j.job_id && (
                            <span className="text-[10px] text-emerald-400">Copied</span>
                          )}
                        </div>
                        <div className="text-[11px] text-slate-400">{j.job_path}</div>
                      </td>
                      <td className="py-2.5 px-2 font-mono">
                        <span
                          className={`px-2 py-0.5 rounded text-[11px] font-semibold ${
                            j.derived_state === 'running'
                              ? 'bg-emerald-500/15 text-emerald-300 border border-emerald-500/30'
                              : j.derived_state === 'waiting'
                              ? 'bg-amber-500/15 text-amber-300 border border-amber-500/30'
                              : 'bg-slate-700 text-slate-200'
                          }`}
                        >
                          {j.derived_state}
                        </span>
                      </td>
                      <td className="py-2.5 px-2 font-mono">
                        <div>{j.active_stage || 'queued'}</div>
                        <div className="text-[11px] text-slate-400">
                          role: {j.logical_role || 'pending'}
                        </div>
                      </td>
                      <td className="py-2.5 px-2 font-mono">
                        <div className="truncate max-w-[190px]" title={j.model_id}>
                          {j.model_id}
                        </div>
                        <div className="text-[11px] text-sky-400">
                          {j.endpoint} · effort={j.effort}
                        </div>
                      </td>
                      <td className="py-2.5 px-2 font-mono">
                        {j.waiting ? (
                          <span className="px-1.5 py-0.5 rounded bg-amber-950 text-amber-300 border border-amber-700 text-[11px]">
                            {j.waiting.why}
                          </span>
                        ) : (
                          <span className="text-slate-500">none</span>
                        )}
                      </td>
                      <td className="py-2.5 px-2 font-mono">
                        <div>{j.heartbeat_age_sec}s ago</div>
                        <div className="text-[11px] text-slate-400">
                          PID: {j.lease_pid ?? 'n/a'}
                        </div>
                      </td>
                      <td className="py-2.5 pl-2 text-right">
                        <button
                          type="button"
                          onClick={() => onOpenLoopJob(j.job_id)}
                          className="px-2.5 py-1 rounded bg-sky-500/20 hover:bg-sky-500/30 text-sky-300 border border-sky-500/40 font-mono text-xs"
                        >
                          Open Loop
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* Right 1/3: Pending Reviews & Recent Errors */}
        <div className={`rounded-md border p-4 space-y-4 ${bgPanel}`}>
          <div className="space-y-2">
            <h2 className="text-sm font-bold uppercase tracking-wider font-mono text-amber-400">
              Pending Review Attention
            </h2>
            {reviewAttentionJobs.map((j) => (
              <div key={j.job_id} className={`p-3 rounded border space-y-1.5 text-xs ${bgSubtle}`}>
                <div className="flex items-center justify-between font-mono">
                  <span className="font-bold text-sky-300">{j.job_id}</span>
                  <span className="px-1.5 py-0.5 rounded bg-amber-500/20 text-amber-300 border border-amber-500/40">
                    {j.end_state}
                  </span>
                </div>
                <div className="text-slate-300">
                  Test Verdict: <strong className="text-emerald-400">{j.test_verdict}</strong> |
                  Reviewer: <strong className="text-amber-300">{j.reviewer_verdict}</strong> |
                  Merge: <strong className="text-rose-300">{j.merge_state}</strong>
                </div>
                {j.restored_green && (
                  <div className="text-[11px] text-emerald-300 font-mono">
                    ✓ Restored-Green: Last test-passing candidate retained
                  </div>
                )}
                <button
                  type="button"
                  onClick={() => onOpenResultJob(j.job_id)}
                  className="w-full mt-1 py-1 rounded bg-slate-800 hover:bg-slate-700 text-sky-300 font-mono text-xs border border-slate-700"
                >
                  Inspect Findings &amp; Evidence in Results →
                </button>
              </div>
            ))}
          </div>

          <div className="space-y-2 pt-2 border-t border-slate-800">
            <h3 className="text-xs font-bold uppercase tracking-wider font-mono text-rose-400">
              Recent Telemetry Errors &amp; Backoffs
            </h3>
            <div className="space-y-2 text-xs">
              {overview.recent_errors.map((err) => (
                <div key={err.id} className={`p-2.5 rounded border ${bgSubtle}`}>
                  <div className="font-mono text-[11px] text-amber-300">
                    [{formatTimestamp(err.ts)}] {err.job_id} · {err.stage} · {err.code}
                  </div>
                  <div className="text-slate-300 mt-0.5">{err.message}</div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>

      {/* Lower Region: Terminal Outcomes with 4-Way Verdict Split */}
      <div className={`rounded-md border p-4 space-y-3 ${bgPanel}`}>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-sm font-bold uppercase tracking-wider font-mono text-sky-400">
            Recent Terminal Jobs — 4 Independent Verdicts (`end_state` != `test_verdict` !=
            `reviewer_verdict` != `merge_state`)
          </h2>
          <button
            type="button"
            onClick={() => onNavigate('/results')}
            className="text-xs font-mono text-sky-400 hover:underline"
          >
            View Full Results Ledger →
          </button>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse text-xs">
            <thead>
              <tr className="border-b border-slate-800 text-[11px] font-mono text-slate-400">
                <th className="py-2 pr-3">Job ID &amp; Path</th>
                <th className="py-2 px-2">1. Job `end_state`</th>
                <th className="py-2 px-2">2. Sandbox Tests</th>
                <th className="py-2 px-2">3. Independent Reviewers</th>
                <th className="py-2 px-2">4. `merge_state`</th>
                <th className="py-2 px-2">Finished (`finished`)</th>
                <th className="py-2 pl-2 text-right">Inspect</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/70 font-mono">
              {terminalJobs.map((j) => (
                <tr key={j.job_id} className="hover:bg-slate-800/30">
                  <td className="py-2.5 pr-3">
                    <div className="font-semibold text-sky-300">{j.job_id}</div>
                    <div className="text-[11px] text-slate-400">{j.job_path}</div>
                  </td>
                  <td className="py-2.5 px-2">
                    <span
                      className={`px-2 py-0.5 rounded text-[11px] ${
                        j.end_state === 'passed'
                          ? 'bg-emerald-500/15 text-emerald-300'
                          : j.end_state === 'review-open'
                          ? 'bg-amber-500/15 text-amber-300'
                          : 'bg-rose-500/15 text-rose-300'
                      }`}
                    >
                      {j.end_state}
                    </span>
                  </td>
                  <td className="py-2.5 px-2 text-emerald-300">{j.test_verdict}</td>
                  <td className="py-2.5 px-2">
                    <span
                      className={
                        j.reviewer_verdict.includes('READY')
                          ? 'text-emerald-300'
                          : 'text-amber-300'
                      }
                    >
                      {j.reviewer_verdict}
                    </span>
                  </td>
                  <td className="py-2.5 px-2">
                    <span
                      className={`px-2 py-0.5 rounded text-[11px] ${
                        j.merge_state === 'merged'
                          ? 'bg-emerald-500/20 text-emerald-300'
                          : j.merge_state === 'pending'
                          ? 'bg-sky-500/20 text-sky-300'
                          : j.merge_state === 'merge-conflict'
                          ? 'bg-rose-500/20 text-rose-300'
                          : 'bg-slate-800 text-slate-300'
                      }`}
                    >
                      {j.merge_state === 'none' ? 'Not merged (none)' : j.merge_state}
                    </span>
                  </td>
                  <td className="py-2.5 px-2 text-slate-400">{formatTimestamp(j.finished)}</td>
                  <td className="py-2.5 pl-2 text-right">
                    <button
                      type="button"
                      onClick={() => onOpenResultJob(j.job_id)}
                      className="px-2.5 py-1 rounded border border-slate-700 hover:border-sky-400 text-sky-300"
                    >
                      Details
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
};
