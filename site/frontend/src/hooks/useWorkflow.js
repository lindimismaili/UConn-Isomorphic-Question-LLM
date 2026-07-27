import { useCallback, useEffect, useRef, useState } from 'react';

import { abortableSleep } from '../utils/abortableSleep';
import {
  clearPaidStartAttempt,
  fetchWithAbort,
  getOrCreatePaidStartAttempt,
  isAmbiguousPaidStartFailure,
  persistPaidStartAttempt,
  readErrorDetail,
  requestJson,
  withIdempotencyHeader,
} from '../utils/api';
import { getDerivedRegistry, loadAgentRegistry } from './useAgentRegistry';
/** Polling interval used while waiting for workflow status updates. */
export const STATUS_POLL_INTERVAL_MS = 1_500;
export const HIDDEN_STATUS_POLL_INTERVAL_MS = 7_500;
export const ACTIVE_WORKFLOW_STORAGE_KEY = 'activeWorkflowId';
export const WORKFLOW_RETRY_DELAYS_MS = Object.freeze([1_000, 2_000, 5_000, 10_000]);

function fileIntent(file) {
  const source = file?.file || file;
  return {
    name: String(file?.name || source?.name || ''),
    source: String(file?.source || ''),
    testFile: String(file?.testFile || ''),
    size: Number(source?.size || 0),
    lastModified: Number(source?.lastModified || 0),
    type: String(source?.type || ''),
  };
}

function paidStartAttempt(ref, intent) {
  const signature = JSON.stringify(intent);
  if (!ref.current || ref.current.signature !== signature) {
    const stored = getOrCreatePaidStartAttempt(
      'workflow',
      intent.endpoint,
      signature,
    );
    ref.current = {
      signature,
      ...stored,
      preparedUpload: stored.preparedUpload || null,
    };
  }
  return ref.current;
}

function clearWorkflowPaidStart(ref, attempt) {
  clearPaidStartAttempt(attempt);
  if (ref.current?.key === attempt?.key) {
    ref.current = null;
  }
}

function readActiveWorkflowId() {
  try {
    return sessionStorage.getItem(ACTIVE_WORKFLOW_STORAGE_KEY) || '';
  } catch {
    return '';
  }
}

function storeActiveWorkflowId(workflowId) {
  try {
    if (workflowId) sessionStorage.setItem(ACTIVE_WORKFLOW_STORAGE_KEY, workflowId);
  } catch {
    // Reconnect remains best-effort when storage is unavailable.
  }
}

function clearActiveWorkflowId(workflowId) {
  try {
    const stored = sessionStorage.getItem(ACTIVE_WORKFLOW_STORAGE_KEY);
    if (!workflowId || stored === workflowId) {
      sessionStorage.removeItem(ACTIVE_WORKFLOW_STORAGE_KEY);
    }
  } catch {
    // Storage is optional; the server remains authoritative.
  }
}

function currentPollInterval() {
  return typeof document !== 'undefined' && document.visibilityState === 'hidden'
    ? HIDDEN_STATUS_POLL_INTERVAL_MS
    : STATUS_POLL_INTERVAL_MS;
}

/** Return whether an idempotent workflow read should be retried automatically. */
export function isRetryableWorkflowReadStatus(status) {
  return status === 408
    || status === 425
    || status === 429
    || (status >= 500 && status <= 599);
}

function retryAfterMilliseconds(response) {
  const raw = response?.headers?.get?.('Retry-After')?.trim();
  if (!raw) return 0;

  const seconds = Number(raw);
  if (Number.isFinite(seconds) && seconds >= 0) {
    return seconds * 1_000;
  }

  const retryAt = Date.parse(raw);
  return Number.isFinite(retryAt) ? Math.max(0, retryAt - Date.now()) : 0;
}

/** Return a capped retry delay, honoring a bounded Retry-After when present. */
export function workflowRetryDelay(attempt, response) {
  const safeAttempt = Number.isInteger(attempt) && attempt > 0 ? attempt : 0;
  const index = Math.min(safeAttempt, WORKFLOW_RETRY_DELAYS_MS.length - 1);
  const baseDelay = WORKFLOW_RETRY_DELAYS_MS[index];
  const requestedDelay = retryAfterMilliseconds(response);
  return Math.min(
    WORKFLOW_RETRY_DELAYS_MS.at(-1),
    Math.max(baseDelay, requestedDelay),
  );
}

