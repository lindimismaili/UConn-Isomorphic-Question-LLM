import { useRef, useCallback } from 'react';

import { abortableSleep } from '../utils/abortableSleep';
import { fetchWithAbort, readErrorDetail } from '../utils/api';
import { getDerivedRegistry, loadAgentRegistry } from './useAgentRegistry';
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
    // The first pipeline node (runtime setup) is simulated locally, so polling
    // tracks every stage after it. If the registry fetch failed earlier, fall
    // back to the stage keys reported by the status payload itself.
    await loadAgentRegistry();
    let agentKeys = getDerivedRegistry()?.pipelineKeys.slice(1) ?? null;
    const statusByAgent = {};

    while (true) {
      if (signal.aborted || inFlightRef.current !== runId) return;

      const statusRes = await fetchWithAbort(`/api/workflow/${workflowId}/status`, {}, signal);
      if (!statusRes.ok) {
        throw new Error(await readErrorDetail(statusRes, 'Failed to get workflow status'));
      }
      const status = await statusRes.json();
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
        dispatch({
          type: 'LOG',
          message: label ? `${label} blocked the run` : 'Workflow blocked by a guardrail',
          level: 'warning',
        });
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
   * @param {{runEvaluations?: boolean, theme?: string}} options - Workflow execution options.
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

    dispatch({
      type: 'WORKFLOW_START',
      prompt,
      fileNames: files.map((f) => f?.name).filter(Boolean),
    });

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
      startForm.append('theme', options.theme || '');
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

      dispatch({ type: 'WORKFLOW_REGISTERED', workflowId });
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

      dispatch({ type: 'WORKFLOW_REGISTERED', workflowId });
      dispatch({
        type: 'LOG',
        message: `Workflow started from ${(getDerivedRegistry()?.agentLabels ?? {})[startAgent] || startAgent} (${workflowId})`,
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
