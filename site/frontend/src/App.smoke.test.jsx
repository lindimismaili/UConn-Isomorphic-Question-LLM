/* @vitest-environment jsdom */

/**
 * App smoke test — mounts the whole chat-first App (in dev mode, so the
 * developer-tools side panel renders) and asserts the core chat surface
 * (rail + transcript + composer) and the side panel render without throwing.
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
  // Dev mode shows the developer-tools side panel this test asserts on.
  localStorage.setItem('uiMode', 'dev');
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
  it('mounts the chat shell, stepper, composer, and dev-tools side panel', async () => {
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
    expect(container.querySelector('.chat-transcript')).toBeTruthy();
    expect(container.querySelector('.composer')).toBeTruthy();
    expect(container.querySelector('textarea.composer-input')).toBeTruthy();

    // Site-mode dropdown sits in the header, currently on Dev Mode.
    const modeSelect = container.querySelector('select.mode-select');
    expect(modeSelect).toBeTruthy();
    expect(modeSelect.value).toBe('dev');

    // All 10 registry nodes render in the stepper (incl. guardrail + runtime).
    expect(container.querySelectorAll('.stepper-node')).toHaveLength(PIPELINE_KEYS.length);
    expect(container.querySelector('.stepper-node[data-node-kind="guardrail"]')).toBeTruthy();
    expect(container.querySelector('.stepper-node[data-node-kind="runtime"]')).toBeTruthy();

    // Developer tools preserved in the side panel (all four tiles + the toggle +
    // the log all render — a DOM guard against the side-panel clipping bug).
    const side = container.querySelector('.workspace-side');
    expect(side.querySelector('.developer-tools-card')).toBeTruthy();
    expect(side.querySelectorAll('.developer-tools-row .developer-tool-btn')).toHaveLength(4);
    expect(side.querySelector('.developer-tools-row .developer-toggle')).toBeTruthy();
    expect(side.querySelector('.log-frame')).toBeTruthy();

    // Empty conversation state before any run.
    expect(container.querySelector('.chat-empty')).toBeTruthy();
  });
});
