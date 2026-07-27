import { Fragment, useState } from 'react';
import Modal from './Modal.jsx';
import { IconDownload } from '../Icons.jsx';
import useFileContent from '../../hooks/useFileContent.js';
import { DEFAULT_PASSING_THRESHOLD } from './eval/evalData.js';
import { Button, DataTable, EmptyState, KeyValueTable, Tabs } from '../ui/index.js';

const TABS = ['Config', 'Source Questions', 'Generated Questions', 'Evaluation'];

/** Strip common LaTeX delimiters for readable plain-text display. */
function stripLatex(text) {
  if (!text) return '';
  return text
    .replace(/\\\(|\\\)/g, '')
    .replace(/\$\$/g, '')
    .replace(/\$/g, '');
}

function ConfigTab({ config }) {
  if (!config) return <EmptyState variant="modal">No config data.</EmptyState>;
  const rows = Object.entries(config).map(([k, v]) => {
    const isEmpty = v === null || v === undefined || v === '';
    return { key: k.replaceAll('_', ' '), value: isEmpty ? 'None' : String(v) };
  });
  return <KeyValueTable rows={rows} />;
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
        {option.distractor_logic && (
          <span className="viewer-option-distractor">
            <span className="viewer-option-note-label">Distractor logic:</span>{' '}
            {stripLatex(option.distractor_logic)}
          </span>
        )}
        {option.explanation && (
          <span className="viewer-option-explanation">
            <span className="viewer-option-note-label">
              {option.is_correct ? 'Why correct:' : 'Why wrong:'}
            </span>{' '}
            {stripLatex(option.explanation)}
          </span>
        )}
      </div>
    </div>
  );
}

