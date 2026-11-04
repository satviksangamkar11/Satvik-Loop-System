import React from 'react';
import { Copy, Play } from 'lucide-react';
import type { DerivedJobState, JobRecord, NavPage, NormalizedOverview } from '../api/types';
import { fmtBool, fmtInt, fmtStr, fmtUsd } from '../api/format';

export interface DashboardPageProps {
  overview: NormalizedOverview | null;
  jobs: JobRecord[];
  stopActive: boolean | null;
  indexOk: boolean | null;
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

const ACTIVE_STATES: DerivedJobState[] = ['queued', 'running', 'waiting', 'waiting-unknown', 'stale-warning', 'orphan'];
const REVIEW_END_STATES = ['review-open', 'escalate', 'needs_review'];

export const DashboardPage: React.FC<DashboardPageProps> = ({
  overview,
  jobs,
  stopActive,
  indexOk,
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
  const activeJobs = jobs.filter((j) => j.derived_state !== null && ACTIVE_STATES.includes(j.derived_state));
  const reviewJobs = jobs.filter((j) => j.end_state !== null && REVIEW_END_STATES.includes(j.end_state));
  const terminalJobs = jobs.filter((j) => j.derived_state === 'finished' || j.derived_state === 'merged');

  const freeOver = overview?.free_today.used != null && overview.free_today.cap != null && overview.free_today.used >= overview.free_today.cap;
  const paceOver =
    overview?.pace.sent_last_60s != null && overview.pace.limit != null && overview.pace.sent_last_60s >= overview.pace.limit;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="text-lg md:text-xl font-bold tracking-tight">1. Operational Dashboard (/dashboard)</h1>
          <p className={`text-xs ${textMuted}`}>
            Counters from GET /overview, the job list from GET /jobs. Anything the backend did not send shows as Unknown.
            {indexOk === false && ' index_ok is false: the data came straight from the files.'}
          </p>
        </div>
        <button
          type="button"
          onClick={() => onNavigate('/jobs/new')}
          className="px-3 py-1.5 rounded bg-sky-500 hover:bg-sky-400 text-slate-950 font-semibold text-xs inline-flex items-center gap-1.5"
        >
          <Play className="w-3.5 h-3.5" />
          Queue / Launch Job
        </button>
      </div>

      {overview === null ? (
        <div className={`p-4 rounded-md border text-xs ${bgPanel}`}>Waiting for GET /api/overview. Nothing is shown until it answers.</div>
      ) : (
        <div className="grid grid-cols-2 sm:grid-cols-4 xl:grid-cols-8 gap-2.5">
          <div className={`p-3 rounded-md border ${bgPanel}`}>
            <div className={`text-[11px] font-mono ${textMuted}`}>RUNNING LOOPS</div>
            <div className="text-xl font-bold font-mono text-sky-400 mt-1">{fmtInt(overview.loops_running)}</div>
            <div className="text-[11px] text-slate-400">{fmtInt(overview.jobs_waiting)} waiting</div>
          </div>

          <div className={`p-3 rounded-md border ${bgPanel}`}>
            <div className={`text-[11px] font-mono ${textMuted}`}>QUEUE DEPTH</div>
            <div className="text-xl font-bold font-mono mt-1">{fmtInt(overview.queue_depth)}</div>
            <div className="text-[11px] text-slate-400">Queued job files</div>
          </div>

          <div className={`p-3 rounded-md border ${freeOver ? 'border-rose-600 bg-rose-950/30' : bgPanel}`}>
            <div className={`text-[11px] font-mono ${textMuted}`}>FREE REQUESTS USED</div>
            <div className="text-xl font-bold font-mono mt-1">
              {fmtInt(overview.free_today.used)} <span className="text-xs font-normal">/ {fmtInt(overview.free_today.cap)}</span>
            </div>
            <div className="text-[10px] font-mono text-slate-400 truncate" title={`cap_source: ${fmtStr(overview.free_today.cap_source)}`}>
              cap_source: {fmtStr(overview.free_today.cap_source)}
            </div>
          </div>

          <div className={`p-3 rounded-md border ${paceOver ? 'border-rose-600 bg-rose-950/30' : bgPanel}`}>
            <div className={`text-[11px] font-mono ${textMuted}`}>PACE (LAST 60s)</div>
            <div className="text-xl font-bold font-mono mt-1">
              {fmtInt(overview.pace.sent_last_60s)} <span className="text-xs font-normal">/ {fmtInt(overview.pace.limit, ' rpm')}</span>
            </div>
            <div className="text-[11px] text-slate-400">Account shared ledger</div>
          </div>

          <div className={`p-3 rounded-md border ${bgPanel}`}>
            <div className={`text-[11px] font-mono ${textMuted}`}>SANDBOX SLOTS</div>
            <div className="text-xl font-bold font-mono mt-1">
              {fmtInt(overview.sandbox_slots.used)} <span className="text-xs font-normal">/ {fmtInt(overview.sandbox_slots.total)}</span>
            </div>
            <div className="text-[11px] text-slate-400">In use / total</div>
          </div>

          <div className={`p-3 rounded-md border ${bgPanel}`}>
            <div className={`text-[11px] font-mono ${textMuted}`}>PENDING REVIEWS</div>
            <div className="text-xl font-bold font-mono text-amber-400 mt-1">{fmtInt(overview.pending_reviews)}</div>
            <div className="text-[11px] text-slate-400">Active errors: {fmtInt(overview.active_errors)}</div>
          </div>

          <div className={`p-3 rounded-md border ${bgPanel}`}>
            <div className={`text-[11px] font-mono ${textMuted}`}>REVIEW SPEND (TODAY)</div>
            <div className="text-lg font-bold font-mono mt-1">{fmtUsd(overview.review_usd_today.spent)}</div>
            <div className="text-[11px] font-mono text-slate-400">Cap: {fmtUsd(overview.review_usd_today.cap)}</div>
          </div>

          <div className={`p-3 rounded-md border ${stopActive === true ? 'border-rose-500 bg-rose-950/50' : bgPanel}`}>
            <div className={`text-[11px] font-mono ${textMuted}`}>GLOBAL STOP</div>
            <div
              className={`text-lg font-bold font-mono mt-1 ${
                stopActive === true ? 'text-rose-400' : stopActive === false ? 'text-emerald-400' : 'text-slate-400'
              }`}
            >
              {stopActive === null ? 'Unknown' : stopActive ? 'SOFT STOP ON' : 'NORMAL (OFF)'}
            </div>
            <div className="text-[11px] text-slate-400">In-flight calls finish</div>
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
        <div className={`xl:col-span-2 rounded-md border p-4 space-y-3 ${bgPanel}`}>
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-bold uppercase tracking-wider font-mono text-sky-400">Active &amp; Queued Loops</h2>
            <span className="text-xs font-mono text-slate-400">Polled every 5 s</span>
          </div>

          {activeJobs.length === 0 ? (
            <div className={`p-6 rounded border text-center space-y-2 ${bgSubtle}`}>
              <div className="text-sm font-semibold">No active or queued loops</div>
              <p className={`text-xs ${textMuted}`}>Queue a job from Run Jobs (/jobs/new) and start loop workers.</p>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left border-collapse text-xs">
                <thead>
                  <tr className="border-b border-slate-800 text-[11px] font-mono text-slate-400">
                    <th className="py-2 pr-3">Job ID</th>
                    <th className="py-2 px-2">State</th>
                    <th className="py-2 px-2">Active stage</th>
                    <th className="py-2 px-2">Active model</th>
                    <th className="py-2 px-2">Wait reason</th>
                    <th className="py-2 px-2">Heartbeat</th>
                    <th className="py-2 pl-2 text-right">Action</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800/70">
                  {activeJobs.map((j) => (
                    <tr key={j.job_id} className="hover:bg-slate-800/30">
                      <td className="py-2.5 pr-3 font-mono">
                        <div className="flex items-center gap-1.5">
                          <span className="font-semibold text-sky-300">{j.job_id}</span>
                          <button type="button" onClick={() => onCopyText(j.job_id)} className="p-0.5 rounded hover:bg-slate-700" title="Copy Job ID">
                            <Copy className="w-3 h-3" />
                          </button>
                          {copiedToken === j.job_id && <span className="text-[10px] text-emerald-400">Copied</span>}
                        </div>
                        <div className="text-[11px] text-slate-400">{fmtStr(j.name)}</div>
                      </td>
                      <td className="py-2.5 px-2 font-mono">
                        <span className="px-2 py-0.5 rounded text-[11px] font-semibold bg-slate-700 text-slate-200">{j.derived_state}</span>
                      </td>
                      <td className="py-2.5 px-2 font-mono">{fmtStr(j.active_stage)}</td>
                      <td className="py-2.5 px-2 font-mono">
                        <div className="truncate max-w-[190px]" title={fmtStr(j.active_model)}>{fmtStr(j.active_model)}</div>
                      </td>
                      <td className="py-2.5 px-2 font-mono">
                        {j.waiting?.why ? (
                          <span className="px-1.5 py-0.5 rounded bg-amber-950 text-amber-300 border border-amber-700 text-[11px]">{j.waiting.why}</span>
                        ) : (
                          <span className="text-slate-500">none</span>
                        )}
                      </td>
                      <td className="py-2.5 px-2 font-mono">
                        <div>{fmtInt(j.heartbeat_age_s, ' s ago')}</div>
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

        <div className={`rounded-md border p-4 space-y-4 ${bgPanel}`}>
          <div className="space-y-2">
            <h2 className="text-sm font-bold uppercase tracking-wider font-mono text-amber-400">Review Attention</h2>
            {reviewJobs.length === 0 && <p className={`text-xs ${textMuted}`}>No job is waiting on a review decision.</p>}
            {reviewJobs.map((j) => (
              <div key={j.job_id} className={`p-3 rounded border space-y-1.5 text-xs ${bgSubtle}`}>
                <div className="flex items-center justify-between font-mono">
                  <span className="font-bold text-sky-300">{j.job_id}</span>
                  <span className="px-1.5 py-0.5 rounded bg-amber-500/20 text-amber-300 border border-amber-500/40">{j.end_state}</span>
                </div>
                <div className="text-slate-300">
                  Tests: <strong>{j.tests_passed === undefined ? 'not loaded' : fmtBool(j.tests_passed, 'passed', 'failed')}</strong> | Reviewer:{' '}
                  <strong>{fmtStr(j.review_verdict)}</strong> | Merge: <strong>{fmtStr(j.merge_state)}</strong>
                </div>
                <button
                  type="button"
                  onClick={() => onOpenResultJob(j.job_id)}
                  className="w-full mt-1 py-1 rounded bg-slate-800 hover:bg-slate-700 text-sky-300 font-mono text-xs border border-slate-700"
                >
                  Inspect in Results
                </button>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className={`rounded-md border p-4 space-y-3 ${bgPanel}`}>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-sm font-bold uppercase tracking-wider font-mono text-sky-400">Recent Terminal Jobs</h2>
          <button type="button" onClick={() => onNavigate('/results')} className="text-xs font-mono text-sky-400 hover:underline">
            Full results →
          </button>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse text-xs">
            <thead>
              <tr className="border-b border-slate-800 text-[11px] font-mono text-slate-400">
                <th className="py-2 pr-3">Job ID</th>
                <th className="py-2 px-2">end_state</th>
                <th className="py-2 px-2">Tests</th>
                <th className="py-2 px-2">Reviewer verdict</th>
                <th className="py-2 px-2">merge_state</th>
                <th className="py-2 px-2">Finished</th>
                <th className="py-2 pl-2 text-right">Inspect</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/70 font-mono">
              {terminalJobs.map((j) => (
                <tr key={j.job_id} className="hover:bg-slate-800/30">
                  <td className="py-2.5 pr-3 font-semibold text-sky-300">{j.job_id}</td>
                  <td className="py-2.5 px-2">{fmtStr(j.end_state)}</td>
                  <td className="py-2.5 px-2">{j.tests_passed === undefined ? 'not loaded' : fmtBool(j.tests_passed, 'passed', 'failed')}</td>
                  <td className="py-2.5 px-2">{fmtStr(j.review_verdict)}</td>
                  <td className="py-2.5 px-2">{fmtStr(j.merge_state)}</td>
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
