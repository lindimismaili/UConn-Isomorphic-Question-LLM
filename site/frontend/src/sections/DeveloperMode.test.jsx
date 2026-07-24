/* @vitest-environment jsdom */

import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import DeveloperMode from './DeveloperMode.jsx';
import { loadAgentRegistry } from '../hooks/useAgentRegistry.js';

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const AGENTS_PAYLOAD = {
  setup: { sequence: 0, name: 'Setup', kind: 'runtime' },
  normalizer: { sequence: 1, name: 'Normalizer', kind: 'agent' },
  output: { sequence: 2, name: 'Output', kind: 'runtime' },
};

let root;
let container;

beforeEach(() => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url) => {
      const request = String(url);
      if (request.includes('/api/agents')) return { ok: true, json: async () => AGENTS_PAYLOAD };
      if (request.includes('/api/dropdown')) return { ok: true, json: async () => ({ items: [] }) };
      return { ok: true, json: async () => ({}) };
    }),
  );
});

afterEach(() => {
  if (root) act(() => root.unmount());
  container?.remove();
  root = null;
  container = null;
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('DeveloperMode', () => {
  it('renders the complete former Dev mode workspace behind the admin surface', async () => {
    await loadAgentRegistry();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    await act(async () => {
      root.render(<DeveloperMode />);
      await Promise.resolve();
    });

    expect(container.querySelector('.chat-shell')).toBeTruthy();
    const side = container.querySelector('.workspace-side');
    expect(side).toBeTruthy();
    expect(side.querySelector('.developer-tools-card')).toBeTruthy();
    expect(side.querySelectorAll('.developer-tools-row .developer-tool-btn')).toHaveLength(4);
    expect(side.querySelector('.developer-tools-row .developer-toggle')).toBeTruthy();
    expect(side.querySelector('.log-frame')).toBeTruthy();
  });
});
