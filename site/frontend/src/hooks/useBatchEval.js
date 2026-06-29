import { useCallback, useEffect, useRef, useState } from 'react';
import { abortableSleep } from '../utils/abortableSleep';
import { requestJson } from '../utils/api';

const INITIAL_STATE = {
  batchId: null,
  status: 'idle',
  isStarting: false,
  isRunning: false,
  mode: 'prior_runs',
  totalFiles: 0,
  doneFiles: 0,
  completedFiles: 0,
  failedFiles: 0,
  cancelledFiles: 0,
  outputFolder: '',
  entries: [],
  error: '',
  availableRuns: [],
  loadingRuns: false,
};

const TERMINAL = new Set(['completed', 'errored', 'cancelled']);

function buildState(payload, fallback = {}) {
  const completed = Number(payload?.completedFiles || 0);
  const failed = Number(payload?.failedFiles || 0);
  const cancelled = Number(payload?.cancelledFiles || 0);
  const done = Number(payload?.doneFiles ?? (completed + failed + cancelled));
  const status = payload?.status || fallback.status || 'idle';
  return {
    batchId: payload?.batchId || fallback.batchId || null,
    status,
    isStarting: false,
    isRunning: !TERMINAL.has(status) && status !== 'idle',
    mode: payload?.mode || fallback.mode || 'prior_runs',
    totalFiles: Number(payload?.totalFiles || fallback.totalFiles || 0),
    doneFiles: done, completedFiles: completed, failedFiles: failed, cancelledFiles: cancelled,
    outputFolder: payload?.outputFolder || '',
    entries: Array.isArray(payload?.entries) ? payload.entries : [],
    error: typeof payload?.error === 'string' ? payload.error : '',
    availableRuns: fallback.availableRuns || [],
    loadingRuns: false,
  };
}

export function useBatchEval() {
  const [state, setState] = useState(INITIAL_STATE);
  const abortRef = useRef(null);

  useEffect(() => () => { abortRef.current?.abort(); }, []);

  const loadRuns = useCallback(async () => {
    setState(prev => ({ ...prev, loadingRuns: true }));
    try {
      const data = await requestJson('/api/eval-batch/outputs');
      setState(prev => ({ ...prev, availableRuns: data.runs || [], loadingRuns: false }));
    } catch {
      setState(prev => ({ ...prev, loadingRuns: false }));
    }
  }, []);

  const start = useCallback(async ({ mode, selectedFolders, sourceFile, generatedFiles }) => {
    if (state.isStarting || state.isRunning) return;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    const { signal } = controller;

    setState(prev => ({ ...INITIAL_STATE, availableRuns: prev.availableRuns, isStarting: true, status: 'starting', mode }));

    try {
      const formData = new FormData();
      formData.append('mode', mode);
      if (mode === 'prior_runs') {
        formData.append('folders', JSON.stringify(selectedFolders || []));
      } else {
        if (sourceFile) formData.append('source_file', sourceFile);
        (generatedFiles || []).forEach(f => formData.append('generated_files', f));
      }

      const started = await requestJson('/api/eval-batch/start', { method: 'POST', body: formData, signal });
      let nextState = { ...INITIAL_STATE, batchId: started.batchId, totalFiles: Number(started.totalFiles || 0), status: 'starting', isRunning: true, mode, availableRuns: state.availableRuns };
      setState(nextState);

      while (!signal.aborted) {
        const statusPayload = await requestJson(`/api/eval-batch/${started.batchId}/status`, { signal });
        nextState = buildState(statusPayload, nextState);
        setState(nextState);
        if (TERMINAL.has(statusPayload.status)) {
          try {
            const result = await requestJson(`/api/eval-batch/${started.batchId}/result`, { signal });
            setState(buildState(result, nextState));
          } catch { setState(nextState); }
          break;
        }
        await abortableSleep(600, signal);
      }
    } catch (err) {
      if (err?.name === 'AbortError') return;
      setState(prev => ({ ...prev, status: 'errored', isStarting: false, isRunning: false, error: err?.message || 'Batch eval failed.' }));
    } finally {
      if (abortRef.current === controller) abortRef.current = null;
    }
  }, [state.isRunning, state.isStarting]);

  const stop = useCallback(async () => {
    const batchId = state.batchId;
    abortRef.current?.abort();
    abortRef.current = null;
    if (!batchId) { setState(prev => ({ ...prev, status: 'idle', isRunning: false })); return; }
    setState(prev => ({ ...prev, status: 'stopping', isRunning: true }));
    try {
      const stopped = await requestJson(`/api/eval-batch/${batchId}/stop`, { method: 'POST' });
      setState(prev => buildState(stopped, { ...prev, status: 'cancelled' }));
    } catch (err) {
      setState(prev => ({ ...prev, status: 'errored', isRunning: false, error: err?.message || 'Failed to stop.' }));
    }
  }, [state.batchId]);

  return { state, start, stop, loadRuns };
}
