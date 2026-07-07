/**
 * Chat transcript derivation — turns the presentation-agnostic reducer state
 * (`state.agents`, `state.result`, `state.blocked`, `state.error`,
 * `state.runInputs`) into an ordered list of message descriptors the chat view
 * renders. Pure and registry-driven: node order/labels/kinds come from the
 * derived registry, never hardcoded. The reducer is untouched — this is a view
 * projection only.
 *
 * Message descriptor shape:
 *   { id, variant: 'user'|'assistant'|'agent', kind, author, timestamp,
 *     status, tone, agentKey, text, description }
 */

/** Statuses that mean a node has begun (so it earns a transcript message). */
const STARTED = new Set(['thinking', 'completed', 'errored', 'cancelled', 'blocked']);

/** Maps an agent status to a ChatMessage tone (bubble tint), or undefined. */
export function toneForStatus(status) {
  if (status === 'completed') return 'success';
  if (status === 'errored') return 'error';
  if (status === 'blocked') return 'blocked';
  if (status === 'thinking') return 'thinking';
  return undefined;
}

/** Formats a clock timestamp (HH:MM) from a Date-ish value, or ''. */
export function formatClock(value) {
  if (!value) return '';
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', hour12: false });
}

/** One-line human status summary for an agent bubble. */
export function agentStatusLine(agent) {
  const seconds = typeof agent.executionTime === 'number' ? `${agent.executionTime.toFixed(2)}s` : '';
  switch (agent.status) {
    case 'thinking':
      return 'Working…';
    case 'completed':
      return seconds ? `Completed in ${seconds}` : 'Completed';
    case 'errored':
      return 'Errored';
    case 'cancelled':
      return 'Cancelled';
    case 'blocked':
      return 'Blocked';
    default:
      return '';
  }
}

/**
 * Derive the chat message list from workflow state.
 *
 * @param {object} state - The reducer state.
 * @param {object|null} derivedRegistry - `getDerivedRegistry()` output (or null).
 * @returns {Array<object>} Ordered message descriptors.
 */
export function deriveTranscript(state, derivedRegistry) {
  const messages = [];
  const pipelineKeys = derivedRegistry?.pipelineKeys ?? [];
  const agentLabels = derivedRegistry?.agentLabels ?? {};

  // 1. The user's instruction (or a placeholder when a run had no prompt).
  const runInputs = state.runInputs;
  if (runInputs) {
    const promptText = (runInputs.prompt || '').trim();
    const fileNames = runInputs.files || [];
    if (promptText || fileNames.length) {
      messages.push({
        id: 'user-prompt',
        variant: 'user',
        author: 'You',
        text: promptText || '(no prompt — using defaults)',
        files: fileNames,
      });
    }
  }

  // 1b. A "workflow began" note carrying the latest run's id, so the transcript
  // is clearly tied to the run shown in the pipeline. Shows "starting…" during
  // the setup window (before the backend registers the id) and persists after
  // completion (workflowId is not cleared until the next run starts).
  if (state.isRunning || state.workflowId) {
    messages.push({
      id: 'workflow-started',
      variant: 'assistant',
      kind: 'workflow-started',
      author: 'Workflow',
      workflowId: state.workflowId || null,
      text: state.workflowId
        ? `Workflow ID: ${state.workflowId.slice(0, 8)} - Latest Run`
        : 'Workflow starting…',
    });
  }

  // 2. One bubble per started pipeline node, in registry order.
  const order = pipelineKeys.length
    ? pipelineKeys
    : Object.keys(state.agents);
  for (const key of order) {
    const agent = state.agents[key];
    if (!agent || !STARTED.has(agent.status)) continue;
    messages.push({
      id: `agent-${key}`,
      variant: 'agent',
      agentKey: key,
      kind: agent.kind || 'agent',
      author: agent.name || agentLabels[key] || key,
      timestamp: formatClock(agent.endTime || agent.startTime),
      status: agent.status,
      tone: toneForStatus(agent.status),
      description: agent.description || '',
      text: agentStatusLine(agent),
    });
  }

  // 3. A guardrail block bubble (terminal `blocked` status). Uses kind
  // `'blocked'` — distinct from the per-node `'guardrail'` activity bubbles
  // above, which render as ordinary pass/working/blocked node updates.
  if (state.blocked) {
    messages.push({
      id: 'blocked',
      variant: 'assistant',
      kind: 'blocked',
      author: 'Guardrail',
      status: 'blocked',
      tone: 'blocked',
      blocked: state.blocked,
      text: state.blocked.reason || 'The request was blocked by a guardrail.',
    });
  }

  // 4. The final result (rich assistant bubble with actions).
  if (state.result) {
    messages.push({
      id: 'result',
      variant: 'assistant',
      kind: 'result',
      author: 'Assistant',
      status: 'completed',
      tone: 'success',
      result: state.result,
    });
  }

  // 5. A hard failure bubble.
  if (state.error) {
    messages.push({
      id: 'error',
      variant: 'assistant',
      kind: 'error',
      author: 'Assistant',
      status: 'errored',
      tone: 'error',
      error: state.error,
      errorFolder: state.errorFolder || '',
    });
  }

  return messages;
}

/**
 * Build the ordered stepper node list from state + registry (all nodes, in
 * order, with live status/kind). Registry-driven; never hardcodes keys.
 *
 * @param {object} state
 * @param {object|null} derivedRegistry
 * @returns {Array<{key: string, label: string, status: string, kind: string}>}
 */
export function deriveStepperNodes(state, derivedRegistry) {
  const pipelineKeys = derivedRegistry?.pipelineKeys ?? Object.keys(state.agents);
  const agentLabels = derivedRegistry?.agentLabels ?? {};
  return pipelineKeys.map((key) => {
    const agent = state.agents[key] || {};
    return {
      key,
      label: agent.name || agentLabels[key] || key,
      status: agent.status || 'inactive',
      kind: agent.kind || derivedRegistry?.registry?.[key]?.kind || 'agent',
    };
  });
}

/**
 * The key of the node currently working (first `thinking` node), or null.
 *
 * @param {object} state
 * @param {object|null} derivedRegistry
 * @returns {string|null}
 */
export function activeAgentKey(state, derivedRegistry) {
  const pipelineKeys = derivedRegistry?.pipelineKeys ?? Object.keys(state.agents);
  return pipelineKeys.find((key) => state.agents[key]?.status === 'thinking') || null;
}
