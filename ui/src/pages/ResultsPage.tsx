import React from 'react';
import { Download } from 'lucide-react';
import type { JobRecord } from '../api/types';

export interface ResultsPageProps {
  jobs: JobRecord[];
  selectedResultJob: JobRecord | null;
  resultsSearch: string;
  resultsOutcomeFilter: string;
  resultsTab: 'assertions' | 'reviews' | 'diff' | 'lineage' | 'calls' | 'artifacts';
  pendingAction: string | null;
  bgPanel: string;
  bgSubtle: string;
  textMuted: string;
  onChangeSearch: (q: string) => void;
  onChangeOutcomeFilter: (f: string) => void;
  onSelectResultJob: (jobId: string) => void;
  onChangeTab: (
    t: 'assertions' | 'reviews' | 'diff' | 'lineage' | 'calls' | 'artifacts'
  ) => void;
  onDownloadArchive: (jobId: string) => void;
  onSimulateMergeComplete: (jobId: string) => void;
}

export const ResultsPage: React.FC<ResultsPageProps> = ({
  jobs,
  selectedResultJob,
  resultsSearch,
  resultsOutcomeFilter,
  resultsTab,
  pendingAction,
  bgPanel,
  bgSubtle,
  textMuted,
  onChangeSearch,
  onChangeOutcomeFilter,
  onSelectResultJob,
  onChangeTab,
  onDownloadArchive,
  onSimulateMergeComplete,
}) => {
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-lg md:text-xl font-bold tracking-tight">
          6. Results &amp; Archival Ledger (/results)
        </h1>
        <p className={`text-xs ${textMuted}`}>
          Four independent top-level verdicts (`end_state != test_verdict != reviewer_verdict !=
          merge_state`), candidate SHA-256 hash equality verification, `restored-green` evidence,
          and real binary Blob ZIP archive download ({'(GET /api/results/{id}/download)'}).
        </p>
      </div>

      <div className="p-2.5 rounded border border-slate-800 bg-slate-900/50 text-xs font-mono text-slate-300">
        Adapter Notice (Gap C-03): GET /api/results collection pagination is not yet specified in
        docs/API_CONTRACT.md. Filtering obtained job records client-side via typed normalizer
        `normalizeJob()`.
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-4 items-start">
        <div className={`lg:col-span-4 rounded-md border p-3 space-y-3 ${bgPanel}`}>
          <div className="space-y-2">
            <input
              type="text"
              aria-label="Search Results by Job ID or Spec"
              value={resultsSearch}
              onChange={(e) => onChangeSearch(e.target.value)}
              placeholder="Search job_id or spec_id..."
              className="w-full bg-slate-900 border border-slate-700 rounded px-2.5 py-1.5 text-xs font-mono"
            />
            <select
              aria-label="Filter Results by Outcome"
              value={resultsOutcomeFilter}
              onChange={(e) => onChangeOutcomeFilter(e.target.value)}
              className="w-full bg-slate-900 border border-slate-700 rounded px-2.5 py-1.5 text-xs font-mono"
            >
              <option value="all">All Outcomes</option>
              <option value="passed">end_state: passed</option>
              <option value="review-open">end_state: review-open</option>
              <option value="escalate">end_state: escalate</option>
              <option value="quota-exhausted">end_state: quota-exhausted</option>
            </select>
          </div>

          <div className="space-y-2 max-h-[560px] overflow-y-auto pr-1">
            {jobs
              .filter((j) => {
                if (
                  resultsSearch &&
                  !j.job_id.toLowerCase().includes(resultsSearch.toLowerCase()) &&
                  !j.spec_id.toLowerCase().includes(resultsSearch.toLowerCase())
                ) {
                  return false;
                }
                if (resultsOutcomeFilter !== 'all' && j.end_state !== resultsOutcomeFilter) {
                  return false;
                }
                return true;
              })
              .map((j) => {
                const isSel = selectedResultJob?.job_id === j.job_id;
                return (
                  <button
                    key={j.job_id}
                    type="button"
                    onClick={() => onSelectResultJob(j.job_id)}
                    className={`w-full text-left p-2.5 rounded border text-xs font-mono space-y-1 transition ${
                      isSel
                        ? 'bg-sky-500/15 border-sky-400'
                        : 'bg-slate-900/50 border-slate-800 hover:border-slate-700'
                    }`}
                  >
                    <div className="flex items-center justify-between">
                      <span className="font-bold text-sky-300">{j.job_id}</span>
                      <span className="px-1.5 py-0.5 rounded bg-black/40 text-[10px]">
                        {j.end_state || j.derived_state}
                      </span>
                    </div>
                    <div className="text-[11px] text-slate-400">{j.job_path}</div>
                    <div className="text-[10px] text-slate-300 flex flex-wrap gap-2">
                      <span>Test: {j.test_verdict}</span>
                      <span>Rev: {j.reviewer_verdict}</span>
                      <span>Merge: {j.merge_state}</span>
                    </div>
                  </button>
                );
              })}
          </div>
        </div>

        {/* Right Detail Panel (8 cols) */}
        {selectedResultJob ? (
          <div className="lg:col-span-8 space-y-4">
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2.5 font-mono">
              <div className={`p-3 rounded-md border ${bgPanel}`}>
                <div className="text-[10px] text-slate-400">1. JOB VERDICT (`end_state`)</div>
                <div className="text-base font-bold text-sky-300 mt-1">
                  {selectedResultJob.end_state || selectedResultJob.derived_state}
                </div>
              </div>
              <div className={`p-3 rounded-md border ${bgPanel}`}>
                <div className="text-[10px] text-slate-400">2. SANDBOX TESTS</div>
                <div className="text-base font-bold text-emerald-300 mt-1">
                  {selectedResultJob.test_verdict}
                </div>
              </div>
              <div className={`p-3 rounded-md border ${bgPanel}`}>
                <div className="text-[10px] text-slate-400">3. INDEPENDENT REVIEW</div>
                <div className="text-base font-bold text-amber-300 mt-1">
                  {selectedResultJob.reviewer_verdict}
                </div>
              </div>
              <div className={`p-3 rounded-md border ${bgPanel}`}>
                <div className="text-[10px] text-slate-400">4. MERGE (`merge_state`)</div>
                <div className="text-base font-bold text-purple-300 mt-1">
                  {selectedResultJob.merge_state === 'none'
                    ? 'Not merged (none)'
                    : selectedResultJob.merge_state}
                </div>
              </div>
            </div>

            <div className={`rounded-md border p-3.5 space-y-2 text-xs font-mono ${bgPanel}`}>
              {selectedResultJob.normalization_note && (
                <div className="p-2 rounded bg-sky-950/50 border border-sky-700/60 text-sky-200 text-[11px]">
                  ✓ {selectedResultJob.normalization_note}
                </div>
              )}
              {selectedResultJob.restored_green && (
                <div className="p-2.5 rounded bg-emerald-950/50 border border-emerald-600/60 text-emerald-200">
                  <strong>RESTORED-GREEN CANDIDATE RETAINED:</strong>{' '}
                  {selectedResultJob.restored_green_note}
                </div>
              )}
              <div className="grid grid-cols-1 md:grid-cols-3 gap-2 text-[11px] pt-1">
                <div className="p-2 rounded bg-slate-900 border border-slate-800">
                  <span className="text-slate-400 block">tested_sha256:</span>
                  <code>{selectedResultJob.hashes.tested_sha256 || 'n/a'}</code>
                </div>
                <div className="p-2 rounded bg-slate-900 border border-slate-800">
                  <span className="text-slate-400 block">reviewed_sha256:</span>
                  <code>{selectedResultJob.hashes.reviewed_sha256 || 'n/a'}</code>
                </div>
                <div className="p-2 rounded bg-slate-900 border border-slate-800">
                  <span className="text-slate-400 block">delivered_sha256:</span>
                  <code>{selectedResultJob.hashes.delivered_sha256 || 'n/a'}</code>
                </div>
              </div>
              <div className="flex flex-wrap items-center justify-between gap-2 pt-1">
                <span className="text-[11px]">
                  Integrity Gate (`tested == reviewed == delivered`):{' '}
                  <strong
                    className={
                      selectedResultJob.hashes.integrity_equal
                        ? 'text-emerald-400'
                        : 'text-amber-400'
                    }
                  >
                    {selectedResultJob.hashes.integrity_equal
                      ? 'EQUAL & VERIFIED'
                      : 'INCOMPLETE / NOT DELIVERED'}
                  </strong>
                </span>

                {selectedResultJob.merge_state === 'pending' && (
                  <button
                    type="button"
                    onClick={() => onSimulateMergeComplete(selectedResultJob.job_id)}
                    className="px-2.5 py-1 rounded bg-emerald-500 text-slate-950 font-bold text-xs"
                  >
                    Simulate Automatic Merge Completion (Scenario 3)
                  </button>
                )}
              </div>
            </div>

            <div className={`rounded-md border p-4 space-y-3 ${bgPanel}`}>
              <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-800 pb-2.5">
                <div className="flex flex-wrap gap-1 text-xs font-mono">
                  {(
                    [
                      { id: 'reviews', label: '1. Reviews & Evidence' },
                      { id: 'assertions', label: '2. Failed Assertions' },
                      { id: 'diff', label: '3. Unified Diff' },
                      { id: 'lineage', label: '4. Contributor Lineage' },
                      { id: 'calls', label: '5. Calls & Cost' },
                      { id: 'artifacts', label: '6. Archive & Merge' },
                    ] as const
                  ).map((t) => (
                    <button
                      key={t.id}
                      type="button"
                      onClick={() => onChangeTab(t.id)}
                      className={`px-2.5 py-1 rounded ${
                        resultsTab === t.id
                          ? 'bg-sky-500 text-slate-950 font-bold'
                          : 'bg-slate-900 text-slate-300'
                      }`}
                    >
                      {t.label}
                    </button>
                  ))}
                </div>

                <button
                  type="button"
                  disabled={
                    !selectedResultJob.archive.available || pendingAction === 'download-archive'
                  }
                  onClick={() => onDownloadArchive(selectedResultJob.job_id)}
                  className={`px-3 py-1 rounded text-xs font-mono font-semibold inline-flex items-center gap-1.5 ${
                    selectedResultJob.archive.available
                      ? 'bg-emerald-500 hover:bg-emerald-400 text-slate-950'
                      : 'bg-slate-800 text-slate-500 cursor-not-allowed border border-slate-700'
                  }`}
                  title={
                    selectedResultJob.archive.available
                      ? `Download ${selectedResultJob.archive.path_hint}`
                      : selectedResultJob.archive.failure_reason
                  }
                >
                  <Download className="w-3.5 h-3.5" />
                  {selectedResultJob.archive.available
                    ? `Download ZIP (${((selectedResultJob.archive.size_bytes || 0) / 1024).toFixed(
                        1
                      )} KB)`
                    : 'Archive ZIP Unavailable'}
                </button>
              </div>

              {resultsTab === 'reviews' && (
                <div className="space-y-2.5 text-xs font-mono">
                  {selectedResultJob.reviews.length === 0 ? (
                    <div className="text-slate-400">
                      No reviewer reports recorded for {selectedResultJob.job_id}.
                    </div>
                  ) : (
                    selectedResultJob.reviews.map((rv, i) => (
                      <div key={i} className={`p-3 rounded border space-y-2 ${bgSubtle}`}>
                        <div className="flex items-center justify-between">
                          <span className="font-bold text-sky-300">{rv.reviewer_slot}</span>
                          <span
                            className={`px-2 py-0.5 rounded ${
                              rv.verdict === 'READY'
                                ? 'bg-emerald-500/20 text-emerald-300'
                                : 'bg-amber-500/20 text-amber-300'
                            }`}
                          >
                            {rv.verdict}
                          </span>
                        </div>
                        <div className="text-[11px] text-slate-300">
                          Model: <code>{rv.model_id}</code> · Contributor-Disjoint:{' '}
                          <strong className="text-emerald-400">
                            {String(rv.independent_of_authors)}
                          </strong>{' '}
                          · Backend `evidence_ok`:{' '}
                          <strong className="text-emerald-400">{String(rv.evidence_ok)}</strong>
                        </div>
                        {rv.findings.map((fd, fi) => (
                          <div
                            key={fi}
                            className="p-2.5 rounded bg-amber-950/40 border border-amber-700/60 space-y-1"
                          >
                            <div className="flex items-center justify-between text-amber-300 font-bold">
                              <span>
                                [{fd.ref}] {fd.file}:{fd.line}
                              </span>
                              <span className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-500/20 text-emerald-300">
                                evidence_ok: {String(fd.evidence_ok)}
                              </span>
                            </div>
                            <div className="text-slate-200">{fd.message}</div>
                          </div>
                        ))}
                      </div>
                    ))
                  )}
                </div>
              )}

              {resultsTab === 'assertions' && (
                <div className="space-y-2 text-xs font-mono">
                  {selectedResultJob.attempts
                    .filter((a) => a.tests_summary && a.tests_summary.failures.length > 0)
                    .map((a) => (
                      <div
                        key={a.attempt_id}
                        className={`p-3 rounded border space-y-1.5 ${bgSubtle}`}
                      >
                        <div className="font-bold text-rose-300">
                          {a.attempt_id} ({a.stage_key}) — {a.outcome}
                        </div>
                        {a.tests_summary?.failures.map((f, fi) => (
                          <div key={fi} className="p-2 rounded bg-slate-950 border border-slate-800">
                            <div className="text-sky-300">
                              {f.test_name} ({f.file}:{f.line})
                            </div>
                            <div className="text-rose-300 mt-0.5">{f.assertion}</div>
                          </div>
                        ))}
                      </div>
                    ))}
                  {selectedResultJob.attempts.every(
                    (a) => !a.tests_summary || a.tests_summary.failures.length === 0
                  ) && (
                    <div className="text-emerald-300">
                      Zero failed assertions on delivered candidate for {selectedResultJob.job_id}.
                    </div>
                  )}
                </div>
              )}

              {resultsTab === 'diff' && (
                <div className="text-xs font-mono">
                  {selectedResultJob.diff_text ? (
                    <pre className="p-3 rounded bg-slate-950 border border-slate-800 text-emerald-300 overflow-x-auto whitespace-pre-wrap">
                      {selectedResultJob.diff_text}
                    </pre>
                  ) : (
                    <div className="p-3 rounded border border-rose-800/50 bg-rose-950/30 text-rose-200">
                      Diff unavailable for {selectedResultJob.job_id} (no candidate diff recorded
                      or archive failed).
                    </div>
                  )}
                </div>
              )}

              {resultsTab === 'lineage' && (
                <div className="space-y-2 text-xs font-mono">
                  {selectedResultJob.contributor_chain.map((c, idx) => (
                    <div key={idx} className={`p-2.5 rounded border ${bgSubtle}`}>
                      <span className="font-bold text-sky-300">{c.attempt_id}</span> ·{' '}
                      <span>{c.stage}</span> · canonical: <code>{c.canonical_model_id}</code> ·{' '}
                      <span>{c.status}</span>
                    </div>
                  ))}
                </div>
              )}

              {resultsTab === 'calls' && (
                <div className="space-y-2 text-xs font-mono">
                  <div>Free Requests Used: {selectedResultJob.free_requests_used}</div>
                  <div>
                    Total Reconciled Cost:{' '}
                    {selectedResultJob.total_cost_usd !== null
                      ? `$${selectedResultJob.total_cost_usd}`
                      : 'Unknown'}
                  </div>
                </div>
              )}

              {resultsTab === 'artifacts' && (
                <div className="space-y-3 text-xs font-mono">
                  <div className={`p-3 rounded border space-y-1.5 ${bgSubtle}`}>
                    <div className="font-bold text-sky-300">
                      Merge Details (`merge.json`): {selectedResultJob.merge_detail.status}
                    </div>
                    <div>Target Branch: {selectedResultJob.merge_detail.target_branch}</div>
                    <div>Base SHA: {selectedResultJob.merge_detail.base_sha}</div>
                    {selectedResultJob.merge_detail.commit_sha && (
                      <div className="text-emerald-300">
                        Merged Commit SHA: {selectedResultJob.merge_detail.commit_sha}
                      </div>
                    )}
                    <div>Retries: {selectedResultJob.merge_detail.retries}</div>
                    <div className="text-slate-300">{selectedResultJob.merge_detail.message}</div>
                  </div>

                  <div className={`p-3 rounded border space-y-1.5 ${bgSubtle}`}>
                    <div className="font-bold text-sky-300">
                      Archive Status (`{selectedResultJob.archive.path_hint}`):{' '}
                      {selectedResultJob.archive.available ? 'AVAILABLE' : 'UNAVAILABLE / FAILED'}
                    </div>
                    {!selectedResultJob.archive.available && (
                      <div className="text-rose-300">
                        Reason: {selectedResultJob.archive.failure_reason}
                      </div>
                    )}
                    <div>Recorded Files: {selectedResultJob.archive.artifacts.join(', ')}</div>
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
