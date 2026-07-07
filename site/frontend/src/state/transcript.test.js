/* @vitest-environment jsdom */

import { describe, expect, it } from 'vitest';
import {
  deriveTranscript,
  deriveStepperNodes,
  activeAgentKey,
  toneForStatus,
  agentStatusLine,
} from './transcript.js';

/** A derived-registry stand-in with the 10-node pipeline (incl. guardrails). */
const REGISTRY = {
  registry: {},
  pipelineKeys: [
    'setup',
    'input_guardrail',
    'normalizer',
    'extracter',
    'content_guardrail',
    'verifier',
    'generator',
    'latexifier',
    'evaluation',
    'output',
  ],
  agentLabels: {
    setup: 'Setup',
    input_guardrail: 'Input Guardrail',
    normalizer: 'Normalizer',
    extracter: 'Extracter',
    content_guardrail: 'Content Guardrail',
    verifier: 'Verifier',
    generator: 'Generator',
    latexifier: 'Latexifier',
    evaluation: 'Evaluation',
    output: 'Output',
  },
};

function agent(overrides = {}) {
  return {
    name: overrides.name || 'Agent',
    description: '',
    status: 'inactive',
    kind: 'agent',
    startTime: null,
    endTime: null,
    executionTime: null,
    ...overrides,
  };
}

function baseState(overrides = {}) {
  return {
    agents: {},
    logs: [],
    result: null,
    error: null,
    errorFolder: '',
    blocked: null,
    runInputs: null,
    ...overrides,
  };
}

describe('toneForStatus / agentStatusLine', () => {
  it('maps status to bubble tone', () => {
    expect(toneForStatus('completed')).toBe('success');
    expect(toneForStatus('errored')).toBe('error');
    expect(toneForStatus('blocked')).toBe('blocked');
    expect(toneForStatus('thinking')).toBe('thinking');
    expect(toneForStatus('queued')).toBeUndefined();
  });

  it('summarizes agent status with timing', () => {
    expect(agentStatusLine(agent({ status: 'thinking' }))).toBe('Working…');
    expect(agentStatusLine(agent({ status: 'completed', executionTime: 3.2 }))).toBe('Completed in 3.20s');
    expect(agentStatusLine(agent({ status: 'blocked' }))).toBe('Blocked');
  });
});

describe('deriveTranscript', () => {
  it('opens with the user prompt message including attached file names', () => {
    const state = baseState({ runInputs: { prompt: 'make it harder', files: ['a.pdf'] } });
    const messages = deriveTranscript(state, REGISTRY);
    expect(messages[0]).toMatchObject({ id: 'user-prompt', variant: 'user', text: 'make it harder', files: ['a.pdf'] });
  });

  it('adds a workflow-started note (after the user prompt) carrying the latest run id', () => {
    const state = baseState({
      runInputs: { prompt: 'go', files: [] },
      isRunning: true,
      workflowId: '3f2504e0-4f89-41d3-9a0c-0305e82c3301',
    });
    const messages = deriveTranscript(state, REGISTRY);
    const started = messages.find((m) => m.id === 'workflow-started');
    expect(messages.indexOf(started)).toBe(1); // right after 'user-prompt'
    expect(started).toMatchObject({ kind: 'workflow-started', variant: 'assistant' });
    expect(started.text).toBe('Workflow ID: 3f2504e0 - Latest Run');
  });

  it('shows a starting placeholder before the workflow id is registered', () => {
    const state = baseState({ isRunning: true, workflowId: null });
    const started = deriveTranscript(state, REGISTRY).find((m) => m.id === 'workflow-started');
    expect(started.text).toBe('Workflow starting…');
  });

  it('omits the workflow-started note when no run has started', () => {
    const state = baseState();
    expect(deriveTranscript(state, REGISTRY).find((m) => m.id === 'workflow-started')).toBeUndefined();
  });

  it('emits one bubble per started node, in registry order, skipping inactive/queued', () => {
    const state = baseState({
      agents: {
        setup: agent({ name: 'Setup', kind: 'runtime', status: 'completed', executionTime: 1 }),
        input_guardrail: agent({ name: 'Input Guardrail', kind: 'guardrail', status: 'completed' }),
        normalizer: agent({ name: 'Normalizer', status: 'thinking' }),
        extracter: agent({ name: 'Extracter', status: 'queued' }),
      },
    });
    const messages = deriveTranscript(state, REGISTRY);
    const ids = messages.map((m) => m.id);
    expect(ids).toEqual(['agent-setup', 'agent-input_guardrail', 'agent-normalizer']);
    expect(messages[0]).toMatchObject({ kind: 'runtime', author: 'Setup', tone: 'success' });
    expect(messages[1]).toMatchObject({ kind: 'guardrail', author: 'Input Guardrail' });
    expect(messages[2]).toMatchObject({ status: 'thinking', tone: 'thinking' });
  });

  it('appends a guardrail block bubble when state.blocked is set', () => {
    const state = baseState({ blocked: { category: 'policy', reason: 'Off topic', stage: 'content_guardrail' } });
    const messages = deriveTranscript(state, REGISTRY);
    const blocked = messages.find((m) => m.id === 'blocked');
    expect(blocked).toMatchObject({ kind: 'blocked', tone: 'blocked', text: 'Off topic' });
    expect(blocked.blocked.category).toBe('policy');
  });

  it('appends a result bubble and an error bubble from their state fields', () => {
    const resultState = baseState({ result: { workflowId: 'wf', files: [] } });
    expect(deriveTranscript(resultState, REGISTRY).at(-1)).toMatchObject({ id: 'result', kind: 'result', tone: 'success' });

    const errorState = baseState({ error: 'boom', errorFolder: 'outputs/x' });
    expect(deriveTranscript(errorState, REGISTRY).at(-1)).toMatchObject({ id: 'error', kind: 'error', tone: 'error', errorFolder: 'outputs/x' });
  });
});

describe('deriveStepperNodes / activeAgentKey', () => {
  it('returns every registry node in order with live status/kind', () => {
    const state = baseState({
      agents: {
        setup: agent({ name: 'Setup', kind: 'runtime', status: 'completed' }),
        content_guardrail: agent({ name: 'Content Guardrail', kind: 'guardrail', status: 'blocked' }),
      },
    });
    const nodes = deriveStepperNodes(state, REGISTRY);
    expect(nodes).toHaveLength(REGISTRY.pipelineKeys.length);
    expect(nodes[0]).toMatchObject({ key: 'setup', status: 'completed', kind: 'runtime' });
    const guardrail = nodes.find((n) => n.key === 'content_guardrail');
    expect(guardrail).toMatchObject({ status: 'blocked', kind: 'guardrail' });
    // A node with no agent state falls back to inactive.
    expect(nodes.find((n) => n.key === 'generator')).toMatchObject({ status: 'inactive' });
  });

  it('activeAgentKey returns the first thinking node', () => {
    const state = baseState({
      agents: {
        normalizer: agent({ status: 'completed' }),
        extracter: agent({ status: 'thinking' }),
        verifier: agent({ status: 'thinking' }),
      },
    });
    expect(activeAgentKey(state, REGISTRY)).toBe('extracter');
    expect(activeAgentKey(baseState(), REGISTRY)).toBeNull();
  });
});
