import Modal from './Modal.jsx';
import { IconDownload } from '../Icons.jsx';
import useFileContent from '../../hooks/useFileContent.js';

/** Strip LaTeX delimiters for readable display. */
function stripLatex(text) {
  if (!text) return '';
  return text
    .replace(/\\\(|\\\)/g, '')
    .replace(/\$\$/g, '')
    .replace(/\$/g, '');
}

function OptionRow({ option }) {
  const marker = option.is_correct ? '✓' : '✗';
  const markerClass = option.is_correct ? 'viewer-option-correct' : 'viewer-option-wrong';
  return (
    <div className="viewer-option-row">
      <span className={`viewer-option-marker ${markerClass}`}>{marker}</span>
      <div className="viewer-option-body">
        <span className="viewer-option-key">{option.key}.</span>
        <span className="viewer-option-text">{stripLatex(option.text)}</span>
        {option.distractor_logic && (
          <span className="viewer-option-distractor">{option.distractor_logic}</span>
        )}
        {option.explanation && (
          <span className="viewer-option-explanation">{option.explanation}</span>
        )}
      </div>
    </div>
  );
}

function QuestionCard({ question, showSourceBadge }) {
  const id = question.id ?? question.source_id;
  const sourceBadge = showSourceBadge && question.source_id
    ? `← Q${question.source_id}`
    : null;

  return (
    <div className="viewer-question-card">
      <div className="viewer-question-card-header">
        <span className="viewer-question-id">Q{id}</span>
        {sourceBadge && <span className="viewer-source-badge">{sourceBadge}</span>}
        {question.skipped && <span className="viewer-pill viewer-pill-fail">Skipped</span>}
      </div>
      <p className="viewer-question-stem">{stripLatex(question.stem)}</p>
      {Array.isArray(question.options) && question.options.length > 0 && (
        <div className="viewer-options">
          {question.options.map((opt) => (
            <OptionRow key={opt.key} option={opt} />
          ))}
        </div>
      )}
      {question.generation_notes && (
        <div className="viewer-generation-notes">{question.generation_notes}</div>
      )}
      {question.skip_reason && (
        <div className="viewer-generation-notes viewer-skip-reason">{question.skip_reason}</div>
      )}
    </div>
  );
}

/** Renders unknown JSON data as a formatted code block. */
function JsonFallback({ data }) {
  return (
    <pre className="tex-pre viewer-json-fallback">{JSON.stringify(data, null, 2)}</pre>
  );
}

/** Parse input JSONL and extract the task config object from input_text content. */
function parseInputData(data) {
  // Input JSONL is typically a message object: {role, content: [{type, text},...]}
  // or an array of messages
  const messages = Array.isArray(data) ? data : (data.content ? [data] : null);
  if (!messages) return null;

  for (const msg of messages) {
    const parts = Array.isArray(msg.content) ? msg.content : [];
    for (const part of parts) {
      if (part.type === 'input_text' && typeof part.text === 'string') {
        try {
          return JSON.parse(part.text);
        } catch {
          return { raw: part.text };
        }
      }
    }
  }
  return null;
}

function InputView({ data }) {
  const parsed = parseInputData(data);
  if (!parsed) return <JsonFallback data={data} />;

  const entries = Object.entries(parsed).filter(([, v]) => v !== undefined && v !== null);
  return (
    <div className="viewer-kv-table">
      {entries.map(([k, v]) => (
        <div className="viewer-kv-row" key={k}>
          <span className="viewer-kv-key">{k}</span>
          <span className="viewer-kv-value">
            {typeof v === 'object'
              ? <pre className="viewer-kv-json">{JSON.stringify(v, null, 2)}</pre>
              : String(v)}
          </span>
        </div>
      ))}
    </div>
  );
}

/** Derive agent name and kind from the file path. */
function parseArtifactPath(fileName) {
  const match = /agent_outputs\/([^/]+)\/(agent_input|agent_output)\.jsonl$/i.exec(fileName || '');
  if (!match) return { agentKey: '', kind: 'artifact' };
  const agentKey = match[1];
  const kind = match[2] === 'agent_input' ? 'Input' : 'Output';
  const agentLabel = agentKey.charAt(0).toUpperCase() + agentKey.slice(1);
  return { agentKey, agentLabel, kind };
}

export default function AgentArtifactModal({ isOpen, onClose, fileName, url, onDownload }) {
  const { text, loading, error } = useFileContent(url, isOpen);
  const { agentLabel, kind } = parseArtifactPath(fileName);
  const title = agentLabel ? `${agentLabel} — ${kind}` : (fileName?.split('/').pop() || 'Artifact');

  let data = null;
  if (text) {
    const firstLine = text.split('\n').find((l) => l.trim());
    if (firstLine) {
      try { data = JSON.parse(firstLine); } catch { /* ignore */ }
    }
  }

  function renderContent() {
    if (!data) return <div className="modal-empty-state">No data found.</div>;

    if (kind === 'Output') {
      if (Array.isArray(data.isomorphic_questions)) {
        return (
          <div className="viewer-question-list">
            {data.isomorphic_questions.map((q) => (
              <QuestionCard key={q.id ?? q.source_id} question={q} showSourceBadge />
            ))}
          </div>
        );
      }
      if (Array.isArray(data.source_questions)) {
        return (
          <div className="viewer-question-list">
            {data.source_questions.map((q) => (
              <QuestionCard key={q.source_id} question={q} showSourceBadge={false} />
            ))}
          </div>
        );
      }
      return <JsonFallback data={data} />;
    }

    // Input
    return <InputView data={data} />;
  }

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title={title}
      contentClassName="agent-artifact-modal" size="full"
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
      {!loading && !error && renderContent()}
    </Modal>
  );
}
