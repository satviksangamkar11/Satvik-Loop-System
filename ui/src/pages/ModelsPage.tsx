import React from 'react';
import { Search, Trash2 } from 'lucide-react';
import type { ModelRecord, SemanticContract } from '../api/types';
import { contractClass, contractLabel, fmtInt, fmtModelPrice, fmtStr } from '../api/format';

export interface ModelsPageProps {
  models: ModelRecord[];
  filteredModels: ModelRecord[];
  selectedModelId: string | null;
  selectedModel: ModelRecord | null;
  modelSearch: string;
  modelBillingFilter: string;
  modelContractFilter: string;
  pendingAction: string | null;
  bgPanel: string;
  bgSubtle: string;
  textMuted: string;
  onRefreshModels: () => void;
  onOpenAddModelModal: () => void;
  onChangeSearch: (q: string) => void;
  onChangeBillingFilter: (b: string) => void;
  onChangeContractFilter: (c: string) => void;
  onSelectModel: (modelId: string) => void;
  onOpenProbeModal: (params: {
    modelId: string;
    probeType: 'verify_contract' | 'verify_reasoning';
    contract?: SemanticContract;
  }) => void;
  onRemoveModel: (model: ModelRecord) => void;
}

/** Reasoning switch states the reasoning_mode allows (contract section 1). */
function reasoningSwitches(mode: ModelRecord['capabilities']['reasoning_mode']): string {
  switch (mode) {
    case 'optional':
      return 'on / off';
    case 'mandatory':
      return 'on only (cannot be turned off)';
    case 'unsupported':
      return 'not supported';
    case 'unknown':
      return 'Unknown';
  }
}

