import Modal from './Modal.jsx';
import { IconFile } from '../Icons.jsx';
import { Button, EmptyState } from '../ui/index.js';

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
        <Button variant="primary" onClick={onClose}>Close</Button>
      }
    >
      <div className="modal-body-stack">
        <div className="files-list">
        {files.length === 0 ? (
          <EmptyState variant="modal">No files uploaded.</EmptyState>
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
              <Button
                variant={null}
                size="sm"
                className="btn-remove"
                onClick={() => onRemove(file.id)}
              >
                Remove
              </Button>
            </div>
          ))
        )}
        </div>
      </div>
    </Modal>
  );
}
