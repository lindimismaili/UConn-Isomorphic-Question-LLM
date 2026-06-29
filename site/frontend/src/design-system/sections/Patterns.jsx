import AgentCard from '../../components/AgentCard.jsx';
import { Button, Card, CardBody, CardHeader, LogList } from '../../components/ui/index.js';
import { IconSend, IconUpload } from '../../components/Icons.jsx';
import { Row, Spec, Stack } from '../Specimen.jsx';

const AGENT_FIXTURES = [
  {
    status: 'completed',
    name: 'Extracter',
    description: 'Reads uploaded files and extracts source questions.',
    kind: 'agent',
    model: 'gpt-5.5',
    reasoningLevel: 'medium',
    executionTime: 8.42,
  },
  {
    status: 'thinking',
    name: 'Generator',
    description: 'Creates isomorphic questions from the verified set.',
    kind: 'agent',
    model: 'gpt-5.5',
    reasoningLevel: 'medium',
  },
  {
    status: 'errored',
    name: 'Latexifier',
    description: 'Produces LaTeX and compiles the PDF.',
    kind: 'agent',
    model: 'gpt-5.5',
    reasoningLevel: 'medium',
  },
];

const SAMPLE_LOGS = [
  { id: 1, level: 'info', time: '12:00:01', message: 'Workflow started.' },
  { id: 2, level: 'success', time: '12:00:09', message: 'Extracter completed.' },
];

/** Patterns: recurring compositions assembled from the primitives. */
export default function Patterns() {
  return (
    <Stack gap="var(--section-gap)">
      <h2 id="patterns" style={{ scrollMarginTop: '80px' }}>
        Patterns
      </h2>

      <Card>
        <CardHeader title="Prompt actions row" subtitle="Theme select + Upload + Send share one 56px tier and never stack." />
        <CardBody>
          <div className="prompt-actions-row" style={{ display: 'flex', gap: 'var(--spacing-sm)', flexWrap: 'wrap' }}>
            <Button variant="secondary" icon={<IconUpload className="btn-icon" />}>
              <span className="btn-label">Upload Files</span>
            </Button>
            <Button variant="primary" icon={<IconSend className="btn-icon" />}>
              <span>Send</span>
            </Button>
          </div>
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="Agent pipeline node" subtitle="The clickable .agent-card — the one card that lifts on hover. Status drives border/glow/icon." />
        <CardBody>
          <Row gap="var(--spacing-lg)" style={{ paddingTop: 8 }}>
            {AGENT_FIXTURES.map((agent, i) => (
              <AgentCard key={i} agentKey={`demo-${i}`} agent={agent} onClick={() => {}} />
            ))}
          </Row>
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="Log panel" subtitle="LogList inside a .log-frame." />
        <CardBody>
          <div className="log-frame" style={{ maxHeight: 140, overflow: 'auto' }}>
            <LogList entries={SAMPLE_LOGS} />
          </div>
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="Modal shell" subtitle="Static preview of the Modal.jsx structure (live modals always flow through the shell; §8 owns stacking)." />
        <CardBody>
          <Spec label="Structure">
            <div
              className="modal-content"
              style={{ position: 'static', maxWidth: 420, width: '100%', transform: 'none', opacity: 1 }}
            >
              <div className="modal-header">
                <h3>Modal title</h3>
                <button type="button" className="btn-icon-only" aria-label="Close">
                  ×
                </button>
              </div>
              <div className="modal-body">
                <p className="subtitle">Body scrolls; footer actions are .btn.</p>
              </div>
              <div className="modal-footer">
                <Button variant="secondary">Cancel</Button>
                <Button variant="primary">Confirm</Button>
              </div>
            </div>
          </Spec>
        </CardBody>
      </Card>
    </Stack>
  );
}
