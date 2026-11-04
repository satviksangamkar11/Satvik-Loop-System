import React from 'react';
import { Lock } from 'lucide-react';
import type { Effort, ModelRecord, ProfileName, StageKey, StageSetting } from '../api/types';
import { fmtInt, fmtStr } from '../api/format';
import { isEligibleForStage } from '../api/eligibility';

export interface StageSettingsPageProps {
  profile: ProfileName;
  revision: number | null;
  rows: StageSetting[];
  selectedStageKey: StageKey;
  allModels: ModelRecord[];
  pendingAction: string | null;
  bgPanel: string;
  bgSubtle: string;
  textMuted: string;
  onChangeViewProfile: (prof: ProfileName) => void;
  onReloadStages: () => void;
  onSelectStageKey: (key: StageKey) => void;
  onChangeStageModel: (stageKey: StageKey, modelId: string) => void;
  onChangeStageEffort: (stageKey: StageKey, effort: Effort) => void;
  onChangeStageMaxTokens: (stageKey: StageKey, maxTokens: number | null) => void;
  onValidateStages: () => void;
  onPutStages: () => void;
}

export const StageSettingsPage: React.FC<StageSettingsPageProps> = ({
  profile,
  revision,
  rows,
  selectedStageKey,
  allModels,
  pendingAction,
  bgPanel,
  bgSubtle,
  textMuted,
  onChangeViewProfile,
  onReloadStages,
  onSelectStageKey,
  onChangeStageModel,
  onChangeStageEffort,
  onChangeStageMaxTokens,
  onValidateStages,
  onPutStages,
}) => {
  const st = rows.find((s) => s.stage_key === selectedStageKey) ?? rows[0] ?? null;
  const selectedModel = st?.configured_model_id ? allModels.find((m) => m.model_id === st.configured_model_id) ?? null : null;
  const effortOptions: Effort[] = selectedModel ? selectedModel.capabilities.efforts : [];

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="text-lg md:text-xl font-bold tracking-tight">5. Stage Settings (/stages)</h1>
          <p className={`text-xs ${textMuted}`}>
            Settings for the 10 stage types (GET /api/stages?profile=...). Saving affects future jobs only. Running jobs keep their snapshot.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2 text-xs font-mono">
          <div className="inline-flex rounded border border-slate-700 overflow-hidden">
            {(['test', 'production'] as ProfileName[]).map((p) => (
              <button
                key={p}
                type="button"
                onClick={() => onChangeViewProfile(p)}
                className={`px-3 py-1.5 font-semibold ${
                  profile === p ? (p === 'test' ? 'bg-amber-400 text-slate-950' : 'bg-emerald-400 text-slate-950') : 'bg-slate-900 text-slate-300'
                }`}
              >
                Profile: {p}
              </button>
            ))}
          </div>
          <button type="button" onClick={onReloadStages} className="px-2.5 py-1.5 rounded border border-slate-700 hover:border-sky-400">
            Reload
          </button>
        </div>
      </div>

      <div className="p-2.5 rounded border border-sky-800/60 bg-sky-950/30 text-xs font-mono text-sky-200">
        This view reads and edits the profile&apos;s defaults. The backend&apos;s runtime profile is not in the contract (C-05).
      </div>

      {rows.length === 0 ? (
        <div className={`p-4 rounded-md border text-xs ${bgPanel} ${textMuted}`}>No stage settings loaded for {profile} from GET /api/stages.</div>
      ) : (
        <div className="grid grid-cols-1 xl:grid-cols-12 gap-4 items-start">
          <div className={`xl:col-span-8 rounded-md border p-4 space-y-3 ${bgPanel}`}>
            <div className="flex items-center justify-between font-mono text-xs">
              <span className="font-bold uppercase text-sky-400">
                Stage types ({profile}) · revision: {fmtInt(revision)}
              </span>
              <span className="text-slate-400">Click a row to edit it</span>
            </div>

            <div className="overflow-x-auto">
              <table className="w-full text-left border-collapse text-xs font-mono">
                <thead>
                  <tr className="border-b border-slate-800 text-[11px] text-slate-400">
                    <th className="py-2 pr-2">Stage</th>
                    <th className="py-2 px-2">Model</th>
                    <th className="py-2 px-2">Effort</th>
                    <th className="py-2 px-2">Max output</th>
                    <th className="py-2 pl-2">Selectable</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800/70">
                  {rows.map((row) => {
                    const isSel = selectedStageKey === row.stage_key;
                    return (
                      <tr
                        key={row.stage_key}
                        data-testid={`stage-row-${row.stage_key}`}
                        onClick={() => onSelectStageKey(row.stage_key)}
                        className={`cursor-pointer transition ${isSel ? 'bg-sky-500/15' : 'hover:bg-slate-800/40'}`}
                      >
                        <td className="py-2.5 pr-2 font-bold text-sky-300">{row.stage_key}</td>
                        <td className="py-2.5 px-2">
                          <div className="truncate max-w-[200px]">{fmtStr(row.configured_model_id)}</div>
                          <div className="text-[10px] text-slate-400">{fmtStr(row.source)}</div>
                        </td>
                        <td className="py-2.5 px-2">{fmtStr(row.effort)}</td>
                        <td className="py-2.5 px-2">{fmtInt(row.max_output_tokens)}</td>
                        <td className="py-2.5 pl-2">
                          <span
                            className={`px-1.5 py-0.5 rounded text-[10px] ${
                              row.model_selectable === true ? 'bg-sky-500/20 text-sky-300' : 'bg-slate-800 text-slate-400'
                            }`}
                          >
                            {row.model_selectable === true ? 'selectable' : row.model_selectable === false ? 'locked' : 'Unknown'}
                          </span>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>

          {st && (
            <div className={`xl:col-span-4 rounded-md border p-4 space-y-4 ${bgPanel}`}>
              <div className="border-b border-slate-800 pb-2 font-mono">
                <div className="text-sm font-bold text-sky-300">{st.stage_key}</div>
                <div className="text-xs text-slate-400">via: {fmtStr(st.via)}</div>
              </div>

              <div className={`p-3 rounded border text-xs space-y-1.5 ${bgSubtle}`}>
                <div>
                  <strong className="text-slate-300">Inherits:</strong>{' '}
                  <span className="text-amber-300 font-mono">{fmtStr(st.inherits)}</span>
                </div>
                {st.note && (
                  <div>
                    <strong className="text-slate-300">Note:</strong> <span className="text-slate-400">{st.note}</span>
                  </div>
                )}
              </div>

              <div className="space-y-1.5 text-xs font-mono">
                <label htmlFor="stage-model-select" className="block font-semibold">
                  Model ({st.model_selectable === true ? 'selectable' : 'locked'}):
                </label>
                <select
                  id="stage-model-select"
                  data-testid="stage-model-select"
                  disabled={st.model_selectable !== true}
                  value={st.configured_model_id ?? ''}
                  onChange={(e) => onChangeStageModel(st.stage_key, e.target.value)}
                  className="w-full bg-slate-900 border border-slate-700 rounded px-2.5 py-1.5 disabled:opacity-50"
                >
                  {st.configured_model_id === null && <option value="">{'Locked (no model)'}</option>}
                  {st.configured_model_id !== null && !allModels.some((m) => m.model_id === st.configured_model_id) && (
                    <option value={st.configured_model_id}>{st.configured_model_id}</option>
                  )}
                  {allModels.map((m) => {
                    const eligible = isEligibleForStage(m, st.required_contract);
                    // Only eligible models are offered. The saved value stays listed (disabled) so the select shows what is set.
                    if (!eligible && m.model_id !== st.configured_model_id) return null;
                    return (
                      <option key={m.model_id} value={m.model_id} disabled={!eligible}>
                        {eligible
                          ? m.model_id
                          : `${m.model_id} (not verified for ${st.required_contract ?? 'any contract'})`}
                      </option>
                    );
                  })}
                </select>
                <p className={`text-[11px] ${textMuted}`}>
                  POST /stages/validate checks that the model is eligible for this stage.
                </p>
              </div>

              <div className="grid grid-cols-2 gap-2 text-xs font-mono">
                <div>
                  <label htmlFor="stage-effort-select" className="block text-slate-400 mb-1">
                    Effort:
                  </label>
                  <select
                    id="stage-effort-select"
                    data-testid="stage-effort-select"
                    value={st.effort ?? ''}
                    disabled={st.model_selectable !== true}
                    onChange={(e) => onChangeStageEffort(st.stage_key, e.target.value as Effort)}
                    className="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1.5 disabled:opacity-50"
                  >
                    {st.effort === null && <option value="">Unknown</option>}
                    {effortOptions.map((e) => (
                      <option key={e} value={e}>
                        {e}
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <label htmlFor="stage-max-tokens" className="block text-slate-400 mb-1">
                    Max output tokens:
                  </label>
                  <input
                    id="stage-max-tokens"
                    data-testid="stage-max-tokens"
                    type="number"
                    value={st.max_output_tokens ?? ''}
                    placeholder="not set"
                    onChange={(e) => onChangeStageMaxTokens(st.stage_key, e.target.value === '' ? null : Number(e.target.value))}
                    className="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1.5"
                  />
                  {selectedModel && selectedModel.max_output_tokens !== null && st.max_output_tokens !== null && st.max_output_tokens > selectedModel.max_output_tokens && (
                    <p className="text-[10px] text-rose-300 mt-1">Above this model&apos;s max output ({fmtInt(selectedModel.max_output_tokens)}).</p>
                  )}
                </div>
              </div>

              {st.stage_key === 'review_fix_repair' && (
                <div className="p-2.5 rounded border border-amber-700/60 bg-amber-950/30 text-[11px] space-y-1">
                  <div className="font-mono font-bold text-amber-300 flex items-center gap-1">
                    <Lock className="w-3.5 h-3.5" />
                    Locked: this stage inherits the current writer.
                  </div>
                </div>
              )}

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
                  data-testid="save-stages-btn"
                  disabled={pendingAction === 'put-stages' || revision === null}
                  onClick={onPutStages}
                  className="flex-1 py-1.5 px-3 rounded bg-sky-500 hover:bg-sky-400 text-slate-950 font-bold disabled:bg-slate-800 disabled:text-slate-500"
                >
                  Save for future jobs (PUT /stages)
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
};
