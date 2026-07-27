/* @vitest-environment jsdom */

import { act, useEffect } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  ACTIVE_WORKFLOW_STORAGE_KEY,
  WORKFLOW_RETRY_DELAYS_MS,
  isRetryableWorkflowReadStatus,
  useWorkflow,
  workflowRetryDelay,
} from './useWorkflow.js';
import {
  IDEMPOTENCY_KEY_HEADER,
  PAID_START_ATTEMPT_STORAGE_PREFIX,
} from '../utils/api.js';

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const REGISTRY = {
  setup: {
    sequence: 0,
    name: 'Setup',
    kind: 'runtime',
  },
  normalizer: {
    sequence: 1,
    name: 'Normalizer',
    kind: 'agent',
  },
};

function jsonResponse(payload, status = 200, headers = {}) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers(headers),
    json: async () => payload,
    text: async () => JSON.stringify(payload),
  };
}

let root;
let container;
let workflowApi;

function Harness({ dispatch, onReady }) {
  const api = useWorkflow(dispatch);
  useEffect(() => {
    onReady(api);
  }, [api, onReady]);
  return null;
}

async function mount(dispatch) {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  const onReady = (api) => {
    workflowApi = api;
  };
  await act(async () => {
    root.render(<Harness dispatch={dispatch} onReady={onReady} />);
  });
}

async function flush(count = 6) {
  await act(async () => {
    for (let index = 0; index < count; index += 1) {
      await Promise.resolve();
    }
  });
}

beforeEach(() => {
  vi.useRealTimers();
  sessionStorage.clear();
  workflowApi = null;
});

afterEach(() => {
  if (root) {
    act(() => root.unmount());
  }
  container?.remove();
  root = null;
  container = null;
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.useRealTimers();
  sessionStorage.clear();
});

