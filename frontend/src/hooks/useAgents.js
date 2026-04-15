import { useEffect } from 'react';

const AGENT_KEY_ALIASES = {
  extractor: 'extracter',
  latex_formatter: 'latex',
  latexformatter: 'latex',
};

const KNOWN_AGENT_KEYS = new Set([
  'setup',
  'normalizer',
  'extracter',
  'verifier',
  'generator',
  'latex',
  'evaluation',
]);

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
 * Loads agents config from the server on mount and dispatches AGENTS_LOADED.
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
