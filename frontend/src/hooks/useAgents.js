import { useEffect } from 'react';

/** Maps backend naming variants onto the frontend agent keys. */
const AGENT_KEY_ALIASES = {
  extractor: 'extracter',
  latex: 'latexifier',
  latex_formatter: 'latexifier',
  latexformatter: 'latexifier',
};

/** Agent keys the frontend knows how to render and update. */
const KNOWN_AGENT_KEYS = new Set([
  'setup',
  'normalizer',
  'extracter',
  'verifier',
  'generator',
  'latexifier',
  'evaluation',
  'output',
]);

/**
 * Normalizes agent metadata returned by `/api/agents` or the bundled
 * `agents.json` asset into the internal agent-keyed shape used by App.jsx.
 *
 * @param {unknown} raw - API payload or JSON asset contents.
 * @returns {Record<string, object> | null} Normalized agent config map, or
 *   null when the payload cannot be used.
 */
function normalizeAgentsConfig(raw) {
  if (!raw || typeof raw !== 'object') return null;
  const source = raw.agents && typeof raw.agents === 'object' ? raw.agents : raw;
  const normalized = {};

  for (const [key, value] of Object.entries(source)) {
    if (!value || typeof value !== 'object') continue;
    const mappedKey = AGENT_KEY_ALIASES[key] || key;
    if (!KNOWN_AGENT_KEYS.has(mappedKey)) continue;
    normalized[mappedKey] = value;
  }

  return Object.keys(normalized).length > 0 ? normalized : null;
}

/**
 * Fetches a JSON document from a static or API endpoint.
 *
 * @param {string} url - Endpoint path to request.
 * @returns {Promise<object | null>} Parsed JSON on success, otherwise null.
 *   Network and parse failures are swallowed so startup can continue.
 */
async function fetchJson(url) {
  try {
    const res = await fetch(url, { cache: 'no-store' });
    if (!res.ok) return null;
    return await res.json();
  } catch (err) {
    console.debug(`Failed to load ${url}:`, err);
    return null;
  }
}

/**
 * Tries the runtime agent config endpoints in priority order.
 *
 * Touches: `/api/agents`, `/agents.json`, `/data/agents.json`.
 *
 * @returns {Promise<Record<string, object> | null>} The first usable config.
 */
async function fetchAgentsConfig() {
  const endpoints = ['/api/agents', '/agents.json', '/data/agents.json'];
  for (const endpoint of endpoints) {
    const data = await fetchJson(endpoint);
    const normalized = normalizeAgentsConfig(data);
    if (normalized) return normalized;
  }
  return null;
}

/**
 * Loads the shared agent config on mount and dispatches `AGENTS_LOADED`.
 *
 * @param {Function} dispatch - App reducer dispatcher.
 * @sideEffects Performs one or more fetches for the agent config asset/API.
 */
export function useAgents(dispatch) {
  useEffect(() => {
    let cancelled = false;
    fetchAgentsConfig().then((config) => {
      if (!cancelled && config) {
        dispatch({ type: 'AGENTS_LOADED', config });
      }
    });
    return () => {
      cancelled = true;
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
}
