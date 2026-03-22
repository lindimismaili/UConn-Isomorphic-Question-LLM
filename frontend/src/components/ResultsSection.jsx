import { useEffect, useRef, useState } from 'react';
import {
  IconCheckCircle,
  IconFolder,
  IconFile,
  IconDownload,
  IconMonitor,
} from './Icons.jsx';
import PdfPreviewModal from './modals/PdfPreviewModal.jsx';

function formatOutputFolderPath(folder) {
  if (!folder) return 'outputs/';
  return folder.startsWith('outputs/') ? folder : `outputs/${folder}`;
}

function formatElapsed(seconds) {
  if (typeof seconds !== 'number' || Number.isNaN(seconds)) return '';
  return `${seconds.toFixed(2)}s`;
}

function collectResultFiles(result) {
  const mergedFiles = [];
  const seen = new Set();

  const addFile = (file) => {
    const fileName = typeof file === 'string' ? file : file?.name || file;
    if (!fileName || seen.has(fileName)) return;
    seen.add(fileName);
    mergedFiles.push(typeof file === 'string' ? { name: fileName } : { ...file, name: fileName });
  };

  (result.files || []).forEach(addFile);

  const workflowResult = result.workflowResult || {};
  [
    workflowResult.latex_file,
    workflowResult.pdf_file,
  ].forEach(addFile);

  return mergedFiles;
}

function isPdfFile(fileName) {
  return typeof fileName === 'string' && fileName.toLowerCase().endsWith('.pdf');
}

export default function ResultsSection({ result }) {
  const sectionRef = useRef(null);
  const [previewFile, setPreviewFile] = useState('');

  useEffect(() => {
    if (result && sectionRef.current) {
      sectionRef.current.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  }, [result]);

  if (!result) return null;

  const files = collectResultFiles(result);
  const folderPath = formatOutputFolderPath(result.outputFolder);
  const fileCount = files.length;
  const elapsedText = formatElapsed(result.elapsedTimeSeconds);

  const getFileUrl = (fileName, inline = false) => {
    if (!result.workflowId) return '';
    const encodedFileName = encodeURIComponent(fileName);
    const inlineQuery = inline ? '?inline=1' : '';
    return `/api/download/${result.workflowId}/${encodedFileName}${inlineQuery}`;
  };

  const handleDownload = async (fileName) => {
    try {
      const url = getFileUrl(fileName);
      if (!url) {
        throw new Error('Workflow download link is unavailable.');
      }
      const link = document.createElement('a');
      link.href = url;
      link.download = fileName;
      link.click();
    } catch (err) {
      alert(`Download failed: ${err.message}`);
    }
  };

  const handlePreviewOpen = (fileName) => {
    setPreviewFile(fileName);
  };

  const handlePreviewClose = () => {
    setPreviewFile('');
  };

  const previewUrl = previewFile ? getFileUrl(previewFile, true) : '';

  return (
    <>
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
              {files.map((file) => {
                const fileName = file.name;
                const pdfFile = isPdfFile(fileName);
                return (
                  <div key={fileName} className="download-item">
                    <div className="download-item-info">
                      <IconFile />
                      <span className="download-item-name">{fileName}</span>
                    </div>
                    <div className="download-item-actions">
                      <button
                        className={`btn btn-secondary btn-download${pdfFile ? ' btn-download-split' : ''}`}
                        onClick={() => handleDownload(fileName)}
                        type="button"
                      >
                        <IconDownload className="btn-icon" />
                        Download
                      </button>
                      {pdfFile && (
                        <button
                          className="btn btn-primary btn-preview"
                          onClick={() => handlePreviewOpen(fileName)}
                          type="button"
                        >
                          <IconMonitor className="btn-icon" />
                          VIEW NOW
                        </button>
                      )}
                    </div>
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

      <PdfPreviewModal
        isOpen={Boolean(previewFile)}
        onClose={handlePreviewClose}
        fileName={previewFile}
        pdfUrl={previewUrl}
        onDownload={handleDownload}
      />
    </>
  );
}
