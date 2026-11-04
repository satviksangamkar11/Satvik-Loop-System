import fs from 'node:fs';
import { defineConfig, devices } from '@playwright/test';

// Use the preinstalled Chromium when it is present in this environment. Never runs `playwright install`.
const PREINSTALLED_CHROMIUM = '/opt/pw-browsers/chromium';
const launchOptions = fs.existsSync(PREINSTALLED_CHROMIUM) ? { executablePath: PREINSTALLED_CHROMIUM } : {};

export default defineConfig({
  testDir: './tests',
  testMatch: '**/*.spec.ts',
  // The real-API spec needs the live backend: it runs only through playwright.live.config.ts (npm run test:e2e:real).
  testIgnore: '**/e2e.real.spec.ts',
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
      name: 'tablet-768',
      use: { ...devices['Desktop Chrome'], viewport: { width: 768, height: 1024 } },
    },
    {
      name: 'mobile-320',
      use: { ...devices['Pixel 5'], viewport: { width: 320, height: 740 } },
    },
  ],
  // The e2e suite runs the UI in LIVE mode (`dev:live`) against the fixture-backed fake backend.
  webServer: [
    {
      command: 'node tests/fake_fastapi_server.mjs',
      url: 'http://127.0.0.1:8765/api/overview',
      reuseExistingServer: false,
      timeout: 30_000,
    },
    {
      command: 'npm run dev:live -- --port 5173 --strictPort',
      url: 'http://127.0.0.1:5173',
      reuseExistingServer: false,
      timeout: 60_000,
    },
  ],
});
