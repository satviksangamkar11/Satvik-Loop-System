import fs from 'node:fs';
import { defineConfig, devices } from '@playwright/test';
import { REPO_ROOT, SATVIK_HOME, SATVIK_REPO, SEED_DIR } from './tests/live_home';

// Playwright config for the LIVE e2e run against the REAL FastAPI backend (`monitor.api`), not the fixture server.
//
//   npm run test:e2e:real
//
// 1. ui/tests/seed_live_home.py wipes and seeds a temporary SATVIK_HOME (one archived job copied from git, its registry row,
//    an expired released lease, one queued job file) and a SATVIK_REPO holding the job files the API validates.
// 2. uvicorn starts on 127.0.0.1:8765 (one worker, SATVIK_PROFILE=test). No network, no model call: the backend reads files only.
// 3. vite starts in live mode (VITE_DATA_MODE=live) on 127.0.0.1:5173. The UI calls 127.0.0.1:8765 directly (CORS allows 5173).
// The fixture suite (playwright.config.ts, tests/fake_fastapi_server.mjs) is not involved.

const PYTHON = process.env.SATVIK_PYTHON ?? (process.platform === 'win32' ? 'python' : 'python3');

// Use the preinstalled Chromium when it is present in this environment. Never runs `playwright install`.
const PREINSTALLED_CHROMIUM = '/opt/pw-browsers/chromium';
const launchOptions = fs.existsSync(PREINSTALLED_CHROMIUM) ? { executablePath: PREINSTALLED_CHROMIUM } : {};

export default defineConfig({
  testDir: './tests',
  testMatch: '**/e2e.real.spec.ts',
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: 0,
  workers: 1,
  reporter: 'list',
  use: {
    baseURL: 'http://127.0.0.1:5173',
    trace: 'off',
    screenshot: 'off',
    launchOptions,
  },
  projects: [
    {
      name: 'desktop-1440',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } },
    },
    {
      name: 'mobile-320',
      use: { ...devices['Pixel 5'], viewport: { width: 320, height: 740 } },
    },
  ],
  webServer: [
    {
      command: `${PYTHON} ui/tests/seed_live_home.py "${SEED_DIR}" && ${PYTHON} -m uvicorn monitor.api:app --host 127.0.0.1 --port 8765 --workers 1`,
      cwd: REPO_ROOT,
      url: 'http://127.0.0.1:8765/api/overview',
      env: {
        SATVIK_HOME,
        SATVIK_REPO,
        SATVIK_PROFILE: 'test',
      },
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command: 'npm run dev:live -- --port 5173 --strictPort',
      url: 'http://127.0.0.1:5173',
      reuseExistingServer: false,
      timeout: 60_000,
    },
  ],
});
