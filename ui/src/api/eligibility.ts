/**
 * Which models a stage may use. docs/API_CONTRACT.md section 1: "A model is eligible for a stage only when that stage's contract is
 * `verified`." The UI offers a model for a stage only when this returns true.
 *
 * GET /stages does not yet carry `required_contract` per row (checklist I2), so the contract a stage needs is derived here from the
 * stage's role, as the engine uses it: the planner needs planner_json, a writer stage writer_file_blocks, a repair stage
 * repair_edit_blocks, a reviewer stage reviewer_verdict. When the API sends `required_contract`, use that instead of this table.
 *
 * A contract also needs a recorded verification time: `verified_at` is set by POST /models/verify. Claude Code entries report
 * `verified` for every contract with no probe behind it (checklist I16), so they stay unoffered until a real probe records a time.
 */

import type { ModelRecord, SemanticContract, StageKey } from './types';

export const STAGE_REQUIRED_CONTRACT: Record<StageKey, SemanticContract> = {
  planner: 'planner_json',
  writer_a_write: 'writer_file_blocks',
  writer_b_write: 'writer_file_blocks',
  writer_a_repair: 'repair_edit_blocks',
  writer_b_repair: 'repair_edit_blocks',
  specialist_repair: 'repair_edit_blocks',
  review_fix_repair: 'repair_edit_blocks',
  reviewer_a: 'reviewer_verdict',
  reviewer_b: 'reviewer_verdict',
  reviewer_reask: 'reviewer_verdict',
};

/** True when the model's contract for this stage is verified and a verification time is recorded. */
export function isEligibleForStage(model: ModelRecord, stage: StageKey): boolean {
  const contract = STAGE_REQUIRED_CONTRACT[stage];
  const state = model.capabilities?.contracts?.[contract] ?? null;
  const verifiedAt = typeof model.verified_at === 'string' && model.verified_at.length > 0;
  return state === 'verified' && verifiedAt;
}
