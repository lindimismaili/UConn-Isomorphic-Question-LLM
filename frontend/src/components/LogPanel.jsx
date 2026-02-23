import { useEffect, useRef } from 'react';

export default function LogPanel({ logs }) {
  const frameRef = useRef(null);

  // Auto-scroll to bottom on new logs
  useEffect(() => {
    if (frameRef.current) {
      frameRef.current.scrollTop = frameRef.current.scrollHeight;
    }
  }, [logs]);

  return (
    <div className="pipeline-panel log-panel">
      <h3 className="section-title">Log</h3>
      <p className="section-subtitle">Live status updates</p>
      <div className="log-frame" role="log" aria-live="polite" ref={frameRef}>
        <div className="log-list">
          {logs.map((entry) => (
            <div key={entry.id} className={`log-item log-${entry.level}`}>
              <span className="log-time">[{entry.time}]</span>
              <span>{entry.message}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
