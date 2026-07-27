/* @vitest-environment jsdom */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  APP_ACCESS_TOKEN_HEADER,
  APP_ACCESS_TOKEN_STORAGE_KEY,
  APP_ACCESS_UNAUTHORIZED_EVENT,
  IDEMPOTENCY_KEY_HEADER,
  PAID_START_ATTEMPT_STORAGE_PREFIX,
  clearAccessToken,
  clearPaidStartAttempt,
  downloadApiFile,
  fetchWithAbort,
  getAccessToken,
  getOrCreatePaidStartAttempt,
  isAmbiguousPaidStartFailure,
  persistPaidStartAttempt,
  setAccessToken,
  withIdempotencyHeader,
} from './api.js';

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  sessionStorage.clear();
  localStorage.clear();
});

describe('application access token transport', () => {
  it('stores the token only in sessionStorage and supports clearing it', () => {
    setAccessToken(' tab-token ');

    expect(getAccessToken()).toBe('tab-token');
    expect(sessionStorage.getItem(APP_ACCESS_TOKEN_STORAGE_KEY)).toBe('tab-token');
    expect(localStorage.length).toBe(0);

    clearAccessToken();
    expect(getAccessToken()).toBe('');
    expect(sessionStorage.getItem(APP_ACCESS_TOKEN_STORAGE_KEY)).toBeNull();
  });

  it('adds the access header only to same-origin API requests', async () => {
    setAccessToken('tab-token');
    const fetchMock = vi.fn(async () => ({ ok: true, status: 200 }));
    vi.stubGlobal('fetch', fetchMock);

    await fetchWithAbort('/api/readiness');
    await fetchWithAbort('/version.json');
    await fetchWithAbort('https://example.com/api/readiness');

    const apiHeaders = new Headers(fetchMock.mock.calls[0][1].headers);
    expect(apiHeaders.get(APP_ACCESS_TOKEN_HEADER)).toBe('tab-token');
    expect(new Headers(fetchMock.mock.calls[1][1].headers).has(APP_ACCESS_TOKEN_HEADER)).toBe(false);
    expect(new Headers(fetchMock.mock.calls[2][1].headers).has(APP_ACCESS_TOKEN_HEADER)).toBe(false);
  });

  it('preserves an explicit candidate token and reports protected 401 responses', async () => {
    setAccessToken('stored-token');
    const unauthorized = vi.fn();
    globalThis.addEventListener(APP_ACCESS_UNAUTHORIZED_EVENT, unauthorized);
    const fetchMock = vi.fn(async () => ({ ok: false, status: 401 }));
    vi.stubGlobal('fetch', fetchMock);

    try {
      await fetchWithAbort('/api/readiness', {
        headers: { [APP_ACCESS_TOKEN_HEADER]: 'candidate-token' },
      });
    } finally {
      globalThis.removeEventListener(APP_ACCESS_UNAUTHORIZED_EVENT, unauthorized);
    }

    const headers = new Headers(fetchMock.mock.calls[0][1].headers);
    expect(headers.get(APP_ACCESS_TOKEN_HEADER)).toBe('candidate-token');
    expect(unauthorized).toHaveBeenCalledTimes(1);
  });

  it('downloads protected API artifacts with the tab token and a safe basename', async () => {
    setAccessToken('tab-token');
    const fetchMock = vi.fn(async () => new Response(new Blob(['archive']), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    const createObjectURL = vi.fn(() => 'blob:test');
    const revokeObjectURL = vi.fn();
    const createObjectURLDescriptor = Object.getOwnPropertyDescriptor(
      globalThis.URL,
      'createObjectURL',
    );
    const revokeObjectURLDescriptor = Object.getOwnPropertyDescriptor(
      globalThis.URL,
      'revokeObjectURL',
    );
    Object.defineProperty(globalThis.URL, 'createObjectURL', {
      configurable: true,
      value: createObjectURL,
    });
    Object.defineProperty(globalThis.URL, 'revokeObjectURL', {
      configurable: true,
      value: revokeObjectURL,
    });
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});

    try {
      await downloadApiFile('/api/download-zip/batch-id', '../batch.zip');

      const headers = new Headers(fetchMock.mock.calls[0][1].headers);
      expect(headers.get(APP_ACCESS_TOKEN_HEADER)).toBe('tab-token');
      expect(click).toHaveBeenCalledOnce();
      expect(createObjectURL).toHaveBeenCalledOnce();
    } finally {
      if (createObjectURLDescriptor) {
        Object.defineProperty(
          globalThis.URL,
          'createObjectURL',
          createObjectURLDescriptor,
        );
      } else {
        delete globalThis.URL.createObjectURL;
      }
      if (revokeObjectURLDescriptor) {
        Object.defineProperty(
          globalThis.URL,
          'revokeObjectURL',
          revokeObjectURLDescriptor,
        );
      } else {
        delete globalThis.URL.revokeObjectURL;
      }
    }
  });
});

describe('paid-start idempotency transport', () => {
  it('attaches a client key only to same-origin API requests', () => {
    const options = withIdempotencyHeader(
      '/api/workflow/start',
      '12345678-1234-4234-8234-123456789abc',
      { method: 'POST' },
    );
    expect(new Headers(options.headers).get(IDEMPOTENCY_KEY_HEADER)).toBe(
      '12345678-1234-4234-8234-123456789abc',
    );
    expect(() => withIdempotencyHeader(
      'https://example.com/api/workflow/start',
      '12345678-1234-4234-8234-123456789abc',
    )).toThrow(/only be sent to this site/i);
  });

  it('persists and reuses one key for an unchanged intent across remounts', () => {
    const first = getOrCreatePaidStartAttempt(
      'workflow',
      '/api/workflow/start',
      '{"prompt":"same"}',
    );
    first.preparedUpload = {
      sessionId: 'upload-session',
      uploadedFileIds: ['file-1'],
    };
    persistPaidStartAttempt(first);

    const restored = getOrCreatePaidStartAttempt(
      'workflow',
      '/api/workflow/start',
      '{"prompt":"same"}',
    );
    expect(restored.key).toBe(first.key);
    expect(restored.preparedUpload).toEqual(first.preparedUpload);
    expect(sessionStorage.getItem(
      `${PAID_START_ATTEMPT_STORAGE_PREFIX}workflow`,
    )).toContain(first.key);

    expect(() => getOrCreatePaidStartAttempt(
      'workflow',
      '/api/workflow/start',
      '{"prompt":"changed"}',
    )).toThrow(/previous paid start has an unknown outcome/i);

    clearPaidStartAttempt(first);
    expect(sessionStorage.getItem(
      `${PAID_START_ATTEMPT_STORAGE_PREFIX}workflow`,
    )).toBeNull();
  });

  it('classifies only uncertain paid-start outcomes as reusable', () => {
    for (const status of [undefined, null, 408, 425, 429, 500, 503, 599]) {
      expect(isAmbiguousPaidStartFailure(status)).toBe(true);
    }
    for (const status of [200, 400, 401, 403, 404, 409, 600]) {
      expect(isAmbiguousPaidStartFailure(status)).toBe(false);
    }
  });
});
