import { useCallback, useEffect, useRef, useState } from 'react';

import { abortableSleep } from '../utils/abortableSleep';
import {
  clearPaidStartAttempt,
  fetchWithAbort,
  getAccessToken,
  getOrCreatePaidStartAttempt,
  isAmbiguousPaidStartFailure,
  readErrorDetail,
  withIdempotencyHeader,
} from '../utils/api';
import {
  currentPollInterval,
  isRetryableReadFailure,
  retryDelay,
} from '../utils/polling';

export const ACTIVE_CREATE_DATA_BATCH_STORAGE_KEY = 'activeCreateDataBatchId';

function readActiveBatchId() {
  try {
    return sessionStorage.getItem(ACTIVE_CREATE_DATA_BATCH_STORAGE_KEY) || '';
  } catch {
    return '';
  }
}

function storeActiveBatchId(batchId) {
  try {
    if (batchId) {
      sessionStorage.setItem(ACTIVE_CREATE_DATA_BATCH_STORAGE_KEY, batchId);
    }
  } catch {
    // Recovery remains best-effort when browser storage is unavailable.
  }
}

function clearActiveBatchId(batchId) {
  try {
    const stored = sessionStorage.getItem(ACTIVE_CREATE_DATA_BATCH_STORAGE_KEY);
    if (!batchId || stored === batchId) {
      sessionStorage.removeItem(ACTIVE_CREATE_DATA_BATCH_STORAGE_KEY);
    }
  } catch {
    // Storage is optional; the server remains authoritative.
  }
}

async function requestCreateDataJson(url, { signal, fallbackError, ...options } = {}) {
  const response = await fetchWithAbort(url, options, signal);
  if (!response.ok) {
    const error = new Error(
      await readErrorDetail(
        response,
        fallbackError || `Create Data request failed (${response.status})`,
      ),
    );
    error.status = response.status;
    throw error;
  }
  return response.json();
}

/** Baseline batch state before a Create Data run starts. */
const INITIAL_STATE = {
  batchId: null,
  status: 'idle',
  isStarting: false,
  isRunning: false,
  isLoadingResult: false,
  totalFiles: 0,
  doneFiles: 0,
  completedFiles: 0,
  failedFiles: 0,
  cancelledFiles: 0,
  blockedFiles: 0,
  outputFolder: '',
  summaryFile: '',
  entries: [],
  error: '',
};

/** Backend states that stop the polling loop. */
export const CREATE_DATA_TERMINAL_STATUSES = new Set([
  'completed',
  'errored',
  'cancelled',
  'blocked',
]);

/**
 * Normalizes Create Data API payloads into stable hook state.
 *
 * @param {object} payload - Response from start/status/result/stop.
 * @param {object} fallback - Previous state used to preserve batch metadata.
 * @returns {object} UI state for the Create Data modal.
 */
