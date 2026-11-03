/**
 * Strict RFC-4122 v4 UUID Generator & Intent-Scoped Idempotency Manager.
 * Guarantees:
 * 1. Every generated key matches /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
 * 2. Never uses non-UUID prefixes like 'idem-'.
 * 3. Reuses the exact same UUID for double-clicks or transport retries of the same intent.
 * 4. Generates a new UUID only when the user creates a distinct new intent (or explicitly clears after completion).
 */

export const UUID_V4_REGEX =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export function generateUuidV4(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  // Deterministic-format RFC-4122 v4 fallback
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === 'x' ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

export function isValidUuidV4(value: string): boolean {
  return typeof value === 'string' && UUID_V4_REGEX.test(value);
}

export class IdempotencyManager {
  private activeKeys: Map<string, string> = new Map();

  constructor(initialSeeds?: Record<string, string>) {
    if (initialSeeds) {
      for (const [k, v] of Object.entries(initialSeeds)) {
        if (isValidUuidV4(v)) {
          this.activeKeys.set(k, v);
        }
      }
    }
  }

  /**
   * Returns a stable RFC-4122 v4 UUID for a given intent signature.
   * Reused across double-clicks and network retries; rotates only when forceRotate=true
   * for a deliberate new action (such as an explicit rerun).
   */
  getOrCreateKey(intentSignature: string, forceRotate = false): string {
    if (!forceRotate && this.activeKeys.has(intentSignature)) {
      return this.activeKeys.get(intentSignature)!;
    }
    const uuid = generateUuidV4();
    this.activeKeys.set(intentSignature, uuid);
    return uuid;
  }

  clearIntent(intentSignature: string): void {
    this.activeKeys.delete(intentSignature);
  }

  snapshot(): Record<string, string> {
    const out: Record<string, string> = {};
    for (const [k, v] of this.activeKeys.entries()) {
      out[k] = v;
    }
    return out;
  }
}

export const generateIdempotencyKey = generateUuidV4;
