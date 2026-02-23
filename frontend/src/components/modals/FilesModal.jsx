import Modal from './Modal.jsx';
import { IconFile } from '../Icons.jsx';

export default function FilesModal({ isOpen, onClose, files, onRemove }) {
  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title="Uploaded Files"
      footer={
        <button className="btn btn-secondary" onClick={onClose}>Close</button>
      }
    >
      <div className="files-list">
        {files.length === 0 ? (
          <p style={{ color: 'var(--color-text-muted)', textAlign: 'center' }}>
            No files uploaded
          </p>
        ) : (
          files.map((file) => (
            <div key={file.id} className="file-item">
              <div className="file-item-info">
                <IconFile />
                <span className="file-item-name">{file.name}</span>
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
    </Modal>
  );
}
