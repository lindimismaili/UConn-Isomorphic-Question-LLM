import { useEffect, useRef, useState } from 'react';
import { useFileUpload } from '../hooks/useFileUpload.js';
import {
  IconFile,
  IconUpload,
  IconTrash,
  IconSend,
  IconInfo,
} from './Icons.jsx';

/**
 * Normalizes the theme-prompt payload from `/api/dropdown`.
 *
 * The dropdown JSON stores only `label` and `prompt`; the `value` (used as the
 * select option key) is derived from the label by lowercasing and replacing
 * spaces with underscores. e.g. "Video Games" → "video_games".
 *
 * @param {unknown} raw
 * @returns {Array<{value: string, label: string, prompt: string}>}
 */
function normalizeDropdownItems(raw) {
  const source = Array.isArray(raw) ? raw : raw?.items;
  if (!Array.isArray(source)) return [];

  return source
    .map((item) => {
      const label = String(item?.label || '').trim();
      const value = label.toLowerCase().replace(/\s+/g, '_');
      return {
        value,
        label,
        prompt: String(item?.prompt || '').trim(),
      };
    })
    .filter((item) => item.value && item.label && item.prompt);
}

/**
 * Removes one previously inserted theme line from the prompt textarea.
 *
 * @param {string} text
 * @param {string} lineToRemove
 * @returns {string}
 */
