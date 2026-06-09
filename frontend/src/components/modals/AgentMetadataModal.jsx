import Modal from './Modal.jsx';
import { IconDownload } from '../Icons.jsx';
import useFileContent from '../../hooks/useFileContent.js';

const PIPELINE_ORDER = ['normalizer', 'extracter', 'verifier', 'generator', 'latexifier', 'evaluation', 'output'];

function sortAgents(agents) {
  return [...agents].sort((a, b) => {
    const ai = PIPELINE_ORDER.indexOf(a.agent_key);
    const bi = PIPELINE_ORDER.indexOf(b.agent_key);
    const na = ai === -1 ? PIPELINE_ORDER.length : ai;
    const nb = bi === -1 ? PIPELINE_ORDER.length : bi;
    return na - nb || (a.agent_name || '').localeCompare(b.agent_name || '');
  });
}

function AgentCard({ agent }) {
  const statusClass =
    agent.status === 'completed' ? 'viewer-pill-pass'
    : agent.status === 'failed' ? 'viewer-pill-fail'
    : 'viewer-pill-neutral';

  const execTime = typeof agent.execution_time_seconds === 'number'
    ? `${agent.execution_time_seconds.toFixed(2)}s`
    : null;

  return (
    <div className="viewer-agent-card">
      <div className="viewer-agent-card-header">
        <span className="viewer-agent-name">{agent.agent_name || agent.agent_key}</span>
        <div className="viewer-agent-meta">
          {agent.model && <span className="viewer-agent-model">{agent.model}</span>}
          {execTime && <span className="viewer-agent-time">{execTime}</span>}
          <span className={`viewer-pill ${statusClass}`}>{agent.status || 'unknown'}</span>
        </div>
      </div>
      <div className="viewer-flow-line">
        <span className="viewer-flow-node">{agent.received_from_node || '—'}</span>
        <span className="viewer-flow-arrow">→</span>
        <span className="viewer-flow-node viewer-flow-node-self">{agent.agent_name || agent.agent_key}</span>
        <span className="viewer-flow-arrow">→</span>
        <span className="viewer-flow-node">{agent.sent_to_node || '—'}</span>
      </div>
      {(agent.input_artifact || agent.output_artifact) && (
        <div className="viewer-agent-artifacts">
          {agent.input_artifact && (
            <span className="viewer-agent-artifact">In: {agent.input_artifact}</span>
          )}
          {agent.output_artifact && (
            <span className="viewer-agent-artifact">Out: {agent.output_artifact}</span>
          )}
        </div>
      )}
    </div>
  );
}

export default function AgentMetadataModal({ isOpen, onClose, fileName, url, onDownload }) {
  const { text, loading, error } = useFileContent(url, isOpen);

  let data = null;
  if (text) {
    try { data = JSON.parse(text); } catch { /* ignore */ }
  }

  const agents = data ? sortAgents(Array.isArray(data.agents) ? data.agents : []) : [];
  const sourceFiles = Array.isArray(data?.source_files) ? data.source_files : [];
  const generatedAt = data?.generated_at || '';
  const workflowId = data?.workflow_id || '';

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title="Agent Metadata"
      contentClassName="agent-metadata-modal" size="full"
      footer={(
        <div className="modal-footer-actions">
          {onDownload && fileName && (
            <button className="btn btn-secondary" onClick={() => onDownload(fileName)} type="button">
              <IconDownload className="btn-icon" />
              Download
            </button>
          )}
          <button className="btn btn-primary" onClick={onClose} type="button">Close</button>
        </div>
      )}
    >
      {loading && <div className="modal-empty-state">Loading…</div>}
      {error && <div className="modal-empty-state modal-empty-state-error">{error}</div>}
      {!loading && !error && data && (
        <div className="viewer-body-stack">
          {(workflowId || generatedAt) && (
            <div className="viewer-meta-row">
              {workflowId && <span className="viewer-meta-item"><strong>Workflow:</strong> {workflowId}</span>}
              {generatedAt && <span className="viewer-meta-item"><strong>Generated:</strong> {generatedAt}</span>}
            </div>
          )}

          {sourceFiles.length > 0 && (
            <div className="viewer-section">
              <h5 className="viewer-section-heading">Source Files</h5>
              <ul className="viewer-source-files">
                {sourceFiles.map((f, i) => (
                  <li key={i} className="viewer-source-file">
                    {typeof f === 'string' ? f : (f.name || f.path || JSON.stringify(f))}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {agents.length > 0 && (
            <div className="viewer-section">
              <h5 className="viewer-section-heading">Pipeline Agents ({agents.length})</h5>
              <div className="viewer-agent-list">
                {agents.map((agent, i) => (
                  <AgentCard key={agent.agent_key || i} agent={agent} />
                ))}
              </div>
            </div>
          )}

          {agents.length === 0 && (
            <div className="modal-empty-state">No agent records found.</div>
          )}
        </div>
      )}
    </Modal>
  );
}
