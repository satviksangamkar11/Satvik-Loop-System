import React from 'react';
import { RefreshCw } from 'lucide-react';
import type { DataMode, JobRecord } from '../api/types';

export interface SystemPageProps {
  dataMode: DataMode;
  liveConnected: boolean;
  stopActive: boolean;
  systemHealth: Record<string, any> | null;
  jobs: JobRecord[];
  sandboxCap: number;
  reviewCeilingUsd: string;
  limitsRevision: string | null;
  showRev4Reference: boolean;
  pendingAction: string | null;
  bgPanel: string;
  bgSubtle: string;
  textMuted: string;
  onRefreshHealth: () => void;
  onOpenStopModal: () => void;
  onToggleLimitsRevisionSim: () => void;
  onChangeSandboxCap: (cap: number) => void;
  onChangeReviewCeiling: (val: string) => void;
  onSaveSystemLimits: () => void;
  onToggleRev4Reference: () => void;
}

export const SystemPage: React.FC<SystemPageProps> = ({
  dataMode,
  liveConnected,
  stopActive,
  systemHealth,
  jobs,
  sandboxCap,
  reviewCeilingUsd,
  limitsRevision,
  showRev4Reference,
  pendingAction,
  bgPanel,
  bgSubtle,
  textMuted,
  onRefreshHealth,
  onOpenStopModal,
  onToggleLimitsRevisionSim,
  onChangeSandboxCap,
  onChangeReviewCeiling,
  onSaveSystemLimits,
  onToggleRev4Reference,
}) => {
  const healthStatus =
    dataMode === 'live' && !liveConnected
      ? 'DISCONNECTED (503)'
      : systemHealth?.status
      ? String(systemHealth.status).toUpperCase()
      : 'UNKNOWN';

  const dockerInfo = String(
    systemHealth?.docker_status ??
      (dataMode === 'live' && !liveConnected
        ? 'Unavailable (backend offline)'
        : 'satvik-sandbox:rev4-py312')
  );

  const engineSha = String(
    systemHealth?.engine_main_sha ??
      systemHealth?.engine_sha ??
      (dataMode === 'live' && !liveConnected
        ? 'Unknown (disconnected)'
        : 'b6569d86e2321a8fd544e8d3705a84dbc28b349c')
  );

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="text-lg md:text-xl font-bold tracking-tight">
            7. System Health, Quotas, Leases &amp; Limits (/system)
          </h1>
          <p className={`text-xs ${textMuted}`}>
            Docker &amp; engine SHA telemetry (`GET /api/system/health`), boolean API key alias
            flags (never raw secrets), model concurrency gates (`OWN_SLOT=1`), worktree
            PIDs/heartbeats, and C-07/C-08/C-11 contract-gated controls.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-xs font-mono">
          <button
            type="button"
            onClick={onRefreshHealth}
            className="px-2.5 py-1.5 rounded border border-slate-700 hover:border-sky-400 inline-flex items-center gap-1"
          >
            <RefreshCw className="w-3.5 h-3.5" />
            Refresh Health (`GET /system/health`)
          </button>
          <button
            type="button"
            disabled
            title="Blocked on Gap C-11: Log export route is not defined in docs/API_CONTRACT.md"
            className="px-2.5 py-1.5 rounded bg-slate-800 text-slate-500 border border-slate-700 cursor-not-allowed"
          >
            Export System Logs (Blocked: Gap C-11)
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-3 font-mono text-xs">
        <div className={`p-3.5 rounded-md border space-y-1 ${bgPanel}`}>
          <div className="text-slate-400 text-[11px]">BACKEND &amp; DOCKER HEALTH</div>
          <div
            className={
              dataMode === 'live' && !liveConnected
                ? 'text-rose-400 font-bold'
                : 'text-emerald-400 font-bold'
            }
          >
            {healthStatus}
          </div>
          <div className="text-[11px] text-slate-300 truncate" title={dockerInfo}>
            {dockerInfo}
          </div>
        </div>

        <div className={`p-3.5 rounded-md border space-y-1 ${bgPanel}`}>
          <div className="text-slate-400 text-[11px]">ENGINE BASELINE SHA</div>
          <div className="text-sky-300 font-bold truncate" title={engineSha}>
            {engineSha}
          </div>
          <div className="text-[11px] text-slate-400">
            Handoff: claude/satvik-loop-handoff-2gvtcm (+2)
          </div>
        </div>

        <div className={`p-3.5 rounded-md border space-y-1 ${bgPanel}`}>
          <div className="text-slate-400 text-[11px]">KEY ALIASES (BOOLEAN FLAGS ONLY)</div>
          <div className="space-y-0.5 text-[11px]">
            <div className="flex justify-between">
              <span>OPENROUTER_API_KEY:</span>
              <strong className="text-emerald-400">PRESENT (true)</strong>
            </div>
            <div className="flex justify-between">
              <span>CLAUDE_CODE_CLI_SESSION:</span>
              <strong className="text-emerald-400">PRESENT (true)</strong>
            </div>
            <div className="flex justify-between">
              <span>ANTHROPIC_API_KEY:</span>
              <span className="text-slate-500">ABSENT (false)</span>
            </div>
          </div>
        </div>

        <div className={`p-3.5 rounded-md border space-y-1 ${bgPanel}`}>
          <div className="text-slate-400 text-[11px]">SOFT STOP BOUNDARY</div>
          <div className={stopActive ? 'text-rose-400 font-bold' : 'text-emerald-400 font-bold'}>
            {stopActive ? 'STOP FILE PRESENT (ON)' : 'CLEAR (OFF)'}
          </div>
          <button
            type="button"
            onClick={onOpenStopModal}
            className="text-[11px] text-sky-400 underline"
          >
            Configure Soft STOP →
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <div className={`rounded-md border p-4 space-y-3 ${bgPanel}`}>
          <div className="flex items-center justify-between font-mono text-xs">
            <span className="font-bold uppercase text-sky-400">
              Per-Model Concurrency Gates (Read-Only · Gap C-07)
            </span>
            <span className="text-[11px] text-slate-400">OWN_SLOT=1 Fallback Enforced</span>
          </div>
          <table className="w-full text-left border-collapse text-xs font-mono">
            <thead>
              <tr className="border-b border-slate-800 text-[11px] text-slate-400">
                <th className="py-2 pr-2">Model ID</th>
                <th className="py-2 px-2">Gate Source</th>
                <th className="py-2 px-2">Used / Cap</th>
                <th className="py-2 pl-2">Mutability</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/70">
              <tr>
                <td className="py-2 pr-2 text-sky-300">deepseek/deepseek-chat-v3-0324:free</td>
                <td className="py-2 px-2">roster_configured</td>
                <td className="py-2 px-2">0 / 2</td>
                <td className="py-2 pl-2 text-slate-500">Requires C-07 contract</td>
              </tr>
              <tr>
                <td className="py-2 pr-2 text-amber-300">qwen/qwen-2.5-coder-32b-instruct:free</td>
                <td className="py-2 px-2">OWN_SLOT=1 fallback</td>
                <td className="py-2 px-2 font-bold text-amber-300">1 / 1 (held)</td>
                <td className="py-2 pl-2 text-slate-500">Requires C-07 contract</td>
              </tr>
              <tr>
                <td className="py-2 pr-2 text-sky-300">deepseek/deepseek-r1:free</td>
                <td className="py-2 px-2">roster_configured</td>
                <td className="py-2 px-2">1 / 2</td>
                <td className="py-2 pl-2 text-slate-500">Requires C-07 contract</td>
              </tr>
            </tbody>
          </table>
        </div>

        <div className={`rounded-md border p-4 space-y-3 ${bgPanel}`}>
          <div className="flex items-center justify-between font-mono text-xs">
            <span className="font-bold uppercase text-sky-400">
              Worktree Leases, Heartbeats &amp; Shared Claim Evidence
            </span>
            <span className="text-[11px] text-slate-400">
              Heartbeat: 30s · Orphan: &gt;300s + dead PID
            </span>
          </div>
          <div className="space-y-2 text-xs font-mono">
            {jobs.filter((j) => j.lease_alive).length === 0 ? (
              <div className={`p-3 rounded border ${bgSubtle} text-slate-400`}>
                No active worktree leases currently held.
              </div>
            ) : (
              jobs
                .filter((j) => j.lease_alive)
                .map((j) => (
                  <div key={j.job_id} className={`p-2.5 rounded border ${bgSubtle}`}>
                    <div className="flex justify-between">
                      <span className="font-bold text-sky-300">
                        {j.job_id} ({j.worktree_path})
                      </span>
                      <span className="text-emerald-400">
                        PID {j.lease_pid} (alive) · HB {j.heartbeat_age_sec ?? 'Unknown'}s ago
                      </span>
                    </div>
                    <div className="text-[11px] text-slate-400 mt-1">
                      Claim Conflicts:{' '}
                      {j.shared_claim_conflicts.length === 0
                        ? 'None (disjoint file claims)'
                        : j.shared_claim_conflicts.join(', ')}
                    </div>
                  </div>
                ))
            )}
          </div>
        </div>
      </div>

      <div className={`rounded-md border p-4 space-y-3 ${bgPanel}`}>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h2 className="text-sm font-bold uppercase tracking-wider font-mono text-sky-400">
              System Limits Editor (PUT /api/system/limits) — Gap C-07 &amp; C-08 Transparency
            </h2>
            <p className={`text-xs ${textMuted}`}>
              PUT /api/system/limits only covers `sandbox_slots` and `review_spend_cap_usd`
              (per-model slots &amp; separate paid-stage ceilings are read-only per C-07).
              Moreover, GET /api/system/limits omits `revision` while PUT requires one (C-08).
            </p>
          </div>
          {dataMode === 'mock' && (
            <button
              type="button"
              onClick={onToggleLimitsRevisionSim}
              className="px-2.5 py-1 rounded border border-amber-500/50 bg-amber-500/15 text-amber-300 text-xs font-mono"
            >
              {limitsRevision
                ? 'Clear Mock Limits Revision (Restore C-08 Gate)'
                : 'Simulate Backend Providing Limits Revision (Test PUT)'}
            </button>
          )}
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-3 text-xs font-mono items-end">
          <div>
            <label className="block text-slate-400 mb-1">Sandbox Slots (`sandbox_slots`):</label>
            <input
              type="number"
              disabled={!limitsRevision}
              value={sandboxCap}
              onChange={(e) => onChangeSandboxCap(Number(e.target.value) || 4)}
              className="w-full bg-slate-900 border border-slate-700 rounded px-2.5 py-1.5 disabled:opacity-50"
            />
          </div>
          <div>
            <label className="block text-slate-400 mb-1">
              Review Spend Ceiling USD (`review_spend_cap_usd`):
            </label>
            <input
              type="text"
              disabled={!limitsRevision}
              value={reviewCeilingUsd}
              onChange={(e) => onChangeReviewCeiling(e.target.value)}
              className="w-full bg-slate-900 border border-slate-700 rounded px-2.5 py-1.5 disabled:opacity-50"
            />
          </div>
          <div>
            <button
              type="button"
              disabled={!limitsRevision || pendingAction === 'put-limits'}
              onClick={onSaveSystemLimits}
              className={`w-full py-1.5 px-3 rounded font-bold ${
                limitsRevision
                  ? 'bg-sky-500 text-slate-950 hover:bg-sky-400'
                  : 'bg-slate-800 text-slate-500 cursor-not-allowed border border-slate-700'
              }`}
            >
              {limitsRevision
                ? `Save Limits (rev: ${limitsRevision})`
                : 'Locked: Missing GET revision (Gap C-08)'}
            </button>
          </div>
        </div>
      </div>

      <div className={`rounded-md border p-4 space-y-3 ${bgPanel}`}>
        <div className="flex items-center justify-between">
          <span className="text-xs font-mono font-bold uppercase text-sky-400">
            Architecture Revision 4 &amp; Four Semantic Call Contracts (Read-Only Reference Subview)
          </span>
          <button
            type="button"
            onClick={onToggleRev4Reference}
            className="text-xs font-mono text-sky-400 underline"
          >
            {showRev4Reference ? 'Hide Reference' : 'Expand Reference'}
          </button>
        </div>
        {showRev4Reference && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-xs font-mono">
            <div className={`p-3 rounded border space-y-1 ${bgSubtle}`}>
              <div className="font-bold text-sky-300">1. Planner JSON (`planner_json`)</div>
              <p className="text-slate-300 font-sans">
                Returns `plan_md`, `spec_md`, `job`, `acceptance`, `controls`, `expected_lines`.
                Writes immutable `INTENT.json` in `plan_job.py`. Refused in `test` profile.
              </p>
            </div>
            <div className={`p-3 rounded border space-y-1 ${bgSubtle}`}>
              <div className="font-bold text-sky-300">2. Writer Files (`writer_file_blocks`)</div>
              <p className="text-slate-300 font-sans">
                Native JSON <code>{'{ "files": [{ "path", "content" }] }'}</code> converted by
                backend adapter into validated `FILE:` blocks before sandbox pytest.
              </p>
            </div>
            <div className={`p-3 rounded border space-y-1 ${bgSubtle}`}>
              <div className="font-bold text-sky-300">
                3. Repair Dual Mode (`repair_edit_blocks` / `repair_whole_file`)
              </div>
              <p className="text-slate-300 font-sans">
                Incremental `search_replace` edits with corrective re-ask and `full_file` fallback.
                On red review-fix repair, restores last green candidate (`restored-green`).
              </p>
            </div>
            <div className={`p-3 rounded border space-y-1 ${bgSubtle}`}>
              <div className="font-bold text-sky-300">
                4. Reviewer Structured Verdict (`reviewer_verdict`)
              </div>
              <p className="text-slate-300 font-sans">
                Returns `READY` or `FIX FIRST` with verified `file:line` and `SPEC`/`CHECK`
                citations (`evidence_ok`). Requires dual contributor-disjoint reviewers.
              </p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
