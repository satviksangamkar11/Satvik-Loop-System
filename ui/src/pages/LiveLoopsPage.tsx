import React, { useMemo } from 'react';
import { X } from 'lucide-react';
import type { AttemptStatus, DataMode, Effort, JobRecord, LoopEvent, StageKey } from '../api/types';
import { fmtBool, fmtInt, fmtStr, fmtUsd } from '../api/format';

export interface LiveLoopsPageProps {
  dataMode: DataMode;
  jobs: JobRecord[];
  selectedLoopJob: JobRecord | null;
  events: LoopEvent[];
  eventsIndexOk: boolean | null;
  loopFilter: string;
  loopViewMode: 'timeline_list' | 'flow_diagram';
  loopInspectorTab: 'tests' | 'reviews' | 'diff' | 'lineage' | 'budget' | 'artifacts';
  futureStageDrawerOpen: boolean;
  futureStageTarget: StageKey;
  futureStageEffort: Effort;
  indexOk: boolean | null;
  bgPanel: string;
  bgSubtle: string;
  textMuted: string;
  formatTimestamp: (iso: string | null | undefined) => string;
  onOpenStopModal: () => void;
  onChangeLoopFilter: (f: string) => void;
  onSelectLoopJob: (jobId: string) => void;
  onToggleFutureStageDrawer: () => void;
  onChangeFutureStageTarget: (st: StageKey) => void;
  onChangeFutureStageEffort: (eff: Effort) => void;
  onPatchFutureStage: () => void;
  onChangeLoopViewMode: (m: 'timeline_list' | 'flow_diagram') => void;
  onChangeInspectorTab: (t: 'tests' | 'reviews' | 'diff' | 'lineage' | 'budget' | 'artifacts') => void;
}

/** One attempt, rebuilt only from recorded attempt-start / attempt-end events. */
interface ObservedAttempt {
  attempt_id: string;
  stage: string | null;
  model: string | null;
  via: string | null;
  effort: string | null;
  status: AttemptStatus | null;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  reasoning_tokens: number | null;
  seconds: number | null;
  error: string | null;
}

function observedAttempts(events: LoopEvent[]): ObservedAttempt[] {
  const byId = new Map<string, ObservedAttempt>();
  for (const ev of events) {
    if (!ev.attempt_id) continue;
    const existing = byId.get(ev.attempt_id);
    if (ev.type === 'attempt-start' || !existing) {
      byId.set(ev.attempt_id, {
        attempt_id: ev.attempt_id,
        stage: ev.stage,
        model: ev.model,
        via: ev.via,
        effort: ev.effort,
        status: ev.status,
        prompt_tokens: ev.prompt_tokens,
        completion_tokens: ev.completion_tokens,
        reasoning_tokens: ev.reasoning_tokens,
        seconds: ev.seconds,
        error: ev.error,
      });
    }
    if (ev.type === 'attempt-end' && existing) {
      byId.set(ev.attempt_id, {
        ...existing,
        status: ev.status,
        prompt_tokens: ev.prompt_tokens,
        completion_tokens: ev.completion_tokens,
        reasoning_tokens: ev.reasoning_tokens,
        seconds: ev.seconds,
        error: ev.error,
      });
    }
  }
  return [...byId.values()];
}

/** Text for one event, from the fields that event type carries. Missing fields read as Unknown. */
function describeEvent(ev: LoopEvent): string {
  switch (ev.type) {
    case 'job-start':
      return 'Job started.';
    case 'attempt-start':
      return `${fmtStr(ev.stage)} started on ${fmtStr(ev.model)} (${fmtStr(ev.via)}, effort ${fmtStr(ev.effort)}).`;
    case 'attempt-end':
      return `${fmtStr(ev.stage)} ${fmtStr(ev.status)}: prompt ${fmtInt(ev.prompt_tokens)}, completion ${fmtInt(
        ev.completion_tokens
      )}, reasoning ${fmtInt(ev.reasoning_tokens)} tokens, ${fmtInt(ev.seconds, ' s')}.${ev.error ? ` Error: ${ev.error}` : ''}`;
    case 'wait-start':
      return `Waiting (${fmtStr(ev.why)}) for ${fmtStr(ev.model)}.`;
    case 'wait-end':
      return 'Wait ended.';
    case 'tests':
      return `Tests ${ev.green === null ? 'Unknown' : ev.green ? 'green' : 'red'}.`;
    case 'review':
      return `Review by ${fmtStr(ev.reviewer)}: ${fmtStr(ev.verdict)} (evidence_ok: ${fmtBool(ev.evidence_ok)}).`;
    case 'restore-green':
      return 'Restored the last green candidate.';
    case 'finish':
      return `Finished: ${fmtStr(ev.end_state)}${ev.reason ? `. ${ev.reason}` : ''}`;
    case 'merge':
      return `Merge ${fmtStr(ev.merge_state)}, head ${fmtStr(ev.head)}, attempts ${fmtInt(ev.attempts)}.`;
    default:
      return `Event type ${ev.type}.`;
  }
}