export const ModelsPage: React.FC<ModelsPageProps> = ({
  models,
  filteredModels,
  selectedModelId,
  selectedModel,
  modelSearch,
  modelBillingFilter,
  modelContractFilter,
  pendingAction,
  bgPanel,
  bgSubtle,
  textMuted,
  onRefreshModels,
  onOpenAddModelModal,
  onChangeSearch,
  onChangeBillingFilter,
  onChangeContractFilter,
  onSelectModel,
  onOpenProbeModal,
  onRemoveModel,
}) => {
  const openrouterRows = filteredModels.filter((m) => m.via === 'openrouter' || m.via === null);
  const claudeRows = filteredModels.filter((m) => m.via === 'claude_code');

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="text-lg md:text-xl font-bold tracking-tight">4. Models (/models)</h1>
          <p className={`text-xs ${textMuted}`}>
            The model pool from GET /api/models. Probes run only when you confirm them. A contract is verified only after a pass.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-xs font-mono">
          <button
            type="button"
            disabled={pendingAction === 'refresh-models'}
            onClick={onRefreshModels}
            className="px-2.5 py-1.5 rounded border border-slate-700 hover:border-sky-400"
          >
            Refresh Prices (POST /models/refresh)
          </button>
          <button
            type="button"
            onClick={onOpenAddModelModal}
            className="px-3 py-1.5 rounded bg-sky-500 hover:bg-sky-400 text-slate-950 font-bold"
          >
            + Add Model from Catalog
          </button>
        </div>
      </div>

      <div className={`rounded-md border p-3 flex flex-wrap items-center gap-3 text-xs ${bgPanel}`}>
        <div className="flex items-center gap-1.5 flex-1 min-w-[200px]">
          <Search className="w-4 h-4 text-slate-400" />
          <input
            type="text"
            aria-label="Search Models"
            value={modelSearch}
            onChange={(e) => onChangeSearch(e.target.value)}
            placeholder="Filter by model ID"
            className="w-full bg-slate-900 border border-slate-700 rounded px-2.5 py-1 font-mono"
          />
        </div>
        <label className="flex items-center gap-1.5 font-mono">
          <span className="text-slate-400">Billing:</span>
          <select
            value={modelBillingFilter}
            onChange={(e) => onChangeBillingFilter(e.target.value)}
            className="bg-slate-900 border border-slate-700 rounded px-2 py-1"
          >
            <option value="all">All</option>
            <option value="free">Free</option>
            <option value="paid">Paid</option>
            <option value="subscription">Subscription</option>
          </select>
        </label>
        <label className="flex items-center gap-1.5 font-mono">
          <span className="text-slate-400">Verified contract:</span>
          <select
            value={modelContractFilter}
            onChange={(e) => onChangeContractFilter(e.target.value)}
            className="bg-slate-900 border border-slate-700 rounded px-2 py-1"
          >
            <option value="all">Any</option>
            <option value="planner_json">planner_json verified</option>
            <option value="writer_file_blocks">writer_file_blocks verified</option>
            <option value="repair_edit_blocks">repair_edit_blocks verified</option>
            <option value="repair_whole_file">repair_whole_file verified</option>
            <option value="reviewer_verdict">reviewer_verdict verified</option>
            <option value="native_structured_output">native_structured_output verified</option>
          </select>
        </label>
        <span className="font-mono text-[11px] text-slate-400">
          Pool: <strong>{fmtInt(models.length)}</strong>
        </span>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-12 gap-4 items-start">
        <div className="xl:col-span-7 space-y-4">
          <div className={`rounded-md border p-4 space-y-3 ${bgPanel}`}>
            <h2 className="text-sm font-bold uppercase tracking-wider font-mono text-sky-400">
              OpenRouter pool ({openrouterRows.length} shown)
            </h2>
            {models.length === 0 && <p className={`text-xs ${textMuted}`}>No models from GET /api/models yet.</p>}
            <div className="overflow-x-auto max-h-[460px] overflow-y-auto">
              <table className="w-full text-left border-collapse text-xs font-mono">
                <thead>
                  <tr className="border-b border-slate-800 text-[11px] text-slate-400">
                    <th className="py-2 pr-2">Model ID</th>
                    <th className="py-2 px-2">USD per 1M (in / out)</th>
                    <th className="py-2 px-2">Context / max output</th>
                    <th className="py-2 pl-2">Contracts</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800/70">
                  {openrouterRows.map((m) => (
                    <tr
                      key={m.model_id}
                      data-testid={`model-row-${m.model_id}`}
                      onClick={() => onSelectModel(m.model_id)}
                      className={`cursor-pointer transition ${selectedModelId === m.model_id ? 'bg-sky-500/15' : 'hover:bg-slate-800/40'}`}
                    >
                      <td className="py-2.5 pr-2">
                        <div className="font-bold text-sky-300">{m.model_id}</div>
                        <div className="text-[10px] text-slate-400">
                          endpoint: {fmtStr(m.endpoint)} · verified_at: {fmtStr(m.verified_at)}
                        </div>
                      </td>
                      <td className="py-2.5 px-2">{fmtModelPrice(m)}</td>
                      <td className="py-2.5 px-2">
                        <div>ctx {fmtInt(m.context_tokens)}</div>
                        <div className="text-[10px] text-slate-400">max out {fmtInt(m.max_output_tokens)}</div>
                      </td>
                      <td className="py-2.5 pl-2">
                        <div className="flex flex-wrap gap-1">
                          {(['writer_file_blocks', 'repair_edit_blocks', 'reviewer_verdict'] as const).map((ck) => (
                            <span key={ck} className={`px-1 py-0.5 rounded text-[9px] ${contractClass(m.capabilities.contracts[ck])}`}>
                              {ck.split('_')[0]}: {contractLabel(m.capabilities.contracts[ck])}
                            </span>
                          ))}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div className={`rounded-md border p-4 space-y-3 ${bgPanel}`}>
            <h2 className="text-sm font-bold uppercase tracking-wider font-mono text-purple-400">
              Claude Code routes ({claudeRows.length} shown)
            </h2>
            <p className={`text-[11px] font-mono ${textMuted}`}>Subscription: no USD figure. Replies are checked by text (text_validated).</p>
            <div className="overflow-x-auto">
              <table className="w-full text-left border-collapse text-xs font-mono">
                <thead>
                  <tr className="border-b border-slate-800 text-[11px] text-slate-400">
                    <th className="py-2 pr-2">Route</th>
                    <th className="py-2 px-2">Billing</th>
                    <th className="py-2 px-2">Efforts</th>
                    <th className="py-2 pl-2">text_validated</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800/70">
                  {claudeRows.map((cm) => (
                    <tr
                      key={cm.model_id}
                      data-testid={`model-row-${cm.model_id}`}
                      onClick={() => onSelectModel(cm.model_id)}
                      className={`cursor-pointer ${selectedModelId === cm.model_id ? 'bg-purple-500/15' : 'hover:bg-slate-800/40'}`}
                    >
                      <td className="py-2.5 pr-2 font-bold text-purple-300">{cm.model_id}</td>
                      <td className="py-2.5 px-2">
                        <span className="px-2 py-0.5 rounded bg-purple-500/20 text-purple-200 border border-purple-500/40">subscription</span>
                      </td>
                      <td className="py-2.5 px-2">{cm.capabilities.efforts.join(', ') || 'Unknown'}</td>
                      <td className="py-2.5 pl-2">
                        {cm.capabilities.text_validated === null ? 'Unknown' : cm.capabilities.text_validated ? 'yes' : 'no'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>

        <div className={`xl:col-span-5 rounded-md border p-4 space-y-4 ${bgPanel}`}>
          {selectedModel ? (
            <>
              <div className="flex items-start justify-between border-b border-slate-800 pb-3">
                <div className="font-mono">
                  <div className="text-sm font-bold text-sky-300 break-all">{selectedModel.model_id}</div>
                  <div className="text-xs text-slate-400">via {fmtStr(selectedModel.via)}</div>
                </div>
                <span className="px-2 py-0.5 rounded text-xs font-mono bg-slate-800 text-slate-200">
                  {selectedModel.via === 'claude_code' ? 'subscription' : selectedModel.paid === true ? 'paid' : selectedModel.paid === false ? 'free' : 'Unknown'}
                </span>
              </div>

              <div className={`p-3 rounded border text-xs font-mono space-y-1.5 ${bgSubtle}`}>
                <div>
                  <span className="text-slate-400">Price:</span> <strong>{fmtModelPrice(selectedModel)}</strong>
                </div>
                <div>
                  <span className="text-slate-400">Endpoint:</span> <strong>{fmtStr(selectedModel.endpoint)}</strong>
                </div>
                <div>
                  <span className="text-slate-400">Efforts:</span>{' '}
                  <strong>{selectedModel.capabilities.efforts.join(', ') || 'Unknown'}</strong>
                </div>
                <div>
                  <span className="text-slate-400">Reasoning switch:</span>{' '}
                  <strong>{reasoningSwitches(selectedModel.capabilities.reasoning_mode)}</strong>
                </div>
                <div>
                  <span className="text-slate-400">Context / max output:</span>{' '}
                  <strong>
                    {fmtInt(selectedModel.context_tokens)} / {fmtInt(selectedModel.max_output_tokens)}
                  </strong>
                </div>
                {selectedModel.note && (
                  <div>
                    <span className="text-slate-400">Note:</span> {selectedModel.note}
                  </div>
                )}
              </div>

              <div className="space-y-2">
                <div className="text-xs font-mono font-bold uppercase text-sky-400">Contract verification</div>
                <div className="space-y-1.5 text-xs font-mono">
                  {(Object.keys(selectedModel.capabilities.contracts) as SemanticContract[]).map((ck) => {
                    const st = selectedModel.capabilities.contracts[ck];
                    return (
                      <div key={ck} className="p-2 rounded bg-slate-900 border border-slate-800 flex items-center justify-between gap-2">
                        <span>{ck}</span>
                        <div className="flex items-center gap-2">
                          <span className={`px-2 py-0.5 rounded text-[10px] uppercase ${contractClass(st)}`} data-testid={`contract-${ck}`}>
                            {contractLabel(st)}
                          </span>
                          {selectedModel.via === 'openrouter' && (
                            <button
                              type="button"
                              onClick={() => onOpenProbeModal({ modelId: selectedModel.model_id, probeType: 'verify_contract', contract: ck })}
                              className="px-2 py-0.5 rounded bg-sky-500/20 hover:bg-sky-500/30 text-sky-300 text-[10px]"
                            >
                              Verify contract
                            </button>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>

              <div className="flex flex-wrap gap-2 pt-2 border-t border-slate-800 text-xs font-mono">
                {selectedModel.via === 'openrouter' &&
                  (selectedModel.capabilities.reasoning_mode === 'optional' ||
                    selectedModel.capabilities.reasoning_mode === 'mandatory') && (
                    <button
                      type="button"
                      onClick={() => onOpenProbeModal({ modelId: selectedModel.model_id, probeType: 'verify_reasoning' })}
                      className="px-3 py-1.5 rounded bg-sky-500/20 hover:bg-sky-500/30 text-sky-300 border border-sky-500/40"
                    >
                      Test reasoning (POST /models/verify-reasoning)
                    </button>
                  )}

                {selectedModel.via === 'openrouter' && (
                  <button
                    type="button"
                    data-testid={`delete-model-${selectedModel.model_id}`}
                    disabled={pendingAction === 'delete-model'}
                    onClick={() => onRemoveModel(selectedModel)}
                    className="px-3 py-1.5 rounded bg-rose-950/80 hover:bg-rose-900 text-rose-200 border border-rose-700 inline-flex items-center gap-1"
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                    Remove from pool (DELETE)
                  </button>
                )}
              </div>
            </>
          ) : (
            <p className={`text-xs ${textMuted}`}>Select a model to see its contracts.</p>
          )}
        </div>
      </div>
    </div>
  );
};
