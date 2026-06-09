import { useState } from 'react';
import Modal from './Modal.jsx';
import { IconDownload } from '../Icons.jsx';
import useFileContent from '../../hooks/useFileContent.js';

const TABS = ['Config', 'Source Questions', 'Generated Questions', 'Evaluation'];

/** Strip common LaTeX delimiters for readable plain-text display. */
function stripLatex(text) {
  if (!text) return '';
  return text
    .replace(/\\\(|\\\)/g, '')
    .replace(/\$\$/g, '')
    .replace(/\$/g, '');
}

function TabBar({ active, onChange }) {
  return (
    <div className="viewer-tab-bar">
      {TABS.map((tab) => (
        <button
          className={`viewer-tab${active === tab ? ' is-active' : ''}`}
          key={tab}
          onClick={() => onChange(tab)}
          type="button"
        >
          {tab}
        </button>
      ))}
    </div>
  );
}

function ConfigTab({ config }) {
  if (!config) return <div className="modal-empty-state">No config data.</div>;
  const entries = Object.entries(config);
  return (
    <div className="viewer-kv-table">
      {entries.map(([k, v]) => (
        <div className="viewer-kv-row" key={k}>
          <span className="viewer-kv-key">{k.replaceAll('_', ' ')}</span>
          <span className="viewer-kv-value">{String(v ?? '—')}</span>
        </div>
      ))}
    </div>
  );
}

function OptionRow({ option, showMarker }) {
  const markerClass = option.is_correct ? 'viewer-option-correct' : 'viewer-option-wrong';
  return (
    <div className="viewer-option-row">
      {showMarker && (
        <span className={`viewer-option-marker ${markerClass}`}>
          {option.is_correct ? '✓' : '✗'}
        </span>
      )}
      <div className="viewer-option-body">
        <span className="viewer-option-key">{option.key}.</span>
        <span className="viewer-option-text">{stripLatex(option.text)}</span>
      </div>
    </div>
  );
}

function QuestionCard({ question, showSourceBadge, showMarkers }) {
  const id = question.id ?? question.source_id;
  return (
    <div className="viewer-question-card">
      <div className="viewer-question-card-header">
        <span className="viewer-question-id">Q{id}</span>
        {showSourceBadge && question.source_id && question.id && (
          <span className="viewer-source-badge">← Q{question.source_id}</span>
        )}
        {question.skipped && <span className="viewer-pill viewer-pill-fail">Skipped</span>}
      </div>
      <p className="viewer-question-stem">{stripLatex(question.stem)}</p>
      {Array.isArray(question.options) && question.options.length > 0 && (
        <div className="viewer-options">
          {question.options.map((opt) => (
            <OptionRow key={opt.key} option={opt} showMarker={showMarkers} />
          ))}
        </div>
      )}
      {question.generation_notes && (
        <div className="viewer-generation-notes">{question.generation_notes}</div>
      )}
    </div>
  );
}

function EvaluationTab({ evaluation }) {
  if (!evaluation) return <div className="modal-empty-state">No evaluation data.</div>;

  const status = evaluation.status || evaluation.overall_status || '';
  const statusClass = status === 'passed' ? 'viewer-pill-pass' : status === 'failed' ? 'viewer-pill-fail' : 'viewer-pill-neutral';
  const criterionScores = evaluation.criterion_scores || {};

  return (
    <div className="viewer-body-stack">
      {status && (
        <div className="viewer-eval-header">
          <span className="viewer-eval-status-label">Overall</span>
          <span className={`viewer-pill ${statusClass}`}>{status}</span>
          {typeof evaluation.overall_score === 'number' && (
            <span className="viewer-eval-score">{(evaluation.overall_score * 100).toFixed(0)}%</span>
          )}
        </div>
      )}
      {Object.keys(criterionScores).length > 0 && (
        <div className="file-viewer-table-shell">
          <table className="file-viewer-table">
            <thead>
              <tr>
                <th>Criterion</th>
                <th>Category</th>
                <th>Score</th>
                <th>Items</th>
                <th>Passed</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(criterionScores).map(([name, data]) => {
                const passed = data?.passed;
                const pillClass = passed === true ? 'viewer-pill-pass' : passed === false ? 'viewer-pill-fail' : 'viewer-pill-neutral';
                return (
                  <tr key={name}>
                    <td>{name.replaceAll('_', ' ')}</td>
                    <td><span className="viewer-cell-muted">{data?.category || '—'}</span></td>
                    <td className="viewer-cell-score">{typeof data?.score === 'number' ? data.score.toFixed(2) : '—'}</td>
                    <td className="viewer-cell-score">{data?.items_scored ?? '—'}</td>
                    <td>
                      {passed !== undefined && passed !== null
                        ? <span className={`viewer-pill ${pillClass}`}>{passed ? 'Pass' : 'Fail'}</span>
                        : <span className="viewer-cell-muted">—</span>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export default function FinalResultModal({ isOpen, onClose, fileName, url, onDownload }) {
  const { text, loading, error } = useFileContent(url, isOpen);
  const [activeTab, setActiveTab] = useState(TABS[0]);

  let data = null;
  if (text) {
    try { data = JSON.parse(text); } catch { /* ignore */ }
  }

  const sourceCount = data?.source_questions?.length ?? 0;
  const generatedCount = data?.generated_questions?.length ?? 0;

  function renderTab() {
    if (!data) return null;
    if (activeTab === 'Config') return <ConfigTab config={data.config} />;
    if (activeTab === 'Source Questions') {
      const questions = data.source_questions || [];
      if (questions.length === 0) return <div className="modal-empty-state">No source questions.</div>;
      return (
        <div className="viewer-question-list">
          {questions.map((q) => <QuestionCard key={q.source_id} question={q} showSourceBadge={false} showMarkers={false} />)}
        </div>
      );
    }
    if (activeTab === 'Generated Questions') {
      const questions = data.generated_questions || [];
      if (questions.length === 0) return <div className="modal-empty-state">No generated questions.</div>;
      return (
        <div className="viewer-question-list">
          {questions.map((q) => <QuestionCard key={q.id ?? q.source_id} question={q} showSourceBadge showMarkers />)}
        </div>
      );
    }
    if (activeTab === 'Evaluation') return <EvaluationTab evaluation={data.evaluation} />;
    return null;
  }

  const tabLabels = [
    'Config',
    `Source Questions${sourceCount ? ` (${sourceCount})` : ''}`,
    `Generated Questions${generatedCount ? ` (${generatedCount})` : ''}`,
    'Evaluation',
  ];

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title="Final Result"
      contentClassName="final-result-modal" size="full"
      modalBodyClassName="final-result-modal-body"
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
        <>
          <div className="viewer-tab-bar">
            {TABS.map((tab, i) => (
              <button
                className={`viewer-tab${activeTab === tab ? ' is-active' : ''}`}
                key={tab}
                onClick={() => setActiveTab(tab)}
                type="button"
              >
                {tabLabels[i]}
              </button>
            ))}
          </div>
          <div className="viewer-tab-content">
            {renderTab()}
          </div>
        </>
      )}
    </Modal>
  );
}
