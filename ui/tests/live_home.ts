/**
 * Paths and ids of the seeded LIVE home (ui/tests/seed_live_home.py). The Playwright live config and the real-API spec both
 * import this module, so they agree on where SATVIK_HOME is and which job the seed copied.
 */
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const testsDir = path.dirname(fileURLToPath(import.meta.url));

/** The UI folder's parent: the repository root (monitor/, config/, docs/). */
export const REPO_ROOT = path.resolve(testsDir, '..', '..');

/** Wiped and re-seeded by the webServer command before every run. */
export const SEED_DIR = path.join(os.tmpdir(), 'satvik-live-e2e');
export const SATVIK_HOME = path.join(SEED_DIR, 'home');
export const SATVIK_REPO = path.join(SEED_DIR, 'repo');

/** The archived job copied from origin/ah-run-2026-11-04 (merged, 18 events in events.jsonl). */
export const ARCHIVED_JOB_ID = 'ah-budget-20261104-0344-1119';
export const ARCHIVED_JOB_NAME = 'ah-budget';

/** The job file queued by the seed (the file name the API gives a queued job). */
export const SEEDED_QUEUED_JOB_ID = '00000000000000000001-0-live-e2e-pending';

/** A job the spec queues itself (through the UI), twice. Repo-relative, as the API expects. */
export const TWICE_JOB_PATH = 'jobs/live-e2e-twice.json';
