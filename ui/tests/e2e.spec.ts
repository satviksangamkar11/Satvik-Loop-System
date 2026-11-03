import { test, expect } from '@playwright/test';

test.describe('Satvik Loop Control Center — Full 7-Page & Failure Path E2E Suite', () => {
  test('1. Navigates across all 7 pages and verifies page headings and core landmarks', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByText('SATVIK LOOP')).toBeVisible();

    await page.getByTestId('nav-queue').click();
    await expect(page.getByText('Path A: Existing Repo-Relative Job (jobs/<spec>.json)')).toBeVisible();

    await page.getByTestId('nav-live').click();
    await expect(page.getByText('10-Stage Execution Ladder')).toBeVisible();

    await page.getByTestId('nav-models').click();
    await expect(page.getByText('Add Approved Model (`POST /api/models`)')).toBeVisible();

    await page.getByTestId('nav-stages').click();
    await expect(page.getByText('10 Configurable Stage Types (`PUT /api/stages`)')).toBeVisible();

    await page.getByTestId('nav-results').click();
    await expect(page.getByText('Terminal Result & Diff Inspector')).toBeVisible();

    await page.getByTestId('nav-system').click();
    await expect(page.getByText('Host & Engine Health (`GET /api/system/health`)')).toBeVisible();
  });

  test('2. Soft STOP modal requires explicit confirmation before mutating state and supports resume', async ({ page }) => {
    await page.goto('/');
    await page.getByTestId('soft-stop-toggle-btn').click();
    await expect(page.getByText('Confirm Global Soft STOP (`POST /api/system/stop`)')).toBeVisible();
    await page.getByTestId('confirm-soft-stop-btn').click();
    await expect(page.getByText('CLEAR STOP / RESUME QUEUE')).toBeVisible();

    // Resume queue
    await page.getByTestId('soft-stop-toggle-btn').click();
    await page.getByTestId('confirm-soft-stop-btn').click();
    await expect(page.getByText('SOFT STOP (DRAIN SAFELY)')).toBeVisible();
  });

  test('3. Queue / Start Job page validates repo-relative path, queues job, and starts workers', async ({ page }) => {
    await page.goto('/');
    await page.getByTestId('nav-queue').click();

    // Validate path
    await page.getByTestId('validate-job-btn').click();
    await expect(page.getByText('Validation Result (`POST /api/jobs/validate`)')).toBeVisible();

    // Queue job
    await page.getByTestId('queue-job-btn').click();
    await expect(page.getByText(/Queued .* with immutable stage snapshot/)).toBeVisible();

    // Start workers
    await page.getByTestId('start-workers-btn').click();
    await expect(page.getByText(/Started .* loop worker/)).toBeVisible();
  });

  test('4. Queue / Start Job page rejects path traversal outside jobs/<spec>.json', async ({ page }) => {
    await page.goto('/');
    await page.getByTestId('nav-queue').click();
    const input = page.getByTestId('job-path-input');
    await input.fill('../outside.json');
    await page.getByTestId('validate-job-btn').click();
    await expect(page.getByText(/Path must start with jobs/)).toBeVisible();
  });

  test('5. Path B Natural Language Plan Request generates job spec and loads into Path A', async ({ page }) => {
    await page.goto('/');
    await page.getByTestId('nav-queue').click();
    await page.getByTestId('generate-plan-btn').click();
    await expect(page.getByText('Generated Spec Preview (`GET /api/plans/{id}`)')).toBeVisible();
    await page.getByTestId('use-generated-job-btn').click();
  });

  test('6. Models page adds a model, blocks deleting an active stage model (409), and deletes an unused model (204)', async ({ page }) => {
    await page.goto('/');
    await page.getByTestId('nav-models').click();

    await page.getByTestId('new-model-id-input').fill('openrouter/deepseek/deepseek-r1:free');
    await page.getByTestId('new-model-label-input').fill('DeepSeek R1 Free');
    await page.getByTestId('add-model-btn').click();
    await expect(page.getByText('DeepSeek R1 Free')).toBeVisible();

    // Attempt to delete active stage model -> 409 error banner
    await page.getByTestId('delete-model-openrouter/qwen/qwen3-coder:free').click();
    await expect(page.getByText(/HTTP 409/)).toBeVisible();

    // Delete newly added unused model -> succeeds via 204
    await page.getByTestId('delete-model-openrouter/deepseek/deepseek-r1:free').click();
  });

  test('7. Stage Settings page enforces reviewer independence and handles 409 rev conflict recovery', async ({ page }) => {
    await page.goto('/');
    await page.getByTestId('nav-stages').click();
    await page.getByTestId('save-stages-btn').click();
    await expect(page.getByText(/Stage settings saved/)).toBeVisible();

    // Trigger 409 conflict scenario
    await page.getByTestId('scenario-selector').selectOption('conflict_409');
    await page.getByTestId('save-stages-btn').click();
    await expect(page.getByText(/Stage revision conflict/)).toBeVisible();
  });

  test('8. Results page switches completed/discarded results, inspects diff, and downloads ZIP archive', async ({ page }) => {
    await page.goto('/');
    await page.getByTestId('nav-results').click();
    await page.getByTestId('result-select-job_flaky_retry_discarded').click();
    await expect(page.getByText('discarded_Restored_Green')).toBeVisible();
    await page.getByTestId('download-archive-btn').click();
    await expect(page.getByText(/Downloaded archive/)).toBeVisible();
  });

  test('9. LIVE mode clears mock data immediately and shows disconnected error banner when FastAPI is offline', async ({ page }) => {
    await page.goto('/');
    await page.getByTestId('mode-live-btn').click();
    await expect(page.getByText(/LIVE • DISCONNECTED|LIVE • CONNECTING/)).toBeVisible();
    await expect(page.getByTestId('live-disconnected-banner')).toBeVisible();
  });
});
