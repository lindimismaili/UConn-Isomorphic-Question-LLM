import { useCallback, useEffect, useRef, useState } from 'react';

/** Baseline batch state before a Create Data run starts. */
const INITIAL_STATE = {
  batchId: null,
  status: 'idle',
  isStarting: false,
  isRunning: false,
  totalFiles: 0,
  doneFiles: 0,
  completedFiles: 0,
  failedFiles: 0,
  cancelledFiles: 0,
  outputFolder: '',
  summaryFile: '',
  entries: [],
  error: '',
};

/** Backend states that stop the polling loop. */
const TERMINAL_STATUSES = new Set(['completed', 'errored', 'cancelled']);

/**
 * Fetches a JSON payload from the batch-evaluation API.
 *
 * @param {string} url - Endpoint path to request.
 * @param {RequestInit} [options] - Fetch options.
 * @param {AbortSignal} [signal] - Abort signal used to cancel polling.
 * @returns {Promise<object>} Parsed response body.
 * @throws {Error} When the response is not OK.
 */
async function fetchJson(url, options, signal) {
  const response = await fetch(url, { ...options, signal });
  if (!response.ok) {
    const text = await response.text().catch(() => `Status ${response.status}`);
    throw new Error(text || `Request failed with status ${response.status}`);
  }
  return response.json();
}

/**
 * Normalizes Create Data API payloads into stable hook state.
 *
 * @param {object} payload - Response from start/status/result/stop.
 * @param {object} fallback - Previous state used to preserve batch metadata.
 * @returns {object} UI state for the Create Data modal.
 */
function buildState(payload, fallback = {}) {
  const completedFiles = Number(payload?.completedFiles || 0);
  const failedFiles = Number(payload?.failedFiles || 0);
  const cancelledFiles = Number(payload?.cancelledFiles || 0);
  const doneFiles = Number(
    payload?.doneFiles ?? (completedFiles + failedFiles + cancelledFiles)
  );
  const status = payload?.status || fallback.status || 'idle';
  const terminal = TERMINAL_STATUSES.has(status);

  return {
    batchId: payload?.batchId || fallback.batchId || null,
    status,
    isStarting: false,
    isRunning: !terminal && status !== 'idle',
    totalFiles: Number(payload?.totalFiles || fallback.totalFiles || 0),
    doneFiles,
    completedFiles,
    failedFiles,
    cancelledFiles,
    outputFolder: payload?.outputFolder || '',
    summaryFile: payload?.summaryFile || '',
    entries: Array.isArray(payload?.entries) ? payload.entries : [],
    error: typeof payload?.error === 'string' ? payload.error : '',
  };
}

/**
 * Runs the Create Data batch workflow from the frontend.
 *
 * Touches `/api/create-data/start`, `/api/create-data/:batchId/status`,
 * `/api/create-data/:batchId/result`, and `/api/create-data/:batchId/stop`.
 *
 * @returns {{state: object, start: Function, stop: Function}}
 */
export function useCreateData() {
  const [state, setState] = useState(INITIAL_STATE);
  const abortRef = useRef(null);

  useEffect(() => {
    return () => {
      abortRef.current?.abort();
      abortRef.current = null;
    };
  }, []);

  const start = useCallback(async (prompt = '') => {
    if (state.isStarting || state.isRunning) {
      return;
    }

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    const { signal } = controller;

    setState({
      ...INITIAL_STATE,
      isStarting: true,
      status: 'starting',
    });

    try {
      const formData = new FormData();
      formData.append('prompt', prompt);

      const started = await fetchJson('/api/create-data/start', { method: 'POST', body: formData }, signal);
      let nextState = {
        ...INITIAL_STATE,
        batchId: started.batchId,
        totalFiles: Number(started.totalFiles || 0),
        status: 'starting',
        isRunning: true,
      };
      setState(nextState);

      while (!signal.aborted) {
        const statusPayload = await fetchJson(`/api/create-data/${started.batchId}/status`, {}, signal);
        nextState = buildState(statusPayload, nextState);
        setState(nextState);

        if (TERMINAL_STATUSES.has(statusPayload.status)) {
          try {
            const resultPayload = await fetchJson(`/api/create-data/${started.batchId}/result`, {}, signal);
            setState(buildState(resultPayload, nextState));
          } catch {
            setState(nextState);
          }
          break;
        }

        await new Promise((resolve, reject) => {
          const timeoutId = setTimeout(resolve, 400);
          signal.addEventListener(
            'abort',
            () => {
              clearTimeout(timeoutId);
              reject(new DOMException('Aborted', 'AbortError'));
            },
            { once: true }
          );
        });
      }
    } catch (error) {
      if (error?.name === 'AbortError') {
        return;
      }
      setState({
        ...INITIAL_STATE,
        status: 'errored',
        error: error?.message || 'Create Data failed.',
      });
    } finally {
      if (abortRef.current === controller) {
        abortRef.current = null;
      }
    }
  }, [state.isRunning, state.isStarting]);

  const stop = useCallback(async () => {
    const batchId = state.batchId;
    abortRef.current?.abort();
    abortRef.current = null;

    if (!batchId) {
      setState({
        ...INITIAL_STATE,
        status: 'idle',
      });
      return;
    }

    setState((prev) => ({
      ...prev,
      status: 'stopping',
      isStarting: false,
      isRunning: true,
    }));

    try {
      const stopped = await fetchJson(
        `/api/create-data/${batchId}/stop`,
        { method: 'POST' },
      );
      setState(buildState(stopped, { batchId, status: 'cancelled' }));
    } catch (error) {
      setState((prev) => ({
        ...prev,
        status: 'errored',
        isStarting: false,
        isRunning: false,
        error: error?.message || 'Failed to stop Create Data.',
      }));
    }
  }, [state.batchId]);

  return { state, start, stop };
}
