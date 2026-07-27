import { useEffect } from 'react';

import { loadAgentRegistry } from './useAgentRegistry';

/**
 * Loads the shared agent config on mount via the registry and dispatches
 * AGENTS_LOADED. Replaces the previous fetch+normalize logic; the registry
 * module owns the fetch so all consumers share a single cached response.
 *
 * @param {Function} dispatch - App reducer dispatcher.
 * @param {boolean} enabled - Wait until the application access gate is open.
 */
export function useAgents(dispatch, enabled = true) {
  useEffect(() => {
    if (!enabled) return undefined;
    let cancelled = false;
    loadAgentRegistry().then((config) => {
      if (!cancelled && config) {
        dispatch({ type: 'AGENTS_LOADED', config });
      }
    });
    return () => {
      cancelled = true;
    };
  }, [dispatch, enabled]);
}
