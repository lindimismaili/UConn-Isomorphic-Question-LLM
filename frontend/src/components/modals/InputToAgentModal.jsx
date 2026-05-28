import { useMemo, useRef, useState } from 'react';
import Modal from './Modal.jsx';
import { IconFile, IconUpload } from '../Icons.jsx';

/** Agent stages that can receive an uploaded start file. */
const INPUT_AGENT_KEYS = [
  'normalizer',
  'extracter',
  'verifier',
  'generator',
  'latexifier',
  'evaluation',
];

/** Stages that need a source document rather than a saved JSON input artifact. */
const SOURCE_FILE_AGENT_KEYS = new Set(['normalizer', 'extracter']);

/**
 * Lets developers upload an agent input artifact and resume from that stage.
 *
 * @param {object} props
 */
export default function InputToAgentModal({
  isOpen,
  onClose,
  agents,
  isRunning,
  serviceInactive,
  onRun,
}) {
  const [selectedAgent, setSelectedAgent] = useState(INPUT_AGENT_KEYS[0]);
  const [inputFile, setInputFile] = useState(null);
  const [error, setError] = useState('');
  const fileInputRef = useRef(null);
  const needsSourceFile = SOURCE_FILE_AGENT_KEYS.has(selectedAgent);
  const acceptedFileTypes = needsSourceFile
    ? '.pdf,.docx,.doc,.txt'
    : '.json,.jsonl,.txt';

  const agentOptions = useMemo(() => (
    INPUT_AGENT_KEYS.map((key) => ({
      key,
      label: agents?.[key]?.name && agents[key].name !== 'Unavailable'
        ? agents[key].name
        : key,
      description: agents?.[key]?.description || '',
    }))
  ), [agents]);

  const handleFileChange = (event) => {
    const file = event.target.files?.[0] || null;
    setInputFile(file);
    setError('');
  };

  const resetLocalState = () => {
    setInputFile(null);
    setError('');
    if (fileInputRef.current) {
      fileInputRef.current.value = '';
    }
  };

  const handleClose = () => {
    resetLocalState();
    onClose();
  };

  const handleRun = () => {
    if (!inputFile) {
      setError('Choose an agent input file first.');
      return;
    }
    setError('');
    onRun(selectedAgent, inputFile, {
      onStarted: () => {
        resetLocalState();
        onClose();
      },
      onError: (message) => {
        setError(message || 'Schema match invalid');
      },
    });
  };

  return (
    <Modal
      isOpen={isOpen}
      onClose={handleClose}
      title="Input To Agent"
      footer={
        <div className="modal-footer-actions">
          <button className="btn btn-secondary" onClick={handleClose} type="button">
            Cancel
          </button>
          <button
            className="btn btn-primary"
            onClick={handleRun}
            disabled={isRunning || serviceInactive || !inputFile}
            type="button"
          >
            Start Workflow
          </button>
        </div>
      }
    >
      <div className="modal-body-stack input-to-agent-modal">
        <p className="modal-supporting-text">
          Starter and Output always run. Choose any agent between them, upload a
          source document for Normalizer or Extracter, or a saved agent input
          file for later stages. Saved agent inputs must match that agent's
          input schema.
        </p>

        <label className="input-to-agent-field" htmlFor="input-to-agent-select">
          <span>Agent</span>
          <select
            id="input-to-agent-select"
            className="input-to-agent-select"
            value={selectedAgent}
            onChange={(event) => {
              setSelectedAgent(event.target.value);
              resetLocalState();
            }}
            disabled={isRunning || serviceInactive}
          >
            {agentOptions.map((agent) => (
              <option key={agent.key} value={agent.key}>
                {agent.label}
              </option>
            ))}
          </select>
        </label>

        <div className="input-to-agent-upload">
          <div className="input-to-agent-file">
            <IconFile />
            <div>
              <div className="file-item-name">
                {inputFile
                  ? inputFile.name
                  : needsSourceFile
                    ? 'No source file selected'
                    : 'No agent input selected'}
              </div>
              <div className="file-item-path">
                {needsSourceFile
                  ? 'Accepted: .pdf, .docx, .doc, .txt'
                  : 'Accepted: .json, .jsonl, .txt'}
              </div>
            </div>
          </div>
          <button
            className="btn btn-secondary"
            onClick={() => fileInputRef.current?.click()}
            disabled={isRunning || serviceInactive}
            type="button"
          >
            <IconUpload className="btn-icon" />
            Select Input
          </button>
          <input
            ref={fileInputRef}
            type="file"
            hidden
            accept={acceptedFileTypes}
            onChange={handleFileChange}
          />
        </div>

        {error && <div className="modal-empty-state modal-empty-state-error">{error}</div>}
      </div>
    </Modal>
  );
}
