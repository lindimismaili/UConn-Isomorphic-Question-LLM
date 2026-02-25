import { useRef, useCallback } from 'react';

function sleep(ms, signal) {
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(resolve, ms);
    if (signal) {
      const onAbort = () => {
        clearTimeout(timeout);
        reject(new DOMException('Aborted', 'AbortError'));
      };
      signal.addEventListener('abort', onAbort, { once: true });
    }
  });
}

async function fetchWithAbort(url, options, signal) {
  try {
    return await fetch(url, { ...options, signal });
  } catch (err) {
    if (err.name === 'AbortError') throw err;
    throw new Error(`Network error: ${err.message}`);
  }
}

const AGENT_LABELS = {
  normalizer: 'Normalizer',
  extracter: 'Extracter',
  verifier: 'Verifier',
  generator: 'Generator',
  latex: 'LaTeX',
};

const AGENT_COUNT_LOGS = {
  extracter: 'Extracted',
  verifier: 'Verified',
  generator: 'Generated',
};

function formatSeconds(seconds) {
  if (typeof seconds !== 'number' || Number.isNaN(seconds)) return '';
  return `${seconds.toFixed(2)}s`;
}

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
 * Handles the full workflow lifecycle: upload → start → poll → complete/fail
 * All state updates go through dispatch.
 */
export function useWorkflow(dispatch) {
  const abortRef = useRef(null);
  const statusRef = useRef({});

  const abort = useCallback(() => {
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
  }, []);

  const run = useCallback(async (prompt, files) => {
    // Create abort controller for this run
    const controller = new AbortController();
    abortRef.current = controller;
    const { signal } = controller;
    const setupStartMs = Date.now();
    statusRef.current = {};

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
          const err = await uploadRes.text().catch(() => `Status ${uploadRes.status}`);
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
        const err = await startRes.text().catch(() => `Status ${startRes.status}`);
        throw new Error(`Failed to start workflow: ${err}`);
      }
      const { workflowId } = await startRes.json();
      const setupElapsedSeconds = (Date.now() - setupStartMs) / 1000;

      dispatch({ type: 'SETUP_COMPLETE', workflowId });
      dispatch({ type: 'LOG', message: `Workflow started (${workflowId})`, level: 'info' });

      // Step 3: Poll for status
      const agentKeys = ['normalizer', 'extracter', 'verifier', 'generator', 'latex'];

      while (true) {
        if (signal.aborted) return;

        const statusRes = await fetchWithAbort(`/api/workflow/${workflowId}/status`, {}, signal);
        if (!statusRes.ok) throw new Error('Failed to get workflow status');
        const status = await statusRes.json();

        // Update agent statuses
        for (const key of agentKeys) {
          const agentStatus = status.agents?.[key];
          if (agentStatus) {
            const data = agentStatus.data || {};
            const prevStatus = statusRef.current[key];
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
              }
              statusRef.current[key] = nextStatus;
            }

            dispatch({
              type: 'AGENT_UPDATE',
              agentKey: key,
              status: agentStatus.status,
              data: {
                startTime: data.startTime ? new Date(data.startTime) : null,
                endTime: data.endTime ? new Date(data.endTime) : null,
                executionTime: data.executionTime ?? null,
                inputText: data.input ?? '',
                outputText: data.output ?? '',
                inputFrom: data.receivedFrom || undefined,
                outputTo: data.sentTo || undefined,
              },
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
          const err = new Error('Workflow failed on the server');
          err.outputFolder = status.outputFolder || '';
          throw err;
        }

        await sleep(1000, signal);
      }
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
    }
  }, [dispatch]);

  return { run, abort };
}
