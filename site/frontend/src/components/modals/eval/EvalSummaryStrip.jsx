import { formatScore } from './evalData.js';

/**
 * Top strip of the Evaluations modal: status, overall score, and item count.
 *
 * @param {object} props
 * @param {object} props.report - Evaluation report payload.
 * @param {number} props.itemCount - Number of normalized detail rows.
 */
export default function EvalSummaryStrip({ report, itemCount }) {
  const reportStatus = report?.status || 'unknown';
  const statusLabel = reportStatus.charAt(0).toUpperCase() + reportStatus.slice(1);
  return (
    <div className="eval-summary-strip">
      <div>
        <span className="eval-summary-label">Status</span>
        <span className={`eval-status-chip eval-status-${reportStatus}`}>{statusLabel}</span>
      </div>
      <div>
        <span className="eval-summary-label">Overall Score</span>
        <strong>{formatScore(report?.overall_score)}</strong>
      </div>
      <div>
        <span className="eval-summary-label">Items</span>
        <strong>{report?.items_evaluated ?? itemCount}</strong>
      </div>
    </div>
  );
}
