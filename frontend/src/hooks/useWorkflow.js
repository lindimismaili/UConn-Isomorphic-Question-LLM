import { useRef, useCallback } from 'react';

import { abortableSleep } from '../utils/abortableSleep';

/**
 * Fetch wrapper that preserves AbortError and wraps other network failures.
 *
 * @param {string} url - Endpoint path to request.
 * @param {RequestInit} [options] - Fetch options.
 * @param {AbortSignal} signal - Abort signal for the in-flight workflow.
 * @returns {Promise<Response>}
 */
async function fetchWithAbort(url, options, signal) {
  try {
    return await fetch(url, { ...options, signal });
  } catch (err) {
    if (err.name === 'AbortError') throw err;
    throw new Error(`Network error: ${err.message}`);
  }
}

/**
 * Reads a useful error detail from a failed API response.
 *
 * @param {Response} response - Failed fetch response.
 * @param {string} fallback - Message to use when the response has no body.
 * @returns {Promise<string>}
 */
async function readErrorDetail(response, fallback) {
  const text = await response.text().catch(() => '');
  if (!text) return fallback;
  try {
    const data = JSON.parse(text);
    if (typeof data?.detail === 'string') return data.detail;
    if (Array.isArray(data?.detail)) return data.detail.map((item) => item?.msg || String(item)).join('; ');
  } catch {
    // Fall through to raw text.
  }
  return text;
}

/** Human-readable labels for workflow log messages. */
const AGENT_LABELS = {
  normalizer: 'Normalizer',
  extracter: 'Extracter',
  verifier: 'Verifier',
  generator: 'Generator',
  latexifier: 'Latexifier',
  evaluation: 'Evaluator',
  output: 'Output',
};

/** Stages that emit question-count logs when they complete. */
const AGENT_COUNT_LOGS = {
  extracter: 'Extracted',
  verifier: 'Verified',
  generator: 'Generated',
};
/** Polling interval used while waiting for workflow status updates. */
const STATUS_POLL_INTERVAL_MS = 150;

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
 * Extracts question counts from agent outputs for selected stages.
 *
 * @param {string} agentKey - Workflow stage key.
 * @param {object} output - Agent output payload.
 * @returns {number | null}
 */
