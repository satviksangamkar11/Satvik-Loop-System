import React from 'react';
import { AlertTriangle, Octagon, X } from 'lucide-react';
import type { ModelRecord, SemanticContract } from '../api/types';
import { fmtModelPrice } from '../api/format';

export interface StopModalProps {
  open: boolean;
  stopActive: boolean;
  pending: boolean;
  bgPanel: string;
  onClose: () => void;
  onConfirm: (nextStopOn: boolean) => void;
}

export const StopConfirmModal: React.FC<StopModalProps> = ({
  open,
  stopActive,
  pending,
  bgPanel,
  onClose,
  onConfirm,
}) => {
  if (!open) return null;
  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="stop-modal-title"
      className="fixed inset-0 z-50 bg-black/75 flex items-center justify-center p-4"
    >
      <div className={`max-w-md w-full rounded-md border p-5 space-y-4 ${bgPanel}`}>
        <div className="flex items-center justify-between">
          <h2
            id="stop-modal-title"
            className="text-base font-bold font-mono text-rose-400 flex items-center gap-2"
          >
            <Octagon className="w-5 h-5" />
            {stopActive
              ? 'Clear Global Soft STOP?'
              : 'Confirm Global Soft STOP (POST /api/system/stop)'}
          </h2>
          <button
            type="button"
            onClick={onClose}
            className="text-slate-400 hover:text-white"
            aria-label="Close STOP Modal"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
        <p className="text-xs text-slate-300 leading-relaxed">
          <strong>Soft STOP Semantics:</strong> Global STOP sets a soft boundary (`STOP` file). It
          prevents workers from claiming queued jobs or starting new stage attempts, but{' '}
          <strong>any model call already in flight will finish naturally</strong>{' '}
          and is never forcibly killed or labelled cancelled.
        </p>
        <div className="flex justify-end gap-2 font-mono text-xs">
          <button
            type="button"
            onClick={onClose}
            className="px-3 py-1.5 rounded border border-slate-700"
          >
            Cancel
          </button>
          <button
            type="button"
            disabled={pending}
            onClick={() => onConfirm(!stopActive)}
            className="px-3 py-1.5 rounded bg-rose-600 hover:bg-rose-500 text-white font-bold disabled:opacity-50"
          >
            {pending
              ? 'Sending POST /api/system/stop...'
              : stopActive
              ? 'Resume Normal Queueing (on: false)'
              : 'Enable Soft STOP (on: true)'}
          </button>
        </div>
      </div>
    </div>
  );
};

export interface ProbeModalState {
  modelId: string;
  probeType: 'verify_contract' | 'verify_reasoning';
  contract?: SemanticContract;
  ackChecked: boolean;
}

export interface ProbeConfirmModalProps {
  modal: ProbeModalState | null;
  pending: boolean;
  bgPanel: string;
  onClose: () => void;
  onToggleAck: (checked: boolean) => void;
  onConfirm: () => void;
}

export const ProbeConfirmModal: React.FC<ProbeConfirmModalProps> = ({
  modal,
  pending,
  bgPanel,
  onClose,
  onToggleAck,
  onConfirm,
}) => {
  if (!modal) return null;
  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="probe-modal-title"
      className="fixed inset-0 z-50 bg-black/75 flex items-center justify-center p-4"
    >
      <div className={`max-w-lg w-full rounded-md border p-5 space-y-4 ${bgPanel}`}>
        <div className="flex items-center justify-between">
          <h2 id="probe-modal-title" className="text-sm font-bold font-mono text-amber-400">
            Explicit Model Verification Probe Confirmation
          </h2>
          <button
            type="button"
            onClick={onClose}
            className="text-slate-400 hover:text-white"
            aria-label="Close Probe Modal"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="text-xs space-y-2 text-slate-300 font-mono">
          <div>
            Target Model: <code>{modal.modelId}</code>
          </div>
          <div>
            Action:{' '}
            <code>
              {modal.probeType === 'verify_contract'
                ? `POST /api/models/verify (contract: ${modal.contract})`
                : 'POST /api/models/verify-reasoning (every listed effort except off)'}
            </code>
          </div>
          <div className="p-2.5 rounded bg-amber-950/50 border border-amber-700 text-amber-200 font-sans">
            <strong>Quota &amp; Cost Disclosure:</strong> This probe dispatches{' '}
            {modal.probeType === 'verify_contract' ? '1 live request' : 'one request per listed effort'} against
            the provider endpoint, consuming daily free quota or incurred token spend. Default
            selection is <strong>NO / Cancel</strong>.
          </div>
        </div>

        <label className="flex items-center gap-2 text-xs cursor-pointer">
          <input
            type="checkbox"
            checked={modal.ackChecked}
            onChange={(e) => onToggleAck(e.target.checked)}
          />
          <span>I explicitly authorize dispatching this verification probe request.</span>
        </label>

        <div className="flex justify-end gap-2 font-mono text-xs">
          <button
            type="button"
            onClick={onClose}
            className="px-3 py-1.5 rounded bg-slate-800 hover:bg-slate-700 text-white font-bold border border-slate-600"
          >
            NO / Cancel (Default)
          </button>
          <button
            type="button"
            disabled={!modal.ackChecked || pending}
            onClick={onConfirm}
            className={`px-3 py-1.5 rounded font-bold ${
              modal.ackChecked && !pending
                ? 'bg-amber-400 text-slate-950 hover:bg-amber-300'
                : 'bg-slate-900 text-slate-600 cursor-not-allowed'
            }`}
          >
            {pending ? 'Dispatching Probe...' : 'Confirm & Dispatch Probe'}
          </button>
        </div>
      </div>
    </div>
  );
};

