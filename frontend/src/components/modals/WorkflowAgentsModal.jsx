import { useState } from 'react';
import Modal from './Modal.jsx';
import PromptViewerModal from './PromptViewerModal.jsx';
import RubricViewerModal from './RubricViewerModal.jsx';
import { IconDownload, IconFile, IconList, IconMonitor } from '../Icons.jsx';
import useFileContent from '../../hooks/useFileContent.js';
import { useAgentRegistry, getDerivedRegistry } from '../../hooks/useAgentRegistry';

/** Orders metadata agent records by the pipeline sequence. */
function sortAgents(agents, pipelineOrder) {
  return [...agents].sort((a, b) => {
    const ai = pipelineOrder.indexOf(a.agent_key);
    const bi = pipelineOrder.indexOf(b.agent_key);
    const na = ai === -1 ? pipelineOrder.length : ai;
    const nb = bi === -1 ? pipelineOrder.length : bi;
    return na - nb || (a.agent_name || '').localeCompare(b.agent_name || '');
  });
}

/** Resolves how to open a given agent's reference prompt, mirroring AgentDetailModal. */
function resolvePrompt(agentKey) {
  const entry = agentKey ? getDerivedRegistry()?.registry?.[agentKey] : null;
  if (!entry) return null;
  const isEvaluation = Boolean(!entry.isStoppable && entry.isLLM);
  const promptName = entry.promptName || (entry.hasPrompt ? agentKey : '');
  if (!promptName && !isEvaluation) return null;
  return { promptName, isEvaluation, title: entry.name || agentKey };
}

function formatSeconds(value) {
  return typeof value === 'number' && !Number.isNaN(value) ? `${value.toFixed(2)}s` : null;
}

/** Capitalizes a raw word (status values, fallback node keys). */
function capitalize(value) {
  if (!value || typeof value !== 'string') return value;
  return value.charAt(0).toUpperCase() + value.slice(1);
}

/**
 * Display label for a flow node key: registry name when known
 * (e.g. extracter -> Extracter, latexifier -> LaTeXifier), otherwise the
 * capitalized key (user -> User).
 */
function nodeLabel(nodeKey) {
  if (!nodeKey) return '—';
  const labels = getDerivedRegistry()?.agentLabels ?? {};
  return labels[nodeKey] || capitalize(nodeKey);
}

function AgentCard({ agent, onViewFile, onOpenPrompt }) {
  const statusClass =
    agent.status === 'completed' ? 'viewer-pill-pass'
    : agent.status === 'failed' ? 'viewer-pill-fail'
    : 'viewer-pill-neutral';

  const registryEntry = getDerivedRegistry()?.registry?.[agent.agent_key];
  const reasoning = agent.reasoning_level || registryEntry?.reasoning || 'none';
  const model = agent.model || registryEntry?.model;
  const execTime = formatSeconds(agent.execution_time_seconds);
  const prompt = resolvePrompt(agent.agent_key);
  // Prefer the registry label so display names stay consistent with the
  // pipeline even when the metadata was written by an older backend.
  const agentName = getDerivedRegistry()?.agentLabels?.[agent.agent_key]
    || agent.agent_name
    || capitalize(agent.agent_key);

  const fileButton = (label, path) => (
    <button
      className="btn btn-secondary btn-sm"
      onClick={() => onViewFile(path)}
      title={`View ${path}`}
      type="button"
    >
      <IconMonitor className="btn-icon" />
      {label}
    </button>
  );

  return (
    <div className="workflow-agent-card">
      <div className="workflow-agent-card-header">
        <span className="workflow-agent-name">{agentName}</span>
        <span className={`viewer-pill ${statusClass}`}>{capitalize(agent.status) || 'Unknown'}</span>
      </div>

      <div className="workflow-agent-meta-grid">
        {model && <div className="workflow-agent-meta"><label>Model</label><span>{model}</span></div>}
        <div className="workflow-agent-meta"><label>Reasoning</label><span>{reasoning}</span></div>
        <div className="workflow-agent-meta"><label>Time</label><span>{execTime || '—'}</span></div>
        {typeof agent.questions_count === 'number' && (
          <div className="workflow-agent-meta"><label>Questions</label><span>{agent.questions_count}</span></div>
        )}
      </div>

      <div className="viewer-flow-line">
        <span className="viewer-flow-node">{nodeLabel(agent.received_from_node)}</span>
        <span className="viewer-flow-arrow">→</span>
        <span className="viewer-flow-node viewer-flow-node-self">{agentName}</span>
        <span className="viewer-flow-arrow">→</span>
        <span className="viewer-flow-node">{nodeLabel(agent.sent_to_node)}</span>
      </div>

      <div className="workflow-agent-files">
        {agent.input_artifact && fileButton('Input', agent.input_artifact)}
        {agent.output_artifact && fileButton('Output', agent.output_artifact)}
        {prompt && (
          <button
            className="btn btn-secondary btn-sm workflow-agent-prompt"
            onClick={() => onOpenPrompt(prompt)}
            type="button"
          >
            <IconFile className="btn-icon" />
            Prompt
          </button>
        )}
      </div>
    </div>
  );
}

/**
 * Comprehensive view of the agent pipeline: run summary (inputs, normalizer
 * config, question counts), every agent's metadata, flow, timing, input/output
 * artifacts (clickable), and reference prompt.
 *
 * @param {object} props
 */
