import React from 'react';
import { Ban, Play, Plus, RotateCcw } from 'lucide-react';
import type { ProfileName, StageConfigRow } from '../api/types';

export interface RunJobsPageProps {
  authoritativeProfile: ProfileName;
  stageRevision: string;
  stages: StageConfigRow[];
  productionPlannerStage: StageConfigRow;
  runEntryMode: 'existing_path' | 'planner_request';
  jobPathInput: string;
  jobValidated: boolean;
  budgetValidated: boolean;
  authorizedPaidBudget: string;
  workerCountToStart: number;
  planJobName: string;
  planPrompt: string;
  planAllowedFiles: string;
  planCostAuthorized: boolean;
  generatedPlan: {
    plan_id: string;
    job_sha256: string;
    known: string[];
    assumed: Array<{ id: string; text: string; checked: boolean }>;
  } | null;
  activeIdempotencyKey: string;
  pendingAction: string | null;
  bgPanel: string;
  bgSubtle: string;
  textMuted: string;
  onChangeEntryMode: (mode: 'existing_path' | 'planner_request') => void;
  onChangeJobPath: (path: string) => void;
  onResetForm: () => void;
  onValidateJobPath: () => void;
  onChangePaidBudget: (val: string) => void;
  onValidateBudget: () => void;
  onQueueJob: (isRerun: boolean) => void;
  onChangeWorkerCount: (n: number) => void;
  onStartWorkers: () => void;
  onChangePlanJobName: (val: string) => void;
  onChangePlanPrompt: (val: string) => void;
  onChangePlanAllowedFiles: (val: string) => void;
  onChangePlanCostAuthorized: (checked: boolean) => void;
  onCreatePlan: () => void;
  onToggleAssumedItem: (id: string, checked: boolean) => void;
  onApprovePlan: () => void;
}

