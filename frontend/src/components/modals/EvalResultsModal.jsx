import { useEffect, useMemo, useState } from 'react';
import Modal from './Modal.jsx';
import { IconDownload, IconFile } from '../Icons.jsx';

/** Strips backend UUID suffixes that are attached to rubric keys. */
const UUID_SUFFIX_RE = /-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
/** Default pass threshold used when the report does not specify one. */
const DEFAULT_PASSING_THRESHOLD = 70;
/** Shared empty array to keep memoized fallbacks stable. */
const EMPTY_ITEMS = [];

/**
 * Parses newline-delimited JSON records used by evaluation item exports.
 *
 * @param {string} text
 * @returns {Array<object>}
 */
function parseJsonl(text) {
  return text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => JSON.parse(line));
}

/**
 * Strips UUID suffixes from evaluation criterion keys for display.
 *
 * @param {string} key
 * @returns {string}
 */
function normalizeCriterionName(key) {
  return String(key || '').replace(UUID_SUFFIX_RE, '');
}

/**
 * Resolves a display label for one evaluation criterion.
 *
 * @param {string} key
 * @param {Map<string, object>} rubricByName
 * @returns {string}
 */
function formatCriterionLabel(key, rubricByName) {
  const name = normalizeCriterionName(key);
  const criterion = rubricByName.get(name);
  const label = criterion?.name || name || 'criterion';
  return label
    .replaceAll('_', ' ')
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

/**
 * Formats numeric scores for the summary table and per-item chips.
 *
 * @param {number} value
 * @returns {string}
 */
function formatScore(value) {
  if (typeof value !== 'number' || Number.isNaN(value)) return 'n/a';
  return Number.isInteger(value) ? String(value) : value.toFixed(1);
}

/**
 * Returns the last path segment for a file-like string.
 *
 * @param {string} value
 * @returns {string}
 */
function baseName(value) {
  if (typeof value !== 'string' || !value) return '';
  return value.split('/').pop() || value;
}

/**
 * Coerces a maybe-object field into a plain object.
 *
 * @param {unknown} value
 * @returns {object}
 */
function asObject(value) {
  return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
}

/**
 * Normalizes a backend file reference for the modal side panels.
 *
 * @param {unknown} value
 * @param {string} [fallbackLabel]
 * @returns {object | null}
 */
function normalizeFileReference(value, fallbackLabel = '') {
  if (typeof value === 'string' && value) {
    return {
      name: value,
      label: fallbackLabel || baseName(value),
      kind: '',
      displayPath: value,
    };
  }

  const file = asObject(value);
  const name = typeof file.name === 'string' ? file.name : '';
  const displayPath = typeof file.display_path === 'string' ? file.display_path : name;
  if (!name && !displayPath) return null;

  return {
    name,
    label:
      (typeof file.label === 'string' && file.label) ||
      fallbackLabel ||
      baseName(name) ||
      displayPath ||
      'File',
    kind: typeof file.kind === 'string' ? file.kind : '',
    displayPath,
  };
}

/**
 * Formats a short chip label for file kind metadata.
 *
 * @param {string} kind
 * @returns {string}
 */
function formatFileKind(kind) {
  if (!kind) return '';
  return kind.replaceAll('_', ' ').replace(/\b\w/g, (char) => char.toUpperCase());
}

/**
 * Normalizes pass/fail fields to booleans when possible.
 *
 * @param {unknown} value
 * @returns {boolean | null}
 */
function coercePassed(value) {
  if (typeof value === 'boolean') return value;
  if (typeof value === 'string') {
    const normalized = value.toLowerCase();
    if (['pass', 'passed', 'true'].includes(normalized)) return true;
    if (['fail', 'failed', 'false'].includes(normalized)) return false;
  }
  return null;
}

/**
 * Pulls score and pass maps from a single eval record payload.
 *
 * @param {object} record
 * @returns {{grades: object, passes: object}}
 */
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

/**
 * Extracts a short grader note from a sampled evaluation payload.
 *
 * @param {object} sample
 * @returns {string}
 */
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

/**
 * Builds a lookup table for rubric entries keyed by criterion name.
 *
 * @param {object} report
 * @returns {Map<string, object>}
 */
function buildRubricMap(report) {
  const map = new Map();
  for (const criterion of Array.isArray(report?.rubric) ? report.rubric : []) {
    if (criterion?.name) {
      map.set(criterion.name, criterion);
    }
  }
  return map;
}

/**
 * Converts one item from the evaluation export into table-ready data.
 *
 * @param {object} record
 * @param {number} index
 * @param {Map<string, object>} rubricByName
 * @param {number} threshold
 * @returns {object}
 */
function normalizeEvalRecord(record, index, rubricByName, threshold) {
  const item = asObject(
    record.datasource_item ||
    record.data_source_item ||
    record.item ||
    record.input ||
    record.sample?.input,
  );
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

/**
 * Builds the summary rows shown when only aggregate evaluation data exists.
 *
 * @param {object} report
 * @returns {Array<object>}
 */
function buildAggregateRows(report) {
  const criterionScores = asObject(report?.criterion_scores);
  return Object.entries(criterionScores).map(([name, scoreData]) => ({
    name: formatCriterionLabel(name, new Map([[name, { name }]])),
    score: scoreData?.score,
    itemsScored: scoreData?.items_scored,
    category: scoreData?.category || '',
  }));
}

/**
 * Displays detailed evaluation output and lets the user download source files.
 *
 * @param {object} props
 */
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
  const runInputFiles = useMemo(
    () =>
      (Array.isArray(report?.run_inputs) ? report.run_inputs : [])
        .map((file) => normalizeFileReference(file))
        .filter(Boolean),
    [report],
  );
  const testingFiles = useMemo(
    () =>
      (Array.isArray(report?.testing_files) ? report.testing_files : [])
        .map((file) => normalizeFileReference(file))
        .filter(Boolean),
    [report],
  );
  const rubricFile = useMemo(() => {
    const sourcePath =
      typeof report?.rubric_source_path === 'string' && report.rubric_source_path
        ? report.rubric_source_path
        : 'data/rubric.json';
    if (typeof report?.rubric_file === 'string' && report.rubric_file) {
      return normalizeFileReference({
        name: report.rubric_file,
        label: 'Rubric snapshot',
        kind: 'rubric',
        display_path: sourcePath,
      });
    }
    if (Array.isArray(report?.rubric) && report.rubric.length > 0) {
      return normalizeFileReference({
        label: 'Rubric config',
        kind: 'rubric',
        display_path: sourcePath,
      });
    }
    return null;
  }, [report]);
  const spreadsheetFileName =
    typeof report?.spreadsheet_file === 'string' ? report.spreadsheet_file : '';
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
      title="Evaluator Results"
      size="full"
      contentClassName="eval-results-modal"
      footer={(
        <div className="modal-footer-actions eval-modal-footer-actions">
          {spreadsheetFileName && (
            <button
              className="btn btn-secondary"
              onClick={() => onDownload(spreadsheetFileName)}
              type="button"
            >
              <IconDownload className="btn-icon" />
              Spreadsheet CSV
            </button>
          )}
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

      <div className="eval-reference-grid">
        <section className="eval-reference-panel">
          <div className="eval-reference-header">
            <h4>Run Inputs</h4>
            <span>{runInputFiles.length}</span>
          </div>
          {runInputFiles.length === 0 ? (
            <div className="eval-reference-empty">No input files recorded for this run.</div>
          ) : (
            <div className="eval-reference-list">
              {runInputFiles.map((file, index) => (
                <div
                  className="eval-reference-row"
                  key={`${file.displayPath || file.label || 'input'}-${index}`}
                >
                  <div className="eval-reference-meta">
                    <div className="eval-reference-title">
                      <IconFile className="eval-reference-icon" />
                      <span>{file.label}</span>
                    </div>
                    <div className="eval-reference-path">{file.displayPath || file.name}</div>
                  </div>
                  {file.kind && (
                    <span className={`eval-reference-kind is-${file.kind}`}>
                      {formatFileKind(file.kind)}
                    </span>
                  )}
                </div>
              ))}
            </div>
          )}
        </section>

        <section className="eval-reference-panel">
          <div className="eval-reference-header">
            <h4>Testing Files</h4>
            <span>{testingFiles.length}</span>
          </div>
          {testingFiles.length === 0 ? (
            <div className="eval-reference-empty">No testing artifacts were recorded.</div>
          ) : (
            <div className="eval-reference-list">
              {testingFiles.map((file, index) => (
                <div className="eval-reference-row" key={`${file.name || file.label}-${index}`}>
                  <div className="eval-reference-meta">
                    <div className="eval-reference-title">
                      <IconFile className="eval-reference-icon" />
                      <span>{file.label}</span>
                    </div>
                    <div className="eval-reference-path">{file.displayPath || file.name}</div>
                  </div>
                  <button
                    className="btn btn-secondary eval-reference-download"
                    disabled={!file.name}
                    onClick={() => onDownload(file.name)}
                    type="button"
                  >
                    <IconDownload className="btn-icon" />
                    Download
                  </button>
                </div>
              ))}
            </div>
          )}
        </section>

        <section className="eval-reference-panel">
          <div className="eval-reference-header">
            <h4>Rubric</h4>
            <span>{rubricFile ? 1 : 0}</span>
          </div>
          {!rubricFile ? (
            <div className="eval-reference-empty">No rubric file metadata is available.</div>
          ) : (
            <div className="eval-reference-list">
              <div className="eval-reference-row">
                <div className="eval-reference-meta">
                  <div className="eval-reference-title">
                    <IconFile className="eval-reference-icon" />
                    <span>{rubricFile.label}</span>
                  </div>
                  <div className="eval-reference-path">
                    {rubricFile.displayPath || rubricFile.name || 'data/rubric.json'}
                  </div>
                </div>
                <button
                  className="btn btn-secondary eval-reference-download"
                  disabled={!rubricFile.name}
                  onClick={() => rubricFile.name && onDownload(rubricFile.name)}
                  type="button"
                >
                  <IconDownload className="btn-icon" />
                  Download
                </button>
              </div>
            </div>
          )}
        </section>
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
                    <div className="eval-question-text">{row.input || 'Input unavailable'}</div>
                  </td>
                  <td>
                    <div className="eval-question-text">{row.output || 'Output unavailable'}</div>
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
