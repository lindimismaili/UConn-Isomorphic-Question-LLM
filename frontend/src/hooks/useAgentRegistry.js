/**
 * Agent registry module — single source of truth for agent data in the frontend.
 *
 * Fetches /api/agents once, caches it at module level, and exposes derived
 * constants so components never hardcode agent names or sequences.
 *
 * Usage:
 *   import { loadAgentRegistry, useAgentRegistry, getDerivedRegistry } from './useAgentRegistry';
 *
 *   // In a component:
 *   const registry = useAgentRegistry();  // re-renders when loaded
 *   const { pipelineKeys, agentLabels } = registry ?? {};
 *
 *   // In non-hook code (reducers, callbacks):
 *   const { nextAgentByKey } = getDerivedRegistry() ?? {};
 *
 *   // On app mount (call once):
 *   await loadAgentRegistry();
 */

import { useEffect, useState } from 'react';

// ── Module-level cache ────────────────────────────────────────────────────────
let _registry = null;
let _derived = null;
let _promise = null;
const _subscribers = new Set();

function _computeDerived(registry) {
  const entries = Object.entries(registry).sort(
    (a, b) => (a[1].sequence ?? 0) - (b[1].sequence ?? 0),
  );
  const pipelineKeys = entries.map(([k]) => k);

  return {
    registry,
    pipelineKeys,
    agentLabels: Object.fromEntries(entries.map(([k, v]) => [k, v.name || k])),
    countLabels: Object.fromEntries(
      entries.filter(([, v]) => v.countLabel).map(([k, v]) => [k, v.countLabel]),
    ),
    aliasesMap: Object.fromEntries(
      entries.flatMap(([k, v]) => (v.aliases || []).map((a) => [a, k])),
    ),
    knownKeys: new Set(pipelineKeys),
    nextAgentByKey: Object.fromEntries(
      pipelineKeys.slice(0, -1).map((k, i) => [k, pipelineKeys[i + 1]]),
    ),
    inputAgentKeys: entries
      .filter(([, v]) => v.isStoppable || v.acceptsSourceFiles)
      .map(([k]) => k),
    sourceFileAgentKeys: new Set(
      entries.filter(([, v]) => v.acceptsSourceFiles).map(([k]) => k),
    ),
  };
}

// ── Public API ────────────────────────────────────────────────────────────────

/**
 * Fetch and cache the agent registry from /api/agents.
 * Idempotent — safe to call multiple times; only one fetch is ever made.
 * Resolves with the raw registry dict, or null on failure.
 *
 * @returns {Promise<object|null>}
 */
export async function loadAgentRegistry() {
  if (_registry) return _registry;
  if (_promise) return _promise;

  _promise = (async () => {
    try {
      const res = await fetch('/api/agents');
      if (!res.ok) return null;
      const raw = await res.json();
      const agentEntries = Object.entries(raw)
        .filter(([k, v]) => !k.startsWith('_') && v && typeof v === 'object')
        .sort((a, b) => (a[1].sequence ?? 0) - (b[1].sequence ?? 0));
      _registry = Object.fromEntries(agentEntries);
      _derived = _computeDerived(_registry);
      _subscribers.forEach((cb) => cb());
      return _registry;
    } catch {
      return null;
    }
  })();

  return _promise;
}

/**
 * Synchronously return computed derived constants, or null before the registry loads.
 * Safe to call from non-hook contexts such as reducers.
 *
 * @returns {{
 *   registry: object,
 *   pipelineKeys: string[],
 *   agentLabels: object,
 *   countLabels: object,
 *   aliasesMap: object,
 *   knownKeys: Set<string>,
 *   nextAgentByKey: object,
 *   inputAgentKeys: string[],
 *   sourceFileAgentKeys: Set<string>,
 * } | null}
 */
export function getDerivedRegistry() {
  return _derived;
}

/**
 * React hook that returns the derived registry and triggers a re-render
 * when the registry finishes loading.
 *
 * @returns {ReturnType<typeof getDerivedRegistry>}
 */
export function useAgentRegistry() {
  const [, forceUpdate] = useState(0);

  useEffect(() => {
    if (_derived) return;
    const cb = () => forceUpdate((n) => n + 1);
    _subscribers.add(cb);
    return () => _subscribers.delete(cb);
  }, []);

  return _derived;
}
