# Satvik Loop Control Center UI (`ui/`) — Revision 3 Verified

Production-quality React 19 + TypeScript + Vite + Tailwind CSS Control Center UI for the **Satvik Loop System**, aligned strictly with `docs/API_CONTRACT.md`, `docs/UI_ENGINE_HANDOFF.md`, `docs/ARCHITECTURE_REVISION_4.md`, and the 12 baseline fixtures.

## Revision 3 Audit Fixes Verified

1. **Authoritative LIVE Mode Loading (`src/App.tsx`, `src/pages/DashboardPage.tsx`, `src/pages/SystemPage.tsx`)**
   - Switching to or starting in `LIVE` mode (`VITE_SATVIK_DATA_MODE=live`) immediately clears all demonstration state, enters an explicit `LIVE • CONNECTING` state, and fetches all required FastAPI endpoints at `0ms` (`getOverview`, `getJobs`, `getModels`, `getStages`, `getSystemHealth`, `getSystemLimits`, plus page-specific `getLoop`, `getLoopEvents`, `getResult`, and `getResultDiff`).
   - Demonstration data is never carried into a live screen.

2. **Exact `API_CONTRACT.md` Normalizer Fidelity (`src/api/normalizer.ts`)**
   - `normalizeOverview()` maps `stop`, `loops_running`, `queue_depth`, `jobs_waiting`, `pace`, `review_usd_today`, `review_cap_usd`, `free_today`, and `sandbox_slots` (`"1/2"`, number, or object) with **zero** hardcoded demonstration defaults.
   - `normalizeJob()` preserves `model`, `lease` (`alive`, `holder_pid`, `heartbeat_age_s`, `heartbeat_at`), and `stages` execution status maps/arrays.

3. **Full Mutation Wire Compatibility (`src/api/client.ts`)**
   - `POST /api/system/stop` -> `{"stop": true}`
   - `POST /api/jobs/validate` -> `{"valid": true, "problems": []}`
   - `POST /api/jobs` -> `{"queued": true, "queue_file": "...", "stage_snapshot": {...}}`
   - `POST /api/loops/start` -> `{"started": 2}`
   - `POST /api/models` -> direct `Model` object
   - `DELETE /api/models/{id}` -> HTTP `204 No Content` empty body handled safely without calling `response.json()` on empty payload
   - `POST /api/plans` -> HTTP `202 Accepted` (`{"plan_id": "...", "status": "planning"}`) followed by `GET /api/plans/{id}`
   - `buildValidZipArchiveBytes()` computes real IEEE 802.3 CRC-32 checksums verified by `zipfile.testzip()`.

## Running Portable Tests & Development Server

```bash
# Run the portable Node.js transport & normalizer test suite (works with zero external dependencies)
node --test tests/transport.test.mjs

# Install dependencies and start Vite dev server
npm install
npm run dev
```