/**
 * Formats elapsed seconds for log messages.
 *
 * @param {number} seconds
 * @returns {string}
 */
function formatSeconds(seconds) {
  if (typeof seconds !== 'number' || Number.isNaN(seconds)) return '';
  return `${seconds.toFixed(2)}s`;
}

/**
 * Extracts question counts from agent outputs using the registry's per-agent
 * `countField` (the output array holding that stage's questions).
 *
 * @param {string} agentKey - Workflow stage key.
 * @param {object} output - Agent output payload.
 * @returns {number | null}
 */
function getQuestionCount(agentKey, output) {
  if (!output || typeof output !== 'object') return null;
  const countField = (getDerivedRegistry()?.countFields ?? {})[agentKey];
  if (!countField) return null;
  return Array.isArray(output[countField]) ? output[countField].length : null;
}

/**
 * Converts workflow status payloads into the reducer-friendly agent patch.
 *
 * @param {object} agentStatus - Agent status payload from `/api/workflow/:id/status`.
 * @returns {object} Partial agent update with normalized dates and text.
 */
function buildAgentPatch(agentStatus) {
  const data = agentStatus?.data || {};
  const patch = {};

  if (Object.prototype.hasOwnProperty.call(data, 'startTime')) {
    patch.startTime = data.startTime ? new Date(data.startTime) : null;
  }
  if (Object.prototype.hasOwnProperty.call(data, 'endTime')) {
    patch.endTime = data.endTime ? new Date(data.endTime) : null;
  }
  if (Object.prototype.hasOwnProperty.call(data, 'executionTime')) {
    patch.executionTime = data.executionTime ?? null;
  }
  if (Object.prototype.hasOwnProperty.call(data, 'input')) {
    patch.inputText = data.input ?? '';
  }
  if (Object.prototype.hasOwnProperty.call(data, 'output')) {
    patch.outputText = data.output ?? '';
  }

  const inputFrom = data.receivedFrom ?? agentStatus?.receivedFrom;
  const outputTo = data.sentTo ?? agentStatus?.sentTo;
  const model = data.model ?? agentStatus?.model;
  const reasoningLevel =
    data.reasoningLevel ??
    data.reasoning_level ??
    data.reasoning ??
    agentStatus?.reasoningLevel ??
    agentStatus?.reasoning_level ??
    agentStatus?.reasoning;

  if (inputFrom !== undefined && inputFrom !== null && inputFrom !== '') {
    patch.inputFrom = inputFrom;
  }
  if (outputTo !== undefined && outputTo !== null && outputTo !== '') {
    patch.outputTo = outputTo;
  }
  if (model !== undefined && model !== null && model !== '') {
    patch.model = model;
  }
  if (reasoningLevel !== undefined && reasoningLevel !== null && reasoningLevel !== '') {
    patch.reasoningLevel = reasoningLevel;
  }

  return patch;
}

/**
 * Drives the primary workflow lifecycle for the app.
 *
 * Touches `/api/upload`, `/api/workflow/start`, `/api/workflow/:id/status`,
 * and `/api/workflow/:id/result`.
 *
 * @param {Function} dispatch - App reducer dispatcher.
 * @returns {{run: Function, abort: Function}}
 */
