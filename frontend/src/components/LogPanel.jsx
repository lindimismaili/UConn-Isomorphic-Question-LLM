import { useEffect, useRef } from 'react';

function LogEntries({ logs }) {
  return logs.length > 0 ? (
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
      Logs will appear here when you start a workflow.
    </div>
  );
}

export default function LogPanel({ logs, embedded = false }) {
  const frameRef = useRef(null);

  useEffect(() => {
    if (frameRef.current) {
      frameRef.current.scrollTop = frameRef.current.scrollHeight;
    }
  }, [logs]);

  if (embedded) {
    return (
      <div className="log-frame" role="log" aria-live="polite" ref={frameRef}>
        <LogEntries logs={logs} />
      </div>
    );
  }

  return (
    <section className="card pipeline-panel log-panel log-panel-card">
      <div className="card-header">
        <h2>Live Log</h2>
        <p className="subtitle">Workflow events appear here as the system progresses.</p>
      </div>
      <div className="card-body">
        <div className="log-frame" role="log" aria-live="polite" ref={frameRef}>
          <LogEntries logs={logs} />
        </div>
      </div>
    </section>
  );
}
