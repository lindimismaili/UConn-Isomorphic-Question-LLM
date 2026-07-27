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
  // Dev mode shows the developer-tools side panel this test asserts on.
  localStorage.setItem('uiMode', 'dev');
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

    // Site-mode dropdown (custom Dropdown) sits in the header, currently on Dev Mode.
    const modeTrigger = container.querySelector('.mode-select .dropdown-trigger');
    expect(modeTrigger).toBeTruthy();
    expect(modeTrigger.textContent).toContain('Dev View');
    expect(modeTrigger.title).toContain('does not grant access');

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

    const donationLink = container.querySelector('a[href*="paypal.com"]');
    expect(donationLink?.textContent).toContain('Support this project');
    expect(donationLink?.target).toBe('_blank');
    expect(donationLink?.rel).toContain('noopener');

    const createDataButton = Array.from(side.querySelectorAll('button'))
      .find((button) => button.textContent.includes('Create Test Data'));
    await act(async () => {
      createDataButton.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    const createDataModal = document.querySelector('.create-data-modal');
    expect(createDataModal?.textContent).toContain('up to 3 complete generation workflows');
    expect(createDataModal?.textContent).toContain(
      'Rubric evaluation is not included; use Batch Evaluation separately.',
    );
    const startButton = Array.from(document.querySelectorAll('.modal button'))
      .find((button) => button.textContent.trim() === 'Start');
    expect(startButton?.disabled).toBe(true);
  });

  it('drops next-run evaluation approval when leaving Dev View', async () => {
    await loadAgentRegistry();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    await act(async () => {
      root.render(<App />);
    });
    await flush();

    const evaluationToggle = container.querySelector(
      '.developer-tools-row .developer-toggle input[type="checkbox"]',
    );
    expect(evaluationToggle).toBeTruthy();
    await act(async () => {
      evaluationToggle.click();
    });
    expect(evaluationToggle.checked).toBe(true);

    const modeTrigger = container.querySelector('.mode-select .dropdown-trigger');
    await act(async () => {
      modeTrigger.click();
    });
    const userOption = [...document.body.querySelectorAll('[role="option"]')]
      .find((option) => option.textContent.includes('User View'));
    await act(async () => {
      userOption.click();
    });

    expect(container.querySelector('.workspace-side')).toBeNull();

    const textarea = container.querySelector('textarea.composer-input');
    await act(async () => {
      const valueSetter = Object.getOwnPropertyDescriptor(
        HTMLTextAreaElement.prototype,
        'value',
      ).set;
      valueSetter.call(textarea, 'Generate one question');
      textarea.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await act(async () => {
      container.querySelector('.composer-send').click();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(workflowStartForms).toHaveLength(1);
    expect(workflowStartForms[0].get('runEvaluation')).toBe('false');
    expect(workflowStartForms[0].get('confirmEvaluationCost')).toBe('false');
  });

  it('consumes evaluation approval for exactly one Dev View run', async () => {
    await loadAgentRegistry();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    await act(async () => {
      root.render(<App />);
    });
    await flush();

    const evaluationToggle = container.querySelector(
      '.developer-tools-row .developer-toggle input[type="checkbox"]',
    );
    await act(async () => {
      evaluationToggle.click();
    });

    const textarea = container.querySelector('textarea.composer-input');
    await act(async () => {
      const valueSetter = Object.getOwnPropertyDescriptor(
        HTMLTextAreaElement.prototype,
        'value',
      ).set;
      valueSetter.call(textarea, 'Evaluate this run');
      textarea.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await act(async () => {
      container.querySelector('.composer-send').click();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(workflowStartForms).toHaveLength(1);
    expect(workflowStartForms[0].get('runEvaluation')).toBe('true');
    expect(workflowStartForms[0].get('confirmEvaluationCost')).toBe('true');
    expect(evaluationToggle.checked).toBe(false);
  });
});
