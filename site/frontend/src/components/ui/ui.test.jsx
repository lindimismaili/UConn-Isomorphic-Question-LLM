/* @vitest-environment jsdom */

import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';
import {
  Badge,
  Button,
  Card,
  CardBody,
  CardHeader,
  DataTable,
  EmptyState,
  IconButton,
  LogItem,
  Pill,
  ScoreChip,
  Select,
  StatusChip,
  StatusDot,
  Tabs,
  TextArea,
  TextInput,
  ToggleSwitch,
} from './index.js';

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
  document.body.innerHTML = '';
  root = null;
  container = null;
});

describe('Button', () => {
  it('emits .btn + variant + modifiers and forwards rest props', () => {
    render(
      <Button variant="primary" size="sm" fixedWidth className="send-action" title="go">
        Send
      </Button>,
    );
    const btn = container.querySelector('button');
    expect(btn.className).toBe('btn btn-primary btn-sm btn-fixed-width send-action');
    expect(btn.getAttribute('type')).toBe('button');
    expect(btn.getAttribute('title')).toBe('go');
    expect(btn.textContent).toBe('Send');
  });

  it('maps feedbackState to the feedback classes', () => {
    render(<Button feedbackState="error">x</Button>);
    expect(container.querySelector('button').className).toContain('btn-error-feedback');
  });

  it('IconButton renders the standalone .btn-icon-only square', () => {
    render(<IconButton aria-label="close">×</IconButton>);
    const btn = container.querySelector('button');
    expect(btn.className).toBe('btn-icon-only');
    expect(btn.getAttribute('aria-label')).toBe('close');
  });
});

describe('form controls', () => {
  it('TextInput renders a native input', () => {
    render(<TextInput value="" onChange={() => {}} />);
    expect(container.querySelector('input').getAttribute('type')).toBe('text');
  });

  it('TextArea uses .prompt-input', () => {
    render(<TextArea value="" onChange={() => {}} />);
    expect(container.querySelector('textarea').className).toBe('prompt-input');
  });

  it('Select renders options + placeholder', () => {
    render(<Select value="" onChange={() => {}} placeholder="Pick" options={[{ value: 'a', label: 'A' }]} />);
    const sel = container.querySelector('select');
    expect(sel.className).toBe('prompt-dropdown-select');
    expect(sel.querySelectorAll('option')).toHaveLength(2);
  });
});

describe('ToggleSwitch', () => {
  it('wraps a checkbox in .developer-toggle with track + label', () => {
    render(<ToggleSwitch checked onChange={() => {}} label="On" />);
    expect(container.querySelector('label.developer-toggle')).toBeTruthy();
    expect(container.querySelector('.developer-toggle-track .developer-toggle-thumb')).toBeTruthy();
    expect(container.querySelector('input[type="checkbox"]').checked).toBe(true);
    expect(container.querySelector('.developer-toggle-label').textContent).toBe('On');
  });
});

describe('Card', () => {
  it('maps variant to the documented status class and renders header/body', () => {
    render(
      <Card variant="completed">
        <CardHeader title="T" subtitle="S" />
        <CardBody>B</CardBody>
      </Card>,
    );
    expect(container.querySelector('section').className).toBe('card completed-card');
    expect(container.querySelector('.card-header-row h2').textContent).toBe('T');
    expect(container.querySelector('.subtitle').textContent).toBe('S');
    expect(container.querySelector('.card-body').textContent).toBe('B');
  });
});

describe('status + feedback', () => {
  it('Badge maps kind + tone', () => {
    render(<Badge kind="reference-kind" tone="input">in</Badge>);
    expect(container.querySelector('span').className).toBe('eval-reference-kind is-input');
  });

  it('StatusChip and ScoreChip emit the eval classes', () => {
    render(
      <div>
        <StatusChip status="passed" />
        <ScoreChip tone="fail">0</ScoreChip>
      </div>,
    );
    expect(container.querySelector('.eval-status-chip').className).toBe('eval-status-chip eval-status-passed');
    expect(container.querySelector('.eval-score-chip').className).toBe('eval-score-chip is-fail');
  });

  it('Pill maps state', () => {
    render(<Pill state="running">Running</Pill>);
    expect(container.querySelector('span').className).toBe('create-data-pill is-running');
  });

  it('StatusDot emits class + data-status', () => {
    render(<StatusDot status="thinking" />);
    const dot = container.querySelector('.status-indicator');
    expect(dot.className).toBe('status-indicator thinking');
    expect(dot.getAttribute('data-status')).toBe('thinking');
  });

  it('LogItem and EmptyState use the documented classes', () => {
    render(<LogItem level="error" time="12:00">boom</LogItem>);
    expect(container.querySelector('.log-item').className).toBe('log-item log-error');
    expect(container.querySelector('.log-time').textContent).toBe('[12:00]');
    act(() => root.render(<EmptyState variant="modal" error>none</EmptyState>));
    expect(container.querySelector('div').className).toBe('modal-empty-state modal-empty-state-error');
  });
});

describe('Tabs + tables', () => {
  it('Tabs marks the active tab', () => {
    render(<Tabs value="b" onChange={() => {}} tabs={[{ id: 'a', label: 'A' }, { id: 'b', label: 'B' }]} />);
    const tabs = container.querySelectorAll('.viewer-tab');
    expect(tabs[0].className).toBe('viewer-tab');
    expect(tabs[1].className).toBe('viewer-tab is-active');
    expect(tabs[1].getAttribute('aria-selected')).toBe('true');
  });

  it('DataTable selects the variant class', () => {
    render(<DataTable variant="eval"><tbody><tr><td>x</td></tr></tbody></DataTable>);
    expect(container.querySelector('table').className).toBe('eval-table');
  });
});
