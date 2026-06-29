import { useEffect, useRef, useState } from 'react';
import { useFileUpload } from '../hooks/useFileUpload.js';
import {
  IconUpload,
  IconTrash,
  IconSend,
  IconInfo,
} from './Icons.jsx';
import { Button, Card, CardBody, CardHeader, Select, TextArea } from './ui/index.js';

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
 * Builds the "Label: Prompt" line injected into the textarea for a theme.
 *
 * @param {{label: string, prompt: string} | undefined} item
 * @returns {string}
 */
function themeLine(item) {
  return item ? `${item.label}: ${item.prompt}` : '';
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
    if (isRunning) {
      handleReset();
      return;
    }
    if (serviceInactive) {
      alert('OpenAI servers are down. This site is currently inactive.');
      return;
    }
    if (!prompt.trim() && files.length === 0) {
      alert('Please enter a prompt or upload files.');
      return;
    }
    const selectedItem = dropdownItems.find((item) => item.value === dropdownSelectValue);
    onSend(prompt, files, themeLine(selectedItem));
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
    const cleanedPrompt = removePromptLine(prompt, themeLine(previousItem));
    const nextThemeLine = themeLine(nextItem);
    const mergedPrompt = nextThemeLine
      ? [cleanedPrompt, nextThemeLine].filter(Boolean).join('\n\n')
      : cleanedPrompt;

    setSelectedDropdownValue(nextValue);
    onPromptChange(mergedPrompt);
  };

  return (
    <Card variant="main">
      <CardHeader
        className="main-card-header"
        title="Start Here!"
        actions={
          <Button variant="secondary" onClick={onShowHelp} icon={<IconInfo className="btn-icon" />}>
            <span>Summary</span>
          </Button>
        }
        disclaimer="Disclaimer: All data/files/prompts sent are saved to our servers."
        subtitle="Enter a prompt and provide some example questions. You can upload files as well! By default, the generator will create brand new questions with the same concept, difficulty, and logic as the original."
      />

      <CardBody>
        <div className="prompt-layout">
          <TextArea
            id="prompt-input"
            name="prompt"
            placeholder="Enter your instructions here... e.g., 'Generate 3 isomorphic questions per source question with medium difficulty'. If no prompt is given, internal prompt will try and generate questions using default parameters."
            value={prompt}
            onChange={(e) => onPromptChange(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={isRunning || serviceInactive}
          />

          <div className="prompt-actions-row">
            <Select
              id="theme-prompt-select"
              className="prompt-theme-select"
              value={dropdownSelectValue}
              onChange={handleDropdownChange}
              disabled={isRunning || serviceInactive || dropdownItems.length === 0}
              placeholder="Select a theme..."
              options={dropdownItems}
            />

            <div className="prompt-actions-right">
              <Button
                variant="secondary"
                feedbackState={uploadWarning ? 'error' : undefined}
                onClick={() => {
                  if (limitReached) {
                    handleUploadWarning();
                    return;
                  }
                  fileInputRef.current?.click();
                }}
                disabled={isRunning || serviceInactive}
                title="Upload up to 5 files (.txt, .pdf, images, .md, .tex, .latex, .docx, .doc)"
                icon={<IconUpload className="btn-icon" />}
              >
                <span className="btn-label">
                  {uploadWarning
                    ? 'Unsupported or too many files!'
                    : files.length > 0
                      ? `Upload Files - ${files.length}`
                      : 'Upload Files'}
                </span>
              </Button>
              <input
                ref={fileInputRef}
                id="source-files-input"
                name="source_files"
                type="file"
                multiple
                accept=".txt,.pdf,image/*,.md,.markdown,.tex,.latex,.docx,.doc"
                hidden
                onChange={handleFileSelect}
              />

              <Button
                variant={isRunning ? 'danger' : 'primary'}
                className="send-action"
                feedbackState={resetFeedback ? 'success' : undefined}
                onClick={handleSend}
                disabled={resetFeedback || (!isRunning && serviceInactive)}
                icon={isRunning ? <IconTrash className="btn-icon" /> : <IconSend className="btn-icon" />}
              >
                <span>{isRunning ? 'Stop' : 'Send'}</span>
              </Button>
            </div>
          </div>
        </div>
      </CardBody>
    </Card>
  );
}
