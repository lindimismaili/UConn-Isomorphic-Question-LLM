import { useEffect, useRef } from 'react';

/**
 * Streams workflow events into the persistent log panel.
 *
 * @param {{logs: Array<{id: string, time: string, message: string, level: string}>}} props
 */
export default function LogPanel({ logs }) {
  const frameRef = useRef(null);

  // Keep the newest workflow event visible without user interaction.
  useEffect(() => {
    if (frameRef.current) {
      frameRef.current.scrollTop = frameRef.current.scrollHeight;
    }
  }, [logs]);

  return (
    <section className="card pipeline-panel log-panel log-panel-card">
      <div className="card-header">
        <h2>Live Log</h2>
        <p className="subtitle">Workflow events appear here as the system progresses.</p>
      </div>
      <div className="card-body">
        <div className="log-frame" role="log" aria-live="polite" ref={frameRef}>
          {logs.length > 0 ? (
            <div className="log-list">
              {logs.map((entry) => (
                <div key={entry.id} className={`log-item log-${entry.level}`}>
                  <span className="log-time">[{entry.time}]</span>
                  <span>{entry.message}</span>
                </div>
              ))}
            </div>
          ) : (
            <div className="log-empty-state">
              Logs will appear here when you start a workflow. The panel stays visible so the full agent lifecycle is always in view.
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
