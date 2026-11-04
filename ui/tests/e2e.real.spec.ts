/**
 * LIVE e2e against the REAL FastAPI backend (monitor/api.py), run by `npm run test:e2e:real` (playwright.live.config.ts).
 *
 * The backend runs on the home that tests/seed_live_home.py seeds: one archived job (merged), its registry row, an expired released
 * lease, one queued job file. Nothing here starts a loop or calls a model. The fixture suite (e2e.spec.ts) is not used.
 */
import fs from 'node:fs';
import path from 'node:path';
import { test, expect, type Page, type APIRequestContext } from '@playwright/test';
import { ARCHIVED_JOB_ID, ARCHIVED_JOB_NAME, REPO_ROOT, SATVIK_HOME, TWICE_JOB_PATH } from './live_home';

const API = 'http://127.0.0.1:8765/api';
const STOP_FILE = path.join(SATVIK_HOME, 'STOP');
const QUEUE_DIR = path.join(SATVIK_HOME, 'queue');
const ARCHIVED_EVENTS = path.join(SATVIK_HOME, 'results', ARCHIVED_JOB_ID, ARCHIVED_JOB_NAME, 'events.jsonl');

const PAGES: Array<{ testid: string; heading: string }> = [
  { testid: 'nav-dashboard', heading: '1. Operational Dashboard (/dashboard)' },
  { testid: 'nav-jobs-new', heading: '2. Run Jobs (/jobs/new)' },
  { testid: 'nav-loops', heading: '3. Live Loops (/loops)' },
  { testid: 'nav-models', heading: '4. Models (/models)' },
  { testid: 'nav-stages', heading: '5. Stage Settings (/stages)' },
  { testid: 'nav-results', heading: '6. Results (/results)' },
  { testid: 'nav-system', heading: '7. System (/system)' },
];

/** The catalog the backend reads: config/models.json (OpenRouter `model_id`, Claude Code `model`). */
function catalogIds(): string[] {
  const catalog = JSON.parse(fs.readFileSync(path.join(REPO_ROOT, 'config', 'models.json'), 'utf8')) as {
    openrouter: Array<{ model_id: string }>;
    claude_code: Array<{ model: string }>;
  };
  return [...catalog.openrouter.map((m) => m.model_id), ...catalog.claude_code.map((m) => m.model)];
}

/** Lines in the archived events.jsonl: the number of events the timeline must show. */
function archivedEventCount(): number {
  return fs.readFileSync(ARCHIVED_EVENTS, 'utf8').split('\n').filter((line) => line.trim().length > 0).length;
}

/** Opens the navigation (phone widths hide it behind the menu button), then goes to a page. */
async function goToPage(page: Page, testid: string) {
  const menuToggle = page.getByLabel('Toggle Navigation Menu');
  if (await menuToggle.isVisible()) {
    await menuToggle.click();
  }
  await page.getByTestId(testid).click();
}

/** Turns STOP off through the API, so a failed test cannot leave the shared home stopped for the next project. */
async function clearStop(request: APIRequestContext) {
  if (fs.existsSync(STOP_FILE)) {
    await request.post(`${API}/system/stop`, { data: { on: false } });
  }
}