export function buildCreateDataState(payload, fallback = {}) {
  const completedFiles = Number(payload?.completedFiles || 0);
  const failedFiles = Number(payload?.failedFiles || 0);
  const cancelledFiles = Number(payload?.cancelledFiles || 0);
  const blockedFiles = Number(payload?.blockedFiles || 0);
  const doneFiles = Number(
    payload?.doneFiles
      ?? (completedFiles + failedFiles + cancelledFiles + blockedFiles)
  );
  const status = payload?.status || fallback.status || 'idle';
  const terminal = CREATE_DATA_TERMINAL_STATUSES.has(status);

  return {
    batchId: payload?.batchId || fallback.batchId || null,
    status,
    isStarting: false,
    isRunning: !terminal && status !== 'idle',
    isLoadingResult: false,
    totalFiles: Number(payload?.totalFiles || fallback.totalFiles || 0),
    doneFiles,
    completedFiles,
    failedFiles,
    cancelledFiles,
    blockedFiles,
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
  const unauthorizedRef = useRef(false);
  const paidStartRef = useRef(null);
  const stopRequestedRef = useRef(false);
  const accessToken = getAccessToken();

  useEffect(() => {
    return () => {
      abortRef.current?.abort();
      abortRef.current = null;
    };
  }, []);

  const pollBatch = useCallback(async (batchId, controller, seedState) => {
    const { signal } = controller;
    let nextState = {
      ...INITIAL_STATE,
      ...seedState,
      batchId,
      isStarting: false,
      isRunning: true,
    };
    let statusRetryAttempt = 0;

    while (!signal.aborted) {
      let statusPayload;
      try {
        statusPayload = await requestCreateDataJson(
          `/api/create-data/${batchId}/status`,
          { signal },
        );
        unauthorizedRef.current = false;
        statusRetryAttempt = 0;
      } catch (error) {
        if (error?.name === 'AbortError' || signal.aborted) return;
        if (error?.status === 404) {
          clearActiveBatchId(batchId);
          setState({
            ...INITIAL_STATE,
            status: 'errored',
            error: 'The previous Create Data batch is no longer available on the server.',
          });
          return;
        }
        if (error?.status === 401) {
          unauthorizedRef.current = true;
          setState((prev) => ({
            ...prev,
            batchId,
            status: prev.status === 'stopping' ? 'stopping' : 'running',
            isStarting: false,
            isRunning: true,
            error: 'Monitoring paused until application access is restored.',
          }));
          return;
        }
        if (!isRetryableReadFailure(error)) {
          setState((prev) => ({
            ...prev,
            batchId,
            status: prev.status === 'stopping' ? 'stopping' : 'running',
            isStarting: false,
            isRunning: true,
            error: `Monitoring paused: ${error?.message || 'Unknown error'}`,
          }));
          return;
        }

        setState((prev) => ({
          ...prev,
          batchId,
          status: prev.status === 'stopping' ? 'stopping' : 'running',
          isStarting: false,
          isRunning: true,
          error: `Monitoring interrupted; retrying: ${error?.message || 'Unknown error'}`,
        }));
        await abortableSleep(retryDelay(statusRetryAttempt), signal);
        statusRetryAttempt += 1;
        continue;
      }

      if (signal.aborted) return;
      nextState = buildCreateDataState(statusPayload, nextState);
      if (CREATE_DATA_TERMINAL_STATUSES.has(statusPayload.status)) {
        nextState = { ...nextState, isLoadingResult: true };
      }
      setState(nextState);

      if (CREATE_DATA_TERMINAL_STATUSES.has(statusPayload.status)) {
        let resultRetryAttempt = 0;
        while (!signal.aborted) {
          try {
            const resultPayload = await requestCreateDataJson(
              `/api/create-data/${batchId}/result`,
              { signal },
            );
            if (!signal.aborted) {
              nextState = buildCreateDataState(resultPayload, nextState);
              setState(nextState);
              clearActiveBatchId(batchId);
            }
            return;
          } catch (error) {
            if (error?.name === 'AbortError' || signal.aborted) return;
            if (error?.status === 404) {
              clearActiveBatchId(batchId);
              setState({
                ...nextState,
                isLoadingResult: false,
                error: 'The completed Create Data result is no longer available on the server.',
              });
              return;
            }
            if (error?.status === 401) {
              unauthorizedRef.current = true;
              setState({
                ...nextState,
                error: 'Result loading paused until application access is restored.',
              });
              return;
            }
            if (!isRetryableReadFailure(error)) {
              setState({
                ...nextState,
                error: `Result loading paused: ${error?.message || 'Unknown error'}`,
              });
              return;
            }
            setState({
              ...nextState,
              error: `Run finished; detailed results are temporarily unavailable and will be retried: ${
                error?.message || 'Unknown error'
              }`,
            });
            await abortableSleep(retryDelay(resultRetryAttempt), signal);
            resultRetryAttempt += 1;
          }
        }
        return;
      }

      await abortableSleep(currentPollInterval(), signal);
    }
  }, []);

  const start = useCallback(async (
    prompt = '',
    { confirmWorkflowCosts = false } = {},
  ) => {
    if (state.isStarting || state.isRunning || state.isLoadingResult) {
      return;
    }

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    const { signal } = controller;
    stopRequestedRef.current = false;
    const intentSignature = JSON.stringify({
      prompt,
      confirmWorkflowCosts: Boolean(confirmWorkflowCosts),
    });
    try {
      if (!paidStartRef.current || paidStartRef.current.intentSignature !== intentSignature) {
        paidStartRef.current = {
          ...getOrCreatePaidStartAttempt(
            'create-data',
            '/api/create-data/start',
            intentSignature,
          ),
          intentSignature,
        };
      }
    } catch (error) {
      setState((prev) => ({
        ...prev,
        status: 'start_uncertain',
        isStarting: false,
        isRunning: false,
        error: error?.message || 'A previous Create Data start must be recovered first.',
      }));
      if (abortRef.current === controller) abortRef.current = null;
      return;
    }
    const startKey = paidStartRef.current.key;

    setState({
      ...INITIAL_STATE,
      isStarting: true,
      status: 'starting',
    });

    try {
      const formData = new FormData();
      formData.append('prompt', prompt);
      formData.append(
        'confirmWorkflowCosts',
        confirmWorkflowCosts ? 'true' : 'false',
      );

      const started = await requestCreateDataJson('/api/create-data/start', {
        ...withIdempotencyHeader(
          '/api/create-data/start',
          startKey,
          { method: 'POST', body: formData },
        ),
        signal,
      });
      if (typeof started?.batchId !== 'string' || !started.batchId) {
        throw new Error('Create Data start returned an invalid batch identifier.');
      }
      let nextState = {
        ...INITIAL_STATE,
        batchId: started.batchId,
        totalFiles: Number(started.totalFiles || 0),
        status: 'starting',
        isRunning: true,
      };
      storeActiveBatchId(started.batchId);
      clearPaidStartAttempt(paidStartRef.current);
      paidStartRef.current = null;
      setState(nextState);

      if (stopRequestedRef.current) {
        setState((prev) => ({
          ...prev,
          batchId: started.batchId,
          status: 'stopping',
          isStarting: false,
          isRunning: true,
          error: 'Start was accepted; waiting for the server to confirm the stop.',
        }));
        try {
          const stopped = await requestCreateDataJson(
            `/api/create-data/${started.batchId}/stop`,
            { method: 'POST' },
          );
          clearActiveBatchId(started.batchId);
          setState(buildCreateDataState(stopped, {
            ...nextState,
            status: 'cancelled',
          }));
          return;
        } catch (stopError) {
          nextState = {
            ...nextState,
            error: `The start was accepted, but the stop failed; monitoring continues: ${
              stopError?.message || 'Unknown error'
            }`,
          };
          setState(nextState);
        } finally {
          stopRequestedRef.current = false;
        }
      }

      await pollBatch(started.batchId, controller, nextState);
    } catch (error) {
      const ambiguous = isAmbiguousPaidStartFailure(error?.status);
      if (!ambiguous) {
        clearPaidStartAttempt(paidStartRef.current);
        paidStartRef.current = null;
      }
      if (error?.name === 'AbortError') {
        return;
      }
      setState({
        ...INITIAL_STATE,
        status: ambiguous ? 'start_uncertain' : 'errored',
        error: ambiguous
          ? `${
            stopRequestedRef.current ? 'Stop was requested, but ' : ''
          }the Create Data start may already have been accepted. The exact recovery key was retained; recover this start before beginning another.`
          : error?.message || 'Create Data failed.',
      });
    } finally {
      stopRequestedRef.current = false;
      if (abortRef.current === controller) {
        abortRef.current = null;
      }
    }
  }, [pollBatch, state.isLoadingResult, state.isRunning, state.isStarting]);

  const stop = useCallback(async () => {
    const batchId = state.batchId || readActiveBatchId();

    if (!batchId) {
      if (state.isStarting || abortRef.current) {
        stopRequestedRef.current = true;
        setState((prev) => ({
          ...prev,
          status: 'stopping',
          isStarting: true,
          isRunning: true,
          error: 'Stop requested. Waiting for the server to resolve the start before confirming cancellation.',
        }));
        return false;
      }
      return false;
    }

    setState((prev) => ({
      ...prev,
      status: 'stopping',
      isStarting: false,
      isRunning: true,
    }));

    try {
      const stopped = await requestCreateDataJson(
        `/api/create-data/${batchId}/stop`,
        { method: 'POST' },
      );
      clearActiveBatchId(batchId);
      abortRef.current?.abort();
      abortRef.current = null;
      setState(buildCreateDataState(stopped, { batchId, status: 'cancelled' }));
      return true;
    } catch (error) {
      if (error?.status === 404) {
        clearActiveBatchId(batchId);
        abortRef.current?.abort();
        abortRef.current = null;
        setState({
          ...INITIAL_STATE,
          status: 'errored',
          error: 'The Create Data batch is no longer available on the server.',
        });
        return false;
      }
      setState((prev) => ({
        ...prev,
        batchId,
        status: 'running',
        isStarting: false,
        isRunning: true,
        error: `Failed to stop Create Data; monitoring continues: ${
          error?.message || 'Unknown error'
        }`,
      }));
      return false;
    }
  }, [state.batchId, state.isStarting]);

  const reset = useCallback(() => {
    if (
      state.isStarting
      || state.isRunning
      || state.isLoadingResult
      || state.status === 'start_uncertain'
    ) {
      return false;
    }
    setState(INITIAL_STATE);
    return true;
  }, [state.isLoadingResult, state.isRunning, state.isStarting, state.status]);

  useEffect(() => {
    const batchId = readActiveBatchId();
    if (!batchId) return undefined;
    if (unauthorizedRef.current && !accessToken) return undefined;

    unauthorizedRef.current = false;
    const controller = new AbortController();
    abortRef.current?.abort();
    abortRef.current = controller;
    const seedState = {
      ...INITIAL_STATE,
      batchId,
      status: 'running',
      isRunning: true,
    };
    setState(seedState);

    pollBatch(batchId, controller, seedState)
      .catch((error) => {
        if (error?.name === 'AbortError') return;
        setState((prev) => ({
          ...prev,
          batchId,
          status: 'running',
          isStarting: false,
          isRunning: true,
          error: `Monitoring interrupted; retry after access is restored: ${
            error?.message || 'Unknown error'
          }`,
        }));
      })
      .finally(() => {
        if (abortRef.current === controller) {
          abortRef.current = null;
        }
      });

    return () => {
      controller.abort();
      if (abortRef.current === controller) {
        abortRef.current = null;
      }
    };
  }, [accessToken, pollBatch]);

  return { state, start, stop, reset };
}