describe('useWorkflow hardening', () => {
  it('retries only transient workflow-read statuses with capped backoff', () => {
    for (const status of [408, 425, 429, 500, 503, 599]) {
      expect(isRetryableWorkflowReadStatus(status)).toBe(true);
    }
    for (const status of [200, 400, 401, 403, 404, 409, 600]) {
      expect(isRetryableWorkflowReadStatus(status)).toBe(false);
    }

    expect(workflowRetryDelay(0)).toBe(WORKFLOW_RETRY_DELAYS_MS[0]);
    expect(workflowRetryDelay(1)).toBe(WORKFLOW_RETRY_DELAYS_MS[1]);
    expect(workflowRetryDelay(2)).toBe(WORKFLOW_RETRY_DELAYS_MS[2]);
    expect(workflowRetryDelay(3)).toBe(WORKFLOW_RETRY_DELAYS_MS[3]);
    expect(workflowRetryDelay(99)).toBe(WORKFLOW_RETRY_DELAYS_MS.at(-1));
    expect(workflowRetryDelay(
      0,
      jsonResponse({}, 429, { 'Retry-After': '60' }),
    )).toBe(WORKFLOW_RETRY_DELAYS_MS.at(-1));
  });

  it('starts with the explicit uploaded file ID list', async () => {
    const dispatch = vi.fn();
    let startForm;
    vi.stubGlobal('fetch', vi.fn(async (url, options = {}) => {
      const path = String(url);
      if (path === '/api/agents') return jsonResponse(REGISTRY);
      if (path === '/api/upload') {
        return jsonResponse({
          sessionId: '07-27-2026/upload-1',
          files: [{ fileId: 'file-abc' }],
        });
      }
      if (path === '/api/workflow/start') {
        startForm = options.body;
        return jsonResponse({ workflowId: 'wf-file-ids' });
      }
      if (path === '/api/workflow/wf-file-ids/status') {
        return jsonResponse({
          status: 'completed',
          agents: {
            normalizer: {
              status: 'completed',
              data: { executionTime: 1, output: {} },
            },
          },
        });
      }
      if (path === '/api/workflow/wf-file-ids/result') {
        return jsonResponse({ outputFolder: 'run', files: [], result: {} });
      }
      throw new Error(`Unexpected request: ${path}`);
    }));
    await mount(dispatch);

    const sourceFile = new File(['source'], 'source.txt', { type: 'text/plain' });
    await act(async () => {
      await workflowApi.run(
        'Make a variation',
        [{ id: 'local-1', name: sourceFile.name, file: sourceFile }],
      );
    });

    expect(startForm).toBeInstanceOf(FormData);
    expect(JSON.parse(startForm.get('fileIds'))).toEqual(['file-abc']);
    expect(startForm.get('sessionId')).toBe('07-27-2026/upload-1');
    expect(sessionStorage.getItem(ACTIVE_WORKFLOW_STORAGE_KEY)).toBeNull();
  });

  it('reuses one persisted key and upload after an ambiguous lost response', async () => {
    const dispatch = vi.fn();
    let uploadCalls = 0;
    let startCalls = 0;
    const startKeys = [];
    vi.stubGlobal('fetch', vi.fn(async (url, options = {}) => {
      const path = String(url);
      if (path === '/api/agents') return jsonResponse(REGISTRY);
      if (path === '/api/upload') {
        uploadCalls += 1;
        return jsonResponse({
          sessionId: 'upload-retry',
          files: [{ fileId: 'file-retry' }],
        });
      }
      if (path === '/api/workflow/start') {
        startCalls += 1;
        startKeys.push(new Headers(options.headers).get(IDEMPOTENCY_KEY_HEADER));
        if (startCalls === 1) {
          throw new TypeError('connection reset after acceptance');
        }
        return jsonResponse({ workflowId: 'wf-idempotent' });
      }
      if (path === '/api/workflow/wf-idempotent/status') {
        return jsonResponse({ status: 'completed', agents: {} });
      }
      if (path === '/api/workflow/wf-idempotent/result') {
        return jsonResponse({ outputFolder: 'run', files: [], result: {} });
      }
      throw new Error(`Unexpected request: ${path}`);
    }));
    await mount(dispatch);

    const sourceFile = new File(['source'], 'source.txt', {
      type: 'text/plain',
      lastModified: 123,
    });
    const files = [{ id: 'local-1', name: sourceFile.name, file: sourceFile }];
    await act(async () => {
      await workflowApi.run('Same paid intent', files);
    });

    expect(startCalls).toBe(1);
    expect(uploadCalls).toBe(1);
    expect(sessionStorage.getItem(
      `${PAID_START_ATTEMPT_STORAGE_PREFIX}workflow`,
    )).toContain(startKeys[0]);

    act(() => root.unmount());
    container.remove();
    root = null;
    container = null;
    await mount(dispatch);

    await act(async () => {
      await workflowApi.run('Same paid intent', files);
    });

    expect(startCalls).toBe(2);
    expect(uploadCalls).toBe(1);
    expect(startKeys[1]).toBe(startKeys[0]);
    expect(sessionStorage.getItem(
      `${PAID_START_ATTEMPT_STORAGE_PREFIX}workflow`,
    )).toBeNull();
  });

  it('keeps a normal-start key after a 200 response with invalid JSON', async () => {
    const dispatch = vi.fn();
    const keys = [];
    let starts = 0;
    vi.stubGlobal('fetch', vi.fn(async (url, options = {}) => {
      const path = String(url);
      if (path === '/api/agents') return jsonResponse(REGISTRY);
      if (path === '/api/workflow/start') {
        starts += 1;
        keys.push(new Headers(options.headers).get(IDEMPOTENCY_KEY_HEADER));
        if (starts === 1) {
          return {
            ...jsonResponse({}),
            json: async () => {
              throw new SyntaxError('truncated JSON');
            },
          };
        }
        return jsonResponse({ workflowId: 'wf-json-recovered' });
      }
      if (path === '/api/workflow/wf-json-recovered/status') {
        return jsonResponse({ status: 'completed', agents: {} });
      }
      if (path === '/api/workflow/wf-json-recovered/result') {
        return jsonResponse({ outputFolder: 'run', files: [], result: {} });
      }
      throw new Error(`Unexpected request: ${path}`);
    }));
    await mount(dispatch);

    await act(async () => {
      await workflowApi.run('Recover JSON', []);
    });
    expect(starts).toBe(1);
    await act(async () => {
      await workflowApi.run('Recover JSON', []);
    });

    expect(starts).toBe(2);
    expect(keys[1]).toBe(keys[0]);
  });

  it('keeps an agent-input key after a 200 response without a workflow ID', async () => {
    const dispatch = vi.fn();
    const keys = [];
    let starts = 0;
    vi.stubGlobal('fetch', vi.fn(async (url, options = {}) => {
      const path = String(url);
      if (path === '/api/agents') return jsonResponse(REGISTRY);
      if (path === '/api/workflow/start-from-agent-input') {
        starts += 1;
        keys.push(new Headers(options.headers).get(IDEMPOTENCY_KEY_HEADER));
        return starts === 1
          ? jsonResponse({})
          : jsonResponse({ workflowId: 'wf-agent-recovered' });
      }
      if (path === '/api/workflow/wf-agent-recovered/status') {
        return jsonResponse({ status: 'completed', agents: {} });
      }
      if (path === '/api/workflow/wf-agent-recovered/result') {
        return jsonResponse({ outputFolder: 'run', files: [], result: {} });
      }
      throw new Error(`Unexpected request: ${path}`);
    }));
    await mount(dispatch);
    const input = new File(['{}'], 'input.json', { type: 'application/json' });

    await act(async () => {
      await workflowApi.runFromAgentInput('generator', input);
    });
    expect(starts).toBe(1);
    await act(async () => {
      await workflowApi.runFromAgentInput('generator', input);
    });

    expect(starts).toBe(2);
    expect(keys[1]).toBe(keys[0]);
  });

  it('keeps evaluation opt-in explicit for agent-input replays', async () => {
    const dispatch = vi.fn();
    const submitted = [];
    vi.stubGlobal('fetch', vi.fn(async (url, options = {}) => {
      const path = String(url);
      if (path === '/api/agents') return jsonResponse(REGISTRY);
      if (path === '/api/workflow/start-from-agent-input') {
        submitted.push(options.body);
        return jsonResponse({ workflowId: `wf-agent-${submitted.length}` });
      }
      if (path.startsWith('/api/workflow/wf-agent-') && path.endsWith('/status')) {
        return jsonResponse({ status: 'completed', agents: {} });
      }
      if (path.startsWith('/api/workflow/wf-agent-') && path.endsWith('/result')) {
        return jsonResponse({ outputFolder: 'run', files: [], result: {} });
      }
      throw new Error(`Unexpected request: ${path}`);
    }));
    await mount(dispatch);

    const input = new File(['{}'], 'input.json', { type: 'application/json' });
    await act(async () => {
      await workflowApi.runFromAgentInput('normalizer', input);
      await workflowApi.runFromAgentInput('normalizer', input, '', { runEvaluations: true });
    });

    expect(submitted).toHaveLength(2);
    expect(submitted[0].get('runEvaluation')).toBe('false');
    expect(submitted[1].get('runEvaluation')).toBe('true');
    expect(submitted[0].get('confirmEvaluationCost')).toBe('false');
    expect(submitted[1].get('confirmEvaluationCost')).toBe('true');
  });

  it('acknowledges server cancellation before aborting local monitoring', async () => {
    const dispatch = vi.fn();
    const lifecycle = [];
    vi.stubGlobal('fetch', vi.fn((url, options = {}) => {
      const path = String(url);
      if (path === '/api/agents') return Promise.resolve(jsonResponse(REGISTRY));
      if (path === '/api/workflow/start') {
        return Promise.resolve(jsonResponse({ workflowId: 'wf-stop' }));
      }
      if (path === '/api/workflow/wf-stop/status') {
        lifecycle.push('status-started');
        return new Promise((resolve, reject) => {
          options.signal?.addEventListener('abort', () => {
            lifecycle.push('status-aborted');
            reject(new DOMException('Aborted', 'AbortError'));
          }, { once: true });
        });
      }
      if (path === '/api/workflow/wf-stop/stop') {
        lifecycle.push('stop-acknowledged');
        return Promise.resolve(jsonResponse({ status: 'cancelled' }));
      }
      return Promise.reject(new Error(`Unexpected request: ${path}`));
    }));
    await mount(dispatch);

    let runPromise;
    await act(async () => {
      runPromise = workflowApi.run('Start then stop', []);
    });
    await flush();
    expect(sessionStorage.getItem(ACTIVE_WORKFLOW_STORAGE_KEY)).toBe('wf-stop');

    let stopped;
    await act(async () => {
      stopped = await workflowApi.stop();
      await runPromise;
    });

    expect(stopped).toBe(true);
    expect(lifecycle).toEqual([
      'status-started',
      'stop-acknowledged',
      'status-aborted',
    ]);
    expect(sessionStorage.getItem(ACTIVE_WORKFLOW_STORAGE_KEY)).toBeNull();
    expect(dispatch.mock.calls.some(
      ([action]) => action.type === 'LOG' && action.message === 'Workflow stopped by the server',
    )).toBe(true);
  });

  it('waits for an in-flight start ID before confirming Stop', async () => {
    const dispatch = vi.fn();
    let resolveStart;
    let stopCalls = 0;
    vi.stubGlobal('fetch', vi.fn((url) => {
      const path = String(url);
      if (path === '/api/agents') return Promise.resolve(jsonResponse(REGISTRY));
      if (path === '/api/workflow/start') {
        return new Promise((resolve) => {
          resolveStart = () => resolve(jsonResponse({ workflowId: 'wf-start-race' }));
        });
      }
      if (path === '/api/workflow/wf-start-race/stop') {
        stopCalls += 1;
        return Promise.resolve(jsonResponse({ status: 'cancelled' }));
      }
      return Promise.reject(new Error(`Unexpected request: ${path}`));
    }));
    await mount(dispatch);

    let runPromise;
    await act(async () => {
      runPromise = workflowApi.run('Start then stop safely', []);
    });
    await flush();
    expect(resolveStart).toBeTypeOf('function');

    let immediateStopResult;
    await act(async () => {
      immediateStopResult = await workflowApi.stop();
    });
    expect(immediateStopResult).toBe(false);
    expect(stopCalls).toBe(0);
    expect(dispatch.mock.calls.some(
      ([action]) => action.type === 'WORKFLOW_ABORTED',
    )).toBe(false);
    expect(dispatch.mock.calls.some(
      ([action]) => action.type === 'LOG'
        && action.message.includes('Waiting for the server to resolve the start'),
    )).toBe(true);

    await act(async () => {
      resolveStart();
      await runPromise;
    });

    expect(stopCalls).toBe(1);
    expect(sessionStorage.getItem(ACTIVE_WORKFLOW_STORAGE_KEY)).toBeNull();
    expect(dispatch.mock.calls.some(
      ([action]) => action.type === 'WORKFLOW_ABORTED'
        && action.clearWorkflowId === true,
    )).toBe(true);
  });

  it('recovers status and completed-result reads without a duplicate paid start', async () => {
    vi.useFakeTimers();
    const dispatch = vi.fn();
    let startCalls = 0;
    let agentInputStartCalls = 0;
    let statusCalls = 0;
    let resultCalls = 0;

    vi.stubGlobal('fetch', vi.fn(async (url) => {
      const path = String(url);
      if (path === '/api/agents') return jsonResponse(REGISTRY);
      if (path === '/api/workflow/start') {
        startCalls += 1;
        return jsonResponse({ workflowId: 'wf-retry' });
      }
      if (path === '/api/workflow/start-from-agent-input') {
        agentInputStartCalls += 1;
        return jsonResponse({ workflowId: 'wf-duplicate' });
      }
      if (path === '/api/workflow/wf-retry/status') {
        statusCalls += 1;
        if (statusCalls === 1) throw new TypeError('offline');
        if (statusCalls === 2) return jsonResponse({ detail: 'busy' }, 503);
        return jsonResponse({
          status: 'completed',
          agents: {
            normalizer: {
              status: 'completed',
              data: { executionTime: 1, output: {} },
            },
          },
        });
      }
      if (path === '/api/workflow/wf-retry/result') {
        resultCalls += 1;
        if (resultCalls === 1) {
          return jsonResponse(
            { detail: 'result still publishing' },
            429,
            { 'Retry-After': '60' },
          );
        }
        return jsonResponse({ outputFolder: 'run', files: [], result: {} });
      }
      throw new Error(`Unexpected request: ${path}`);
    }));
    await mount(dispatch);

    let runPromise;
    await act(async () => {
      runPromise = workflowApi.run('Retry safely', []);
    });
    await flush();

    expect(startCalls).toBe(1);
    expect(statusCalls).toBe(1);
    expect(sessionStorage.getItem(ACTIVE_WORKFLOW_STORAGE_KEY)).toBe('wf-retry');
    expect(dispatch.mock.calls.some(
      ([action]) => action.type === 'WORKFLOW_MONITORING_INTERRUPTED',
    )).toBe(true);
    expect(dispatch.mock.calls.some(
      ([action]) => action.type === 'WORKFLOW_ERROR',
    )).toBe(false);

    const input = new File(['{}'], 'input.json', { type: 'application/json' });
    await act(async () => {
      await workflowApi.run('Do not start twice', []);
      await workflowApi.runFromAgentInput('normalizer', input);
    });
    expect(startCalls).toBe(1);
    expect(agentInputStartCalls).toBe(0);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });
    expect(statusCalls).toBe(2);
    expect(sessionStorage.getItem(ACTIVE_WORKFLOW_STORAGE_KEY)).toBe('wf-retry');

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000);
    });
    expect(statusCalls).toBe(3);
    expect(resultCalls).toBe(1);
    expect(sessionStorage.getItem(ACTIVE_WORKFLOW_STORAGE_KEY)).toBe('wf-retry');

    await act(async () => {
      await vi.advanceTimersByTimeAsync(9_999);
    });
    expect(resultCalls).toBe(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
      await runPromise;
    });

    expect(resultCalls).toBe(2);
    expect(sessionStorage.getItem(ACTIVE_WORKFLOW_STORAGE_KEY)).toBeNull();
    expect(dispatch.mock.calls.some(
      ([action]) => action.type === 'WORKFLOW_MONITORING_RESUMED',
    )).toBe(true);
    expect(dispatch.mock.calls.some(
      ([action]) => action.type === 'WORKFLOW_COMPLETE',
    )).toBe(true);
    expect(dispatch.mock.calls.some(
      ([action]) => action.type === 'WORKFLOW_ERROR',
    )).toBe(false);
  });

  it('blocks both start entrypoints when another workflow ID is stored', async () => {
    const dispatch = vi.fn();
    const fetchSpy = vi.fn(async (url) => {
      const path = String(url);
      if (path === '/api/agents') return jsonResponse(REGISTRY);
      throw new Error(`Unexpected request: ${path}`);
    });
    vi.stubGlobal('fetch', fetchSpy);
    await mount(dispatch);
    sessionStorage.setItem(ACTIVE_WORKFLOW_STORAGE_KEY, 'wf-existing');

    const input = new File(['{}'], 'input.json', { type: 'application/json' });
    await act(async () => {
      await workflowApi.run('Do not duplicate', []);
      await workflowApi.runFromAgentInput('normalizer', input);
    });

    expect(fetchSpy.mock.calls.some(
      ([url]) => String(url) === '/api/workflow/start',
    )).toBe(false);
    expect(fetchSpy.mock.calls.some(
      ([url]) => String(url) === '/api/workflow/start-from-agent-input',
    )).toBe(false);
    expect(dispatch.mock.calls.some(
      ([action]) => action.type === 'WORKFLOW_START',
    )).toBe(false);
  });

  it('clears unavailable workflow tracking on an authoritative 404', async () => {
    const dispatch = vi.fn();
    vi.stubGlobal('fetch', vi.fn(async (url) => {
      const path = String(url);
      if (path === '/api/agents') return jsonResponse(REGISTRY);
      if (path === '/api/workflow/start') {
        return jsonResponse({ workflowId: 'wf-missing' });
      }
      if (path === '/api/workflow/wf-missing/status') {
        return jsonResponse({ detail: 'not found' }, 404);
      }
      throw new Error(`Unexpected request: ${path}`);
    }));
    await mount(dispatch);

    await act(async () => {
      await workflowApi.run('Missing workflow', []);
    });

    expect(sessionStorage.getItem(ACTIVE_WORKFLOW_STORAGE_KEY)).toBeNull();
    expect(dispatch.mock.calls.some(
      ([action]) => action.type === 'WORKFLOW_ABORTED' && action.clearWorkflowId === true,
    )).toBe(true);
    expect(dispatch.mock.calls.some(
      ([action]) => action.type === 'WORKFLOW_ERROR',
    )).toBe(false);
  });

  it('aborts an active retry wait without issuing another read or paid start', async () => {
    vi.useFakeTimers();
    const dispatch = vi.fn();
    let startCalls = 0;
    let statusCalls = 0;
    vi.stubGlobal('fetch', vi.fn(async (url) => {
      const path = String(url);
      if (path === '/api/agents') return jsonResponse(REGISTRY);
      if (path === '/api/workflow/start') {
        startCalls += 1;
        return jsonResponse({ workflowId: 'wf-abort-retry' });
      }
      if (path === '/api/workflow/wf-abort-retry/status') {
        statusCalls += 1;
        return jsonResponse({ detail: 'busy' }, 503);
      }
      throw new Error(`Unexpected request: ${path}`);
    }));
    await mount(dispatch);

    let runPromise;
    await act(async () => {
      runPromise = workflowApi.run('Abort retry', []);
    });
    await flush();
    expect(statusCalls).toBe(1);

    await act(async () => {
      workflowApi.abort();
      await runPromise;
    });
    await act(async () => {
      await vi.runAllTimersAsync();
      await workflowApi.run('Still must not duplicate', []);
    });

    expect(statusCalls).toBe(1);
    expect(startCalls).toBe(1);
    expect(sessionStorage.getItem(ACTIVE_WORKFLOW_STORAGE_KEY)).toBe('wf-abort-retry');
    expect(dispatch.mock.calls.some(
      ([action]) => action.type === 'WORKFLOW_ABORTED',
    )).toBe(true);
  });
});
