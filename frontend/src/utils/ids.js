/**
 * Returns a collision-resistant id for React keys and client-side records.
 *
 * Prefers crypto.randomUUID() and falls back to a base-36 random string in
 * environments where it is unavailable.
 *
 * @returns {string}
 */
export function generateId() {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return Math.random().toString(36).slice(2, 11);
}