function removePromptLine(text, lineToRemove) {
  if (!lineToRemove) return text;
  return text
    .split('\n')
    .filter((line) => line.trim() !== lineToRemove.trim())
    .join('\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

/**
 * Main prompt, upload, and workflow-control surface.
 *
 * @param {object} props - State and callbacks supplied by App.jsx.
 */
export default function PromptInput({
  prompt,
  onPromptChange,
  files,
  dispatch,
  isRunning,
  serviceInactive,
  onSend,
  onClearInputs,
  onClearAgents,
  onShowFiles,
  onShowHelp,
}) {
  const [resetFeedback, setResetFeedback] = useState(false);
  const [uploadWarning, setUploadWarning] = useState(false);
  const [dropdownItems, setDropdownItems] = useState([]);
  const [selectedDropdownValue, setSelectedDropdownValue] = useState('');
  const fileInputRef = useRef(null);
  const uploadWarningTimerRef = useRef(null);
  const resetFeedbackTimerRef = useRef(null);

  useEffect(() => {
    return () => {
      clearTimeout(uploadWarningTimerRef.current);
      clearTimeout(resetFeedbackTimerRef.current);
    };
  }, []);

  useEffect(() => {
    let cancelled = false;

    fetch('/api/dropdown', { cache: 'no-store' })
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (cancelled) return;
        setDropdownItems(normalizeDropdownItems(data));
      })
      .catch(() => {
        if (!cancelled) setDropdownItems([]);
      });

    return () => {
      cancelled = true;
    };
  }, []);

  const { handleFileSelect, limitReached } = useFileUpload(files, dispatch);
  const dropdownSelectValue = prompt.trim() ? selectedDropdownValue : '';

  const handleUploadWarning = () => {
    setUploadWarning(true);
    clearTimeout(uploadWarningTimerRef.current);
    uploadWarningTimerRef.current = setTimeout(() => setUploadWarning(false), 1600);
    dispatch({ type: 'UPLOAD_WARNING' });
  };

  const handleSend = () => {
    if (serviceInactive) {
      alert('OpenAI servers are down. This site is currently inactive.');
      return;
    }
    if (!prompt.trim() && files.length === 0) {
      alert('Please enter a prompt or upload files.');
      return;
    }
    onSend(prompt, files);
  };

  const handleReset = () => {
    onClearInputs();
    onClearAgents();
    dispatch({ type: 'CLEAR_LOGS' });
    setResetFeedback(true);
    clearTimeout(resetFeedbackTimerRef.current);
    resetFeedbackTimerRef.current = setTimeout(() => setResetFeedback(false), 1400);
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
      handleSend();
    }
  };

  const handleDropdownChange = (e) => {
    const nextValue = e.target.value;
    const previousItem = dropdownItems.find((item) => item.value === selectedDropdownValue);
    const nextItem = dropdownItems.find((item) => item.value === nextValue);
    const cleanedPrompt = removePromptLine(prompt, previousItem?.prompt);
    const nextPrompt = nextItem?.prompt || '';
    const mergedPrompt = nextPrompt
      ? [cleanedPrompt, nextPrompt].filter(Boolean).join('\n\n')
      : cleanedPrompt;

    setSelectedDropdownValue(nextValue);
    onPromptChange(mergedPrompt);
  };

  return (
    <section className="card main-card">
      <div className="card-header main-card-header">
        <div className="card-header-row">
          <h2>Start Here!</h2>
          <div className="header-buttons">
            <button
              className="btn btn-secondary"
              onClick={onShowHelp}
            >
              <IconInfo className="btn-icon" />
              <span>Summary</span>
            </button>
          </div>
        </div>
        <p className="disclaimer-text">Disclaimer: All data/files/prompts sent are saved to our servers.</p>
        <p className="subtitle">
          Enter a prompt and provide some example questions. You can upload
          files as well! By default, the generator will create brand new
          questions with the same concept, difficulty, and logic as the original.
        </p>
      </div>

      <div className="card-body">
        <div className="prompt-layout">
          <div className="prompt-theme-row">
            <label className="prompt-dropdown-label" htmlFor="theme-prompt-select">
              Theme Prompt
            </label>
            <select
              id="theme-prompt-select"
              className="prompt-dropdown-select"
              value={dropdownSelectValue}
              onChange={handleDropdownChange}
              disabled={isRunning || serviceInactive || dropdownItems.length === 0}
            >
              <option value="">Choose a theme...</option>
              {dropdownItems.map((item) => (
                <option key={item.value} value={item.value}>
                  {item.label}
                </option>
              ))}
            </select>
          </div>

          <textarea
            id="prompt-input"
            name="prompt"
            className="prompt-input"
            placeholder="Enter your instructions here... e.g., 'Generate 3 isomorphic questions per source question with medium difficulty'"
            value={prompt}
            onChange={(e) => onPromptChange(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={isRunning || serviceInactive}
          />

          <div className="prompt-actions-row">
            <button
              className="btn btn-primary send-action"
              onClick={handleSend}
              disabled={isRunning || serviceInactive}
            >
              <IconSend className="btn-icon" />
              <span>Send</span>
            </button>

            <button
              className={`btn btn-secondary${uploadWarning ? ' btn-error-feedback' : ''}`}
              onClick={() => {
                if (limitReached) {
                  handleUploadWarning();
                  return;
                }
                fileInputRef.current?.click();
              }}
              disabled={isRunning || serviceInactive}
              title="Upload up to 5 files (.pdf, .docx, .doc, .txt)"
            >
              <IconUpload className="btn-icon" />
              <span className="btn-label">{uploadWarning ? 'Too many files!' : 'Upload Files'}</span>
            </button>
            <input
              ref={fileInputRef}
              id="source-files-input"
              name="source_files"
              type="file"
              multiple
              accept=".pdf,.docx,.doc,.txt"
              hidden
              onChange={handleFileSelect}
            />

            <button
              className="btn btn-secondary file-count-btn"
              onClick={onShowFiles}
              type="button"
              aria-label={`Files - ${files.length} selected files to upload`}
            >
              <IconFile className="btn-icon" />
              <span className="btn-label">Files - {files.length}</span>
            </button>

            <button
              className={`btn btn-secondary${resetFeedback ? ' btn-feedback' : ''}`}
              onClick={handleReset}
              disabled={isRunning || serviceInactive || resetFeedback}
            >
              <IconTrash className="btn-icon" />
              <span className="btn-label">Reset</span>
            </button>
          </div>
        </div>
      </div>
    </section>
  );
}
