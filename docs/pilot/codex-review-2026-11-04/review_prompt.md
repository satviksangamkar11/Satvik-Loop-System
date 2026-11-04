You are auditing the Satvik Loop repository read-only. Do not modify files. Do not make network or model calls.

Read docs/ARCHITECTURE_REVISION_4.md (including 'As integrated'), docs/API_CONTRACT.md, docs/CLOSURE_CHECKLIST.md, docs/UI_ENGINE_HANDOFF.md, config/roster.json, config/models.json, and the code in scripts/source/, monitor/ and ui/src/. Tests are in pieces/ and ui/tests/.

Audit the full architecture end to end:
1. Gaps between the docs or contract and the code: anything claimed but not implemented, implemented differently, or untested.
2. Correctness and safety: the review ladder (two independent READYs, the FIX FIRST rule, never discarding code that passed every test), locked acceptance tests, the Docker sandbox isolation, the merge queue, leases, claims and fencing, the shared ledger (15/min pace, daily cap), the paid-call guards, stage snapshots and apply_future_stage, events/history.db as a rebuildable index, idempotency, single-worker assumptions, and secret handling (no key in logs or evidence).
3. Pressure test by reasoning: concurrent loops, crash mid-merge, a stale lease, a duplicate queue, a 429 storm, a corrupt index, a model that never gives a verdict, clock skew, Windows file locks, a full disk. For each, cite the code path that handles it or say it is unhandled.
4. Production readiness: what would break on the first paid production run.

Report: a table of findings (severity critical/high/medium/low | file:line | what is wrong | concrete failure scenario | suggested fix), then the top 5 risks, then what is solid. Cite file:line for every claim. Say 'not verified' instead of guessing.
