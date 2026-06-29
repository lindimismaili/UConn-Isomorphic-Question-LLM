import Modal from './Modal.jsx';
import useFileViewer from './useFileViewer.jsx';
import { IconDownload, IconMonitor } from '../Icons.jsx';
import { baseName, collectResultFiles } from '../../utils/files.js';
import { Button } from '../ui/index.js';

/**
 * Details for the final runtime step that publishes completed artifacts: the
 * inputs the run started with and every output file with view/download.
 *
 * @param {object} props
 * @param {boolean} props.isOpen
 * @param {Function} props.onClose
 * @param {object | null} props.result - Completed workflow result payload.
 * @param {{prompt: string, files: string[], startAgent: string} | null} props.runInputs
 */
export default function OutputModal({ isOpen, onClose, result, runInputs }) {
  const { openFile, downloadFile, viewerModals } = useFileViewer(result?.workflowId);

  const outputFiles = result ? collectResultFiles(result) : [];
  const inputFiles = runInputs?.files || [];
  const promptText = runInputs?.prompt || '';

  return (
    <>
      <Modal
        isOpen={isOpen}
        onClose={onClose}
        title="Ending Runtime"
        large
        footer={
          <Button variant="primary" onClick={onClose}>Close</Button>
        }
      >
        <div className="setup-modal-body modal-body-stack">
          <p>
            <strong>Ending Runtime</strong> is not an agent. It is the final
            workflow step after <strong>Evaluator</strong>, when the system
            writes the completed output artifacts and makes them available for
            download.
          </p>

          <h4>Input Files</h4>
          {inputFiles.length > 0 ? (
            <ul className="viewer-source-files">
              {inputFiles.map((name) => (
                <li className="viewer-source-file" key={name}>{name}</li>
              ))}
            </ul>
          ) : (
            <p className="runtime-empty-note">
              {runInputs ? 'No files were attached to this run.' : 'Run a workflow to see its inputs here.'}
            </p>
          )}

          {promptText && (
            <>
              <h4>Input Text</h4>
              <pre className="detail-text-box runtime-input-text">{promptText}</pre>
            </>
          )}

          <h4>Output Files</h4>
          {outputFiles.length > 0 ? (
            <div className="runtime-output-list">
              {outputFiles.map((file) => (
                <div className="download-item" key={file.name}>
                  <span className="download-item-name" title={file.name}>
                    {baseName(file.name)}
                  </span>
                  <div className="download-item-actions">
                    <Button
                      variant="primary"
                      className="btn-preview"
                      onClick={() => openFile(file.name)}
                      icon={<IconMonitor className="btn-icon" />}
                    >
                      View
                    </Button>
                    <Button
                      variant="secondary"
                      className="btn-download"
                      onClick={() => downloadFile(file.name)}
                      icon={<IconDownload className="btn-icon" />}
                    >
                      Download
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <p className="runtime-empty-note">
              Outputs appear here once the workflow completes.
            </p>
          )}
        </div>
      </Modal>

      {viewerModals}
    </>
  );
}
