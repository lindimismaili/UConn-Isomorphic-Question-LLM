/**
 * Shared HTTP client for the frontend.
 *
 * One place for: the session-only application access token,
 * AbortError-preserving fetch, FastAPI `{detail}` error parsing, and the two
 * response contracts the app needs — "throw on failure" (most calls) and
 * "best-effort, return null on failure" (optional startup config loads).
 */

export const APP_ACCESS_TOKEN_STORAGE_KEY = 'uconnAppAccessToken';
export const APP_ACCESS_TOKEN_HEADER = 'X-App-Access-Token';
export const APP_ACCESS_UNAUTHORIZED_EVENT = 'uconn-app-access-unauthorized';
export const IDEMPOTENCY_KEY_HEADER = 'X-Idempotency-Key';
export const PAID_START_ATTEMPT_STORAGE_PREFIX = 'uconnPaidStartAttempt:';

function getSessionStorage() {
  try {
    return globalThis.sessionStorage ?? null;
  } catch {
    return null;
  }
}

/** Return the application access token for this browser tab, if one exists. */
export function getAccessToken() {
  try {
    return getSessionStorage()?.getItem(APP_ACCESS_TOKEN_STORAGE_KEY) || '';
  } catch {
    return '';
  }
}

/**
 * Store an application access token for this browser tab only.
 *
 * The OpenAI API key never belongs here; it remains server-side.
 */
export function setAccessToken(token) {
  const normalized = typeof token === 'string' ? token.trim() : '';
  const storage = getSessionStorage();
  if (!storage) return;
  try {
    if (normalized) {
      storage.setItem(APP_ACCESS_TOKEN_STORAGE_KEY, normalized);
    } else {
      storage.removeItem(APP_ACCESS_TOKEN_STORAGE_KEY);
    }
  } catch {
    // Storage can be unavailable in private/restricted browser contexts.
  }
}

/** Remove the application access token from this browser tab. */
export function clearAccessToken() {
  setAccessToken('');
}

/** Create an unpredictable per-intent key for one cost-bearing start POST. */
export function createIdempotencyKey() {
  if (typeof globalThis.crypto?.randomUUID === 'function') {
    return globalThis.crypto.randomUUID();
  }
  if (typeof globalThis.crypto?.getRandomValues !== 'function') {
    throw new Error('This browser cannot create a safe request identifier.');
  }
  const bytes = new Uint8Array(16);
  globalThis.crypto.getRandomValues(bytes);
  return Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('');
}

/** Attach a paid-start key only to a same-origin API request. */
export function withIdempotencyHeader(url, key, options = {}) {
  if (!isSameOriginApiUrl(url)) {
    throw new Error('Idempotency keys may only be sent to this site.');
  }
  const normalized = typeof key === 'string' ? key.trim() : '';
  if (!/^[A-Za-z0-9._~:-]{16,128}$/.test(normalized)) {
    throw new Error('Invalid paid-start request identifier.');
  }
  const headers = new Headers(options.headers || {});
  headers.set(IDEMPOTENCY_KEY_HEADER, normalized);
  return { ...options, headers };
}

/** True when a paid-start failure may have occurred after server acceptance. */
export function isAmbiguousPaidStartFailure(status) {
  return status === undefined
    || status === null
    || status === 408
    || status === 425
    || status === 429
    || (status >= 500 && status <= 599);
}

function intentToken(signature) {
  const value = String(signature);
  let first = 0x811c9dc5;
  let second = 0x9e3779b9;
  for (let index = 0; index < value.length; index += 1) {
    const code = value.charCodeAt(index);
    first = Math.imul(first ^ code, 0x01000193);
    second = Math.imul(second ^ (code + index), 0x85ebca6b);
  }
  return `${value.length}:${(first >>> 0).toString(16)}:${(second >>> 0).toString(16)}`;
}

/** Load or create the durable tab-local key for one unchanged paid intent. */
export function getOrCreatePaidStartAttempt(scope, endpoint, intentSignature) {
  const storageKey = `${PAID_START_ATTEMPT_STORAGE_PREFIX}${scope}`;
  const token = intentToken(intentSignature);
  const storage = getSessionStorage();
  if (storage) {
    try {
      const parsed = JSON.parse(storage.getItem(storageKey) || 'null');
      if (
        parsed?.endpoint === endpoint
        && parsed?.intentToken === token
        && typeof parsed?.key === 'string'
        && /^[A-Za-z0-9._~:-]{16,128}$/.test(parsed.key)
      ) {
        return { ...parsed, storageKey };
      }
      if (
        typeof parsed?.key === 'string'
        && /^[A-Za-z0-9._~:-]{16,128}$/.test(parsed.key)
      ) {
        const error = new Error(
          'A previous paid start has an unknown outcome. Recover that exact request before starting a different one.',
        );
        error.code = 'PAID_START_RECOVERY_REQUIRED';
        throw error;
      }
    } catch (error) {
      if (error?.code === 'PAID_START_RECOVERY_REQUIRED') throw error;
      // Replace malformed or inaccessible pending state below.
    }
  }
  const attempt = {
    endpoint,
    intentToken: token,
    key: createIdempotencyKey(),
    storageKey,
  };
  persistPaidStartAttempt(attempt);
  return attempt;
}

/** Persist non-secret retry metadata before a paid POST can be sent. */
export function persistPaidStartAttempt(attempt) {
  const storage = getSessionStorage();
  if (!storage || !attempt?.storageKey) return;
  const { storageKey, ...payload } = attempt;
  try {
    storage.setItem(storageKey, JSON.stringify(payload));
  } catch {
    // The live hook ref still protects retries while this page remains open.
  }
}

