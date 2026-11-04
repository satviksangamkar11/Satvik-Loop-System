import React, { useState } from 'react';
import { Ban, Play, Plus, RotateCcw } from 'lucide-react';
import type { PlanRecord, ProfileName, StageSetting } from '../api/types';
import { fmtInt, fmtStr } from '../api/format';

export interface RunJobsPageProps {
  profile: ProfileName;
  stageRevision: number | null;
  stages: StageSetting[];
  runEntryMode: 'existing_path' | 'planner_request';
  jobPathInput: string;
  jobValidated: boolean | null;
  jobProblems: string[];
  budgetValidated: boolean | null;
  paidBudgetUsd: string;
  workerCountToStart: number;
  planName: string;
  planPrompt: string;
  planFiles: string;
  generatedPlan: PlanRecord | null;
  planApproveAssumed: boolean;
  pendingAction: string | null;
  bgPanel: string;
  bgSubtle: string;
  textMuted: string;
  onChangeProfile: (p: ProfileName) => void;
  onChangeEntryMode: (mode: 'existing_path' | 'planner_request') => void;
  onChangeJobPath: (path: string) => void;
  onValidateJobPath: () => void;
  onChangePaidBudget: (val: string) => void;
  onValidateBudget: () => void;
  onQueueJob: (isRerun: boolean) => void;
  onChangeWorkerCount: (n: number) => void;
  onStartWorkers: () => void;
  onChangePlanName: (val: string) => void;
  onChangePlanPrompt: (val: string) => void;
  onChangePlanFiles: (val: string) => void;
  onCreatePlan: () => void;
  onToggleApproveAssumed: (checked: boolean) => void;
  onApprovePlan: () => void;
}