export const RunJobsPage: React.FC<RunJobsPageProps> = ({
  authoritativeProfile,
  stageRevision,
  stages,
  productionPlannerStage,
  runEntryMode,
  jobPathInput,
  jobValidated,
  budgetValidated,
  authorizedPaidBudget,
  workerCountToStart,
  planJobName,
  planPrompt,
  planAllowedFiles,
  planCostAuthorized,
  generatedPlan,
  activeIdempotencyKey,
  pendingAction,
  bgPanel,
  bgSubtle,
  textMuted,
  onChangeEntryMode,
  onChangeJobPath,
  onResetForm,
  onValidateJobPath,
  onChangePaidBudget,
  onValidateBudget,
  onQueueJob,
  onChangeWorkerCount,
  onStartWorkers,
  onChangePlanJobName,
  onChangePlanPrompt,
  onChangePlanAllowedFiles,
  onChangePlanCostAuthorized,
  onCreatePlan,
  onToggleAssumedItem,
  onApprovePlan,
}) => {
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-lg md:text-xl font-bold tracking-tight">2. Run Jobs (/jobs/new)</h1>
        <p className={`text-xs ${textMuted}`}>
          Two distinct entry paths (Existing repo-relative `job_path` vs Production-only NL
          Planner), 10-stage job snapshot configuration, budget validation, and separate Queue vs
          Start 1–4 Loop Workers HTTP mutations.
        </p>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4 items-start">
        {/* Left 2/3: Step 1 (Source/Plan) & Step 2 (10-Stage Table) */}
        <div className="xl:col-span-2 space-y-4">
          <div className={`rounded-md border p-4 space-y-4 ${bgPanel}`}>
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-800 pb-3">
              <span className="text-xs font-mono uppercase text-sky-400 font-bold">
                Step 1 — Select Entry Path (Never Conflated)
              </span>
              <div className="inline-flex rounded border border-slate-700 overflow-hidden text-xs font-mono">
                <button
                  type="button"
                  onClick={() => onChangeEntryMode('existing_path')}
                  className={`px-3 py-1.5 font-semibold ${
                    runEntryMode === 'existing_path'
                      ? 'bg-sky-500 text-slate-950'
                      : 'bg-slate-900 text-slate-300'
                  }`}
                >
                  {'Path A: Existing Repo-Relative Job (jobs/<spec>.json)'}
                </button>
                <button
                  type="button"
                  onClick={() => onChangeEntryMode('planner_request')}
                  className={`px-3 py-1.5 font-semibold ${
                    runEntryMode === 'planner_request'
                      ? 'bg-sky-500 text-slate-950'
                      : 'bg-slate-900 text-slate-300'
                  }`}
                >
                  Path B: Natural-Language Planner Request
                </button>
              </div>
            </div>

            {runEntryMode === 'existing_path' ? (
              <div className="space-y-3 text-xs">
                <div className="p-2.5 rounded border border-slate-800 bg-slate-900/50 text-slate-300 font-mono text-[11px]">
                  Contract Note (Gap C-04): Arbitrary local browser file uploads and server
                  filesystem directory pickers are NOT supported by docs/API_CONTRACT.md. Enter a
                  repo-relative `job_path` and validate via `POST /api/jobs/validate`.
                </div>

                <div className="space-y-1.5">
                  <label htmlFor="job-path-input" className="block font-semibold">
                    Repo-Relative Job JSON Path (`job_path`):
                  </label>
                  <div className="flex flex-wrap gap-2">
                    <input
                      id="job-path-input"
                      type="text"
                      value={jobPathInput}
                      onChange={(e) => onChangeJobPath(e.target.value)}
                      className="flex-1 min-w-[240px] bg-slate-900 border border-slate-700 rounded px-3 py-1.5 font-mono text-xs text-slate-100"
                      placeholder="jobs/tl_parser_strict.json"
                    />
                    <button
                      type="button"
                      disabled={pendingAction === 'validate-job'}
                      onClick={onValidateJobPath}
                      className="px-3 py-1.5 rounded bg-sky-500/20 hover:bg-sky-500/30 text-sky-300 border border-sky-500/40 font-mono font-semibold"
                    >
                      Validate Job (`POST /jobs/validate`)
                    </button>
                    <button
                      type="button"
                      onClick={onResetForm}
                      className="px-3 py-1.5 rounded border border-slate-700 hover:border-slate-500 font-mono"
                    >
                      Reset / New Job Form
                    </button>
                  </div>
                </div>

                <div className="flex flex-wrap items-center gap-1.5 pt-1">
                  <span className={`text-[11px] font-mono ${textMuted}`}>Test Presets:</span>
                  {[
                    { label: 'Active (409 Conflict)', path: 'jobs/tl_parser_strict.json' },
                    {
                      label: 'Completed (Requires rerun:true)',
                      path: 'jobs/auth_token_validator.json',
                    },
                    { label: 'Fresh Valid Spec', path: 'jobs/packet_crc_verifier.json' },
                    { label: 'Invalid Path Traversal (400)', path: '../outside_repo.json' },
                  ].map((p) => (
                    <button
                      key={p.path}
                      type="button"
                      onClick={() => onChangeJobPath(p.path)}
                      className="px-2 py-0.5 rounded border border-slate-700 bg-slate-900 hover:border-sky-400 font-mono text-[11px]"
                    >
                      {p.label}
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              <div className="space-y-3 text-xs">
                {authoritativeProfile === 'test' && (
                  <div className="p-3 rounded border border-rose-600 bg-rose-950/60 text-rose-100 space-y-1">
                    <div className="font-bold font-mono flex items-center gap-1.5">
                      <Ban className="w-4 h-4 text-rose-400" />
                      REFUSED IN TEST PROFILE (no_paid_or_planner_in_test)
                    </div>
                    <p className="text-[11px]">
                      The authoritative backend profile is currently <code>test</code>, which
                      forbids natural-language planning and paid calls. Switch the header Runtime
                      Profile simulator to <code>PRODUCTION</code> to test authorized planner
                      execution, or click below to observe the HTTP 422 refusal from POST
                      /api/plans.
                    </p>
                  </div>
                )}

                <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                  <div>
                    <label className="block font-semibold mb-1">Job Name:</label>
                    <input
                      type="text"
                      value={planJobName}
                      onChange={(e) => onChangePlanJobName(e.target.value)}
                      className="w-full bg-slate-900 border border-slate-700 rounded px-2.5 py-1.5 font-mono"
                    />
                  </div>
                  <div>
                    <label className="block font-semibold mb-1">
                      Configured Planner Stage Model (C-09):
                    </label>
                    <div className="px-2.5 py-1.5 rounded border border-slate-800 bg-slate-900/60 font-mono text-sky-300">
                      {stages[0]?.configured_model_id || 'Disabled in test profile'} (
                      {stages[0]?.route})
                    </div>
                  </div>
                </div>

                <div>
                  <label className="block font-semibold mb-1">
                    Natural-Language Specification Request:
                  </label>
                  <textarea
                    rows={2}
                    value={planPrompt}
                    onChange={(e) => onChangePlanPrompt(e.target.value)}
                    className="w-full bg-slate-900 border border-slate-700 rounded p-2 font-mono text-xs"
                  />
                </div>

                <div>
                  <label className="block font-semibold mb-1">
                    Allowed Repo-Relative Files (1 to 30 max, one per line):
                  </label>
                  <textarea
                    rows={3}
                    value={planAllowedFiles}
                    onChange={(e) => onChangePlanAllowedFiles(e.target.value)}
                    className="w-full bg-slate-900 border border-slate-700 rounded p-2 font-mono text-xs"
                  />
                </div>

                <label className="flex items-center gap-2 p-2.5 rounded border border-amber-700/60 bg-amber-950/30 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={planCostAuthorized}
                    onChange={(e) => onChangePlanCostAuthorized(e.target.checked)}
                  />
                  <span>
                    I explicitly authorize 1 planner call (
                    <code>{productionPlannerStage?.configured_model_id}</code>) to synthesize
                    PLAN/SPEC and write immutable INTENT.json.
                  </span>
                </label>

                <button
                  type="button"
                  disabled={pendingAction === 'create-plan'}
                  onClick={onCreatePlan}
                  className="px-3 py-1.5 rounded bg-sky-500 hover:bg-sky-400 text-slate-950 font-mono font-semibold"
                >
                  Generate Plan (`POST /api/plans`)
                </button>

                {generatedPlan && (
                  <div className="p-3 rounded border border-sky-600/60 bg-sky-950/20 space-y-2.5">
                    <div className="flex items-center justify-between font-mono text-xs">
                      <span className="font-bold text-sky-300">
                        Plan Draft: {generatedPlan.plan_id}
                      </span>
                      <span>
                        job_sha256: <code>{generatedPlan.job_sha256.slice(0, 16)}…</code>
                      </span>
                    </div>
                    <div className="text-[11px] space-y-1">
                      <div className="font-semibold text-emerald-300">KNOWN CONSTRAINTS:</div>
                      <ul className="list-disc list-inside text-slate-300">
                        {generatedPlan.known.map((k, i) => (
                          <li key={i}>{k}</li>
                        ))}
                      </ul>
                    </div>
                    <div className="text-[11px] space-y-1.5">
                      <div className="font-semibold text-amber-300">
                        ASSUMED ITEMS (Must be individually reviewed &amp; ticked; never
                        auto-checked):
                      </div>
                      {generatedPlan.assumed.map((asItem) => (
                        <label
                          key={asItem.id}
                          className="flex items-start gap-2 p-1.5 rounded bg-slate-900/70 border border-slate-800 cursor-pointer"
                        >
                          <input
                            type="checkbox"
                            checked={asItem.checked}
                            onChange={(e) => onToggleAssumedItem(asItem.id, e.target.checked)}
                            className="mt-0.5"
                          />
                          <span>{asItem.text}</span>
                        </label>
                      ))}
                    </div>
                    <button
                      type="button"
                      disabled={
                        !generatedPlan.assumed.every((a) => a.checked) ||
                        pendingAction === 'approve-plan'
                      }
                      onClick={onApprovePlan}
                      className={`px-3 py-1.5 rounded font-mono font-semibold text-xs ${
                        generatedPlan.assumed.every((a) => a.checked)
                          ? 'bg-emerald-500 text-slate-950 hover:bg-emerald-400'
                          : 'bg-slate-800 text-slate-500 cursor-not-allowed'
                      }`}
                    >
                      {'Approve Plan & Start (POST /plans/{id}/approve)'}
                    </button>
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Step 2: 10-Stage Job-Specific Model/Effort Snapshot Table */}
          <div className={`rounded-md border p-4 space-y-3 ${bgPanel}`}>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <h2 className="text-sm font-bold uppercase tracking-wider font-mono text-sky-400">
                  Step 2 — 10-Stage Job-Specific Snapshot Table ({authoritativeProfile} profile)
                </h2>
                <p className={`text-[11px] ${textMuted}`}>
                  Ten configurable stage types (not 10 mandatory sequential steps; `fallback_write`
                  is a runtime event only).
                </p>
              </div>
              <span className="text-xs font-mono text-slate-400">
                Base Revision: <code>{stageRevision}</code>
              </span>
            </div>

            <div className="overflow-x-auto">
              <table className="w-full text-left border-collapse text-xs">
                <thead>
                  <tr className="border-b border-slate-800 text-[11px] font-mono text-slate-400">
                    <th className="py-2 pr-2">Stage Key &amp; Role</th>
                    <th className="py-2 px-2">Configured Model &amp; Route</th>
                    <th className="py-2 px-2">Effort / Reasoning</th>
                    <th className="py-2 px-2">Max Output</th>
                    <th className="py-2 px-2">Contract</th>
                    <th className="py-2 pl-2">Inheritance / Gate</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800/70 font-mono">
                  {stages.map((st) => (
                    <tr key={st.stage_key} className="hover:bg-slate-800/30">
                      <td className="py-2 pr-2">
                        <div className="font-semibold text-sky-300">{st.stage_key}</div>
                        <div className="text-[10px] text-slate-400">role: {st.logical_role}</div>
                      </td>
                      <td className="py-2 px-2">
                        <div className="truncate max-w-[200px]">
                          {st.configured_model_id || 'disabled'}
                        </div>
                        <div className="text-[10px] text-slate-400">{st.route}</div>
                      </td>
                      <td className="py-2 px-2">
                        <div>effort: {st.effort || 'n/a'}</div>
                        <div className="text-[10px] text-slate-400">
                          reasoning: {st.reasoning_mode}
                        </div>
                      </td>
                      <td className="py-2 px-2">
                        <div>{st.max_output_tokens.toLocaleString()}</div>
                        {st.legacy_roster_max_tokens > 32768 && (
                          <span
                            title="Gap C-02: Legacy roster.json has 40,000; capped to 32,000 (model max 32,768)"
                            className="text-[10px] text-amber-400"
                          >
                            ⚠ C-02 (legacy 40k)
                          </span>
                        )}
                      </td>
                      <td className="py-2 px-2">
                        <span
                          className={`px-1.5 py-0.5 rounded text-[10px] ${
                            st.contract_verified
                              ? 'bg-emerald-500/15 text-emerald-300 border border-emerald-500/30'
                              : 'bg-rose-500/15 text-rose-300 border border-rose-500/30'
                          }`}
                        >
                          {st.required_contract}
                        </span>
                      </td>
                      <td className="py-2 pl-2 text-[11px] font-sans text-slate-300">
                        {st.inheritance_rule}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>

        {/* Right 1/3 Sticky Execution & Idempotency Summary */}
        <div className={`rounded-md border p-4 space-y-4 sticky top-16 ${bgPanel}`}>
          <h2 className="text-sm font-bold uppercase tracking-wider font-mono text-sky-400">
            Step 3 — Snapshot, Budget &amp; Execution Controls
          </h2>

          <div className={`p-3 rounded border space-y-2 text-xs font-mono ${bgSubtle}`}>
            <div className="flex justify-between">
              <span className="text-slate-400">Authoritative Profile:</span>
              <strong className="text-amber-300">{authoritativeProfile}</strong>
            </div>
            <div className="flex justify-between">
              <span className="text-slate-400">Stage Revision:</span>
              <span>{stageRevision}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-slate-400">Frozen Snapshot SHA:</span>
              <span title="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855">
                e3b0c44298fc1c14…
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-slate-400">Job Validated:</span>
              <span className={jobValidated ? 'text-emerald-400' : 'text-rose-400'}>
                {jobValidated ? 'YES' : 'NO'}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-slate-400">Budget Validated:</span>
              <span className={budgetValidated ? 'text-emerald-400' : 'text-rose-400'}>
                {budgetValidated ? 'YES' : 'NO'}
              </span>
            </div>
            <div className="flex flex-col gap-0.5 pt-1 border-t border-slate-800">
              <span className="text-slate-400">RFC-4122 UUID Idempotency-Key:</span>
              <code className="text-sky-300 text-[11px] break-all">{activeIdempotencyKey}</code>
            </div>
          </div>

          <div className="space-y-1.5 text-xs">
            <label htmlFor="paid-budget-input" className="block font-semibold">
              Authorized Paid Budget Ceiling (USD):
            </label>
            <div className="flex gap-2">
              <input
                id="paid-budget-input"
                type="text"
                value={authorizedPaidBudget}
                onChange={(e) => onChangePaidBudget(e.target.value)}
                className="flex-1 bg-slate-900 border border-slate-700 rounded px-2.5 py-1.5 font-mono"
              />
              <button
                type="button"
                disabled={pendingAction === 'validate-budget'}
                onClick={onValidateBudget}
                className="px-2.5 py-1.5 rounded border border-sky-500/40 bg-sky-500/15 text-sky-300 font-mono"
              >
                Validate Budget
              </button>
            </div>
          </div>

          <div className="space-y-2 pt-2 border-t border-slate-800">
            <div className="text-[11px] font-mono text-slate-400">
              QUEUEING != STARTING WORKERS (Separate API Mutations)
            </div>

            <button
              type="button"
              disabled={pendingAction === 'queue-job'}
              onClick={() => onQueueJob(false)}
              className="w-full py-2 px-3 rounded bg-sky-500 hover:bg-sky-400 text-slate-950 font-mono font-bold text-xs flex items-center justify-center gap-2"
            >
              <Plus className="w-4 h-4" />
              Queue Job (POST /api/jobs, rerun=false)
            </button>

            <button
              type="button"
              disabled={pendingAction === 'rerun-job'}
              onClick={() => onQueueJob(true)}
              className="w-full py-2 px-3 rounded bg-amber-500/20 hover:bg-amber-500/30 text-amber-300 border border-amber-500/40 font-mono font-semibold text-xs flex items-center justify-center gap-2"
            >
              <RotateCcw className="w-3.5 h-3.5" />
              Run Again for Completed Job (POST /api/jobs, rerun=true)
            </button>

            <div className="pt-2 border-t border-slate-800 space-y-2">
              <div className="flex items-center justify-between text-xs">
                <span className="font-semibold">Loop Workers to Start (1–4):</span>
                <div className="inline-flex rounded border border-slate-700 overflow-hidden font-mono">
                  {[1, 2, 3, 4].map((n) => (
                    <button
                      key={n}
                      type="button"
                      onClick={() => onChangeWorkerCount(n)}
                      className={`px-2.5 py-1 ${
                        workerCountToStart === n
                          ? 'bg-sky-500 text-slate-950 font-bold'
                          : 'bg-slate-900 text-slate-300'
                      }`}
                    >
                      {n}
                    </button>
                  ))}
                </div>
              </div>

              <button
                type="button"
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
