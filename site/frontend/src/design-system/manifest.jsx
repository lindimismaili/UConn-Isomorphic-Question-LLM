import { useState } from 'react';
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
  Chip,
  Composer,
  DataTable,
  EmptyState,
  Footer,
  IconButton,
  KeyValueTable,
  LogItem,
  LogList,
  Modal,
  PageHeader,
  Pill,
  ScoreChip,
  SectionHeading,
  Select,
  Stepper,
  StatusChip,
  StatusDot,
  Tabs,
  TextArea,
  TextInput,
  TokenStream,
  ToggleSwitch,
  TypingIndicator,
} from '../components/ui/index.js';
import { IconClose } from '../components/Icons.jsx';

/**
 * Machine-readable catalog of every `ui/` primitive: one entry per
 * `src/components/ui/index.js` export. Drives the Components showcase
 * section (`sections/Components.jsx`) and the Modal pattern in
 * `sections/Patterns.jsx`. Fetch-free by construction — every fixture below
 * is a hardcoded literal, never data pulled from the agent registry or any
 * network call.
 *
 * Each entry's `Specimen` is an anonymous inline component (not a named
 * top-level declaration) so this data-only module doesn't trip the
 * `react-refresh/only-export-components` lint rule, which flags files that
 * mix component exports with non-component ones.
 */

/** The full 10-node pipeline fixture (including both guardrail nodes) for the Stepper specimen. */
const STEPPER_NODES = [
  { key: 'setup', label: 'Setup', status: 'completed', kind: 'runtime' },
  { key: 'input_guardrail', label: 'Input Guardrail', status: 'completed', kind: 'guardrail' },
  { key: 'normalizer', label: 'Normalizer', status: 'completed', kind: 'agent' },
  { key: 'extracter', label: 'Extracter', status: 'completed', kind: 'agent' },
  { key: 'content_guardrail', label: 'Content Guardrail', status: 'completed', kind: 'guardrail' },
  { key: 'verifier', label: 'Verifier', status: 'completed', kind: 'agent' },
  { key: 'generator', label: 'Generator', status: 'thinking', kind: 'agent' },
  { key: 'latexifier', label: 'Latexifier', status: 'queued', kind: 'agent' },
  { key: 'evaluation', label: 'Evaluation', status: 'queued', kind: 'agent' },
  { key: 'output', label: 'Output', status: 'queued', kind: 'runtime' },
];

