/**
 * Display helpers. Every helper returns "Unknown" (or "subscription" for Claude Code) for null, so no page
 * ever renders a number it was not given.
 */

import type { ContractState, ModelRecord } from './types';

export function fmtInt(v: number | null | undefined, suffix = ''): string {
  if (typeof v !== 'number' || !Number.isFinite(v)) return 'Unknown';
  return `${v.toLocaleString('en-US')}${suffix}`;
}

export function fmtUsd(v: number | null | undefined): string {
  if (typeof v !== 'number' || !Number.isFinite(v)) return 'Unknown';
  return `$${v.toFixed(2)}`;
}

export function fmtStr(v: string | null | undefined): string {
  return typeof v === 'string' && v.length > 0 ? v : 'Unknown';
}

export function fmtBool(v: boolean | null | undefined, yes = 'yes', no = 'no'): string {
  if (typeof v !== 'boolean') return 'Unknown';
  return v ? yes : no;
}

/** Claude Code models are a subscription: never a dollar figure. */
export function fmtModelPrice(m: ModelRecord): string {
  if (m.via === 'claude_code') return 'subscription';
  const i = m.price_in_usd_per_mtok;
  const o = m.price_out_usd_per_mtok;
  if (typeof i !== 'number' || typeof o !== 'number') return 'Unknown';
  return `$${i}/$${o} per Mtok`;
}

/** Label for a contract state. A failed or unverified contract never reads as verified. */
export function contractLabel(state: ContractState): string {
  switch (state) {
    case 'verified':
      return 'verified';
    case 'failed':
      return 'NOT VERIFIED (failed)';
    case 'unverified':
      return 'not verified';
    case 'not-applicable':
      return 'n/a';
  }
}

export function contractClass(state: ContractState): string {
  switch (state) {
    case 'verified':
      return 'text-emerald-300';
    case 'failed':
      return 'text-rose-300';
    case 'unverified':
      return 'text-amber-300';
    case 'not-applicable':
      return 'text-slate-400';
  }
}
