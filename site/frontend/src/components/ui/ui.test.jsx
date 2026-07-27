/* @vitest-environment jsdom */

import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  AppShell,
  Avatar,
  Badge,
  Banner,
  Button,
  Card,
  CardBody,
  CardHeader,
  ChatMessage,
  ChatTranscript,
  Checkbox,
  Chip,
  Composer,
  DataTable,
  Dropdown,
  EmptyState,
  Footer,
  IconButton,
  LogItem,
  PageHeader,
  Pill,
  ScoreChip,
  Select,
  Stepper,
  StatusChip,
  StatusDot,
  Tabs,
  TextArea,
  TextInput,
  ToggleSwitch,
  TypingIndicator,
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
      <Button variant="primary" size="sm" fixedWidth className="btn-download" title="go">
        Send
      </Button>,
    );
    const btn = container.querySelector('button');
    expect(btn.className).toBe('btn btn-primary btn-sm btn-fixed-width btn-download');
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

  it('TextInput maps controlSize to the documented large control class', () => {
    render(<TextInput controlSize="lg" value="" onChange={() => {}} />);
    expect(container.querySelector('input').className).toBe('prompt-dropdown-select');
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

  it('Dropdown renders a closed listbox trigger with the placeholder', () => {
    render(
      <Dropdown value="" onChange={() => {}} placeholder="Pick" options={[{ value: 'a', label: 'A' }]} />,
    );
    const root = container.querySelector('.dropdown');
    const trigger = root.querySelector('button.dropdown-trigger');
    expect(trigger.getAttribute('role')).toBe('combobox');
    expect(trigger.getAttribute('aria-haspopup')).toBe('listbox');
    expect(trigger.getAttribute('aria-expanded')).toBe('false');
    expect(trigger.hasAttribute('aria-activedescendant')).toBe(false);
    expect(trigger.querySelector('.dropdown-trigger-label.is-placeholder').textContent).toBe('Pick');
    // Closed → no portaled listbox yet.
    expect(document.body.querySelector('[role="listbox"]')).toBeNull();
  });

  it('Dropdown tracks listbox focus through the combobox active descendant', () => {
    render(
      <Dropdown
        aria-label="Question theme"
        value=""
        onChange={() => {}}
        options={[
          { value: 'a', label: 'Alpha' },
          { value: 'b', label: 'Beta' },
        ]}
      />,
    );
    const trigger = container.querySelector('.dropdown-trigger');
    trigger.focus();

    act(() => {
      trigger.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }));
    });

    const listbox = document.body.querySelector('[role="listbox"]');
    const options = listbox.querySelectorAll('[role="option"]');
    expect(trigger.getAttribute('role')).toBe('combobox');
    expect(trigger.getAttribute('aria-controls')).toBe(listbox.id);
    expect(trigger.getAttribute('aria-activedescendant')).toBe(options[0].id);
    expect(listbox.getAttribute('aria-label')).toBe('Question theme');
    expect(document.activeElement).toBe(trigger);

    act(() => {
      trigger.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }));
    });
    expect(trigger.getAttribute('aria-activedescendant')).toBe(options[1].id);
    expect(document.activeElement).toBe(trigger);
  });

  it('Dropdown opens on click (portaled) and commits the chosen value', () => {
    let picked = null;
    render(
      <Dropdown
        value=""
        onChange={(v) => {
          picked = v;
        }}
        options={[
          { value: 'a', label: 'Alpha' },
          { value: 'b', label: 'Beta' },
        ]}
      />,
    );
    act(() => container.querySelector('.dropdown-trigger').click());
    const listbox = document.body.querySelector('[role="listbox"]');
    expect(listbox).toBeTruthy();
    const options = listbox.querySelectorAll('[role="option"]');
    expect(options).toHaveLength(2);
    act(() => options[1].click());
    expect(picked).toBe('b');
    // Selecting closes the menu.
    expect(document.body.querySelector('[role="listbox"]')).toBeNull();
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

describe('Checkbox', () => {
  it('emits one native checkbox and reports its checked state', () => {
    let checked = false;
    render(
      <Checkbox
        checked={false}
        onChange={(nextChecked) => {
          checked = nextChecked;
        }}
        aria-label="Select run"
      />,
    );
    const input = container.querySelector('input[type="checkbox"]');
    expect(input).toBeTruthy();
    act(() => input.click());
    expect(checked).toBe(true);
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

  it('ScoreChip with onClick renders an interactive button', () => {
    let clicked = false;
    render(
      <ScoreChip tone="pass" onClick={() => { clicked = true; }}>
        1
      </ScoreChip>,
    );
    const chip = container.querySelector('button.eval-score-chip');
    expect(chip.className).toBe('eval-score-chip is-pass is-clickable');
    expect(chip.getAttribute('type')).toBe('button');
    act(() => chip.click());
    expect(clicked).toBe(true);
  });

  it('Pill maps state', () => {
    render(<Pill state="running">Running</Pill>);
    expect(container.querySelector('span').className).toBe('create-data-pill is-running');
  });

  it('Chip emits .chip + tone/shape/mono modifiers', () => {
    render(<Chip tone="success" shape="square" mono>gpt-5.5</Chip>);
    expect(container.querySelector('span').className).toBe('chip chip-success chip-square chip-mono');
    act(() => root.render(<Chip>primary</Chip>));
    expect(container.querySelector('span').className).toBe('chip');
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
    expect(container.querySelector('div').getAttribute('role')).toBe('alert');
    expect(container.querySelector('div').getAttribute('aria-live')).toBe('assertive');
  });
});

describe('Tabs + tables', () => {
  it('Tabs marks the active tab', () => {
    const onChange = vi.fn();
    render(<Tabs value="b" onChange={onChange} tabs={[{ id: 'a', label: 'A' }, { id: 'b', label: 'B' }]} />);
    const tabs = container.querySelectorAll('.viewer-tab');
    expect(tabs[0].className).toBe('viewer-tab');
    expect(tabs[1].className).toBe('viewer-tab is-active');
    expect(tabs[1].getAttribute('aria-selected')).toBe('true');
    expect(tabs[0].tabIndex).toBe(-1);
    expect(tabs[1].tabIndex).toBe(0);

    act(() => {
      tabs[1].focus();
      tabs[1].dispatchEvent(new KeyboardEvent('keydown', {
        key: 'ArrowRight',
        bubbles: true,
      }));
    });
    expect(onChange).toHaveBeenCalledWith('a');
    expect(document.activeElement).toBe(tabs[0]);
  });

  it('DataTable selects the variant class', () => {
    render(<DataTable variant="eval"><tbody><tr><td>x</td></tr></tbody></DataTable>);
    expect(container.querySelector('table').className).toBe('eval-table');
  });
});

describe('shell primitives', () => {
  it('Banner emits .outage-banner with an optional detail span', () => {
    render(<Banner detail="us-east">down</Banner>);
    const banner = container.querySelector('.outage-banner');
    expect(banner.getAttribute('role')).toBe('alert');
    expect(banner.textContent).toContain('down');
    expect(container.querySelector('.outage-banner-detail').textContent).toContain('us-east');
  });

  it('PageHeader emits .header (+ .has-hero + .header-motif when motif is set)', () => {
    render(<PageHeader brand={<span>b</span>} aside={<span>a</span>} motif="/assets/physics-motif.svg" />);
    const header = container.querySelector('header.header');
    expect(header.className).toBe('header has-hero');
    expect(container.querySelector('.header-motif')).toBeTruthy();
    expect(container.querySelector('.header-content').textContent).toBe('ba');
  });

  it('PageHeader without a motif omits the hero overlay', () => {
    render(<PageHeader brand={<span>b</span>} />);
    expect(container.querySelector('header.header').className).toBe('header');
    expect(container.querySelector('.header-motif')).toBeNull();
  });

  it('Footer wraps children in .footer > .footer-content', () => {
    render(<Footer><span>f</span></Footer>);
    expect(container.querySelector('footer.footer .footer-content').textContent).toBe('f');
  });

  it('AppShell composes banner/header/main.content-shell/footer', () => {
    render(
      <AppShell banner={<div className="outage-banner">x</div>} header={<header className="header" />} footer={<footer className="footer" />}>
        <section>body</section>
      </AppShell>,
    );
    expect(container.querySelector('.outage-banner')).toBeTruthy();
    expect(container.querySelector('header.header')).toBeTruthy();
    expect(container.querySelector('.main-content .content-shell').textContent).toBe('body');
    expect(container.querySelector('footer.footer')).toBeTruthy();
  });
});

describe('chat primitives', () => {
  it('Avatar emits .chat-avatar + role and renders the default role icon', () => {
    render(<Avatar role="guardrail" label="Content Guardrail" />);
    const avatar = container.querySelector('.chat-avatar');
    expect(avatar.className).toBe('chat-avatar chat-avatar-guardrail');
    expect(avatar.getAttribute('aria-label')).toBe('Content Guardrail');
    expect(avatar.querySelector('svg')).toBeTruthy();
  });

  it('Avatar with an explicit icon uses it instead of the role default', () => {
    render(<Avatar role="user" icon={<span className="custom-icon" />} />);
    expect(container.querySelector('.custom-icon')).toBeTruthy();
    expect(container.querySelector('.chat-avatar').className).toContain('chat-avatar-user');
  });

  it('ChatMessage emits .chat-message + variant/tone and header/content/actions slots', () => {
    render(
      <ChatMessage
        variant="user"
        tone="success"
        avatar={<Avatar role="user" label="You" />}
        author="You"
        timestamp="12:00"
        actions={<Button variant="ghost">Open</Button>}
      >
        hello
      </ChatMessage>,
    );
    const msg = container.querySelector('.chat-message');
    expect(msg.className).toBe('chat-message chat-message-user chat-message-success');
    expect(container.querySelector('.chat-message-author').textContent).toBe('You');
    expect(container.querySelector('.chat-message-time').textContent).toBe('12:00');
    expect(container.querySelector('.chat-message-content').textContent).toBe('hello');
    expect(container.querySelector('.chat-message-actions .btn')).toBeTruthy();
  });

  it('ChatMessage grouped hides the avatar + header', () => {
    render(
      <ChatMessage variant="agent" grouped avatar={<Avatar role="agent" />} author="Agent" timestamp="12:01">
        more
      </ChatMessage>,
    );
    expect(container.querySelector('.chat-message').className).toContain('is-grouped');
    expect(container.querySelector('.chat-message-avatar').textContent).toBe('');
    expect(container.querySelector('.chat-message-header')).toBeNull();
  });

  it('ChatTranscript emits .chat-transcript and shows the empty state at count 0', () => {
    render(
      <ChatTranscript count={0} emptyState={<div className="ds-empty">nothing yet</div>}>
        <ChatMessage>x</ChatMessage>
      </ChatTranscript>,
    );
    const transcript = container.querySelector('.chat-transcript');
    expect(transcript.getAttribute('role')).toBe('log');
    expect(container.querySelector('.ds-empty')).toBeTruthy();
    expect(container.querySelector('.chat-message')).toBeNull();
    act(() =>
      root.render(
        <ChatTranscript count={1} emptyState={<div className="ds-empty">nothing yet</div>}>
          <ChatMessage>x</ChatMessage>
        </ChatTranscript>,
      ),
    );
    expect(container.querySelector('.chat-message')).toBeTruthy();
  });

  it('TypingIndicator emits .chat-typing with three dots + a label', () => {
    render(<TypingIndicator label="Generator is thinking" />);
    expect(container.querySelector('.chat-typing')).toBeTruthy();
    expect(container.querySelectorAll('.chat-typing-dot')).toHaveLength(3);
    expect(container.querySelector('.chat-typing-label').textContent).toBe('Generator is thinking');
  });

  it('Composer emits .composer with a .composer-input textarea + send button', () => {
    const onSend = () => {};
    render(
      <Composer
        value="hi"
        onChange={() => {}}
        onSend={onSend}
        onAttach={() => {}}
        maxLength={20000}
        attachLabel="Upload"
        themeSelect={{ value: '', onChange: () => {}, options: [{ value: 'a', label: 'A' }], placeholder: 'Theme' }}
      />,
    );
    expect(container.querySelector('.composer')).toBeTruthy();
    const composerInput = container.querySelector('textarea.composer-input');
    expect(composerInput).toBeTruthy();
    expect(composerInput.getAttribute('aria-label')).toBe('Workflow instructions');
    expect(composerInput.maxLength).toBe(20000);
    expect(container.querySelector('.composer-send')).toBeTruthy();
    expect(container.querySelector('.composer-attach')).toBeTruthy();
    // The theme selector is the custom Dropdown (a trigger button, not a native select).
    expect(container.querySelector('.composer-theme-select .dropdown-trigger')).toBeTruthy();
    // No theme selected → no active glow (on the wrapper).
    expect(container.querySelector('.composer-theme-select').className).not.toContain('is-active');
    act(() =>
      root.render(
        <Composer
          value="hi"
          onChange={() => {}}
          onSend={onSend}
          onAttach={() => {}}
          attachLabel="Upload"
          themeSelect={{ value: 'a', onChange: () => {}, options: [{ value: 'a', label: 'A' }], placeholder: 'Theme' }}
        />,
      ),
    );
    expect(container.querySelector('.composer-theme-select').className).toContain('is-active');
  });

  it('Composer forwards a custom accessible name to its textarea', () => {
    render(
      <Composer
        aria-label="Describe the question transformation"
        value=""
        onChange={() => {}}
        onSend={() => {}}
      />,
    );
    expect(container.querySelector('textarea.composer-input').getAttribute('aria-label')).toBe(
      'Describe the question transformation',
    );
    expect(container.querySelector('.composer').hasAttribute('aria-label')).toBe(false);
  });

  it('Composer send button flips to Stop while running', () => {
    render(<Composer value="" onChange={() => {}} onSend={() => {}} isRunning />);
    expect(container.querySelector('.composer-send').className).toContain('btn-danger');
    expect(container.querySelector('.composer-send').textContent).toBe('Stop');
  });
});

describe('process rail primitives', () => {
  it('Stepper emits a node per registry node with status/kind data attributes', () => {
    const nodes = [
      { key: 'setup', label: 'Setup', status: 'completed', kind: 'runtime' },
      { key: 'input_guardrail', label: 'Input Guardrail', status: 'blocked', kind: 'guardrail' },
      { key: 'normalizer', label: 'Normalizer', status: 'thinking', kind: 'agent' },
    ];
    render(<Stepper nodes={nodes} onNodeClick={() => {}} activeKey="normalizer" />);
    const stepNodes = container.querySelectorAll('.stepper-node');
    expect(stepNodes).toHaveLength(3);
    expect(stepNodes[1].getAttribute('data-node-kind')).toBe('guardrail');
    expect(stepNodes[1].getAttribute('data-status')).toBe('blocked');
    expect(stepNodes[2].className).toContain('is-active');
    // The cycling status word renders only above the thinking node's icon; the
    // (empty) word slot exists on every node so heights stay equal.
    expect(stepNodes[2].querySelector('.stepper-node-word-text')).toBeTruthy();
    expect(stepNodes[0].querySelector('.stepper-node-word-text')).toBeFalsy();
    expect(stepNodes[0].querySelector('.stepper-node-word')).toBeTruthy();
    // Connectors appear between nodes (n-1 of them).
    expect(container.querySelectorAll('.stepper-connector')).toHaveLength(2);
  });

  it('Stepper fires onNodeClick with the node key', () => {
    let clicked = null;
    render(<Stepper nodes={[{ key: 'output', label: 'Output', status: 'queued', kind: 'runtime' }]} onNodeClick={(k) => { clicked = k; }} />);
    act(() => container.querySelector('.stepper-node').click());
    expect(clicked).toBe('output');
  });

});
