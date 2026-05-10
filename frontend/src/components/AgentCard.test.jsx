/* @vitest-environment jsdom */

import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';
import AgentCard from './AgentCard.jsx';

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

let root;
let container;

function render(element) {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  act(() => {
    root.render(element);
  });
}

afterEach(() => {
  if (root) {
    act(() => root.unmount());
  }
  container?.remove();
  root = null;
  container = null;
});

describe('AgentCard', () => {
  it('renders as a native button and activates with clicks', () => {
    const onClick = vi.fn();
    render(
      <AgentCard
        agentKey="generator"
        onClick={onClick}
        agent={{
          name: 'Generator',
          description: 'Creates questions',
          status: 'completed',
          model: 'gpt-test',
          reasoningLevel: 'high',
        }}
      />,
    );

    const button = document.querySelector('.agent-card');
    expect(button.tagName).toBe('BUTTON');
    expect(button.type).toBe('button');
    expect(button.getAttribute('aria-label')).toContain('Generator agent - Completed');

    act(() => {
      button.click();
    });
    expect(onClick).toHaveBeenCalledWith('generator');
  });

  it('renders one stable timing panel with a running end placeholder', () => {
    render(
      <AgentCard
        agentKey="generator"
        onClick={() => {}}
        agent={{
          name: 'Generator',
          description: 'Creates questions',
          status: 'thinking',
          model: 'gpt-test',
          reasoningLevel: 'high',
          startTime: '2026-05-10T14:05:00Z',
          endTime: null,
        }}
      />,
    );

    const timingPanels = document.querySelectorAll('.agent-timing span');
    expect(timingPanels).toHaveLength(1);
    expect(timingPanels[0].textContent).toMatch(/^\(\d{2}:\d{2} - \.\.\.\)$/);
  });
});
