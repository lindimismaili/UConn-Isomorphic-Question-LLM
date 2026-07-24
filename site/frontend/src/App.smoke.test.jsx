/* @vitest-environment jsdom */

/**
 * App smoke test — mounts the consumer chat-first App and asserts the core
 * chat surface (rail + transcript + composer) renders without exposing the
 * protected developer-tools panel.
 * Exercises the real integration path: App → ChatConversation → ui/ primitives
 * → transcript derivation, with all network calls stubbed. Not a full behavior
 * test — a crash guard for the Wave 2 restructure.
 */

import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App.jsx';
import { loadAgentRegistry } from './hooks/useAgentRegistry.js';

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const PIPELINE_KEYS = [
  'setup',
  'input_guardrail',
  'normalizer',
  'extracter',
  'content_guardrail',
  'verifier',
  'generator',
  'latexifier',
  'evaluation',
  'output',
];

const AGENTS_PAYLOAD = Object.fromEntries(
  PIPELINE_KEYS.map((key, index) => [
    key,
    {
      sequence: index,
      name: key,
      description: `${key} node`,
      model: 'gpt-5.5',
      reasoning: 'medium',
      kind:
        key === 'setup' || key === 'output'
          ? 'runtime'
          : key.endsWith('guardrail')
            ? 'guardrail'
            : 'agent',
    },
  ]),
);

let root;
let container;

beforeEach(() => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url) => {
      const u = String(url);
      if (u.includes('/api/agents')) return { ok: true, json: async () => AGENTS_PAYLOAD };
      if (u.includes('/version.json')) return { ok: true, json: async () => ({ version: '1.0' }) };
      if (u.includes('/api/openai/status')) return { ok: true, json: async () => ({ openai_down: false }) };
      if (u.includes('/api/dropdown')) return { ok: true, json: async () => ({ items: [] }) };
      return { ok: true, json: async () => ({}) };
    }),
  );
});

afterEach(() => {
  if (root) act(() => root.unmount());
  container?.remove();
  root = null;
  container = null;
  localStorage.removeItem('uiMode');
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

async function flush() {
  await act(async () => {
    await Promise.resolve();
  });
}

describe('App (chat-first) smoke', () => {
  it('mounts the consumer chat shell without developer controls', async () => {
    await loadAgentRegistry();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    await act(async () => {
      root.render(<App />);
    });
    await flush();

    // Chat-first surface.
    expect(container.querySelector('.chat-shell')).toBeTruthy();
    const transcript = container.querySelector('.chat-transcript');
    expect(transcript).toBeTruthy();
    expect(transcript.className).toContain('is-empty');
    expect(container.querySelector('.composer')).toBeTruthy();
    expect(container.querySelector('textarea.composer-input')).toBeTruthy();

    // Site-mode dropdown offers only consumer-facing modes.
    const modeTrigger = container.querySelector('.mode-select .dropdown-trigger');
    expect(modeTrigger).toBeTruthy();
    expect(modeTrigger.textContent).toContain('User Mode');

    // All 10 registry nodes render in the stepper (incl. guardrail + runtime).
    expect(container.querySelectorAll('.stepper-node')).toHaveLength(PIPELINE_KEYS.length);
    expect(container.querySelector('.stepper-node[data-node-kind="guardrail"]')).toBeTruthy();
    expect(container.querySelector('.stepper-node[data-node-kind="runtime"]')).toBeTruthy();

    // Developer controls live only in the protected Admin Dashboard.
    expect(container.querySelector('.workspace-side')).toBeNull();
    expect(container.querySelector('.developer-tools-card')).toBeNull();

    // Empty conversation state before any run.
    expect(container.querySelector('.chat-empty')).toBeTruthy();
    expect(container.querySelector('.chat-empty .chat-empty-icon')).toBeTruthy();
    expect(container.querySelector('.chat-empty button').textContent).toContain('How it works');
  });
});
