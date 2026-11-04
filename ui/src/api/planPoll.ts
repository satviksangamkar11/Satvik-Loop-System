/**
 * Plan polling (I61). A plan is drafted asynchronously: POST /plans answers `planning`, and the plan is read until it leaves that state.
 * The loop has no attempt cap and no time cutoff (the old 30 x 2 s loop gave up after about 60 s while the plan was still being drafted).
 * It backs off between reads (1 s, 2 s, 4 s, 8 s, then every 15 s) and stops only at a settled state, when the caller aborts, or when
 * the component unmounts. A read that fails (no response, or an error answer) is retried on the same schedule; it never ends the wait.
 */

export const PLAN_POLL_FIRST_MS = 1000;
export const PLAN_POLL_MAX_MS = 15000;

/** The delay before read number `attempt` (0 for the first retry): doubles from 1 s and stays at 15 s. */
export function planPollDelayMs(attempt: number): number {
  const doubled = PLAN_POLL_FIRST_MS * 2 ** Math.min(Math.max(attempt, 0), 10);
  return Math.min(PLAN_POLL_MAX_MS, doubled);
}

/** `planning` is the only state that is not settled. A null or unknown state is settled: it is shown as it is, never waited on. */
export function isPlanSettled(state: string | null | undefined): boolean {
  return state !== 'planning';
}

function abortError(): Error {
  const err = new Error('aborted');
  err.name = 'AbortError';
  return err;
}

/** Waits `ms`, or rejects with an AbortError as soon as the signal aborts. */
export function sleepUnlessAborted(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(abortError());
      return;
    }
    const onAbort = () => {
      clearTimeout(timer);
      reject(abortError());
    };
    const timer = setTimeout(() => {
      signal?.removeEventListener('abort', onAbort);
      resolve();
    }, ms);
    signal?.addEventListener('abort', onAbort, { once: true });
  });
}

export interface PollPlanOptions<P extends { state: string | null }> {
  /** The plan as the create call answered it (usually `planning`). */
  initial: P;
  /** Reads the plan once (GET /plans/{id}). */
  fetchPlan: () => Promise<P>;
  signal?: AbortSignal;
  /** Called with each plan read (not with failed reads). */
  onUpdate?: (plan: P) => void;
  /** Called with each failed read; the poll goes on. */
  onReadError?: (err: unknown) => void;
  /** Injected for tests: the wait between reads. */
  sleep?: (ms: number, signal?: AbortSignal) => Promise<void>;
}

/**
 * Reads the plan until it is settled and returns that state. Throws AbortError only when `signal` aborts; any other failure is
 * reported through onReadError and retried.
 */
export async function pollPlanUntilSettled<P extends { state: string | null }>(opts: PollPlanOptions<P>): Promise<P> {
  const sleep = opts.sleep ?? sleepUnlessAborted;
  let plan = opts.initial;
  let attempt = 0;
  while (!isPlanSettled(plan.state)) {
    await sleep(planPollDelayMs(attempt), opts.signal);
    try {
      plan = await opts.fetchPlan();
      opts.onUpdate?.(plan);
    } catch (err) {
      if ((err as { name?: string })?.name === 'AbortError' || opts.signal?.aborted) throw err;
      opts.onReadError?.(err);
    }
    attempt += 1;
  }
  return plan;
}

/** The session-scoped key that remembers a plan still being drafted, so a reload resumes its polling (I61). */
export const PENDING_PLAN_KEY = 'satvik.pendingPlanId';

/** sessionStorage can be blocked or missing: every access is guarded and a failure only means "nothing to resume". */
export function rememberPendingPlan(planId: string | null): void {
  try {
    if (planId) window.sessionStorage.setItem(PENDING_PLAN_KEY, planId);
    else window.sessionStorage.removeItem(PENDING_PLAN_KEY);
  } catch {
    /* no storage: the plan is still polled in this page */
  }
}

export function pendingPlanId(): string | null {
  try {
    return window.sessionStorage.getItem(PENDING_PLAN_KEY);
  } catch {
    return null;
  }
}