export const LiveLoopsPage: React.FC<LiveLoopsPageProps> = ({
  jobs,
  selectedLoopJob,
  events,
  eventsIndexOk,
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
  onOpenStopModal,
  onChangeLoopFilter,
  onSelectLoopJob,
  onToggleFutureStageDrawer,
  onChangeFutureStageTarget,
  onChangeFutureStageEffort,
  onPatchFutureStage,
  onChangeLoopViewMode,
  onChangeInspectorTab,
}) => {
  const attempts = useMemo(() => observedAttempts(events), [events]);
  const lastSeq = events.length > 0 ? events[events.length - 1].seq : 0;
  const stageRows = selectedLoopJob?.stages ?? [];

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="text-lg md:text-xl font-bold tracking-tight">3. Live Loops (/loops)</h1>
          <p className={`text-xs ${textMuted}`}>
            Recorded events in order (GET /loops/{'{id}'}/events?after=seq), the job detail, and the future-stage editor.
            {indexOk === false && ' index_ok is false: the data came straight from the files.'}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-xs font-mono">
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
        <div className={`lg:col-span-3 rounded-md border p-3 space-y-3 ${bgPanel}`}>
          <div className="flex items-center justify-between">
            <span className="text-xs font-mono font-bold uppercase text-sky-400">Loops ({jobs.length})</span>
            <select
              aria-label="Filter Loops by State"
              value={loopFilter}
              onChange={(e) => onChangeLoopFilter(e.target.value)}
              className="bg-slate-900 border border-slate-700 rounded px-2 py-0.5 text-xs font-mono"
            >
              <option value="all">All states</option>
              <option value="running">running</option>
              <option value="waiting">waiting</option>
              <option value="queued">queued</option>
              <option value="finished">finished</option>
              <option value="merged">merged</option>
            </select>
          </div>

          <div className="space-y-2 max-h-[600px] overflow-y-auto pr-1">
            {jobs.length === 0 && <p className={`text-xs ${textMuted}`}>No jobs from GET /api/jobs.</p>}
            {jobs
              .filter((j) => loopFilter === 'all' || j.derived_state === loopFilter)
              .map((j) => {
                const isSel = selectedLoopJob?.job_id === j.job_id;
                return (
                  <button
                    key={j.job_id}
                    type="button"
                    data-testid={`loop-row-${j.job_id}`}
                    onClick={() => onSelectLoopJob(j.job_id)}
                    className={`w-full text-left p-2.5 rounded border transition text-xs font-mono space-y-1 ${
                      isSel ? 'bg-sky-500/15 border-sky-400' : 'bg-slate-900/50 border-slate-800 hover:border-slate-700'
                    }`}
                  >
                    <div className="flex items-center justify-between gap-1">
                      <span className="font-bold text-sky-300 truncate">{j.job_id}</span>
                      <span className="px-1.5 py-0.5 rounded text-[10px] bg-slate-800 text-slate-300">{fmtStr(j.derived_state)}</span>
                    </div>
                    <div className="text-[10px] text-slate-300 flex justify-between">
                      <span>stage: {fmtStr(j.active_stage ?? j.end_state)}</span>
                      <span>model: {fmtStr(j.active_model)}</span>
                    </div>
                  </button>
                );
              })}
          </div>
        </div>

        {selectedLoopJob ? (
          <div className="lg:col-span-5 space-y-3">
            <div className={`rounded-md border p-3.5 space-y-2.5 ${bgPanel}`}>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="font-mono">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-bold text-sky-300">{selectedLoopJob.job_id}</span>
                    <span className="px-2 py-0.5 rounded text-[11px] bg-sky-500/15 text-sky-300 border border-sky-500/30">
                      {fmtStr(selectedLoopJob.derived_state)}
                    </span>
                  </div>
                  <div className="text-[11px] text-slate-400 mt-0.5">
                    spec: {fmtStr(selectedLoopJob.spec)} · snapshot revision:{' '}
                    <code>{fmtInt(selectedLoopJob.stage_snapshot?.revision ?? null)}</code>
                  </div>
                </div>

                <button
                  type="button"
                  onClick={onToggleFutureStageDrawer}
                  className="px-2.5 py-1 rounded border border-sky-500/40 bg-sky-500/15 text-sky-300 text-xs font-mono"
                >
                  Edit Future Stage
                </button>
              </div>

              <div className={`p-2.5 rounded border text-[11px] font-mono grid grid-cols-2 sm:grid-cols-3 gap-2 ${bgSubtle}`}>
                <div>
                  <span className="text-slate-400">Active stage:</span> <strong>{fmtStr(selectedLoopJob.active_stage)}</strong>
                </div>
                <div>
                  <span className="text-slate-400">Active model:</span> <strong>{fmtStr(selectedLoopJob.active_model)}</strong>
                </div>
                <div>
                  <span className="text-slate-400">Heartbeat:</span> <strong>{fmtInt(selectedLoopJob.heartbeat_age_s, ' s')}</strong>
                </div>
                <div>
                  <span className="text-slate-400">Lease PID:</span>{' '}
                  <strong>{fmtInt(selectedLoopJob.lease?.pid ?? null)}</strong>
                </div>
                <div>
                  <span className="text-slate-400">Pid alive:</span>{' '}
                  <strong>{fmtBool(selectedLoopJob.lease?.pid_alive ?? null)}</strong>
                </div>
                <div>
                  <span className="text-slate-400">Events index_ok:</span> <strong>{fmtBool(eventsIndexOk)}</strong>
                </div>
                <div className="col-span-2 sm:col-span-3 text-slate-400 border-t border-slate-800 pt-1.5">
                  Soft STOP only. There is no cancel, resume or manual merge. A stage that has started cannot change.
                </div>
              </div>

              {selectedLoopJob.waiting && (
                <div className="p-2.5 rounded border border-amber-600/60 bg-amber-950/40 text-amber-100 text-xs font-mono">
                  <strong>WAITING ({fmtStr(selectedLoopJob.waiting.why)}):</strong> gate on{' '}
                  <code>{fmtStr(selectedLoopJob.waiting.model)}</code> since {formatTimestamp(selectedLoopJob.waiting.since)}
                </div>
              )}

              {futureStageDrawerOpen && (
                <div className="p-3 rounded border border-sky-500/50 bg-slate-900/90 space-y-2.5 text-xs">
                  <div className="flex items-center justify-between font-mono">
                    <span className="font-bold text-sky-300">PATCH /api/loops/{selectedLoopJob.job_id}/stages</span>
                    <button type="button" onClick={onToggleFutureStageDrawer} className="text-slate-400 hover:text-white">
                      <X className="w-4 h-4" />
                    </button>
                  </div>
                  <p className="text-[11px] text-slate-300">
                    Only stages that have not started can change. Snapshot revision:{' '}
                    <code>{fmtInt(selectedLoopJob.stage_snapshot?.revision ?? null)}</code>.
                  </p>
                  <div className="grid grid-cols-2 gap-2 font-mono">
                    <div>
                      <label className="block text-[11px] text-slate-400 mb-1">Stage:</label>
                      <select
                        value={futureStageTarget}
                        onChange={(e) => onChangeFutureStageTarget(e.target.value as StageKey)}
                        className="w-full bg-slate-950 border border-slate-700 rounded px-2 py-1"
                      >
                        <option value="writer_b_repair">writer_b_repair</option>
                        <option value="reviewer_b">reviewer_b</option>
                        <option value="reviewer_a">reviewer_a</option>
                        <option value="reviewer_reask">reviewer_reask</option>
                        <option value="review_fix_repair">review_fix_repair</option>
                      </select>
                    </div>
                    <div>
                      <label className="block text-[11px] text-slate-400 mb-1">Effort:</label>
                      <select
                        value={futureStageEffort}
                        onChange={(e) => onChangeFutureStageEffort(e.target.value as Effort)}
                        className="w-full bg-slate-950 border border-slate-700 rounded px-2 py-1"
                      >
                        <option value="low">low</option>
                        <option value="medium">medium</option>
                        <option value="high">high</option>
                      </select>
                    </div>
                  </div>
                  <button
                    type="button"
                    data-testid="patch-stage-btn"
                    onClick={onPatchFutureStage}
                    className="px-2.5 py-1 rounded bg-sky-500 text-slate-950 font-mono font-semibold"
                  >
                    Apply Stage Patch
                  </button>
                </div>
              )}

              <div className="space-y-1">
                <div className="text-[11px] font-mono text-slate-400">
                  Stage status from the backend (not-triggered is not a failure):
                </div>
                {stageRows.length === 0 ? (
                  <div className="text-[11px] font-mono text-slate-400">Stage list not loaded yet.</div>
                ) : (
                  <div className="grid grid-cols-2 sm:grid-cols-5 gap-1.5 text-[10px] font-mono">
                    {stageRows.map((row) => (
                      <div
                        key={row.stage}
                        className={`px-2 py-1 rounded border truncate ${
                          row.status === 'ran'
                            ? 'bg-emerald-950/40 border-emerald-700/50 text-emerald-300'
                            : row.status === 'running'
                            ? 'bg-sky-950/60 border-sky-500 text-sky-200 font-bold'
                            : row.status === 'pending'
                            ? 'bg-slate-900 border-slate-700 text-slate-300'
                            : 'bg-slate-950/50 border-slate-800/60 text-slate-500'
                        }`}
                        title={`${row.stage}: ${fmtStr(row.status)} (attempts ${fmtInt(row.attempts)})`}
                      >
                        <div>{row.stage}</div>
                        <div className="uppercase text-[9px]">{fmtStr(row.status)}</div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>

            <div className={`rounded-md border p-3.5 space-y-3 ${bgPanel}`}>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="text-xs font-mono font-bold uppercase text-sky-400">
                  Event timeline (after={lastSeq})
                </span>
                <div className="inline-flex rounded border border-slate-700 overflow-hidden text-xs font-mono">
                  <button
                    type="button"
                    onClick={() => onChangeLoopViewMode('timeline_list')}
                    className={`px-2.5 py-1 ${loopViewMode === 'timeline_list' ? 'bg-sky-500 text-slate-950 font-bold' : 'bg-slate-900 text-slate-300'}`}
                  >
                    Event list
                  </button>
                  <button
                    type="button"
                    onClick={() => onChangeLoopViewMode('flow_diagram')}
                    className={`px-2.5 py-1 ${loopViewMode === 'flow_diagram' ? 'bg-sky-500 text-slate-950 font-bold' : 'bg-slate-900 text-slate-300'}`}
                  >
                    Stage status
                  </button>
                </div>
              </div>

              {loopViewMode === 'timeline_list' ? (
                <div className="space-y-2.5 max-h-[460px] overflow-y-auto pr-1">
                  {attempts.length > 0 && (
                    <div className="space-y-2">
                      <div className="text-[11px] font-mono text-slate-400">Attempts seen in events:</div>
                      {attempts.map((att) => (
                        <div key={att.attempt_id} className={`p-3 rounded border text-xs font-mono space-y-1 ${bgSubtle}`}>
                          <div className="font-bold text-sky-300">
                            {att.attempt_id} · {fmtStr(att.stage)}
                          </div>
                          <div className="text-[11px] text-slate-300">
                            {fmtStr(att.model)} · effort {fmtStr(att.effort)} · {fmtStr(att.status)}
                          </div>
                          <div className="text-[11px] text-slate-400">
                            tokens {fmtInt(att.prompt_tokens)} in / {fmtInt(att.completion_tokens)} out · cost: Unknown
                          </div>
                        </div>
                      ))}
                    </div>
                  )}

                  <div className="pt-2 border-t border-slate-800 space-y-1.5">
                    {events.length === 0 && <div className="text-[11px] font-mono text-slate-400">No events recorded.</div>}
                    {events.map((ev) => (
                      <div
                        key={ev.seq}
                        data-testid="loop-event"
                        className="p-2 rounded bg-slate-950/80 border border-slate-800/80 text-[11px] font-mono flex items-start gap-2"
                      >
                        <span className="px-1.5 py-0.5 rounded bg-slate-800 text-sky-300 shrink-0">#{ev.seq}</span>
                        <div className="min-w-0 flex-1">
                          <div className="flex flex-wrap items-center gap-2 text-slate-300">
                            <span className="font-bold text-amber-300">{ev.type}</span>
                            <span className="text-slate-500">{formatTimestamp(ev.ts)}</span>
                          </div>
                          <div className="text-slate-300 mt-0.5 break-words">{describeEvent(ev)}</div>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              ) : (
                <div className={`p-3 rounded border space-y-2 text-xs font-mono ${bgSubtle}`}>
                  {stageRows.length === 0 && <div className="text-slate-400">Stage list not loaded yet.</div>}
                  {stageRows.map((row) => (
                    <div key={row.stage} className="flex items-center justify-between p-2 rounded bg-slate-900 border border-slate-800">
                      <span>{row.stage}</span>
                      <span className="px-2 py-0.5 rounded text-[10px] uppercase bg-slate-800 text-slate-300">{fmtStr(row.status)}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        ) : null}

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
                    loopInspectorTab === tab.id ? 'bg-sky-500 text-slate-950 font-bold' : 'bg-slate-900 text-slate-300 hover:bg-slate-800'
                  }`}
                >
                  {tab.label}
                </button>
              ))}
            </div>

            {loopInspectorTab === 'tests' && (
              <div className="space-y-2 text-xs font-mono">
                <div className="font-bold text-sky-300">Tests</div>
                <div className={`p-2.5 rounded border ${bgSubtle}`}>
                  Tests passed (job): <strong>{fmtBool(selectedLoopJob.tests_passed ?? null, 'yes', 'no')}</strong>
                </div>
                {(selectedLoopJob.failing_tests ?? []).map((f, i) => (
                  <div key={i} className="p-2 rounded bg-rose-950/40 border border-rose-800/60 text-[11px] break-words">
                    {JSON.stringify(f)}
                  </div>
                ))}
              </div>
            )}

            {loopInspectorTab === 'reviews' && (
              <div className="space-y-2 text-xs font-mono">
                <div className="font-bold text-sky-300">Review</div>
                <div className={`p-2.5 rounded border ${bgSubtle}`}>
                  Verdict: <strong>{fmtStr(selectedLoopJob.review_verdict)}</strong>
                </div>
                {(selectedLoopJob.findings ?? []).length === 0 ? (
                  <div className={`p-3 rounded border ${bgSubtle} text-slate-400`}>No findings recorded.</div>
                ) : (
                  (selectedLoopJob.findings ?? []).map((fd, i) => (
                    <div key={i} className="p-2 rounded bg-amber-950/40 border border-amber-700/50 text-[11px] break-words">
                      {fd}
                    </div>
                  ))
                )}
              </div>
            )}

            {loopInspectorTab === 'diff' && (
              <div className="space-y-2 text-xs font-mono">
                <div className="font-bold text-sky-300">Diff</div>
                <div className={`p-3 rounded border ${bgSubtle} text-slate-400`}>
                  The diff is shown on the Results page (GET /api/results/{'{id}'}/diff).
                </div>
              </div>
            )}

            {loopInspectorTab === 'lineage' && (
              <div className="space-y-2 text-xs font-mono">
                <div className="font-bold text-sky-300">Files</div>
                <div className={`p-2.5 rounded border ${bgSubtle} space-y-1`}>
                  {(selectedLoopJob.files ?? []).length === 0 && <div className="text-slate-400">No files listed.</div>}
                  {(selectedLoopJob.files ?? []).map((f) => (
                    <div key={f} className="break-all">
                      {f}
                    </div>
                  ))}
                </div>
                <div className={`p-2.5 rounded border ${bgSubtle}`}>
                  Stage snapshot sha256: <code className="break-all">{fmtStr(selectedLoopJob.stage_snapshot?.sha256 ?? null)}</code>
                </div>
              </div>
            )}

            {loopInspectorTab === 'budget' && (
              <div className="space-y-2 text-xs font-mono">
                <div className="font-bold text-sky-300">Budget</div>
                <div className={`p-2.5 rounded border ${bgSubtle} space-y-1`}>
                  <div>Free requests used: {fmtInt(selectedLoopJob.budget?.free_requests_used ?? selectedLoopJob.free_requests_used ?? null)}</div>
                  <div>Free requests limit: {fmtInt(selectedLoopJob.budget?.free_requests ?? null)}</div>
                  <div>Review spend: {fmtUsd(selectedLoopJob.budget?.review_usd_spent ?? selectedLoopJob.review_usd_spent ?? null)}</div>
                  <div>Review budget: {fmtUsd(selectedLoopJob.budget?.review_usd ?? null)}</div>
                  <div>Repair calls: {fmtInt(selectedLoopJob.budget?.repair_calls ?? null)}</div>
                </div>
              </div>
            )}

            {loopInspectorTab === 'artifacts' && (
              <div className="space-y-2 text-xs font-mono">
                <div className="font-bold text-sky-300">Archive</div>
                <div className={`p-2.5 rounded border ${bgSubtle} space-y-1`}>
                  <div>Available: {fmtBool(selectedLoopJob.archive?.available ?? null)}</div>
                  <div>Archive failed: {fmtBool(selectedLoopJob.archive?.archive_failed ?? null)}</div>
                </div>
              </div>
            )}
          </div>
        ) : null}
      </div>
    </div>
  );
};