function getQuestionCount(agentKey, output) {
  if (!output || typeof output !== 'object') return null;
  if (agentKey === 'extracter' || agentKey === 'verifier') {
    return Array.isArray(output.questions) ? output.questions.length : null;
  }
  if (agentKey === 'generator') {
    return Array.isArray(output.isomorphic_questions) ? output.isomorphic_questions.length : null;
  }
  return null;
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
export function useWorkflow(dispatch) {
  const abortRef = useRef(null);
  const inFlightRef = useRef(null);
  const runIdRef = useRef(0);

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
    const agentKeys = ['normalizer', 'extracter', 'verifier', 'generator', 'latexifier', 'evaluation', 'output'];
    const statusByAgent = {};

    while (true) {
      if (signal.aborted || inFlightRef.current !== runId) return;

      const statusRes = await fetchWithAbort(`/api/workflow/${workflowId}/status`, {}, signal);
      if (!statusRes.ok) {
        throw new Error(await readErrorDetail(statusRes, 'Failed to get workflow status'));
      }
      const status = await statusRes.json();

      for (const key of agentKeys) {
        const agentStatus = status.agents?.[key];
        if (agentStatus) {
          const data = agentStatus.data || {};
          const prevStatus = statusByAgent[key];
          const nextStatus = agentStatus.status;

          if (prevStatus !== nextStatus) {
            const label = AGENT_LABELS[key] || key;
            if (nextStatus === 'thinking') {
              dispatch({ type: 'LOG', message: `${label} started`, level: 'info' });
            } else if (nextStatus === 'completed') {
              const elapsed = formatSeconds(data.executionTime);
              const timeSuffix = elapsed ? ` in ${elapsed}` : '';
              dispatch({ type: 'LOG', message: `${label} completed${timeSuffix}`, level: 'success' });

              const countLabel = AGENT_COUNT_LOGS[key];
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

          dispatch({
            type: 'AGENT_UPDATE',
            agentKey: key,
            status: agentStatus.status,
            data: buildAgentPatch(agentStatus),
          });
        }
      }

      if (status.status === 'completed') {
        const agentsElapsedSeconds = agentKeys.reduce((total, key) => {
          const value = status.agents?.[key]?.data?.executionTime;
          return total + (typeof value === 'number' ? value : 0);
        }, 0);
        const totalElapsedSeconds = setupElapsedSeconds + agentsElapsedSeconds;

        const resultRes = await fetchWithAbort(`/api/workflow/${workflowId}/result`, {}, signal);
        if (resultRes.ok) {
          const result = await resultRes.json();
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
        } else {
          const errText = await resultRes.text().catch(() => `Status ${resultRes.status}`);
          dispatch({ type: 'WORKFLOW_ERROR', message: `Failed to fetch result: ${errText}`, outputFolder: status.outputFolder || '' });
        }
        return;
      }

      if (status.status === 'errored') {
        const erroredAgent = agentKeys
          .map((key) => status.agents?.[key])
          .find((agentStatus) => agentStatus?.status === 'errored');
        const message =
          status.error ||
          erroredAgent?.data?.error ||
          'Workflow failed on the server';
        const err = new Error(message);
        err.outputFolder = status.outputFolder || '';
        throw err;
      }

      if (status.status === 'cancelled') {
        dispatch({ type: 'WORKFLOW_ABORTED' });
        dispatch({ type: 'LOG', message: 'Workflow stopped', level: 'info' });
        return;
      }

      await abortableSleep(STATUS_POLL_INTERVAL_MS, signal);
    }
  }, [dispatch]);

  /**
   * Starts a workflow run, uploads files when needed, then polls status until
   * completion, cancellation, or failure.
   *
   * @param {string} prompt - User prompt forwarded to the backend.
   * @param {Array<object>} files - Selected files from the upload/test-file UI.
   * @param {{runEvaluations?: boolean}} options - Workflow execution options.
   */
  const run = useCallback(async (prompt, files, options = {}) => {
    if (inFlightRef.current) return;
    const controller = new AbortController();
    const runId = runIdRef.current + 1;
    runIdRef.current = runId;
    inFlightRef.current = runId;
    abortRef.current = controller;
    const { signal } = controller;
    const setupStartMs = Date.now();

    dispatch({ type: 'WORKFLOW_START' });

    try {
      let sessionId = null;
      const uploadableFiles = files.filter((f) => {
        if (typeof File === 'undefined') return !!f.file;
        return f.file instanceof File;
      });
      const selectedTestFiles = files
        .filter((f) => f?.source === 'test' && typeof f.testFile === 'string' && f.testFile)
        .map((f) => f.testFile);

      // Step 1: Upload files
      if (uploadableFiles.length > 0) {
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
        dispatch({ type: 'LOG', message: 'Files uploaded successfully', level: 'success' });
      }

      // Step 2: Start workflow
      dispatch({ type: 'LOG', message: 'Starting workflow...', level: 'info' });
      const startForm = new FormData();
      startForm.append('prompt', prompt);
      startForm.append('sessionId', sessionId || '');
      startForm.append('runEvaluation', options.runEvaluations ? 'true' : 'false');
      if (selectedTestFiles.length > 0) {
        startForm.append('testFiles', JSON.stringify(selectedTestFiles));
        dispatch({
          type: 'LOG',
          message: `Using ${selectedTestFiles.length} test file(s) from data/evaluation`,
          level: 'info',
        });
      }

      const startRes = await fetchWithAbort('/api/workflow/start', { method: 'POST', body: startForm }, signal);
      if (!startRes.ok) {
        const err = await readErrorDetail(startRes, `Status ${startRes.status}`);
        throw new Error(`Failed to start workflow: ${err}`);
      }
      const { workflowId } = await startRes.json();
      const setupElapsedSeconds = (Date.now() - setupStartMs) / 1000;

      dispatch({ type: 'SETUP_COMPLETE', workflowId });
      dispatch({ type: 'LOG', message: `Workflow started (${workflowId})`, level: 'info' });
      await pollWorkflow(workflowId, setupElapsedSeconds, signal, runId);
    } catch (err) {
      if (err.name === 'AbortError') {
        dispatch({ type: 'WORKFLOW_ABORTED' });
        return;
      }
      dispatch({ type: 'WORKFLOW_ERROR', message: err.message, outputFolder: err.outputFolder || '' });
    } finally {
      if (abortRef.current === controller) {
        abortRef.current = null;
      }
      if (inFlightRef.current === runId) {
        inFlightRef.current = null;
      }
    }
  }, [dispatch, pollWorkflow]);

  /**
   * Starts a workflow by uploading a prepared agent input artifact.
   *
   * @param {string} startAgent - Agent key to receive the uploaded input.
   * @param {File} inputFile - JSON, JSONL, or text file containing agent input.
   * @param {string} prompt - Optional extra prompt context.
   * @param {{onStarted?: Function, onError?: Function}} [options] - Optional lifecycle callbacks.
   */
  const runFromAgentInput = useCallback(async (startAgent, inputFile, prompt = '', options = {}) => {
    if (inFlightRef.current) return;
    const controller = new AbortController();
    const runId = runIdRef.current + 1;
    runIdRef.current = runId;
    inFlightRef.current = runId;
    abortRef.current = controller;
    const { signal } = controller;
    const setupStartMs = Date.now();

    dispatch({ type: 'WORKFLOW_START' });

    try {
      dispatch({ type: 'LOG', message: `Uploading input for ${AGENT_LABELS[startAgent] || startAgent}...`, level: 'info' });
      const startForm = new FormData();
      startForm.append('startAgent', startAgent);
      startForm.append('prompt', prompt);
      startForm.append('agentInput', inputFile);

      const startRes = await fetchWithAbort(
        '/api/workflow/start-from-agent-input',
        { method: 'POST', body: startForm },
        signal,
      );
      if (!startRes.ok) {
        const detail = await readErrorDetail(startRes, `Status ${startRes.status}`);
        const err = new Error(`Failed to start agent-input workflow: ${detail}`);
        err.userMessage = detail;
        throw err;
      }

      const { workflowId } = await startRes.json();
      const setupElapsedSeconds = (Date.now() - setupStartMs) / 1000;

      dispatch({ type: 'SETUP_COMPLETE', workflowId });
      dispatch({
        type: 'LOG',
        message: `Workflow started from ${AGENT_LABELS[startAgent] || startAgent} (${workflowId})`,
        level: 'info',
      });
      options.onStarted?.(workflowId);
      await pollWorkflow(workflowId, setupElapsedSeconds, signal, runId);
    } catch (err) {
      if (err.name === 'AbortError') {
        dispatch({ type: 'WORKFLOW_ABORTED' });
        return;
      }
      options.onError?.(err.userMessage || err.message);
      dispatch({ type: 'WORKFLOW_ERROR', message: err.message, outputFolder: err.outputFolder || '' });
    } finally {
      if (abortRef.current === controller) {
        abortRef.current = null;
      }
      if (inFlightRef.current === runId) {
        inFlightRef.current = null;
      }
    }
  }, [dispatch, pollWorkflow]);

  return { run, runFromAgentInput, abort };
}
