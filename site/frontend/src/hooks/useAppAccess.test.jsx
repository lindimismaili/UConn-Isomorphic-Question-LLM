/* @vitest-environment jsdom */

import { act, useEffect } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useAppAccess } from './useAppAccess.js';
import {
  APP_ACCESS_TOKEN_HEADER,
  APP_ACCESS_TOKEN_STORAGE_KEY,
} from '../utils/api.js';

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

function jsonResponse(payload, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => payload,
    text: async () => JSON.stringify(payload),
  };
}

let root;
let container;
let accessState;

function Harness({ onReady }) {
  const value = useAppAccess();
  useEffect(() => {
    onReady(value);
  }, [onReady, value]);
  return null;
}

async function mount() {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  const onReady = (value) => {
    accessState = value;
  };
  await act(async () => {
    root.render(<Harness onReady={onReady} />);
  });
}

async function flush(count = 6) {
  await act(async () => {
    for (let index = 0; index < count; index += 1) {
      await Promise.resolve();
    }
  });
}

beforeEach(() => {
  sessionStorage.clear();
  accessState = null;
});

afterEach(() => {
  if (root) act(() => root.unmount());
  container?.remove();
  root = null;
  container = null;
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  sessionStorage.clear();
});

describe('useAppAccess', () => {
  it('waits at the access gate without repeatedly calling readiness', async () => {
    const fetchMock = vi.fn(async (url) => {
      if (String(url) === '/api/public-config') {
        return jsonResponse({ authRequired: true, paypalDonateUrl: '' });
      }
      if (String(url) === '/api/readiness') {
        return jsonResponse({ detail: 'Unauthorized' }, 401);
      }
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal('fetch', fetchMock);
    await mount();
    await flush();

    expect(accessState.authRequired).toBe(true);
    expect(accessState.accessGranted).toBe(false);
    expect(fetchMock.mock.calls.filter(([url]) => String(url) === '/api/readiness'))
      .toHaveLength(0);

    await act(async () => {
      expect(await accessState.verify('wrong-token')).toBe(false);
    });
    await flush(12);

    expect(fetchMock.mock.calls.filter(([url]) => String(url) === '/api/readiness'))
      .toHaveLength(1);
    expect(accessState.accessGranted).toBe(false);
    expect(sessionStorage.getItem(APP_ACCESS_TOKEN_STORAGE_KEY)).toBeNull();
  });

  it('stores a verified application token for this tab and reads readiness', async () => {
    let readinessHeaders;
    const fetchMock = vi.fn(async (url, options = {}) => {
      if (String(url) === '/api/public-config') {
        return jsonResponse({
          authRequired: true,
          donationUrl: 'https://paypal.me/example',
        });
      }
      if (String(url) === '/api/readiness') {
        readinessHeaders = new Headers(options.headers);
        return jsonResponse({
          authenticated: true,
          openaiApiKeyConfigured: true,
        });
      }
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal('fetch', fetchMock);
    await mount();
    await flush();

    await act(async () => {
      expect(await accessState.verify('valid-token')).toBe(true);
    });

    expect(readinessHeaders.get(APP_ACCESS_TOKEN_HEADER)).toBe('valid-token');
    expect(accessState.accessGranted).toBe(true);
    expect(accessState.workflowReady).toBe(true);
    expect(accessState.donationUrl).toBe('https://paypal.me/example');
    expect(sessionStorage.getItem(APP_ACCESS_TOKEN_STORAGE_KEY)).toBe('valid-token');
  });

  it.each([
    [
      'network failure',
      async () => {
        throw new Error('connection reset');
      },
    ],
    [
      'server failure',
      async () => jsonResponse({ detail: 'Temporarily unavailable' }, 503),
    ],
    [
      'response parse failure',
      async () => ({
        ok: true,
        status: 200,
        json: async () => {
          throw new SyntaxError('Unexpected end of JSON input');
        },
        text: async () => '',
      }),
    ],
    [
      'invalid response',
      async () => jsonResponse({ openaiApiKeyConfigured: true }),
    ],
  ])('retains a saved token after a transient readiness %s', async (_label, readinessReply) => {
    sessionStorage.setItem(APP_ACCESS_TOKEN_STORAGE_KEY, 'saved-token');
    let readinessHeaders;
    const fetchMock = vi.fn(async (url, options = {}) => {
      if (String(url) === '/api/public-config') {
        return jsonResponse({ authRequired: true });
      }
      if (String(url) === '/api/readiness') {
        readinessHeaders = new Headers(options.headers);
        return readinessReply();
      }
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal('fetch', fetchMock);

    await mount();
    await flush(12);

    expect(readinessHeaders.get(APP_ACCESS_TOKEN_HEADER)).toBe('saved-token');
    expect(accessState.accessGranted).toBe(false);
    expect(accessState.readiness).toBeNull();
    expect(accessState.accessError).toBe(
      'The saved application access token could not be verified.',
    );
    expect(sessionStorage.getItem(APP_ACCESS_TOKEN_STORAGE_KEY)).toBe('saved-token');
  });

  it.each([
    ['an explicit 401', async () => jsonResponse({ detail: 'Unauthorized' }, 401)],
    [
      'an unauthenticated readiness response',
      async () => jsonResponse({
        authenticated: false,
        openaiApiKeyConfigured: true,
      }),
    ],
  ])('clears a saved token after %s', async (_label, readinessReply) => {
    sessionStorage.setItem(APP_ACCESS_TOKEN_STORAGE_KEY, 'rejected-token');
    const fetchMock = vi.fn(async (url) => {
      if (String(url) === '/api/public-config') {
        return jsonResponse({ authRequired: true });
      }
      if (String(url) === '/api/readiness') {
        return readinessReply();
      }
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal('fetch', fetchMock);

    await mount();
    await flush(12);

    expect(accessState.accessGranted).toBe(false);
    expect(accessState.readiness).toBeNull();
    expect(accessState.accessError).toBe(
      'The saved application access token is no longer valid.',
    );
    expect(sessionStorage.getItem(APP_ACCESS_TOKEN_STORAGE_KEY)).toBeNull();
  });
});
