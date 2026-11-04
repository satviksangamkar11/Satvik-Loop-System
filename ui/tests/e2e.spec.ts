/**
 * Playwright e2e suite. The UI runs in LIVE mode (`npm run dev:live`) against tests/fake_fastapi_server.mjs,
 * which serves the canonical fixtures in monitor/fixtures/ unmodified.
 */
import { test, expect, type Page } from '@playwright/test';

const PAGES: Array<{ testid: string; heading: string }> = [
  { testid: 'nav-dashboard', heading: '1. Operational Dashboard (/dashboard)' },
  { testid: 'nav-jobs-new', heading: '2. Run Jobs (/jobs/new)' },
  { testid: 'nav-loops', heading: '3. Live Loops (/loops)' },
  { testid: 'nav-models', heading: '4. Models (/models)' },
  { testid: 'nav-stages', heading: '5. Stage Settings (/stages)' },
  { testid: 'nav-results', heading: '6. Results (/results)' },
  { testid: 'nav-system', heading: '7. System (/system)' },
];

/** Opens the navigation (phone and tablet widths hide it behind the menu button), then goes to a page. */
async function goToPage(page: Page, testid: string) {
  const menuToggle = page.getByLabel('Toggle Navigation Menu');
  if (await menuToggle.isVisible()) {
    await menuToggle.click();
  }
  await page.getByTestId(testid).click();
}

test.describe('Satvik Loop Control Center (LIVE mode, fixture-backed backend)', () => {
  test('LIVE mode connects and each of the 7 pages renders its heading', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByText('LIVE / CONNECTED (127.0.0.1:8765)')).toBeVisible();

    for (const p of PAGES) {
      await goToPage(page, p.testid);
      await expect(page.getByRole('heading', { level: 1, name: p.heading })).toBeVisible();
    }
  });

  test('a failed contract verify is shown as not verified', async ({ page }) => {
    await page.goto('/');
    await goToPage(page, 'nav-models');

    await page.getByTestId('model-row-z-ai/glm-5.3-flash').click();
    const contractRow = page.getByTestId('contract-repair_edit_blocks').locator('..');
    await contractRow.getByRole('button', { name: 'Verify contract' }).click();

    const dialog = page.getByRole('dialog');
    await dialog.getByRole('checkbox').check();
    await dialog.getByRole('button', { name: 'Confirm & Dispatch Probe' }).click();

    // The backend answered result "fail": the UI must not show verified.
    await expect(page.getByText(/repair_edit_blocks FAILED for z-ai\/glm-5\.3-flash/)).toBeVisible();
    await expect(page.getByTestId('contract-repair_edit_blocks')).toContainText(/not verified/i);
    await expect(page.getByTestId('contract-repair_edit_blocks')).not.toContainText(/^verified$/);
  });

  test('soft STOP asks for confirmation and reports the backend state', async ({ page }) => {
    await page.goto('/');
    await page.getByRole('button', { name: /GLOBAL STOP/ }).click();
    await page.getByRole('button', { name: 'Enable Soft STOP (on: true)' }).click();
    await expect(page.getByText(/STOP is on\./)).toBeVisible();
  });

  test('queueing the same job twice shows the 409 duplicate message', async ({ page }) => {
    // Unique path per run: the fake backend remembers queued paths across the browser projects.
    const jobPath = `jobs/e2e-${Date.now()}-${Math.floor(Math.random() * 1e6)}.json`;

    await page.goto('/');
    await goToPage(page, 'nav-jobs-new');
    await expect(page.getByText('writer_a_write').first()).toBeVisible();

    await page.getByTestId('job-path-input').fill(jobPath);
    await page.getByTestId('validate-job-btn').click();
    await expect(page.getByTestId('validation-result')).toBeVisible();

    await page.getByTestId('queue-job-btn').click();
    await expect(page.getByText(/^Queued as/)).toBeVisible();

    await page.getByTestId('queue-job-btn').click();
    await expect(page.getByText('409-duplicate-pending')).toBeVisible();
    await expect(page.getByText('the same job is already queued')).toBeVisible();
  });
});
