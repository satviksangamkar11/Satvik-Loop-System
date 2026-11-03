import React from 'react';
import { Search, Trash2 } from 'lucide-react';
import type { ModelRecord, SemanticContract } from '../api/types';

export interface ModelsPageProps {
  displayedOpenRouterModels: ModelRecord[];
  filteredOpenRouterModels: ModelRecord[];
  claudeCliModels: ModelRecord[];
  selectedModelId: string | null;
  selectedModelObj: ModelRecord | null;
  modelSearch: string;
  modelBillingFilter: string;
  modelContractFilter: string;
  synthetic20ScaleMode: boolean;
  pendingAction: string | null;
  bgPanel: string;
  bgSubtle: string;
  textMuted: string;
  onToggleSynthetic20Scale: () => void;
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

export const ModelsPage: React.FC<ModelsPageProps> = ({
  displayedOpenRouterModels,
  filteredOpenRouterModels,
  claudeCliModels,
  selectedModelId,
  selectedModelObj,
  modelSearch,
  modelBillingFilter,
  modelContractFilter,
  synthetic20ScaleMode,
  pendingAction,
  bgPanel,
  bgSubtle,
  textMuted,
  onToggleSynthetic20Scale,
  onRefreshModels,
  onOpenAddModelModal,
  onChangeSearch,
  onChangeBillingFilter,
  onChangeContractFilter,
  onSelectModel,
  onOpenProbeModal,
  onRemoveModel,
}) => {
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="text-lg md:text-xl font-bold tracking-tight">4. Models (/models)</h1>
          <p className={`text-xs ${textMuted}`}>
            Curated OpenRouter pool (3 fixture sample records + 20-row synthetic scale test mode
            C-14) and separate Claude Code CLI subscription routes. Never auto-runs paid
            verification probes on mount.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-xs font-mono">
          <button
            type="button"
            onClick={onToggleSynthetic20Scale}
            className={`px-2.5 py-1.5 rounded border font-semibold ${
              synthetic20ScaleMode
                ? 'bg-amber-500 text-slate-950 border-amber-400'
                : 'bg-slate-900 text-amber-300 border-amber-700/60'
            }`}
          >
            {synthetic20ScaleMode
              ? 'Showing 20-Row Scale Test (C-14)'
              : 'Toggle 20-Row Synthetic Scale (C-14)'}
          </button>
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

      {/* Filters Bar */}
      <div className={`rounded-md border p-3 flex flex-wrap items-center gap-3 text-xs ${bgPanel}`}>
        <div className="flex items-center gap-1.5 flex-1 min-w-[200px]">
          <Search className="w-4 h-4 text-slate-400" />
          <input
            type="text"
            aria-label="Search Models"
            value={modelSearch}
            onChange={(e) => onChangeSearch(e.target.value)}
            placeholder="Filter by exact model ID or provider..."
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
            <option value="free">Free ($0.00)</option>
            <option value="paid">Paid</option>
          </select>
        </label>
        <label className="flex items-center gap-1.5 font-mono">
          <span className="text-slate-400">Verified Contract:</span>
          <select
            value={modelContractFilter}
            onChange={(e) => onChangeContractFilter(e.target.value)}
            className="bg-slate-900 border border-slate-700 rounded px-2 py-1"
          >
            <option value="all">Any Contract State</option>
            <option value="planner_json">planner_json == verified</option>
            <option value="writer_file_blocks">writer_file_blocks == verified</option>
            <option value="repair_edit_blocks">repair_edit_blocks == verified</option>
            <option value="reviewer_verdict">reviewer_verdict == verified</option>
          </select>
        </label>
        <span className="font-mono text-[11px] text-slate-400">
          Pool Count: <strong>{displayedOpenRouterModels.length}</strong> (Fixture sample: 3;
          Target policy: 10–20)
        </span>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-12 gap-4 items-start">
        {/* Left 7 cols: OpenRouter Pool + Separate Claude Code CLI Section */}
        <div className="xl:col-span-7 space-y-4">
          <div className={`rounded-md border p-4 space-y-3 ${bgPanel}`}>
            <h2 className="text-sm font-bold uppercase tracking-wider font-mono text-sky-400">
              Section A — Curated OpenRouter Pool ({filteredOpenRouterModels.length} shown)
            </h2>
            <div className="overflow-x-auto max-h-[460px] overflow-y-auto">
              <table className="w-full text-left border-collapse text-xs font-mono">
                <thead>
                  <tr className="border-b border-slate-800 text-[11px] text-slate-400">
                    <th className="py-2 pr-2">Exact Model ID &amp; Endpoint</th>
                    <th className="py-2 px-2">USD / 1M (In / Out)</th>
                    <th className="py-2 px-2">Ctx / Max Out</th>
                    <th className="py-2 px-2">Reasoning</th>
                    <th className="py-2 pl-2">Contracts</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800/70">
                  {filteredOpenRouterModels.map((m) => {
                    const isSel = selectedModelId === m.model_id;
                    return (
                      <tr
                        key={m.model_id}
                        onClick={() => onSelectModel(m.model_id)}
                        className={`cursor-pointer transition ${
                          isSel ? 'bg-sky-500/15' : 'hover:bg-slate-800/40'
                        }`}
                      >
                        <td className="py-2.5 pr-2">
                          <div className="font-bold text-sky-300">{m.model_id}</div>
                          <div className="text-[10px] text-slate-400">
                            {m.active_endpoint} · {m.freshness}
                          </div>
                        </td>
                        <td className="py-2.5 px-2">
                          <div>
                            In: {m.input_usd_per_1m !== null ? `$${m.input_usd_per_1m}` : 'Unknown'}
                          </div>
                          <div>
                            Out:{' '}
                            {m.output_usd_per_1m !== null ? `$${m.output_usd_per_1m}` : 'Unknown'}
                          </div>
                        </td>
                        <td className="py-2.5 px-2">
                          <div>{(m.context_window / 1024).toFixed(0)}k ctx</div>
                          <div className="text-[10px] text-slate-400">
                            max: {m.max_output_tokens.toLocaleString()}
                          </div>
                        </td>
                        <td className="py-2.5 px-2">
                          <span
                            className={`px-1.5 py-0.5 rounded text-[10px] ${
                              m.reasoning.supported === 'mandatory'
                                ? 'bg-purple-500/20 text-purple-300'
                                : m.reasoning.supported === 'optional'
                                ? 'bg-sky-500/20 text-sky-300'
                                : 'bg-slate-800 text-slate-400'
                            }`}
                          >
                            {m.reasoning.supported}
                          </span>
                        </td>
                        <td className="py-2.5 pl-2">
                          <div className="flex flex-wrap gap-1">
                            {(
                              ['writer_file_blocks', 'repair_edit_blocks', 'reviewer_verdict'] as const
                            ).map((ck) => (
                              <span
                                key={ck}
                                className={`px-1 py-0.5 rounded text-[9px] ${
                                  m.contracts[ck] === 'verified'
                                    ? 'bg-emerald-500/20 text-emerald-300'
                                    : m.contracts[ck] === 'failed'
                                    ? 'bg-rose-500/20 text-rose-300'
                                    : 'bg-amber-500/20 text-amber-300'
                                }`}
                              >
                                {ck.split('_')[0]}:{m.contracts[ck]}
                              </span>
                            ))}
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>

          <div className={`rounded-md border p-4 space-y-3 ${bgPanel}`}>
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-bold uppercase tracking-wider font-mono text-purple-400">
                Section B — Claude Code CLI Routes (`text_validated` · Subscription Billing)
              </h2>
              <span className="text-[11px] font-mono text-slate-400">
                USD = null (Displays &quot;Subscription&quot;, never $0.00)
              </span>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-left border-collapse text-xs font-mono">
                <thead>
                  <tr className="border-b border-slate-800 text-[11px] text-slate-400">
                    <th className="py-2 pr-2">CLI Route ID</th>
                    <th className="py-2 px-2">Billing</th>
                    <th className="py-2 px-2">Validation Path</th>
                    <th className="py-2 pl-2">Profile &amp; Owner Gate</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800/70">
                  {claudeCliModels.map((cm) => (
                    <tr
                      key={cm.model_id}
                      onClick={() => onSelectModel(cm.model_id)}
                      className={`cursor-pointer ${
                        selectedModelId === cm.model_id
                          ? 'bg-purple-500/15'
                          : 'hover:bg-slate-800/40'
                      }`}
                    >
                      <td className="py-2.5 pr-2 font-bold text-purple-300">{cm.model_id}</td>
                      <td className="py-2.5 px-2">
                        <span className="px-2 py-0.5 rounded bg-purple-500/20 text-purple-200 border border-purple-500/40">
                          Subscription
                        </span>
                      </td>
                      <td className="py-2.5 px-2 text-sky-300">{cm.validation_path}</td>
                      <td className="py-2.5 pl-2 font-sans text-[11px] text-slate-300">
                        {cm.profile_restriction}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>

        {/* Right 5 cols: Wide Model Details & Verification Drawer */}
        <div className={`xl:col-span-5 rounded-md border p-4 space-y-4 ${bgPanel}`}>
          {selectedModelObj ? (
            <>
              <div className="flex items-start justify-between border-b border-slate-800 pb-3">
                <div className="font-mono">
                  <div className="text-sm font-bold text-sky-300">
                    {selectedModelObj.display_name}
                  </div>
                  <div className="text-xs text-slate-400">{selectedModelObj.model_id}</div>
                </div>
                <span className="px-2 py-0.5 rounded text-xs font-mono bg-slate-800 text-slate-200">
                  {selectedModelObj.billing_type === 'subscription'
                    ? 'Subscription'
                    : selectedModelObj.billing_type.toUpperCase()}
                </span>
              </div>

              <div className={`p-3 rounded border text-xs font-mono space-y-1.5 ${bgSubtle}`}>
                <div>
                  <span className="text-slate-400">Active Endpoint:</span>{' '}
                  <strong>{selectedModelObj.active_endpoint}</strong>
                </div>
                <div>
                  <span className="text-slate-400">Input / Output per 1M:</span>{' '}
                  <strong>
                    {selectedModelObj.billing_type === 'subscription'
                      ? 'Subscription (No API USD)'
                      : `${
                          selectedModelObj.input_usd_per_1m !== null
                            ? '$' + selectedModelObj.input_usd_per_1m
                            : 'Unknown'
                        } / ${
                          selectedModelObj.output_usd_per_1m !== null
                            ? '$' + selectedModelObj.output_usd_per_1m
                            : 'Unknown'
                        }`}
                  </strong>
                </div>
                <div>
                  <span className="text-slate-400">Provider Catalog `structured_outputs`:</span>{' '}
                  <span>
                    {String(selectedModelObj.structured_outputs_claimed)} (Catalog claim != stage
                    contract proof)
                  </span>
                </div>
                <div>
                  <span className="text-slate-400">Reasoning Mode:</span>{' '}
                  <strong className="text-sky-300">{selectedModelObj.reasoning.supported}</strong> |
                  Off Admissible: <strong>{String(selectedModelObj.reasoning.off_admissible)}</strong>{' '}
                  | Efforts: <strong>{selectedModelObj.reasoning.efforts.join(', ')}</strong>
                </div>
                <div>
                  <span className="text-slate-400">Reasoning Token Budget:</span>{' '}
                  {selectedModelObj.reasoning.token_budget_supported
                    ? `${selectedModelObj.reasoning.token_budget_min} – ${selectedModelObj.reasoning.token_budget_max} tokens`
                    : 'Not supported'}
                </div>
                <div>
                  <span className="text-slate-400">C-10 Encoded DELETE Path:</span>{' '}
                  <code className="text-[11px] text-amber-300 break-all">
                    /api/models/{encodeURIComponent(selectedModelObj.model_id)}
                  </code>
                </div>
              </div>

              <div className="space-y-2">
                <div className="text-xs font-mono font-bold uppercase text-sky-400">
                  Per-Contract Verification Matrix (`unverified != unsupported`)
                </div>
                <div className="space-y-1.5 text-xs font-mono">
                  {(Object.keys(selectedModelObj.contracts) as SemanticContract[]).map((ck) => {
                    const st = selectedModelObj.contracts[ck];
                    return (
                      <div
                        key={ck}
                        className="p-2 rounded bg-slate-900 border border-slate-800 flex items-center justify-between gap-2"
                      >
                        <span>{ck}</span>
                        <div className="flex items-center gap-2">
                          <span
                            className={`px-2 py-0.5 rounded text-[10px] uppercase ${
                              st === 'verified'
                                ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/30'
                                : st === 'failed'
                                ? 'bg-rose-500/20 text-rose-300 border border-rose-500/30'
                                : st === 'unverified'
                                ? 'bg-amber-500/20 text-amber-300 border border-amber-500/30'
                                : 'bg-slate-800 text-slate-400'
                            }`}
                          >
                            {st}
                          </span>
                          {selectedModelObj.route === 'openrouter' && (
                            <button
                              type="button"
                              onClick={() =>
                                onOpenProbeModal({
                                  modelId: selectedModelObj.model_id,
                                  probeType: 'verify_contract',
                                  contract: ck,
                                })
                              }
                              className="px-2 py-0.5 rounded bg-sky-500/20 hover:bg-sky-500/30 text-sky-300 text-[10px]"
                            >
                              Probe Contract
                            </button>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>

              <div className="flex flex-wrap gap-2 pt-2 border-t border-slate-800 text-xs font-mono">
                <button
                  type="button"
                  onClick={() =>
                    onOpenProbeModal({
                      modelId: selectedModelObj.model_id,
                      probeType: 'verify_reasoning',
                    })
                  }
                  className="px-3 py-1.5 rounded bg-sky-500/20 hover:bg-sky-500/30 text-sky-300 border border-sky-500/40"
                >
                  Verify Reasoning (POST /models/verify-reasoning)
                </button>

                {selectedModelObj.route === 'openrouter' && (
                  <button
                    type="button"
                    disabled={pendingAction === 'delete-model'}
                    onClick={() => onRemoveModel(selectedModelObj)}
                    className="px-3 py-1.5 rounded bg-rose-950/80 hover:bg-rose-900 text-rose-200 border border-rose-700 inline-flex items-center gap-1"
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                    Remove from Pool (DELETE)
                  </button>
                )}
              </div>
            </>
          ) : null}
        </div>
      </div>
    </div>
  );
};
