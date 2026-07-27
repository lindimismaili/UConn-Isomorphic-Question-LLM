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

export const ACTIVE_EVAL_BATCH_STORAGE_KEY = 'activeEvalBatchId';

function fileIntent(file) {
  return {
    name: String(file?.name || ''),
    size: Number(file?.size || 0),
    lastModified: Number(file?.lastModified || 0),
    type: String(file?.type || ''),
  };
}

function readActiveBatchId() {
  try {
    return sessionStorage.getItem(ACTIVE_EVAL_BATCH_STORAGE_KEY) || '';
  } catch {
    return '';
  }
}

function storeActiveBatchId(batchId) {
  try {
    if (batchId) {
      sessionStorage.setItem(ACTIVE_EVAL_BATCH_STORAGE_KEY, batchId);
    }
  } catch {
    // Recovery remains best-effort when browser storage is unavailable.
  }
}

function clearActiveBatchId(batchId) {
  try {
    const stored = sessionStorage.getItem(ACTIVE_EVAL_BATCH_STORAGE_KEY);
    if (!batchId || stored === batchId) {
      sessionStorage.removeItem(ACTIVE_EVAL_BATCH_STORAGE_KEY);
    }
  } catch {
    // Storage is optional; the server remains authoritative.
  }
}

async function requestBatchEvalJson(url, { signal, fallbackError, ...options } = {}) {
  const response = await fetchWithAbort(url, options, signal);
  if (!response.ok) {
    const error = new Error(
      await readErrorDetail(
        response,
        fallbackError || `Batch Evaluation request failed (${response.status})`,
      ),
    );
    error.status = response.status;
    throw error;
  }
  return response.json();
}

const INITIAL_STATE = {
  batchId: null,
  status: 'idle',
  isStarting: false,
  isRunning: false,
  isLoadingResult: false,
  mode: 'prior_runs',
  totalFiles: 0,
  doneFiles: 0,
  completedFiles: 0,
  failedFiles: 0,
  cancelledFiles: 0,
  blockedFiles: 0,
  outputFolder: '',
  entries: [],
  error: '',
  availableRuns: [],
  loadingRuns: false,
  runsError: '',
  policy: {
    maxEntries: 5,
    maxItems: 100,
    graderCount: 8,
    graderMaxCompletionTokens: 4096,
    maxUploadExtractionFiles: 6,
    maxUploadExtractionCalls: 12,
    maxUploadBootstrapCalls: 6,
  },
  projectedEvaluationItems: 0,
  projectedGraderCalls: 0,
  projectedExtractionFiles: 0,
  projectedExtractionCalls: 0,
  projectedBootstrapCalls: 0,
};

export const BATCH_EVAL_TERMINAL_STATUSES = new Set([
  'completed',
  'errored',
  'cancelled',
  'blocked',
]);

export function buildBatchEvalState(payload, fallback = {}) {
  const completed = Number(payload?.completedFiles || 0);
  const failed = Number(payload?.failedFiles || 0);
  const cancelled = Number(payload?.cancelledFiles || 0);
  const blocked = Number(payload?.blockedFiles || 0);
  const done = Number(
    payload?.doneFiles ?? (completed + failed + cancelled + blocked)
  );
  const status = payload?.status || fallback.status || 'idle';
  return {
    batchId: payload?.batchId || fallback.batchId || null,
    status,
    isStarting: false,
    isRunning: !BATCH_EVAL_TERMINAL_STATUSES.has(status) && status !== 'idle',
    isLoadingResult: false,
    mode: payload?.mode || fallback.mode || 'prior_runs',
    totalFiles: Number(payload?.totalFiles || fallback.totalFiles || 0),
    doneFiles: done,
    completedFiles: completed,
    failedFiles: failed,
    cancelledFiles: cancelled,
    blockedFiles: blocked,
    outputFolder: payload?.outputFolder || '',
    entries: Array.isArray(payload?.entries) ? payload.entries : [],
    error: typeof payload?.error === 'string' ? payload.error : '',
    availableRuns: fallback.availableRuns || [],
    loadingRuns: false,
    runsError: fallback.runsError || '',
    policy: fallback.policy || INITIAL_STATE.policy,
    projectedEvaluationItems: Number(
      payload?.projectedEvaluationItems || fallback.projectedEvaluationItems || 0
    ),
    projectedGraderCalls: Number(
      payload?.projectedGraderCalls || fallback.projectedGraderCalls || 0
    ),
    projectedExtractionFiles: Number(
      payload?.projectedExtractionFiles
        ?? fallback.projectedExtractionFiles
        ?? 0
    ),
    projectedExtractionCalls: Number(
      payload?.projectedExtractionCalls
        ?? fallback.projectedExtractionCalls
        ?? 0
    ),
    projectedBootstrapCalls: Number(
      payload?.projectedBootstrapCalls
        ?? fallback.projectedBootstrapCalls
        ?? 0
    ),
  };
}

