import { useEffect } from 'react';

async function fetchAgentsConfig() {
  try {
    const res = await fetch('/api/agents');
    if (res.ok) return await res.json();
  } catch {}
  try {
    const res = await fetch('/data/agents.json');
    if (res.ok) return await res.json();
  } catch {}
  return null;
}

/**
 * Loads agents config from the server on mount and dispatches AGENTS_LOADED.
 */
export function useAgents(dispatch) {
  useEffect(() => {
    fetchAgentsConfig().then((config) => {
      if (config) {
        dispatch({ type: 'AGENTS_LOADED', config });
      }
    });
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
}
