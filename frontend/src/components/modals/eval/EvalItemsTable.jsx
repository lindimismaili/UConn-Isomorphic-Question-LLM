import { formatScore } from './evalData.js';

/**
 * Per-question evaluation detail table (one row per generated item).
 *
 * @param {object} props
 * @param {Array<object>} props.rows - Normalized eval records.
 * @param {Function} [props.onCriterionClick] - Called with the evaluation object when a chip is clicked.
 */
export default function EvalItemsTable({ rows, onCriterionClick }) {
  return (
    <div className="eval-table-shell">
      <table className="eval-table">
        <thead>
          <tr>
            <th>Source ID</th>
            <th>Result</th>
            <th>Input</th>
            <th>Output</th>
            <th>Evaluations</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id}>
              <td>
                <span className="eval-source-id">{row.sourceId}</span>
              </td>
              <td>
                <span className={`eval-status-chip eval-status-${row.status}`}>{row.status}</span>
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
                    <button
                      className={`eval-score-chip ${
                        evaluation.passed === false
                          ? 'is-fail'
                          : evaluation.passed === true
                            ? 'is-pass'
                            : 'is-neutral'
                      }${onCriterionClick ? ' is-clickable' : ''}`}
                      key={evaluation.key}
                      onClick={onCriterionClick ? () => onCriterionClick(evaluation) : undefined}
                      title={onCriterionClick ? `View reasoning for ${evaluation.label}` : evaluation.label}
                      type="button"
                    >
                      <span>{evaluation.label}</span>
                      <strong>{formatScore(evaluation.score)}</strong>
                    </button>
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
                            {evaluation.model && <small>{evaluation.model}</small>}
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
  );
}
