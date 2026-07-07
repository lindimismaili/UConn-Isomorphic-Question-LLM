import { useMemo, useRef, useState } from 'react';
import Modal from './Modal.jsx';
import { IconFile, IconUpload } from '../Icons.jsx';
import { useAgentRegistry } from '../../hooks/useAgentRegistry';
import { Button, Dropdown, EmptyState } from '../ui/index.js';

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
  const registryData = useAgentRegistry();
  const inputAgentKeys = useMemo(() => registryData?.inputAgentKeys ?? [], [registryData]);
  const sourceFileAgentKeys = registryData?.sourceFileAgentKeys ?? new Set();
  const agentLabels = registryData?.agentLabels ?? {};
  const runtimeAgentKeys = registryData?.runtimeAgentKeys ?? [];
  const runtimeNames = runtimeAgentKeys.map((key) => agentLabels[key] || key);
  const sourceFileNames = (registryData?.pipelineKeys ?? [])
    .filter((key) => sourceFileAgentKeys.has(key))
    .map((key) => agentLabels[key] || key);

  const [selectedAgent, setSelectedAgent] = useState('');
  const resolvedAgent = selectedAgent || inputAgentKeys[0] || '';
  const [inputFile, setInputFile] = useState(null);
  const [error, setError] = useState('');
  const fileInputRef = useRef(null);
  const needsSourceFile = sourceFileAgentKeys.has(resolvedAgent);
  const acceptedFileTypes = needsSourceFile
    ? '.pdf,.docx,.doc,.txt'
    : '.json,.jsonl,.txt';

  const agentOptions = useMemo(() => (
    inputAgentKeys.map((key) => ({
      key,
      label: agents?.[key]?.name && agents[key].name !== 'Unavailable'
        ? agents[key].name
        : key,
      description: agents?.[key]?.description || '',
    }))
  ), [agents, inputAgentKeys]);

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
    onRun(resolvedAgent, inputFile, {
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
          <Button variant="secondary" onClick={handleClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            onClick={handleRun}
            disabled={isRunning || serviceInactive || !inputFile}
          >
            Start Workflow
          </Button>
        </div>
      }
    >
      <div className="modal-body-stack input-to-agent-modal">
        <p className="modal-supporting-text">
          {runtimeNames.join(' and ')} always run. Choose any agent between
          them, upload a source document for {sourceFileNames.join(' or ')}, or
          a saved agent input file for later stages. Saved agent inputs must
          match that agent's input schema.
        </p>

        <label className="input-to-agent-field" htmlFor="input-to-agent-select">
          <span>Agent</span>
          <Dropdown
            id="input-to-agent-select"
            className="input-to-agent-select"
            value={resolvedAgent}
            onChange={(value) => {
              setSelectedAgent(value);
              resetLocalState();
            }}
            disabled={isRunning || serviceInactive}
            options={agentOptions.map((agent) => ({ value: agent.key, label: agent.label }))}
          />
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
          <Button
            variant="secondary"
            onClick={() => fileInputRef.current?.click()}
            disabled={isRunning || serviceInactive}
            icon={<IconUpload className="btn-icon" />}
          >
            Select Input
          </Button>
          <input
            ref={fileInputRef}
            type="file"
            hidden
            accept={acceptedFileTypes}
            onChange={handleFileChange}
          />
        </div>

        {error && <EmptyState variant="modal" error>{error}</EmptyState>}
      </div>
    </Modal>
  );
}
