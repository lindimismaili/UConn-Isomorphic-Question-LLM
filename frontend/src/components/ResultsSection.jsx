import { useEffect, useRef } from 'react';
import { IconCheckCircle, IconFolder, IconFile, IconDownload } from './Icons.jsx';

function formatOutputFolderPath(folder) {
  if (!folder) return 'outputs/';
  return folder.startsWith('outputs/') ? folder : `outputs/${folder}`;
}

function formatElapsed(seconds) {
  if (typeof seconds !== 'number' || Number.isNaN(seconds)) return '';
  return `${seconds.toFixed(2)}s`;
}

export default function ResultsSection({ result }) {
  const sectionRef = useRef(null);

  useEffect(() => {
    if (result && sectionRef.current) {
      sectionRef.current.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  }, [result]);

  if (!result) return null;

  const folderPath = formatOutputFolderPath(result.outputFolder);
  const fileCount = result.files?.length || 0;
  const elapsedText = formatElapsed(result.elapsedTimeSeconds);

  const handleDownload = async (fileName) => {
    try {
      // Use workflowId from result if available, otherwise try a direct URL
      const url = result.workflowId
        ? `/api/download/${result.workflowId}/${fileName}`
        : `/api/download/${fileName}`;
      const link = document.createElement('a');
      link.href = url;
      link.download = fileName;
      link.click();
    } catch (err) {
      alert(`Download failed: ${err.message}`);
    }
  };

  return (
    <section className="card completed-card" ref={sectionRef}>
      <div className="card-header">
        <div className="completed-header">
          <div className="completed-header-left">
            <IconCheckCircle className="completed-icon" />
            <h2>Completed!</h2>
          </div>
          <div className="completed-elapsed-time">
            {elapsedText ? `Elapsed Time: ${elapsedText}` : ''}
          </div>
        </div>
      </div>
      <div className="card-body">
        <div className="downloads-section">
          <div className="downloads-header">
            <h4>
              Download Files -{' '}
              <span className="download-count">
                {fileCount} file{fileCount !== 1 ? 's' : ''}
              </span>
            </h4>
          </div>
          <div className="downloads-list">
            {result.files?.map((file) => {
              const fileName = typeof file === 'string' ? file : file.name || file;
              return (
                <div key={fileName} className="download-item">
                  <div className="download-item-info">
                    <IconFile />
                    <span className="download-item-name">{fileName}</span>
                  </div>
                  <button
                    className="btn btn-secondary btn-download"
                    onClick={() => handleDownload(fileName)}
                  >
                    <IconDownload className="btn-icon" />
                    Download
                  </button>
                </div>
              );
            })}
          </div>
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
