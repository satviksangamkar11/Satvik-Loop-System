import React from 'react';
import { Download } from 'lucide-react';
import type { JobRecord, ResultRecord } from '../api/types';
import { fmtBool, fmtInt, fmtStr, fmtUsd } from '../api/format';

export interface ResultsPageProps {
  jobs: JobRecord[];
  selectedResultJobId: string | null;
  result: ResultRecord | null;
  diffFiles: Array<{ path: string; unified_diff: string }>;
  resultsSearch: string;
  resultsOutcomeFilter: string;
  resultsTab: 'assertions' | 'reviews' | 'diff' | 'lineage' | 'calls' | 'artifacts';
  pendingAction: string | null;
  bgPanel: string;
  bgSubtle: string;
  textMuted: string;
  formatTimestamp: (iso: string | null | undefined) => string;
  onChangeSearch: (q: string) => void;
  onChangeOutcomeFilter: (f: string) => void;
  onSelectResultJob: (jobId: string) => void;
  onChangeTab: (t: 'assertions' | 'reviews' | 'diff' | 'lineage' | 'calls' | 'artifacts') => void;
  onDownloadArchive: (jobId: string) => void;
}

export const ResultsPage: React.FC<ResultsPageProps> = ({
  jobs,
  selectedResultJobId,
  result,
  diffFiles,
  resultsSearch,
  resultsOutcomeFilter,
  resultsTab,
  pendingAction,
  bgPanel,
  bgSubtle,
  textMuted,
  formatTimestamp,
  onChangeSearch,
  onChangeOutcomeFilter,
  onSelectResultJob,
  onChangeTab,
  onDownloadArchive,
}) => {
  const job = jobs.find((j) => j.job_id === selectedResultJobId) ?? null;
  const hashes = job?.hashes ?? null;
  const integrityEqual =
    hashes !== null && hashes.tested !== null && hashes.tested === hashes.reviewed && hashes.reviewed === hashes.delivered;

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-lg md:text-xl font-bold tracking-tight">6. Results (/results)</h1>
        <p className={`text-xs ${textMuted}`}>
          Four verdicts from GET /results/{'{id}'} and the job detail: end_state, tests, reviewer verdict and merge_state.
          The archive ZIP downloads from GET /results/{'{id}'}/download.
        </p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-4 items-start">
        <div className={`lg:col-span-4 rounded-md border p-3 space-y-3 ${bgPanel}`}>
          <div className="space-y-2">
            <input
              type="text"
              aria-label="Search Results by Job ID"
              value={resultsSearch}
              onChange={(e) => onChangeSearch(e.target.value)}
              placeholder="Search job_id"
              className="w-full bg-slate-900 border border-slate-700 rounded px-2.5 py-1.5 text-xs font-mono"
            />
            <select
              aria-label="Filter Results by Outcome"
              value={resultsOutcomeFilter}
              onChange={(e) => onChangeOutcomeFilter(e.target.value)}
              className="w-full bg-slate-900 border border-slate-700 rounded px-2.5 py-1.5 text-xs font-mono"
            >
              <option value="all">All end_state values</option>
              <option value="passed">passed</option>
              <option value="review-open">review-open</option>
              <option value="escalate">escalate</option>
              <option value="quota-exhausted">quota-exhausted</option>
            </select>
          </div>

          <div className="space-y-2 max-h-[560px] overflow-y-auto pr-1">
            {jobs
              .filter((j) => {
                if (resultsSearch && !j.job_id.toLowerCase().includes(resultsSearch.toLowerCase())) return false;
                if (resultsOutcomeFilter !== 'all' && j.end_state !== resultsOutcomeFilter) return false;
                return true;
              })
              .map((j) => {
                const isSel = selectedResultJobId === j.job_id;
                return (
                  <button
                    key={j.job_id}
                    type="button"
                    data-testid={`result-select-${j.job_id}`}
                    onClick={() => onSelectResultJob(j.job_id)}
                    className={`w-full text-left p-2.5 rounded border text-xs font-mono space-y-1 transition ${
                      isSel ? 'bg-sky-500/15 border-sky-400' : 'bg-slate-900/50 border-slate-800 hover:border-slate-700'
                    }`}
                  >
                    <div className="flex items-center justify-between">
                      <span className="font-bold text-sky-300 truncate">{j.job_id}</span>
                      <span className="px-1.5 py-0.5 rounded bg-black/40 text-[10px]">{fmtStr(j.end_state ?? j.derived_state)}</span>
                    </div>
                    <div className="text-[10px] text-slate-300">Merge: {fmtStr(j.merge_state)}</div>
                  </button>
                );
              })}
            {jobs.length === 0 && <p className={`text-xs ${textMuted}`}>No jobs from GET /api/jobs.</p>}
          </div>
        </div>

        {job ? (
          <div className="lg:col-span-8 space-y-4">
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2.5 font-mono">
              <div className={`p-3 rounded-md border ${bgPanel}`}>
                <div className="text-[10px] text-slate-400">END_STATE</div>
                <div className="text-base font-bold text-sky-300 mt-1">{fmtStr(result?.end_state ?? job.end_state)}</div>
              </div>
              <div className={`p-3 rounded-md border ${bgPanel}`}>
                <div className="text-[10px] text-slate-400">TESTS</div>
                <div className="text-base font-bold text-emerald-300 mt-1">
                  {result?.tests ? fmtBool(result.tests.passed, 'passed', 'failed') : job.tests_passed !== undefined ? fmtBool(job.tests_passed, 'passed', 'failed') : 'Unknown'}
                </div>
              </div>
              <div className={`p-3 rounded-md border ${bgPanel}`}>
                <div className="text-[10px] text-slate-400">REVIEWER VERDICT</div>
                <div className="text-base font-bold text-amber-300 mt-1">{fmtStr(job.review_verdict)}</div>
              </div>
              <div className={`p-3 rounded-md border ${bgPanel}`}>
                <div className="text-[10px] text-slate-400">MERGE_STATE</div>
                <div className="text-base font-bold text-purple-300 mt-1">{fmtStr(result?.merge_state ?? job.merge_state)}</div>
              </div>
            </div>

            <div className={`rounded-md border p-3.5 space-y-2 text-xs font-mono ${bgPanel}`}>
              <div className="grid grid-cols-1 md:grid-cols-3 gap-2 text-[11px]">
                <div className="p-2 rounded bg-slate-900 border border-slate-800">
                  <span className="text-slate-400 block">tested:</span>
                  <code className="break-all">{fmtStr(hashes?.tested ?? null)}</code>
                </div>
                <div className="p-2 rounded bg-slate-900 border border-slate-800">
                  <span className="text-slate-400 block">reviewed:</span>
                  <code className="break-all">{fmtStr(hashes?.reviewed ?? null)}</code>
                </div>
                <div className="p-2 rounded bg-slate-900 border border-slate-800">
                  <span className="text-slate-400 block">delivered:</span>
                  <code className="break-all">{fmtStr(hashes?.delivered ?? null)}</code>
                </div>
              </div>
              <div className="flex flex-wrap items-center justify-between gap-2 pt-1">
                <span className="text-[11px]">
                  Integrity (tested = reviewed = delivered):{' '}
                  <strong className={integrityEqual ? 'text-emerald-400' : 'text-amber-400'}>
                    {integrityEqual ? 'EQUAL' : 'NOT EQUAL OR NOT RECORDED'}
                  </strong>
                </span>
                <button
                  type="button"
                  data-testid="download-archive-btn"
                  disabled={job.archive?.available !== true || pendingAction === 'download-archive'}
                  onClick={() => onDownloadArchive(job.job_id)}
                  className={`px-3 py-1 rounded text-xs font-mono font-semibold inline-flex items-center gap-1.5 ${
                    job.archive?.available === true
                      ? 'bg-emerald-500 hover:bg-emerald-400 text-slate-950'
                      : 'bg-slate-800 text-slate-500 cursor-not-allowed border border-slate-700'
                  }`}
                >
                  <Download className="w-3.5 h-3.5" />
                  {job.archive?.available === true ? 'Download archive ZIP' : 'Archive not available'}
                </button>
              </div>
            </div>

            <div className={`rounded-md border p-4 space-y-3 ${bgPanel}`}>
              <div className="flex flex-wrap gap-1 border-b border-slate-800 pb-2.5 text-xs font-mono">
                {(
                  [
                    { id: 'reviews', label: 'Reviews' },
                    { id: 'assertions', label: 'Failed tests' },
                    { id: 'diff', label: 'Diff' },
                    { id: 'lineage', label: 'Files' },
                    { id: 'calls', label: 'Budget' },
                    { id: 'artifacts', label: 'Artifacts & merge' },
                  ] as const
                ).map((t) => (
                  <button
                    key={t.id}
                    type="button"
                    onClick={() => onChangeTab(t.id)}
                    className={`px-2.5 py-1 rounded ${resultsTab === t.id ? 'bg-sky-500 text-slate-950 font-bold' : 'bg-slate-900 text-slate-300'}`}
                  >
                    {t.label}
                  </button>
                ))}
              </div>

              {resultsTab === 'reviews' && (
                <div className="space-y-2.5 text-xs font-mono">
                  {(result?.reviews ?? []).length === 0 ? (
                    <div className="text-slate-400">No reviewer reports recorded.</div>
                  ) : (
                    (result?.reviews ?? []).map((rv, i) => (
                      <div key={i} className={`p-3 rounded border space-y-2 ${bgSubtle}`}>
                        <div className="flex items-center justify-between">
                          <span className="font-bold text-sky-300">{fmtStr(rv.reviewer)}</span>
                          <span className="px-2 py-0.5 rounded bg-amber-500/20 text-amber-300">{fmtStr(rv.verdict)}</span>
                        </div>
                        <div className="text-[11px] text-slate-400">file: {fmtStr(rv.file)}</div>
                        {rv.findings.map((fd, fi) => (
                          <div key={fi} className="p-2.5 rounded bg-amber-950/40 border border-amber-700/60 break-words">
                            {fd}
                          </div>
                        ))}
                      </div>
                    ))
                  )}
                </div>
              )}

              {resultsTab === 'assertions' && (
                <div className="space-y-2 text-xs font-mono">
                  {result?.tests === null || result === null ? (
                    <div className="text-slate-400">Test results are not loaded.</div>
                  ) : result.tests.failing.length === 0 ? (
                    <div className="text-emerald-300">No failing tests recorded.</div>
                  ) : (
                    result.tests.failing.map((f, i) => (
                      <div key={i} className={`p-2 rounded border ${bgSubtle}`}>
                        <div className="text-sky-300">{fmtStr(f.id)}</div>
                        <div className="text-slate-400">
                          {fmtStr(f.file)}:{fmtInt(f.line)}
                        </div>
                        <div className="text-rose-300 mt-0.5 break-words">{fmtStr(f.message)}</div>
                      </div>
                    ))
                  )}
                </div>
              )}

              {resultsTab === 'diff' && (
                <div className="text-xs font-mono space-y-2">
                  {diffFiles.length === 0 && result?.diff_available !== true && (
                    <div className="p-3 rounded border border-rose-800/50 bg-rose-950/30 text-rose-200">
                      No diff is available for this job.
                    </div>
                  )}
                  {diffFiles.map((f) => (
                    <div key={f.path} className="space-y-1">
                      <div className="text-sky-300">{f.path}</div>
                      <pre className="p-3 rounded bg-slate-950 border border-slate-800 text-emerald-300 overflow-x-auto whitespace-pre-wrap">
                        {f.unified_diff}
                      </pre>
                    </div>
                  ))}
                </div>
              )}

              {resultsTab === 'lineage' && (
                <div className="space-y-2 text-xs font-mono">
                  {(job.files ?? []).length === 0 && <div className="text-slate-400">No files listed.</div>}
                  {(job.files ?? []).map((f) => (
                    <div key={f} className={`p-2 rounded border break-all ${bgSubtle}`}>
                      {f}
                    </div>
                  ))}
                </div>
              )}

              {resultsTab === 'calls' && (
                <div className="space-y-2 text-xs font-mono">
                  <div>Free requests used: {fmtInt(job.budget?.free_requests_used ?? job.free_requests_used ?? null)}</div>
                  <div>Review spend: {fmtUsd(job.budget?.review_usd_spent ?? job.review_usd_spent ?? null)}</div>
                  <div>Repair calls: {fmtInt(job.budget?.repair_calls ?? null)}</div>
                </div>
              )}

              {resultsTab === 'artifacts' && (
                <div className="space-y-3 text-xs font-mono">
                  <div className={`p-3 rounded border space-y-1.5 ${bgSubtle}`}>
                    <div className="font-bold text-sky-300">Merge</div>
                    <div>merge_state: {fmtStr(job.merge?.merge_state ?? job.merge_state)}</div>
                    <div>head: {fmtStr(job.merge?.head ?? null)}</div>
                    <div>attempts: {fmtInt(job.merge?.attempts ?? null)}</div>
                    {job.merge?.note && <div className="text-slate-300">{job.merge.note}</div>}
                  </div>
                  <div className={`p-3 rounded border space-y-1.5 ${bgSubtle}`}>
                    <div className="font-bold text-sky-300">Archive</div>
                    <div>available: {fmtBool(job.archive?.available ?? null)}</div>
                    <div>archive_failed: {fmtBool(job.archive?.archive_failed ?? null)}</div>
                    <div>branch: {fmtStr(result?.branch ?? null)}</div>
                    <div>artifacts: {result && result.artifacts.length > 0 ? result.artifacts.join(', ') : 'Unknown'}</div>
                    <div>finished: {formatTimestamp(job.finished)}</div>
                  </div>
                </div>
              )}
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
};
