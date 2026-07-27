/* @vitest-environment jsdom */

import { describe, expect, it, vi } from 'vitest';

import {
  ACTIVE_POLL_INTERVAL_MS,
  HIDDEN_POLL_INTERVAL_MS,
  RETRY_DELAYS_MS,
  currentPollInterval,
  isRetryableReadFailure,
  retryDelay,
} from './polling.js';

describe('polling policy', () => {
  it('slows steady-state polling while the tab is hidden', () => {
    vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible');
    expect(currentPollInterval()).toBe(ACTIVE_POLL_INTERVAL_MS);
    vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden');
    expect(currentPollInterval()).toBe(HIDDEN_POLL_INTERVAL_MS);
  });

  it('retries only network and transient HTTP failures', () => {
    expect(isRetryableReadFailure(new TypeError('offline'))).toBe(true);
    for (const status of [408, 425, 429, 500, 503, 599]) {
      expect(isRetryableReadFailure({ status })).toBe(true);
    }
    for (const status of [400, 401, 403, 404, 409, 422, 600]) {
      expect(isRetryableReadFailure({ status })).toBe(false);
    }
  });

  it('caps retry backoff', () => {
    expect(retryDelay(0)).toBe(RETRY_DELAYS_MS[0]);
    expect(retryDelay(1)).toBe(RETRY_DELAYS_MS[1]);
    expect(retryDelay(2)).toBe(RETRY_DELAYS_MS[2]);
    expect(retryDelay(3)).toBe(RETRY_DELAYS_MS[3]);
    expect(retryDelay(99)).toBe(RETRY_DELAYS_MS.at(-1));
  });
});
