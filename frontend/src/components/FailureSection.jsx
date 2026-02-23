import { useEffect, useRef } from 'react';
import { IconXCircle, IconFolder } from './Icons.jsx';

function formatText(text) {
  if (!text) return '';
  let processed = text.replace(/\\n/g, '\n').replace(/\\t/g, '    ').replace(/\\r/g, '');
  try { return JSON.stringify(JSON.parse(processed), null, 2); }
  catch { return processed; }
}

function formatOutputFolderPath(folder) {
  if (!folder) return 'outputs/';
  return folder.startsWith('outputs/') ? folder : `outputs/${folder}`;
}

export default function FailureSection({ error, outputFolder }) {
  const sectionRef = useRef(null);

  useEffect(() => {
    if (error && sectionRef.current) {
      sectionRef.current.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  }, [error]);

  if (!error) return null;

  const folderPath = formatOutputFolderPath(outputFolder);

  return (
    <section className="card failed-card" ref={sectionRef}>
      <div className="card-header">
        <div className="completed-header failed-header">
          <span className="completed-icon failed-icon"><IconXCircle /></span>
          <h2>Failure!</h2>
        </div>
        <p className="section-subtitle">
          The workflow stopped before the final agent finished. Review the output
          below and try again.
        </p>
      </div>
      <div className="card-body">
        <div className="output-section">
          <h4>Output Summary</h4>
          <div className="output-text">{formatText(error)}</div>
          <div className="output-folder-note">
            <IconFolder />
            <span>
              Files saved to: <code>{folderPath}</code>
            </span>
          </div>
        </div>
      </div>
    </section>
  );
}
