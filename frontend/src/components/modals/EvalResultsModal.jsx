import { useEffect, useMemo, useState } from 'react';
import Modal from './Modal.jsx';
import { IconDownload } from '../Icons.jsx';

const UUID_SUFFIX_RE = /-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const DEFAULT_PASSING_THRESHOLD = 70;
const EMPTY_ITEMS = [];

function parseJsonl(text) {
  return text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => JSON.parse(line));
}

function normalizeCriterionName(key) {
  return String(key || '').replace(UUID_SUFFIX_RE, '');
}

function formatCriterionLabel(key, rubricByName) {
  const name = normalizeCriterionName(key);
  const criterion = rubricByName.get(name);
  const label = criterion?.name || name || 'criterion';
  return label
    .replaceAll('_', ' ')
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

function formatScore(value) {
  if (typeof value !== 'number' || Number.isNaN(value)) return 'n/a';
  return Number.isInteger(value) ? String(value) : value.toFixed(1);
}

function asObject(value) {
  return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
}

function coercePassed(value) {
  if (typeof value === 'boolean') return value;
  if (typeof value === 'string') {
    const normalized = value.toLowerCase();
    if (['pass', 'passed', 'true'].includes(normalized)) return true;
    if (['fail', 'failed', 'false'].includes(normalized)) return false;
  }
  return null;
}

function extractResultMaps(record) {
  const grades = { ...asObject(record.grades) };
  const passes = { ...asObject(record.passes) };

  for (const result of Array.isArray(record.results) ? record.results : []) {
    const name = result?.name || result?.criterion || result?.criterion_name;
    if (!name) continue;
    if (grades[name] === undefined && typeof result.score === 'number') {
      grades[name] = result.score;
    }
    const passed = coercePassed(
      result.passed ?? result.pass ?? result.status ?? result.result,
    );
    if (passes[name] === undefined && passed !== null) {
      passes[name] = passed;
    }
  }

  return { grades, passes };
}

function extractSampleNote(sample) {
  const outputs = Array.isArray(sample?.outputs) ? sample.outputs : [];
  const outputContent = outputs.find((output) => typeof output?.content === 'string')?.content;
  if (!outputContent) return '';

  try {
    const parsed = JSON.parse(outputContent);
    if (Array.isArray(parsed.steps)) {
      return parsed.steps
        .map((step) => step.conclusion || step.description)
        .filter(Boolean)
        .join(' ');
    }
    if (parsed.reason) return String(parsed.reason);
  } catch {
    return outputContent;
  }

  return outputContent;
}

function buildRubricMap(report) {
  const map = new Map();
  for (const criterion of Array.isArray(report?.rubric) ? report.rubric : []) {
    if (criterion?.name) {
      map.set(criterion.name, criterion);
    }
  }
  return map;
}

function normalizeEvalRecord(record, index, rubricByName, threshold) {
  const item = asObject(record.item || record.input || record.sample?.input);
  const { grades, passes } = extractResultMaps(record);
  const samples = asObject(record.grader_samples);
  const keys = [
    ...new Set([
      ...Object.keys(grades),
      ...Object.keys(passes),
      ...Object.keys(samples),
    ]),
  ];

  const evaluations = keys.map((key) => {
    const score = typeof grades[key] === 'number' ? grades[key] : null;
    const explicitPassed = coercePassed(passes[key]);
    const passed = explicitPassed ?? (score !== null ? score >= threshold : null);

    return {
      key,
      label: formatCriterionLabel(key, rubricByName),
      score,
      passed,
      note: extractSampleNote(samples[key]),
      model: samples[key]?.sampled_model_name || '',
    };
  });

  const knownResults = evaluations.filter((evaluation) => typeof evaluation.passed === 'boolean');
  const failedCount = knownResults.filter((evaluation) => !evaluation.passed).length;
  const passedCount = knownResults.filter((evaluation) => evaluation.passed).length;
  const status = failedCount > 0 ? 'failed' : knownResults.length > 0 ? 'passed' : 'unknown';

  return {
    id: `${item.source_id || record.data_source_idx || index}-${index}`,
    sourceId: String(item.source_id || record.source_id || record.data_source_idx || index + 1),
    input: item.source_question_text || item.input || record.source_question_text || '',
    output: item.generated_question_text || item.output || record.generated_question_text || '',
    status,
    passedCount,
    failedCount,
    evaluations,
  };
}

function buildAggregateRows(report) {
  const criterionScores = asObject(report?.criterion_scores);
  return Object.entries(criterionScores).map(([name, scoreData]) => ({
    name: formatCriterionLabel(name, new Map([[name, { name }]])),
    score: scoreData?.score,
    itemsScored: scoreData?.items_scored,
    category: scoreData?.category || '',
  }));
}

export default function EvalResultsModal({
  isOpen,
  onClose,
  report,
  itemsUrl,
  itemsFileName,
  reportFileName,
  onDownload,
}) {
  const [itemsResult, setItemsResult] = useState({
    url: '',
    items: [],
    error: '',
  });

  useEffect(() => {
    if (!isOpen || !itemsUrl) return undefined;

    const controller = new AbortController();

    fetch(itemsUrl, { signal: controller.signal, cache: 'no-store' })
      .then((response) => {
        if (!response.ok) {
          throw new Error(`Unable to load eval items (${response.status})`);
        }
        return response.text();
      })
      .then((text) => {
        setItemsResult({
          url: itemsUrl,
          items: parseJsonl(text),
          error: '',
        });
      })
      .catch((err) => {
        if (err.name === 'AbortError') return;
        setItemsResult({
          url: itemsUrl,
          items: [],
          error: err.message,
        });
      });

    return () => controller.abort();
  }, [isOpen, itemsUrl]);

  const threshold = typeof report?.passing_score_threshold === 'number'
    ? report.passing_score_threshold
    : DEFAULT_PASSING_THRESHOLD;
  const rubricByName = useMemo(() => buildRubricMap(report), [report]);
  const items = itemsResult.url === itemsUrl ? itemsResult.items : EMPTY_ITEMS;
  const error = itemsResult.url === itemsUrl ? itemsResult.error : '';
  const loadState = !itemsUrl
    ? 'unavailable'
    : error
      ? 'error'
      : itemsResult.url === itemsUrl
        ? 'loaded'
        : 'loading';
  const rows = useMemo(
    () => items.map((item, index) => normalizeEvalRecord(item, index, rubricByName, threshold)),
    [items, rubricByName, threshold],
  );
  const aggregateRows = useMemo(() => buildAggregateRows(report), [report]);

  const totalPassed = rows.filter((row) => row.status === 'passed').length;
  const totalFailed = rows.filter((row) => row.status === 'failed').length;
  const overallScore = formatScore(report?.overall_score);
  const reportStatus = report?.status || 'unknown';

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title="Evaluation Results"
      size="full"
      contentClassName="eval-results-modal"
      footer={(
        <div className="modal-footer-actions eval-modal-footer-actions">
          {itemsFileName && (
            <button
              className="btn btn-secondary"
              onClick={() => onDownload(itemsFileName)}
              type="button"
            >
              <IconDownload className="btn-icon" />
              Items JSONL
            </button>
          )}
          {reportFileName && (
            <button
              className="btn btn-secondary"
              onClick={() => onDownload(reportFileName)}
              type="button"
            >
              <IconDownload className="btn-icon" />
              Report JSON
            </button>
          )}
          <button className="btn btn-primary" onClick={onClose} type="button">
            Close
          </button>
        </div>
      )}
    >
      <div className="eval-summary-strip">
        <div>
          <span className="eval-summary-label">Status</span>
          <span className={`eval-status-chip eval-status-${reportStatus}`}>
            {reportStatus}
          </span>
        </div>
        <div>
          <span className="eval-summary-label">Overall Score</span>
          <strong>{overallScore}</strong>
        </div>
        <div>
          <span className="eval-summary-label">Items</span>
          <strong>{report?.items_evaluated ?? rows.length}</strong>
        </div>
        <div>
          <span className="eval-summary-label">Question Results</span>
          <strong>{totalPassed} pass / {totalFailed} fail</strong>
        </div>
      </div>

      {loadState === 'loading' && (
        <div className="modal-empty-state">Loading detailed eval items...</div>
      )}

      {loadState === 'error' && (
        <div className="modal-empty-state">{error}</div>
      )}

      {loadState === 'unavailable' && aggregateRows.length === 0 && (
        <div className="modal-empty-state">
          Detailed eval items are not available for this run.
        </div>
      )}

      {rows.length > 0 && (
        <div className="eval-table-shell">
          <table className="eval-table">
            <thead>
              <tr>
                <th>Source ID</th>
                <th>Result</th>
                <th>Input</th>
                <th>Output</th>
                <th>Evals</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id}>
                  <td>
                    <span className="eval-source-id">{row.sourceId}</span>
                  </td>
                  <td>
                    <span className={`eval-status-chip eval-status-${row.status}`}>
                      {row.status}
                    </span>
                    <div className="eval-result-count">
                      {row.passedCount} pass / {row.failedCount} fail
                    </div>
                  </td>
                  <td>
                    <div className="eval-question-text">{row.input || 'No input text'}</div>
                  </td>
                  <td>
                    <div className="eval-question-text">{row.output || 'No output text'}</div>
                  </td>
                  <td>
                    <div className="eval-chip-grid">
                      {row.evaluations.map((evaluation) => (
                        <span
                          className={`eval-score-chip ${
                            evaluation.passed === false
                              ? 'is-fail'
                              : evaluation.passed === true
                                ? 'is-pass'
                                : 'is-neutral'
                          }`}
                          key={evaluation.key}
                          title={evaluation.label}
                        >
                          <span>{evaluation.label}</span>
                          <strong>{formatScore(evaluation.score)}</strong>
                        </span>
                      ))}
                    </div>
                    {row.evaluations.some((evaluation) => evaluation.note) && (
                      <details className="eval-notes">
                        <summary>Grader notes</summary>
                        <div className="eval-notes-list">
                          {row.evaluations
                            .filter((evaluation) => evaluation.note)
                            .map((evaluation) => (
                              <div className="eval-note" key={`${evaluation.key}-note`}>
                                <div className="eval-note-title">
                                  <span>{evaluation.label}</span>
                                  <span>{formatScore(evaluation.score)}</span>
                                </div>
                                <p>{evaluation.note}</p>
                                {evaluation.model && (
                                  <small>{evaluation.model}</small>
                                )}
                              </div>
                            ))}
                        </div>
                      </details>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {rows.length === 0 && aggregateRows.length > 0 && loadState !== 'loading' && (
        <div className="eval-table-shell">
          <table className="eval-table eval-aggregate-table">
            <thead>
              <tr>
                <th>Criterion</th>
                <th>Category</th>
                <th>Score</th>
                <th>Items Scored</th>
              </tr>
            </thead>
            <tbody>
              {aggregateRows.map((row) => (
                <tr key={row.name}>
                  <td>{row.name}</td>
                  <td>{row.category || 'n/a'}</td>
                  <td>{formatScore(row.score)}</td>
                  <td>{row.itemsScored ?? 'n/a'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Modal>
  );
}
