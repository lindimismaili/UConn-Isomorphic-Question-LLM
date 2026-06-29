import { useEffect, useMemo, useState } from 'react';
import Modal from './Modal.jsx';
import { IconFile } from '../Icons.jsx';
import { generateId } from '../../utils/ids';
import { Button, EmptyState } from '../ui/index.js';

/** Mirrors the main upload limit for test-file selections. */
const MAX_FILES = 5;

/**
 * Lets the user pick evaluation fixtures from `/api/evaluation/files`.
 *
 * @param {object} props
 */
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

    Promise.resolve()
      .then(() => {
        setLoading(true);
        setError('');
        return fetch('/api/evaluation/files');
      })
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
        <Button variant="primary" onClick={onClose}>Close</Button>
      }
    >
      <div className="modal-body-stack">
        <div className="files-list">
        {loading && (
          <EmptyState variant="modal">Loading test files...</EmptyState>
        )}

        {!loading && error && (
          <EmptyState variant="modal" error>{error}</EmptyState>
        )}

        {!loading && !error && availableFiles.length === 0 && (
          <EmptyState variant="modal">No test files found in `data/evaluation`.</EmptyState>
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
              <Button
                variant={selected ? null : 'secondary'}
                className={`test-file-toggle-btn${selected ? ' btn-remove' : ''}`}
                onClick={() => toggleTestFile(item)}
                disabled={isRunning}
              >
                {selected ? 'Unselect' : 'Select'}
              </Button>
            </div>
          );
        })}
        </div>
      </div>
    </Modal>
  );
}