export default function WorkflowAgentsModal({
  isOpen,
  onClose,
  fileName,
  url,
  finalResultUrl,
  onDownload,
  onViewFile,
  totalTimeSeconds,
  onViewResults,
  onViewEvaluations,
}) {
  const { text, loading, error } = useFileContent(url, isOpen);
  const { text: finalResultText } = useFileContent(finalResultUrl, isOpen);
  const pipelineOrder = useAgentRegistry()?.pipelineKeys ?? [];
  const [promptTarget, setPromptTarget] = useState(null);

  let data = null;
  if (text) {
    try { data = JSON.parse(text); } catch { /* ignore */ }
  }
  let finalResult = null;
  if (finalResultText) {
    try { finalResult = JSON.parse(finalResultText); } catch { /* ignore */ }
  }

  const agents = data
    ? sortAgents(Array.isArray(data.agents) ? data.agents : [], pipelineOrder)
    : [];
  const workflowId = data?.workflow_id || '';
  const totalTime = formatSeconds(totalTimeSeconds)
    || formatSeconds(agents.reduce((sum, a) => sum + (a.execution_time_seconds || 0), 0));

  const sourceFiles = Array.isArray(data?.source_files) ? data.source_files : [];
  const config = finalResult?.config && typeof finalResult.config === 'object'
    ? finalResult.config
    : null;

  const countFor = (agentKey) => {
    const record = agents.find((a) => a.agent_key === agentKey);
    return typeof record?.questions_count === 'number' ? record.questions_count : null;
  };
  const extractedCount = countFor('extracter') ?? finalResult?.source_questions?.length ?? null;
  const generatedCount = countFor('generator') ?? finalResult?.generated_questions?.length ?? null;

  return (
    <>
      <Modal
        isOpen={isOpen}
        onClose={onClose}
        title="Agents"
        contentClassName="workflow-agents-modal" size="full"
        footer={(
          <div className="modal-footer-actions">
            {onViewResults && (
              <button className="btn btn-secondary" onClick={onViewResults} type="button">
                <IconMonitor className="btn-icon" />
                View Results
              </button>
            )}
            {onViewEvaluations && (
              <button className="btn btn-secondary" onClick={onViewEvaluations} type="button">
                <IconList className="btn-icon" />
                View Evaluations
              </button>
            )}
            {onDownload && fileName && (
              <button className="btn btn-secondary" onClick={() => onDownload(fileName)} type="button">
                <IconDownload className="btn-icon" />
                Download Metadata
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
            <div className="workflow-agents-summary">
              {workflowId && (
                <div className="workflow-agents-summary-item">
                  <label>Workflow ID</label>
                  <span className="workflow-agents-id">{workflowId}</span>
                </div>
              )}
              {totalTime && (
                <div className="workflow-agents-summary-item">
                  <label>Total Time</label>
                  <span>{totalTime}</span>
                </div>
              )}
              <div className="workflow-agents-summary-item">
                <label>Agents</label>
                <span>{agents.length}</span>
              </div>
              {extractedCount !== null && (
                <div className="workflow-agents-summary-item">
                  <label>Questions Extracted</label>
                  <span>{extractedCount}</span>
                </div>
              )}
              {generatedCount !== null && (
                <div className="workflow-agents-summary-item">
                  <label>Questions Generated</label>
                  <span>{generatedCount}</span>
                </div>
              )}
            </div>

            {(sourceFiles.length > 0 || config) && (
              <div className="workflow-agents-context">
                {sourceFiles.length > 0 && (
                  <div className="workflow-agents-context-block">
                    <h4 className="workflow-agents-context-title">Input Files</h4>
                    <ul className="viewer-source-files">
                      {sourceFiles.map((file, i) => (
                        <li className="viewer-source-file" key={file?.name || i}>
                          {file?.name || String(file)}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {config && (
                  <div className="workflow-agents-context-block">
                    <h4 className="workflow-agents-context-title">Configuration</h4>
                    <div className="viewer-kv-table">
                      {Object.entries(config).map(([key, value]) => {
                        const isEmpty = value === null || value === undefined || value === '';
                        return (
                          <div className="viewer-kv-row" key={key}>
                            <span className="viewer-kv-key">{key.replaceAll('_', ' ')}</span>
                            <span className="viewer-kv-value">{isEmpty ? 'None' : String(value)}</span>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                )}
              </div>
            )}

            {agents.length > 0 ? (
              <div className="workflow-agent-grid">
                {agents.map((agent, i) => (
                  <AgentCard
                    key={agent.agent_key || i}
                    agent={agent}
                    onViewFile={onViewFile}
                    onOpenPrompt={setPromptTarget}
                  />
                ))}
              </div>
            ) : (
              <div className="modal-empty-state">No agent records found.</div>
            )}
          </div>
        )}
      </Modal>

      <PromptViewerModal
        isOpen={Boolean(promptTarget && !promptTarget.isEvaluation)}
        onClose={() => setPromptTarget(null)}
        promptName={promptTarget?.promptName}
        title={promptTarget ? `${promptTarget.title} Prompt` : ''}
      />
      <RubricViewerModal
        isOpen={Boolean(promptTarget && promptTarget.isEvaluation)}
        onClose={() => setPromptTarget(null)}
      />
    </>
  );
}
