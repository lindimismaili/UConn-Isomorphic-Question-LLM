import { useCallback } from 'react';

import { generateId } from '../utils/ids';

/** Maximum number of source files the UI accepts at once. */
const MAX_FILES = 5;
const ALLOWED_EXTENSIONS = new Set([
  'doc',
  'docx',
  'gif',
  'jpeg',
  'jpg',
  'latex',
  'markdown',
  'md',
  'pdf',
  'png',
  'svg',
  'tex',
  'tif',
  'tiff',
  'txt',
  'webp',
]);

/**
 * Checks whether the selected file is one of the frontend-supported source
 * document formats.
 *
 * @param {File} file
 * @returns {boolean}
 */
function isAllowedSourceFile(file) {
  const extension = file.name.split('.').pop()?.toLowerCase();
  return Boolean(extension && ALLOWED_EXTENSIONS.has(extension));
}

/**
 * Returns file-selection helpers plus the current upload limit state.
 *
 * The hook deduplicates files by `name + size`, enforces the five-file cap,
 * and dispatches the file actions handled by App.jsx.
 *
 * @param {Array<{id: string, name: string, size: number}>} files - Current
 *   file list from app state.
 * @param {Function} dispatch - App reducer dispatcher.
 * @returns {{handleFileSelect: Function, removeFile: Function, limitReached: boolean}}
 */
export function useFileUpload(files, dispatch) {
  const handleFileSelect = useCallback((event) => {
    const selected = Array.from(event.target.files);
    const available = MAX_FILES - files.length;

    if (available <= 0) {
      dispatch({
        type: 'UPLOAD_WARNING',
        message: `You can attach at most ${MAX_FILES} files.`,
      });
      event.target.value = '';
      return;
    }

    const supported = selected.filter(isAllowedSourceFile);
    const allowed = supported.slice(0, available);
    const newFiles = allowed
      .filter((f) => !files.some((existing) => existing.name === f.name && existing.size === f.size))
      .map((f) => ({ id: generateId(), name: f.name, size: f.size, type: f.type, file: f }));

    if (newFiles.length > 0) {
      dispatch({ type: 'ADD_FILES', files: newFiles });
    }

    const unsupportedCount = selected.length - supported.length;
    const overflowCount = Math.max(0, supported.length - available);
    const duplicateCount = allowed.length - newFiles.length;
    const warningParts = [];
    if (unsupportedCount > 0) {
      warningParts.push(
        `${unsupportedCount} unsupported file${unsupportedCount === 1 ? '' : 's'}`,
      );
    }
    if (overflowCount > 0) {
      warningParts.push(
        `${overflowCount} file${overflowCount === 1 ? '' : 's'} over the ${MAX_FILES}-file limit`,
      );
    }
    if (duplicateCount > 0) {
      warningParts.push(
        `${duplicateCount} duplicate file${duplicateCount === 1 ? '' : 's'}`,
      );
    }
    if (warningParts.length > 0) {
      dispatch({
        type: 'UPLOAD_WARNING',
        message: `Not attached: ${warningParts.join(', ')}.`,
      });
    }

    event.target.value = '';
  }, [files, dispatch]);

  const removeFile = useCallback((fileId) => {
    dispatch({ type: 'REMOVE_FILE', fileId });
  }, [dispatch]);

  const limitReached = files.length >= MAX_FILES;

  return { handleFileSelect, removeFile, limitReached };
}
