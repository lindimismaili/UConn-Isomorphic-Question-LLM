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
import App, { ActiveBatchNotice } from './App.jsx';
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
let workflowStartForms;

beforeEach(() => {
  workflowStartForms = [];
  sessionStorage.clear();
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url, options = {}) => {
      const u = String(url);
      if (u.includes('/api/public-config')) {
        return {
          ok: true,
          json: async () => ({
            authRequired: false,
            donationUrl: 'https://www.paypal.com/donate/example',
            createDataPolicy: { maxEntries: 3 },
          }),
        };
      }
      if (u.includes('/api/readiness')) {
        return {
          ok: true,
          json: async () => ({
            authenticated: true,
            openaiApiKeyConfigured: true,
          }),
        };
      }
      if (u.includes('/api/agents')) return { ok: true, json: async () => AGENTS_PAYLOAD };
      if (u.includes('/version.json')) return { ok: true, json: async () => ({ version: '1.0' }) };
      if (u.includes('/api/openai/status')) return { ok: true, json: async () => ({ openai_down: false }) };
      if (u.includes('/api/dropdown')) return { ok: true, json: async () => ({ items: [] }) };
      if (u === '/api/workflow/start') {
        workflowStartForms.push(options.body);
        return {
          ok: true,
          status: 200,
          headers: new Headers(),
          json: async () => ({ workflowId: `workflow-${workflowStartForms.length}` }),
          text: async () => '',
        };
      }
      if (u.includes('/api/workflow/') && u.endsWith('/status')) {
        return {
          ok: true,
          status: 200,
          headers: new Headers(),
          json: async () => ({ status: 'running', agents: {} }),
          text: async () => '',
        };
      }
      if (u.includes('/api/workflow/') && u.endsWith('/result')) {
        return {
          ok: true,
          status: 200,
          headers: new Headers(),
          json: async () => ({ outputFolder: 'run', files: [], result: {} }),
          text: async () => '',
        };
      }
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
  sessionStorage.clear();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

async function flush() {
  await act(async () => {
    await Promise.resolve();
  });
}

describe('App (chat-first) smoke', () => {
  it('keeps a compact Stop control available for active background batches', async () => {
    const stop = vi.fn();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    await act(async () => {
      root.render(
        <ActiveBatchNotice
          label="Batch Evaluation"
          state={{
            status: 'running',
            isStarting: false,
            isRunning: true,
            totalFiles: 4,
            doneFiles: 2,
          }}
          onStop={stop}
          onRecover={() => {}}
        />,
      );
    });

    expect(container.textContent).toContain('Batch Evaluation');
    expect(container.textContent).toContain('2/4 complete');
    const stopButton = [...container.querySelectorAll('button')]
      .find((button) => button.textContent.trim() === 'Stop');
    expect(stopButton).toBeTruthy();
    await act(async () => {
      stopButton.click();
    });
    expect(stop).toHaveBeenCalledOnce();
  });

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
    expect(modeTrigger.title).toContain('does not grant access');

    // Main-site donation action points at the configurable PayPal recipient.
    const headerDonationLink = container.querySelector('.header-actions a');
    expect(headerDonationLink).toBeTruthy();
    expect(headerDonationLink.textContent).toContain('Donate via PayPal');
    expect(headerDonationLink.getAttribute('target')).toBe('_blank');
    expect(new URL(headerDonationLink.href).searchParams.get('business')).toBe(
      'Lindim.ismaili0001@gmail.com',
    );

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

    // The footer keeps the server-validated donation link, separate from the
    // header action above.
    const footerDonationLink = container.querySelector(
      '.footer-secondary a[href*="paypal.com"]',
    );
    expect(footerDonationLink?.textContent).toContain('Support this project');
    expect(footerDonationLink?.target).toBe('_blank');
    expect(footerDonationLink?.rel).toContain('noopener');
  });
});