export function useWorkflow(dispatch, { enabled = true } = {}) {
  const abortRef = useRef(null);
  const inFlightRef = useRef(null);
  const runIdRef = useRef(0);
  const workflowIdRef = useRef('');
  const stopPromiseRef = useRef(null);
  const paidStartRef = useRef(null);
  const stopRequestedRef = useRef(false);
  const startPhaseRef = useRef('idle');
  const [isStopping, setIsStopping] = useState(false);

  const abort = useCallback(() => {
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
    inFlightRef.current = null;
  }, []);

  /**
   * Polls a running workflow and mirrors backend state into the UI reducer.
   *
   * @param {string} workflowId - Backend workflow id.
   * @param {number} setupElapsedSeconds - Local setup time before polling began.
   * @param {AbortSignal} signal - Abort signal for this run.
   */
  const pollWorkflow = useCallback(async (workflowId, setupElapsedSeconds, signal, runId) => {
    // The first pipeline node (runtime setup) is simulated locally, so polling
    // tracks every stage after it. If the registry fetch failed earlier, fall
    // back to the stage keys reported by the status payload itself.
    const registry = await loadAgentRegistry();
    if (registry) dispatch({ type: 'AGENTS_LOADED', config: registry });
    let agentKeys = getDerivedRegistry()?.pipelineKeys.slice(1) ?? null;
    const statusByAgent = {};
    const updateSignatures = {};
    let statusRetryAttempt = 0;
    let monitoringInterrupted = false;

    const markMonitoringInterrupted = (message) => {
      if (monitoringInterrupted) return;
      monitoringInterrupted = true;
      dispatch({ type: 'WORKFLOW_MONITORING_INTERRUPTED', message });
    };

    const markMonitoringResumed = () => {
      if (!monitoringInterrupted) return;
      monitoringInterrupted = false;
      dispatch({ type: 'WORKFLOW_MONITORING_RESUMED' });
    };

    const clearTrackedWorkflow = () => {
      clearActiveWorkflowId(workflowId);
      workflowIdRef.current = '';
    };

    const finishUnavailableWorkflow = () => {
      clearTrackedWorkflow();
      dispatch({ type: 'WORKFLOW_ABORTED', clearWorkflowId: true });
      dispatch({
        type: 'LOG',
        message: 'The workflow is no longer available on the server.',
        level: 'warning',
      });
    };

    const waitForRetry = async (attempt, response) => {
      await abortableSleep(workflowRetryDelay(attempt, response), signal);
    };

    while (true) {
      if (signal.aborted || inFlightRef.current !== runId) return;

      let statusRes;
      try {
        statusRes = await fetchWithAbort(`/api/workflow/${workflowId}/status`, {}, signal);
      } catch (error) {
        if (error.name === 'AbortError') throw error;
        markMonitoringInterrupted(
          'Workflow monitoring was interrupted; retrying automatically.',
        );
        await waitForRetry(statusRetryAttempt);
        statusRetryAttempt += 1;
        continue;
      }

      if (statusRes.status === 404) {
        finishUnavailableWorkflow();
        return;
      }
      if (!statusRes.ok) {
        if (isRetryableWorkflowReadStatus(statusRes.status)) {
          markMonitoringInterrupted(
            `Workflow monitoring returned ${statusRes.status}; retrying automatically.`,
          );
          await waitForRetry(statusRetryAttempt, statusRes);
          statusRetryAttempt += 1;
          continue;
        }
        const detail = await readErrorDetail(
          statusRes,
          `Workflow monitoring paused (${statusRes.status}).`,
        );
        markMonitoringInterrupted(`Workflow monitoring paused: ${detail}`);
        return;
      }

      let status;
      try {
        status = await statusRes.json();
      } catch {
        markMonitoringInterrupted(
          'Workflow monitoring received an invalid response; retrying automatically.',
        );
        await waitForRetry(statusRetryAttempt);
        statusRetryAttempt += 1;
        continue;
      }
      statusRetryAttempt = 0;
      if (status.status !== 'completed') markMonitoringResumed();
      if (!agentKeys) agentKeys = Object.keys(status.agents ?? {});

      // The Starter node represents backend runtime setup (file upload + OpenAI
      // container bootstrap). Keep it "thinking" until a real agent actually
      // starts on the backend, then complete it. Idempotent in the reducer.
      const anyAgentStarted = agentKeys.some((key) => {
        const agentState = status.agents?.[key]?.status;
        return agentState && agentState !== 'inactive' && agentState !== 'queued';
      });
      if (anyAgentStarted || status.status === 'completed') {
        dispatch({ type: 'SETUP_COMPLETE' });
      }

      for (const key of agentKeys) {
        const agentStatus = status.agents?.[key];
        if (agentStatus) {
          const data = agentStatus.data || {};
          const prevStatus = statusByAgent[key];
          const nextStatus = agentStatus.status;

          if (prevStatus !== nextStatus) {
            const label = (getDerivedRegistry()?.agentLabels ?? {})[key] || key;
            if (nextStatus === 'thinking') {
              dispatch({ type: 'LOG', message: `${label} started`, level: 'info' });
            } else if (nextStatus === 'completed') {
              const elapsed = formatSeconds(data.executionTime);
              const timeSuffix = elapsed ? ` in ${elapsed}` : '';
              dispatch({ type: 'LOG', message: `${label} completed${timeSuffix}`, level: 'success' });

              const countLabel = (getDerivedRegistry()?.countLabels ?? {})[key];
              const count = getQuestionCount(key, data.output);
              if (countLabel && typeof count === 'number') {
                dispatch({ type: 'LOG', message: `${countLabel}: ${count} questions`, level: 'info' });
              }
            } else if (nextStatus === 'errored') {
              dispatch({ type: 'LOG', message: `${label} errored`, level: 'error' });
            } else if (nextStatus === 'cancelled') {
              dispatch({ type: 'LOG', message: `${label} stopped`, level: 'info' });
            }
            statusByAgent[key] = nextStatus;
          }

          const patch = buildAgentPatch(agentStatus);
          const updateSignature = JSON.stringify([agentStatus.status, patch]);
          if (updateSignatures[key] !== updateSignature) {
            dispatch({
              type: 'AGENT_UPDATE',
              agentKey: key,
              status: agentStatus.status,
              data: patch,
            });
            updateSignatures[key] = updateSignature;
          }
        }
      }

      if (status.status === 'completed') {
        const agentsElapsedSeconds = agentKeys.reduce((total, key) => {
          const value = status.agents?.[key]?.data?.executionTime;
          return total + (typeof value === 'number' ? value : 0);
        }, 0);
        const totalElapsedSeconds = setupElapsedSeconds + agentsElapsedSeconds;

        let resultRetryAttempt = 0;
        while (true) {
          let resultRes;
          try {
            resultRes = await fetchWithAbort(
              `/api/workflow/${workflowId}/result`,
              {},
              signal,
            );
          } catch (error) {
            if (error.name === 'AbortError') throw error;
            markMonitoringInterrupted(
              'The workflow completed, but its result is temporarily unavailable; retrying automatically.',
            );
            await waitForRetry(resultRetryAttempt);
            resultRetryAttempt += 1;
            continue;
          }

          if (resultRes.status === 404) {
            finishUnavailableWorkflow();
            return;
          }
          if (!resultRes.ok) {
            if (isRetryableWorkflowReadStatus(resultRes.status)) {
              markMonitoringInterrupted(
                `The workflow completed, but its result returned ${resultRes.status}; retrying automatically.`,
              );
              await waitForRetry(resultRetryAttempt, resultRes);
              resultRetryAttempt += 1;
              continue;
            }
            const detail = await readErrorDetail(
              resultRes,
              `Result monitoring paused (${resultRes.status}).`,
            );
            markMonitoringInterrupted(`Result monitoring paused: ${detail}`);
            return;
          }

          let result;
          try {
            result = await resultRes.json();
          } catch {
            markMonitoringInterrupted(
              'The workflow result was incomplete; retrying automatically.',
            );
            await waitForRetry(resultRetryAttempt);
            resultRetryAttempt += 1;
            continue;
          }

          markMonitoringResumed();
          for (const [agentKey, agentStatus] of Object.entries(result.agents || {})) {
            dispatch({
              type: 'AGENT_UPDATE',
              agentKey,
              status: agentStatus?.status,
              data: buildAgentPatch(agentStatus),
            });
          }
          dispatch({
            type: 'WORKFLOW_COMPLETE',
            result: {
              workflowId,
              outputFolder: result.outputFolder,
              files: result.files || [],
              workflowResult: result.result || null,
              elapsedTimeSeconds: totalElapsedSeconds,
            },
            elapsedTimeSeconds: totalElapsedSeconds,
          });
          clearTrackedWorkflow();
          return;
        }
      }

      if (status.status === 'blocked') {
        // Terminal guardrail block. The poller stays authoritative; surface the
        // block so the transcript can explain it and the tripped node styles.
        const blocked = status.blocked || {};
        const blockedStage =
          blocked.stage ||
          agentKeys.find((key) => status.agents?.[key]?.status === 'blocked') ||
          '';
        const label = (getDerivedRegistry()?.agentLabels ?? {})[blockedStage] || blockedStage;
        dispatch({
          type: 'WORKFLOW_BLOCKED',
          blocked: { ...blocked, stage: blockedStage },
        });
        clearTrackedWorkflow();
        dispatch({
          type: 'LOG',
          message: label ? `${label} blocked the run` : 'Workflow blocked by a guardrail',
          level: 'warning',
        });
        return;
      }

      if (status.status === 'errored' || status.status === 'lost') {
        const erroredAgent = agentKeys
          .map((key) => status.agents?.[key])
          .find((agentStatus) => agentStatus?.status === 'errored');
        const message =
          status.error ||
          erroredAgent?.data?.error ||
          'Workflow failed on the server';
        const err = new Error(message);
        err.outputFolder = status.outputFolder || '';
        clearTrackedWorkflow();
        throw err;
      }

      if (status.status === 'cancelled' || status.status === 'canceled') {
        clearTrackedWorkflow();
        dispatch({ type: 'WORKFLOW_ABORTED' });
        dispatch({ type: 'LOG', message: 'Workflow stopped', level: 'info' });
        return;
      }

      await abortableSleep(currentPollInterval(), signal);
    }
  }, [dispatch]);

  /**
   * Starts a workflow run, uploads files when needed, then polls status until
   * completion, cancellation, or failure.
   *
   * @param {string} prompt - User prompt forwarded to the backend.
   * @param {Array<object>} files - Selected files from the upload/test-file UI.
   * @param {{runEvaluations?: boolean, theme?: string}} options - Workflow execution options.
   */
  const run = useCallback(async (prompt, files, options = {}) => {
    if (
      !enabled
      || inFlightRef.current
      || workflowIdRef.current
      || readActiveWorkflowId()
    ) {
      return;
    }
    let startAttempt;
    try {
      startAttempt = paidStartAttempt(paidStartRef, {
        endpoint: '/api/workflow/start',
        prompt,
        files: files.map(fileIntent),
        runEvaluations: Boolean(options.runEvaluations),
        theme: String(options.theme || ''),
      });
    } catch (error) {
      dispatch({
        type: 'WORKFLOW_ERROR',
        message: error?.message || 'A previous workflow start must be recovered first.',
      });
      return;
    }
    const controller = new AbortController();
    const runId = runIdRef.current + 1;
    runIdRef.current = runId;
    inFlightRef.current = runId;
    abortRef.current = controller;
    stopRequestedRef.current = false;
    startPhaseRef.current = 'preparing';
    const { signal } = controller;
    const setupStartMs = Date.now();
    let paidStartSent = false;
    let paidStartStatus;
    let paidStartResolved = false;

    dispatch({
      type: 'WORKFLOW_START',
      prompt,
      fileNames: files.map((f) => f?.name).filter(Boolean),
      fileIds: files.map((f) => f?.id).filter(Boolean),
    });

    try {
      let sessionId = startAttempt.preparedUpload?.sessionId || null;
      let uploadedFileIds = startAttempt.preparedUpload?.uploadedFileIds || [];
      const uploadableFiles = files.filter((f) => {
        if (typeof File === 'undefined') return !!f.file;
        return f.file instanceof File;
      });
      const selectedTestFiles = files
        .filter((f) => f?.source === 'test' && typeof f.testFile === 'string' && f.testFile)
        .map((f) => f.testFile);

      // Step 1: Upload files
      if (uploadableFiles.length > 0 && !startAttempt.preparedUpload) {
        dispatch({ type: 'LOG', message: 'Uploading files...', level: 'info' });
        const formData = new FormData();
        uploadableFiles.forEach((f) => formData.append('files', f.file));

        const uploadRes = await fetchWithAbort('/api/upload', { method: 'POST', body: formData }, signal);
        if (!uploadRes.ok) {
          const err = await readErrorDetail(uploadRes, `Status ${uploadRes.status}`);
          throw new Error(`Upload failed: ${err}`);
        }
        const uploadResult = await uploadRes.json();
        sessionId = uploadResult.sessionId;
        uploadedFileIds = Array.isArray(uploadResult.files)
          ? uploadResult.files
            .map((file) => file?.fileId)
            .filter((fileId) => typeof fileId === 'string' && fileId)
          : [];
        if (!sessionId || uploadedFileIds.length !== uploadableFiles.length) {
          throw new Error('Upload failed: the server did not return a complete file selection.');
        }
        startAttempt.preparedUpload = {
          sessionId,
          uploadedFileIds: [...uploadedFileIds],
        };
        persistPaidStartAttempt(startAttempt);
        dispatch({ type: 'LOG', message: 'Files uploaded successfully', level: 'success' });
      }

      if (stopRequestedRef.current) {
        dispatch({ type: 'WORKFLOW_ABORTED' });
        dispatch({
          type: 'LOG',
          message: startAttempt.preparedUpload
            ? 'Workflow start cancelled before the paid request was sent; the uploaded session is retained for an explicit retry.'
            : 'Workflow start cancelled before the paid request was sent.',
          level: 'info',
        });
        return;
      }

      // Step 2: Start workflow
      dispatch({ type: 'LOG', message: 'Starting workflow...', level: 'info' });
      const startForm = new FormData();
      startForm.append('prompt', prompt);
      startForm.append('sessionId', sessionId || '');
      startForm.append('fileIds', JSON.stringify(uploadedFileIds));
      startForm.append('runEvaluation', options.runEvaluations ? 'true' : 'false');
      startForm.append(
        'confirmEvaluationCost',
        options.runEvaluations ? 'true' : 'false',
      );
      startForm.append('theme', options.theme || '');
      if (selectedTestFiles.length > 0) {
        startForm.append('testFiles', JSON.stringify(selectedTestFiles));
        dispatch({
          type: 'LOG',
          message: `Using ${selectedTestFiles.length} test file(s) from data/evaluation`,
          level: 'info',
        });
      }

      startPhaseRef.current = 'paid-start';
      paidStartSent = true;
      const startRes = await fetchWithAbort(
        '/api/workflow/start',
        withIdempotencyHeader(
          '/api/workflow/start',
          startAttempt.key,
          { method: 'POST', body: startForm },
        ),
        signal,
      );
      paidStartStatus = startRes.status;
      if (!startRes.ok) {
        const err = await readErrorDetail(startRes, `Status ${startRes.status}`);
        throw new Error(`Failed to start workflow: ${err}`);
      }
      const { workflowId } = await startRes.json();
      if (typeof workflowId !== 'string' || !workflowId) {
        throw new Error('Workflow start returned an invalid workflow identifier.');
      }
      paidStartResolved = true;
      const setupElapsedSeconds = (Date.now() - setupStartMs) / 1000;

      workflowIdRef.current = workflowId;
      storeActiveWorkflowId(workflowId);
      clearWorkflowPaidStart(paidStartRef, startAttempt);
      dispatch({ type: 'WORKFLOW_REGISTERED', workflowId });
      dispatch({ type: 'LOG', message: `Workflow started (${workflowId})`, level: 'info' });

      if (stopRequestedRef.current) {
        dispatch({
          type: 'LOG',
          message: 'The start was accepted; waiting for the server to confirm the stop.',
          level: 'info',
        });
        try {
          await requestJson(`/api/workflow/${workflowId}/stop`, {
            method: 'POST',
            fallbackError: 'The server could not stop the workflow.',
          });
          clearActiveWorkflowId(workflowId);
          workflowIdRef.current = '';
          dispatch({ type: 'WORKFLOW_ABORTED', clearWorkflowId: true });
          dispatch({ type: 'LOG', message: 'Workflow stopped by the server', level: 'info' });
          return;
        } catch (stopError) {
          dispatch({
            type: 'LOG',
            message: `Stop failed; workflow monitoring continues: ${stopError.message}`,
            level: 'error',
          });
        } finally {
          stopRequestedRef.current = false;
          setIsStopping(false);
        }
      }

      startPhaseRef.current = 'monitoring';
      await pollWorkflow(workflowId, setupElapsedSeconds, signal, runId);
    } catch (err) {
      if (
        !paidStartSent
        || (
          paidStartStatus >= 400
          && !isAmbiguousPaidStartFailure(paidStartStatus)
        )
      ) {
        clearWorkflowPaidStart(paidStartRef, startAttempt);
      }
      if (err.name === 'AbortError') {
        if (!paidStartSent || paidStartResolved) {
          dispatch({ type: 'WORKFLOW_ABORTED' });
        }
        return;
      }
      const ambiguousStart = paidStartSent && !paidStartResolved;
      dispatch({
        type: 'WORKFLOW_ERROR',
        message: ambiguousStart
          ? `${
            stopRequestedRef.current ? 'Stop was requested, but ' : ''
          }the workflow start may already have been accepted. Its exact recovery key was retained; retry only this unchanged start to recover the server response.`
          : err.message,
        outputFolder: err.outputFolder || '',
      });
    } finally {
      startPhaseRef.current = 'idle';
      stopRequestedRef.current = false;
      setIsStopping(false);
      if (abortRef.current === controller) {
        abortRef.current = null;
      }
      if (inFlightRef.current === runId) {
        inFlightRef.current = null;
      }
    }
  }, [dispatch, enabled, pollWorkflow]);

  /**
   * Starts a workflow by uploading a prepared agent input artifact.
   *
   * @param {string} startAgent - Agent key to receive the uploaded input.
   * @param {File} inputFile - JSON, JSONL, or text file containing agent input.
   * @param {string} prompt - Optional extra prompt context.
   * @param {{onStarted?: Function, onError?: Function}} [options] - Optional lifecycle callbacks.
   */
  const runFromAgentInput = useCallback(async (startAgent, inputFile, prompt = '', options = {}) => {
    if (
      !enabled
      || inFlightRef.current
      || workflowIdRef.current
      || readActiveWorkflowId()
    ) {
      return;
    }
    let startAttempt;
    try {
      startAttempt = paidStartAttempt(paidStartRef, {
        endpoint: '/api/workflow/start-from-agent-input',
        startAgent,
        prompt,
        inputFile: fileIntent(inputFile),
        runEvaluations: Boolean(options.runEvaluations),
      });
    } catch (error) {
      options.onError?.(
        error?.message || 'A previous agent-input workflow start must be recovered first.',
      );
      dispatch({
        type: 'WORKFLOW_ERROR',
        message: error?.message || 'A previous workflow start must be recovered first.',
      });
      return;
    }
    const controller = new AbortController();
    const runId = runIdRef.current + 1;
    runIdRef.current = runId;
    inFlightRef.current = runId;
    abortRef.current = controller;
    stopRequestedRef.current = false;
    startPhaseRef.current = 'preparing';
    const { signal } = controller;
    const setupStartMs = Date.now();
    let paidStartSent = false;
    let paidStartStatus;
    let paidStartResolved = false;

    dispatch({
      type: 'WORKFLOW_START',
      prompt,
      fileNames: inputFile?.name ? [inputFile.name] : [],
      startAgent,
    });

    try {
      dispatch({ type: 'LOG', message: `Uploading input for ${(getDerivedRegistry()?.agentLabels ?? {})[startAgent] || startAgent}...`, level: 'info' });
      const startForm = new FormData();
      startForm.append('startAgent', startAgent);
      startForm.append('prompt', prompt);
      startForm.append('runEvaluation', options.runEvaluations ? 'true' : 'false');
      startForm.append(
        'confirmEvaluationCost',
        options.runEvaluations ? 'true' : 'false',
      );
      startForm.append('agentInput', inputFile);

      startPhaseRef.current = 'paid-start';
      paidStartSent = true;
      const startRes = await fetchWithAbort(
        '/api/workflow/start-from-agent-input',
        withIdempotencyHeader(
          '/api/workflow/start-from-agent-input',
          startAttempt.key,
          { method: 'POST', body: startForm },
        ),
        signal,
      );
      paidStartStatus = startRes.status;
      if (!startRes.ok) {
        const detail = await readErrorDetail(startRes, `Status ${startRes.status}`);
        const err = new Error(`Failed to start agent-input workflow: ${detail}`);
        err.userMessage = detail;
        throw err;
      }

      const { workflowId } = await startRes.json();
      if (typeof workflowId !== 'string' || !workflowId) {
        throw new Error(
          'Agent-input workflow start returned an invalid workflow identifier.',
        );
      }
      paidStartResolved = true;
      const setupElapsedSeconds = (Date.now() - setupStartMs) / 1000;

      workflowIdRef.current = workflowId;
      storeActiveWorkflowId(workflowId);
      clearWorkflowPaidStart(paidStartRef, startAttempt);
      dispatch({ type: 'WORKFLOW_REGISTERED', workflowId });
      dispatch({
        type: 'LOG',
        message: `Workflow started from ${(getDerivedRegistry()?.agentLabels ?? {})[startAgent] || startAgent} (${workflowId})`,
        level: 'info',
      });
      options.onStarted?.(workflowId);

      if (stopRequestedRef.current) {
        dispatch({
          type: 'LOG',
          message: 'The start was accepted; waiting for the server to confirm the stop.',
          level: 'info',
        });
        try {
          await requestJson(`/api/workflow/${workflowId}/stop`, {
            method: 'POST',
            fallbackError: 'The server could not stop the workflow.',
          });
          clearActiveWorkflowId(workflowId);
          workflowIdRef.current = '';
          dispatch({ type: 'WORKFLOW_ABORTED', clearWorkflowId: true });
          dispatch({ type: 'LOG', message: 'Workflow stopped by the server', level: 'info' });
          return;
        } catch (stopError) {
          dispatch({
            type: 'LOG',
            message: `Stop failed; workflow monitoring continues: ${stopError.message}`,
            level: 'error',
          });
        } finally {
          stopRequestedRef.current = false;
          setIsStopping(false);
        }
      }

      startPhaseRef.current = 'monitoring';
      await pollWorkflow(workflowId, setupElapsedSeconds, signal, runId);
    } catch (err) {
      if (
        !paidStartSent
        || (
          paidStartStatus >= 400
          && !isAmbiguousPaidStartFailure(paidStartStatus)
        )
      ) {
        clearWorkflowPaidStart(paidStartRef, startAttempt);
      }
      if (err.name === 'AbortError') {
        if (!paidStartSent || paidStartResolved) {
          dispatch({ type: 'WORKFLOW_ABORTED' });
        }
        return;
      }
      const ambiguousStart = paidStartSent && !paidStartResolved;
      const message = ambiguousStart
        ? `${
          stopRequestedRef.current ? 'Stop was requested, but ' : ''
        }the workflow start may already have been accepted. Its exact recovery key was retained; retry only this unchanged start to recover the server response.`
        : err.userMessage || err.message;
      options.onError?.(message);
      dispatch({ type: 'WORKFLOW_ERROR', message, outputFolder: err.outputFolder || '' });
    } finally {
      startPhaseRef.current = 'idle';
      stopRequestedRef.current = false;
      setIsStopping(false);
      if (abortRef.current === controller) {
        abortRef.current = null;
      }
      if (inFlightRef.current === runId) {
        inFlightRef.current = null;
      }
    }
  }, [dispatch, enabled, pollWorkflow]);

  const stop = useCallback(async () => {
    if (stopPromiseRef.current) return stopPromiseRef.current;

    const stopPromise = (async () => {
      let waitingForStartResolution = false;
      // Yield once so `stopPromiseRef` is assigned before any synchronous
      // no-workflow early return reaches the finally block.
      await Promise.resolve();
      const workflowId = workflowIdRef.current || readActiveWorkflowId();
      setIsStopping(true);
      try {
        if (!workflowId) {
          if (inFlightRef.current && startPhaseRef.current !== 'idle') {
            waitingForStartResolution = true;
            stopRequestedRef.current = true;
            dispatch({
              type: 'LOG',
              message: 'Stop requested. Waiting for the server to resolve the start before confirming cancellation.',
              level: 'info',
            });
            return false;
          }
          dispatch({
            type: 'LOG',
            message: 'No server workflow is available to stop.',
            level: 'warning',
          });
          return false;
        }

        dispatch({ type: 'LOG', message: 'Stopping workflow on the server…', level: 'info' });
        await requestJson(`/api/workflow/${workflowId}/stop`, {
          method: 'POST',
          fallbackError: 'The server could not stop the workflow.',
        });
        clearActiveWorkflowId(workflowId);
        workflowIdRef.current = '';
        abort();
        dispatch({ type: 'WORKFLOW_ABORTED', clearWorkflowId: true });
        dispatch({ type: 'LOG', message: 'Workflow stopped by the server', level: 'info' });
        return true;
      } catch (error) {
        dispatch({
          type: 'LOG',
          message: `Stop failed; workflow monitoring continues: ${error.message}`,
          level: 'error',
        });
        return false;
      } finally {
        if (!waitingForStartResolution) setIsStopping(false);
        stopPromiseRef.current = null;
      }
    })();

    stopPromiseRef.current = stopPromise;
    return stopPromise;
  }, [abort, dispatch]);

  useEffect(() => {
    if (!enabled || inFlightRef.current) return undefined;
    const workflowId = readActiveWorkflowId();
    if (!workflowId) return undefined;

    const controller = new AbortController();
    const runId = runIdRef.current + 1;
    runIdRef.current = runId;
    inFlightRef.current = runId;
    abortRef.current = controller;
    workflowIdRef.current = workflowId;
    dispatch({ type: 'WORKFLOW_RECONNECT', workflowId });

    pollWorkflow(workflowId, 0, controller.signal, runId)
      .catch((error) => {
        if (error.name === 'AbortError') return;
        dispatch({
          type: 'WORKFLOW_ERROR',
          message: `Unable to reconnect: ${error.message}`,
          outputFolder: error.outputFolder || '',
        });
      })
      .finally(() => {
        if (abortRef.current === controller) abortRef.current = null;
        if (inFlightRef.current === runId) inFlightRef.current = null;
      });

    return () => {
      controller.abort();
      if (abortRef.current === controller) abortRef.current = null;
      if (inFlightRef.current === runId) inFlightRef.current = null;
    };
  }, [dispatch, enabled, pollWorkflow]);

  return { run, runFromAgentInput, abort, stop, isStopping };
}
