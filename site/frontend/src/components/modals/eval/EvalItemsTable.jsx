import { Badge, DataTable, ScoreChip, StatusChip } from '../../ui/index.js';
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
      <DataTable variant="eval">
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
                <Badge kind="source-id">{row.sourceId}</Badge>
              </td>
              <td>
                <StatusChip status={row.status} />
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
                    <ScoreChip
                      key={evaluation.key}
                      tone={
                        evaluation.passed === false
                          ? 'fail'
                          : evaluation.passed === true
                            ? 'pass'
                            : 'neutral'
                      }
                      onClick={onCriterionClick ? () => onCriterionClick(evaluation) : undefined}
                      title={onCriterionClick ? `View reasoning for ${evaluation.label}` : evaluation.label}
                    >
                      <span>{evaluation.label}</span>
                      <strong>{formatScore(evaluation.score)}</strong>
                    </ScoreChip>
                  ))}
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </DataTable>
    </div>
  );
}