export function useBatchEval() {
  const [state, setState] = useState(INITIAL_STATE);
  const stateRef = useRef(state);
  const abortRef = useRef(null);
  const paidStartRef = useRef(null);
  const unauthorizedRef = useRef(false);
  const stopRequestedRef = useRef(false);
  const accessToken = getAccessToken();

  useEffect(() => () => { abortRef.current?.abort(); }, []);
  useEffect(() => {
    stateRef.current = state;
  }, [state]);

  const loadRuns = useCallback(async () => {
    setState(prev => ({ ...prev, loadingRuns: true, runsError: '' }));
    try {
      const data = await requestBatchEvalJson('/api/eval-batch/outputs');
      setState(prev => ({
        ...prev,
        availableRuns: data.runs || [],
        policy: { ...prev.policy, ...(data.policy || {}) },
        loadingRuns: false,
        runsError: '',
      }));
    } catch (error) {
      setState(prev => ({
        ...prev,
        loadingRuns: false,
        runsError: error?.message || 'Unable to load completed runs.',
      }));
    }
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
        statusPayload = await requestBatchEvalJson(
          `/api/eval-batch/${batchId}/status`,
          { signal },
        );
        unauthorizedRef.current = false;
        statusRetryAttempt = 0;
      } catch (error) {
        if (error?.name === 'AbortError' || signal.aborted) return;
        if (error?.status === 404) {
          clearActiveBatchId(batchId);
          setState((prev) => ({
            ...INITIAL_STATE,
            availableRuns: prev.availableRuns,
            policy: prev.policy,
            status: 'errored',
            error: 'The previous Batch Evaluation is no longer available on the server.',
          }));
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
      nextState = buildBatchEvalState(statusPayload, nextState);
      if (BATCH_EVAL_TERMINAL_STATUSES.has(statusPayload.status)) {
        nextState = { ...nextState, isLoadingResult: true };
      }
      setState(nextState);

      if (BATCH_EVAL_TERMINAL_STATUSES.has(statusPayload.status)) {
        let resultRetryAttempt = 0;
        while (!signal.aborted) {
          try {
            const result = await requestBatchEvalJson(
              `/api/eval-batch/${batchId}/result`,
              { signal },
            );
            if (!signal.aborted) {
              nextState = buildBatchEvalState(result, nextState);
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
                error: 'The completed Batch Evaluation result is no longer available on the server.',
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
              error: `Batch finished; detailed results are temporarily unavailable and will be retried: ${
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

  const start = useCallback(async ({
    mode,
    selectedFolders,
    sourceFile,
    generatedFiles,
    confirmGraderCalls,
  }) => {
    if (state.isStarting || state.isRunning || state.isLoadingResult) return;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    const { signal } = controller;
    stopRequestedRef.current = false;
    const intentSignature = JSON.stringify({
      mode,
      selectedFolders: selectedFolders || [],
      sourceFile: fileIntent(sourceFile),
      generatedFiles: (generatedFiles || []).map(fileIntent),
      confirmGraderCalls: Boolean(confirmGraderCalls),
    });
    try {
      if (!paidStartRef.current || paidStartRef.current.intentSignature !== intentSignature) {
        paidStartRef.current = {
          ...getOrCreatePaidStartAttempt(
            'eval-batch',
            '/api/eval-batch/start',
            intentSignature,
          ),
          intentSignature,
        };
      }
    } catch (error) {
      setState(prev => ({
        ...prev,
        status: 'start_uncertain',
        isStarting: false,
        isRunning: false,
        error: error?.message || 'A previous Batch Evaluation start must be recovered first.',
      }));
      if (abortRef.current === controller) abortRef.current = null;
      return;
    }
    const startKey = paidStartRef.current.key;

    setState(prev => ({ ...INITIAL_STATE, availableRuns: prev.availableRuns, isStarting: true, status: 'starting', mode }));

    try {
      const formData = new FormData();
      formData.append('mode', mode);
      formData.append('confirm_grader_calls', confirmGraderCalls ? 'true' : 'false');
      if (mode === 'prior_runs') {
        formData.append('folders', JSON.stringify(selectedFolders || []));
      } else {
        if (sourceFile) formData.append('source_file', sourceFile);
        (generatedFiles || []).forEach(f => formData.append('generated_files', f));
      }

      const started = await requestBatchEvalJson('/api/eval-batch/start', {
        ...withIdempotencyHeader(
          '/api/eval-batch/start',
          startKey,
          { method: 'POST', body: formData },
        ),
        signal,
      });
      if (typeof started?.batchId !== 'string' || !started.batchId) {
        throw new Error('Batch Evaluation start returned an invalid batch identifier.');
      }
      let nextState = {
        ...INITIAL_STATE,
        batchId: started.batchId,
        totalFiles: Number(started.totalFiles || 0),
        status: 'starting',
        isRunning: true,
        mode,
        availableRuns: state.availableRuns,
        policy: state.policy,
        projectedEvaluationItems: Number(started.projectedEvaluationItems || 0),
        projectedGraderCalls: Number(started.projectedGraderCalls || 0),
        projectedExtractionFiles: Number(started.projectedExtractionFiles || 0),
        projectedExtractionCalls: Number(started.projectedExtractionCalls || 0),
        projectedBootstrapCalls: Number(started.projectedBootstrapCalls || 0),
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
          const stopped = await requestBatchEvalJson(
            `/api/eval-batch/${started.batchId}/stop`,
            { method: 'POST' },
          );
          clearActiveBatchId(started.batchId);
          setState(prev => buildBatchEvalState(stopped, {
            ...prev,
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
    } catch (err) {
      const ambiguous = isAmbiguousPaidStartFailure(err?.status);
      if (!ambiguous) {
        clearPaidStartAttempt(paidStartRef.current);
        paidStartRef.current = null;
      }
      if (err?.name === 'AbortError') return;
      setState(prev => ({
        ...prev,
        status: ambiguous ? 'start_uncertain' : 'errored',
        isStarting: false,
        isRunning: false,
        error: ambiguous
          ? `${
            stopRequestedRef.current ? 'Stop was requested, but ' : ''
          }the Batch Evaluation start may already have been accepted. The exact recovery key was retained; recover this start before beginning another.`
          : err?.message || 'Batch eval failed.',
      }));
    } finally {
      stopRequestedRef.current = false;
      if (abortRef.current === controller) abortRef.current = null;
    }
  }, [
    pollBatch,
    state.isLoadingResult,
    state.isRunning,
    state.isStarting,
    state.availableRuns,
    state.policy,
  ]);

  const stop = useCallback(async () => {
    const batchId = state.batchId || readActiveBatchId();
    if (!batchId) {
      if (state.isStarting || abortRef.current) {
        stopRequestedRef.current = true;
        setState(prev => ({
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
    setState(prev => ({ ...prev, status: 'stopping', isRunning: true }));
    try {
      const stopped = await requestBatchEvalJson(
        `/api/eval-batch/${batchId}/stop`,
        { method: 'POST' },
      );
      clearActiveBatchId(batchId);
      abortRef.current?.abort();
      abortRef.current = null;
      setState(prev => buildBatchEvalState(stopped, { ...prev, status: 'cancelled' }));
      return true;
    } catch (err) {
      if (err?.status === 404) {
        clearActiveBatchId(batchId);
        abortRef.current?.abort();
        abortRef.current = null;
        setState((prev) => ({
          ...INITIAL_STATE,
          availableRuns: prev.availableRuns,
          policy: prev.policy,
          status: 'errored',
          error: 'The Batch Evaluation is no longer available on the server.',
        }));
        return false;
      }
      setState(prev => ({
        ...prev,
        batchId,
        status: 'running',
        isStarting: false,
        isRunning: true,
        error: `Failed to stop Batch Evaluation; monitoring continues: ${
          err?.message || 'Unknown error'
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
    setState(prev => ({
      ...INITIAL_STATE,
      availableRuns: prev.availableRuns,
      policy: prev.policy,
    }));
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
      availableRuns: stateRef.current.availableRuns,
      policy: stateRef.current.policy,
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

  return { state, start, stop, reset, loadRuns };
}