export const RunJobsPage: React.FC<RunJobsPageProps> = ({
  profile,
  stageRevision,
  stages,
  runEntryMode,
  jobPathInput,
  jobValidated,
  jobProblems,
  budgetValidated,
  paidBudgetUsd,
  workerCountToStart,
  planName,
  planPrompt,
  planFiles,
  generatedPlan,
  planApproveAssumed,
  pendingAction,
  bgPanel,
  bgSubtle,
  textMuted,
  onChangeProfile,
  onChangeEntryMode,
  onChangeJobPath,
  onValidateJobPath,
  onChangePaidBudget,
  onValidateBudget,
  onQueueJob,
  onChangeWorkerCount,
  onStartWorkers,
  onChangePlanName,
  onChangePlanPrompt,
  onChangePlanFiles,
  onCreatePlan,
  onToggleApproveAssumed,
  onApprovePlan,
}) => {
  // UI-only gate: the planner is a paid call, so the user must tick this before the request is sent.
  // The contract's POST /plans body does not carry it.
  const [plannerCostAck, setPlannerCostAck] = useState<boolean>(false);
  const plannerRefused = profile === 'test';
  const plannerPlannerStage = stages.find((s) => s.stage_key === 'planner');

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-lg md:text-xl font-bold tracking-tight">2. Run Jobs (/jobs/new)</h1>
        <p className={`text-xs ${textMuted}`}>
          Two entry paths: a repo-relative job file, or a natural-language plan. Queueing and starting loop workers are
          separate requests.
        </p>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4 items-start">
        <div className="xl:col-span-2 space-y-4">
          <div className={`rounded-md border p-4 space-y-4 ${bgPanel}`}>
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-800 pb-3">
              <span className="text-xs font-mono uppercase text-sky-400 font-bold">Step 1 — Entry path</span>
              <div className="inline-flex rounded border border-slate-700 overflow-hidden text-xs font-mono">
                <button
                  type="button"
                  onClick={() => onChangeEntryMode('existing_path')}
                  className={`px-3 py-1.5 font-semibold ${
                    runEntryMode === 'existing_path' ? 'bg-sky-500 text-slate-950' : 'bg-slate-900 text-slate-300'
                  }`}
                >
                  Path A: Existing job file (jobs/&lt;spec&gt;.json)
                </button>
                <button
                  type="button"
                  onClick={() => onChangeEntryMode('planner_request')}
                  className={`px-3 py-1.5 font-semibold ${
                    runEntryMode === 'planner_request' ? 'bg-sky-500 text-slate-950' : 'bg-slate-900 text-slate-300'
                  }`}
                >
                  Path B: Natural-language plan
                </button>
              </div>
            </div>

            {runEntryMode === 'existing_path' ? (
              <div className="space-y-3 text-xs">
                <div className="p-2.5 rounded border border-slate-800 bg-slate-900/50 text-slate-300 font-mono text-[11px]">
                  The contract has no file picker or upload. Enter a repo-relative job_path and validate it with POST /api/jobs/validate.
                </div>

                <div className="space-y-1.5">
                  <label htmlFor="job-path-input" className="block font-semibold">
                    Repo-relative job path (`job_path`):
                  </label>
                  <div className="flex flex-wrap gap-2">
                    <input
                      id="job-path-input"
                      data-testid="job-path-input"
                      type="text"
                      value={jobPathInput}
                      onChange={(e) => onChangeJobPath(e.target.value)}
                      className="flex-1 min-w-[240px] bg-slate-900 border border-slate-700 rounded px-3 py-1.5 font-mono text-xs text-slate-100"
                      placeholder="jobs/my_spec.json"
                    />
                    <button
                      type="button"
                      data-testid="validate-job-btn"
                      disabled={pendingAction === 'validate-job' || jobPathInput.trim() === ''}
                      onClick={onValidateJobPath}
                      className="px-3 py-1.5 rounded bg-sky-500/20 hover:bg-sky-500/30 text-sky-300 border border-sky-500/40 font-mono font-semibold disabled:opacity-40"
                    >
                      Validate Job (`POST /jobs/validate`)
                    </button>
                  </div>
                </div>

                {jobValidated !== null && (
                  <div
                    data-testid="validation-result"
                    className={`p-2.5 rounded border font-mono text-[11px] space-y-1 ${
                      jobValidated ? 'border-emerald-700 text-emerald-200' : 'border-rose-700 text-rose-200'
                    }`}
                  >
                    <div className="font-bold">Validation Result (`POST /api/jobs/validate`): {jobValidated ? 'valid' : 'invalid'}</div>
                    {jobProblems.map((p, i) => (
                      <div key={i}>• {p}</div>
                    ))}
                  </div>
                )}
              </div>
            ) : (
              <div className="space-y-3 text-xs">
                {plannerRefused && (
                  <div className="p-3 rounded border border-rose-600 bg-rose-950/60 text-rose-100 space-y-1">
                    <div className="font-bold font-mono flex items-center gap-1.5">
                      <Ban className="w-4 h-4 text-rose-400" />
                      test profile: free only
                    </div>
                    <p className="text-[11px]">
                      The test profile refuses the planner (paid). Switch the stage view to production to plan.
                    </p>
                  </div>
                )}

                <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                  <div>
                    <label className="block font-semibold mb-1">Plan name:</label>
                    <input
                      type="text"
                      data-testid="plan-name-input"
                      value={planName}
                      onChange={(e) => onChangePlanName(e.target.value)}
                      className="w-full bg-slate-900 border border-slate-700 rounded px-2.5 py-1.5 font-mono"
                    />
                  </div>
                  <div>
                    <label className="block font-semibold mb-1">Planner stage model (from stage settings):</label>
                    <div className="px-2.5 py-1.5 rounded border border-slate-800 bg-slate-900/60 font-mono text-sky-300">
                      {fmtStr(plannerPlannerStage?.configured_model_id ?? null)}
                    </div>
                  </div>
                </div>

                <div>
                  <label className="block font-semibold mb-1">Request:</label>
                  <textarea
                    rows={2}
                    data-testid="plan-request-input"
                    value={planPrompt}
                    onChange={(e) => onChangePlanPrompt(e.target.value)}
                    className="w-full bg-slate-900 border border-slate-700 rounded p-2 font-mono text-xs"
                  />
                </div>

                <div>
                  <label className="block font-semibold mb-1">Files (1 to 30, one per line):</label>
                  <textarea
                    rows={3}
                    value={planFiles}
                    onChange={(e) => onChangePlanFiles(e.target.value)}
                    className="w-full bg-slate-900 border border-slate-700 rounded p-2 font-mono text-xs"
                  />
                </div>

                <label className="flex items-center gap-2 p-2.5 rounded border border-amber-700/60 bg-amber-950/30 cursor-pointer">
                  <input type="checkbox" checked={plannerCostAck} onChange={(e) => setPlannerCostAck(e.target.checked)} />
                  <span>I authorize one paid planner call to draft the plan.</span>
                </label>

                <button
                  type="button"
                  data-testid="generate-plan-btn"
                  disabled={plannerRefused || !plannerCostAck || pendingAction === 'create-plan'}
                  onClick={onCreatePlan}
                  className="px-3 py-1.5 rounded bg-sky-500 hover:bg-sky-400 text-slate-950 font-mono font-semibold disabled:bg-slate-900 disabled:text-slate-600"
                >
                  Generate Plan (`POST /api/plans`)
                </button>

                {generatedPlan && (
                  <div className="p-3 rounded border border-sky-600/60 bg-sky-950/20 space-y-2.5" data-testid="plan-draft">
                    <div className="flex items-center justify-between font-mono text-xs">
                      <span className="font-bold text-sky-300">Plan {generatedPlan.plan_id}</span>
                      <span>state: {fmtStr(generatedPlan.state)}</span>
                    </div>
                    <div className="font-mono text-[11px]">job_sha256: {fmtStr(generatedPlan.job_sha256)}</div>
                    <div className="text-[11px] space-y-1">
                      <div className="font-semibold text-amber-300">Assumed items (review before approving):</div>
                      {generatedPlan.assumed.length === 0 ? (
                        <div className="text-slate-400">None listed.</div>
                      ) : (
                        generatedPlan.assumed.map((item, i) => (
                          <div key={i} className="p-1.5 rounded bg-slate-900/70 border border-slate-800 font-mono">
                            {typeof item === 'string' ? item : JSON.stringify(item)}
                          </div>
                        ))
                      )}
                    </div>
                    <label className="flex items-center gap-2 text-[11px]">
                      <input type="checkbox" checked={planApproveAssumed} onChange={(e) => onToggleApproveAssumed(e.target.checked)} />
                      <span>I approve the assumed items.</span>
                    </label>
                    <button
                      type="button"
                      data-testid="approve-plan-btn"
                      disabled={!generatedPlan.job_sha256 || generatedPlan.state !== 'planned' || pendingAction === 'approve-plan'}
                      onClick={onApprovePlan}
                      className="px-3 py-1.5 rounded font-mono font-semibold text-xs bg-emerald-500 text-slate-950 hover:bg-emerald-400 disabled:bg-slate-800 disabled:text-slate-500"
                    >
                      Approve &amp; Start Job (`POST /plans/{'{id}'}/approve`; approval starts the job)
                    </button>
                  </div>
                )}
              </div>
            )}
          </div>

          <div className={`rounded-md border p-4 space-y-3 ${bgPanel}`}>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <h2 className="text-sm font-bold uppercase tracking-wider font-mono text-sky-400">
                  Step 2 — Stage settings ({profile} profile)
                </h2>
                <p className={`text-[11px] ${textMuted}`}>
                  The queued job freezes these values. Edit them on Stage Settings.
                </p>
              </div>
              <div className="flex items-center gap-2 text-xs font-mono">
                <span className="text-slate-400">Profile:</span>
                {(['test', 'production'] as ProfileName[]).map((p) => (
                  <button
                    key={p}
                    type="button"
                    onClick={() => onChangeProfile(p)}
                    className={`px-2 py-0.5 rounded border ${profile === p ? 'border-sky-400 text-sky-300' : 'border-slate-700 text-slate-400'}`}
                  >
                    {p}
                  </button>
                ))}
                <span className="text-slate-400">Revision:</span>
                <code>{fmtInt(stageRevision)}</code>
              </div>
            </div>

            {stages.length === 0 ? (
              <p className={`text-xs ${textMuted}`}>No stage settings loaded from GET /api/stages.</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-left border-collapse text-xs">
                  <thead>
                    <tr className="border-b border-slate-800 text-[11px] font-mono text-slate-400">
                      <th className="py-2 pr-2">Stage</th>
                      <th className="py-2 px-2">Model</th>
                      <th className="py-2 px-2">Effort</th>
                      <th className="py-2 px-2">max_output_tokens</th>
                      <th className="py-2 pl-2">Selectable / inherits</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-800/70 font-mono">
                    {stages.map((st) => (
                      <tr key={st.stage_key} className="hover:bg-slate-800/30">
                        <td className="py-2 pr-2 font-semibold text-sky-300">{st.stage_key}</td>
                        <td className="py-2 px-2 truncate max-w-[200px]">{fmtStr(st.configured_model_id)}</td>
                        <td className="py-2 px-2">{fmtStr(st.effort)}</td>
                        <td className="py-2 px-2">{fmtInt(st.max_output_tokens)}</td>
                        <td className="py-2 pl-2 text-[11px] font-sans text-slate-300">
                          {st.model_selectable === true ? 'selectable' : st.model_selectable === false ? `fixed${st.inherits ? `: ${st.inherits}` : ''}` : 'Unknown'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>

        <div className={`rounded-md border p-4 space-y-4 sticky top-16 ${bgPanel}`}>
          <h2 className="text-sm font-bold uppercase tracking-wider font-mono text-sky-400">Step 3 — Budget &amp; execution</h2>

          <div className={`p-3 rounded border space-y-2 text-xs font-mono ${bgSubtle}`}>
            <div className="flex justify-between">
              <span className="text-slate-400">Job validated:</span>
              <span className={jobValidated === true ? 'text-emerald-400' : jobValidated === false ? 'text-rose-400' : 'text-slate-400'}>
                {jobValidated === null ? 'not checked' : jobValidated ? 'YES' : 'NO'}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-slate-400">Budget validated:</span>
              <span className={budgetValidated === true ? 'text-emerald-400' : budgetValidated === false ? 'text-rose-400' : 'text-slate-400'}>
                {budgetValidated === null ? 'not checked' : budgetValidated ? 'YES' : 'NO'}
              </span>
            </div>
          </div>

          <div className="space-y-1.5 text-xs">
            <label htmlFor="paid-budget-input" className="block font-semibold">
              Paid budget ceiling (USD):
            </label>
            <div className="flex gap-2">
              <input
                id="paid-budget-input"
                type="text"
                value={paidBudgetUsd}
                onChange={(e) => onChangePaidBudget(e.target.value)}
                className="flex-1 bg-slate-900 border border-slate-700 rounded px-2.5 py-1.5 font-mono"
              />
              <button
                type="button"
                disabled={pendingAction === 'validate-budget' || Number.isNaN(Number(paidBudgetUsd))}
                onClick={onValidateBudget}
                className="px-2.5 py-1.5 rounded border border-sky-500/40 bg-sky-500/15 text-sky-300 font-mono"
              >
                Validate Budget
              </button>
            </div>
          </div>

          <div className="space-y-2 pt-2 border-t border-slate-800">
            <div className="text-[11px] font-mono text-slate-400">Queueing does not start workers.</div>

            <button
              type="button"
              data-testid="queue-job-btn"
              disabled={pendingAction === 'queue-job' || jobPathInput.trim() === ''}
              onClick={() => onQueueJob(false)}
              className="w-full py-2 px-3 rounded bg-sky-500 hover:bg-sky-400 text-slate-950 font-mono font-bold text-xs flex items-center justify-center gap-2 disabled:bg-slate-800 disabled:text-slate-500"
            >
              <Plus className="w-4 h-4" />
              Queue Job (POST /api/jobs, rerun=false)
            </button>

            <button
              type="button"
              data-testid="rerun-job-btn"
              disabled={pendingAction === 'rerun-job' || jobPathInput.trim() === ''}
              onClick={() => onQueueJob(true)}
              className="w-full py-2 px-3 rounded bg-amber-500/20 hover:bg-amber-500/30 text-amber-300 border border-amber-500/40 font-mono font-semibold text-xs flex items-center justify-center gap-2 disabled:opacity-40"
            >
              <RotateCcw className="w-3.5 h-3.5" />
              Run Again (POST /api/jobs, rerun=true)
            </button>

            <div className="pt-2 border-t border-slate-800 space-y-2">
              <div className="flex items-center justify-between text-xs">
                <span className="font-semibold">Loop workers (1–4):</span>
                <div className="inline-flex rounded border border-slate-700 overflow-hidden font-mono">
                  {[1, 2, 3, 4].map((n) => (
                    <button
                      key={n}
                      type="button"
                      onClick={() => onChangeWorkerCount(n)}
                      className={`px-2.5 py-1 ${workerCountToStart === n ? 'bg-sky-500 text-slate-950 font-bold' : 'bg-slate-900 text-slate-300'}`}
                    >
                      {n}
                    </button>
                  ))}
                </div>
              </div>

              <button
                type="button"
                data-testid="start-workers-btn"
                disabled={pendingAction === 'start-workers'}
                onClick={onStartWorkers}
                className="w-full py-2 px-3 rounded bg-emerald-500 hover:bg-emerald-400 text-slate-950 font-mono font-bold text-xs flex items-center justify-center gap-2"
              >
                <Play className="w-3.5 h-3.5" />
                Start {workerCountToStart} Loop Worker(s) (POST /api/loops/start)
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
