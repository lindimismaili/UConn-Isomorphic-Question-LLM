/* @vitest-environment jsdom */

import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import AgentPipeline from './AgentPipeline.jsx';
import { loadAgentRegistry } from '../hooks/useAgentRegistry';

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const PIPELINE_KEYS = ['setup', 'normalizer', 'extracter', 'verifier', 'generator', 'latexifier', 'evaluation', 'output'];

const REGISTRY_PAYLOAD = Object.fromEntries(
  PIPELINE_KEYS.map((key, index) => [
    key,
    {
      sequence: index,
      name: key,
      kind: key === 'setup' || key === 'output' ? 'runtime' : 'agent',
    },
  ]),
);

let root;
let container;
let originalGetBoundingClientRect;

function buildAgents(statuses) {
  return Object.fromEntries(
    PIPELINE_KEYS.map((key) => [
      key,
      {
        name: key,
        description: `${key} description`,
        status: statuses[key] || 'inactive',
        model: 'test-model',
        reasoningLevel: 'none',
        kind: key === 'setup' || key === 'output' ? 'runtime' : 'agent',
      },
    ]),
  );
}

function rect(left, top, width, height) {
  return {
    left,
    top,
    width,
    height,
    right: left + width,
    bottom: top + height,
    x: left,
    y: top,
    toJSON: () => {},
  };
}

function render(element) {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  act(() => {
    root.render(element);
  });
}

async function flushFrame() {
  await act(async () => {
    await new Promise((resolve) => window.requestAnimationFrame(resolve));
  });
}

beforeEach(async () => {
  vi.stubGlobal('fetch', vi.fn(async () => ({
    ok: true,
    json: async () => REGISTRY_PAYLOAD,
  })));
  await loadAgentRegistry();

  originalGetBoundingClientRect = Element.prototype.getBoundingClientRect;
  Element.prototype.getBoundingClientRect = function getBoundingClientRect() {
    if (this.classList?.contains('agents-track')) {
      return rect(0, 0, 2400, 260);
    }

    const card = this.matches?.('[data-agent]')
      ? this
      : this.querySelector?.('[data-agent]');
    const agentKey = card?.getAttribute('data-agent');
    const index = PIPELINE_KEYS.indexOf(agentKey);

    if (index >= 0) {
      return rect(16 + index * 292, 18, 220, 224);
    }

    return rect(0, 0, 0, 0);
  };
});

afterEach(() => {
  if (root) {
    act(() => root.unmount());
  }
  container?.remove();
  root = null;
  container = null;
  Element.prototype.getBoundingClientRect = originalGetBoundingClientRect;
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('AgentPipeline', () => {
  it('reveals agents progressively and only draws completed connectors to revealed nodes', async () => {
    render(
      <AgentPipeline
        agents={buildAgents({ setup: 'thinking', normalizer: 'queued' })}
        onCardClick={() => {}}
      />,
    );
    await flushFrame();

    expect(document.querySelectorAll('.agent-card')).toHaveLength(1);
    expect(document.querySelector('[data-agent="setup"]')).not.toBeNull();
    expect(document.querySelectorAll('.agent-connector-path')).toHaveLength(0);

    act(() => {
      root.render(
        <AgentPipeline
          agents={buildAgents({ setup: 'completed', normalizer: 'thinking' })}
          onCardClick={() => {}}
        />,
      );
    });
    await flushFrame();

    expect(document.querySelectorAll('.agent-card')).toHaveLength(2);
    const connector = document.querySelector('.agent-connector-path');
    expect(connector).not.toBeNull();
    expect(connector.getAttribute('d')).toBe('M 236 130 L 308 130');
  });
});
