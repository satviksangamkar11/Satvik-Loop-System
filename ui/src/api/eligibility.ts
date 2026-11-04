/**
 * Which models a stage may use. docs/API_CONTRACT.md section 1: "A model is eligible for a stage only when that stage's contract is
 * `verified`." The UI offers a model for a stage only when this returns true.
 *
 * The contract a stage needs comes from the API: GET /stages sends `required_contract` on every row. When a row has none, no model
 * is offered for it (fail closed); nothing is inferred from the stage name.
 *
 * A contract also needs a recorded verification time: `verified_at` is set by POST /models/verify. This is a second check on top of
 * the API's `verified` state, so a contract reported without a probe behind it is never offered.
 */

import type { ModelRecord, SemanticContract } from './types';

/** True when the model's contract `requiredContract` is verified and a verification time is recorded. */
export function isEligibleForStage(model: ModelRecord, requiredContract: SemanticContract | null): boolean {
  if (requiredContract === null) return false;
  const state = model.capabilities?.contracts?.[requiredContract] ?? null;
  const verifiedAt = typeof model.verified_at === 'string' && model.verified_at.length > 0;
  return state === 'verified' && verifiedAt;
}
