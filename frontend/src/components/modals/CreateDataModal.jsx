import Modal from './Modal.jsx';
import { IconFile, IconFolder, IconZap } from '../Icons.jsx';

function formatOutputFolder(folder) {
  if (!folder) return 'outputs/';
  return folder.startsWith('outputs/') ? folder : `outputs/${folder}`;
}

function getStatusLabel(status) {
  switch (status) {
    case 'queued':
      return 'Queued';
    case 'running':
      return 'Running';
    case 'completed':
      return 'Done';
    case 'errored':
      return 'Error';
    case 'starting':
      return 'Starting';
    default:
      return 'Idle';
  }
}

export default function CreateDataModal({ isOpen, onClose, createData, onRun }) {
  const progressText = createData.totalFiles > 0
    ? `${createData.doneFiles}/${createData.totalFiles} files done`
    : 'Preparing evaluation files';
  const outputFolder = formatOutputFolder(createData.outputFolder);

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title="Create Data"
      large
      footer={
        <div style={{ display: 'flex', gap: '0.5rem' }}>
          {createData.status === 'idle' && (
            <button className="btn btn-primary" onClick={onRun}>Run</button>
          )}
          <button className="btn btn-secondary" onClick={onClose}>Close</button>
        </div>
      }
    >
      <div className="create-data-modal">
        <div className="create-data-summary">
          <div className="create-data-summary-title">
            <IconZap />
            <div>
              <div className="create-data-heading">Batch evaluation run</div>
              <div className="create-data-subheading">
                Runs the full workflow on every file in <code>data/evaluation</code>.
              </div>
            </div>
          </div>
          <div className="create-data-progress">
            <span className={`create-data-pill is-${createData.status}`}>{getStatusLabel(createData.status)}</span>
            <strong>{progressText}</strong>
            <span>
              {createData.completedFiles} completed
              {createData.failedFiles > 0 ? `, ${createData.failedFiles} failed` : ''}
            </span>
          </div>
        </div>

        {createData.error && (
          <div className="create-data-alert">
            {createData.error}
          </div>
        )}

        <div className="output-folder-note">
          <IconFolder />
          <span>
            Batch folder: <code>{createData.status === 'completed' ? outputFolder : 'outputs/'}</code>
          </span>
        </div>

        <div className="create-data-list">
          {createData.entries.map((entry) => (
            <div
              key={`${entry.relativePath}-${entry.folderName}`}
              className={`create-data-item is-${entry.status}`}
            >
              <div className="create-data-item-main">
                <div className="create-data-item-header">
                  <div className="create-data-item-title">
                    <IconFile />
                    <span>{entry.name}</span>
                  </div>
                  <div className="create-data-item-tracking">
                    <span className="create-data-step-chip">
                      {`${entry.stepLabel || '1/6'} ${entry.currentAgentName || 'Normalizer'}`}
                    </span>
                    <span className={`create-data-pill is-${entry.status}`}>{getStatusLabel(entry.status)}</span>
                  </div>
                </div>
                <div className="create-data-item-meta">
                  <span>{`data/evaluation/${entry.relativePath}`}</span>
                  <span>{`folder: ${entry.folderName}`}</span>
                </div>
                <div className="create-data-item-output">
                  {entry.status === 'completed' && entry.outputFolder ? `outputs/${entry.outputFolder}` : 'outputs/'}
                </div>
                {entry.error ? (
                  <div className="create-data-item-error">{entry.error}</div>
                ) : null}
              </div>
            </div>
          ))}
        </div>
      </div>
    </Modal>
  );
}
