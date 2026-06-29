import { useState } from 'react';
import {
  Badge,
  Button,
  Card,
  CardBody,
  CardHeader,
  DataTable,
  EmptyState,
  IconButton,
  KeyValueTable,
  LogList,
  Pill,
  ScoreChip,
  Select,
  StatusChip,
  StatusDot,
  Tabs,
  TextArea,
  TextInput,
  ToggleSwitch,
} from '../../components/ui/index.js';
import { IconInfo, IconSend, IconUpload, IconClose } from '../../components/Icons.jsx';
import { Grid, Row, Spec, Stack } from '../Specimen.jsx';

const SAMPLE_LOGS = [
  { id: 1, level: 'info', time: '12:00:01', message: 'Workflow started.' },
  { id: 2, level: 'success', time: '12:00:09', message: 'Extracter completed — 8 questions.' },
  { id: 3, level: 'warning', time: '12:00:14', message: 'Generator retried once (empty output).' },
  { id: 4, level: 'error', time: '12:00:20', message: 'LaTeX compile reported a warning.' },
];

/** Components: every primitive with its variants and states (DESIGN.md §4–§10). */
export default function Components() {
  const [toggle, setToggle] = useState(true);
  const [tab, setTab] = useState('config');

  return (
    <Stack gap="var(--section-gap)">
      <h2 id="components" style={{ scrollMarginTop: '80px' }}>
        Components
      </h2>

      <Card>
        <CardHeader title="Buttons" subtitle="One .btn variant + one height tier per group. Reuse premade sub-variants; never add a new *-btn class." />
        <CardBody>
          <Stack gap="var(--spacing-lg)">
            <Spec label="Variants">
              <Button variant="primary" icon={<IconSend className="btn-icon" />}>
                Primary
              </Button>
              <Button variant="secondary">Secondary</Button>
              <Button variant="ghost">Ghost</Button>
              <Button variant="danger">Danger</Button>
            </Spec>
            <Spec label="Sizes & modifiers">
              <Button variant="secondary" size="sm">
                Small (.btn-sm)
              </Button>
              <Button variant="secondary" fixedWidth>
                Fixed width
              </Button>
              <Button variant="secondary" icon={<IconUpload className="btn-icon" />}>
                <span className="btn-label">With icon</span>
              </Button>
            </Spec>
            <Spec label="States">
              <Button variant="primary" disabled>
                Disabled
              </Button>
              <Button variant="primary" feedbackState="success">
                Saved
              </Button>
              <Button variant="secondary" feedbackState="error">
                Failed
              </Button>
            </Spec>
            <Spec label="Icon-only">
              <IconButton aria-label="Close" icon={<IconClose />} />
              <Button variant="secondary" className="help-btn">
                <IconInfo className="btn-icon" />
                <span>help-btn (premade)</span>
              </Button>
            </Spec>
            <Spec label="Height tier — prompt actions row (56px)">
              <div className="prompt-actions-row" style={{ display: 'flex', gap: 'var(--spacing-sm)' }}>
                <Button variant="secondary">Upload</Button>
                <Button variant="primary">Send</Button>
              </div>
            </Spec>
          </Stack>
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="Form controls" subtitle="44px controls · 180px textarea · shared focus ring." />
        <CardBody>
          <Stack>
            <Spec label="Text input">
              <TextInput placeholder="Enter a value…" defaultValue="" />
              <TextInput placeholder="Disabled" disabled />
            </Spec>
            <Spec label="Select">
              <Select
                placeholder="Select a theme…"
                defaultValue=""
                options={[
                  { value: 'physics', label: 'Physics' },
                  { value: 'biology', label: 'Biology' },
                ]}
              />
            </Spec>
            <Spec label="Textarea">
              <TextArea placeholder="Enter instructions…" style={{ minHeight: 96 }} />
            </Spec>
            <Spec label="Toggle switch">
              <ToggleSwitch checked={toggle} onChange={setToggle} label="Run Evaluations" />
              <ToggleSwitch checked={false} onChange={() => {}} label="Disabled" disabled />
            </Spec>
          </Stack>
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="Cards" subtitle="Base + documented status variants. No hover lift (the clickable agent-card is the one exception)." />
        <CardBody>
          <Grid min="240px">
            <Card>
              <CardHeader title="Base card" subtitle="Neutral surface" />
              <CardBody>Body content.</CardBody>
            </Card>
            <Card variant="completed">
              <CardHeader title="Completed" />
              <CardBody>Success border + glow.</CardBody>
            </Card>
            <Card variant="failed">
              <CardHeader title="Failed" />
              <CardBody>Error border + glow.</CardBody>
            </Card>
          </Grid>
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="Status & feedback" subtitle="Semantic color + glow — never a raw hex." />
        <CardBody>
          <Stack gap="var(--spacing-lg)">
            <Spec label="Status dots">
              <Row>
                {['inactive', 'thinking', 'completed', 'errored', 'cancelled'].map((s) => (
                  <span key={s} style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                    <StatusDot status={s} />
                    <span style={{ fontSize: '0.8rem', color: 'var(--color-text-secondary)' }}>{s}</span>
                  </span>
                ))}
              </Row>
            </Spec>
            <Spec label="Eval status chips">
              <StatusChip status="passed" />
              <StatusChip status="failed" />
              <StatusChip status="skipped" />
            </Spec>
            <Spec label="Eval score chips">
              <ScoreChip tone="pass">1</ScoreChip>
              <ScoreChip tone="fail">0</ScoreChip>
              <ScoreChip tone="neutral">N/A</ScoreChip>
            </Spec>
            <Spec label="Create-data pills">
              <Pill state="idle">Idle</Pill>
              <Pill state="running">Running</Pill>
              <Pill state="completed">Completed</Pill>
              <Pill state="errored">Errored</Pill>
            </Spec>
            <Spec label="Badges">
              <Badge kind="source-id">Q-12</Badge>
              <Badge kind="reference-kind" tone="input">input</Badge>
              <Badge kind="reference-kind" tone="rubric">rubric</Badge>
              <Badge kind="rubric-category">Preservation</Badge>
            </Spec>
            <Spec label="Empty states">
              <EmptyState>Logs will appear here when you start a workflow.</EmptyState>
              <EmptyState variant="modal" error>
                Could not load this file.
              </EmptyState>
            </Spec>
          </Stack>
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="Tabs" subtitle="Viewer tab bar — the active tab underlines." />
        <CardBody>
          <Tabs
            value={tab}
            onChange={setTab}
            tabs={[
              { id: 'config', label: 'Config' },
              { id: 'source', label: 'Source Qs' },
              { id: 'generated', label: 'Generated Qs' },
              { id: 'evaluation', label: 'Evaluation' },
            ]}
          />
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="Log list" subtitle="LogItem rows with status-colored left edges." />
        <CardBody>
          <div className="log-frame" style={{ maxHeight: 180, overflow: 'auto' }}>
            <LogList entries={SAMPLE_LOGS} />
          </div>
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="Tables" subtitle="Eval / file-viewer tables and the key/value table." />
        <CardBody>
          <Stack gap="var(--spacing-lg)">
            <div className="file-viewer-table-shell">
              <DataTable variant="eval">
                <thead>
                  <tr>
                    <th>Source</th>
                    <th>Status</th>
                    <th>Score</th>
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
                    <td>
                      <ScoreChip tone="pass">1</ScoreChip>
                    </td>
                  </tr>
                </tbody>
              </DataTable>
            </div>
            <KeyValueTable
              rows={[
                { key: 'theme', value: 'Physics' },
                { key: 'difficulty', value: 'medium' },
                { key: 'questions per source', value: '2' },
              ]}
            />
          </Stack>
        </CardBody>
      </Card>
    </Stack>
  );
}
