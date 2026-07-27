/* @vitest-environment jsdom */

import { act, useEffect } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  ACTIVE_EVAL_BATCH_STORAGE_KEY,
  useBatchEval,
} from './useBatchEval.js';
import {
  ACTIVE_CREATE_DATA_BATCH_STORAGE_KEY,
  useCreateData,
} from './useCreateData.js';
import {
  APP_ACCESS_TOKEN_HEADER,
  APP_ACCESS_TOKEN_STORAGE_KEY,
  IDEMPOTENCY_KEY_HEADER,
  PAID_START_ATTEMPT_STORAGE_PREFIX,
  setAccessToken,
} from '../utils/api.js';

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

function jsonResponse(payload, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => payload,
    text: async () => JSON.stringify(payload),
  };
}

function pendingUntilAborted(signal) {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new DOMException('Aborted', 'AbortError'));
      return;
    }
    signal?.addEventListener(
      'abort',
      () => reject(new DOMException('Aborted', 'AbortError')),
      { once: true },
    );
  });
}

function CreateDataHarness({ onReady, revision = 0 }) {
  const api = useCreateData();
  useEffect(() => {
    onReady(api);
  }, [api, onReady, revision]);
  return null;
}

function BatchEvalHarness({ onReady, revision = 0 }) {
  const api = useBatchEval();
  useEffect(() => {
    onReady(api);
  }, [api, onReady, revision]);
  return null;
}

let root;
let container;
let hookApi;

async function mountHarness(Harness, revision = 0) {
  if (!root) {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  }
  const onReady = (api) => {
    hookApi = api;
  };
  await act(async () => {
    root.render(<Harness onReady={onReady} revision={revision} />);
  });
}

async function flush(count = 10) {
  await act(async () => {
    for (let index = 0; index < count; index += 1) {
      await Promise.resolve();
    }
  });
}

beforeEach(() => {
  vi.useRealTimers();
  sessionStorage.clear();
  hookApi = null;
});

afterEach(() => {
  if (root) {
    act(() => root.unmount());
  }
  container?.remove();
  root = null;
  container = null;
  hookApi = null;
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.useRealTimers();
  sessionStorage.clear();
});

