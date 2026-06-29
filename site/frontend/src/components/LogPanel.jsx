import { useEffect, useRef } from 'react';
import { Card, CardBody, CardHeader, LogList } from './ui/index.js';

/**
 * Live workflow log. Renders standalone as a card, or `embedded` as just the
 * scrolling frame (used inside the developer-tools panel).
 *
 * @param {object} props
 * @param {Array<{id: string|number, level: string, time?: string, message: import('react').ReactNode}>} props.logs
 * @param {boolean} [props.embedded]
 */
export default function LogPanel({ logs, embedded = false }) {
  const frameRef = useRef(null);

  useEffect(() => {
    if (frameRef.current) {
      frameRef.current.scrollTop = frameRef.current.scrollHeight;
    }
  }, [logs]);

  const frame = (
    <div className="log-frame" role="log" aria-live="polite" ref={frameRef}>
      <LogList entries={logs} />
    </div>
  );

  if (embedded) {
    return frame;
  }

  return (
    <Card variant="log-panel" className="pipeline-panel log-panel">
      <CardHeader title="Live Log" subtitle="Workflow events appear here as the system progresses." />
      <CardBody>{frame}</CardBody>
    </Card>
  );
}
