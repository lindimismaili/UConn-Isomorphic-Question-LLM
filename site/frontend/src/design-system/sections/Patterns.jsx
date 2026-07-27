import { useState } from 'react';
import {
  Avatar,
  Banner,
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
import { Spec, Stack } from '../Specimen.jsx';
import { MANIFEST, STEPPER_NODES } from '../manifest.jsx';

/** Reuse the Modal entry's live Specimen (manifest.jsx) instead of duplicating an opener. */
const ModalSpecimen = MANIFEST.find((entry) => entry.name === 'Modal').Specimen;

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
                nodes={STEPPER_NODES}
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