describe('paid batch recovery', () => {
  it('reuses a Create Data key after remount without automatic retry', async () => {
    let starts = 0;
    const keys = [];
    vi.stubGlobal('fetch', vi.fn(async (url, options = {}) => {
      const path = String(url);
      if (path === '/api/create-data/start') {
        starts += 1;
        keys.push(new Headers(options.headers).get(IDEMPOTENCY_KEY_HEADER));
        if (starts === 1) throw new TypeError('lost response');
        return jsonResponse({ batchId: 'create-idempotent', totalFiles: 1 });
      }
      if (path === '/api/create-data/create-idempotent/status') {
        return jsonResponse({
          batchId: 'create-idempotent',
          status: 'completed',
          totalFiles: 1,
          completedFiles: 1,
          doneFiles: 1,
        });
      }
      if (path === '/api/create-data/create-idempotent/result') {
        return jsonResponse({
          batchId: 'create-idempotent',
          status: 'completed',
          totalFiles: 1,
          completedFiles: 1,
          doneFiles: 1,
        });
      }
      throw new Error(`Unexpected request: ${path}`);
    }));
    await mountHarness(CreateDataHarness);

    await act(async () => {
      await hookApi.start('same prompt', { confirmWorkflowCosts: true });
    });
    expect(starts).toBe(1);
    expect(sessionStorage.getItem(
      `${PAID_START_ATTEMPT_STORAGE_PREFIX}create-data`,
    )).toContain(keys[0]);

    act(() => root.unmount());
    container.remove();
    root = null;
    container = null;
    await mountHarness(CreateDataHarness);
    await act(async () => {
      await hookApi.start('same prompt', { confirmWorkflowCosts: true });
    });

    expect(starts).toBe(2);
    expect(keys[1]).toBe(keys[0]);
    expect(sessionStorage.getItem(
      `${PAID_START_ATTEMPT_STORAGE_PREFIX}create-data`,
    )).toBeNull();
  });

  it('reuses an Eval Batch key after a 5xx until the user resubmits', async () => {
    let starts = 0;
    const keys = [];
    vi.stubGlobal('fetch', vi.fn(async (url, options = {}) => {
      const path = String(url);
      if (path === '/api/eval-batch/start') {
        starts += 1;
        keys.push(new Headers(options.headers).get(IDEMPOTENCY_KEY_HEADER));
        if (starts === 1) {
          return jsonResponse({ detail: 'response uncertain' }, 503);
        }
        return jsonResponse({
          batchId: 'eval-idempotent',
          totalFiles: 1,
          projectedEvaluationItems: 1,
          projectedGraderCalls: 8,
        });
      }
      if (path === '/api/eval-batch/eval-idempotent/status') {
        return jsonResponse({
          batchId: 'eval-idempotent',
          status: 'completed',
          mode: 'prior_runs',
          totalFiles: 1,
          completedFiles: 1,
          doneFiles: 1,
        });
      }
      if (path === '/api/eval-batch/eval-idempotent/result') {
        return jsonResponse({
          batchId: 'eval-idempotent',
          status: 'completed',
          mode: 'prior_runs',
          totalFiles: 1,
          completedFiles: 1,
          doneFiles: 1,
        });
      }
      throw new Error(`Unexpected request: ${path}`);
    }));
    await mountHarness(BatchEvalHarness);
    const intent = {
      mode: 'prior_runs',
      selectedFolders: ['run-a'],
      confirmGraderCalls: true,
    };

    await act(async () => {
      await hookApi.start(intent);
    });
    expect(starts).toBe(1);
    await act(async () => {
      await hookApi.start(intent);
    });

    expect(starts).toBe(2);
    expect(keys[1]).toBe(keys[0]);
    expect(sessionStorage.getItem(
      `${PAID_START_ATTEMPT_STORAGE_PREFIX}eval-batch`,
    )).toBeNull();
  });

  it('stores a Create Data batch before polling and clears it at terminal status', async () => {
    let storedDuringStatus = '';
    vi.stubGlobal('fetch', vi.fn(async (url) => {
      const path = String(url);
      if (path === '/api/create-data/start') {
        return jsonResponse({ batchId: 'create-start', totalFiles: 1 });
      }
      if (path === '/api/create-data/create-start/status') {
        storedDuringStatus = sessionStorage.getItem(
          ACTIVE_CREATE_DATA_BATCH_STORAGE_KEY,
        );
        return jsonResponse({
          batchId: 'create-start',
          status: 'completed',
          totalFiles: 1,
          completedFiles: 1,
          doneFiles: 1,
        });
      }
      if (path === '/api/create-data/create-start/result') {
        return jsonResponse({
          batchId: 'create-start',
          status: 'completed',
          totalFiles: 1,
          completedFiles: 1,
          doneFiles: 1,
        });
      }
      throw new Error(`Unexpected request: ${path}`);
    }));
    await mountHarness(CreateDataHarness);

    await act(async () => {
      await hookApi.start('Generate fixtures', { confirmWorkflowCosts: true });
    });

    expect(storedDuringStatus).toBe('create-start');
    expect(sessionStorage.getItem(ACTIVE_CREATE_DATA_BATCH_STORAGE_KEY)).toBeNull();
    expect(hookApi.state.status).toBe('completed');
  });

  it('restores a Create Data batch after refresh and keeps Stop available', async () => {
    sessionStorage.setItem(
      ACTIVE_CREATE_DATA_BATCH_STORAGE_KEY,
      'create-resume',
    );
    const calls = [];
    vi.stubGlobal('fetch', vi.fn((url, options = {}) => {
      const path = String(url);
      calls.push(path);
      if (path === '/api/create-data/create-resume/status') {
        return pendingUntilAborted(options.signal);
      }
      if (path === '/api/create-data/create-resume/stop') {
        return Promise.resolve(jsonResponse({
          batchId: 'create-resume',
          status: 'cancelled',
          totalFiles: 1,
          cancelledFiles: 1,
          doneFiles: 1,
        }));
      }
      return Promise.reject(new Error(`Unexpected request: ${path}`));
    }));

    await mountHarness(CreateDataHarness);
    await flush();

    expect(hookApi.state.batchId).toBe('create-resume');
    expect(hookApi.state.isRunning).toBe(true);
    expect(sessionStorage.getItem(ACTIVE_CREATE_DATA_BATCH_STORAGE_KEY)).toBe(
      'create-resume',
    );

    await act(async () => {
      await hookApi.stop();
    });

    expect(calls).toContain('/api/create-data/create-resume/stop');
    expect(hookApi.state.status).toBe('cancelled');
    expect(sessionStorage.getItem(ACTIVE_CREATE_DATA_BATCH_STORAGE_KEY)).toBeNull();
  });

  it('clears a missing Create Data recovery record after a 404', async () => {
    sessionStorage.setItem(
      ACTIVE_CREATE_DATA_BATCH_STORAGE_KEY,
      'create-missing',
    );
    vi.stubGlobal('fetch', vi.fn(async (url) => {
      const path = String(url);
      if (path === '/api/create-data/create-missing/status') {
        return jsonResponse({ detail: 'Batch job not found' }, 404);
      }
      throw new Error(`Unexpected request: ${path}`);
    }));

    await mountHarness(CreateDataHarness);
    await flush();

    expect(sessionStorage.getItem(ACTIVE_CREATE_DATA_BATCH_STORAGE_KEY)).toBeNull();
    expect(hookApi.state.batchId).toBeNull();
    expect(hookApi.state.status).toBe('errored');
    expect(hookApi.state.error).toContain('no longer available');
  });

  it('stores an Eval Batch before polling and clears it at terminal status', async () => {
    let storedDuringStatus = '';
    vi.stubGlobal('fetch', vi.fn(async (url) => {
      const path = String(url);
      if (path === '/api/eval-batch/start') {
        return jsonResponse({
          batchId: 'eval-start',
          totalFiles: 1,
          projectedEvaluationItems: 2,
          projectedGraderCalls: 16,
          projectedExtractionFiles: 0,
          projectedExtractionCalls: 0,
          projectedBootstrapCalls: 0,
        });
      }
      if (path === '/api/eval-batch/eval-start/status') {
        storedDuringStatus = sessionStorage.getItem(
          ACTIVE_EVAL_BATCH_STORAGE_KEY,
        );
        return jsonResponse({
          batchId: 'eval-start',
          status: 'completed',
          mode: 'prior_runs',
          totalFiles: 1,
          completedFiles: 1,
          doneFiles: 1,
          projectedExtractionFiles: 0,
          projectedExtractionCalls: 0,
          projectedBootstrapCalls: 0,
        });
      }
      if (path === '/api/eval-batch/eval-start/result') {
        return jsonResponse({
          batchId: 'eval-start',
          status: 'completed',
          mode: 'prior_runs',
          totalFiles: 1,
          completedFiles: 1,
          doneFiles: 1,
        });
      }
      throw new Error(`Unexpected request: ${path}`);
    }));
    await mountHarness(BatchEvalHarness);

    await act(async () => {
      await hookApi.start({
        mode: 'prior_runs',
        selectedFolders: ['run-a'],
        confirmGraderCalls: true,
      });
    });

    expect(storedDuringStatus).toBe('eval-start');
    expect(sessionStorage.getItem(ACTIVE_EVAL_BATCH_STORAGE_KEY)).toBeNull();
    expect(hookApi.state.status).toBe('completed');
    expect(hookApi.state.projectedExtractionFiles).toBe(0);
    expect(hookApi.state.projectedExtractionCalls).toBe(0);
    expect(hookApi.state.projectedBootstrapCalls).toBe(0);
  });

  it('preserves upload extraction and bootstrap projections through polling', async () => {
    let submittedForm;
    vi.stubGlobal('fetch', vi.fn(async (url, options = {}) => {
      const path = String(url);
      if (path === '/api/eval-batch/start') {
        submittedForm = options.body;
        return jsonResponse({
          batchId: 'eval-upload',
          totalFiles: 2,
          projectedEvaluationItems: 4,
          projectedGraderCalls: 32,
          projectedExtractionFiles: 3,
          projectedExtractionCalls: 6,
          projectedBootstrapCalls: 3,
        });
      }
      if (path === '/api/eval-batch/eval-upload/status') {
        return jsonResponse({
          batchId: 'eval-upload',
          status: 'completed',
          mode: 'upload',
          totalFiles: 2,
          completedFiles: 2,
          doneFiles: 2,
        });
      }
      if (path === '/api/eval-batch/eval-upload/result') {
        return jsonResponse({
          batchId: 'eval-upload',
          status: 'completed',
          mode: 'upload',
          totalFiles: 2,
          completedFiles: 2,
          doneFiles: 2,
        });
      }
      throw new Error(`Unexpected request: ${path}`);
    }));
    await mountHarness(BatchEvalHarness);

    const sourceFile = new File(['source'], 'source.pdf', {
      type: 'application/pdf',
    });
    const generatedFiles = [
      new File(['generated-a'], 'generated-a.pdf', {
        type: 'application/pdf',
      }),
      new File(['generated-b'], 'generated-b.pdf', {
        type: 'application/pdf',
      }),
    ];
    await act(async () => {
      await hookApi.start({
        mode: 'upload',
        sourceFile,
        generatedFiles,
        confirmGraderCalls: true,
      });
    });

    expect(submittedForm.get('confirm_grader_calls')).toBe('true');
    expect(submittedForm.get('source_file')).toBe(sourceFile);
    expect(submittedForm.getAll('generated_files')).toEqual(generatedFiles);
    expect(hookApi.state.status).toBe('completed');
    expect(hookApi.state.projectedExtractionFiles).toBe(3);
    expect(hookApi.state.projectedExtractionCalls).toBe(6);
    expect(hookApi.state.projectedBootstrapCalls).toBe(3);
  });

  it('resumes Eval Batch polling when the access token is re-entered', async () => {
    sessionStorage.setItem(ACTIVE_EVAL_BATCH_STORAGE_KEY, 'eval-reauth');
    let statusCalls = 0;
    let authenticatedHeader = '';
    vi.stubGlobal('fetch', vi.fn(async (url, options = {}) => {
      const path = String(url);
      if (path === '/api/eval-batch/eval-reauth/status') {
        statusCalls += 1;
        if (statusCalls === 1) {
          return jsonResponse({ detail: 'Authentication required' }, 401);
        }
        authenticatedHeader = new Headers(options.headers).get(
          APP_ACCESS_TOKEN_HEADER,
        );
        return jsonResponse({
          batchId: 'eval-reauth',
          status: 'completed',
          mode: 'prior_runs',
          totalFiles: 1,
          completedFiles: 1,
          doneFiles: 1,
        });
      }
      if (path === '/api/eval-batch/eval-reauth/result') {
        return jsonResponse({
          batchId: 'eval-reauth',
          status: 'completed',
          mode: 'prior_runs',
          totalFiles: 1,
          completedFiles: 1,
          doneFiles: 1,
        });
      }
      throw new Error(`Unexpected request: ${path}`);
    }));

    await mountHarness(BatchEvalHarness);
    await flush();

    expect(statusCalls).toBe(1);
    expect(hookApi.state.isRunning).toBe(true);
    expect(hookApi.state.error).toContain('access is restored');
    expect(sessionStorage.getItem(ACTIVE_EVAL_BATCH_STORAGE_KEY)).toBe(
      'eval-reauth',
    );

    setAccessToken('replacement-access-token');
    await mountHarness(BatchEvalHarness, 1);
    await flush();

    expect(statusCalls).toBe(2);
    expect(authenticatedHeader).toBe('replacement-access-token');
    expect(hookApi.state.status).toBe('completed');
    expect(sessionStorage.getItem(ACTIVE_EVAL_BATCH_STORAGE_KEY)).toBeNull();
    expect(sessionStorage.getItem(APP_ACCESS_TOKEN_STORAGE_KEY)).toBe(
      'replacement-access-token',
    );
  });

  it('keeps Stop available for a restored Eval Batch', async () => {
    sessionStorage.setItem(ACTIVE_EVAL_BATCH_STORAGE_KEY, 'eval-resume');
    const calls = [];
    vi.stubGlobal('fetch', vi.fn((url, options = {}) => {
      const path = String(url);
      calls.push(path);
      if (path === '/api/eval-batch/eval-resume/status') {
        return pendingUntilAborted(options.signal);
      }
      if (path === '/api/eval-batch/eval-resume/stop') {
        return Promise.resolve(jsonResponse({
          batchId: 'eval-resume',
          status: 'cancelled',
          mode: 'prior_runs',
          totalFiles: 1,
          cancelledFiles: 1,
          doneFiles: 1,
        }));
      }
      return Promise.reject(new Error(`Unexpected request: ${path}`));
    }));

    await mountHarness(BatchEvalHarness);
    await flush();

    expect(hookApi.state.batchId).toBe('eval-resume');
    expect(hookApi.state.isRunning).toBe(true);

    await act(async () => {
      await hookApi.stop();
    });

    expect(calls).toContain('/api/eval-batch/eval-resume/stop');
    expect(hookApi.state.status).toBe('cancelled');
    expect(sessionStorage.getItem(ACTIVE_EVAL_BATCH_STORAGE_KEY)).toBeNull();
  });

  it('clears a missing Eval Batch recovery record after a 404', async () => {
    sessionStorage.setItem(ACTIVE_EVAL_BATCH_STORAGE_KEY, 'eval-missing');
    vi.stubGlobal('fetch', vi.fn(async (url) => {
      const path = String(url);
      if (path === '/api/eval-batch/eval-missing/status') {
        return jsonResponse({ detail: 'Eval batch not found' }, 404);
      }
      throw new Error(`Unexpected request: ${path}`);
    }));

    await mountHarness(BatchEvalHarness);
    await flush();

    expect(sessionStorage.getItem(ACTIVE_EVAL_BATCH_STORAGE_KEY)).toBeNull();
    expect(hookApi.state.batchId).toBeNull();
    expect(hookApi.state.status).toBe('errored');
    expect(hookApi.state.error).toContain('no longer available');
  });

  it('waits for a Create Data start ID before confirming Stop', async () => {
    let resolveStart;
    let stopCalls = 0;
    vi.stubGlobal('fetch', vi.fn((url) => {
      const path = String(url);
      if (path === '/api/create-data/start') {
        return new Promise((resolve) => {
          resolveStart = () => resolve(jsonResponse({
            batchId: 'create-start-race',
            totalFiles: 1,
          }));
        });
      }
      if (path === '/api/create-data/create-start-race/stop') {
        stopCalls += 1;
        return Promise.resolve(jsonResponse({
          batchId: 'create-start-race',
          status: 'cancelled',
          totalFiles: 1,
          cancelledFiles: 1,
          doneFiles: 1,
        }));
      }
      return Promise.reject(new Error(`Unexpected request: ${path}`));
    }));
    await mountHarness(CreateDataHarness);

    let startPromise;
    await act(async () => {
      startPromise = hookApi.start('Start safely', { confirmWorkflowCosts: true });
    });
    await flush();

    let immediateStopResult;
    await act(async () => {
      immediateStopResult = await hookApi.stop();
    });
    expect(immediateStopResult).toBe(false);
    expect(stopCalls).toBe(0);
    expect(hookApi.state.status).toBe('stopping');
    expect(hookApi.state.error).toContain('Waiting for the server');

    await act(async () => {
      resolveStart();
      await startPromise;
    });
    expect(stopCalls).toBe(1);
    expect(hookApi.state.status).toBe('cancelled');
    expect(sessionStorage.getItem(ACTIVE_CREATE_DATA_BATCH_STORAGE_KEY)).toBeNull();
  });

  it('waits for an Eval Batch start ID before confirming Stop', async () => {
    let resolveStart;
    let stopCalls = 0;
    vi.stubGlobal('fetch', vi.fn((url) => {
      const path = String(url);
      if (path === '/api/eval-batch/start') {
        return new Promise((resolve) => {
          resolveStart = () => resolve(jsonResponse({
            batchId: 'eval-start-race',
            totalFiles: 1,
          }));
        });
      }
      if (path === '/api/eval-batch/eval-start-race/stop') {
        stopCalls += 1;
        return Promise.resolve(jsonResponse({
          batchId: 'eval-start-race',
          status: 'cancelled',
          mode: 'prior_runs',
          totalFiles: 1,
          cancelledFiles: 1,
          doneFiles: 1,
        }));
      }
      return Promise.reject(new Error(`Unexpected request: ${path}`));
    }));
    await mountHarness(BatchEvalHarness);

    let startPromise;
    await act(async () => {
      startPromise = hookApi.start({
        mode: 'prior_runs',
        selectedFolders: ['run-a'],
        confirmGraderCalls: true,
      });
    });
    await flush();

    let immediateStopResult;
    await act(async () => {
      immediateStopResult = await hookApi.stop();
    });
    expect(immediateStopResult).toBe(false);
    expect(stopCalls).toBe(0);
    expect(hookApi.state.status).toBe('stopping');

    await act(async () => {
      resolveStart();
      await startPromise;
    });
    expect(stopCalls).toBe(1);
    expect(hookApi.state.status).toBe('cancelled');
    expect(sessionStorage.getItem(ACTIVE_EVAL_BATCH_STORAGE_KEY)).toBeNull();
  });

  it('retains an Eval Batch recovery ID while terminal results retry', async () => {
    vi.useFakeTimers();
    let resultCalls = 0;
    vi.stubGlobal('fetch', vi.fn(async (url) => {
      const path = String(url);
      if (path === '/api/eval-batch/start') {
        return jsonResponse({ batchId: 'eval-result-retry', totalFiles: 1 });
      }
      if (path === '/api/eval-batch/eval-result-retry/status') {
        return jsonResponse({
          batchId: 'eval-result-retry',
          status: 'completed',
          mode: 'prior_runs',
          totalFiles: 1,
          completedFiles: 1,
          doneFiles: 1,
        });
      }
      if (path === '/api/eval-batch/eval-result-retry/result') {
        resultCalls += 1;
        return resultCalls === 1
          ? jsonResponse({ detail: 'still publishing' }, 503)
          : jsonResponse({
            batchId: 'eval-result-retry',
            status: 'completed',
            mode: 'prior_runs',
            totalFiles: 1,
            completedFiles: 1,
            doneFiles: 1,
          });
      }
      throw new Error(`Unexpected request: ${path}`);
    }));
    await mountHarness(BatchEvalHarness);

    let startPromise;
    await act(async () => {
      startPromise = hookApi.start({
        mode: 'prior_runs',
        selectedFolders: ['run-a'],
        confirmGraderCalls: true,
      });
    });
    await flush();

    expect(resultCalls).toBe(1);
    expect(sessionStorage.getItem(ACTIVE_EVAL_BATCH_STORAGE_KEY)).toBe(
      'eval-result-retry',
    );
    expect(hookApi.state.isLoadingResult).toBe(true);
    expect(hookApi.state.error).toContain('will be retried');

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
      await startPromise;
    });

    expect(resultCalls).toBe(2);
    expect(sessionStorage.getItem(ACTIVE_EVAL_BATCH_STORAGE_KEY)).toBeNull();
    expect(hookApi.state.status).toBe('completed');
    expect(hookApi.state.isLoadingResult).toBe(false);
  });
});
