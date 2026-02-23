import { useState } from 'react';
import Modal from './Modal.jsx';
import PromptViewerModal from './PromptViewerModal.jsx';
import { IconFile } from '../Icons.jsx';

const AGENT_PROMPT_MAP = {
  normalizer: 'normalizer',
  extracter: 'extracter',
  verifier: 'verifier',
  generator: 'generator',
  latex: 'latex',
};

const AGENT_PROMPT_TITLES = {
  normalizer: 'Normalizer',
  extracter: 'Extracter',
  verifier: 'Verifier',
  generator: 'Generator',
  latex: 'LaTeX',
};

function formatText(value) {
  if (value === null || value === undefined) return '';
  if (typeof value === 'string') {
    const processed = value.replace(/\\n/g, '\n').replace(/\\t/g, '    ').replace(/\\r/g, '').trim();
    if (!processed) return '';
    try {
      return JSON.stringify(JSON.parse(processed), null, 2);
    } catch {
      return processed;
    }
  }
  if (Array.isArray(value)) {
    if (value.length === 0) return '';
    return JSON.stringify(value, null, 2);
  }
  if (typeof value === 'object') {
    if (Object.keys(value).length === 0) return '';
    return JSON.stringify(value, null, 2);
  }
  return String(value);
}

function formatDateTime(date) {
  if (!date) return '-';
  return new Date(date).toLocaleString();
}

function formatExecutionTime(seconds) {
  if (seconds === null || seconds === undefined) return '-';
  return `${Number(seconds).toFixed(2)}s`;
}

function getValue(val, defaultVal = 'Unavailable') {
  if (val === null || val === undefined || val === '' || val === '-') return defaultVal;
  return val;
}

export default function AgentDetailModal({ isOpen, onClose, agentKey, agent }) {
  const [viewerOpen, setViewerOpen] = useState(false);

  // Do NOT early-return null here. The Modal must always be mounted so the
  // CSS opacity/transform transitions can fire when isOpen flips to true.
  const promptName = agentKey ? (AGENT_PROMPT_MAP[agentKey] || '') : '';
  const promptTitle = agentKey ? (AGENT_PROMPT_TITLES[agentKey] || agentKey) : '';
  const statusLabel = agent?.status
    ? agent.status.charAt(0).toUpperCase() + agent.status.slice(1)
    : 'Unavailable';

  return (
    <>
      <Modal
        isOpen={isOpen}
        onClose={onClose}
        title={agent ? `${agent.name} Details` : 'Agent Details'}
        large
        footer={
          <div className="agent-modal-footer">
            <div className="output-folder-note modal-file-location">
              <IconFile />
              <span>
                File located in:{' '}
                <code>data/agents.json</code>
              </span>
            </div>
            <div className="modal-footer-actions">
              <button
                className="btn btn-ghost"
                disabled={!promptName}
                onClick={() => promptName && setViewerOpen(true)}
              >
                Prompt
              </button>
              <button className="btn btn-secondary" onClick={onClose}>Close</button>
            </div>
          </div>
        }
      >
        {agent && (
          <div className="agent-modal-body" style={{ padding: 0 }}>
            <div className="agent-details-sidebar">
              {[
                { label: 'Agent Name', value: getValue(agent.name) },
                { label: 'Description', value: getValue(agent.description) },
                { label: 'Model', value: getValue(agent.model) },
                { label: 'Reasoning Level', value: getValue(agent.reasoningLevel, 'none') },
                { label: 'Status', value: statusLabel },
                { label: 'Start Time', value: agent.startTime ? formatDateTime(agent.startTime) : 'Unavailable' },
                { label: 'End Time', value: agent.endTime ? formatDateTime(agent.endTime) : 'Unavailable' },
                { label: 'Execution Time', value: agent.executionTime !== null && agent.executionTime !== undefined ? formatExecutionTime(agent.executionTime) : 'Unavailable' },
                { label: 'Input From Agent', value: getValue(agent.inputFrom) },
                { label: 'Output To Agent', value: getValue(agent.outputTo) },
              ].map(({ label, value }) => (
                <div key={label} className="detail-item">
                  <label>{label}</label>
                  <span>{value}</span>
                </div>
              ))}
            </div>
            <div className="agent-details-main">
              <div className="schema-panels">
                <div className="detail-text-section">
                  <label>Input</label>
                  <pre className="detail-text-box">{formatText(agent.inputText) || 'No input data'}</pre>
                </div>
                <div className="detail-text-section">
                  <label>Output</label>
                  <pre className="detail-text-box">{formatText(agent.outputText) || 'No output data'}</pre>
                </div>
              </div>
            </div>
          </div>
        )}
      </Modal>

      <PromptViewerModal
        isOpen={viewerOpen}
        onClose={() => setViewerOpen(false)}
        promptName={promptName}
        title={`${promptTitle} Prompt`}
      />
    </>
  );
}
