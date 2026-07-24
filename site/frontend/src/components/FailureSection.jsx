import { useEffect, useRef } from 'react';
import { IconXCircle } from './Icons.jsx';
import { Card, CardBody, CardHeader } from './ui/index.js';

/**
 * Expands escaped whitespace and formats JSON-like error payloads for display.
 *
 * @param {string} text - Raw error text or serialized payload.
 * @returns {string}
 */
function formatText(text) {
  if (!text) return '';
  let processed = text.replace(/\\n/g, '\n').replace(/\\t/g, '    ').replace(/\\r/g, '');
  try { return JSON.stringify(JSON.parse(processed), null, 2); }
  catch { return processed; }
}

/**
 * Renders the terminal failure summary.
 *
 * @param {{error: string}} props
 */
export default function FailureSection({ error }) {
  const sectionRef = useRef(null);

  useEffect(() => {
    if (error && sectionRef.current) {
      sectionRef.current.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  }, [error]);

  if (!error) return null;

  return (
    <Card variant="failed" ref={sectionRef}>
      <CardHeader>
        <div className="completed-header failed-header">
          <span className="completed-icon failed-icon"><IconXCircle /></span>
          <h2>Failure!</h2>
        </div>
        <p className="section-subtitle">
          The workflow stopped before the final agent finished. Review the output
          below and try again.
        </p>
      </CardHeader>
      <CardBody>
        <div className="output-section">
          <h4>Output Summary</h4>
          <div className="output-text">{formatText(error)}</div>
        </div>
      </CardBody>
    </Card>
  );
}
