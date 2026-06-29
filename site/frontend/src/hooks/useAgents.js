import { useEffect } from 'react';

import { loadAgentRegistry } from './useAgentRegistry';

/**
 * Loads the shared agent config on mount via the registry and dispatches
 * AGENTS_LOADED. Replaces the previous fetch+normalize logic; the registry
 * module owns the fetch so all consumers share a single cached response.
 *
 * @param {Function} dispatch - App reducer dispatcher.
 */
export function useAgents(dispatch) {
  useEffect(() => {
    let cancelled = false;
    loadAgentRegistry().then((config) => {
      if (!cancelled && config) {
        dispatch({ type: 'AGENTS_LOADED', config });
      }
    });
    return () => {
      cancelled = true;
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
}
