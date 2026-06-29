import Modal from './Modal.jsx';
import { IconFile } from '../Icons.jsx';

/**
 * Lists uploaded files and exposes per-file removal actions.
 *
 * @param {object} props
 */
export default function FilesModal({ isOpen, onClose, files, onRemove }) {
  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title="Uploaded Files"
      footer={
        <button className="btn btn-primary" onClick={onClose}>Close</button>
      }
    >
      <div className="modal-body-stack">
        <div className="files-list">
        {files.length === 0 ? (
          <div className="modal-empty-state">No files uploaded.</div>
        ) : (
          files.map((file) => (
            <div key={file.id} className="file-item">
              <div className="file-item-info">
                <IconFile />
                <span className="file-item-name">
                  {file.name}
                  {file.source === 'test' ? ' (Test File)' : ''}
                </span>
              </div>
              <button
                className="btn btn-remove btn-sm"
                onClick={() => onRemove(file.id)}
              >
                Remove
              </button>
            </div>
          ))
        )}
        </div>
      </div>
    </Modal>
  );
}
