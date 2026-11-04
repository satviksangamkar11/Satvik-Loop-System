import React, { useState } from 'react';
import { RefreshCw } from 'lucide-react';
import type { NormalizedOverview, SystemHealth, SystemLimits } from '../api/types';
import { fmtBool, fmtInt, fmtStr, fmtUsd } from '../api/format';

export interface SystemPageProps {
  liveConnected: boolean;
  stopActive: boolean | null;
  health: SystemHealth | null;
  limits: SystemLimits | null;
  overview: NormalizedOverview | null;
  sandboxInput: string;
  reviewInput: string;
  limitsRevision: number | null;
  pendingAction: string | null;
  bgPanel: string;
  bgSubtle: string;
  textMuted: string;
  onRefreshHealth: () => void;
  onOpenStopModal: () => void;
  onChangeSandboxInput: (v: string) => void;
  onChangeReviewInput: (v: string) => void;
  onSaveSystemLimits: () => void;
}

export const SystemPage: React.FC<SystemPageProps> = ({
  liveConnected,
  stopActive,
  health,
  limits,
  overview,
  sandboxInput,
  reviewInput,
  limitsRevision,
  pendingAction,
  bgPanel,
  bgSubtle,
  textMuted,
  onRefreshHealth,
  onOpenStopModal,
  onChangeSandboxInput,
  onChangeReviewInput,
  onSaveSystemLimits,
}) => {
  const [showReference, setShowReference] = useState<boolean>(false);
  const saveBlocked = limitsRevision === null;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="text-lg md:text-xl font-bold tracking-tight">7. System (/system)</h1>
          <p className={`text-xs ${textMuted}`}>
            Health from GET /api/system/health and the leases, claims, gates and ledger from GET /api/system/limits, shown as the backend sent them.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-xs font-mono">
          <button
            type="button"
            onClick={onRefreshHealth}
            className="px-2.5 py-1.5 rounded border border-slate-700 hover:border-sky-400 inline-flex items-center gap-1"
          >
            <RefreshCw className="w-3.5 h-3.5" />
            Refresh (GET /system/health, GET /system/limits)
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-3 font-mono text-xs">
        <div className={`p-3.5 rounded-md border space-y-1 ${bgPanel}`}>
          <div className="text-slate-400 text-[11px]">BACKEND</div>
          <div className={liveConnected ? 'text-emerald-400 font-bold' : 'text-rose-400 font-bold'}>
            {liveConnected ? fmtStr(health?.backend ?? null).toUpperCase() : 'DISCONNECTED'}
          </div>
          <div className="text-[11px] text-slate-300">docker: {fmtStr(health?.docker ?? null)}</div>
          <div className="text-[11px] text-slate-300">sandbox image: {fmtStr(health?.sandbox_image ?? null)}</div>
        </div>
        <div className={`p-3.5 rounded-md border space-y-1 ${bgPanel}`}>
          <div className="text-slate-400 text-[11px]">ENGINE</div>
          <div className="text-sky-300 font-bold truncate" title={fmtStr(health?.engine_version ?? null)}>
            {fmtStr(health?.engine_version ?? null)}
          </div>
          <div className="text-[11px] text-slate-400 truncate" title={fmtStr(health?.satvik_home ?? null)}>
            home: {fmtStr(health?.satvik_home ?? null)}
          </div>
          <div className="text-[11px] text-slate-400">index_ok: {fmtBool(health?.index_ok ?? null)}</div>
        </div>
        <div className={`p-3.5 rounded-md border space-y-1 ${bgPanel}`}>
          <div className="text-slate-400 text-[11px]">KEYS (PRESENT FLAGS ONLY)</div>
          {(health?.keys ?? []).length === 0 && <div className="text-[11px] text-slate-400">Unknown</div>}
          {(health?.keys ?? []).map((k) => (
            <div key={k.alias} className="flex justify-between text-[11px]">
              <span>{k.alias}:</span>
              <strong className={k.present === true ? 'text-emerald-400' : 'text-slate-400'}>{fmtBool(k.present, 'present', 'absent')}</strong>
            </div>
          ))}
        </div>
        <div className={`p-3.5 rounded-md border space-y-1 ${bgPanel}`}>
          <div className="text-slate-400 text-[11px]">GLOBAL STOP</div>
          <div className={stopActive === true ? 'text-rose-400 font-bold' : stopActive === false ? 'text-emerald-400 font-bold' : 'text-slate-400 font-bold'}>
            {stopActive === null ? 'Unknown' : stopActive ? 'ON' : 'OFF'}
          </div>
          <button type="button" onClick={onOpenStopModal} className="text-[11px] text-sky-400 underline">
            Change soft STOP →
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <div className={`rounded-md border p-4 space-y-3 ${bgPanel}`}>
          <span className="font-bold uppercase text-sky-400 font-mono text-xs">Gates</span>
          {(limits?.gates ?? []).length === 0 ? (
            <div className={`p-3 rounded border ${bgSubtle} text-slate-400 text-xs font-mono`}>No gates reported.</div>
          ) : (
            <table className="w-full text-left border-collapse text-xs font-mono">
              <thead>
                <tr className="border-b border-slate-800 text-[11px] text-slate-400">
                  <th className="py-2 pr-2">Gate</th>
                  <th className="py-2 px-2">Holders</th>
                  <th className="py-2 pl-2">Capacity</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/70">
                {(limits?.gates ?? []).map((g) => (
                  <tr key={g.gate}>
                    <td className="py-2 pr-2 text-sky-300">{g.gate}</td>
                    <td className="py-2 px-2">{fmtInt(g.holders)}</td>
                    <td className="py-2 pl-2">{fmtInt(g.capacity)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        <div className={`rounded-md border p-4 space-y-3 ${bgPanel}`}>
          <span className="font-bold uppercase text-sky-400 font-mono text-xs">Leases and claims</span>
          <div className="space-y-2 text-xs font-mono">
            {(limits?.leases ?? []).length === 0 && <div className={`p-3 rounded border ${bgSubtle} text-slate-400`}>No leases reported.</div>}
            {(limits?.leases ?? []).map((l) => (
              <div key={l.job_id} className={`p-2.5 rounded border ${bgSubtle}`}>
                <div className="font-bold text-sky-300">{l.job_id}</div>
                <div className="text-[11px] text-slate-300">
                  held: {fmtBool(l.held)} · pid alive: {fmtBool(l.pid_alive)} · heartbeat: {fmtInt(l.heartbeat_age_s, ' s')}
                </div>
              </div>
            ))}
            {(limits?.claims ?? []).map((c) => (
              <div key={`${c.job_id}:${c.path}`} className={`p-2 rounded border ${bgSubtle} text-[11px] break-all`}>
                claim: {c.path} ({c.job_id})
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className={`rounded-md border p-4 space-y-3 ${bgPanel}`}>
        <div>
          <h2 className="text-sm font-bold uppercase tracking-wider font-mono text-sky-400">Ledger</h2>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3 text-xs font-mono">
          <div className={`p-2.5 rounded border ${bgSubtle}`}>Free used: {fmtInt(limits?.ledger.free_used ?? null)}</div>
          <div className={`p-2.5 rounded border ${bgSubtle}`}>Free cap: {fmtInt(limits?.ledger.free_cap ?? null)}</div>
          <div className={`p-2.5 rounded border ${bgSubtle}`}>Review spent: {fmtUsd(limits?.ledger.review_spent_usd ?? null)}</div>
        </div>
      </div>

      <div className={`rounded-md border p-4 space-y-3 ${bgPanel}`}>
        <div>
          <h2 className="text-sm font-bold uppercase tracking-wider font-mono text-sky-400">Limits (PUT /api/system/limits)</h2>
          <p className={`text-xs ${textMuted}`}>
            Saving sends the revision this page was loaded at. A stale revision (409) or a value the engine does not apply (422) is shown
            as the backend's message. Blank means the backend has not reported a value.
          </p>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3 text-xs font-mono items-end">
          <div>
            <label htmlFor="sandbox-slots-input" className="block text-slate-400 mb-1">
              Sandbox slots (1–4):
            </label>
            <input
              id="sandbox-slots-input"
              type="number"
              min={1}
              max={4}
              disabled={saveBlocked}
              value={sandboxInput}
              placeholder="Unknown"
              onChange={(e) => onChangeSandboxInput(e.target.value)}
              className="w-full bg-slate-900 border border-slate-700 rounded px-2.5 py-1.5 disabled:opacity-50"
            />
            <div className="text-[10px] text-slate-400 mt-1">Now: {fmtInt(overview?.sandbox_slots.total ?? null)}</div>
          </div>
          <div>
            <label htmlFor="review-cap-input" className="block text-slate-400 mb-1">
              Daily review cap (USD):
            </label>
            <input
              id="review-cap-input"
              type="text"
              disabled={saveBlocked}
              value={reviewInput}
              placeholder="Unknown"
              onChange={(e) => onChangeReviewInput(e.target.value)}
              className="w-full bg-slate-900 border border-slate-700 rounded px-2.5 py-1.5 disabled:opacity-50"
            />
            <div className="text-[10px] text-slate-400 mt-1">Now: {fmtUsd(overview?.review_usd_today.cap ?? null)}</div>
          </div>
          <button
            type="button"
            data-testid="save-limits-btn"
            disabled={saveBlocked || pendingAction === 'put-limits'}
            onClick={onSaveSystemLimits}
            className={`w-full py-1.5 px-3 rounded font-bold ${
              saveBlocked ? 'bg-slate-800 text-slate-500 cursor-not-allowed border border-slate-700' : 'bg-sky-500 text-slate-950 hover:bg-sky-400'
            }`}
          >
            {saveBlocked ? 'Locked: no revision from the backend' : `Save limits (rev ${limitsRevision})`}
          </button>
        </div>
      </div>

      <div className={`rounded-md border p-4 space-y-3 ${bgPanel}`}>
        <div className="flex items-center justify-between">
          <span className="text-xs font-mono font-bold uppercase text-sky-400">Contract reference</span>
          <button type="button" onClick={() => setShowReference((v) => !v)} className="text-xs font-mono text-sky-400 underline">
            {showReference ? 'Hide' : 'Show'}
          </button>
        </div>
        {showReference && (
          <div className="text-xs font-mono text-slate-300 space-y-1">
            <p>Stage contracts: planner_json, writer_file_blocks, repair_edit_blocks, repair_whole_file, reviewer_verdict, native_structured_output.</p>
            <p>A model is eligible for a stage only when that stage&apos;s contract is verified. The Models page shows each contract&apos;s state.</p>
          </div>
        )}
      </div>
    </div>
  );
};
