import { useEffect, useRef, useState } from 'react';
import { useFileUpload } from '../hooks/useFileUpload.js';
import { IconFile, IconUpload, IconTrash, IconSend, IconInfo, IconList } from './Icons.jsx';

export default function PromptInput({
  files,
  dispatch,
  isRunning,
  onSend,
  onClearInputs,
  onClearAgents,
  onShowFiles,
  onShowTestFiles,
}) {
  const [prompt, setPrompt] = useState('');
  const [resetFeedback, setResetFeedback] = useState(false);
  const [uploadWarning, setUploadWarning] = useState(false);
  const fileInputRef = useRef(null);
  const uploadWarningTimerRef = useRef(null);
  const resetFeedbackTimerRef = useRef(null);

  useEffect(() => {
    return () => {
      clearTimeout(uploadWarningTimerRef.current);
      clearTimeout(resetFeedbackTimerRef.current);
    };
  }, []);

  const { handleFileSelect, limitReached } = useFileUpload(files, dispatch);

  const handleUploadWarning = () => {
    setUploadWarning(true);
    clearTimeout(uploadWarningTimerRef.current);
    uploadWarningTimerRef.current = setTimeout(() => setUploadWarning(false), 1600);
    dispatch({ type: 'UPLOAD_WARNING' });
  };

  const handleSend = () => {
    if (!prompt.trim() && files.length === 0) {
      alert('Please enter a prompt or upload files.');
      return;
    }
    onSend(prompt, files);
    setPrompt('');
  };

  const handleReset = () => {
    onClearInputs();
    onClearAgents();
    dispatch({ type: 'CLEAR_LOGS' });
    setPrompt('');
    setResetFeedback(true);
    clearTimeout(resetFeedbackTimerRef.current);
    resetFeedbackTimerRef.current = setTimeout(() => setResetFeedback(false), 1400);
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
      handleSend();
    }
  };

  return (
    <section className="card main-card">
      <div className="card-header main-card-header">
        <div className="card-header-row">
          <h2>Start Here!</h2>
          <div className="header-buttons">
            <button
              className="btn btn-secondary"
              onClick={() => dispatch({ type: 'OPEN_MODAL', modal: 'skills' })}
            >
              <IconList className="btn-icon" />
              <span>Skills</span>
            </button>
            <button
              className="btn btn-secondary"
              onClick={() => dispatch({ type: 'OPEN_MODAL', modal: 'help' })}
            >
              <IconInfo className="btn-icon" />
              <span>Summary</span>
            </button>
          </div>
        </div>
        <p className="subtitle">
          Enter a prompt and provide some example questions. You can upload
          files as well! By default, the generator will create brand new
          questions with the same concept, difficulty, and logic as the original.
        </p>
      </div>

      <div className="card-body">
        <div className="prompt-row">
          <textarea
            id="prompt-input"
            name="prompt"
            className="prompt-input"
            placeholder="Enter your instructions here... e.g., 'Generate 3 isomorphic questions per source question with medium difficulty'"
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={isRunning}
          />
        </div>

        <div className="card-actions">
          {files.length > 0 && (
            <div className="file-count-row">
              <button className="btn btn-secondary file-count-btn" onClick={onShowFiles}>
                <IconFile className="btn-icon" />
                <span>Current Files — <span>{files.length}</span></span>
              </button>
            </div>
          )}

          <div className="actions-row">
            <button
              className={`btn btn-secondary${resetFeedback ? ' btn-feedback' : ''}`}
              onClick={handleReset}
              disabled={isRunning || resetFeedback}
            >
              <IconTrash className="btn-icon" />
              <span className="btn-label">Reset</span>
            </button>

            <button
              className="btn btn-secondary"
              onClick={onShowTestFiles}
              disabled={isRunning}
            >
              <IconFile className="btn-icon" />
              <span className="btn-label">Test Files</span>
            </button>

            <div className="upload-group">
              <button
                className={`btn btn-secondary${uploadWarning ? ' btn-error-feedback' : ''}`}
                onClick={() => {
                  if (limitReached) {
                    handleUploadWarning();
                    return;
                  }
                  fileInputRef.current?.click();
                }}
                disabled={isRunning}
              >
                <IconUpload className="btn-icon" />
                <span className="btn-label">{uploadWarning ? 'Too many files!' : 'Upload Files'}</span>
              </button>
              <span className="file-limit-text">Limit: 5 Files</span>
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
            </div>

            <button
              className="btn btn-primary"
              onClick={handleSend}
              disabled={isRunning}
            >
              <IconSend className="btn-icon" />
              <span>Send</span>
            </button>
          </div>
        </div>
      </div>
    </section>
  );
}