export interface AddModelModalProps {
  open: boolean;
  query: string;
  results: ModelRecord[];
  pending: boolean;
  bgPanel: string;
  bgSubtle: string;
  onClose: () => void;
  onChangeQuery: (q: string) => void;
  onSearch: () => void;
  onAddModel: (modelId: string) => void;
}

export const AddModelCatalogModal: React.FC<AddModelModalProps> = ({
  open,
  query,
  results,
  pending,
  bgPanel,
  bgSubtle,
  onClose,
  onChangeQuery,
  onSearch,
  onAddModel,
}) => {
  if (!open) return null;
  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="add-model-modal-title"
      className="fixed inset-0 z-50 bg-black/75 flex items-center justify-center p-4"
    >
      <div className={`max-w-2xl w-full rounded-md border p-5 space-y-4 ${bgPanel}`}>
        <div className="flex items-center justify-between">
          <h2 id="add-model-modal-title" className="text-sm font-bold font-mono text-sky-400">
            Add Model from Broader OpenRouter Catalog (GET /api/models/search?q=)
          </h2>
          <button
            type="button"
            onClick={onClose}
            className="text-slate-400 hover:text-white"
            aria-label="Close Add Model Modal"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="flex gap-2 text-xs font-mono">
          <input
            type="text"
            value={query}
            onChange={(e) => onChangeQuery(e.target.value)}
            placeholder="Search OpenRouter catalog (e.g. llama, codestral)..."
            className="flex-1 bg-slate-900 border border-slate-700 rounded px-3 py-1.5"
          />
          <button
            type="button"
            disabled={pending}
            onClick={onSearch}
            className="px-3 py-1.5 rounded bg-sky-500 text-slate-950 font-bold"
          >
            Search Catalog (GET /models/search)
          </button>
        </div>

        <div className="space-y-2 max-h-64 overflow-y-auto text-xs font-mono">
          {results.length === 0 ? (
            <div className="p-4 text-center text-slate-400">
              Click &quot;Search Catalog&quot; to query matching OpenRouter entries via GET
              /api/models/search. Catalog entries enter the pool with `unverified` stage contracts
              until explicitly probed.
            </div>
          ) : (
            results.map((cand) => (
              <div
                key={cand.model_id}
                className={`p-3 rounded border flex flex-wrap items-center justify-between gap-2 ${bgSubtle}`}
              >
                <div>
                  <div className="font-bold text-sky-300">{cand.model_id}</div>
                  <div className="text-[11px] text-slate-400">
                    via: {cand.via ?? 'Unknown'} · Price: {fmtModelPrice(cand)} · Contracts: not verified until probed
                  </div>
                </div>
                <button
                  type="button"
                  disabled={pending}
                  onClick={() => onAddModel(cand.model_id)}
                  className="px-2.5 py-1 rounded bg-emerald-500 text-slate-950 font-bold"
                >
                  + Add to Pool (POST /models)
                </button>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
};

export interface ConflictModalState {
  clientRevision: string;
  serverRevision: string;
  context: 'global_stages' | 'future_job_stage' | 'system_limits';
}

export interface ConflictResolverModalProps {
  modal: ConflictModalState | null;
  bgPanel: string;
  onClose: () => void;
  onReloadServerState: () => void;
}

export const RevisionConflictModal: React.FC<ConflictResolverModalProps> = ({
  modal,
  bgPanel,
  onClose,
  onReloadServerState,
}) => {
  if (!modal) return null;
  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="conflict-modal-title"
      className="fixed inset-0 z-50 bg-black/75 flex items-center justify-center p-4"
    >
      <div className={`max-w-lg w-full rounded-md border border-rose-600 p-5 space-y-4 ${bgPanel}`}>
        <div className="flex items-center justify-between">
          <h2
            id="conflict-modal-title"
            className="text-sm font-bold font-mono text-rose-400 flex items-center gap-2"
          >
            <AlertTriangle className="w-4 h-4" />
            HTTP 409 Conflict — Stale Configuration Revision (stale-revision)
          </h2>
          <button
            type="button"
            onClick={onClose}
            className="text-slate-400 hover:text-white"
            aria-label="Close Conflict Modal"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
        <div className="text-xs font-mono space-y-2 text-slate-300">
          <div>
            Client Submitted Revision:{' '}
            <code className="text-rose-300">{modal.clientRevision}</code>
          </div>
          <div>
            Authoritative Server Revision:{' '}
            <code className="text-emerald-300">{modal.serverRevision}</code>
          </div>
          <p className="font-sans text-slate-300">
            Another operator or process updated the revision on the server. In accordance with
            safety rules, the UI never blindly overwrites newer server state. Reload the
            authoritative server state via GET to reconcile your changes.
          </p>
        </div>
        <div className="flex justify-end gap-2 font-mono text-xs">
          <button
            type="button"
            onClick={onClose}
            className="px-3 py-1.5 rounded border border-slate-700"
          >
            Keep Local Draft (Do Not Overwrite)
          </button>
          <button
            type="button"
            onClick={onReloadServerState}
            className="px-3 py-1.5 rounded bg-sky-500 text-slate-950 font-bold"
          >
            Fetch &amp; Reload Server State
          </button>
        </div>
      </div>
    </div>
  );
};
