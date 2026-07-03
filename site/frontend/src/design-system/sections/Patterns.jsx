import { useState } from 'react';
import AgentCard from '../../components/AgentCard.jsx';
import {
  Avatar,
  Banner,
  Button,
  Card,
  CardBody,
  CardHeader,
  ChatMessage,
  ChatTranscript,
  Composer,
  Footer,
  LogList,
  PageHeader,
  Stepper,
  TypingIndicator,
} from '../../components/ui/index.js';
import { HEADER_MOTIF, UConnMark } from '../../sections/SiteChrome.jsx';
import { IconSend, IconUpload } from '../../components/Icons.jsx';
import { Row, Spec, Stack } from '../Specimen.jsx';
import { MANIFEST } from '../manifest.jsx';

/** Reuse the Modal entry's live Specimen (manifest.jsx) instead of duplicating an opener. */
const ModalSpecimen = MANIFEST.find((entry) => entry.name === 'Modal').Specimen;

/** A 10-node pipeline fixture (with guardrails) for the chat-first pattern. */
const CHAT_STEPPER_NODES = [
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
  const [composer, setComposer] = useState('Generate 3 isomorphic questions per source, harder.');
  return (
    <Stack gap="var(--section-gap)">
      <h2 id="patterns" style={{ scrollMarginTop: '80px' }}>
        Patterns
      </h2>

      <Card>
        <CardHeader title="Chat-first conversation" subtitle="The primary surface: a Stepper process-rail (a status word cycles above the thinking node) above a ChatTranscript, with the Composer pinned below. The reducer's agents/logs/result project into messages." />
        <CardBody>
          <div className="chat-shell">
            <div className="chat-rail">
              <Stepper
                nodes={CHAT_STEPPER_NODES}
                onNodeClick={() => {}}
                activeKey="generator"
              />
            </div>
            <ChatTranscript count={3} style={{ maxHeight: 260 }}>
              <ChatMessage variant="user" avatar={<Avatar role="user" label="You" />} author="You" timestamp="12:00">
                Generate 3 isomorphic questions per source, harder.
              </ChatMessage>
              <ChatMessage variant="agent" tone="success" avatar={<Avatar role="agent" label="Extracter" />} author="Extracter" timestamp="12:01">
                Extracted 4 source questions.
              </ChatMessage>
              <ChatMessage variant="agent" tone="thinking" avatar={<Avatar role="agent" label="Generator" />} author="Generator" timestamp="12:02">
                <TypingIndicator label="Generator is working" />
              </ChatMessage>
            </ChatTranscript>
            <Composer
              value={composer}
              onChange={setComposer}
              onSend={() => {}}
              onAttach={() => {}}
              attachLabel="Attach"
              placeholder="Describe the questions…"
              themeSelect={{ value: '', onChange: () => {}, placeholder: 'Select a theme…', options: [{ value: 'physics', label: 'Physics' }] }}
            />
          </div>
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="Blocked run (guardrail)" subtitle="A tripped guardrail ends the run in the terminal `blocked` status: a blocked assistant message + the guardrail node styled `blocked` in the rail." />
        <CardBody>
          <Stack gap="var(--spacing-md)">
            <Stepper
              nodes={[
                { key: 'setup', label: 'Setup', status: 'completed', kind: 'runtime' },
                { key: 'input_guardrail', label: 'Input Guardrail', status: 'blocked', kind: 'guardrail' },
                { key: 'normalizer', label: 'Normalizer', status: 'cancelled', kind: 'agent' },
              ]}
              onNodeClick={() => {}}
            />
            <ChatMessage variant="assistant" tone="blocked" avatar={<Avatar role="guardrail" label="Guardrail" />} author="Guardrail" timestamp="12:00">
              Request blocked by the input guardrail — the prompt asked for content outside the physics scope (category: policy).
            </ChatMessage>
          </Stack>
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="App shell — masthead / banner / footer" subtitle="The real AppShell primitives. App.jsx and this showcase compose the same chrome from ui/Shell.jsx (no duplicated raw markup)." />
        <CardBody>
          <Stack gap="var(--spacing-lg)">
            <Spec label="Banner (outage notice)">
              <Banner detail="degraded region us-east">OpenAI servers are down. This site is currently inactive.</Banner>
            </Spec>
            <Spec label="PageHeader / Hero (physics motif behind navy masthead)">
              <div style={{ borderRadius: 'var(--radius-lg)', overflow: 'hidden', width: '100%' }}>
                <PageHeader
                  motif={HEADER_MOTIF}
                  style={{ position: 'static' }}
                  brand={
                    <div className="logo">
                      <span className="logo-icon logo-icon-ai" aria-hidden="true" />
                      <h1>Isomorphic Question Generator</h1>
                    </div>
                  }
                  aside={<UConnMark />}
                />
              </div>
            </Spec>
            <Spec label="Footer">
              <div style={{ borderRadius: 'var(--radius-lg)', overflow: 'hidden', width: '100%' }}>
                <Footer>
                  <p className="footer-primary">UCONN Physics Research - Dr. Xian Wu (2025 - 2026)</p>
                  <p className="footer-secondary">Version 1.0</p>
                </Footer>
              </div>
            </Spec>
          </Stack>
        </CardBody>
      </Card>

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
        <CardHeader title="Modal shell" subtitle="A real, live Modal.jsx dialog (opened on click) — the same shell every modal in the app flows through; §8 owns stacking." />
        <CardBody>
          <Spec label="Structure">
            <ModalSpecimen />
          </Spec>
        </CardBody>
      </Card>
    </Stack>
  );
}