/** Clear only the exact pending attempt that reached a definitive outcome. */
export function clearPaidStartAttempt(attempt) {
  const storage = getSessionStorage();
  if (!storage || !attempt?.storageKey) return;
  try {
    const parsed = JSON.parse(storage.getItem(attempt.storageKey) || 'null');
    if (!parsed || parsed.key === attempt.key) {
      storage.removeItem(attempt.storageKey);
    }
  } catch {
    storage.removeItem(attempt.storageKey);
  }
}

/**
 * True only for same-origin `/api` URLs. Static files and cross-origin
 * requests must never receive the application access token.
 */
export function isSameOriginApiUrl(url) {
  try {
    const base =
      globalThis.location?.href ||
      'http://localhost/';
    const parsed = new URL(
      typeof url === 'string' ? url : url?.url,
      base,
    );
    const baseOrigin = new URL(base).origin;
    return parsed.origin === baseOrigin && (
      parsed.pathname === '/api' || parsed.pathname.startsWith('/api/')
    );
  } catch {
    return false;
  }
}

function withAccessHeader(url, options = {}) {
  if (!isSameOriginApiUrl(url)) return options;
  const token = getAccessToken();
  if (!token) return options;

  const headers = new Headers(options.headers || {});
  if (!headers.has(APP_ACCESS_TOKEN_HEADER)) {
    headers.set(APP_ACCESS_TOKEN_HEADER, token);
  }
  return { ...options, headers };
}

function notifyUnauthorized(url, response) {
  if (
    response.status !== 401 ||
    !isSameOriginApiUrl(url) ||
    typeof globalThis.dispatchEvent !== 'function' ||
    typeof globalThis.CustomEvent !== 'function'
  ) {
    return;
  }
  globalThis.dispatchEvent(new CustomEvent(APP_ACCESS_UNAUTHORIZED_EVENT));
}

/**
 * Extracts a human-readable error message from a failed response body.
 *
 * Understands FastAPI's `{ "detail": ... }` shape (string or validation array)
 * and falls back to raw text, then to `fallback`.
 *
 * @param {Response} response
 * @param {string} fallback
 * @returns {Promise<string>}
 */
export async function readErrorDetail(response, fallback) {
  const text = await response.text().catch(() => '');
  if (!text) return fallback;
  try {
    const data = JSON.parse(text);
    if (typeof data?.detail === 'string') return data.detail;
    if (data?.detail && typeof data.detail === 'object') {
      return data.detail.message || data.detail.code || fallback;
    }
    if (Array.isArray(data?.detail)) {
      return data.detail.map((item) => item?.msg || String(item)).join('; ');
    }
  } catch {
    // Body was not JSON; fall through to the raw text.
  }
  return text;
}

/**
 * fetch() that preserves AbortError and wraps other network failures.
 *
 * @param {string} url
 * @param {RequestInit} [options]
 * @param {AbortSignal} [signal]
 * @returns {Promise<Response>}
 */
export async function fetchWithAbort(url, options, signal) {
  try {
    const response = await fetch(url, {
      ...withAccessHeader(url, options),
      signal,
    });
    notifyUnauthorized(url, response);
    return response;
  } catch (err) {
    if (err.name === 'AbortError') throw err;
    throw new Error(`Network error: ${err.message}`, { cause: err });
  }
}

/**
 * Requests JSON and throws an Error with the parsed detail on any non-2xx.
 *
 * @param {string} url
 * @param {{ signal?: AbortSignal, fallbackError?: string } & RequestInit} [options]
 * @returns {Promise<any>} Parsed JSON body.
 */
export async function requestJson(url, { signal, fallbackError, ...options } = {}) {
  const response = await fetchWithAbort(url, options, signal);
  if (!response.ok) {
    throw new Error(await readErrorDetail(response, fallbackError || `Request failed (${response.status})`));
  }
  return response.json();
}

/**
 * Download a protected same-origin API response through the shared token
 * transport, then trigger a browser save without putting credentials in URLs.
 *
 * @param {string} url
 * @param {string} filename
 * @returns {Promise<void>}
 */
export async function downloadApiFile(url, filename) {
  const response = await fetchWithAbort(url);
  if (!response.ok) {
    throw new Error(
      await readErrorDetail(response, `Download failed (${response.status})`),
    );
  }
  const createObjectURL = globalThis.URL?.createObjectURL;
  const revokeObjectURL = globalThis.URL?.revokeObjectURL;
  if (
    typeof createObjectURL !== 'function' ||
    typeof globalThis.document?.createElement !== 'function'
  ) {
    throw new Error('This browser cannot save downloaded files.');
  }
  const objectUrl = createObjectURL(await response.blob());
  const anchor = globalThis.document.createElement('a');
  anchor.href = objectUrl;
  anchor.download =
    String(filename || 'download').split(/[\\/]/).pop() || 'download';
  anchor.hidden = true;
  globalThis.document.body.appendChild(anchor);
  try {
    anchor.click();
  } finally {
    anchor.remove();
    globalThis.setTimeout?.(() => revokeObjectURL?.(objectUrl), 0);
  }
}

/**
 * Best-effort JSON GET: returns parsed JSON, or null on any network/HTTP/parse
 * failure. Used for optional startup config where the UI degrades gracefully.
 *
 * @param {string} url
 * @param {RequestInit} [options]
 * @returns {Promise<any | null>}
 */
export async function getJsonOrNull(url, options = {}) {
  try {
    const response = await fetchWithAbort(
      url,
      { cache: 'no-store', ...options },
      options.signal,
    );
    if (!response.ok) return null;
    return await response.json();
  } catch {
    return null;
  }
}
