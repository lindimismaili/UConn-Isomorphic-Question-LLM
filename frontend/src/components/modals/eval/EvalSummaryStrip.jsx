import { IconDownload } from '../../Icons.jsx';
import { formatScore } from './evalData.js';

/**
 * Top strip of the Evaluator Results modal: status, overall score, item count, and rubric.
 *
 * @param {object} props
 * @param {object} props.report - Evaluation report payload.
 * @param {number} props.itemCount - Number of normalized detail rows.
 * @param {Array<object>} props.rubricFiles - Normalized rubric file references.
 * @param {(name: string) => void} [props.onDownload]
 */
export default function EvalSummaryStrip({ report, itemCount, rubricFiles, onDownload }) {
  const reportStatus = report?.status || 'unknown';
  const primaryRubric = rubricFiles?.[0];
  return (
    <div className="eval-summary-strip">
      <div>
        <span className="eval-summary-label">Status</span>
        <span className={`eval-status-chip eval-status-${reportStatus}`}>{reportStatus}</span>
      </div>
      <div>
        <span className="eval-summary-label">Overall Score</span>
        <strong>{formatScore(report?.overall_score)}</strong>
      </div>
      <div>
        <span className="eval-summary-label">Items</span>
        <strong>{report?.items_evaluated ?? itemCount}</strong>
      </div>
      <div className="eval-summary-rubric-panel">
        <span className="eval-summary-label">Rubric</span>
        {primaryRubric ? (
          <div className="eval-summary-rubric-body">
            <span className="eval-summary-rubric-name">{primaryRubric.label}</span>
            <div className="eval-summary-rubric-path">{primaryRubric.displayPath || primaryRubric.name}</div>
            {onDownload && primaryRubric.name && (
              <button
                className="btn btn-secondary eval-summary-rubric-btn"
                onClick={() => onDownload(primaryRubric.name)}
                type="button"
              >
                <IconDownload className="btn-icon" />
                Download
              </button>
            )}
          </div>
        ) : (
          <span className="eval-summary-rubric-empty">—</span>
        )}
      </div>
    </div>
  );
}