test.describe('Satvik Loop Control Center (LIVE mode, real backend)', () => {
  test.afterEach(async ({ request }) => {
    await clearStop(request);
  });

  test('all 7 pages render real data and log no console errors', async ({ page }) => {
    const errors: string[] = [];
    page.on('console', (msg) => {
      if (msg.type() === 'error') errors.push(`console: ${msg.text()} @ ${msg.location().url}`);
    });
    page.on('pageerror', (err) => errors.push(`pageerror: ${err.message}`));
    page.on('response', (res) => {
      if (res.status() >= 400) errors.push(`http ${res.status()} ${res.request().method()} ${res.url()}`);
    });

    await page.goto('/');
    await expect(page.getByText('LIVE / CONNECTED (127.0.0.1:8765)')).toBeVisible();

    for (const p of PAGES) {
      await goToPage(page, p.testid);
      await expect(page.getByRole('heading', { level: 1, name: p.heading })).toBeVisible();
    }

    // Real data on each page, read from the seeded home.
    await goToPage(page, 'nav-dashboard');
    await expect(page.getByText(/live-e2e-pending/).first()).toBeVisible();

    await goToPage(page, 'nav-jobs-new');
    await expect(page.getByText('writer_a_write').first()).toBeVisible();

    await goToPage(page, 'nav-loops');
    await expect(page.getByTestId(`loop-row-${ARCHIVED_JOB_ID}`)).toBeVisible();

    await goToPage(page, 'nav-models');
    await expect(page.getByTestId(`model-row-${catalogIds()[0]}`)).toBeVisible();

    await goToPage(page, 'nav-stages');
    await expect(page.getByTestId('stage-row-writer_a_write')).toBeVisible();

    await goToPage(page, 'nav-results');
    await expect(page.getByTestId(`result-select-${ARCHIVED_JOB_ID}`)).toBeVisible();

    await goToPage(page, 'nav-system');
    await expect(page.getByText(/satvik-live-e2e/).first()).toBeVisible();

    expect(errors, errors.join('\n')).toEqual([]);
  });

  test('the archived job shows merged, with its attempt timeline from events.jsonl', async ({ page }) => {
    await page.goto('/');
    await goToPage(page, 'nav-loops');
    // The derived state comes from the registry row, the lease and the merge event (events.jsonl line 18), not from a guess.
    const row = page.getByTestId(`loop-row-${ARCHIVED_JOB_ID}`);
    await expect(row).toContainText('merged');
    await row.click();

    const events = page.getByTestId('loop-event');
    await expect(events).toHaveCount(archivedEventCount());
    await expect(events.first()).toContainText('job-start');
    await expect(events.last()).toContainText('merge');

    // The attempts are rebuilt from attempt-start events: the stages that ran are named, the rest are not.
    await expect(page.getByText('Attempts seen in events:')).toBeVisible();
    await expect(page.getByText(/writer_a_write/).first()).toBeVisible();
    await expect(page.getByText(/reviewer_b/).first()).toBeVisible();
    await expect(page.getByText(/reviewer_a/).first()).toBeVisible();
  });

  test('queueing the same job twice shows the 409 duplicate message', async ({ page, request }) => {
    await page.goto('/');
    await goToPage(page, 'nav-jobs-new');
    await expect(page.getByText('writer_a_write').first()).toBeVisible();

    await page.getByTestId('job-path-input').fill(TWICE_JOB_PATH);
    await page.getByTestId('validate-job-btn').click();
    await expect(page.getByTestId('validation-result')).toBeVisible();

    // First submit: queued (201) on the first project, already pending (409) on the later ones. Second submit: always 409.
    await page.getByTestId('queue-job-btn').click();
    await page.waitForTimeout(2500);
    console.log('DEBUG-NOTICE', JSON.stringify(await page.getByTestId('api-notice').allInnerTexts().catch(() => 'no-notice-testid')));
    console.log('DEBUG-BODY', (await page.locator('body').innerText()).slice(0, 1500).replace(/\n+/g, ' | '));
    await expect(page.getByText(/^Queued as|409-duplicate-pending/).first()).toBeVisible();

    await page.getByTestId('queue-job-btn').click();
    await expect(page.getByText('409-duplicate-pending')).toBeVisible();
    await expect(page.getByText('the same job is already queued')).toBeVisible();

    // The backend holds exactly one pending file for that job.
    const pending = fs.readdirSync(QUEUE_DIR).filter((name) => name.endsWith('live-e2e-twice.json'));
    expect(pending).toHaveLength(1);

    // The API itself refuses the same job the same way (no UI in between).
    const again = await request.post(`${API}/jobs`, {
      headers: { 'Idempotency-Key': crypto.randomUUID() },
      data: { job_path: TWICE_JOB_PATH, rerun: false, paid_budget_usd: 0 },
    });
    expect(again.status()).toBe(409);
    expect((await again.json()).error.code).toBe('duplicate-pending');
  });

  test('STOP toggles on and off, and SATVIK_HOME/STOP follows', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByText('LIVE / CONNECTED (127.0.0.1:8765)')).toBeVisible();
    expect(fs.existsSync(STOP_FILE)).toBe(false);

    await page.getByRole('button', { name: /GLOBAL STOP/ }).click();
    await page.getByRole('button', { name: 'Enable Soft STOP (on: true)' }).click();
    await expect(page.getByText(/STOP is on\./)).toBeVisible();
    expect(fs.existsSync(STOP_FILE)).toBe(true);

    await page.getByRole('button', { name: 'STOP ON (SOFT)' }).click();
    await page.getByRole('button', { name: 'Resume Normal Queueing (on: false)' }).click();
    await expect(page.getByText(/STOP is off\./)).toBeVisible();
    expect(fs.existsSync(STOP_FILE)).toBe(false);
  });

  test('the Models page lists the catalog from config/models.json', async ({ page }) => {
    await page.goto('/');
    await goToPage(page, 'nav-models');
    await expect(page.getByRole('heading', { level: 1, name: '4. Models (/models)' })).toBeVisible();

    for (const id of catalogIds()) {
      await expect(page.getByTestId(`model-row-${id}`)).toBeVisible();
    }
    await expect(page.locator('[data-testid^="model-row-"]')).toHaveCount(catalogIds().length);
  });

  test('a model with an unverified contract is not offered as selectable', async ({ page }) => {
    await page.goto('/');
    await goToPage(page, 'nav-stages');
    await page.getByTestId('stage-row-writer_a_write').click();

    // config/models.json holds no verified contract with a recorded verification time: the OpenRouter entries are unverified, and
    // the Claude Code entries say `verified` with verified_at null (no probe ran). So no model may be selectable for the writer stage.
    const select = page.getByTestId('stage-model-select');
    const selectable = await select.locator('option:not([disabled])').evaluateAll((els) =>
      els.map((el) => (el as HTMLOptionElement).value)
    );
    expect(selectable).toEqual([]);

    const offered = await select.locator('option').evaluateAll((els) => els.map((el) => (el as HTMLOptionElement).value));
    expect(offered).not.toContain('nvidia/nemotron-3-ultra-550b-a55b:free');
    expect(offered).not.toContain('opus');
  });
});
