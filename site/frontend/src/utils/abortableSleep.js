/**
 * Sleeps for `ms` milliseconds, rejecting with an AbortError if `signal` aborts.
 *
 * Shared by the polling loops in useWorkflow and useCreateData so the abort
 * bookkeeping lives in one place.
 *
 * @param {number} ms - Delay in milliseconds.
 * @param {AbortSignal} [signal] - Optional abort signal.
 * @returns {Promise<void>}
 */
export function abortableSleep(ms, signal) {
  return new Promise((resolve, reject) => {
    let settled = false;
    const cleanup = () => {
      if (signal) signal.removeEventListener('abort', onAbort);
    };
    const finish = () => {
      if (settled) return;
      settled = true;
      cleanup();
      resolve();
    };
    const onAbort = () => {
      if (settled) return;
      settled = true;
      clearTimeout(timeoutId);
      cleanup();
      reject(new DOMException('Aborted', 'AbortError'));
    };
    const timeoutId = setTimeout(finish, ms);
    if (signal) {
      if (signal.aborted) {
        onAbort();
        return;
      }
      signal.addEventListener('abort', onAbort, { once: true });
    }
  });
}
