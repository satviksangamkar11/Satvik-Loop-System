import React from 'react';
import { Lock } from 'lucide-react';
import type { ModelRecord, ProfileName, StageConfigRow, StageKey } from '../api/types';

export interface StageSettingsPageProps {
  authoritativeProfile: ProfileName;
  stageViewProfile: ProfileName;
  stageRevision: string;
  stages: StageConfigRow[];
  selectedStageKey: StageKey;
  allModels: ModelRecord[];
  pendingAction: string | null;
  bgPanel: string;
  bgSubtle: string;
  textMuted: string;
  onChangeViewProfile: (prof: ProfileName) => void;
  onSimulate409Conflict: () => void;
  onSelectStageKey: (key: StageKey) => void;
  onChangeStageModel: (stageKey: StageKey, modelId: string) => void;
  onChangeStageEffort: (stageKey: StageKey, effort: 'low' | 'medium' | 'high') => void;
  onChangeStageMaxTokens: (stageKey: StageKey, maxTokens: number) => void;
  onValidateStages: () => void;
  onPutStages: () => void;
}

export const StageSettingsPage: React.FC<StageSettingsPageProps> = ({
  authoritativeProfile,
  stageViewProfile,
  stageRevision,
  stages,
  selectedStageKey,
  allModels,
  pendingAction,
  bgPanel,
  bgSubtle,
  textMuted,
  onChangeViewProfile,
  onSimulate409Conflict,
  onSelectStageKey,
  onChangeStageModel,
  onChangeStageEffort,
  onChangeStageMaxTokens,
  onValidateStages,
  onPutStages,
}) => {
  const st = stages.find((s) => s.stage_key === selectedStageKey) || stages[0];

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="text-lg md:text-xl font-bold tracking-tight">
            5. Stage Settings (/stages)
          </h1>
          <p className={`text-xs ${textMuted}`}>
            Global defaults for the 10 stage types (GET /api/stages?profile=...). Affect future
            jobs only; never mutates in-flight stages.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2 text-xs font-mono">
          <div className="inline-flex rounded border border-slate-700 overflow-hidden">
            <button
              type="button"
              onClick={() => onChangeViewProfile('test')}
              className={`px-3 py-1.5 font-semibold ${
                stageViewProfile === 'test'
                  ? 'bg-amber-400 text-slate-950'
                  : 'bg-slate-900 text-slate-300'
              }`}
            >
              View Profile: test
            </button>
            <button
              type="button"
              onClick={() => onChangeViewProfile('production')}
              className={`px-3 py-1.5 font-semibold ${
                stageViewProfile === 'production'
                  ? 'bg-emerald-400 text-slate-950'
                  : 'bg-slate-900 text-slate-300'
              }`}
            >
              View Profile: production
            </button>
          </div>

          <button
            type="button"
            onClick={onSimulate409Conflict}
            className="px-2.5 py-1.5 rounded border border-rose-700 bg-rose-950/50 text-rose-200"
          >
            Simulate 409 Conflict
          </button>
        </div>
      </div>

      <div className="p-2.5 rounded border border-sky-800/60 bg-sky-950/30 text-xs font-mono text-sky-200">
        {`Contract Note (Gap C-05): Toggling the profile selector above calls GET /api/stages?profile=${stageViewProfile} to inspect or edit profile defaults. It does NOT change the backend process's active runtime SATVIK_PROFILE (${authoritativeProfile}).`}
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-12 gap-4 items-start">
        <div className={`xl:col-span-8 rounded-md border p-4 space-y-3 ${bgPanel}`}>
          <div className="flex items-center justify-between font-mono text-xs">
            <span className="font-bold uppercase text-sky-400">
              10 Fixed Stage Types ({stageViewProfile} profile · revision: {stageRevision})
            </span>
            <span className="text-slate-400">Click any row to open Stage Editor</span>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse text-xs font-mono">
              <thead>
                <tr className="border-b border-slate-800 text-[11px] text-slate-400">
                  <th className="py-2 pr-2">Stage Key &amp; Role</th>
                  <th className="py-2 px-2">Model / Inheritance</th>
                  <th className="py-2 px-2">Contract</th>
                  <th className="py-2 px-2">Effort / Reasoning</th>
                  <th className="py-2 px-2">Max Out</th>
                  <th className="py-2 pl-2">Selectable</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/70">
                {stages.map((row) => {
                  const isSel = selectedStageKey === row.stage_key;
                  return (
                    <tr
                      key={row.stage_key}
                      onClick={() => onSelectStageKey(row.stage_key)}
                      className={`cursor-pointer transition ${
                        isSel ? 'bg-sky-500/15' : 'hover:bg-slate-800/40'
                      }`}
                    >
                      <td className="py-2.5 pr-2">
                        <div className="font-bold text-sky-300">{row.stage_key}</div>
                        <div className="text-[10px] text-slate-400">role: {row.logical_role}</div>
                      </td>
                      <td className="py-2.5 px-2">
                        <div className="truncate max-w-[200px]">
                          {row.configured_model_id || 'disabled'}
                        </div>
                        <div className="text-[10px] text-slate-400">{row.profile_source}</div>
                      </td>
                      <td className="py-2.5 px-2">
                        <span className="px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-300 text-[10px]">
                          {row.required_contract}
                        </span>
                      </td>
                      <td className="py-2.5 px-2">
                        <div>effort: {row.effort || 'n/a'}</div>
                        <div className="text-[10px] text-slate-400">
                          reasoning: {row.reasoning_mode}
                        </div>
                      </td>
                      <td className="py-2.5 px-2">
                        <div>{row.max_output_tokens.toLocaleString()}</div>
                        {row.legacy_roster_max_tokens > 32768 && (
                          <div className="text-[10px] text-amber-400">⚠ C-02 (40k)</div>
                        )}
                      </td>
                      <td className="py-2.5 pl-2">
                        <span
                          className={`px-1.5 py-0.5 rounded text-[10px] ${
                            row.model_selectable
                              ? 'bg-sky-500/20 text-sky-300'
                              : 'bg-slate-800 text-slate-400'
                          }`}
                        >
                          {row.model_selectable ? 'selectable' : 'locked/inherited'}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>

        {/* Right 4 cols: Selected Stage Editor & Contract Validator */}
        <div className={`xl:col-span-4 rounded-md border p-4 space-y-4 ${bgPanel}`}>
          {st ? (
            <>
              <div className="border-b border-slate-800 pb-2 font-mono">
                <div className="text-sm font-bold text-sky-300">{st.display_name}</div>
                <div className="text-xs text-slate-400">
                  Key: <code>{st.stage_key}</code> · Logical Role: <code>{st.logical_role}</code>
                </div>
              </div>

              <div className={`p-3 rounded border text-xs space-y-1.5 ${bgSubtle}`}>
                <div>
                  <strong className="text-slate-300">Routing Trigger:</strong>{' '}
                  <span className="text-slate-400">{st.routing_trigger}</span>
                </div>
                <div>
                  <strong className="text-slate-300">Inheritance / Rule:</strong>{' '}
                  <span className="text-amber-300 font-mono">{st.inheritance_rule}</span>
                </div>
                <div>
                  <strong className="text-slate-300">Required Contract:</strong>{' '}
                  <code className="text-emerald-300">{st.required_contract}</code>
                  {st.stage_key === 'reviewer_b' && (
                    <span className="block text-[11px] text-purple-300 mt-0.5">
                      + Must also verify `writer_file_blocks` for runtime `fallback_write`
                      compatibility (C-16).
                    </span>
                  )}
                </div>
              </div>

              <div className="space-y-1.5 text-xs font-mono">
                <label className="block font-semibold">
                  Configured Model (`model_selectable: {String(st.model_selectable)}`):
                </label>
                <select
                  disabled={!st.model_selectable}
                  value={st.configured_model_id || ''}
                  onChange={(e) => onChangeStageModel(st.stage_key, e.target.value)}
                  className="w-full bg-slate-900 border border-slate-700 rounded px-2.5 py-1.5 disabled:opacity-50"
                >
                  {!st.model_selectable && (
                    <option value={st.configured_model_id || ''}>
                      {st.configured_model_id || 'Locked by profile / inheritance'}
                    </option>
                  )}
                  {allModels.map((m) => {
                    const contractOk = m.contracts[st.required_contract] === 'verified';
                    return (
                      <option key={m.model_id} value={m.model_id} disabled={!contractOk}>
                        {m.model_id} ({st.required_contract}: {m.contracts[st.required_contract]})
                      </option>
                    );
                  })}
                </select>
              </div>

              {st.stage_key === 'review_fix_repair' && (
                <div className="p-2.5 rounded border border-amber-700/60 bg-amber-950/30 text-[11px] space-y-1">
                  <div className="font-mono font-bold text-amber-300 flex items-center gap-1">
                    <Lock className="w-3.5 h-3.5" />
                    Protected Reviewer-Disjoint Override (Held on Gap C-06)
                  </div>
                  <p className="text-slate-300">
                    Approved architecture allows overriding `review_fix_repair` with a validated
                    contributor/reviewer-disjoint model, but current docs/API_CONTRACT.md sets
                    `model_selectable: false`. Control is rendered safely and gated until backend
                    P1 exposes eligible override models.
                  </p>
                </div>
              )}

              <div className="grid grid-cols-2 gap-2 text-xs font-mono">
                <div>
                  <label className="block text-slate-400 mb-1">Stage Effort:</label>
                  <select
                    value={st.effort || 'high'}
                    disabled={st.route === 'disabled'}
                    onChange={(e) =>
                      onChangeStageEffort(
                        st.stage_key,
                        e.target.value as 'low' | 'medium' | 'high'
                      )
                    }
                    className="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1.5"
                  >
                    <option value="low">low</option>
                    <option value="medium">medium</option>
                    <option value="high">high</option>
                  </select>
                </div>
                <div>
                  <label className="block text-slate-400 mb-1">Max Output Tokens:</label>
                  <input
                    type="number"
                    value={st.max_output_tokens}
                    onChange={(e) =>
                      onChangeStageMaxTokens(st.stage_key, Number(e.target.value) || 16000)
                    }
                    className="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1.5"
                  />
                </div>
              </div>

              <div className="flex flex-wrap gap-2 pt-3 border-t border-slate-800 text-xs font-mono">
                <button
                  type="button"
                  disabled={pendingAction === 'validate-stages'}
                  onClick={onValidateStages}
                  className="flex-1 py-1.5 px-3 rounded border border-sky-500/40 bg-sky-500/15 text-sky-300 font-semibold"
                >
                  Validate (POST /stages/validate)
                </button>

                <button
                  type="button"
                  disabled={pendingAction === 'put-stages'}
                  onClick={onPutStages}
                  className="flex-1 py-1.5 px-3 rounded bg-sky-500 hover:bg-sky-400 text-slate-950 font-bold"
                >
                  Apply to Future Jobs (PUT /stages)
                </button>
              </div>
            </>
          ) : null}
        </div>
      </div>
    </div>
  );
};