// Render a question list, emitting a shared-context preamble once before the first
// member of each group (questions that share a non-empty group_id).
function QuestionList({ questions, showSourceBadge, showMarkers }) {
  return (
    <div className="viewer-question-list">
      {questions.map((q, index) => {
        const groupId = q.group_id || '';
        const prevGroupId = index > 0 ? (questions[index - 1].group_id || '') : '';
        const startsGroup = groupId && groupId !== prevGroupId;
        const key = q.id ?? q.source_id;
        return (
          <Fragment key={key}>
            {startsGroup && q.group_context && (
              <p className="viewer-group-preamble">{stripLatex(q.group_context)}</p>
            )}
            <QuestionCard question={q} showSourceBadge={showSourceBadge} showMarkers={showMarkers} />
          </Fragment>
        );
      })}
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

function EvaluationTab({ evaluation, onViewInDepth }) {
  if (!evaluation) return <EmptyState variant="modal">No evaluation data.</EmptyState>;

  const criterionScores = evaluation.criterion_scores || {};
  const hasScore = typeof evaluation.overall_score === 'number';
  const threshold = typeof evaluation.passing_score_threshold === 'number'
    ? evaluation.passing_score_threshold
    : DEFAULT_PASSING_THRESHOLD;

  return (
    <div className="viewer-body-stack">
      {Object.keys(criterionScores).length > 0 && (
        <div className="file-viewer-table-shell">
          <DataTable variant="file-viewer" className="eval-summary-table">
            <thead>
              <tr>
                <th>Criterion</th>
                <th>Category</th>
                <th className="viewer-th-num">Score</th>
                <th className="viewer-th-num">Items</th>
                <th className="viewer-th-num">Passed</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(criterionScores).map(([name, data]) => {
                const score = typeof data?.score === 'number' ? data.score : null;
                // The report stores per-criterion pass_rate/items_passed rather
                // than a boolean; a criterion passes when its score clears the
                // run's passing threshold.
                const passed = typeof data?.passed === 'boolean'
                  ? data.passed
                  : score !== null ? score >= threshold : null;
                const itemsPassed = typeof data?.items_passed === 'number' ? data.items_passed : null;
                const itemsScored = typeof data?.items_scored === 'number' ? data.items_scored : null;
                const pillClass = passed === true ? 'viewer-pill-pass' : passed === false ? 'viewer-pill-fail' : 'viewer-pill-neutral';
                return (
                  <tr key={name}>
                    <td>{name.replaceAll('_', ' ').toUpperCase()}</td>
                    <td><span className="viewer-cell-muted">{data?.category || '—'}</span></td>
                    <td className="viewer-cell-num">{score !== null ? score.toFixed(2) : '—'}</td>
                    <td className="viewer-cell-num">{itemsScored ?? '—'}</td>
                    <td className="viewer-cell-num">
                      {passed !== null
                        ? (
                          <span className="viewer-passed-cell">
                            <span className={`viewer-pill ${pillClass}`}>{passed ? 'Pass' : 'Fail'}</span>
                            {itemsPassed !== null && itemsScored !== null && (
                              <span className="viewer-cell-muted">{itemsPassed}/{itemsScored}</span>
                            )}
                          </span>
                        )
                        : <span className="viewer-cell-muted">—</span>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </DataTable>
        </div>
      )}
      <div className="viewer-eval-footer">
        {onViewInDepth && (
          <Button variant="secondary" onClick={onViewInDepth}>
            View In-Depth
          </Button>
        )}
        {hasScore && (
          <span className="viewer-eval-score">{evaluation.overall_score.toFixed(0)}%</span>
        )}
      </div>
    </div>
  );
}

export default function FinalResultModal({ isOpen, onClose, fileName, url, onDownload, onViewInDepth }) {
  const { text, loading, error } = useFileContent(url, isOpen);
  const [activeTab, setActiveTab] = useState(TABS[0]);

  let data = null;
  let parseError = '';
  if (text !== null) {
    try {
      const parsed = JSON.parse(text);
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
        throw new TypeError('Expected a JSON object.');
      }
      data = parsed;
    } catch {
      parseError = 'This results file is not valid JSON. Download the file to inspect it or run the workflow again.';
    }
  }

  // Verified questions are the source questions after the Verifier fills in
  // the correct answer, per-option explanations, and distractor logic; the raw
  // extracter output leaves those fields empty.
  const sourceQuestions = data?.verified_questions?.length
    ? data.verified_questions
    : data?.source_questions || [];
  const sourceCount = sourceQuestions.length;
  const generatedCount = data?.generated_questions?.length ?? 0;

  function renderTab() {
    if (!data) return null;
    if (activeTab === 'Config') return <ConfigTab config={data.config} />;
    if (activeTab === 'Source Questions') {
      const questions = sourceQuestions;
      if (questions.length === 0) return <EmptyState variant="modal">No source questions.</EmptyState>;
      return <QuestionList questions={questions} showSourceBadge={false} showMarkers />;
    }
    if (activeTab === 'Generated Questions') {
      const questions = data.generated_questions || [];
      if (questions.length === 0) return <EmptyState variant="modal">No generated questions.</EmptyState>;
      return <QuestionList questions={questions} showSourceBadge showMarkers />;
    }
    if (activeTab === 'Evaluation') return <EvaluationTab evaluation={data.evaluation} onViewInDepth={onViewInDepth} />;
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
      title="Results"
      contentClassName="final-result-modal" size="full"
      modalBodyClassName="final-result-modal-body"
      footer={(
        <div className="modal-footer-actions">
          {onDownload && fileName && (
            <Button variant="secondary" onClick={() => onDownload(fileName)} icon={<IconDownload className="btn-icon" />}>
              Download
            </Button>
          )}
          <Button variant="primary" onClick={onClose}>Close</Button>
        </div>
      )}
    >
      {loading && <EmptyState variant="modal">Loading…</EmptyState>}
      {error && <EmptyState variant="modal" error>{error}</EmptyState>}
      {!loading && !error && parseError && (
        <EmptyState variant="modal" error>{parseError}</EmptyState>
      )}
      {!loading && !error && data && (
        <>
          <Tabs
            value={activeTab}
            onChange={setActiveTab}
            tabs={TABS.map((tab, i) => ({ id: tab, label: tabLabels[i] }))}
          />
          <div className="viewer-tab-content">
            {renderTab()}
          </div>
        </>
      )}
    </Modal>
  );
}
