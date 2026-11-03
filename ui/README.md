# Satvik Loop Control Center UI (`ui/`)

React 19 + TypeScript + Vite + Tailwind control center for the Satvik Loop System. The contract is `docs/API_CONTRACT.md`; the canonical sample responses are `monitor/fixtures/*.json`, and they are the truth.

## Modes

- `npm run dev`: MOCK mode. Reads answer from the fixtures in `monitor/fixtures/` (unmodified). Anything that needs a live engine (a provider probe, a budget check, an archive) returns 503 instead of an invented result.
- `npm run dev:live`: LIVE mode against `http://127.0.0.1:8765/api`. Mode comes from `VITE_DATA_MODE` in `.env.mock` / `.env.live`. For debugging only, `window.__SATVIK_DATA_MODE__` overrides it.

## Rules the code follows

- Absent or unknown backend values are `null` and render as "Unknown". Nothing is defaulted (no sandbox count, review ceiling, price, job id or status is invented).
- A contract is "verified" only on `POST /models/verify` result `pass`. `fail` is failed, and a missing or malformed result is unverified.
- Claude Code models are a subscription: no USD figure.
- Every mutation sends an `Idempotency-Key` (RFC-4122 v4). Payloads follow contract section 3; `POST /jobs` sends `stages` as a keyed object.
- `PUT /system/limits` stays locked until the backend supplies a revision (Gap C-08).

## Scripts

```bash
npm ci
npm run typecheck     # tsc --noEmit
npm run lint          # eslint
npm run build         # tsc -b && vite build
npm run test:unit     # vitest, against the fixtures in monitor/fixtures/
npm run test:e2e      # Playwright, LIVE mode against tests/fake_fastapi_server.mjs
```

`test:e2e` uses the preinstalled Chromium at `/opt/pw-browsers/chromium` when it exists, and does not run `playwright install`.
