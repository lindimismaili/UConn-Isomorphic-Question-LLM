import { useCallback } from 'react';

const MAX_FILES = 5;

function generateId() {
  return Math.random().toString(36).substring(2, 11);
}

/**
 * Returns handlers for file selection, removal, and upload button state.
 * All state mutations go through the dispatch from App.jsx.
 */
export function useFileUpload(files, dispatch) {
  const handleFileSelect = useCallback((event) => {
    const selected = Array.from(event.target.files);
    const available = MAX_FILES - files.length;

    if (available <= 0) {
      dispatch({ type: 'UPLOAD_WARNING' });
      event.target.value = '';
      return;
    }

    const allowed = selected.slice(0, available);
    const newFiles = allowed
      .filter((f) => !files.some((existing) => existing.name === f.name && existing.size === f.size))
      .map((f) => ({ id: generateId(), name: f.name, size: f.size, type: f.type, file: f }));

    if (selected.length > allowed.length) {
      dispatch({ type: 'UPLOAD_WARNING' });
    }

    if (newFiles.length > 0) {
      dispatch({ type: 'ADD_FILES', files: newFiles });
    }

    event.target.value = '';
  }, [files, dispatch]);

  const removeFile = useCallback((fileId) => {
    dispatch({ type: 'REMOVE_FILE', fileId });
  }, [dispatch]);

  const limitReached = files.length >= MAX_FILES;

  return { handleFileSelect, removeFile, limitReached };
}
