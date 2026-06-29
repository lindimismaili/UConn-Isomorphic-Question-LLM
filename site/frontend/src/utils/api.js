/**
 * Shared HTTP client for the frontend.
 *
 * One place for: AbortError-preserving fetch, FastAPI `{detail}` error parsing,
 * and the two response contracts the app needs — "throw on failure" (most calls)
 * and "best-effort, return null on failure" (startup config loads).
 */

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
    return await fetch(url, { ...options, signal });
  } catch (err) {
    if (err.name === 'AbortError') throw err;
    throw new Error(`Network error: ${err.message}`);
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
 * Best-effort JSON GET: returns parsed JSON, or null on any network/HTTP/parse
 * failure. Used for optional startup config where the UI degrades gracefully.
 *
 * @param {string} url
 * @param {RequestInit} [options]
 * @returns {Promise<any | null>}
 */
export async function getJsonOrNull(url, options = {}) {
  try {
    const response = await fetch(url, { cache: 'no-store', ...options });
    if (!response.ok) return null;
    return await response.json();
  } catch {
    return null;
  }
}