export const MANIFEST = [
  {
    name: 'Button',
    group: 'Buttons',
    summary:
      'The single button primitive — always `.btn` + exactly one variant, or `.btn-icon-only` when `iconOnly`.',
    props: [
      { name: 'variant', type: "'primary'|'secondary'|'ghost'|'danger'|null" },
      { name: 'size', type: "'sm'|'default'" },
      { name: 'icon', type: 'ReactNode' },
      { name: 'iconOnly', type: 'boolean' },
      { name: 'fixedWidth', type: 'boolean' },
      { name: 'feedbackState', type: "'success'|'error'" },
      { name: 'className', type: 'string' },
      { name: 'children', type: 'ReactNode' },
    ],
    usage: '<Button variant="primary">Send</Button>',
    Specimen: () => (
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--spacing-sm)' }}>
        <Button variant="primary">Primary</Button>
        <Button variant="secondary">Secondary</Button>
        <Button variant="ghost">Ghost</Button>
        <Button variant="danger">Danger</Button>
      </div>
    ),
  },
  {
    name: 'IconButton',
    group: 'Buttons',
    summary: 'The standalone `.btn-icon-only` 36×36 square control (close buttons, compact icon actions).',
    props: [
      { name: 'icon', type: 'ReactNode', required: true },
      { name: 'aria-label', type: 'string', required: true, description: 'Accessible label (icon-only control).' },
    ],
    usage: '<IconButton aria-label="Close" icon={<IconClose />} />',
    Specimen: () => <IconButton aria-label="Close" icon={<IconClose />} />,
  },
  {
    name: 'TextInput',
    group: 'Form controls',
    summary: 'Single-line text input; inherits the global `input` styling.',
    props: [
      { name: 'value', type: 'string' },
      { name: 'onChange', type: '(event) => void' },
      { name: 'type', type: 'string' },
      { name: 'placeholder', type: 'string' },
      { name: 'disabled', type: 'boolean' },
      { name: 'className', type: 'string' },
    ],
    usage: '<TextInput placeholder="Enter a value…" value={value} onChange={onChange} />',
    Specimen: () => {
      const [value, setValue] = useState('');
      return (
        <TextInput placeholder="Enter a value…" value={value} onChange={(event) => setValue(event.target.value)} />
      );
    },
  },
  {
    name: 'TextArea',
    group: 'Form controls',
    summary: 'Multi-line text area defaulting to the `.prompt-input` treatment (180px min-height).',
    props: [
      { name: 'value', type: 'string' },
      { name: 'onChange', type: '(event) => void' },
      { name: 'placeholder', type: 'string' },
      { name: 'className', type: 'string' },
    ],
    usage: '<TextArea placeholder="Enter instructions…" value={value} onChange={onChange} />',
    Specimen: () => {
      const [value, setValue] = useState('');
      return (
        <TextArea
          placeholder="Enter instructions…"
          style={{ minHeight: 96 }}
          value={value}
          onChange={(event) => setValue(event.target.value)}
        />
      );
    },
  },
  {
    name: 'Select',
    group: 'Form controls',
    summary: 'Select control rendering `options` (or `children`) inside the documented `.prompt-dropdown-select`.',
    props: [
      { name: 'options', type: 'Array<{value, label}>' },
      { name: 'placeholder', type: 'string' },
      { name: 'children', type: 'ReactNode' },
      { name: 'value', type: 'string' },
      { name: 'onChange', type: '(event) => void' },
    ],
    usage: '<Select placeholder="Select a theme…" options={options} value={value} onChange={onChange} />',
    Specimen: () => {
      const [value, setValue] = useState('');
      return (
        <Select
          placeholder="Select a theme…"
          value={value}
          onChange={(event) => setValue(event.target.value)}
          options={[
            { value: 'physics', label: 'Physics' },
            { value: 'biology', label: 'Biology' },
          ]}
        />
      );
    },
  },
  {
    name: 'ToggleSwitch',
    group: 'Toggles',
    summary: 'The documented `.developer-toggle` boolean control — a label-wrapped checkbox styled as a switch.',
    props: [
      { name: 'checked', type: 'boolean', required: true },
      { name: 'onChange', type: '(checked: boolean) => void', required: true },
      { name: 'label', type: 'ReactNode' },
      { name: 'disabled', type: 'boolean' },
      { name: 'className', type: 'string' },
    ],
    usage: '<ToggleSwitch checked={checked} onChange={setChecked} label="Run Evaluations" />',
    Specimen: () => {
      const [checked, setChecked] = useState(true);
      return <ToggleSwitch checked={checked} onChange={setChecked} label="Run Evaluations" />;
    },
  },
  {
    name: 'Card',
    group: 'Cards',
    summary: '`.card` + optional documented status/context variant; cards never lift on hover.',
    props: [
      { name: 'variant', type: "'completed'|'failed'|'main'|'developer-tools'|'workflow'|'log-panel'" },
      { name: 'className', type: 'string' },
      { name: 'children', type: 'ReactNode' },
    ],
    usage: '<Card variant="completed">…</Card>',
    Specimen: () => (
      <Card>
        <CardHeader title="Base card" subtitle="Neutral surface" />
        <CardBody>Body content.</CardBody>
      </Card>
    ),
  },
  {
    name: 'CardHeader',
    group: 'Cards',
    summary: 'Card header band — renders `title` as the h2, optional `subtitle`, `disclaimer`, and an `actions` group.',
    props: [
      { name: 'title', type: 'ReactNode' },
      { name: 'subtitle', type: 'ReactNode' },
      { name: 'disclaimer', type: 'ReactNode' },
      { name: 'actions', type: 'ReactNode' },
      { name: 'className', type: 'string' },
    ],
    usage: '<CardHeader title="Buttons" subtitle="…" />',
    Specimen: () => (
      <Card>
        <CardHeader title="Header title" subtitle="Header subtitle" />
      </Card>
    ),
  },
  {
    name: 'CardBody',
    group: 'Cards',
    summary: 'Card body; applies `--panel-padding` via `.card-body`.',
    props: [
      { name: 'className', type: 'string' },
      { name: 'children', type: 'ReactNode' },
    ],
    usage: '<CardBody>Body content.</CardBody>',
    Specimen: () => (
      <Card>
        <CardBody>Card body content.</CardBody>
      </Card>
    ),
  },
  {
    name: 'SectionHeading',
    group: 'Cards',
    summary: 'Standalone section heading (h2 + optional subtitle) for non-card headers.',
    props: [
      { name: 'title', type: 'ReactNode', required: true },
      { name: 'subtitle', type: 'ReactNode' },
    ],
    usage: '<SectionHeading title="Section title" subtitle="…" />',
    Specimen: () => <SectionHeading title="Section title" subtitle="Section subtitle" />,
  },
  {
    name: 'Badge',
    group: 'Status & feedback',
    summary: 'Small tinted tag; `kind` selects the documented badge class, `tone` adds the reference-kind color variant.',
    props: [
      { name: 'kind', type: "'source-id'|'reference-kind'|'rubric-index'|'rubric-category'|'step-chip'" },
      { name: 'tone', type: "'input'|'test'|'rubric'" },
      { name: 'className', type: 'string' },
      { name: 'children', type: 'ReactNode' },
    ],
    usage: '<Badge kind="source-id">Q-12</Badge>',
    Specimen: () => (
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--spacing-sm)' }}>
        <Badge kind="source-id">Q-12</Badge>
        <Badge kind="reference-kind" tone="input">
          input
        </Badge>
        <Badge kind="rubric-category">Preservation</Badge>
      </div>
    ),
  },
  {
    name: 'StatusChip',
    group: 'Status & feedback',
    summary: 'Eval status chip (`.eval-status-chip` + `.eval-status-{status}`).',
    props: [
      { name: 'status', type: 'string', required: true },
      { name: 'children', type: 'ReactNode' },
    ],
    usage: '<StatusChip status="passed" />',
    Specimen: () => (
      <div style={{ display: 'flex', gap: 'var(--spacing-sm)' }}>
        <StatusChip status="passed" />
        <StatusChip status="failed" />
      </div>
    ),
  },
  {
    name: 'ScoreChip',
    group: 'Status & feedback',
    summary:
      'Eval score chip (`.eval-score-chip` + `.is-pass|.is-fail|.is-neutral`); passing `onClick` renders a real `<button>` instead of a `<span>`.',
    props: [
      { name: 'tone', type: "'pass'|'fail'|'neutral'" },
      { name: 'interactive', type: 'boolean' },
      { name: 'onClick', type: '() => void' },
      { name: 'className', type: 'string' },
      { name: 'children', type: 'ReactNode' },
    ],
    usage: '<ScoreChip tone="pass" onClick={onClick}>1</ScoreChip>',
    Specimen: () => {
      const [clicks, setClicks] = useState(0);
      return (
        <div style={{ display: 'flex', gap: 'var(--spacing-sm)', alignItems: 'center' }}>
          <ScoreChip tone="pass">1</ScoreChip>
          <ScoreChip tone="fail">0</ScoreChip>
          <ScoreChip tone="pass" onClick={() => setClicks((c) => c + 1)}>
            {clicks}
          </ScoreChip>
        </div>
      );
    },
  },
  {
    name: 'Pill',
    group: 'Status & feedback',
    summary: 'Create-data status pill (`.create-data-pill` + `.is-{state}`).',
    props: [
      { name: 'state', type: 'string' },
      { name: 'children', type: 'ReactNode' },
    ],
    usage: '<Pill state="running">Running</Pill>',
    Specimen: () => (
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--spacing-sm)' }}>
        <Pill state="idle">Idle</Pill>
        <Pill state="running">Running</Pill>
        <Pill state="completed">Completed</Pill>
      </div>
    ),
  },
  {
    name: 'Chip',
    group: 'Status & feedback',
    summary: 'The generic small tinted tag (`.chip` + modifiers) consolidating the eval/create-data/rubric/viewer tag looks.',
    props: [
      { name: 'tone', type: "'primary'|'neutral'|'success'|'warning'|'error'" },
      { name: 'shape', type: "'pill'|'square'" },
      { name: 'mono', type: 'boolean' },
      { name: 'className', type: 'string' },
      { name: 'children', type: 'ReactNode' },
    ],
    usage: '<Chip tone="success">passed</Chip>',
    Specimen: () => (
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--spacing-sm)' }}>
        <Chip>primary</Chip>
        <Chip tone="success">passed</Chip>
        <Chip tone="error">failed</Chip>
        <Chip mono tone="neutral">
          gpt-5.5
        </Chip>
      </div>
    ),
  },
  {
    name: 'StatusDot',
    group: 'Status & feedback',
    summary: 'Status dot (`.status-indicator` + status) — a 12px circle with the matching semantic color + glow; `thinking` pulses.',
    props: [
      { name: 'status', type: "'inactive'|'thinking'|'completed'|'errored'|'cancelled'" },
      { name: 'className', type: 'string' },
    ],
    usage: '<StatusDot status="thinking" />',
    Specimen: () => (
      <div style={{ display: 'flex', gap: 'var(--spacing-md)' }}>
        {['inactive', 'thinking', 'completed', 'errored'].map((status) => (
          <StatusDot key={status} status={status} />
        ))}
      </div>
    ),
  },
  {
    name: 'LogItem',
    group: 'Status & feedback',
    summary: 'Single log line (`.log-item` + `.log-{level}`) with a `.log-time` timestamp.',
    props: [
      { name: 'level', type: "'success'|'warning'|'error'|'info'" },
      { name: 'time', type: 'string' },
      { name: 'children', type: 'ReactNode', required: true },
    ],
    usage: '<LogItem level="success" time="12:00:09">Extracter completed.</LogItem>',
    Specimen: () => (
      <LogItem level="success" time="12:00:09">
        Extracter completed — 8 questions.
      </LogItem>
    ),
  },
  {
    name: 'LogList',
    group: 'Status & feedback',
    summary: 'Log list — renders `LogItem`s or an `EmptyState` fallback.',
    props: [
      { name: 'entries', type: 'Array<{id, level, time?, message}>', required: true },
      { name: 'emptyText', type: 'ReactNode' },
    ],
    usage: '<LogList entries={entries} />',
    Specimen: () => (
      <div className="log-frame" style={{ maxHeight: 120, overflow: 'auto' }}>
        <LogList
          entries={[
            { id: 1, level: 'info', time: '12:00:01', message: 'Workflow started.' },
            { id: 2, level: 'success', time: '12:00:09', message: 'Extracter completed.' },
          ]}
        />
      </div>
    ),
  },
  {
    name: 'EmptyState',
    group: 'Status & feedback',
    summary: 'Empty-state block (`.log-empty-state` / `.modal-empty-state`, + error variant).',
    props: [
      { name: 'variant', type: "'log'|'modal'" },
      { name: 'error', type: 'boolean', description: 'Adds `.modal-empty-state-error` (modal variant).' },
      { name: 'children', type: 'ReactNode', required: true },
    ],
    usage: '<EmptyState>Logs will appear here.</EmptyState>',
    Specimen: () => <EmptyState>Logs will appear here when you start a workflow.</EmptyState>,
  },
  {
    name: 'Tabs',
    group: 'Tabs',
    summary: 'Tabs primitive rendering the `.viewer-tab-bar` with `.viewer-tab` buttons; the active tab gets `.is-active`.',
    props: [
      { name: 'value', type: 'string', required: true },
      { name: 'onChange', type: '(id: string) => void', required: true },
      { name: 'tabs', type: 'Array<{id, label}>', required: true },
      { name: 'className', type: 'string' },
    ],
    usage: '<Tabs value={value} onChange={onChange} tabs={tabs} />',
    Specimen: () => {
      const [tab, setTab] = useState('config');
      return (
        <Tabs
          value={tab}
          onChange={setTab}
          tabs={[
            { id: 'config', label: 'Config' },
            { id: 'source', label: 'Source Qs' },
            { id: 'generated', label: 'Generated Qs' },
          ]}
        />
      );
    },
  },
  {
    name: 'DataTable',
    group: 'Tables',
    summary: 'Scrollable table; `variant` selects the documented base class (`eval-table` / `file-viewer-table`).',
    props: [
      { name: 'variant', type: "'eval'|'file-viewer'" },
      { name: 'className', type: 'string' },
      { name: 'children', type: 'ReactNode', description: '<thead> / <tbody>.' },
    ],
    usage: '<DataTable variant="eval"><thead>…</thead><tbody>…</tbody></DataTable>',
    Specimen: () => (
      <div className="file-viewer-table-shell">
        <DataTable variant="eval">
          <thead>
            <tr>
              <th>Source</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>
                <Badge kind="source-id">Q-1</Badge>
              </td>
              <td>
                <StatusChip status="passed" />
              </td>
            </tr>
          </tbody>
        </DataTable>
      </div>
    ),
  },
  {
    name: 'KeyValueTable',
    group: 'Tables',
    summary: 'Key/value table (`.viewer-kv-table`) for config-style rows.',
    props: [
      { name: 'rows', type: 'Array<{key, value}>', required: true },
      { name: 'className', type: 'string' },
    ],
    usage: '<KeyValueTable rows={[{ key: "theme", value: "Physics" }]} />',
    Specimen: () => (
      <KeyValueTable
        rows={[
          { key: 'theme', value: 'Physics' },
          { key: 'difficulty', value: 'medium' },
        ]}
      />
    ),
  },
  {
    name: 'AppShell',
    group: 'Shell',
    summary:
      'The outer scaffold: optional `banner`, a `header`, the centered `.main-content` / `.content-shell` region, and an optional `footer`.',
    props: [
      { name: 'banner', type: 'ReactNode' },
      { name: 'header', type: 'ReactNode' },
      { name: 'footer', type: 'ReactNode' },
      { name: 'children', type: 'ReactNode', required: true },
      { name: 'mainClassName', type: 'string' },
    ],
    usage: '<AppShell header={<PageHeader .../>} footer={<Footer>…</Footer>}>…</AppShell>',
    Specimen: () => (
      <div style={{ border: '1px solid var(--color-border)', borderRadius: 'var(--radius-lg)', overflow: 'hidden' }}>
        <AppShell
          header={<PageHeader style={{ position: 'static' }} brand={<strong>Brand</strong>} />}
          footer={
            <Footer>
              <span className="subtitle">Footer content</span>
            </Footer>
          }
        >
          <p className="subtitle">Main content region.</p>
        </AppShell>
      </div>
    ),
  },
  {
    name: 'PageHeader',
    group: 'Shell',
    summary: 'PageHeader / Hero — the sticky navy masthead with an optional physics motif behind the content.',
    props: [
      { name: 'brand', type: 'ReactNode', required: true },
      { name: 'aside', type: 'ReactNode' },
      { name: 'motif', type: 'string', description: 'URL of a background motif SVG.' },
      { name: 'className', type: 'string' },
    ],
    usage: '<PageHeader brand={<Logo />} aside={<Mark />} motif={HEADER_MOTIF} />',
    Specimen: () => (
      <div style={{ borderRadius: 'var(--radius-lg)', overflow: 'hidden', width: '100%' }}>
        <PageHeader
          style={{ position: 'static' }}
          brand={
            <div className="logo">
              <h1 style={{ fontSize: '1.1rem' }}>Brand</h1>
            </div>
          }
          aside={<span className="subtitle">Aside</span>}
        />
      </div>
    ),
  },
  {
    name: 'Banner',
    group: 'Shell',
    summary: 'The full-width notice strip above the header (replaces the raw `.outage-banner`).',
    props: [
      { name: 'children', type: 'ReactNode', required: true },
      { name: 'detail', type: 'ReactNode' },
      { name: 'role', type: 'string' },
      { name: 'className', type: 'string' },
    ],
    usage: '<Banner detail="degraded region us-east">OpenAI servers are down.</Banner>',
    Specimen: () => <Banner detail="degraded region us-east">OpenAI servers are down.</Banner>,
  },
  {
    name: 'Footer',
    group: 'Shell',
    summary: 'The centered footer band.',
    props: [
      { name: 'children', type: 'ReactNode', required: true },
      { name: 'className', type: 'string' },
    ],
    usage: '<Footer><p>Version 1.0</p></Footer>',
    Specimen: () => (
      <div style={{ borderRadius: 'var(--radius-lg)', overflow: 'hidden', width: '100%' }}>
        <Footer>
          <p className="footer-primary">Footer primary line</p>
        </Footer>
      </div>
    ),
  },
  {
    name: 'Avatar',
    group: 'Chat',
    summary: 'A round identity chip (`.chat-avatar` + `.chat-avatar-{role}`) showing a custom icon, role default icon, or an initial.',
    props: [
      { name: 'role', type: "'user'|'assistant'|'agent'|'runtime'|'guardrail'" },
      { name: 'icon', type: 'ReactNode' },
      { name: 'label', type: 'string' },
      { name: 'className', type: 'string' },
    ],
    usage: '<Avatar role="agent" label="Generator" />',
    Specimen: () => (
      <div style={{ display: 'flex', gap: 'var(--spacing-sm)' }}>
        <Avatar role="user" label="You" />
        <Avatar role="agent" label="Generator" />
        <Avatar role="guardrail" label="Guardrail" />
      </div>
    ),
  },
  {
    name: 'ChatMessage',
    group: 'Chat',
    summary: "One conversation turn (`.chat-message` + `.chat-message-{variant}`); `user` bubbles align right, `assistant`/`agent` align left.",
    props: [
      { name: 'variant', type: "'user'|'assistant'|'agent'" },
      { name: 'avatar', type: 'ReactNode' },
      { name: 'author', type: 'ReactNode' },
      { name: 'timestamp', type: 'ReactNode' },
      { name: 'actions', type: 'ReactNode' },
      { name: 'tone', type: "'success'|'error'|'blocked'|'thinking'" },
      { name: 'grouped', type: 'boolean' },
      { name: 'children', type: 'ReactNode', required: true },
    ],
    usage: '<ChatMessage variant="assistant" tone="success" author="Assistant" timestamp="12:02">Done.</ChatMessage>',
    Specimen: () => (
      <ChatMessage
        variant="assistant"
        tone="success"
        avatar={<Avatar role="assistant" label="Assistant" />}
        author="Assistant"
        timestamp="12:02"
      >
        Done — 6 questions generated and compiled.
      </ChatMessage>
    ),
  },
  {
    name: 'ChatTranscript',
    group: 'Chat',
    summary: 'The scrollable conversation surface (`.chat-transcript`) that auto-scrolls to the newest message.',
    props: [
      { name: 'children', type: 'ReactNode', required: true },
      { name: 'count', type: 'number', description: 'Message count; auto-scroll fires when it grows.' },
      { name: 'emptyState', type: 'ReactNode' },
      { name: 'className', type: 'string' },
    ],
    usage: '<ChatTranscript count={messages.length}>{messages.map(…)}</ChatTranscript>',
    Specimen: () => (
      <div
        style={{
          border: '1px solid var(--color-border)',
          borderRadius: 'var(--radius-lg)',
          maxHeight: 180,
          overflow: 'hidden',
          width: '100%',
        }}
      >
        <ChatTranscript count={2}>
          <ChatMessage variant="user" avatar={<Avatar role="user" label="You" />} author="You" timestamp="12:00">
            Make them harder.
          </ChatMessage>
          <ChatMessage
            variant="agent"
            avatar={<Avatar role="agent" label="Extracter" />}
            author="Extracter"
            timestamp="12:01"
          >
            Extracted 4 source questions.
          </ChatMessage>
        </ChatTranscript>
      </div>
    ),
  },
  {
    name: 'TypingIndicator',
    group: 'Chat',
    summary: 'The animated "agent is working" dots (`.chat-typing`).',
    props: [
      { name: 'label', type: 'ReactNode' },
      { name: 'className', type: 'string' },
    ],
    usage: '<TypingIndicator label="Generator is working" />',
    Specimen: () => <TypingIndicator label="Generator is working" />,
  },
  {
    name: 'Composer',
    group: 'Chat',
    summary: 'The pinned chat input (`.composer`) — a multiline textarea with attach, theme select, and send controls.',
    props: [
      { name: 'value', type: 'string', required: true },
      { name: 'onChange', type: '(value: string) => void', required: true },
      { name: 'onSend', type: '() => void', required: true },
      { name: 'onAttach', type: '() => void' },
      { name: 'placeholder', type: 'string' },
      { name: 'disabled', type: 'boolean' },
      { name: 'isRunning', type: 'boolean' },
      { name: 'themeSelect', type: '{value, onChange, options, placeholder, disabled}' },
      { name: 'attachLabel', type: 'ReactNode' },
    ],
    usage: '<Composer value={value} onChange={setValue} onSend={onSend} themeSelect={themeSelect} />',
    Specimen: () => {
      const [value, setValue] = useState('Generate 2 isomorphic questions per source, medium difficulty.');
      return (
        <div
          style={{
            border: '1px solid var(--color-border)',
            borderRadius: 'var(--radius-lg)',
            overflow: 'hidden',
            width: '100%',
          }}
        >
          <Composer
            value={value}
            onChange={setValue}
            onSend={() => {}}
            onAttach={() => {}}
            attachLabel="Attach"
            placeholder="Describe the questions…"
            themeSelect={{
              value: '',
              onChange: () => {},
              placeholder: 'Select a theme…',
              options: [{ value: 'physics', label: 'Physics' }],
            }}
          />
        </div>
      );
    },
  },
  {
    name: 'Stepper',
    group: 'Process rail',
    summary: "A compact inline process-rail rendering a workflow's nodes in order with a per-node status dot and kind affordance.",
    props: [
      { name: 'nodes', type: 'Array<{key, label, status?, kind?}>', required: true },
      { name: 'onNodeClick', type: '(key: string) => void' },
      { name: 'activeKey', type: 'string', description: 'Node currently working (gets the `stream` slot).' },
      { name: 'stream', type: 'ReactNode', description: 'Live-output node (e.g. TokenStream).' },
      { name: 'className', type: 'string' },
    ],
    usage: '<Stepper nodes={nodes} onNodeClick={onNodeClick} activeKey={activeKey} />',
    Specimen: () => (
      <div style={{ width: '100%' }}>
        <Stepper nodes={STEPPER_NODES} onNodeClick={() => {}} activeKey="generator" />
      </div>
    ),
  },
  {
    name: 'TokenStream',
    group: 'Process rail',
    summary: 'A matrix-style live-output view rendering accumulated streamed text with a GPU-light rain backdrop.',
    props: [
      { name: 'text', type: 'string' },
      { name: 'active', type: 'boolean' },
      { name: 'columns', type: 'number' },
      { name: 'placeholder', type: 'string' },
      { name: 'className', type: 'string' },
    ],
    usage: '<TokenStream active text={streamedText} />',
    Specimen: () => (
      <div style={{ width: 'min(360px, 100%)' }}>
        <TokenStream active text={'∫ F·dr = ΔKE\nGenerating distractors for question 2…'} />
      </div>
    ),
  },
  {
    name: 'Modal',
    group: 'Overlays',
    summary: 'Base modal wrapper for all in-app dialogs — handles backdrop clicks, Escape-to-close, scroll locking, and portal rendering.',
    props: [
      { name: 'isOpen', type: 'boolean', required: true },
      { name: 'onClose', type: '() => void', required: true },
      { name: 'title', type: 'ReactNode' },
      { name: 'large', type: 'boolean' },
      { name: 'size', type: 'string' },
      { name: 'footer', type: 'ReactNode' },
      { name: 'children', type: 'ReactNode' },
    ],
    usage: '<Modal isOpen={open} onClose={close} title="Modal title" footer={<Actions />}>…</Modal>',
    Specimen: () => {
      const [open, setOpen] = useState(false);
      return (
        <>
          <Button variant="primary" onClick={() => setOpen(true)}>
            Open modal
          </Button>
          <Modal
            isOpen={open}
            onClose={() => setOpen(false)}
            title="Modal title"
            footer={
              <>
                <Button variant="secondary" onClick={() => setOpen(false)}>
                  Cancel
                </Button>
                <Button variant="primary" onClick={() => setOpen(false)}>
                  Confirm
                </Button>
              </>
            }
          >
            <p className="subtitle">Body scrolls; footer actions are .btn.</p>
          </Modal>
        </>
      );
    },
  },
];
