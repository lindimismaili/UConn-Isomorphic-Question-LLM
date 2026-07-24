import { useState } from 'react';
import Modal from './Modal.jsx';
import PromptViewerModal from './PromptViewerModal.jsx';
import RubricViewerModal from './RubricViewerModal.jsx';
import { IconFile } from '../Icons.jsx';
import { Button } from '../ui/index.js';
import { getDerivedRegistry } from '../../hooks/useAgentRegistry';

const DEFAULT_TIME_ZONE = 'America/New_York';

/**
 * Formats agent input/output payloads for the detail view.
 *
 * @param {unknown} value
 * @returns {string}
 */
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

/**
 * Formats agent timestamps in the configured display timezone.
 *
 * @param {string | number | Date | null | undefined} date
 * @returns {string}
 */
function formatDateTime(date) {
  if (!date) return '-';
  const parsed = new Date(date);
  if (Number.isNaN(parsed.getTime())) return String(date);
  try {
    return parsed.toLocaleString(undefined, {
      timeZone: DEFAULT_TIME_ZONE,
      timeZoneName: 'short',
    });
  } catch {
    return parsed.toLocaleString();
  }
}

/**
 * Formats execution time for the agent details sidebar.
 *
 * @param {number | null | undefined} seconds
 * @returns {string}
 */
function formatExecutionTime(seconds) {
  if (seconds === null || seconds === undefined) return '-';
  return `${Number(seconds).toFixed(2)}s`;
}

/**
 * Returns a display-safe value for optional agent metadata fields.
 *
 * @param {unknown} val
 * @param {string} defaultVal
 * @returns {unknown}
 */
function getValue(val, defaultVal = 'Unavailable') {
  if (val === null || val === undefined || val === '' || val === '-') return defaultVal;
  return val;
}

/**
 * Shows agent configuration plus the captured input/output payloads.
 *
 * @param {object} props
 */
export default function AgentDetailModal({ isOpen, onClose, agentKey, agent }) {
  const [viewerOpen, setViewerOpen] = useState(false);

  // Do NOT early-return null here. The Modal must always be mounted so the
  // CSS opacity/transform transitions can fire when isOpen flips to true.
  const registryEntry = agentKey ? getDerivedRegistry()?.registry?.[agentKey] : null;
  const promptName = registryEntry?.promptName || (registryEntry?.hasPrompt ? agentKey : '') || '';
  const isEvaluation = Boolean(registryEntry && !registryEntry.isStoppable && registryEntry.isLLM);
  const canOpenReference = Boolean(promptName || isEvaluation);
  const promptTitle = agentKey ? (registryEntry?.name || agentKey) : '';
  const statusLabel = agent?.status
    ? agent.status.charAt(0).toUpperCase() + agent.status.slice(1)
    : 'Unavailable';
  const nodeKind = agent?.kind || agent?.nodeKind || registryEntry?.kind || 'agent';
  const isRuntime = nodeKind === 'runtime';
  const details = agent
    ? [
        { label: isRuntime ? 'Runtime Name' : 'Agent Name', value: getValue(agent.name) },
        { label: 'Description', value: getValue(agent.description) },
        { label: isRuntime ? 'Runtime' : 'Model', value: getValue(agent.model) },
        ...(!isRuntime ? [{ label: 'Reasoning Level', value: getValue(agent.reasoningLevel, 'none') }] : []),
        { label: 'Status', value: statusLabel },
        { label: 'Start Time', value: agent.startTime ? formatDateTime(agent.startTime) : 'Unavailable' },
        { label: 'End Time', value: agent.endTime ? formatDateTime(agent.endTime) : 'Unavailable' },
        { label: 'Execution Time', value: agent.executionTime !== null && agent.executionTime !== undefined ? formatExecutionTime(agent.executionTime) : 'Unavailable' },
        { label: 'Input From Agent', value: getValue(agent.inputFrom) },
        { label: 'Output To Agent', value: getValue(agent.outputTo) },
      ]
    : [];

  return (
    <>
      <Modal
        isOpen={isOpen}
        onClose={onClose}
        title={agent ? `${agent.name} Details` : 'Agent Details'}
        large
        contentClassName="agent-detail-modal"
        modalBodyClassName="modal-body-flush"
        footer={
          <div className="modal-footer-actions">
            <Button
              variant="secondary"
              disabled={!canOpenReference}
              onClick={() => canOpenReference && setViewerOpen(true)}
              icon={<IconFile className="btn-icon" />}
            >
              <span>Prompt</span>
            </Button>
            <Button variant="primary" onClick={onClose}>Close</Button>
          </div>
        }
      >
        {agent && (
          <div className="agent-modal-body">
            <div className="agent-details-sidebar">
              {details.map(({ label, value }) => (
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
        isOpen={viewerOpen && !isEvaluation}
        onClose={() => setViewerOpen(false)}
        promptName={promptName}
        title={`${promptTitle} Prompt`}
      />
      <RubricViewerModal
        isOpen={viewerOpen && isEvaluation}
        onClose={() => setViewerOpen(false)}
      />
    </>
  );
}
