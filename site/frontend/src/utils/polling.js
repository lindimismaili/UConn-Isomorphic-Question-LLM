export const ACTIVE_POLL_INTERVAL_MS = 1_500;
export const HIDDEN_POLL_INTERVAL_MS = 7_500;
export const RETRY_DELAYS_MS = Object.freeze([1_000, 2_000, 5_000, 10_000]);

/** Use a slower steady-state cadence while the tab is hidden. */
export function currentPollInterval() {
  return typeof document !== 'undefined' && document.visibilityState === 'hidden'
    ? HIDDEN_POLL_INTERVAL_MS
    : ACTIVE_POLL_INTERVAL_MS;
}

/** Only network failures and explicitly transient HTTP statuses are retried. */
export function isRetryableReadFailure(error) {
  const status = error?.status;
  return status == null
    || status === 408
    || status === 425
    || status === 429
    || (status >= 500 && status <= 599);
}

/** Exponential-style retry schedule capped at ten seconds. */
export function retryDelay(attempt) {
  const safeAttempt = Number.isInteger(attempt) && attempt > 0 ? attempt : 0;
  return RETRY_DELAYS_MS[Math.min(safeAttempt, RETRY_DELAYS_MS.length - 1)];
}
