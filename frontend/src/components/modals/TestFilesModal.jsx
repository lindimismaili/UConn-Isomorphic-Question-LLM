import { useEffect, useMemo, useState } from 'react';
import Modal from './Modal.jsx';
import { IconFile } from '../Icons.jsx';

const MAX_FILES = 5;

function generateId() {
  return Math.random().toString(36).slice(2, 11);
}

export default function TestFilesModal({ isOpen, onClose, files, dispatch, isRunning }) {
  const [availableFiles, setAvailableFiles] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const selectedTestFileByPath = useMemo(() => {
    const map = new Map();
    for (const file of files) {
      if (file?.source === 'test' && typeof file.testFile === 'string') {
        map.set(file.testFile, file);
      }
    }
    return map;
  }, [files]);

  useEffect(() => {
    if (!isOpen) return;
    setLoading(true);
    setError('');

    fetch('/api/evaluation/files')
      .then((res) => {
        if (!res.ok) throw new Error(`Failed to load test files (${res.status})`);
        return res.json();
      })
      .then((payload) => {
        const list = Array.isArray(payload.files) ? payload.files : [];
        setAvailableFiles(list);
      })
      .catch((err) => {
        setAvailableFiles([]);
        setError(err.message || 'Failed to load test files');
      })
      .finally(() => setLoading(false));
  }, [isOpen]);

  const toggleTestFile = (item) => {
    const existing = selectedTestFileByPath.get(item.relativePath);
    if (existing) {
      dispatch({ type: 'REMOVE_FILE', fileId: existing.id });
      return;
    }

    if (files.length >= MAX_FILES) {
      alert(`Maximum ${MAX_FILES} files allowed.`);
      return;
    }

    dispatch({
      type: 'ADD_FILES',
      files: [
        {
          id: generateId(),
          name: item.name,
          size: item.size,
          type: '',
          file: null,
          source: 'test',
          testFile: item.relativePath,
        },
      ],
    });
  };

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title="Test Files"
      footer={
        <button className="btn btn-secondary" onClick={onClose}>Close</button>
      }
    >
      <div className="files-list">
        {loading && (
          <p style={{ color: 'var(--color-text-muted)', textAlign: 'center' }}>
            Loading test files...
          </p>
        )}

        {!loading && error && (
          <p style={{ color: 'var(--color-error)', textAlign: 'center' }}>
            {error}
          </p>
        )}

        {!loading && !error && availableFiles.length === 0 && (
          <p style={{ color: 'var(--color-text-muted)', textAlign: 'center' }}>
            No test files found in data/evaluation
          </p>
        )}

        {!loading && !error && availableFiles.map((item) => {
          const selected = selectedTestFileByPath.has(item.relativePath);
          return (
            <div key={item.relativePath} className="file-item">
              <div className="file-item-info">
                <IconFile />
                <div>
                  <div className="file-item-name">{item.name}</div>
                  <div className="file-item-path">{`data/evaluation/${item.relativePath}`}</div>
                </div>
              </div>
              <button
                className={`btn btn-sm ${selected ? 'btn-remove' : 'btn-secondary'}`}
                onClick={() => toggleTestFile(item)}
                disabled={isRunning}
              >
                {selected ? 'Unselect' : 'Select'}
              </button>
            </div>
          );
        })}
      </div>
    </Modal>
  );
}

